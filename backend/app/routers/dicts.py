# -*- coding: utf-8 -*-
"""字典接口：机构、科目、职业、省市区三级、证件类型、民族、学历、模板元信息。

机构 / 科目 / 职业三张表是**实时数据源**（报名校验、批量导入、表单下拉都读它），
因此提供管理员自助维护（增删改）。省市区与证件类型来自官方模板，保持只读。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db
from .. import exam_types as ET
from ..config import load_dicts
from ..deps import ApiError, ok
from ..permissions import require_perms

router = APIRouter(prefix="/api/dicts", tags=["dicts"])

# 可维护的字典：table / 主键 / 字段 / 报名表里引用它的列（用于删除前的影响检查）
EDITABLE = {
    "org": {"table": "dict_exam_org", "pk": "code", "text_pk": True,
            "cols": ("code", "name"), "label": "考试机构", "ref": ("org_code", "org_name")},
    "subject": {"table": "dict_subject", "pk": "id", "text_pk": False,
                "cols": ("name",), "label": "报考科目", "ref": ("subject",)},
    "occupation": {"table": "dict_occupation", "pk": "id", "text_pk": False,
                   "cols": ("name",), "label": "从事职业", "ref": ("occupation",)},
    # 二级学院：三套模板的院系列名不同（computer=school / mandarin=department /
    # generic=college），全部纳入影响统计，避免删掉一个仍在用的学院
    "college": {"table": "dict_college", "pk": "id", "text_pk": False,
                "cols": ("name",), "label": "二级学院",
                "ref": ("college", "department", "school"), "user_ref": ("college",)},
    # 部门：账号所属部门（users.department），报名表没有对应列。
    # extra 是「可编辑但不参与引用统计」的附加列：college 只表达这个部门归属哪个学院，
    # 不能混进 cols —— 否则 _used_count() 会拿部门名去报名表的院系列里比对，凭空造出引用。
    "department": {"table": "dict_department", "pk": "id", "text_pk": False,
                   "cols": ("name",), "extra": ("college",), "label": "部门",
                   "ref": (), "user_ref": ("department",)},
}


# 中文名 → EDITABLE 键：前端 / 脚本直接传「二级学院」「部门」也能命中，
# 不会因为中英文不一致而落到「字典类型不合法」
KIND_ALIAS = {
    "二级学院": "college", "学院": "college", "院系": "college",
    "部门": "department", "科室": "department",
}


def _norm_kind(kind: str) -> str:
    """把请求里的类型标识归一成 EDITABLE 的键（兼容中文名与多余空白）。"""
    k = (kind or "").strip()
    return KIND_ALIAS.get(k, k)


def _kind_or_400(kind: str) -> dict:
    k = EDITABLE.get(_norm_kind(kind))
    if not k:
        raise ApiError(f"字典类型不合法：{kind or '(空)'}（可选："
                       f"{'、'.join(EDITABLE)} 或中文名「二级学院」「部门」）")
    return k


def _serialize(kind: str, row: dict) -> dict:
    """统一输出：org 带 code/name，subject/occupation 只有 name。"""
    k = EDITABLE[kind]
    out = {c: row[c] for c in k["cols"]}
    for c in k.get("extra", ()):
        out[c] = row.get(c) or ""
    out["id"] = row[k["pk"]]
    return out


def _extra_cols(kind: str) -> tuple:
    return tuple(EDITABLE[kind].get("extra", ()))


def _college_exists(name: str) -> bool:
    return bool(db.query_one("SELECT id FROM dict_college WHERE name=?", (name,)))


def _norm_college(v) -> str:
    """部门归属学院：留空表示「不归属任何学院」（如校级处室）。

    非空则必须是字典里已有的学院 —— 否则管理员手打一个错字，
    这个部门就永远挂不到任何学院下面，还查不出来。
    """
    c = (v or "").strip()
    if not c:
        return ""
    if len(c) > 60:
        raise ApiError("所属学院不能超过 60 个字符")
    if not _college_exists(c):
        raise ApiError(f"所属学院「{c}」不在二级学院字典中，请先在「二级学院」里新增，或留空")
    return c


_TBL_COLS = {}


def _cols_of(tbl: str) -> set:
    """报名表的列集合（缓存，避免逐行 PRAGMA）。"""
    if tbl not in _TBL_COLS:
        _TBL_COLS[tbl] = {r["name"] for r in db.query(f"PRAGMA table_info({tbl})")}
    return _TBL_COLS[tbl]


def _used_count(kind: str, row: dict) -> int:
    """该字典项被多少条报名记录引用（跨全部报名表）。

    机构既可能按 code 存（org_code）也可能按名称存（org_name），所以两个值都比对。
    """
    k = EDITABLE[kind]
    values = [str(row.get(c) or "") for c in k["cols"] if str(row.get(c) or "")]
    if not values:
        return 0
    total = 0
    for tbl in ET.ALL_APP_TABLES:
        # 只统计表内真实存在的列：通用表没有 org_code/subject，跳过即可
        hit = [c for c in k["ref"] if c in _cols_of(tbl)]
        if not hit:
            continue
        # 列 × 候选值 的笛卡尔条件：任一列命中任一候选值都算引用
        conds, params = [], []
        for c in hit:
            for v in values:
                conds.append(f"{c}=?")
                params.append(v)
        r = db.query_one(f"SELECT COUNT(*) c FROM {tbl} WHERE {' OR '.join(conds)}",
                         tuple(params))
        total += r["c"] if r else 0
    # 学院 / 部门还会挂在账号上，改名或删除前同样要知道影响面
    for c in k.get("user_ref", ()):
        for v in values:
            r = db.query_one(f"SELECT COUNT(*) c FROM users WHERE {c}=?", (v,))
            total += r["c"] if r else 0
    return total


class DictIn(BaseModel):
    code: str = ""
    name: str = ""
    # 部门归属哪个二级学院（仅 department 用，留空 = 校级/不归属）
    college: str = ""


@router.get("/org")
def orgs():
    return ok(db.query("SELECT code,name FROM dict_exam_org ORDER BY code"))


@router.get("/subject")
def subjects():
    return ok([r["name"] for r in db.query("SELECT name FROM dict_subject ORDER BY id")])


@router.get("/occupation")
def occupations():
    return ok([r["name"] for r in db.query("SELECT name FROM dict_occupation ORDER BY id")])


@router.get("/college")
def colleges():
    """二级学院：报名院系字段与账号院系的下拉候选。"""
    return ok([r["name"] for r in db.query("SELECT name FROM dict_college ORDER BY id")])


@router.get("/department")
def departments():
    """部门：账号所属部门的下拉候选（只要名称，供老调用方使用）。"""
    return ok([r["name"] for r in db.query("SELECT name FROM dict_department ORDER BY id")])


@router.get("/department/full")
def departments_full():
    """部门 + 所属学院。

    层级是平铺单层的：部门只是「可归属到某个学院」（`college` 为空 = 校级处室），
    不做上下级嵌套。账号编辑页拿到这个就能按所选学院过滤部门候选。
    """
    rows = db.query("SELECT id, name, college FROM dict_department ORDER BY id")
    return ok([{"id": r["id"], "name": r["name"], "college": r["college"] or ""}
               for r in rows])


# ------------------------------------------------------------------ 字典维护
@router.get("/manage/{kind}")
def list_manage(kind: str, keyword: str = "", page: int = 1, page_size: int = 20,
                user=Depends(require_perms("dict_manage"))):
    """字典维护列表：带关键词过滤与引用计数（删除前的影响评估）。"""
    kind = _norm_kind(kind)
    k = _kind_or_400(kind)
    page, page_size = max(1, page), min(200, max(1, page_size))
    extra = _extra_cols(kind)
    cols = ", ".join(k["cols"]) if k["text_pk"] else "id, " + ", ".join(k["cols"])
    if extra:
        cols += ", " + ", ".join(extra)
    where, params = "", []
    if keyword.strip():
        kw = f"%{keyword.strip()}%"
        search_cols = list(k["cols"]) + list(extra)
        where = " WHERE " + " OR ".join(f"{c} LIKE ?" for c in search_cols)
        params = [kw] * len(search_cols)
    total = db.query_one(f"SELECT COUNT(*) c FROM {k['table']}{where}", params)["c"]
    rows = db.query(f"SELECT {cols} FROM {k['table']}{where} "
                    f"ORDER BY {k['pk']} LIMIT ? OFFSET ?",
                    params + [page_size, (page - 1) * page_size])
    out = []
    for r in rows:
        item = _serialize(kind, r)
        item["used"] = _used_count(kind, r)
        out.append(item)
    return ok({"list": out, "total": total, "page": page, "page_size": page_size,
               "kind": kind, "label": k["label"], "text_pk": k["text_pk"]})


@router.post("/manage/{kind}")
def create_dict(kind: str, body: DictIn, user=Depends(require_perms("dict_manage"))):
    kind = _norm_kind(kind)
    k = _kind_or_400(kind)
    name = (body.name or "").strip()
    if not name:
        raise ApiError("名称不能为空")
    if len(name) > 60:
        raise ApiError("名称不能超过 60 个字符")
    if k["text_pk"]:
        code = (body.code or "").strip()
        if not code:
            raise ApiError("编码不能为空")
        if len(code) > 32:
            raise ApiError("编码不能超过 32 个字符")
        if db.query_one(f"SELECT code FROM {k['table']} WHERE code=?", (code,)):
            raise ApiError(f"编码「{code}」已存在")
        db.execute(f"INSERT INTO {k['table']}(code,name) VALUES(?,?)", (code, name))
        return ok({"code": code, "name": name, "id": code}, f"已新增{k['label']}「{name}」")
    if db.query_one(f"SELECT id FROM {k['table']} WHERE name=?", (name,)):
        raise ApiError(f"「{name}」已存在")
    extra = _extra_cols(kind)
    if extra:
        vals = {"college": _norm_college(body.college)} if "college" in extra else {}
        cols = ", ".join(["name"] + list(vals.keys()))
        ph = ", ".join(["?"] * (1 + len(vals)))
        db.execute(f"INSERT INTO {k['table']}({cols}) VALUES({ph})",
                   (name,) + tuple(vals.values()))
        return ok({"name": name, **vals}, f"已新增{k['label']}「{name}」")
    db.execute(f"INSERT INTO {k['table']}(name) VALUES(?)", (name,))
    return ok({"name": name}, f"已新增{k['label']}「{name}」")


@router.put("/manage/{kind}/{pk}")
def update_dict(kind: str, pk: str, body: DictIn,
                user=Depends(require_perms("dict_manage"))):
    kind = _norm_kind(kind)
    k = _kind_or_400(kind)
    name = (body.name or "").strip()
    if not name:
        raise ApiError("名称不能为空")
    if len(name) > 60:
        raise ApiError("名称不能超过 60 个字符")
    row = db.query_one(f"SELECT * FROM {k['table']} WHERE {k['pk']}=?", (pk,))
    if not row:
        raise ApiError("该字典项不存在", code=404, status=404)
    old = row.get("name") or ""
    if name != old and db.query_one(
            f"SELECT 1 FROM {k['table']} WHERE name=? AND {k['pk']}<>?", (name, pk)):
        raise ApiError(f"「{name}」已存在")
    sets, args = ["name=?"], [name]
    extra = _extra_cols(kind)
    new_college = ""
    if "college" in extra:
        new_college = _norm_college(body.college)
        sets.append("college=?")
        args.append(new_college)
    db.execute(f"UPDATE {k['table']} SET {', '.join(sets)} WHERE {k['pk']}=?",
               tuple(args) + (pk,))
    # 改名不会同步历史报名记录，明确告知影响面，避免管理员以为「全改了」
    used = _used_count(kind, row) if name != old else 0
    hint = f"已保存为「{name}」"
    if used:
        hint += f"；注意：已有 {used} 条报名记录仍保留旧值「{old}」，导出时以原值为准"
    out = {"id": pk, "name": name, "old_name": old, "used": used}
    if "college" in extra:
        out["college"] = new_college
    return ok(out, hint)


@router.delete("/manage/{kind}/{pk}")
def delete_dict(kind: str, pk: str, user=Depends(require_perms("dict_manage"))):
    kind = _norm_kind(kind)
    k = _kind_or_400(kind)
    row = db.query_one(f"SELECT * FROM {k['table']} WHERE {k['pk']}=?", (pk,))
    if not row:
        raise ApiError("该字典项不存在", code=404, status=404)
    label = row.get("name") or ""
    used = _used_count(kind, row)
    if used:
        raise ApiError(f"「{label}」已被 {used} 条报名记录使用，不能删除；"
                       f"如不再启用可改名为「{label}（停用）」")
    db.execute(f"DELETE FROM {k['table']} WHERE {k['pk']}=?", (pk,))
    return ok({"deleted": pk}, f"已删除{k['label']}「{label}」")


@router.get("/id-type")
def id_types():
    return ok(db.query("SELECT code,name FROM dict_id_type ORDER BY code"))


@router.get("/ethnicity")
def ethnicities():
    return ok(load_dicts()["ethnicities"])


@router.get("/education")
def educations():
    return ok(load_dicts()["educations"])


@router.get("/region/provinces")
def region_provinces():
    rows = db.query("SELECT DISTINCT province FROM dict_region ORDER BY id")
    return ok([r["province"] for r in rows])


@router.get("/region/cities")
def region_cities(province: str = Query(...)):
    rows = db.query("SELECT DISTINCT city FROM dict_region WHERE province=? AND city<>'' ORDER BY id",
                    (province,))
    return ok([r["city"] for r in rows])


@router.get("/region/counties")
def region_counties(province: str = Query(...), city: str = Query(...)):
    rows = db.query(
        "SELECT county FROM dict_region WHERE province=? AND city=? AND county<>'' ORDER BY id",
        (province, city))
    return ok([r["county"] for r in rows])


@router.get("/region/tree")
def region_tree():
    """一次性返回省-市-县区三级树，供前端本地联动（减少请求次数）。"""
    d = load_dicts()
    return ok([{"province": r["province"],
                "cities": [{"name": c["name"], "counties": c["counties"]} for c in r["cities"]]}
               for r in d["mandarin"]["regions"]])


@router.get("/meta")
def meta():
    """官方模板元信息：表头、填表说明、模板版本（导出与表单渲染共用）。"""
    d = load_dicts()
    return ok({
        "computer": {
            "headers": d["computer"]["headers"],
            "instructions": d["computer"]["instructions"],
            "org_count": len(d["computer"]["orgs"]),
            "subject_count": len(d["computer"]["subjects"]),
        },
        "mandarin": {
            "headers": d["mandarin"]["headers"],
            "occupation_count": len(d["mandarin"]["occupations"]),
            "template_version": d["mandarin"]["version"],
        },
        # 通用模板没有官方表头，字段由管理员在「考试类型管理」里自定义
        "generic": {
            "headers": [],
            "instructions": "通用模板无固定官方表头，字段由管理员自定义。",
            "template_version": "-",
        },
    })
