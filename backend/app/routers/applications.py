# -*- coding: utf-8 -*-
"""模块3/4：考试报名（两种动态表单）、我的报名、信息审核、批量导入。

权限说明
--------
- 考生(candidate)         → apply
- 班主任(head_teacher)    → apply + import + export + analysis，数据范围＝本班级（可多班）
- 审核员(reviewer)        → audit + export + analysis，数据范围＝全校
- 管理员(admin)           → 全部权限，数据范围＝全校
- 审核员(reviewer)        → audit + export + analysis，数据范围＝全校
- 管理员(admin)           → 全部权限，数据范围＝全校

所有列表/详情/审核/导出接口都会叠加 scope_filter()，防止越权查看他人数据。
"""
import io
import itertools
import json
import re

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from pydantic import BaseModel, ValidationError

from .. import db
from ..import_map import (FIELD_LABEL, auto_map, convert_row, detect_header_row,
                          fields_of, match_header)
from ..deps import ApiError, get_current_user, ok, page_params
from ..permissions import (describe, has_perm, require_perms, scope_filter,
                           scope_label, scope_of)
from ..security import hash_password
from ..validators import check_id_type, validate_id_number
from .. import exam_types as ET
from .exams import TYPE_LABEL, auto_close_exams, exam_open_now

router = APIRouter(prefix="/api/applications", tags=["applications"])

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+(\.[\w\-]+)+$")
POSTCODE_RE = re.compile(r"^\d{6}$")
SITE_RE = re.compile(r"^[A-Za-z0-9]{4,12}$")
ID_RE = re.compile(r"^[0-9A-Za-z]{6,30}$")

AUDIT_LABEL = {"pending": "待审核", "approved": "已通过", "rejected": "已驳回", "returned": "已退回"}

COMPUTER_FIELDS = ["org_code", "exam_site_code", "name", "gender", "id_type", "id_number",
                   "subject", "school", "class_name", "education", "phone", "email", "address"]
MANDARIN_FIELDS = ["name", "gender", "ethnicity", "id_type", "id_number", "occupation", "employer",
                   "phone", "student_no", "class_name", "department", "contact_address",
                   "mail_address", "postcode", "birth_province", "birth_city", "birth_county",
                   "live_province", "live_city", "live_county"]
# 通用模板的固定列；自定义字段的值统一存在 extra(JSON) 里
GENERIC_FIELDS = ["name", "gender", "id_type", "id_number", "phone", "email",
                  "class_name", "college"]

IMPORT_HEADER_FILL = PatternFill("solid", fgColor="D9E7F5")
IMPORT_HINT_FILL = PatternFill("solid", fgColor="FFF3CD")


class ComputerIn(BaseModel):
    exam_id: int
    org_code: str
    exam_site_code: str
    name: str
    gender: str
    id_type: str = "1"
    id_number: str
    subject: str
    school: str = ""
    class_name: str = ""
    education: str = ""
    phone: str
    email: str = ""
    address: str = ""


class MandarinIn(BaseModel):
    exam_id: int
    name: str
    gender: str
    ethnicity: str = ""
    id_type: str = "1"
    id_number: str
    occupation: str
    employer: str = ""
    phone: str
    student_no: str = ""
    class_name: str = ""
    department: str = ""
    contact_address: str = ""
    mail_address: str = ""
    postcode: str = ""
    birth_province: str = ""
    birth_city: str = ""
    birth_county: str = ""
    live_province: str = ""
    live_city: str = ""
    live_county: str = ""


class GenericIn(BaseModel):
    """通用模板：固定字段走列，自定义字段由 extra 承载（值在校验阶段注入）。"""
    exam_id: int
    name: str
    gender: str = ""
    id_type: str = "1"
    id_number: str
    phone: str
    email: str = ""
    class_name: str = ""
    college: str = ""
    extra: dict = None


class AuditIn(BaseModel):
    app_type: str
    action: str
    comment: str = ""


class BatchAuditIn(BaseModel):
    items: list
    action: str
    comment: str = ""


# ------------------------------------------------------------------ 工具

def table_of(app_type: str) -> str:
    if app_type == "computer":
        return "applications_computer"
    if app_type == "mandarin":
        return "applications_mandarin"
    if app_type == "generic":
        return "applications_generic"
    raise ApiError("报名类型不合法")


def app_tables() -> list:
    """三张报名表（顺序即业务默认顺序）。"""
    return [("computer", "applications_computer"),
            ("mandarin", "applications_mandarin"),
            ("generic", "applications_generic")]


def norm_gender(v: str) -> str:
    v = (v or "").strip()
    return {"1": "男", "2": "女", "男": "男", "女": "女"}.get(v, "")


# 证件号 / 证件类型校验统一走 ..validators（报名表与用户资料共用同一套规则）


def get_exam_for_apply(exam_id: int) -> dict:
    auto_close_exams()
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    if not exam_open_now(exam):
        raise ApiError("该批次当前不在报名开放期（或已截止），无法提交报名")
    return exam


# 校验器无条件要求的字段（对应官方模板标红项），管理员只能通过「关闭采集」去掉
# 从 ET.BASE_REQUIRED 派生，保证「后端校验」与「前端星号 / 校验规则」永远一致；
# 改动必填项请改 exam_types.BASE_REQUIRED，不要在这里另写一份。
FIXED_REQUIRED = {b: set(v) for b, v in ET.BASE_REQUIRED.items()}


def _field_rule(code: str, base: str):
    """返回（采集字段集合, 必填字段集合），已叠加考试类型的字段配置。"""
    return set(ET.fields_of(code)), FIXED_REQUIRED[base] | set(ET.required_of(code))


def validate_computer(body: ComputerIn, allow_unknown_org: bool = False,
                      code: str = "computer") -> dict:
    data = body.model_dump()
    on, req = _field_rule(code, "computer")

    def val(key, label, lo, hi):
        """按类型配置取值：关闭采集的字段清空且不校验；必填项（含管理员追加）强制校验。"""
        if key not in on:
            return ""
        v = data.get(key)
        v = (v or "").strip() if isinstance(v, str) else (v or "")
        if key in req and (not v or len(v) < lo):
            raise ApiError(f"{label}为必填项")
        if v and len(v) > hi:
            raise ApiError(f"{label}长度不能超过 {hi} 个字符")
        return v

    org_code = val("org_code", "考试机构编码", 2, 20)
    if org_code:
        org = db.query_one("SELECT * FROM dict_exam_org WHERE code=?", (org_code,))
        if not org:
            if not allow_unknown_org:
                raise ApiError("考试机构编码不存在，请从下拉列表选择")
            # 批量导入时用户自带的单位编码（如 6501）可能不在初始示例字典中，
            # 自动登记，避免「编码不存在」卡死真实数据
            db.execute("INSERT OR IGNORE INTO dict_exam_org(code,name) VALUES(?,?)",
                       (org_code, org_code))
            org = {"code": org_code, "name": org_code}
        data["org_name"] = org["name"]
    else:
        data["org_name"] = ""
    data["org_code"] = org_code
    data["exam_site_code"] = val("exam_site_code", "考点编码", 4, 12)
    if data["exam_site_code"] and not SITE_RE.match(data["exam_site_code"]):
        raise ApiError("考点编码应为 4-12 位数字或字母")
    data["name"] = val("name", "姓名", 2, 50)
    data["gender"] = norm_gender(data["gender"])
    if "gender" in on and not data["gender"]:
        raise ApiError("性别需选择男或女")
    data["id_type"] = check_id_type(data["id_type"])
    data["id_number"] = validate_id_number(data["id_type"], data["id_number"])
    data["subject"] = val("subject", "报考科目", 2, 100)
    if data["subject"] and not db.query_one("SELECT id FROM dict_subject WHERE name=?",
                                            (data["subject"],)):
        raise ApiError("报考科目不在字典范围内，请从下拉列表选择")
    data["phone"] = val("phone", "手机号码", 11, 11)
    if data["phone"] and not PHONE_RE.match(data["phone"]):
        raise ApiError("手机号码格式不正确（应为 11 位且第二位为 3-9）")
    data["email"] = val("email", "Email", 0, 100)
    if data["email"] and not EMAIL_RE.match(data["email"]):
        raise ApiError("Email 格式不正确")
    data["school"] = val("school", "就读或者毕业院校", 1, 100)
    data["class_name"] = val("class_name", "班级", 1, 50)
    data["education"] = val("education", "学历", 1, 50)
    data["address"] = val("address", "通讯地址", 0, 255)
    return data


def validate_mandarin(body: MandarinIn, code: str = "mandarin") -> dict:
    data = body.model_dump()
    on, req = _field_rule(code, "mandarin")

    def val(key, label, lo, hi):
        if key not in on:
            return ""
        v = data.get(key)
        v = (v or "").strip() if isinstance(v, str) else (v or "")
        if key in req and (not v or len(v) < lo):
            raise ApiError(f"{label}为必填项")
        if v and len(v) > hi:
            raise ApiError(f"{label}长度不能超过 {hi} 个字符")
        return v

    data["name"] = val("name", "考生姓名", 2, 50)
    data["gender"] = norm_gender(data["gender"])
    if "gender" in on and not data["gender"]:
        raise ApiError("考生性别需选择男或女")
    data["id_type"] = check_id_type(data["id_type"])
    data["id_number"] = validate_id_number(data["id_type"], data["id_number"])
    data["occupation"] = val("occupation", "从事职业", 2, 50)
    if data["occupation"] and not db.query_one("SELECT id FROM dict_occupation WHERE name=?",
                                               (data["occupation"],)):
        raise ApiError("从事职业不在字典范围内，请从下拉列表选择")
    data["phone"] = val("phone", "联系电话", 11, 11)
    if data["phone"] and not PHONE_RE.match(data["phone"]):
        raise ApiError("联系电话格式不正确（应为 11 位且第二位为 3-9）")
    data["postcode"] = val("postcode", "邮政编码", 0, 6)
    if data["postcode"] and not POSTCODE_RE.match(data["postcode"].strip()):
        raise ApiError("邮政编码必须为 6 位数字")
    data["ethnicity"] = val("ethnicity", "考生民族", 1, 20)
    data["employer"] = val("employer", "所在单位", 1, 100)
    # 出生地 / 现居住地：官方模板标黑为选填；提供省时做省-市-县区一致性校验
    for prefix in ("birth", "live"):
        label = "出生地" if prefix == "birth" else "现居住地"
        if f"{prefix}_province" not in on:      # 整组被关闭采集
            data[f"{prefix}_province"] = data[f"{prefix}_city"] = data[f"{prefix}_county"] = ""
            continue
        p = (data.get(f"{prefix}_province") or "").strip()
        c = (data.get(f"{prefix}_city") or "").strip()
        co = (data.get(f"{prefix}_county") or "").strip()
        data[f"{prefix}_province"] = p
        if not p:
            data[f"{prefix}_city"], data[f"{prefix}_county"] = "", ""
            continue
        if c and not db.query_one("SELECT id FROM dict_region WHERE province=? AND city=?", (p, c)):
            raise ApiError(f"{label}所在市与所选省份不匹配")
        if c and co and not db.query_one(
                "SELECT id FROM dict_region WHERE province=? AND city=? AND county=?",
                (p, c, co)):
            raise ApiError(f"{label}所在县(区)与所选市不匹配")
        data[f"{prefix}_city"], data[f"{prefix}_county"] = c, co
    for k in ("student_no", "class_name", "department", "contact_address",
              "mail_address"):
        data[k] = "" if k not in on else (data.get(k) or "").strip()
    return data


def parse_extra(raw) -> dict:
    """extra 列 -> dict；脏数据返回空字典，不让整条记录渲染失败。"""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def validate_generic(body: GenericIn, code: str = "generic") -> dict:
    """通用模板校验：固定字段按列校验，自定义字段校验后塞进 extra。"""
    data = body.model_dump()
    on, req = _field_rule(code, "generic")

    def val(key, label, lo, hi):
        if key not in on:
            return ""
        v = data.get(key)
        v = (v or "").strip() if isinstance(v, str) else (v or "")
        if key in req and (not v or len(v) < lo):
            raise ApiError(f"{label}为必填项")
        if v and len(v) > hi:
            raise ApiError(f"{label}长度不能超过 {hi} 个字符")
        return v

    data["name"] = val("name", "姓名", 2, 50)
    data["gender"] = norm_gender(data["gender"])
    if "gender" in on and data["gender"] in req and not data["gender"]:
        raise ApiError("性别需选择男或女")
    data["id_type"] = check_id_type(data["id_type"])
    data["id_number"] = validate_id_number(data["id_type"], data["id_number"])
    data["phone"] = val("phone", "手机号码", 11, 11)
    if data["phone"] and not PHONE_RE.match(data["phone"]):
        raise ApiError("手机号码格式不正确（应为 11 位且第二位为 3-9）")
    data["email"] = val("email", "电子邮箱", 0, 100)
    if data["email"] and not EMAIL_RE.match(data["email"]):
        raise ApiError("电子邮箱格式不正确")
    data["class_name"] = val("class_name", "班级", 1, 50)
    data["college"] = val("college", "院系", 1, 100)

    extra, err = ET.clean_custom_values(code, data.get("extra"))
    if err:
        raise ApiError(err)
    data["extra"] = json.dumps(extra, ensure_ascii=False)
    return data


def decorate_app(row: dict, exam: dict = None, with_user: bool = False) -> dict:
    r = dict(row)
    r["audit_status_label"] = AUDIT_LABEL.get(r["audit_status"], r["audit_status"])
    # app_type 必须是基础类型（决定存储表），不能是自定义编码
    r["app_type"] = ET.base_type_of(exam["exam_type"]) if exam else None
    if exam:
        r["exam_name"] = exam["name"]
        r["exam_type"] = exam["exam_type"]
        r["exam_type_base"] = ET.base_type_of(exam["exam_type"])
        r["exam_type_label"] = ET.label_of(exam["exam_type"])
    if with_user:
        u = db.query_one("SELECT username,role FROM users WHERE id=?", (r["user_id"],))
        r["username"] = u["username"] if u else ""
    # 通用模板：把 extra 展开成 {key: value}，并把自定义字段的显示值一并给出
    if r.get("app_type") == "generic":
        extra = parse_extra(r.get("extra"))
        r["extra"] = extra
        labels = {}
        for cf in ET.custom_fields_of(exam["exam_type"] if exam else ""):
            v = extra.get(cf["key"], "" if cf["type"] != "multiselect" else [])
            if isinstance(v, list):
                v = "、".join(str(x) for x in v)
            labels[cf["label"]] = v
        r["custom_values"] = labels
    return r


def _scope_guard(user, table: str, app_id: int):
    """校验单条记录是否落在当前用户的数据范围内。"""
    frag, params = scope_filter(user, table)
    row = db.query_one(f"SELECT id FROM {table} WHERE id=?{frag}", tuple([app_id] + params))
    if not row:
        raise ApiError("报名记录不存在或不在您的数据范围内", code=404, status=404)


# ------------------------------------------------------------------ 报名

@router.post("")
def submit(body: dict, user=Depends(require_perms("apply"))):
    exam_id = body.get("exam_id")
    if not exam_id:
        raise ApiError("缺少考试批次")
    exam = get_exam_for_apply(int(exam_id))
    # 管理员若开启「实名通过才可报名」，未通过认证的账号在这里被拦下
    from .. import realname as RN
    RN.assert_can_apply(user)
    tbl = table_of(ET.base_type_of(exam["exam_type"]))
    if db.query_one(f"SELECT id FROM {tbl} WHERE exam_id=? AND user_id=?", (exam["id"], user["id"])):
        raise ApiError("您已报名该考试批次，不可重复报名")

    base = ET.base_type_of(exam["exam_type"])
    if base == "computer":
        data = validate_computer(ComputerIn(**body), code=exam["exam_type"])
        cols = COMPUTER_FIELDS + ["org_name"]
    elif base == "mandarin":
        data = validate_mandarin(MandarinIn(**body), code=exam["exam_type"])
        cols = MANDARIN_FIELDS
    else:
        # 自定义字段既支持整体放 extra，也支持与固定字段平铺提交
        payload = dict(body)
        if not isinstance(payload.get("extra"), dict):
            keys = set(ET.custom_keys_of(exam["exam_type"]))
            payload["extra"] = {k: v for k, v in payload.items() if k in keys}
        data = validate_generic(GenericIn(**payload), code=exam["exam_type"])
        cols = GENERIC_FIELDS + ["extra"]
    values = [data[c] for c in cols]
    sql = (f"INSERT INTO {tbl}(exam_id,user_id," + ",".join(cols) +
           ",audit_status,created_at,updated_at) VALUES(" +
           ",".join(["?"] * (2 + len(cols))) + ",'pending',?,?)")
    ts = db.now_str()
    try:
        rid = db.execute(sql, tuple([exam["id"], user["id"]] + values + [ts, ts]))
    except Exception as e:
        raise ApiError(f"提交失败：{e}")
    return ok({"id": rid, "audit_status": "pending"}, "报名提交成功，请等待审核")


@router.get("/mine")
def mine(user=Depends(get_current_user)):
    auto_close_exams()
    out = []
    for t, tbl in app_tables():
        rows = db.query(f"SELECT * FROM {tbl} WHERE user_id=? ORDER BY id DESC", (user["id"],))
        for r in rows:
            exam = db.query_one("SELECT * FROM exams WHERE id=?", (r["exam_id"],))
            item = decorate_app(r, exam)
            item["can_edit"] = r["audit_status"] in ("pending", "returned") and exam and exam_open_now(exam)
            out.append(item)
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return ok(out)


@router.get("/my-scope")
def my_scope(user=Depends(get_current_user)):
    """当前账号的权限组与数据范围（前端用于渲染菜单/按钮与范围提示）。"""
    return ok(describe(user))


# ------------------------------------------------------------------ 批量导入

def _peek_rows(rows, n: int) -> list:
    """预读前 n 行用于探测表头所在行（有的文件首行是大标题）。"""
    out = []
    for _ in range(n):
        try:
            out.append(next(rows))
        except StopIteration:
            break
    return out


def _friendly_validation_error(e) -> str:
    """把 Pydantic 的英文校验错误翻译成中文，别把堆栈丢给用户。"""
    missing = []
    try:
        for err in e.errors():
            if err.get("type") == "missing" and (err.get("loc") or ()):
                missing.append(FIELD_LABEL.get(err["loc"][0], str(err["loc"][0])))
    except Exception:
        pass
    if missing:
        return ("缺少必需字段：" + "、".join(missing)
                + "（可在导入预览界面为其指定统一默认值，或在表格中补上该列）")
    return "数据校验未通过：" + str(e).splitlines()[0]


def _resolve_mapping(header_row, exam_type: str, mapping_json: str = "",
                     type_code: str = "") -> dict:
    """列下标 -> 字段。优先用前端确认/修正后的映射，否则自动模糊识别。

    type_code 为该批次的类型编码：把自定义字段名也作为别名，
    这样管理员改过名之后导出的模板仍能自动识别。
    通用模板的自定义字段 key 一并通过 extra_fields 参与匹配。
    """
    extra = ET.custom_keys_of(type_code) if type_code else []
    if mapping_json:
        try:
            ov = json.loads(mapping_json)
        except Exception:
            raise ApiError("列映射参数格式不正确")
        fields = fields_of(exam_type, extra)
        col_field = {}
        for k, v in (ov or {}).items():
            try:
                idx = int(k)
            except (TypeError, ValueError):
                continue
            if v in fields and v not in col_field.values():
                col_field[idx] = v
        return col_field
    labels = None
    if type_code:
        labels = {f: ET.field_label(type_code, f) for f in fields_of(exam_type, extra)}
        # 自定义字段的显示名同样是合法表头
        for cf in ET.custom_fields_of(type_code):
            labels[cf["key"]] = cf["label"]
    am = auto_map(header_row, exam_type, labels, extra)
    return {i: info["field"] for i, info in am.items()}


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def _ensure_student_user(name: str, id_number: str, phone: str, college: str, class_name: str,
                         created: list, grade: str = "") -> int:
    """为批量导入的学生建立/复用考生账号，返回 user_id。

    **必须写入 id_number**：批量导入证件照按证件号匹配，且本函数靠它做同一考生的
    去重。早期版本漏写，会导致「按证件号匹配照片全部落空 + 重复导入建多个账号」。
    """
    # 用证件号后 8 位作为用户名，避免与既有账号冲突
    base = "s" + re.sub(r"\W", "", id_number)[-8:]

    exist = None
    if _has_column("users", "id_number"):
        exist = db.query_one("SELECT id FROM users WHERE id_number=?", (id_number,))
    if not exist:
        # 兼容历史账号：早期导入没写 id_number，按派生用户名找回并补写，
        # 否则同一考生再次导入会被当成新人、建出重复账号
        exist = db.query_one("SELECT id FROM users WHERE username=?", (base,))
        if exist and _has_column("users", "id_number"):
            db.execute("UPDATE users SET id_number=?,updated_at=? WHERE id=?",
                       (id_number, db.now_str(), exist["id"]))
    if exist:
        # 已有账号：只补还空着的院系 / 班级 / 年级，不覆盖已有值
        row = db.query_one("SELECT * FROM users WHERE id=?", (exist["id"],))
        sets, params = [], []
        for col, val in (("college", college), ("class_name", class_name), ("grade", grade)):
            if val and not (row.get(col) or "").strip():
                sets.append(f"{col}=?")
                params.append(val)
        if sets:
            params += [db.now_str(), exist["id"]]
            db.execute(f"UPDATE users SET {','.join(sets)},updated_at=? WHERE id=?",
                       tuple(params))
        return exist["id"]

    username, i = base, 1
    while db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        i += 1
        username = f"{base}_{i}"
    pwd = re.sub(r"\W", "", id_number)[-6:] or "Exam@123"
    if len(pwd) < 8:
        pwd = (pwd + "Exam@123")[:12]
    ts = db.now_str()
    cols = ["username", "password_hash", "real_name", "phone", "email", "role",
            "grade", "college", "class_name", "status", "created_at", "updated_at"]
    vals = [username, hash_password(pwd), name, phone, "", "candidate", grade,
            college, class_name, 1, ts, ts]
    if _has_column("users", "id_number"):
        cols.insert(7, "id_number")
        vals.insert(7, id_number)
    uid = db.execute(
        "INSERT INTO users(" + ",".join(cols) + ") VALUES(" +
        ",".join("?" for _ in cols) + ")", tuple(vals))
    created.append({"username": username, "initial_password": pwd, "name": name,
                    "id_number": id_number, "class_name": class_name, "college": college})
    return uid


def _has_column(table: str, col: str) -> bool:
    return col in {r["name"] for r in db.query(f"PRAGMA table_info({table})")}


def _apply_scope_defaults(user, exam_type: str, data: dict):
    """班主任导入时，强制把管理范围写入数据，避免越界导入。

    班主任可管理多个班级：导入行的班级取自文件，但必须落在班主任的管理班级列表内；
    未填班级时默认归入第一个管理班级。
    """
    from ..permissions import _split_classes
    if user["role"] == "head_teacher":
        my_classes = _split_classes(user.get("classes") or user.get("class_name") or "")
        if not my_classes:
            raise ApiError("当前班主任账号未绑定班级，无法批量导入")
        fc = (data.get("class_name") or "").strip()
        if not fc:
            fc = my_classes[0]
        if fc not in my_classes:
            raise ApiError(f"班级「{fc}」不在您的管理范围内（可管理：{('、'.join(my_classes))}）")
        data["class_name"] = fc
        if exam_type == "computer":
            data["school"] = (user.get("college") or "").strip() or data.get("school", "")
        elif exam_type == "generic":
            data["college"] = (user.get("college") or "").strip() or data.get("college", "")
        else:
            data["department"] = (user.get("college") or "").strip() or data.get("department", "")


@router.get("/import-template")
def import_template(exam_id: int = Query(...), user=Depends(require_perms("import"))):
    """下载批量导入模板（表头严格对齐官方模板，附 1 行示例）。"""
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    et = ET.base_type_of(exam["exam_type"])
    # 通用模板没有官方字典，表头 = 固定字段标签 + 自定义字段标签
    if et == "generic":
        d = {"headers": [], "orgs": [], "subjects": [], "occupations": [], "regions": []}
        allf, official = ET.BASE_FIELDS["generic"], []
    else:
        from ..config import load_dicts
        d = load_dicts()[et]
        allf = ET.BASE_FIELDS[et]
        official = d["headers"]
    # 内置模板沿用官方表头；自定义类型按配置去掉关闭采集的列并应用改名
    fields = ET.fields_of(exam["exam_type"])
    official_map = dict(zip(allf, official)) if len(official) == len(allf) else {}
    cfg = ET.config_of(exam["exam_type"])
    headers = [(cfg.get(f) or {}).get("label")
               or official_map.get(f) or ET.default_label(et, f) for f in fields]
    customs = ET.custom_fields_of(exam["exam_type"])
    headers += [c["label"] for c in customs]
    wb = Workbook()
    ws = wb.active
    ws.title = "报名数据"
    ws.append(headers)
    sample = _sample_row(ET.base_type_of(exam["exam_type"]))
    ws.append([sample.get(f, "") for f in fields]
              + [_sample_custom(c) for c in customs])
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill = IMPORT_HEADER_FILL
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
    hint = ws.cell(row=3, column=1)
    hint.value = ("填写说明：第 2 行为示例数据，导入前请删除；表头顺序可调整，系统按表头名称识别字段。"
                  "带 * 字段为必填。导入范围：" + scope_label(user))
    hint.fill = IMPORT_HINT_FILL

    ws2 = wb.create_sheet("可选值")
    if et == "generic":
        # 通用模板：把自定义字段的选项列出来，方便填写时对照
        for cf in customs:
            if cf["type"] in ("select", "multiselect") and cf["options"]:
                ws2.append([cf["label"] + ("（可多选，用、分隔）"
                                           if cf["type"] == "multiselect" else "")])
                for o in cf["options"]:
                    ws2.append([o])
                ws2.append([])
        if not customs:
            ws2.append(["该考试类型尚未配置自定义字段"])
    elif ET.base_type_of(exam["exam_type"]) == "computer":
        ws2.append(["考试机构编码", "考试机构名称"])
        for o in d["orgs"]:
            ws2.append([o["code"], o["name"]])
        ws2.append([])
        ws2.append(["报考科目"])
        for s in d["subjects"]:
            ws2.append([s])
    else:
        ws2.append(["从事职业"])
        for o in d["occupations"]:
            ws2.append([o])
        ws2.append([])
        ws2.append(["省级", "市级", "县区级"])
        for r in d["regions"]:
            for c in r["cities"]:
                if c["counties"]:
                    for co in c["counties"]:
                        ws2.append([r["province"], c["name"], co])
                else:
                    ws2.append([r["province"], c["name"], ""])
            if not r["cities"]:
                ws2.append([r["province"], "", ""])
    ws2.column_dimensions["A"].width = 24
    ws2.column_dimensions["B"].width = 28
    ws2.column_dimensions["C"].width = 28

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    tmpl_name = {"computer": "计算机", "mandarin": "普通话",
                 "generic": "通用"}.get(et, "通用")
    name = f"批量导入模板-{tmpl_name}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{_q(name)}"})


def _q(s: str) -> str:
    from urllib.parse import quote
    return quote(s)


def _sample_row(exam_type: str) -> dict:
    if exam_type == "computer":
        return {"org_code": "1001", "exam_site_code": "100101", "name": "张三", "gender": "男",
                "id_type": "1", "id_number": "320102200301011234", "subject": "计算机一级",
                "school": "计算机学院", "class_name": "计算机2101", "education": "本科",
                "phone": "13900000000", "email": "zhangsan@example.com",
                "address": "江苏省南京市鼓楼区1号"}
    if exam_type == "generic":
        return {"name": "王五", "gender": "男", "id_type": "1",
                "id_number": "320102200303031234", "phone": "13900000002",
                "email": "wangwu@example.com", "class_name": "计科2101",
                "college": "计算机学院"}
    return {"name": "李四", "gender": "女", "ethnicity": "汉族", "id_type": "1",
            "id_number": "320102200302021234", "occupation": "学生", "employer": "计算机学院",
            "phone": "13900000001", "student_no": "20220001", "class_name": "计算机2101",
            "department": "计算机学院", "contact_address": "江苏省南京市鼓楼区1号",
            "mail_address": "江苏省南京市鼓楼区1号", "postcode": "210000",
            "birth_province": "江苏省", "birth_city": "南京市", "birth_county": "鼓楼区",
            "live_province": "江苏省", "live_city": "南京市", "live_county": "鼓楼区"}


def _import_field_label(code: str, field: str) -> str:
    """导入界面里字段的显示名：自定义字段取其配置名，固定字段走类型配置。"""
    for cf in ET.custom_fields_of(code):
        if cf["key"] == field:
            return cf["label"]
    return ET.field_label(code, field)


def _sample_custom(cf: dict) -> str:
    """给自定义字段生成一行示例值（按类型给最直观的写法）。"""
    if cf["type"] == "select":
        return cf["options"][0] if cf["options"] else "选项一"
    if cf["type"] == "multiselect":
        return "、".join(cf["options"][:2]) if cf["options"] else "选项一"
    if cf["type"] == "number":
        return "0"
    if cf["type"] == "date":
        return "2026-01-01"
    if cf["type"] == "textarea":
        return "示例文本"
    return "示例文本"


@router.post("/import/preview")
async def import_preview(exam_id: int = Form(...), file: UploadFile = File(...),
                         user=Depends(require_perms("import"))):
    """解析上传文件并返回列识别结果，供导入前确认/手工修正。

    支持任意自定义表头：逐列给出「源表头 → 识别到的字段 + 置信度 + 样例值」，
    未识别的列与缺失的必填字段一并返回，前端可下拉调整后把 mapping 回传给 /import。
    """
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    raw = await file.read()
    if not raw:
        raise ApiError("上传文件为空")
    if len(raw) > 20 * 1024 * 1024:
        raise ApiError("文件过大（上限 20MB），请分批导入")
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ApiError("无法解析该文件，请使用 .xlsx 格式（可先下载导入模板）")

    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    head_rows = _peek_rows(rows, 5)
    if not head_rows:
        raise ApiError("文件中没有内容")
    et = ET.base_type_of(exam["exam_type"])
    extra = ET.custom_keys_of(exam["exam_type"])
    hr = detect_header_row(head_rows, et, extra)
    header_row = head_rows[hr] or ()
    labels = {f: ET.field_label(exam["exam_type"], f) for f in fields_of(et, extra)}
    for cf in ET.custom_fields_of(exam["exam_type"]):
        labels[cf["key"]] = cf["label"]
    am = auto_map(header_row, et, labels, extra)

    # 统计总行数并留存前若干行做样例
    total_rows, data_rows = 0, []
    for r in itertools.chain(head_rows[hr + 1:], rows):
        if r is None or all(v is None or str(v).strip() == "" for v in r):
            continue
        total_rows += 1
        if len(data_rows) < 50:
            data_rows.append(r)

    samples = {i: [] for i in range(len(header_row))}
    for r in data_rows[:20]:
        for i in range(len(header_row)):
            if i < len(r) and str(r[i] or "").strip() and len(samples[i]) < 3:
                samples[i].append(str(r[i]).strip())

    # 按该考试类型的配置取字段：关闭采集的不出现在可映射列表，必填含管理员追加项
    type_code = exam["exam_type"]
    fields = ET.fields_of(type_code) + [c["key"] for c in ET.custom_fields_of(type_code)]
    req_fields = set(ET.required_of(type_code)) | {
        c["key"] for c in ET.custom_fields_of(type_code) if c["required"]}
    required = [f for f in fields if f in req_fields]
    mapping = []
    for i, h in enumerate(header_row):
        info = am.get(i)
        mapping.append({
            "col": i, "header": str(h or ""),
            "field": info["field"] if info else None,
            "confidence": info["confidence"] if info else 0.0,
            "samples": samples.get(i, []),
        })
    detected = {v["field"] for v in mapping if v["field"]}
    col_field = {v["col"]: v["field"] for v in mapping if v["field"]}

    # 前 3 行按识别结果转换后的效果，便于用户确认转换是否正确
    preview_rows = []
    for r in data_rows[:3]:
        d = {f: (_cell(r[idx]) if idx < len(r) else "") for idx, f in col_field.items()}
        d, _ = convert_row(d, et)
        preview_rows.append(d)

    return ok({
        "header_row": hr,
        "total_rows": total_rows,
        "mapping": mapping,
        "fields": [{"key": f, "label": _import_field_label(type_code, f),
                    "required": f in req_fields} for f in fields],
        "missing_required": [f for f in required if f not in detected],
        "unmapped_headers": [v["header"] for v in mapping if not v["field"] and v["header"]],
        "preview_rows": preview_rows,
    })


@router.post("/import")
async def batch_import(exam_id: int = Form(...), audit_status: str = Form("approved"),
                       create_accounts: int = Form(1), mapping: str = Form(""),
                       defaults: str = Form(""), file: UploadFile = File(...),
                       user=Depends(require_perms("import"))):
    """批量导入报名数据（管理权限级别：管理员 / 班主任）。

    - 支持任意自定义表头：自动模糊识别列（别名 + 关键词），也可由前端传入
      `mapping`（JSON：{列下标: 字段名}）覆盖自动识别结果
    - 自动转换数据格式：性别、证件类型、手机号、证件号、省市区名称、科目/职业/学历/民族
    - 自动为考生建立账号（可用 create_accounts=0 关闭）
    - 返回成功/失败明细，失败原因落库供下载错误报告
    """
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    if audit_status not in ("pending", "approved"):
        raise ApiError("导入后的审核状态只能是 pending 或 approved")

    raw = await file.read()
    if not raw:
        raise ApiError("上传文件为空")
    if len(raw) > 20 * 1024 * 1024:
        raise ApiError("文件过大（上限 20MB），请分批导入")
    try:
        wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise ApiError("无法解析该文件，请使用 .xlsx 格式（可先下载导入模板）")

    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    head_rows = _peek_rows(rows, 5)
    if not head_rows:
        raise ApiError("文件中没有内容")
    et = ET.base_type_of(exam["exam_type"])
    extra = ET.custom_keys_of(exam["exam_type"])
    hr = detect_header_row(head_rows, et, extra)
    header_row = head_rows[hr]
    rest = itertools.chain(head_rows[hr + 1:], rows)

    col_field = _resolve_mapping(header_row, ET.base_type_of(exam["exam_type"]), mapping,
                                 exam["exam_type"])
    if not col_field:
        raise ApiError("未识别到任何有效表头，请在预览界面手工指定各列对应的字段")

    # 整批默认值：表里没有该列时（如考试机构编码/考点编码），由管理员统一指定
    default_vals = {}
    if defaults:
        try:
            default_vals = json.loads(defaults)
        except Exception:
            raise ApiError("默认值参数格式不正确")
        # 关闭采集的字段不接受默认值；通用模板的自定义字段可以用默认值统一补齐
        fs = set(ET.fields_of(exam["exam_type"])) | set(extra)
        default_vals = {k: str(v).strip() for k, v in (default_vals or {}).items()
                        if k in fs and str(v).strip()}

    if et == "computer":
        required = ["name", "id_number", "phone", "subject"]
    elif et == "mandarin":
        required = ["name", "id_number", "phone", "occupation"]
    else:
        required = ["name", "id_number", "phone"]
    missing = [f for f in required if f not in col_field.values()]
    # 必填的自定义字段：列里没有、又没给整批默认值时才算缺列
    for cf in ET.custom_fields_of(exam["exam_type"]):
        if cf["required"] and cf["key"] not in col_field.values() \
                and cf["key"] not in default_vals:
            missing.append(cf["key"])
    if missing:
        label = {"name": "姓名", "id_number": "证件号码", "phone": "手机号码",
                 "subject": "报考科目", "occupation": "从事职业",
                 "birth_province": "出生地省", "live_province": "现居住地省"}
        for cf in ET.custom_fields_of(exam["exam_type"]):
            label[cf["key"]] = cf["label"]
        raise ApiError("模板缺少必需列：" + "、".join(label.get(m, m) for m in missing))

    tbl = table_of(ET.base_type_of(exam["exam_type"]))
    ts = db.now_str()
    batch_id = db.execute(
        "INSERT INTO import_batches(exam_id,operator_id,operator_name,filename,total,success,"
        "failed,created_at) VALUES(?,?,?,?,0,0,0,?)",
        (exam_id, user["id"], user["real_name"] or user["username"],
         (file.filename or "")[:120], ts))

    total = success = failed = 0
    errors, created_accounts, seen_ids = [], [], set()
    for row_no, raw_row in enumerate(rest, start=hr + 2):
        if raw_row is None or all(v is None or str(v).strip() == "" for v in raw_row):
            continue
        total += 1
        data = {}
        for idx, f in col_field.items():
            data[f] = _cell(raw_row[idx]) if idx < len(raw_row) else ""
        if ET.base_type_of(exam["exam_type"]) == "computer":
            data.setdefault("id_type", "1")
            data.setdefault("gender", "")
        else:
            data.setdefault("id_type", "1")
        # 自动转换：性别/证件类型/手机号/证件号/省市区/科目·职业·学历·民族
        data, _converted = convert_row(data, ET.base_type_of(exam["exam_type"]))
        # 该行缺失且管理员指定了整批默认值的字段，用默认值补齐
        for k, v in default_vals.items():
            if not str(data.get(k) or "").strip():
                data[k] = v

        name, idno = data.get("name", ""), data.get("id_number", "")
        try:
            if not ID_RE.match(idno):
                raise ApiError("证件号码格式不正确")
            if idno in seen_ids:
                raise ApiError("文件内证件号码重复")
            seen_ids.add(idno)
            _apply_scope_defaults(user, ET.base_type_of(exam["exam_type"]), data)
            # 同批次证件号去重（含跨账号、跨导入批次）
            if db.query_one(f"SELECT id FROM {tbl} WHERE exam_id=? AND id_number=?",
                            (exam_id, idno)):
                raise ApiError("该考生在本批次已存在报名记录")

            if et == "computer":
                payload = {k: v for k, v in data.items() if k in COMPUTER_FIELDS}
                payload["exam_id"] = exam_id
                vdata = validate_computer(ComputerIn(**payload), allow_unknown_org=True,
                                          code=exam["exam_type"])
            elif et == "mandarin":
                payload = {k: v for k, v in data.items() if k in MANDARIN_FIELDS}
                payload["exam_id"] = exam_id
                vdata = validate_mandarin(MandarinIn(**payload), code=exam["exam_type"])
            else:
                payload = {k: v for k, v in data.items() if k in GENERIC_FIELDS}
                payload["exam_id"] = exam_id
                payload["extra"] = {k: v for k, v in data.items() if k in extra}
                vdata = validate_generic(GenericIn(**payload), code=exam["exam_type"])

            if create_accounts:
                # grade 来自导入表的「年级」列或整批默认值；报名表本身不存年级，
                # 只写到考生账号上，供「本年级」数据范围过滤使用。
                uid = _ensure_student_user(vdata["name"], idno, vdata.get("phone", ""),
                                           (user.get("college") or "") if user["role"] != "admin"
                                           else (vdata.get("college") or vdata.get("department")
                                                 or vdata.get("school") or ""),
                                           vdata.get("class_name", ""), created_accounts,
                                           grade=str(data.get("grade") or "").strip())
            else:
                uid = 0
                if db.query_one(f"SELECT id FROM {tbl} WHERE exam_id=? AND user_id=0", (exam_id,)):
                    raise ApiError("已存在未关联账号的导入记录，请开启「同时创建考生账号」")

            if et == "computer":
                cols = COMPUTER_FIELDS + ["org_name"]
            elif et == "mandarin":
                cols = MANDARIN_FIELDS
            else:
                cols = GENERIC_FIELDS + ["extra"]
            db.execute(
                f"INSERT INTO {tbl}(exam_id,user_id," + ",".join(cols) +
                ",audit_status,created_at,updated_at) VALUES(" +
                ",".join(["?"] * (2 + len(cols))) + ",?,?,?)",
                tuple([exam_id, uid] + [vdata[c] for c in cols] + [audit_status, ts, ts]))
            success += 1
        except ApiError as e:
            failed += 1
            errors.append({"row_no": row_no, "name": name, "id_number": idno, "reason": e.message})
        except ValidationError as e:
            failed += 1
            errors.append({"row_no": row_no, "name": name, "id_number": idno,
                           "reason": _friendly_validation_error(e)})
        except Exception as e:  # noqa: BLE001
            failed += 1
            errors.append({"row_no": row_no, "name": name, "id_number": idno,
                           "reason": f"写入失败：{e}"})

    if errors:
        db.executemany(
            "INSERT INTO import_errors(batch_id,row_no,name,id_number,reason) VALUES(?,?,?,?,?)",
            [(batch_id, e["row_no"], e["name"], e["id_number"], e["reason"]) for e in errors])
    db.execute("UPDATE import_batches SET total=?,success=?,failed=? WHERE id=?",
               (total, success, failed, batch_id))

    msg = f"批量导入完成：共 {total} 行，成功 {success} 条，失败 {failed} 条"
    return ok({
        "batch_id": batch_id, "total": total, "success": success, "failed": failed,
        "errors": errors[:100], "error_total": len(errors),
        "created_accounts": created_accounts[:200],
        "created_account_total": len(created_accounts),
        "scope_label": scope_label(user),
    }, msg)


@router.get("/import-batches")
def import_batches(page: int = 1, page_size: int = 10, user=Depends(require_perms("import"))):
    page, page_size = page_params(page, page_size)
    where, params = "", []
    if scope_of(user) != "scope_all":
        where = " WHERE operator_id=?"
        params = [user["id"]]
    total = db.query_one(f"SELECT COUNT(*) c FROM import_batches{where}", tuple(params))["c"]
    rows = db.query(f"SELECT b.*, e.name AS exam_name, e.exam_type FROM import_batches b"
                    f" LEFT JOIN exams e ON e.id=b.exam_id{where.replace('operator_id', 'b.operator_id')}"
                    f" ORDER BY b.id DESC LIMIT ? OFFSET ?",
                    tuple(params) + (page_size, (page - 1) * page_size))
    return ok({"list": rows, "total": total, "page": page, "page_size": page_size})


@router.get("/import-batches/{batch_id}/errors")
def import_batch_errors(batch_id: int, user=Depends(require_perms("import"))):
    """下载某次导入的错误报告（xlsx）。"""
    batch = db.query_one("SELECT * FROM import_batches WHERE id=?", (batch_id,))
    if not batch:
        raise ApiError("导入批次不存在", code=404, status=404)
    if scope_of(user) != "scope_all" and batch["operator_id"] != user["id"]:
        raise ApiError("无权查看他人导入批次", code=403, status=403)
    rows = db.query("SELECT row_no,name,id_number,reason FROM import_errors WHERE batch_id=?"
                    " ORDER BY row_no", (batch_id,))
    wb = Workbook()
    ws = wb.active
    ws.title = "导入失败明细"
    ws.append(["原始行号", "姓名", "证件号码", "失败原因"])
    for r in rows:
        ws.append([r["row_no"], r["name"], r["id_number"], r["reason"]])
    for c in range(1, 5):
        cell = ws.cell(row=1, column=c)
        cell.fill = IMPORT_HEADER_FILL
        cell.font = Font(bold=True)
    ws.column_dimensions["A"].width = 10
    ws.column_dimensions["B"].width = 12
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 60
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    name = f"导入失败明细-批次{batch_id}.xlsx"
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{_q(name)}"})


# ------------------------------------------------------------------ 审核

@router.get("/audit-records")
def audit_records(app_type: str = "", page: int = 1, page_size: int = 20,
                  user=Depends(require_perms("audit"))):
    page, page_size = page_params(page, page_size)
    where, params = [], []
    if app_type:
        where.append("app_type=?")
        params.append(app_type)
    if not has_perm(user, "scope_all"):
        where.append("reviewer_id=?")
        params.append(user["id"])
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one(f"SELECT COUNT(*) c FROM audits{sql_where}", tuple(params))["c"]
    rows = db.query(f"SELECT * FROM audits{sql_where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    tuple(params) + (page_size, (page - 1) * page_size))
    return ok({"list": rows, "total": total, "page": page, "page_size": page_size})


@router.get("")
def list_apps(app_type: str = "", exam_id: int = 0, audit_status: str = "", keyword: str = "",
              page: int = 1, page_size: int = 10, user=Depends(get_current_user)):
    """报名列表：审核员 / 管理员 / 辅导员 / 班主任 均可用，结果按数据范围收敛。"""
    if not (has_perm(user, "audit") or has_perm(user, "import")):
        raise ApiError("无权查看报名数据", code=403, status=403)
    auto_close_exams()
    page, page_size = page_params(page, page_size)
    types = [app_type] if app_type else ["computer", "mandarin", "generic"]
    collected = []
    for t in types:
        tbl = table_of(t)
        where, params = [], []
        if exam_id:
            where.append("exam_id=?")
            params.append(int(exam_id))
        if audit_status:
            where.append("audit_status=?")
            params.append(audit_status)
        if keyword:
            where.append("(name LIKE ? OR id_number LIKE ? OR phone LIKE ?)")
            params += [f"%{keyword}%"] * 3
        frag, sparams = scope_filter(user, tbl)
        sql_where = (" WHERE " + " AND ".join(where)) if where else " WHERE 1=1"
        rows = db.query(f"SELECT * FROM {tbl}{sql_where}{frag} ORDER BY created_at DESC LIMIT 5000",
                        tuple(params + sparams))
        exam_cache = {}
        for r in rows:
            ex = exam_cache.get(r["exam_id"])
            if ex is None:
                ex = db.query_one("SELECT * FROM exams WHERE id=?", (r["exam_id"],))
                exam_cache[r["exam_id"]] = ex
            item = decorate_app(r, ex, with_user=True)
            item["app_type"] = t
            # 自定义类型显示其自定义名称，而不是底层模板名（找不到所属批次时退回模板名）
            item["app_type_label"] = item.get("exam_type_label") or TYPE_LABEL.get(t, t)
            collected.append(item)
    collected.sort(key=lambda x: x["created_at"], reverse=True)
    total = len(collected)
    start = (page - 1) * page_size
    return ok({"list": collected[start:start + page_size], "total": total,
               "page": page, "page_size": page_size,
               "scope_label": scope_label(user), "can_audit": has_perm(user, "audit")})


def _do_audit(app_type: str, app_id: int, action: str, comment: str, user: dict):
    if action not in ("approved", "rejected", "returned"):
        raise ApiError("审核动作不合法")
    comment = (comment or "").strip()
    if action in ("rejected", "returned") and not comment:
        raise ApiError("驳回或退回时必须填写审核意见")
    tbl = table_of(app_type)
    _scope_guard(user, tbl, app_id)
    row = db.query_one(f"SELECT * FROM {tbl} WHERE id=?", (app_id,))
    if row["audit_status"] == action:
        raise ApiError("该记录已是当前审核状态")
    ts = db.now_str()
    db.execute(f"UPDATE {tbl} SET audit_status=?,audit_comment=?,reviewed_by=?,reviewed_at=?,"
               f"updated_at=? WHERE id=?", (action, comment, user["id"], ts, ts, app_id))
    db.execute("INSERT INTO audits(app_type,app_id,reviewer_id,reviewer_name,action,comment,created_at)"
               " VALUES(?,?,?,?,?,?,?)",
               (app_type, app_id, user["id"], user["real_name"], action, comment, ts))


@router.post("/batch-audit")
def batch_audit(body: BatchAuditIn, user=Depends(require_perms("audit"))):
    if not body.items:
        raise ApiError("请至少选择一条报名记录")
    if len(body.items) > 500:
        raise ApiError("单次批量审核不超过 500 条")
    success, errors = 0, []
    for item in body.items:
        try:
            _do_audit(item.get("app_type"), int(item.get("id")), body.action, body.comment, user)
            success += 1
        except ApiError as e:
            errors.append(f"#{item.get('id')}: {e.message}")
    msg = f"批量审核完成，成功 {success} 条"
    if errors:
        msg += f"，失败 {len(errors)} 条"
    return ok({"success": success, "errors": errors[:20]}, msg)


@router.get("/{app_id}")
def detail(app_id: int, app_type: str = Query(""), user=Depends(get_current_user)):
    """报名详情。app_type 可省略（深链场景），系统会自动判定所属表。"""
    if app_type:
        tbl = table_of(app_type)
        row = db.query_one(f"SELECT * FROM {tbl} WHERE id=?", (app_id,))
    else:
        row, tbl = None, None
        for t in ("computer", "mandarin", "generic"):
            cand = db.query_one(f"SELECT * FROM {table_of(t)} WHERE id=?", (app_id,))
            if cand:
                row, tbl, app_type = cand, table_of(t), t
                break
    if not row:
        raise ApiError("报名记录不存在", code=404, status=404)
    if row["user_id"] != user["id"]:
        # 非本人：必须是具备审核/管理权限且落在数据范围内的账号
        if not (has_perm(user, "audit") or has_perm(user, "import")):
            raise ApiError("无权查看他人报名记录", code=403, status=403)
        _scope_guard(user, tbl, app_id)
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (row["exam_id"],))
    item = decorate_app(row, exam, with_user=True)
    item["app_type"] = app_type
    item["app_type_label"] = TYPE_LABEL.get(app_type, app_type)
    # 字段元信息：审核/我的报名详情页据此只展示该类型实际采集的字段并使用自定义名称
    item["fields"] = ET.fields_meta(exam["exam_type"]) if exam else []
    item["audits"] = db.query(
        "SELECT a.*, u.real_name FROM audits a LEFT JOIN users u ON u.id=a.reviewer_id"
        " WHERE a.app_type=? AND a.app_id=? ORDER BY a.id DESC", (app_type, app_id))
    item["can_edit"] = (row["user_id"] == user["id"] and row["audit_status"] in ("pending", "returned")
                        and exam and exam_open_now(exam))
    item["can_audit"] = has_perm(user, "audit")
    return ok(item)


@router.put("/{app_id}")
def update_app(app_id: int, body: dict, app_type: str = Query(...),
               user=Depends(get_current_user)):
    tbl = table_of(app_type)
    row = db.query_one(f"SELECT * FROM {tbl} WHERE id=?", (app_id,))
    if not row:
        raise ApiError("报名记录不存在", code=404, status=404)
    if row["user_id"] != user["id"]:
        raise ApiError("无权修改他人报名记录", code=403, status=403)
    if row["audit_status"] not in ("pending", "returned"):
        raise ApiError("当前状态不允许修改（仅待审核或已退回可修改）")
    exam = get_exam_for_apply(row["exam_id"])

    if app_type == "computer":
        data = validate_computer(ComputerIn(**body), code=exam["exam_type"])
        cols = COMPUTER_FIELDS
    elif app_type == "mandarin":
        data = validate_mandarin(MandarinIn(**body), code=exam["exam_type"])
        cols = MANDARIN_FIELDS
    else:
        payload = dict(body)
        if not isinstance(payload.get("extra"), dict):
            keys = set(ET.custom_keys_of(exam["exam_type"]))
            payload["extra"] = {k: v for k, v in payload.items() if k in keys}
        data = validate_generic(GenericIn(**payload), code=exam["exam_type"])
        cols = GENERIC_FIELDS + ["extra"]
    sets = ",".join(f"{c}=?" for c in cols)
    db.execute(f"UPDATE {tbl} SET {sets},audit_status='pending',audit_comment='',reviewed_by=NULL,"
               f"reviewed_at=NULL,updated_at=? WHERE id=?",
               tuple([data[c] for c in cols]) + (db.now_str(), app_id))
    return ok({"id": app_id, "audit_status": "pending"}, "修改成功，已重新提交待审核")


@router.post("/{app_id}/withdraw")
def withdraw(app_id: int, app_type: str = Query(...), user=Depends(get_current_user)):
    tbl = table_of(app_type)
    row = db.query_one(f"SELECT * FROM {tbl} WHERE id=?", (app_id,))
    if not row:
        raise ApiError("报名记录不存在", code=404, status=404)
    # 本人可撤；管理员 / 审核员 / 班主任在其数据范围内也可代考生撤回
    if row["user_id"] != user["id"]:
        if not has_perm(user, "audit"):
            raise ApiError("无权操作他人报名记录", code=403, status=403)
        _scope_guard(user, tbl, app_id)   # 越权（不在范围内）一律按 404 处理
    if row["audit_status"] != "pending":
        raise ApiError("仅待审核状态的报名可以撤回")
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (row["exam_id"],))
    if not exam_open_now(exam):
        # 撤回是物理删除，窗口关闭后一律不允许（管理员请走审核「驳回」，保留记录）
        raise ApiError("报名窗口已关闭，无法撤回；如需处理该记录请走审核「驳回」")
    db.execute(f"DELETE FROM {tbl} WHERE id=?", (app_id,))
    db.execute("DELETE FROM audits WHERE app_type=? AND app_id=?", (app_type, app_id))
    return ok(None, "已撤回报名")


@router.put("/{app_id}/audit")
def audit(app_id: int, body: AuditIn, user=Depends(require_perms("audit"))):
    _do_audit(body.app_type, app_id, body.action, body.comment, user)
    return ok({"id": app_id, "audit_status": body.action},
              {"approved": "已通过", "rejected": "已驳回", "returned": "已退回"}[body.action])
