# -*- coding: utf-8 -*-
"""模块1（补充）：用户与权限管理。

- 管理员可开通 / 维护各类账号：考生、班主任、二级学院审核、审核员、管理员
- 班主任 / 辅导员需绑定管理范围（班级 / 院系），后端强制核验
- 提供「权限组核验」接口，用于后台核对每个账号实际生效的权限组与数据范围
"""
import json
import random
import re

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import db
from .. import exam_types as ET
from .. import realname as RN
from ..deps import ApiError, ok, page_params
from ..permissions import (FUNC_GROUPS, GROUP_DESC, PERM_GROUPS, ROLE_LABEL, ROLE_ORDER,
                           ROLE_PERMS,
                           SCOPED_ROLES, SCOPE_LEVEL, SCOPE_SHORT, allowed_scopes, describe,
                           effective_perms, is_custom_perms, is_custom_scope, perms_of, stored_scope,
                           require_admin, role_func_perms, role_scope, scope_label, scope_of)
from ..security import hash_password, password_strength_error
from ..validators import validate_id_number

router = APIRouter(prefix="/api/users", tags=["users"])

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+(\.[\w\-]+)+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
ROLES = tuple(ROLE_ORDER)

# SQLite 无 FIELD()，用 CASE 实现角色排序
ROLE_ORDER_SQL = (" ORDER BY CASE role " +
                  " ".join(f"WHEN '{r}' THEN {i}" for i, r in enumerate(ROLE_ORDER)) +
                  " ELSE 99 END, id")


class UserCreateIn(BaseModel):
    username: str
    real_name: str = ""
    phone: str
    email: str = ""
    role: str = "candidate"
    password: str = ""
    status: int = 1
    grade: str = ""
    college: str = ""
    department: str = ""
    class_name: str = ""
    classes: str = ""
    id_number: str = ""


class UserUpdateIn(BaseModel):
    real_name: str = None
    phone: str = None
    email: str = None
    role: str = None
    status: int = None
    grade: str = None
    college: str = None
    department: str = None
    class_name: str = None
    classes: str = None
    id_number: str = None


class ResetPwdIn(BaseModel):
    new_password: str = ""


def public_user(u: dict) -> dict:
    item = {k: u.get(k) for k in ("id", "username", "real_name", "phone", "email", "role",
                                  "status", "grade", "college", "department", "class_name",
                                  "classes", "photo", "scope")}
    item["role_label"] = ROLE_LABEL.get(u["role"], u["role"])
    item["perms"] = effective_perms(u)
    item["perm_labels"] = [PERM_GROUPS.get(p, p) for p in item["perms"]]
    item["custom_perms"] = is_custom_perms(u)
    item["role_perms"] = perms_of(u["role"])
    item["scope"] = scope_of(u)
    item["scope_label"] = scope_label(u)
    item["custom_scope"] = is_custom_scope(u)
    return item


def decorate(u: dict) -> dict:
    item = public_user(u)
    item["app_count"] = 0
    # 遍历全部报名表：漏掉通用表会让「报名数」少算
    for tbl in ET.ALL_APP_TABLES:
        r = db.query_one(f"SELECT COUNT(*) c FROM {tbl} WHERE user_id=?", (u["id"],))
        item["app_count"] += r["c"] if r else 0
    reviewed = db.query_one("SELECT COUNT(*) c FROM audits WHERE reviewer_id=?", (u["id"],))
    item["review_count"] = reviewed["c"] if reviewed else 0
    item["scope_issues"] = scope_issues(u)
    # 实名状态：管理员在用户列表里就能看出谁还没实名，不用跑到实名页一个个查
    item["realname_status"] = RN.status_of(u["id"])
    return item


def scope_issues(u: dict) -> list:
    """核验账号管理范围是否配置完整（缺字段等于看不见任何数据）。"""
    issues = []
    if u.get("status") == 1:
        if u["role"] == "head_teacher":
            if not (u.get("college") or "").strip():
                issues.append("未绑定所属院系，无法判定本院系数据范围（导入时作为考生院系）")
            if not (u.get("classes") or u.get("class_name") or "").strip():
                issues.append("未绑定任何班级，数据范围为空，无法看到任何报名数据")
        if u["role"] == "college_reviewer" and not (u.get("college") or "").strip():
            issues.append("二级学院审核未绑定院系，数据范围为空，无法看到任何报名数据")
        # 逐账号单独收窄过的范围，也要有对应的依据字段
        if scope_of(u) == "scope_grade" and not (u.get("grade") or "").strip():
            issues.append("数据范围为「本年级」但未填写年级，将看不到任何数据")
        if scope_of(u) == "scope_class" and not (u.get("classes") or u.get("class_name") or "").strip():
            issues.append("数据范围为「本班级」但未填写班级，将看不到任何数据")
    if u["role"] == "candidate" and (u.get("college") or u.get("class_name") or u.get("classes")):
        issues.append("考生账号无需管理范围，已填写的院系/班级不参与权限判定")
    return issues


def _normalize_scope(role: str, grade: str, college: str, class_name: str, classes: str = ""):
    grade, college = (grade or "").strip(), (college or "").strip()
    class_name, classes = (class_name or "").strip(), (classes or "").strip()
    if role == "head_teacher":
        if not college:
            raise ApiError("班主任账号必须绑定所属院系")
        if not classes:
            raise ApiError("班主任账号必须绑定至少一个所带班级（多个用逗号分隔）")
    elif role == "college_reviewer":
        # 二级学院审核按「本院系」过滤，没有院系就等于什么都看不到，所以必填
        if not college:
            raise ApiError("二级学院审核账号必须绑定所属院系")
        classes = ""
    # 其余角色（审核员 / 管理员 / 考生）不再强行清空范围字段：
    # 管理员可以把账号「逐账号收窄」到本年级 / 本班级，此时需要 grade / classes
    # 作为过滤依据；未收窄时这些字段不参与任何过滤（范围仍由角色决定），不会越权。
    return grade, college, class_name, classes


# --------------------------------------------------------------- 权限组字典

@router.get("/permission-groups")
def permission_groups(user=Depends(require_admin())):
    """权限组字典：角色 → 权限组映射，供后台核验与前端渲染使用。"""
    return ok({
        "groups": [{"key": k, "label": v, "desc": GROUP_DESC.get(k, ""),
                    "kind": ("scope" if k.startswith("scope_") else "action")}
                   for k, v in PERM_GROUPS.items()],
        "roles": [{"key": r, "label": ROLE_LABEL[r], "perms": ROLE_PERMS[r],
                   "perm_labels": [PERM_GROUPS.get(p, p) for p in ROLE_PERMS[r]],
                   "manage_level": "import" in ROLE_PERMS[r],
                   "scoped": r in SCOPED_ROLES} for r in ROLE_ORDER],
    })


@router.get("/verify")
def verify_perms(only_issues: int = 0, user=Depends(require_admin())):
    """后台核验：逐账号核对「角色 → 权限组 → 数据范围」是否自洽。"""
    rows = db.query(f"SELECT * FROM users{ROLE_ORDER_SQL}")
    items, issue_count = [], 0
    for r in rows:
        issues = scope_issues(r)
        if issues:
            issue_count += 1
        if only_issues and not issues:
            continue
        items.append({
            "id": r["id"], "username": r["username"], "real_name": r["real_name"],
            "role": r["role"], "role_label": ROLE_LABEL.get(r["role"], r["role"]),
            "status": r["status"],
            "perms": effective_perms(r),
            "perm_labels": [PERM_GROUPS.get(p, p) for p in effective_perms(r)],
            "custom_perms": is_custom_perms(r),
            "scope": scope_of(r), "scope_label": scope_label(r),
            "grade": r.get("grade", ""), "college": r.get("college", ""),
            "class_name": r.get("class_name", ""), "classes": r.get("classes", ""),
            "issues": issues,
        })
    return ok({"list": items, "total": len(items), "issue_count": issue_count,
               "checked": len(rows)})


# --------------------------------------------------------------- 列表 / 统计

@router.get("")
def list_users(role: str = "", status: int = -1, keyword: str = "",
               page: int = 1, page_size: int = 10, user=Depends(require_admin())):
    page, page_size = page_params(page, page_size)
    where, params = [], []
    if role:
        where.append("role=?")
        params.append(role)
    if status in (0, 1):
        where.append("status=?")
        params.append(int(status))
    if keyword:
        where.append("(username LIKE ? OR real_name LIKE ? OR phone LIKE ? OR email LIKE ?"
                     " OR college LIKE ? OR class_name LIKE ?)")
        params += [f"%{keyword}%"] * 6
    sql_where = (" WHERE " + " AND ".join(where)) if where else ""
    total = db.query_one(f"SELECT COUNT(*) c FROM users{sql_where}", tuple(params))["c"]
    rows = db.query(
        f"SELECT * FROM users{sql_where}{ROLE_ORDER_SQL} LIMIT ? OFFSET ?",
        tuple(params) + (page_size, (page - 1) * page_size))
    return ok({"list": [decorate(r) for r in rows], "total": total, "page": page,
               "page_size": page_size})


@router.get("/stats")
def stats(user=Depends(require_admin())):
    rows = db.query("SELECT role, status, COUNT(*) c FROM users GROUP BY role, status")
    out = {r: {"total": 0, "enabled": 0, "disabled": 0} for r in ROLES}
    for r in rows:
        role = r["role"]
        if role not in out:
            continue
        out[role]["total"] += r["c"]
        out[role]["enabled" if r["status"] == 1 else "disabled"] += r["c"]
    out["_total"] = sum(v["total"] for v in out.values())
    return ok(out)


@router.get("/options")
def options(user=Depends(require_admin())):
    """角色下拉与权限提示（避免前端硬编码）。"""
    return ok([{"key": r, "label": ROLE_LABEL[r], "perms": ROLE_PERMS[r],
                "perm_labels": [PERM_GROUPS.get(p, p) for p in ROLE_PERMS[r]],
                "manage_level": "import" in ROLE_PERMS[r],
                "scoped": r in SCOPED_ROLES,
                "scope": role_scope(r),
                "scope_label": PERM_GROUPS.get(role_scope(r), SCOPE_SHORT.get(role_scope(r), "")),
                "allowed_scopes": [{"key": s, "label": PERM_GROUPS.get(s, s)}
                                   for s in allowed_scopes(r)]}
               for r in ROLE_ORDER])


# --------------------------------------------------------------- 增 / 改 / 重置

@router.post("")
def create_user(body: UserCreateIn, user=Depends(require_admin())):
    username = (body.username or "").strip()
    phone = (body.phone or "").strip()
    email = (body.email or "").strip()
    if not USERNAME_RE.match(username):
        raise ApiError("用户名需为 3-20 位字母、数字或下划线")
    if not PHONE_RE.match(phone):
        raise ApiError("手机号需为 11 位数字且以 1 开头")
    if email and not EMAIL_RE.match(email):
        raise ApiError("邮箱格式不正确")
    if body.role not in ROLES:
        raise ApiError("角色不合法")
    grade, college, class_name, classes = _normalize_scope(
        body.role, body.grade, body.college, body.class_name, body.classes)
    pwd = (body.password or "").strip() or None
    if pwd:
        err = password_strength_error(pwd)
        if err:
            raise ApiError(err)
    else:
        pwd = f"Exam@{random.randint(1000, 9999)}"
    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        raise ApiError("用户名已被占用")
    if db.query_one("SELECT id FROM users WHERE phone=?", (phone,)):
        raise ApiError("手机号已被注册")
    # 证件号可选登记：既用于身份核对，也是「批量导入证件照按证件号匹配」的依据
    id_number = validate_id_number("1", (body.id_number or "").strip(), required=False)
    if id_number and db.query_one("SELECT id FROM users WHERE id_number=?", (id_number,)):
        raise ApiError("该证件号码已被其它账号登记")

    ts = db.now_str()
    uid = db.execute(
        "INSERT INTO users(username,password_hash,real_name,phone,email,role,grade,college,"
        "department,class_name,classes,id_number,status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (username, hash_password(pwd), body.real_name.strip() or username, phone, email,
         body.role, grade, college, (body.department or "").strip(), class_name, classes,
         id_number, 1 if body.status else 0, ts, ts))
    item = decorate(db.query_one("SELECT * FROM users WHERE id=?", (uid,)))
    item["initial_password"] = pwd
    return ok(item, f"{ROLE_LABEL[body.role]}账号已开通，初始密码：{pwd}")


# ---- 找回密码申请队列（静态路由必须注册在 /{user_id} 之前，否则被动态路由吞掉）----

class RejectIn(BaseModel):
    note: str = ""


@router.get("/password-resets")
def list_password_resets(status: str = "pending", page: int = 1, page_size: int = 20,
                         user=Depends(require_admin())):
    """找回密码申请列表（图形验证码方式身份证核验未通过时产生）。"""
    page, page_size = page_params(page, page_size)
    st = (status or "").strip()
    where, params = "", []
    if st in ("pending", "approved", "rejected"):
        where, params = " WHERE status=?", [st]
    total = db.query_one(f"SELECT COUNT(*) c FROM password_resets{where}", params)["c"]
    rows = db.query(
        f"SELECT * FROM password_resets{where} ORDER BY id DESC LIMIT ? OFFSET ?",
        params + [page_size, (page - 1) * page_size])
    for r in rows:
        u = db.query_one("SELECT username,real_name,phone,role FROM users WHERE id=?",
                         (r["user_id"],)) or {}
        r["user"] = u
        # 身份证只回显脱敏值：审核时够用于核对，又不至于在页面上明文铺开
        idn = (r.get("id_number") or "")
        r["id_number_masked"] = idn[:4] + "*" * max(0, len(idn) - 6) + idn[-2:] if len(idn) > 6 else idn
        r.pop("id_number", None)
    counts = {x["status"]: x["c"] for x in
              db.query("SELECT status, COUNT(*) c FROM password_resets GROUP BY status")}
    return ok({"list": rows, "total": total, "page": page, "page_size": page_size,
               "counts": counts})


@router.post("/password-resets/{rid}/approve")
def approve_password_reset(rid: int, user=Depends(require_admin())):
    """审核通过：为该账号生成一个临时密码并重置（临时密码只在本次返回一次）。"""
    r = db.query_one("SELECT * FROM password_resets WHERE id=?", (rid,))
    if not r:
        raise ApiError("申请不存在", code=404, status=404)
    if r["status"] != "pending":
        raise ApiError("该申请已处理过")
    target = db.query_one("SELECT * FROM users WHERE id=?", (r["user_id"],))
    if not target:
        raise ApiError("关联账号不存在或已删除")
    pwd = f"Exam@{random.randint(1000, 9999)}"
    db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
               (hash_password(pwd), db.now_str(), target["id"]))
    db.execute("UPDATE password_resets SET status='approved', admin_id=?, admin_name=?,"
               " admin_note=?, handled_at=? WHERE id=?",
               (user["id"], user.get("username") or "", "管理员审核通过并重置密码",
                db.now_str(), rid))
    return ok({"id": rid, "username": target.get("username"), "new_password": pwd},
              f"已重置，临时密码：{pwd}（请线下告知本人，并提醒其尽快修改）")


@router.post("/password-resets/{rid}/reject")
def reject_password_reset(rid: int, body: RejectIn, user=Depends(require_admin())):
    """驳回申请。"""
    r = db.query_one("SELECT * FROM password_resets WHERE id=?", (rid,))
    if not r:
        raise ApiError("申请不存在", code=404, status=404)
    if r["status"] != "pending":
        raise ApiError("该申请已处理过")
    db.execute("UPDATE password_resets SET status='rejected', admin_id=?, admin_name=?,"
               " admin_note=?, handled_at=? WHERE id=?",
               (user["id"], user.get("username") or "",
                (body.note or "").strip()[:200] or "管理员驳回", db.now_str(), rid))
    return ok({"id": rid}, "已驳回")


@router.put("/{user_id}")
def update_user(user_id: int, body: UserUpdateIn, user=Depends(require_admin())):
    target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        raise ApiError("用户不存在", code=404, status=404)
    fields, params = [], []

    if body.phone is not None:
        phone = body.phone.strip()
        if phone and not PHONE_RE.match(phone):
            raise ApiError("手机号需为 11 位数字且以 1 开头")
        if phone:
            other = db.query_one("SELECT id FROM users WHERE phone=? AND id<>?", (phone, user_id))
            if other:
                raise ApiError("手机号已被其他账号使用")
        fields.append("phone=?")
        params.append(phone or target["phone"])
    if body.email is not None:
        email = body.email.strip()
        if email and not EMAIL_RE.match(email):
            raise ApiError("邮箱格式不正确")
        fields.append("email=?")
        params.append(email)
    if body.id_number is not None:
        id_number = validate_id_number("1", (body.id_number or "").strip(), required=False)
        if id_number:
            other = db.query_one("SELECT id FROM users WHERE id_number=? AND id<>?",
                                 (id_number, user_id))
            if other:
                raise ApiError("该证件号码已被其它账号登记")
        fields.append("id_number=?")
        params.append(id_number)
    if body.real_name is not None:
        fields.append("real_name=?")
        params.append(body.real_name.strip())

    if body.role is not None:
        if body.role not in ROLES:
            raise ApiError("角色不合法")
        if body.role != target["role"]:
            if user_id == user["id"]:
                raise ApiError("不能修改自己的角色")
            if target["role"] == "admin":
                admins = db.query_one("SELECT COUNT(*) c FROM users WHERE role='admin' AND status=1")["c"]
                if admins <= 1:
                    raise ApiError("系统至少需要保留一名启用状态的管理员")
        fields.append("role=?")
        params.append(body.role)

    if body.status is not None:
        status = 1 if int(body.status) else 0
        if status != target["status"]:
            if user_id == user["id"]:
                raise ApiError("不能禁用自己的账号")
            if status == 0 and target["role"] == "admin":
                admins = db.query_one("SELECT COUNT(*) c FROM users WHERE role='admin' AND status=1")["c"]
                if admins <= 1:
                    raise ApiError("系统至少需要保留一名启用状态的管理员")
        fields.append("status=?")
        params.append(status)

    # 部门只是附加信息，不参与数据范围校验：改它不该触发范围重算
    if body.department is not None:
        fields.append("department=?")
        params.append((body.department or "").strip())

    # 管理范围：任一范围字段变更时按最终角色整体校验（含 classes 多班级）
    if (body.grade is not None or body.college is not None or body.class_name is not None
            or body.classes is not None):
        final_role = body.role if body.role is not None else target["role"]
        grade, college, class_name, classes = _normalize_scope(
            final_role,
            body.grade if body.grade is not None else target.get("grade", ""),
            body.college if body.college is not None else target.get("college", ""),
            body.class_name if body.class_name is not None else target.get("class_name", ""),
            body.classes if body.classes is not None else target.get("classes", ""))
        fields += ["grade=?", "college=?", "class_name=?", "classes=?"]
        params += [grade, college, class_name, classes]
    elif body.role is not None and body.role != target["role"]:
        # 仅改角色：非范围角色需清空历史范围值
        grade, college, class_name, classes = _normalize_scope(
            body.role, target.get("grade", ""), target.get("college", ""), target.get("class_name", ""),
            target.get("classes", ""))
        fields += ["grade=?", "college=?", "class_name=?", "classes=?"]
        params += [grade, college, class_name, classes]

    if not fields:
        raise ApiError("没有需要更新的内容")
    fields.append("updated_at=?")
    params.append(db.now_str())
    params.append(user_id)
    db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", tuple(params))
    return ok(decorate(db.query_one("SELECT * FROM users WHERE id=?", (user_id,))), "已保存")


@router.post("/{user_id}/reset-password")
def reset_password(user_id: int, body: ResetPwdIn, user=Depends(require_admin())):
    target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        raise ApiError("用户不存在", code=404, status=404)
    pwd = (body.new_password or "").strip()
    if not pwd:
        pwd = f"Exam@{random.randint(1000, 9999)}"
    err = password_strength_error(pwd)
    if err:
        raise ApiError(err)
    db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
               (hash_password(pwd), db.now_str(), user_id))
    return ok({"id": user_id, "new_password": pwd}, f"密码已重置为：{pwd}")


@router.get("/{user_id}/perms")
def get_user_perms(user_id: int, user=Depends(require_admin())):
    """读取某账号的功能开关：逐项返回当前是否开启、以及角色默认值。"""
    target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        raise ApiError("用户不存在", code=404, status=404)
    owned = effective_perms(target)
    return ok({
        "id": target["id"],
        "username": target["username"],
        "real_name": target["real_name"],
        "role": target["role"],
        "role_label": ROLE_LABEL.get(target["role"], target["role"]),
        "custom_perms": is_custom_perms(target),
        "perms": owned,
        "role_perms": perms_of(target["role"]),
        "scope": scope_of(target),
        "scope_label": scope_label(target),
        "custom_scope": is_custom_scope(target),
        "role_scope": role_scope(target["role"]),
        # 单独设定的原始值（空串=跟随角色），供后台回显，区别于上面「最终生效」的 scope
        "stored_scope": stored_scope(target),
        # 只列出该角色「有权拥有」的范围档位，从源头杜绝选出超范围的项
        "allowed_scopes": [{"key": s, "label": PERM_GROUPS.get(s, s)}
                           for s in allowed_scopes(target["role"])],
        # 带上说明：管理员面对 14 个开关时，光看名字猜不出「批量导入」
        # 是否包含改别人数据的能力，逐项给一句话解释
        "groups": [{"key": g, "label": PERM_GROUPS.get(g, g),
                    "desc": GROUP_DESC.get(g, ""), "on": g in owned,
                    "role_default": g in perms_of(target["role"])}
                   for g in FUNC_GROUPS],
    })


class UserPermsIn(BaseModel):
    perms: list = []
    scope: str = None


@router.put("/{user_id}/perms")
def update_user_perms(user_id: int, body: UserPermsIn,
                      user=Depends(require_admin())):
    """逐项设置某账号的功能开关（perms 为「最终生效」的开启项）。

    - perms 列出要开启的功能（不含 scope_*；数据范围仍随角色判定）
    - perms 与角色默认值完全一致时，自动清除单独授权，后续跟随角色调整
    - perms 传空数组 = 关闭全部功能（有意为之，不会再回退到角色默认）
      若需恢复角色默认，请传该角色的默认权限组，或使用后台「恢复角色默认权限」
    """
    target = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
    if not target:
        raise ApiError("用户不存在", code=404, status=404)
    if user_id == user["id"]:
        # 避免管理员误关自己的权限导致无法恢复
        raise ApiError("不能修改自己的权限配置，请由其他管理员操作")
    picked = []
    for p in (body.perms or []):
        p = str(p).strip()
        if p not in FUNC_GROUPS:
            raise ApiError(f"功能项不合法：{p}")
        if p not in picked:
            picked.append(p)
    # 与角色默认的功能项完全一致 → 清除单独授权，后续角色调整仍可自动跟随
    # （只比对功能项：数据范围 scope_* 固定随角色，不参与开关）
    if set(picked) == set(role_func_perms(target["role"])):
        stored = ""
    else:
        stored = json.dumps(picked, ensure_ascii=False)
    db.execute("UPDATE users SET perms=?,updated_at=? WHERE id=?",
               (stored, db.now_str(), user_id))

    # 数据范围：可逐账号单独设定，但不得超过该角色的上限（否则就是提权）
    scope_msg = ""
    if body.scope is not None:
        want = (body.scope or "").strip()
        if want and want not in SCOPE_LEVEL:
            raise ApiError(f"数据范围不合法：{want}")
        if want and want not in allowed_scopes(target["role"]):
            top = role_scope(target["role"])
            raise ApiError(
                f"该角色的数据范围最高只能到「{PERM_GROUPS.get(top, top)}」，"
                f"不能设为「{PERM_GROUPS.get(want, want)}」")
        db.execute("UPDATE users SET scope=?,updated_at=? WHERE id=?",
                   (want, db.now_str(), user_id))
        scope_msg = "，数据范围已恢复角色默认" if not want else "，数据范围已单独设定"

    row = db.query_one("SELECT * FROM users WHERE id=?", (user_id,))
    return ok(public_user(row),
              ("已恢复沿用角色默认权限" if not stored else "功能权限已保存（已单独授权）")
              + scope_msg)


@router.get("/{user_id}/applications")
def user_applications(user_id: int, page: int = 1, page_size: int = 10,
                      user=Depends(require_admin())):
    """查看某用户的报名记录（用于账号停用前的影响评估）。"""
    page, page_size = page_params(page, page_size)
    out = []
    for t, tbl in ET.APP_TABLE.items():
        for r in db.query(f"SELECT * FROM {tbl} WHERE user_id=? ORDER BY id DESC", (user_id,)):
            exam = db.query_one("SELECT name,exam_type FROM exams WHERE id=?", (r["exam_id"],))
            item = dict(r)
            item["app_type"] = t
            item["exam_name"] = exam["name"] if exam else ""
            # 自定义类型显示其自定义名称（app_type 仍是基础模板，用于定位存储表）
            item["exam_type_label"] = ET.label_of(exam["exam_type"]) if exam else ""
            out.append(item)
    out.sort(key=lambda x: x["created_at"], reverse=True)
    return ok({"list": out[((page - 1) * page_size):(page * page_size)], "total": len(out),
               "page": page, "page_size": page_size})
