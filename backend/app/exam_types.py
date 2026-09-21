# -*- coding: utf-8 -*-
"""考试类型：内置两套基础模板，管理员可基于模板自定义新类型。

关键约定
--------
* ``exams.exam_type`` 保存**类型编码**（面向用户，可自定义，如 ``english``）
* 各处 ``app_type`` 保存**基础类型**（``computer`` / ``mandarin``），它决定：
  存储表、字段集、校验器、官方导出模板、院系列名（school / department）

因此新增一个类型 = 选一个基础模板 + 起名字/编码，即可复用整条
报名 / 导入 / 审核 / 导出 / 分析链路，不需要改代码。

字段配置 ``fields_config`` 为 JSON，形如：
    {"subject": {"enabled": true, "required": true, "label": "报考科目"}}
缺省即沿用基础模板默认值；只允许「加必填」，系统必填项不可取消。
"""
import json
import re
from datetime import datetime

from . import db

# 基础模板：决定存储表与字段集，管理员不能新增，只能选一个作为底座
# generic 为「通用考试报名」：核心字段固定，其余字段由管理员在后台自由增删
BASE_TYPES = {"computer": "计算机类考试", "mandarin": "普通话水平测试",
              "generic": "通用考试报名"}

# 自定义字段支持的类型（通用模板专用）
CUSTOM_FIELD_TYPES = {
    "text": "单行文本", "textarea": "多行文本", "number": "数字",
    "date": "日期", "select": "下拉单选", "multiselect": "下拉多选",
}

# 落库字段（与两张报名表的列一一对应；grade 是导入用虚拟字段，不在此列）
BASE_FIELDS = {
    "computer": ["org_code", "exam_site_code", "name", "gender", "id_type", "id_number",
                 "subject", "school", "class_name", "education", "phone", "email", "address"],
    "mandarin": ["name", "gender", "ethnicity", "id_type", "id_number", "occupation",
                 "employer", "phone", "student_no", "class_name", "department",
                 "contact_address", "mail_address", "postcode", "birth_province",
                 "birth_city", "birth_county", "live_province", "live_city", "live_county"],
    # 通用模板：只保留各类考试通用的核心字段，其余交给自定义字段
    "generic": ["name", "gender", "id_type", "id_number", "phone", "email",
                "class_name", "college"],
}

# 各模板的系统必填项：管理员可追加必填，但不能去掉这些
# ⚠ 这是**唯一事实源**：后端校验（applications.FIXED_REQUIRED）与前端字段元信息
#   （required / system_required）都从它派生。曾经两处各写一份且不一致，导致
#   后端强制必填、前端却既无星号也不校验（考生提交才报「所在单位不能为空」）。
BASE_REQUIRED = {
    "computer": ["name", "gender", "id_number", "subject", "phone",
                 "org_code", "exam_site_code", "school", "class_name", "education"],
    "mandarin": ["name", "gender", "id_number", "occupation", "phone",
                 "ethnicity", "employer"],
    "generic": ["name", "gender", "id_number", "phone"],
}

# 院系列名差异：计算机用 school，普通话用 department，通用模板用 college
COLLEGE_COLUMN = {"computer": "school", "mandarin": "department", "generic": "college"}

# 报名存储表名：新增基础模板时只改这一处，避免各处三目判断再漏掉新模板
APP_TABLE = {"computer": "applications_computer", "mandarin": "applications_mandarin",
             "generic": "applications_generic"}
ALL_APP_TABLES = [APP_TABLE[k] for k in ("computer", "mandarin", "generic")]

# 固定字段在前端该用什么控件渲染（通用模板的自定义字段自带 type，不在此表）
FIELD_INPUT = {"gender": "select", "id_type": "select"}

FIELD_LABEL = {
    "org_code": "考试机构编码", "exam_site_code": "考点编码", "name": "姓名",
    "gender": "性别", "id_type": "证件类型", "id_number": "证件号码",
    "subject": "报考科目", "school": "就读或毕业院校", "class_name": "班级",
    "grade": "年级", "education": "学历", "phone": "手机号码", "email": "Email",
    "address": "通讯地址", "ethnicity": "考生民族", "occupation": "从事职业",
    "employer": "所在单位", "student_no": "学号", "department": "院系",
    "contact_address": "联系地址", "mail_address": "邮寄地址", "postcode": "邮政编码",
    "birth_province": "出生地省", "birth_city": "出生地市", "birth_county": "出生地县区",
    "live_province": "现居住地省", "live_city": "现居住地市", "live_county": "现居住地县区",
    "college": "院系",
}

# 同一字段在两套模板里的叫法不同（如 name：计算机叫「姓名」、普通话叫「考生姓名」），
# 必须按模板取，否则报名表单会被通用标签覆盖掉原有的模板化名称。
BASE_FIELD_LABEL = {
    "computer": {
        "org_code": "考试机构编码", "exam_site_code": "考点编码", "name": "姓名",
        "gender": "性别", "id_type": "证件类型", "id_number": "证件号码",
        "subject": "报考科目", "school": "就读或者毕业院校", "class_name": "班级",
        "education": "学历", "phone": "手机号码", "email": "Email", "address": "通讯地址",
    },
    "mandarin": {
        "name": "考生姓名", "gender": "考生性别", "ethnicity": "考生民族",
        "id_type": "证件类型", "id_number": "证件编号", "occupation": "从事职业",
        "employer": "所在单位", "phone": "联系电话", "student_no": "考生学号",
        "class_name": "考生班级", "department": "考生院系", "contact_address": "联系地址",
        "mail_address": "邮寄地址", "postcode": "邮政编码",
        "birth_province": "出生所在省", "birth_city": "出生所在城市",
        "birth_county": "出生所在县(区)", "live_province": "现居住省",
        "live_city": "现居住城市", "live_county": "现居住县(区)",
    },
    "generic": {
        "name": "姓名", "gender": "性别", "id_type": "证件类型",
        "id_number": "证件号码", "phone": "手机号码", "email": "电子邮箱",
        "class_name": "班级", "college": "院系",
    },
}


def default_label(base: str, field: str) -> str:
    """字段的基础显示名（未配置自定义名时）。"""
    return (BASE_FIELD_LABEL.get(base, {}).get(field)
            or FIELD_LABEL.get(field) or field)

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
# 自定义字段 key：小写字母开头，仅字母数字下划线，避开固定字段与内部字段
FIELD_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,29}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_ROWS = {}          # code -> row 缓存；CRUD 后调 refresh() 失效
_FIELDS = {}        # code -> 自定义字段定义缓存


def refresh():
    """类型被增删改后清缓存。"""
    _ROWS.clear()
    _FIELDS.clear()


def _load(code: str):
    code = (code or "").strip()
    if not code:
        return None
    if code not in _ROWS:
        try:
            _ROWS[code] = db.query_one(
                "SELECT * FROM exam_types WHERE code=?", (code,))
        except Exception:
            _ROWS[code] = None
    return _ROWS[code]


def base_type_of(code: str) -> str:
    """类型编码 -> 基础模板（computer / mandarin）。

    未知编码兜底 computer：删除类型时已阻止删除正在使用的类型，
    正常不会出现；兜底仅为防御，避免整条链路崩在取值上。
    """
    code = (code or "").strip()
    if code in BASE_TYPES:
        return code
    row = _load(code)
    if row and row["base_type"] in BASE_TYPES:
        return row["base_type"]
    return "computer"


def label_of(code: str) -> str:
    """类型显示名，未登记时退回编码本身。"""
    code = (code or "").strip()
    row = _load(code)
    if row:
        return row["name"]
    return BASE_TYPES.get(code, code)


def field_label(code: str, field: str) -> str:
    """字段显示名：类型级自定义名 > 模板默认名。"""
    cfg = config_of(code)
    lb = (cfg.get(field) or {}).get("label")
    return lb or default_label(base_type_of(code), field)


def config_of(code: str) -> dict:
    """解析字段配置 JSON，结构异常时返回空配置（不因脏数据崩掉）。"""
    row = _load(code)
    raw = (row or {}).get("fields_config") or ""
    if not raw:
        return {}
    try:
        cfg = json.loads(raw)
        return cfg if isinstance(cfg, dict) else {}
    except (ValueError, TypeError):
        return {}


def fields_of(code: str) -> list:
    """该类型实际采集的字段（基础模板字段，去掉被管理员关闭的）。"""
    base = base_type_of(code)
    cfg = config_of(code)
    out = []
    for f in BASE_FIELDS[base]:
        c = cfg.get(f) or {}
        if c.get("enabled", True):
            out.append(f)
    return out


def required_of(code: str) -> list:
    """该类型的必填字段 = 系统必填 ∪ 管理员追加。"""
    base = base_type_of(code)
    req = set(BASE_REQUIRED[base])
    for f, c in config_of(code).items():
        if isinstance(c, dict) and c.get("required"):
            req.add(f)
    return sorted(req)


def college_column(code: str) -> str:
    """院系列名（计算机 school / 普通话 department）。"""
    return COLLEGE_COLUMN[base_type_of(code)]


def table_of(code_or_base: str) -> str:
    """报名存储表名：传类型编码或基础模板都可以（内部统一按基础模板解析）。"""
    key = code_or_base if code_or_base in APP_TABLE else base_type_of(code_or_base)
    if key not in APP_TABLE:
        raise KeyError(f"未知的报名类型：{code_or_base}")
    return APP_TABLE[key]


def exists(code: str) -> bool:
    code = (code or "").strip()
    return code in BASE_TYPES or _load(code) is not None


def all_types(enabled_only: bool = False) -> list:
    """全部类型（含内置），按 sort、id 排序。"""
    try:
        rows = db.query("SELECT * FROM exam_types ORDER BY sort, id")
    except Exception:
        rows = []
    out = []
    for r in rows:
        if enabled_only and not r["enabled"]:
            continue
        out.append(r)
    # 兜底：老库还没种子时至少保证两种内置类型可用
    if not out:
        return [{"code": c, "name": n, "base_type": c, "enabled": 1, "is_builtin": 1}
                for c, n in BASE_TYPES.items()]
    return out


def fields_meta(code: str) -> list:
    """该类型的采集字段元信息：key / label / enabled / required / type / options。

    报名、审核详情、我的报名等页面据此渲染，管理员在「考试类型管理」
    里关闭或改名的字段会同步生效，无需改前端。

    通用模板会额外附带自定义字段（custom=True，值存在 extra 里）。
    """
    base = base_type_of(code)
    cfg = config_of(code)
    req = set(BASE_REQUIRED[base])
    out = [
        {
            "key": f,
            "label": (cfg.get(f) or {}).get("label") or default_label(base, f),
            "enabled": bool((cfg.get(f) or {}).get("enabled", True)),
            "required": f in req or bool((cfg.get(f) or {}).get("required")),
            "system_required": f in req,
            "type": FIELD_INPUT.get(f, "text"),
            "options": [],
            "placeholder": "",
            "custom": False,
            "sort": 0,
        }
        for f in BASE_FIELDS[base]
    ]
    for cf in custom_fields_of(code):
        out.append({
            "key": cf["key"],
            "label": cf["label"],
            "enabled": True,
            "required": cf["required"],
            "system_required": False,
            "type": cf["type"],
            "options": cf["options"],
            "placeholder": cf["placeholder"],
            "custom": True,
            "sort": cf["sort"],
        })
    return out


def custom_fields_of(code: str) -> list:
    """该类型的自定义字段定义（仅通用模板有意义，其余返回空）。

    结构：key / label / type / required / options / placeholder / sort
    options 为 JSON 数组，解析失败按空列表处理（不因脏数据崩掉）。
    """
    code = (code or "").strip()
    if not code:
        return []
    if code not in _FIELDS:
        try:
            rows = db.query("SELECT * FROM exam_type_fields WHERE type_code=? "
                            "ORDER BY sort, id", (code,))
        except Exception:
            rows = []
        out = []
        for r in rows:
            try:
                opts = json.loads(r["options"] or "[]")
            except (ValueError, TypeError):
                opts = []
            if not isinstance(opts, list):
                opts = []
            out.append({
                "id": r["id"],
                "key": r["field_key"],
                "label": r["label"],
                "type": r["field_type"] if r["field_type"] in CUSTOM_FIELD_TYPES else "text",
                "required": bool(r["required"]),
                "options": [str(o) for o in opts],
                "placeholder": r["placeholder"] or "",
                "sort": r["sort"],
            })
        _FIELDS[code] = out
    return _FIELDS[code]


def custom_keys_of(code: str) -> list:
    return [f["key"] for f in custom_fields_of(code)]


def clean_custom_values(code: str, values) -> tuple:
    """校验并规范化自定义字段的值。

    返回 ``(cleaned, error)``；error 为 None 表示通过。
    故意不抛异常：报名提交与批量导入都要共用这一段，调用方决定怎么报错。

    * 未登记的 key 一律丢弃 —— 防止前端伪造字段 key 写脏数据
    * 必填为空直接报错；非空则按类型校验（数字 / 日期 / 选项合法性）
    * multiselect 归一化成 list，导出时用「、」连接
    """
    defs = {f["key"]: f for f in custom_fields_of(code)}
    if not defs:
        return {}, None
    if not isinstance(values, dict):
        values = {}
    out = {}
    for key, f in defs.items():
        raw = values.get(key)
        if isinstance(raw, list):
            raw = [str(x).strip() for x in raw if str(x).strip()]
            v = "、".join(raw)
        else:
            v = "" if raw is None else str(raw).strip()
        if not v:
            if f["required"]:
                return {}, f"{f['label']}为必填项"
            out[key] = [] if f["type"] == "multiselect" else ""
            continue
        t = f["type"]
        if t == "number":
            try:
                float(v)
            except ValueError:
                return {}, f"{f['label']}需要填写数字"
        elif t == "date":
            if not _DATE_RE.match(v):
                return {}, f"{f['label']}日期格式应为 YYYY-MM-DD"
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                return {}, f"{f['label']}不是一个有效日期"
        elif t == "select":
            if f["options"] and v not in f["options"]:
                return {}, f"{f['label']}只能选择给定选项"
        elif t == "multiselect":
            picked = [x.strip() for x in str(v).replace("，", "、").split("、") if x.strip()]
            if f["options"]:
                bad = [x for x in picked if x not in f["options"]]
                if bad:
                    return {}, f"{f['label']}存在不在选项范围内的值：{'、'.join(bad[:3])}"
            out[key] = picked
            continue
        if t == "multiselect":
            out[key] = [x.strip() for x in v.split("、") if x.strip()]
        else:
            out[key] = v
    return out, None


def public(row: dict) -> dict:
    """输出给前端的类型结构（含派生字段）。"""
    code = row["code"]
    base = row["base_type"]
    cfg = config_of(code)
    return {
        "id": row["id"],
        "code": code,
        "name": row["name"],
        "base_type": base,
        "base_type_label": BASE_TYPES.get(base, base),
        "description": row["description"] or "",
        "enabled": bool(row["enabled"]),
        "is_builtin": bool(row["is_builtin"]),
        "sort": row["sort"],
        "custom_fields": custom_fields_of(code),
        "fields": [
            {
                "key": f,
                "label": (cfg.get(f) or {}).get("label") or default_label(base, f),
                "enabled": bool((cfg.get(f) or {}).get("enabled", True)),
                "required": f in set(BASE_REQUIRED[base]) or bool((cfg.get(f) or {}).get("required")),
                "system_required": f in set(BASE_REQUIRED[base]),
            }
            for f in BASE_FIELDS[base]
        ],
    }
