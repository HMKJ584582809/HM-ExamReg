# -*- coding: utf-8 -*-
"""考试类型管理接口：管理员基于两套基础模板自定义考试类型。

新增类型只需选底座（computer / mandarin）+ 起名称与编码，
即可复用报名、导入、审核、导出、分析整条链路。
"""
import json

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from .. import exam_types as ET
from ..deps import ApiError, get_current_user, ok
from ..permissions import require_perms

router = APIRouter(prefix="/api/exam-types", tags=["exam_types"])


class TypeIn(BaseModel):
    code: str = ""
    name: str = ""
    base_type: str = "computer"
    description: str = ""
    fields_config: dict = None
    enabled: bool = True
    sort: int = 0


class TypeUpdateIn(BaseModel):
    name: str = None
    description: str = None
    fields_config: dict = None
    enabled: bool = None
    sort: int = None


class FieldIn(BaseModel):
    """通用模板的自定义字段定义。"""
    field_key: str = ""
    label: str = ""
    field_type: str = "text"
    required: bool = False
    options: list = None
    placeholder: str = ""
    sort: int = 0


# 报名表已有列 / 内部字段，自定义字段不能占用
_RESERVED_KEYS = {"extra", "id", "exam_id", "user_id", "audit_status", "audit_comment",
                  "reviewed_by", "reviewed_at", "created_at", "updated_at", "app_type"}
_MAX_CUSTOM_FIELDS = 30


def _clean_field(body: FieldIn, type_code: str, exclude_id: int = 0) -> dict:
    """校验并规范化一个自定义字段定义，返回待落库的参数元组。"""
    key = (body.field_key or "").strip()
    label = (body.label or "").strip()
    ftype = (body.field_type or "text").strip()
    if not ET.FIELD_KEY_RE.match(key):
        raise ApiError("字段标识需用小写字母开头，仅含小写字母/数字/下划线，长度 2-30")
    if key in _RESERVED_KEYS:
        raise ApiError(f"字段标识「{key}」为系统保留字，请换一个")
    if key in ET.BASE_FIELDS["generic"]:
        raise ApiError(f"字段标识「{key}」与通用模板的固定字段重复")
    if not label:
        raise ApiError("请填写字段名称")
    if len(label) > 30:
        raise ApiError("字段名称不能超过 30 个字符")
    if ftype not in ET.CUSTOM_FIELD_TYPES:
        raise ApiError("字段类型不合法")
    opts = [str(o).strip() for o in (body.options or []) if str(o).strip()]
    if ftype in ("select", "multiselect") and not opts:
        raise ApiError("下拉类型的字段至少需要一个选项")
    if len(opts) > 50:
        raise ApiError("选项数量不能超过 50 个")
    dup = db.query_one("SELECT id FROM exam_type_fields WHERE type_code=? AND field_key=?",
                       (type_code, key))
    if dup and dup["id"] != exclude_id:
        raise ApiError(f"字段标识「{key}」已存在")
    return {
        "field_key": key, "label": label, "field_type": ftype,
        "required": 1 if body.required else 0,
        "options": json.dumps(opts, ensure_ascii=False) if opts else "",
        "placeholder": (body.placeholder or "").strip()[:50],
        "sort": int(body.sort or 0),
    }


def _clean_config(base: str, cfg: dict) -> str:
    """只保留该底座真实存在的字段；系统必填项不允许被关闭。"""
    if not cfg:
        return ""
    allowed = set(ET.BASE_FIELDS[base])
    sysreq = set(ET.BASE_REQUIRED[base])
    out = {}
    for f, c in cfg.items():
        if f not in allowed or not isinstance(c, dict):
            continue
        item = {}
        if "enabled" in c:
            item["enabled"] = bool(c["enabled"])
        if "required" in c:
            item["required"] = bool(c["required"])
        lb = str(c.get("label") or "").strip()[:30]
        if lb:
            item["label"] = lb
        if f in sysreq:               # 系统必填：强制开启且必填
            item["enabled"] = True
            item["required"] = True
        out[f] = item
    return json.dumps(out, ensure_ascii=False)


def _row_or_404(tid: int) -> dict:
    row = db.query_one("SELECT * FROM exam_types WHERE id=?", (tid,))
    if not row:
        raise ApiError("考试类型不存在", code=404, status=404)
    return row


@router.get("")
def list_types(user=Depends(get_current_user)):
    return ok({"list": [ET.public(r) for r in ET.all_types()]})


@router.get("/options")
def options(user=Depends(get_current_user)):
    """下拉数据：只返回启用中的类型。"""
    return ok({"list": [{"value": r["code"], "label": r["name"],
                         "base_type": r["base_type"]}
                        for r in ET.all_types(enabled_only=True)]})


@router.post("")
def create_type(body: TypeIn, user=Depends(require_perms("dict_manage"))):
    code = (body.code or "").strip()
    name = (body.name or "").strip()
    if not ET.CODE_RE.match(code):
        raise ApiError("类型编码需用小写字母开头，仅含小写字母/数字/下划线，长度 2-30")
    if not name:
        raise ApiError("请填写类型名称")
    if body.base_type not in ET.BASE_TYPES:
        raise ApiError("基础模板不合法")
    if db.query_one("SELECT id FROM exam_types WHERE code=?", (code,)):
        raise ApiError("该类型编码已存在")
    ts = db.now_str()
    tid = db.execute(
        "INSERT INTO exam_types(code,name,base_type,description,fields_config,"
        "enabled,is_builtin,sort,created_at,updated_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
        (code, name, body.base_type, (body.description or "").strip(),
         _clean_config(body.base_type, body.fields_config),
         1 if body.enabled else 0, int(body.sort or 0), ts, ts))
    ET.refresh()
    return ok(ET.public(_row_or_404(tid)), "考试类型已创建")


@router.put("/{tid}")
def update_type(tid: int, body: TypeUpdateIn, user=Depends(require_perms("dict_manage"))):
    row = _row_or_404(tid)
    fields, params = [], []
    if body.name is not None and body.name.strip():
        fields.append("name=?")
        params.append(body.name.strip())
    if body.description is not None:
        fields.append("description=?")
        params.append(body.description.strip())
    if body.fields_config is not None:
        fields.append("fields_config=?")
        params.append(_clean_config(row["base_type"], body.fields_config))
    if body.enabled is not None:
        fields.append("enabled=?")
        params.append(1 if body.enabled else 0)
    if body.sort is not None:
        fields.append("sort=?")
        params.append(int(body.sort))
    if not fields:
        raise ApiError("没有需要更新的内容")
    fields.append("updated_at=?")
    params.append(db.now_str())
    params.append(tid)
    db.execute(f"UPDATE exam_types SET {','.join(fields)} WHERE id=?", tuple(params))
    ET.refresh()
    return ok(ET.public(_row_or_404(tid)), "已保存")


# ------------------------------------------------------------------ 自定义字段（通用模板）

def _generic_type_or_400(tid: int) -> dict:
    row = _row_or_404(tid)
    if row["base_type"] != "generic":
        raise ApiError("只有「通用考试报名」模板支持自定义字段", code=400, status=400)
    return row


@router.get("/{tid}/fields")
def list_fields(tid: int, user=Depends(get_current_user)):
    row = _generic_type_or_400(tid)
    return ok({"list": ET.custom_fields_of(row["code"]), "type_code": row["code"],
               "types": ET.CUSTOM_FIELD_TYPES, "max": _MAX_CUSTOM_FIELDS})


@router.post("/{tid}/fields")
def create_field(tid: int, body: FieldIn, user=Depends(require_perms("dict_manage"))):
    row = _generic_type_or_400(tid)
    cnt = db.query_one("SELECT COUNT(*) c FROM exam_type_fields WHERE type_code=?",
                       (row["code"],))["c"]
    if cnt >= _MAX_CUSTOM_FIELDS:
        raise ApiError(f"单个类型最多 {_MAX_CUSTOM_FIELDS} 个自定义字段")
    f = _clean_field(body, row["code"])
    ts = db.now_str()
    fid = db.execute(
        "INSERT INTO exam_type_fields(type_code,field_key,label,field_type,required,"
        "options,placeholder,sort,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (row["code"], f["field_key"], f["label"], f["field_type"], f["required"],
         f["options"], f["placeholder"], f["sort"], ts, ts))
    ET.refresh()
    return ok(ET.public(_row_or_404(tid)), f"已添加字段「{f['label']}」")


@router.put("/{tid}/fields/{fid}")
def update_field(tid: int, fid: int, body: FieldIn,
                 user=Depends(require_perms("dict_manage"))):
    row = _generic_type_or_400(tid)
    cur = db.query_one("SELECT * FROM exam_type_fields WHERE id=? AND type_code=?",
                       (fid, row["code"]))
    if not cur:
        raise ApiError("字段不存在", code=404, status=404)
    f = _clean_field(body, row["code"], exclude_id=fid)
    db.execute("UPDATE exam_type_fields SET field_key=?,label=?,field_type=?,required=?,"
               "options=?,placeholder=?,sort=?,updated_at=? WHERE id=?",
               (f["field_key"], f["label"], f["field_type"], f["required"], f["options"],
                f["placeholder"], f["sort"], db.now_str(), fid))
    ET.refresh()
    return ok(ET.public(_row_or_404(tid)), f"已保存字段「{f['label']}」")


@router.delete("/{tid}/fields/{fid}")
def delete_field(tid: int, fid: int, user=Depends(require_perms("dict_manage"))):
    row = _generic_type_or_400(tid)
    cur = db.query_one("SELECT * FROM exam_type_fields WHERE id=? AND type_code=?",
                       (fid, row["code"]))
    if not cur:
        raise ApiError("字段不存在", code=404, status=404)
    # 统计已有多少条报名填了这个字段（值存在 extra 里），返回给前端提示
    used = 0
    for r in db.query("SELECT extra FROM applications_generic WHERE exam_id IN "
                      "(SELECT id FROM exams WHERE exam_type=?)", (row["code"],)):
        try:
            if (json.loads(r["extra"] or "{}") or {}).get(cur["field_key"]):
                used += 1
        except (ValueError, TypeError):
            continue
    db.execute("DELETE FROM exam_type_fields WHERE id=?", (fid,))
    ET.refresh()
    msg = f"已删除字段「{cur['label']}」"
    if used:
        msg += f"（{used} 条报名记录中该字段的值将不再显示，导出时也不再包含）"
    return ok(ET.public(_row_or_404(tid)), msg)


@router.delete("/{tid}")
def delete_type(tid: int, user=Depends(require_perms("dict_manage"))):
    row = _row_or_404(tid)
    if row["is_builtin"]:
        raise ApiError("内置类型不允许删除，可将其停用")
    used = db.query_one("SELECT COUNT(*) c FROM exams WHERE exam_type=?", (row["code"],))["c"]
    if used:
        raise ApiError(f"已有 {used} 个考试批次在使用该类型，不能删除；可先停用")
    db.execute("DELETE FROM exam_type_fields WHERE type_code=?", (row["code"],))
    db.execute("DELETE FROM exam_types WHERE id=?", (tid,))
    ET.refresh()
    return ok(None, "已删除")
