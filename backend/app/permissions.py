# -*- coding: utf-8 -*-
"""角色 / 权限组定义与后端强制核验。

设计要点
--------
1. 角色（role）是「身份」，权限组（permission group）是「能力」。
   角色到权限组的映射集中在本文件，接口层只声明自己需要哪些权限组，
   不再散落 role 字符串判断 —— 新增角色时只需改这一张表。
2. 数据范围（scope）也作为一种权限组表达：
   scope_all（全部） / scope_class（本班级，班主任可管理多班）。
3. 所有涉及数据的接口都必须调用 scope_filter() 做范围收敛，
   避免「有权限但越权看别人数据」。
"""
from fastapi import Depends

from .deps import ApiError, get_current_user

# ------------------------------------------------------------------ 权限组

PERM_GROUPS = {
    "apply":          "在线报名",
    "audit":          "信息审核",
    "import":         "批量导入",
    "export":         "汇总导出",
    "analysis":       "数据分析",
    "exam_manage":    "考试批次管理",
    "dict_manage":    "字典与考试类型维护",
    "user_manage":    "用户与权限管理",
    "dashboard":      "数据看板",
    "idphoto":        "证件照制作",
    "realname":       "实名认证（本人填报）",
    "realname_audit": "实名信息审核",
    "ai":             "AI 助手",
    "system_manage":  "系统维护",
    "scope_class":    "数据范围：本班级（可多班）",
    "scope_grade":    "数据范围：本年级",
    "scope_college":  "数据范围：本院系",
    "scope_all":      "数据范围：全校",
}

# 每个功能权限组的一句话说明：后台授权页直接展示，
# 免得管理员面对一串名字猜「批量导入」到底能不能改别人的数据。
GROUP_DESC = {
    "apply":          "填报与提交自己的报名信息，可撤回待审记录",
    "audit":          "审核范围内的报名记录：通过 / 驳回 / 退回修改",
    "import":         "批量导入报名数据与证件照（仍受本人数据范围约束）",
    "export":         "汇总导出报名数据与照片（导出范围为可见范围）",
    "analysis":       "查看统计分析与数据看板下的明细报表",
    "exam_manage":    "新建 / 编辑 / 发布考试批次，设置照片要求",
    "dict_manage":    "维护字典（学院、部门、职业、科目等）与考试类型字段",
    "user_manage":    "开通账号、改角色与权限（用户管理页，仅管理员角色可用）",
    "dashboard":      "查看 16:9 数据大屏",
    "idphoto":        "使用证件照制作（抠图、换底、排版）",
    "realname":       "填写并提交本人的实名认证信息",
    "realname_audit": "审核他人提交的实名信息（通过 / 驳回）",
    "ai":             "使用 AI 助手问答与智能填表建议",
    "system_manage":  "系统维护：网关配置、数据清理、测试模式",
}

# 可逐个开关的「功能」权限组（不含数据范围；数据范围另按角色上限单独设定）
FUNC_GROUPS = ["apply", "audit", "import", "export", "analysis",
               "exam_manage", "dict_manage", "user_manage", "dashboard",
               "idphoto", "realname", "realname_audit", "ai", "system_manage"]

# 数据范围档位，由小到大（索引即级别，用于「不得超过角色上限」的越权校验）
SCOPE_KEYS = ["scope_class", "scope_grade", "scope_college", "scope_all"]
SCOPE_LEVEL = {k: i for i, k in enumerate(SCOPE_KEYS)}
SCOPE_SHORT = {"scope_class": "本班级", "scope_grade": "本年级",
               "scope_college": "本院系", "scope_all": "全校", "none": "仅本人"}

# 角色 -> 权限组
#
# 细化原则（2026-09 重构）：
#   1. 每个业务模块都有自己的权限组，不再「借」别人的 —— 早先字典维护挂在
#      考试批次管理下，导致「能建批次」就等于「能改全校字典」。
#   2. 考生也能用证件照制作与实名认证（本来就该有），但要能单独关掉。
#   3. 审核分层：二级学院审核能审实名（本院系范围内），不必去找管理员。
ROLE_PERMS = {
    "candidate":        ["apply", "idphoto", "realname", "ai"],
    "head_teacher":     ["apply", "import", "export", "analysis", "dashboard",
                         "idphoto", "realname", "ai", "scope_class"],
    # 二级学院审核：介于班主任（本班）与审核员（全校）之间，审本院系
    "college_reviewer": ["audit", "export", "analysis", "dashboard",
                         "realname_audit", "idphoto", "ai", "scope_college"],
    "reviewer":         ["audit", "export", "analysis", "dashboard",
                         "realname_audit", "idphoto", "ai", "scope_all"],
    "admin":            ["apply", "audit", "import", "export", "analysis",
                         "exam_manage", "dict_manage", "user_manage", "dashboard",
                         "idphoto", "realname", "realname_audit", "ai",
                         "system_manage", "scope_all"],
}

# 老账号补齐：历史上「借」用别组的功能，迁移时按这个映射补上新组，
# 避免单独授权过的账号升级后突然少了字典维护、系统维护这类能力。
PERM_MIGRATE_MAP = {
    "dict_manage": ("exam_manage",),
    "system_manage": ("user_manage",),
    "realname_audit": ("user_manage",),
}

ROLE_LABEL = {
    "candidate":        "考生",
    "head_teacher":     "班主任",
    "college_reviewer": "二级学院审核",
    "reviewer":         "审核员",
    "admin":            "管理员",
}

# 展示与排序用（按数据范围由宽到窄：管理员 > 审核员(全校) > 二级学院审核(本院系)
# > 班主任(本班) > 考生(仅本人)）
ROLE_ORDER = ["admin", "reviewer", "college_reviewer", "head_teacher", "candidate"]

# 需要绑定管理范围字段的角色（班主任绑定多班级；二级学院审核绑定所属院系）
SCOPED_ROLES = ("head_teacher", "college_reviewer")


def perms_of(role: str) -> list:
    """角色默认权限组（未单独配置的用户沿用此值）。"""
    return list(ROLE_PERMS.get(role or "", []))


def role_func_perms(role: str) -> list:
    """角色默认权限中的「功能」部分（剔除数据范围，用于功能开关比对）。"""
    return [p for p in perms_of(role) if p in FUNC_GROUPS]


def _stored_perms(user):
    """读取用户单独配置的权限组。

    返回 None 表示「未单独配置」（沿用角色默认）；
    返回 []  表示「已单独配置但全部关闭」（有意为之，不再回退）。
    """
    if not isinstance(user, dict):
        return None
    raw = user.get("perms")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        s = str(raw).strip()
        if s == "":
            return None          # NULL / 空串 → 未配置
        try:
            import json as _json
            items = _json.loads(s)
        except Exception:
            items = s.replace("，", ",").split(",")
    return [p for p in items if isinstance(p, str) and p in PERM_GROUPS]


def effective_perms(user) -> list:
    """账号实际生效的权限组：单独配置过的用配置值（可能为空），否则沿用角色默认。"""
    stored = _stored_perms(user)
    if stored is not None:
        return stored
    role = user.get("role") if isinstance(user, dict) else user
    return perms_of(role)


def is_custom_perms(user) -> bool:
    """该账号是否被单独授权过（与角色默认值不同）。"""
    return _stored_perms(user) is not None


def stored_scope(user) -> str:
    """账号被单独设定的数据范围（users.scope）；空串表示沿用角色默认。"""
    if not isinstance(user, dict):
        return ""
    raw = (user.get("scope") or "").strip()
    return raw if raw in SCOPE_LEVEL else ""


def role_scope(role: str) -> str:
    """角色默认数据范围（取该角色拥有的最宽一档；无则 none）。"""
    owned = ROLE_PERMS.get(role or "", [])
    for s in reversed(SCOPE_KEYS):
        if s in owned:
            return s
    return "none"


def scope_of(user) -> str:
    """账号实际生效的数据范围。

    可逐账号单独设定，但**不得超过角色默认的上限**——否则给班主任设成「全校」
    就成了提权。超过上限时静默回退到角色默认值。
    """
    role = user.get("role", "") if isinstance(user, dict) else user
    default = role_scope(role)
    stored = stored_scope(user)
    if not stored:
        return default
    if SCOPE_LEVEL.get(stored, -1) > SCOPE_LEVEL.get(default, -1):
        return default                      # 防越权：不给超过角色的范围
    return stored


def is_custom_scope(user) -> bool:
    """该账号是否被单独设定过数据范围。"""
    return bool(stored_scope(user))


def allowed_scopes(role: str) -> list:
    """该角色可设定的数据范围档位（不超过角色默认上限）。"""
    default = role_scope(role)
    top = SCOPE_LEVEL.get(default, -1)
    if top < 0:
        return []
    return [s for s in SCOPE_KEYS if SCOPE_LEVEL[s] <= top]


def has_perm(user, perm: str) -> bool:
    if not user:
        return False
    # 数据范围按「账号实际生效的范围」判定（含逐账号设定后的结果），
    # 而不是死看角色——否则单独收窄过范围的账号仍会被当成全校可见。
    if str(perm).startswith("scope_"):
        return scope_of(user) == perm
    return perm in effective_perms(user)


def is_manage_level(role_or_user) -> bool:
    """管理权限级别：可批量导入、可管理范围内数据。"""
    return "import" in effective_perms(role_or_user)


def covers_scope(user, perm: str) -> bool:
    """范围「覆盖」判定：scope_all 的账号也满足 scope_college 的要求。

    ⚠ has_perm() 对 scope_ 前缀做的是**精确相等**（那是「当前生效范围是 X」的
    语义，用于回显）。判断「能不能看到某范围的数据」必须用这个函数——
    否则全校可见的账号反而通不过 scope_college 校验。
    """
    cur = scope_of(user)
    if cur not in SCOPE_LEVEL or perm not in SCOPE_LEVEL:
        return False
    return SCOPE_LEVEL[cur] >= SCOPE_LEVEL[perm]


def missing_perms(user, perms) -> list:
    """返回缺少的权限组键（前端据此置灰按钮，后端据此报 403）。"""
    owned = effective_perms(user)
    return [p for p in (perms or []) if p not in owned]


def role_label(role: str) -> str:
    return ROLE_LABEL.get(role or "", role or "")


# ------------------------------------------------------------------ 依赖注入

def require_perms(*perms):
    """声明接口所需权限组（满足其一即可，多个视为「或」）。"""
    def _dep(user=Depends(get_current_user)):
        owned = effective_perms(user)
        if perms and not any(p in owned for p in perms):
            raise ApiError("无权访问该功能（缺少权限：" +
                           "/".join(PERM_GROUPS.get(p, p) for p in perms) + "）",
                           code=403, status=403)
        return user
    return _dep


def require_admin():
    """仅管理员可用（不看权限组）。

    用户管理涉及开号、改角色与改权限，属于最高危操作：即便管理员把
    ``user_manage`` 单独授权给了某个非管理员账号，这里也一律拦掉——
    授权只能用来分配功能，不能用来提权到能改别人的账号。
    """
    def _dep(user=Depends(get_current_user)):
        if (user.get("role") or "") != "admin":
            raise ApiError("仅管理员可使用用户管理", code=403, status=403)
        return user
    return _dep


def require_all_perms(*perms):
    """要求同时具备全部权限组。"""
    def _dep(user=Depends(get_current_user)):
        owned = effective_perms(user)
        missing = [p for p in perms if p not in owned]
        if missing:
            raise ApiError("无权访问该功能（缺少权限：" +
                           "/".join(PERM_GROUPS.get(p, p) for p in missing) + "）",
                           code=403, status=403)
        return user
    return _dep


# ------------------------------------------------------------------ 数据范围

def _college_column(table: str) -> str:
    """报名表里承载「院系」的列名。

    普通话模板叫 department，计算机模板叫 school，通用模板叫 college ——
    三张表列名不同，范围过滤必须按表取，写死一个会导致本院系范围静默漏数据。
    """
    t = table or ""
    if "mandarin" in t:
        return "department"
    if "generic" in t:
        return "college"
    return "school"


def scope_filter(user, table: str):
    """返回 (sql 片段, 参数列表)，片段以 ' AND ' 开头，用于追加到 WHERE 子句。"""
    scope = scope_of(user)
    if scope == "scope_all":
        return "", []
    if scope == "scope_college":
        college = (user.get("college") or "").strip()
        if not college:
            return " AND 1=0", []
        return f" AND {_college_column(table)}=?", [college]
    if scope == "scope_grade":
        # 报名表没有年级字段（官方模板表头不能随便加列），只能关联 users 取同年级班级。
        # 已知局限：批量导入且未建账号的考生不在 users 里，这一档会漏掉他们。
        grade = (user.get("grade") or "").strip()
        if not grade:
            return " AND 1=0", []
        return (" AND class_name IN (SELECT class_name FROM users WHERE grade=?"
                " AND class_name<>'')", [grade])
    if scope == "scope_class":
        # 班主任可管理多个班级：classes 字段以逗号分隔，按 IN 匹配报名记录 class_name
        classes = _split_classes(user.get("classes") or user.get("class_name") or "")
        if not classes:
            return " AND 1=0", []
        ph = ",".join("?" for _ in classes)
        return f" AND class_name IN ({ph})", classes
    return " AND 1=0", []


def _split_classes(raw: str) -> list:
    return [c.strip() for c in (raw or "").replace("，", ",").split(",") if c.strip()]


def can_access_user(operator, target) -> bool:
    """operator 能否操作 target 这个**账号**（按数据范围收敛，不只是报名记录）。

    账号维度的操作（改资料、导证件照、看实名）以前只校验了权限组，没校验范围：
    班主任拿着「批量导入」就能按文件名给全校任何人换证件照。这里补上这一层，
    与报名数据的 scope_filter 保持同一套口径。
    """
    if not operator or not target:
        return False
    if target.get("id") and target.get("id") == operator.get("id"):
        return True                                  # 自己的账号永远可操作
    scope = scope_of(operator)
    if scope == "scope_all":
        return True
    if scope == "scope_college":
        c = (operator.get("college") or "").strip()
        return bool(c) and (target.get("college") or "").strip() == c
    if scope == "scope_grade":
        g = (operator.get("grade") or "").strip()
        return bool(g) and (target.get("grade") or "").strip() == g
    if scope == "scope_class":
        owned = set(_split_classes(operator.get("classes") or operator.get("class_name") or ""))
        theirs = _split_classes(target.get("classes") or target.get("class_name") or "")
        return bool(owned) and any(c in owned for c in theirs)
    return False


def user_scope_filter(operator, alias: str = "u"):
    """账号表的范围过滤（报名表用 scope_filter，两张表的列名不一样）。

    返回 (以 ' AND ' 开头的 SQL 片段, 参数列表)；alias 是 users 表的别名。
    """
    scope = scope_of(operator)
    if scope == "scope_all":
        return "", []
    if scope == "scope_college":
        c = (operator.get("college") or "").strip()
        return (f" AND {alias}.college=?", [c]) if c else (" AND 1=0", [])
    if scope == "scope_grade":
        g = (operator.get("grade") or "").strip()
        return (f" AND {alias}.grade=?", [g]) if g else (" AND 1=0", [])
    if scope == "scope_class":
        cs = _split_classes(operator.get("classes") or operator.get("class_name") or "")
        if not cs:
            return " AND 1=0", []
        ph = ",".join("?" for _ in cs)
        return f" AND {alias}.class_name IN ({ph})", cs
    return " AND 1=0", []


def scope_label(user) -> str:
    scope = scope_of(user)
    if scope == "scope_all":
        return "全部数据"
    if scope == "scope_college":
        college = (user.get("college") or "").strip()
        return f"本院系（{college}）" if college else "本院系（未设置）"
    if scope == "scope_grade":
        grade = (user.get("grade") or "").strip()
        return f"本年级（{grade}）" if grade else "本年级（未设置）"
    if scope == "scope_class":
        classes = _split_classes(user.get("classes") or user.get("class_name") or "")
        return f"本班级（{'、'.join(classes)}）" if classes else "本班级（未设置）"
    return "仅本人数据"


def describe(user) -> dict:
    """返回给前端的权限描述（前端据此渲染菜单与按钮）。"""
    role = user.get("role", "")
    perms = effective_perms(user)
    return {
        "role": role,
        "role_label": role_label(role),
        "perms": perms,
        "perm_labels": [PERM_GROUPS.get(p, p) for p in perms],
        # 每个权限组的名称 + 说明 + 是否拥有：前端菜单/按钮直接按这个渲染，
        # 不必再在前端硬编码一份权限清单（两边不同步就会出现「按钮显示但 403」）
        "perm_detail": [{"key": p, "label": PERM_GROUPS.get(p, p),
                         "desc": GROUP_DESC.get(p, "")} for p in perms],
        "custom_perms": is_custom_perms(user),
        "manage_level": is_manage_level(user),
        "scope": scope_of(user),
        "scope_label": scope_label(user),
        "custom_scope": is_custom_scope(user),
        "allowed_scopes": [{"key": s, "label": PERM_GROUPS.get(s, s)}
                           for s in allowed_scopes(role)],
        "college": user.get("college", "") or "",
        "class_name": user.get("class_name", "") or "",
        "classes": user.get("classes", "") or "",
        "grade": user.get("grade", "") or "",
    }
