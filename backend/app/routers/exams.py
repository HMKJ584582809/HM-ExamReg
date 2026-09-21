# -*- coding: utf-8 -*-
"""模块2：新建考试（支持按月开放）与批次管理。"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db
from .. import exam_types as ET
# 照片要求的取值（尺寸 / 底色）以 idphoto/specs.py 为唯一事实源，
# 避免这里再写一遍枚举导致两边口径不一致
from ..idphoto import specs as IDS
from ..config import load_settings
from ..deps import ApiError, get_current_user, ok, page_params
from ..permissions import require_perms

router = APIRouter(prefix="/api/exams", tags=["exams"])

class _TypeLabel(dict):
    """按类型编码取显示名。

    自定义类型不在内置字典里，这里自动回查 exam_types 表，
    使 ``TYPE_LABEL[code]`` / ``.get(code)`` / ``code in TYPE_LABEL``
    对管理员新增的类型同样成立，历史调用点无需逐个修改。
    """

    def __getitem__(self, code):
        return ET.label_of(code)

    def get(self, code, default=None):
        return ET.label_of(code) if code else default

    def __contains__(self, code):
        return ET.exists(code)

    def items(self):
        return [(r["code"], r["name"]) for r in ET.all_types()]


TYPE_LABEL = _TypeLabel(computer="计算机类考试", mandarin="普通话水平测试")
STATUS_LABEL = {"draft": "草稿", "open": "报名中", "closed": "已截止", "archived": "已归档"}
STATUS_FLOW = {"draft": ["open", "archived"], "open": ["closed", "archived"],
               "closed": ["open", "archived"], "archived": []}


def _norm_photo_opt(v, kind: str) -> str:
    """校验并归一化照片要求取值（尺寸 / 底色）；空串表示「不限制」。"""
    s = (v or "").strip()
    if not s:
        return ""
    allowed = [x["key"] for x in (IDS.SIZES if kind == "size" else IDS.COLORS)]
    if s not in allowed:
        raise ApiError("照片要求取值不合法")
    return s


class ExamIn(BaseModel):
    exam_type: str
    exam_year: int
    exam_month: int
    name: str = ""
    signup_start_at: str
    signup_end_at: str
    description: str = ""
    status: str = "draft"
    # 本场考试的照片要求（空串 = 不限制）
    photo_size: str = ""
    photo_color: str = ""


class ExamUpdateIn(BaseModel):
    name: str = None
    signup_start_at: str = None
    signup_end_at: str = None
    description: str = None
    status: str = None
    exam_year: int = None
    exam_month: int = None
    photo_size: str = None
    photo_color: str = None


def _parse_dt(s: str):
    s = (s or "").strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ApiError("时间格式不正确，应为 YYYY-MM-DD HH:MM:SS")


def auto_close_exams():
    """报名窗口结束后自动置为 closed。"""
    now = db.now_str()
    db.execute("UPDATE exams SET status='closed',updated_at=? WHERE status='open' AND signup_end_at<?",
               (now, now))


def exam_open_now(exam: dict) -> bool:
    now = db.now_str()
    return exam["status"] == "open" and exam["signup_start_at"] <= now <= exam["signup_end_at"]


def decorate(exam: dict) -> dict:
    e = dict(exam)
    base = ET.base_type_of(e["exam_type"])
    e["exam_type_base"] = base          # 决定存储表 / 字段集 / 导出模板
    e["exam_type_label"] = ET.label_of(e["exam_type"])
    e["status_label"] = STATUS_LABEL.get(e["status"], e["status"])
    e["open_now"] = exam_open_now(e)
    e["signup_period"] = f"{e['signup_start_at']} ~ {e['signup_end_at']}"
    # 按基础模板路由到存储表：写死 computer/mandarin 会让通用模板的统计恒为 0
    tbl = ET.table_of(base)
    stat = db.query_one(
        f"SELECT COUNT(*) total, SUM(audit_status='pending') pending,"
        f" SUM(audit_status='approved') approved, SUM(audit_status='rejected') rejected,"
        f" SUM(audit_status='returned') returned FROM {tbl} WHERE exam_id=?", (e["id"],))
    e["stats"] = {k: (stat[k] or 0) for k in ("total", "pending", "approved", "rejected", "returned")}
    # 照片要求：带中文标签一并返回，前端直接展示「一寸照片、白底」，
    # 空串表示本场不限制（老库补列前有缺列的可能，用 .get 兜底）
    ps = (e.get("photo_size") or "").strip()
    pc = (e.get("photo_color") or "").strip()
    s_label = IDS.size_of(ps)["label"] if ps else ""
    c_label = IDS.color_of(pc)["label"] if pc else ""
    e["photo_requirement"] = {
        "size": ps, "color": pc,
        "size_label": s_label, "color_label": c_label,
        "text": "、".join(x for x in (s_label, c_label) if x) or "不限制",
        "limited": bool(ps or pc),
    }
    return e


@router.get("")
def list_exams(exam_type: str = "", year: int = 0, month: int = 0, status: str = "",
               keyword: str = "", page: int = 1, page_size: int = 50,
               user=Depends(get_current_user)):
    auto_close_exams()
    page, page_size = page_params(page, page_size)
    where, params = [], []
    is_admin = user["role"] in ("admin", "reviewer")
    if not is_admin:
        # 考生仅可见开放中的考试（报名期内）
        where.append("status='open' AND signup_start_at<=? AND signup_end_at>=?")
        now = db.now_str()
        params += [now, now]
    if exam_type:
        where.append("exam_type=?")
        params.append(exam_type)
    if year:
        where.append("exam_year=?")
        params.append(int(year))
    if month:
        where.append("exam_month=?")
        params.append(int(month))
    if status and is_admin:
        where.append("status=?")
        params.append(status)
    if keyword:
        where.append("name LIKE ?")
        params.append(f"%{keyword}%")
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one(f"SELECT COUNT(*) c FROM exams{sql_where}", tuple(params))["c"]
    rows = db.query(
        f"SELECT * FROM exams{sql_where} ORDER BY exam_year DESC, exam_month DESC, id DESC"
        f" LIMIT ? OFFSET ?", tuple(params) + (page_size, (page - 1) * page_size))
    return ok({"list": [decorate(r) for r in rows], "total": total, "page": page,
               "page_size": page_size})


@router.get("/options")
def options(user=Depends(get_current_user)):
    """年份/月份下拉数据。"""
    auto_close_exams()
    years = [r["exam_year"] for r in
             db.query("SELECT DISTINCT exam_year FROM exams ORDER BY exam_year DESC")]
    return ok({"years": years, "months": list(range(1, 13)),
               "types": [{"value": r["code"], "label": r["name"],
                          "base_type": r["base_type"]}
                         for r in ET.all_types(enabled_only=True)],
               "statuses": [{"value": k, "label": v} for k, v in STATUS_LABEL.items()]})


@router.get("/{exam_id}")
def exam_detail(exam_id: int, user=Depends(get_current_user)):
    auto_close_exams()
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    if user["role"] == "candidate" and not exam_open_now(exam):
        raise ApiError("该批次当前不在报名开放期", code=403, status=403)
    e = decorate(exam)
    e["fields"] = ET.fields_meta(e["exam_type"])  # 报名表单按类型渲染（仅详情附带，列表不带）
    # 表单默认值随批次下发：考生没有「系统设置」权限，读不到 /api/system/settings
    e["defaults"] = apply_defaults()
    return ok(e)


def apply_defaults() -> dict:
    """报名表单默认值（管理员可在系统维护里改）。"""
    seg = (load_settings().get("apply") or {})
    return {"employer": (seg.get("employer_default") or "").strip()}


@router.post("")
def create_exam(body: ExamIn, user=Depends(require_perms("exam_manage"))):
    trow = db.query_one("SELECT * FROM exam_types WHERE code=?", (body.exam_type,))
    if trow:
        if not trow["enabled"]:
            raise ApiError("该考试类型已停用，请在「考试类型管理」中启用")
    elif body.exam_type not in ET.BASE_TYPES:
        raise ApiError("考试类型不存在")
    if not 1 <= int(body.exam_month) <= 12:
        raise ApiError("月份需在 1-12 之间")
    start, end = _parse_dt(body.signup_start_at), _parse_dt(body.signup_end_at)
    if end <= start:
        raise ApiError("报名截止时间必须晚于开始时间")
    name = (body.name or "").strip() or f"{body.exam_year}年{body.exam_month}月{TYPE_LABEL[body.exam_type]}"
    dup = db.query_one("SELECT id FROM exams WHERE name=?", (name,))
    if dup:
        raise ApiError("同名考试批次已存在")
    ts = db.now_str()
    psize = _norm_photo_opt(body.photo_size, "size")
    pcolor = _norm_photo_opt(body.photo_color, "color")
    eid = db.execute(
        "INSERT INTO exams(name,exam_type,exam_year,exam_month,signup_start_at,signup_end_at,"
        "status,description,photo_size,photo_color,created_by,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (name, body.exam_type, int(body.exam_year), int(body.exam_month),
         start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"),
         body.status if body.status in STATUS_LABEL else "draft",
         body.description or "", psize, pcolor, user["id"], ts, ts))
    return ok(decorate(db.query_one("SELECT * FROM exams WHERE id=?", (eid,))), "考试批次已创建")


@router.put("/{exam_id}")
def update_exam(exam_id: int, body: ExamUpdateIn, user=Depends(require_perms("exam_manage"))):
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    fields, params = [], []
    if body.name is not None and body.name.strip():
        fields.append("name=?")
        params.append(body.name.strip())
    if body.signup_start_at is not None:
        fields.append("signup_start_at=?")
        params.append(_parse_dt(body.signup_start_at).strftime("%Y-%m-%d %H:%M:%S"))
    if body.signup_end_at is not None:
        fields.append("signup_end_at=?")
        params.append(_parse_dt(body.signup_end_at).strftime("%Y-%m-%d %H:%M:%S"))
    if body.description is not None:
        fields.append("description=?")
        params.append(body.description)
    if body.exam_year is not None:
        fields.append("exam_year=?")
        params.append(int(body.exam_year))
    if body.exam_month is not None:
        fields.append("exam_month=?")
        params.append(int(body.exam_month))
    if body.status is not None:
        if body.status not in STATUS_LABEL:
            raise ApiError("状态不合法")
        if body.status != exam["status"] and body.status not in STATUS_FLOW[exam["status"]]:
            raise ApiError(f"不允许从「{STATUS_LABEL[exam['status']]}」直接变更为"
                           f"「{STATUS_LABEL[body.status]}」")
        if body.status == "open":
            start = body.signup_start_at or exam["signup_start_at"]
            end = body.signup_end_at or exam["signup_end_at"]
            if end <= start:
                raise ApiError("报名截止时间必须晚于开始时间")
        fields.append("status=?")
        params.append(body.status)
    if body.photo_size is not None:
        fields.append("photo_size=?")
        params.append(_norm_photo_opt(body.photo_size, "size"))
    if body.photo_color is not None:
        fields.append("photo_color=?")
        params.append(_norm_photo_opt(body.photo_color, "color"))
    if not fields:
        raise ApiError("没有需要更新的内容")
    fields.append("updated_at=?")
    params.append(db.now_str())
    params.append(exam_id)
    db.execute(f"UPDATE exams SET {','.join(fields)} WHERE id=?", tuple(params))
    return ok(decorate(db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))), "已保存")


@router.post("/{exam_id}/status")
def change_status(exam_id: int, status: str = Query(...), user=Depends(require_perms("exam_manage"))):
    return update_exam(exam_id, ExamUpdateIn(status=status), user)


@router.delete("/{exam_id}")
def delete_exam(exam_id: int, user=Depends(require_perms("exam_manage"))):
    exam = db.query_one("SELECT * FROM exams WHERE id=?", (exam_id,))
    if not exam:
        raise ApiError("考试批次不存在", code=404, status=404)
    if exam["status"] != "draft":
        raise ApiError("仅草稿状态的批次允许删除")
    db.execute("DELETE FROM exams WHERE id=?", (exam_id,))
    return ok(None, "已删除")
