# -*- coding: utf-8 -*-
"""模块6：数据分析（汇总指标 + 多维图表数据）。

维度覆盖
--------
审核状态 / 考试批次 / 月度趋势 / 性别 / 学历
报考科目·职业 / 考试机构 / 考点
出生地 省→市 / 现居住地 省→市 / 班级 / 院系

所有查询都会叠加当前账号的数据范围（全校 / 本班级（班主任可多班））。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from .. import db
from ..permissions import has_perm, require_perms, scope_filter, scope_label
from ..deps import ok
from .applications import AUDIT_LABEL
from .. import exam_types as ET
from .exams import TYPE_LABEL

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

TABLES = {"computer": "applications_computer", "mandarin": "applications_mandarin",
          "generic": "applications_generic"}


def build_scope(user, exam_id: int = 0, exam_type: str = "", date_from: str = "",
                date_to: str = ""):
    """返回 [(类型, 表名, WHERE 子句, 参数)]，已叠加数据范围。

    exam_type 可能是自定义编码：选基础类型时包含所有基于该模板的批次
    （含自定义类型），选自定义类型时只统计该类型自己的批次。
    """
    et = ET.base_type_of(exam_type) if exam_type else ""
    types = [et] if et in TABLES else list(TABLES)
    if exam_type and exam_type in ET.BASE_TYPES:
        type_sql = ("exam_id IN (SELECT id FROM exams WHERE exam_type=?"
                    " OR exam_type IN (SELECT code FROM exam_types WHERE base_type=?))")
        type_params = (exam_type, exam_type)
    elif exam_type:
        type_sql = "exam_id IN (SELECT id FROM exams WHERE exam_type=?)"
        type_params = (exam_type,)
    else:
        type_sql, type_params = "", ()
    scope = []
    for t in types:
        where, params = [], []
        if exam_id:
            where.append("exam_id=?")
            params.append(int(exam_id))
        if type_sql:
            where.append(type_sql)
            params += list(type_params)
        if date_from:
            where.append("created_at>=?")
            params.append(date_from + " 00:00:00")
        if date_to:
            where.append("created_at<=?")
            params.append(date_to + " 23:59:59")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        frag, sparams = scope_filter(user, TABLES[t])
        clause = (clause or " WHERE 1=1") + frag
        scope.append((t, TABLES[t], clause, tuple(params + sparams)))
    return scope


def summary_of(scope):
    total = pending = approved = rejected = returned = today = 0
    today_str = db.now_str()[:10]
    for _, tbl, clause, params in scope:
        r = db.query_one(
            f"SELECT COUNT(*) total,"
            f" SUM(audit_status='pending') pending,"
            f" SUM(audit_status='approved') approved,"
            f" SUM(audit_status='rejected') rejected,"
            f" SUM(audit_status='returned') returned,"
            f" SUM(substr(created_at,1,10)=?) today FROM {tbl}{clause}",
            (today_str,) + params)
        total += r["total"] or 0
        pending += r["pending"] or 0
        approved += r["approved"] or 0
        rejected += r["rejected"] or 0
        returned += r["returned"] or 0
        today += r["today"] or 0
    reviewed = approved + rejected + returned
    return {
        "total": total, "pending": pending, "approved": approved,
        "rejected": rejected, "returned": returned, "today": today,
        "reviewed": reviewed,
        "pass_rate": round(approved / reviewed * 100, 2) if reviewed else 0.0,
        "reject_rate": round(rejected / reviewed * 100, 2) if reviewed else 0.0,
        "pending_rate": round(pending / total * 100, 2) if total else 0.0,
    }


def _rank(scope, expr: str, limit: int = 12, exclude_empty: bool = True):
    """按表达式聚合计数并降序取前 N（跨两张表合并）。"""
    m = {}
    for _t, tbl, clause, params in scope:
        for r in db.query(f"SELECT {expr} k, COUNT(*) c FROM {tbl}{clause} GROUP BY k", params):
            k = r["k"]
            if k is None or (exclude_empty and str(k).strip() == ""):
                continue
            m[str(k)] = m.get(str(k), 0) + r["c"]
    return [{"name": k, "value": v} for k, v in sorted(m.items(), key=lambda x: -x[1])[:limit]]


def _merge_rank(a: list, b: list, limit: int = 12) -> list:
    """合并两个同名维度的排行（如「院系」在普通话表里叫 department、通用表里叫 college）。"""
    m = {}
    for r in (a or []) + (b or []):
        m[r["name"]] = m.get(r["name"], 0) + r["value"]
    return [{"name": k, "value": v} for k, v in sorted(m.items(), key=lambda x: -x[1])[:limit]]


def _two_level(scope, prov_col: str, city_col: str, limit: int = 10):
    """省 → 市 两级分布，返回 [{province, total, cities:[{name,value}]}]。"""
    tree = {}
    for _t, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT {prov_col} p, {city_col} c, COUNT(*) n FROM {tbl}{clause}"
                f" GROUP BY p, c", params):
            p = (r["p"] or "").strip()
            if not p:
                continue
            node = tree.setdefault(p, {"province": p, "total": 0, "cities": {}})
            node["total"] += r["n"]
            c = (r["c"] or "").strip()
            if c:
                node["cities"][c] = node["cities"].get(c, 0) + r["n"]
    out = sorted(tree.values(), key=lambda x: -x["total"])[:limit]
    for node in out:
        node["cities"] = [{"name": k, "value": v} for k, v in
                          sorted(node["cities"].items(), key=lambda x: -x[1])[:12]]
    return out


@router.get("/summary")
def summary(exam_id: int = 0, exam_type: str = "", date_from: str = "", date_to: str = "",
            user=Depends(require_perms("analysis"))):
    scope = build_scope(user, exam_id, exam_type, date_from, date_to)
    data = summary_of(scope)
    data["type_breakdown"] = []
    for t, tbl, clause, params in scope:
        r = db.query_one(f"SELECT COUNT(*) c FROM {tbl}{clause}", params)
        data["type_breakdown"].append({"type": t, "label": TYPE_LABEL[t], "count": r["c"]})
    data["scope_label"] = scope_label(user)
    data["scope"] = "all" if has_perm(user, "scope_all") else "class"
    return ok(data)


@router.get("/charts")
def charts(exam_id: int = 0, exam_type: str = "", date_from: str = "", date_to: str = "",
           user=Depends(require_perms("analysis"))):
    scope = build_scope(user, exam_id, exam_type, date_from, date_to)

    # 审核状态构成
    status_count = {"pending": 0, "approved": 0, "rejected": 0, "returned": 0}
    for _, tbl, clause, params in scope:
        for r in db.query(f"SELECT audit_status s, COUNT(*) c FROM {tbl}{clause} GROUP BY audit_status",
                          params):
            status_count[r["s"]] = status_count.get(r["s"], 0) + r["c"]
    status_pie = [{"name": AUDIT_LABEL[k], "value": v} for k, v in status_count.items()]

    # 各批次报名量
    exam_rows = {}
    for t, tbl, clause, params in scope:
        for r in db.query(f"SELECT exam_id, COUNT(*) c FROM {tbl}{clause} GROUP BY exam_id", params):
            exam_rows[r["exam_id"]] = exam_rows.get(r["exam_id"], 0) + r["c"]
    exam_bar = []
    for eid, cnt in exam_rows.items():
        e = db.query_one("SELECT name,exam_type,exam_year,exam_month FROM exams WHERE id=?", (eid,))
        if e:
            exam_bar.append({"exam_id": eid, "name": e["name"], "type": e["exam_type"],
                             "sort_key": (e["exam_year"], e["exam_month"]), "count": cnt})
    exam_bar.sort(key=lambda x: x["sort_key"])
    for e in exam_bar:
        e.pop("sort_key", None)

    # 按月趋势
    month_map = {}
    for _, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT substr(created_at,1,7) m, COUNT(*) c FROM {tbl}{clause} GROUP BY m", params):
            month_map[r["m"]] = month_map.get(r["m"], 0) + r["c"]
    month_line = [{"month": k, "count": v} for k, v in sorted(month_map.items())]

    # 考点 / 考试机构（计算机类专用；普通话类按院系聚合）
    site_rank = _rank([s for s in scope if s[0] == "computer"], "exam_site_code", 12)
    org_rank = _rank([s for s in scope if s[0] == "computer"], "org_name", 12)

    # 地区：出生地 省→市
    birth_tree = _two_level([s for s in scope if s[0] == "mandarin"], "birth_province", "birth_city")
    live_tree = _two_level([s for s in scope if s[0] == "mandarin"], "live_province", "live_city")
    # 兼容旧图表：地区柱状（出生省）
    region_bar = [{"name": n["province"], "value": n["total"]} for n in birth_tree]

    # 班级 / 院系分布（管理级别关注）
    class_rank = _rank(scope, "class_name", 12)
    # 「院系」维度：普通话 department / 通用 college / 计算机 school 分开聚合再合并
    dept_rank = _merge_rank(_rank([s for s in scope if s[0] == "mandarin"], "department", 12),
                            _rank([s for s in scope if s[0] == "generic"], "college", 12))
    school_rank = _rank([s for s in scope if s[0] == "computer"], "school", 12)

    # 职业（普通话）/ 报考科目（计算机）/ 院系（通用模板）排行
    item_map = {}
    for t, tbl, clause, params in scope:
        col = {"mandarin": "occupation", "generic": "college"}.get(t, "subject")
        for r in db.query(f"SELECT {col} k, COUNT(*) c FROM {tbl}{clause} GROUP BY {col}", params):
            if r["k"]:
                item_map[r["k"]] = item_map.get(r["k"], 0) + r["c"]
    item_rank = [{"name": k, "value": v} for k, v in
                 sorted(item_map.items(), key=lambda x: -x[1])[:12]]

    # 性别 / 学历 / 民族
    gender_map, edu_map, eth_map = {}, {}, {}
    for _, tbl, clause, params in scope:
        for r in db.query(f"SELECT gender g, COUNT(*) c FROM {tbl}{clause} GROUP BY gender", params):
            if r["g"]:
                gender_map[r["g"]] = gender_map.get(r["g"], 0) + r["c"]
        if tbl == TABLES["computer"]:
            for r in db.query(f"SELECT education e, COUNT(*) c FROM {tbl}{clause} GROUP BY education",
                              params):
                if r["e"]:
                    edu_map[r["e"]] = edu_map.get(r["e"], 0) + r["c"]
        elif tbl == TABLES["mandarin"]:
            # 通用模板没有民族列，跳过即可
            for r in db.query(f"SELECT ethnicity e, COUNT(*) c FROM {tbl}{clause} GROUP BY e",
                              params):
                if r["e"]:
                    eth_map[r["e"]] = eth_map.get(r["e"], 0) + r["c"]
    gender_pie = [{"name": k, "value": v} for k, v in gender_map.items()]
    edu_bar = [{"name": k, "value": v} for k, v in sorted(edu_map.items(), key=lambda x: -x[1])]
    ethnicity_bar = [{"name": k, "value": v} for k, v in
                     sorted(eth_map.items(), key=lambda x: -x[1])[:10]]

    return ok({
        "status_pie": status_pie,
        "exam_bar": exam_bar,
        "month_line": month_line,
        "region_bar": region_bar,
        "birth_tree": birth_tree,
        "live_tree": live_tree,
        "site_rank": site_rank,
        "org_rank": org_rank,
        "class_rank": class_rank,
        "dept_rank": dept_rank,
        "school_rank": school_rank,
        "item_rank": item_rank,
        "gender_pie": gender_pie,
        "edu_bar": edu_bar,
        "ethnicity_bar": ethnicity_bar,
        "summary": summary_of(scope),
        "scope_label": scope_label(user),
    })


# ------------------------------------------------------------------ 深度分析

AGE_BUCKETS = [(0, 17, "17 岁及以下"), (18, 19, "18-19 岁"), (20, 22, "20-22 岁"),
               (23, 25, "23-25 岁"), (26, 30, "26-30 岁"), (31, 40, "31-40 岁"),
               (41, 200, "41 岁以上")]
# 时长分布（小时）：(下界, 上界, 名称)
HOUR_BUCKETS = [(0, 1, "1 小时内"), (1, 6, "1-6 小时"), (6, 24, "6-24 小时"),
                (24, 72, "1-3 天"), (72, 1 << 30, "3 天以上")]
# 驳回意见归类：先看关键词，归类后比逐条罗列更有指导性
REJECT_KEYWORDS = [
    ("照片", ["照片", "证件照", "头像", "图片"]),
    # 联系方式要排在证件号码前面：否则「手机号码格式有误」会被归进证件号码
    ("联系方式", ["电话", "手机", "联系", "打不通"]),
    ("证件号码", ["证件号", "身份证", "证件编", "证件"]),
    ("班级 / 院系", ["班级", "院系", "学院", "专业", "年级"]),
    ("姓名", ["姓名", "名字", "改名"]),
    ("学历 / 职业", ["学历", "职业", "单位", "在职"]),
    ("地区信息", ["地址", "省份", "城市", "区县", "邮编", "邮寄"]),
    ("重复报名", ["重复", "已报名", "多次"]),
    ("材料不全", ["缺少", "不全", "未填写", "空白", "缺失"]),
]


def _collect(scope) -> list:
    """取出分析所需的全部行（含类型标记）。"""
    out = []
    for t, tbl, clause, params in scope:
        for r in db.query(f"SELECT *, '{t}' _type FROM {tbl}{clause}", params):
            out.append(dict(r))
    return out


def _hours_between(a: str, b: str):
    """两个 'YYYY-MM-DD HH:MM:SS' 之间相差的小时数；解析失败返回 None。"""
    if not (a and b):
        return None
    try:
        d1 = datetime.strptime(a[:19], "%Y-%m-%d %H:%M:%S")
        d2 = datetime.strptime(b[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return max(0.0, (d2 - d1).total_seconds() / 3600.0)


def _age_of(row: dict, now_year: int):
    """从身份证号取出生年份算年龄；非身份证或解析失败返回 None。"""
    if str(row.get("id_type") or "1") != "1":
        return None
    s = (row.get("id_number") or "").strip()
    if len(s) < 11:
        return None
    try:
        y = int(s[6:10])
    except ValueError:
        return None
    if not 1900 <= y <= now_year:
        return None
    return now_year - y


def _bucket_age(age: int) -> str:
    for lo, hi, label in AGE_BUCKETS:
        if lo <= age <= hi:
            return label
    return "未知"


def _bucket_hours(h: float) -> str:
    for lo, hi, label in HOUR_BUCKETS:
        if lo <= h < hi:
            return label
    return "3 天以上"


def _org_of(row: dict) -> str:
    """单位 / 院校：三类模板列名不同，统一取一个口径。"""
    t = row.get("_type")
    if t == "computer":
        return (row.get("school") or row.get("org_name") or "").strip()
    if t == "mandarin":
        return (row.get("employer") or row.get("department") or "").strip()
    return (row.get("college") or "").strip()


def _profile(rows: list, now_year: int) -> dict:
    gender, age, edu, eth, occ = {}, {}, {}, {}, {}
    for r in rows:
        g = (r.get("gender") or "").strip()
        if g:
            gender[g] = gender.get(g, 0) + 1
        a = _age_of(r, now_year)
        if a is not None:
            k = _bucket_age(a)
            age[k] = age.get(k, 0) + 1
        e = (r.get("education") or "").strip()
        if e:
            edu[e] = edu.get(e, 0) + 1
        x = (r.get("ethnicity") or "").strip()
        if x:
            eth[x] = eth.get(x, 0) + 1
        o = (r.get("occupation") or "").strip()
        if o:
            occ[o] = occ.get(o, 0) + 1
    order_age = [lab for _lo, _hi, lab in AGE_BUCKETS]
    return {
        "gender": [{"name": k, "value": v} for k, v in
                   sorted(gender.items(), key=lambda x: -x[1])],
        "age": [{"name": k, "value": age.get(k, 0)} for k in order_age if age.get(k)],
        "education": [{"name": k, "value": v} for k, v in
                      sorted(edu.items(), key=lambda x: -x[1])],
        "ethnicity": [{"name": k, "value": v} for k, v in
                      sorted(eth.items(), key=lambda x: -x[1])[:12]],
        "occupation": [{"name": k, "value": v} for k, v in
                       sorted(occ.items(), key=lambda x: -x[1])[:12]],
        "age_known": sum(age.values()),
    }


def _org_class_matrix(rows: list, limit: int = 10) -> list:
    """单位 / 院系 → 班级 两级分布（看一个单位下面各班报了多少）。"""
    tree = {}
    for r in rows:
        org = _org_of(r) or "未填写"
        node = tree.setdefault(org, {"name": org, "value": 0, "children": {}})
        node["value"] += 1
        c = (r.get("class_name") or "").strip() or "未填班级"
        node["children"][c] = node["children"].get(c, 0) + 1
    out = sorted(tree.values(), key=lambda x: -x["value"])[:limit]
    for n in out:
        n["children"] = [{"name": k, "value": v} for k, v in
                         sorted(n["children"].items(), key=lambda x: -x[1])[:10]]
    return out


def _efficiency(rows: list) -> dict:
    """审核时效：均值 / 中位数 / 分布 / 审核员工作量。"""
    diffs = []
    buckets = {lab: 0 for _lo, _hi, lab in HOUR_BUCKETS}
    for r in rows:
        if r.get("audit_status") == "pending" or not r.get("reviewed_at"):
            continue
        h = _hours_between(r.get("created_at"), r.get("reviewed_at"))
        if h is None:
            continue
        diffs.append(h)
        buckets[_bucket_hours(h)] = buckets.get(_bucket_hours(h), 0) + 1
    diffs.sort()
    n = len(diffs)
    avg = round(sum(diffs) / n, 1) if n else 0.0
    median = round(diffs[n // 2], 1) if n else 0.0
    p90 = round(diffs[max(0, int(n * 0.9) - 1)], 1) if n else 0.0
    return {
        "reviewed": n, "avg_hours": avg, "median_hours": median, "p90_hours": p90,
        "buckets": [{"name": lab, "value": buckets.get(lab, 0)}
                    for _lo, _hi, lab in HOUR_BUCKETS],
    }


def _reviewer_rank(scope) -> list:
    """审核员工作量与平均处理时长（只数当前范围内被审过的记录）。"""
    stat = {}
    for t, tbl, clause, params in scope:
        for r in db.query(
                "SELECT a.reviewer_name rn, a.created_at at, p.created_at ct, p.id pid"
                " FROM audits a JOIN " + tbl + " p ON p.id=a.app_id"
                " WHERE a.app_type=? AND a.action IN ('approve','reject','return')"
                " AND p.id IN (SELECT id FROM " + tbl + clause + ")",
                (t,) + tuple(params)):
            name = (r["rn"] or "").strip() or "未知"
            node = stat.setdefault(name, {"name": name, "count": 0, "hours": 0.0, "n": 0})
            node["count"] += 1
            h = _hours_between(r["ct"], r["at"])
            if h is not None:
                node["hours"] += h
                node["n"] += 1
    out = []
    for node in stat.values():
        node["avg_hours"] = round(node["hours"] / node["n"], 1) if node["n"] else 0.0
        node.pop("hours", None)
        node.pop("n", None)
        out.append(node)
    out = sorted(out, key=lambda x: -x["count"])[:10]
    if out:
        return out

    # 兜底：早期数据与批量审核可能没写 audits 流水，改用报名行上的 reviewed_by
    #
    # ⚠ 这里必须先把「带范围过滤的报名表」包成派生表再 JOIN users：
    #   范围片段里的列名是不带表名的（class_name / school …），而 users 也有
    #   class_name，直接 `FROM tbl p LEFT JOIN users u ON ... WHERE class_name IN (...)`
    #   会让 SQLite 报「ambiguous column name: class_name」（班主任 scope_class 必现）。
    #   派生表是独立作用域，裸列名只落在子查询里，不会和外层的 users 撞。
    by = {}
    for t, tbl, clause, params in scope:
        for r in db.query(
                "SELECT p.reviewed_by uid, u.real_name nm, u.username un, COUNT(*) c,"
                " AVG((julianday(p.reviewed_at)-julianday(p.created_at))*24) h"
                f" FROM (SELECT * FROM {tbl}{clause}) p"
                " LEFT JOIN users u ON u.id=p.reviewed_by"
                " WHERE p.reviewed_at<>'' AND p.reviewed_at IS NOT NULL"
                " GROUP BY p.reviewed_by", params):
            if not r["uid"]:
                continue
            name = (r["nm"] or r["un"] or "未知").strip()
            node = by.setdefault(name, {"name": name, "count": 0, "avg_hours": 0.0, "h": 0.0})
            node["count"] += r["c"]
            # 加权平均：各表的 AVG 再按各自条数加权，不能直接对 AVG 求平均
            node["h"] += (r["h"] or 0.0) * r["c"]
    # ⚠ 收尾必须在「遍历完所有表」之后：写在表循环里会在第一张表后就 pop 掉 h，
    #    第二张表 setdefault 拿到已删键的节点，直接 KeyError
    for node in by.values():
        node["avg_hours"] = round(node["h"] / node["count"], 1) if node["count"] else 0.0
        node.pop("h", None)
    return sorted(by.values(), key=lambda x: -x["count"])[:10]


def _reject_reasons(rows: list, limit: int = 8) -> list:
    """驳回 / 退回原因归类：先按关键词归并，再给原文样例。"""
    plain, samples = {}, {}
    for r in rows:
        if r.get("audit_status") not in ("rejected", "returned"):
            continue
        c = (r.get("audit_comment") or "").strip()
        if not c:
            plain["（未填写意见）"] = plain.get("（未填写意见）", 0) + 1
            continue
        hit = ""
        for label, words in REJECT_KEYWORDS:
            if any(w in c for w in words):
                hit = label
                break
        key = hit or "其他"
        plain[key] = plain.get(key, 0) + 1
        samples.setdefault(key, [])
        if len(samples[key]) < 3 and c not in samples[key]:
            samples[key].append(c[:40])
    out = [{"name": k, "value": v, "samples": samples.get(k, [])}
           for k, v in sorted(plain.items(), key=lambda x: -x[1])]
    return out[:limit]


def _photo_stats(rows: list) -> dict:
    """证件照完整率（整体 + 按单位）。"""
    ids = [r.get("user_id") for r in rows if r.get("user_id")]
    with_photo = set()
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" for _ in chunk)
        for r in db.query(f"SELECT id FROM users WHERE id IN ({ph}) AND photo<>'' "
                          "AND photo IS NOT NULL", tuple(chunk)):
            with_photo.add(r["id"])
    by = {}
    for r in rows:
        org = _org_of(r) or "未填写"
        node = by.setdefault(org, {"name": org, "total": 0, "with_photo": 0})
        node["total"] += 1
        if r.get("user_id") in with_photo:
            node["with_photo"] += 1
    total = len(rows)
    ok_n = sum(1 for r in rows if r.get("user_id") in with_photo)
    return {
        "total": total, "with_photo": ok_n, "missing": total - ok_n,
        "rate": round(ok_n / total * 100, 2) if total else 0.0,
        "by_org": [{"name": n["name"], "total": n["total"], "with_photo": n["with_photo"],
                    "rate": round(n["with_photo"] / n["total"] * 100, 1) if n["total"] else 0.0}
                   for n in sorted(by.values(), key=lambda x: -x["total"])[:10]],
    }


def _completeness(scope) -> list:
    """必填字段缺失率：最能反映「填报质量」的指标。"""
    from .applications import FIXED_REQUIRED
    out = []
    for t, tbl, clause, params in scope:
        total = db.query_one(f"SELECT COUNT(*) c FROM {tbl}{clause}", params)["c"]
        if not total:
            continue
        for f in sorted(FIXED_REQUIRED.get(t, set())):
            r = db.query_one(
                f"SELECT SUM(CASE WHEN {f} IS NULL OR trim({f})='' THEN 1 ELSE 0 END) m"
                f" FROM {tbl}{clause}", params)
            miss = (r or {}).get("m") or 0
            if not miss:
                continue
            out.append({"type": t, "field": f, "total": total, "missing": miss,
                        "rate": round(miss / total * 100, 2)})
    return sorted(out, key=lambda x: -x["missing"])[:12]


def _batch_progress(rows: list) -> list:
    """各批次的报名量与审核进度。"""
    exams = {e["id"]: e for e in db.query("SELECT id,name,exam_type,status FROM exams")}
    agg = {}
    for r in rows:
        eid = r.get("exam_id")
        node = agg.setdefault(eid, {"total": 0, "approved": 0, "pending": 0,
                                    "rejected": 0, "returned": 0})
        node["total"] += 1
        st = r.get("audit_status") or ""
        if st in node:
            node[st] += 1
    out = []
    for eid, n in agg.items():
        e = exams.get(eid) or {}
        reviewed = n["approved"] + n["rejected"] + n["returned"]
        out.append({"exam_id": eid, "name": e.get("name") or f"批次{eid}",
                    "type_label": TYPE_LABEL.get(e.get("exam_type"), e.get("exam_type") or ""),
                    "total": n["total"], "approved": n["approved"], "pending": n["pending"],
                    "rejected": n["rejected"], "returned": n["returned"],
                    "pass_rate": round(n["approved"] / reviewed * 100, 1) if reviewed else 0.0,
                    "reviewed_rate": round(reviewed / n["total"] * 100, 1) if n["total"] else 0.0})
    return sorted(out, key=lambda x: -x["total"])[:12]


def _trend_daily(rows: list, days: int = 30) -> dict:
    """近 N 天的「新增报名 / 完成审核」双线趋势 + 按周聚合。"""
    today = datetime.now().date()
    dmap = {str(today - timedelta(days=i)): [0, 0] for i in range(days - 1, -1, -1)}
    for r in rows:
        d = (r.get("created_at") or "")[:10]
        if d in dmap:
            dmap[d][0] += 1
        rd = (r.get("reviewed_at") or "")[:10]
        if rd in dmap:
            dmap[rd][1] += 1
    week = {}
    for d, (a, b) in dmap.items():
        dt = datetime.strptime(d, "%Y-%m-%d").date()
        wk = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        node = week.setdefault(wk, {"week": wk, "submitted": 0, "reviewed": 0})
        node["submitted"] += a
        node["reviewed"] += b
    return {
        "days": [{"date": d[5:], "submitted": v[0], "reviewed": v[1]}
                 for d, v in sorted(dmap.items())],
        "weeks": [week[k] for k in sorted(week)],
    }


@router.get("/deep")
def deep(exam_id: int = 0, exam_type: str = "", date_from: str = "", date_to: str = "",
         user=Depends(require_perms("analysis"))):
    """深度分析：人口画像 / 审核时效 / 驳回原因 / 照片完整率 / 批次进度 / 趋势。

    与 /charts 的分工：charts 给的是「一眼看完」的常规图表，
    deep 给的是要看原因、找瓶颈、写汇报材料时才需要的细粒度指标。
    """
    scope = build_scope(user, exam_id, exam_type, date_from, date_to)
    rows = _collect(scope)
    now_year = datetime.now().year
    return ok({
        "summary": summary_of(scope),
        "scope_label": scope_label(user),
        "profile": _profile(rows, now_year),
        "org_class": _org_class_matrix(rows),
        "efficiency": _efficiency(rows),
        "reviewer_rank": _reviewer_rank(scope),
        "reject_reasons": _reject_reasons(rows),
        "photo": _photo_stats(rows),
        "completeness": _completeness(scope),
        "batch_progress": _batch_progress(rows),
        "trend": _trend_daily(rows),
    })


@router.get("/exam-options")
def exam_options(user=Depends(require_perms("analysis"))):
    rows = db.query("SELECT id,name,exam_type,exam_year,exam_month,status FROM exams"
                    " ORDER BY exam_year DESC, exam_month DESC, id DESC")
    for r in rows:
        r["label"] = f"{r['name']}（{TYPE_LABEL[r['exam_type']]}）"
    return ok(rows)


@router.get("/dimensions")
def dimensions(user=Depends(require_perms("analysis"))):
    """可选分析维度字典（前端渲染下拉）。"""
    dims = [
        {"key": "audit_status", "label": "审核状态"},
        {"key": "exam", "label": "考试批次"},
        {"key": "month", "label": "月度趋势"},
        {"key": "gender", "label": "性别"},
        {"key": "item", "label": "报考科目 / 从事职业"},
        {"key": "org", "label": "考试机构（计算机类）"},
        {"key": "site", "label": "考点（计算机类）"},
        {"key": "birth_region", "label": "出生地 省→市（普通话）"},
        {"key": "live_region", "label": "现居住地 省→市（普通话）"},
        {"key": "education", "label": "学历（计算机类）"},
        {"key": "ethnicity", "label": "民族（普通话）"},
        {"key": "class", "label": "班级"},
        {"key": "department", "label": "院系（普通话）"},
        {"key": "school", "label": "学校（计算机类）"},
    ]
    return ok({"dimensions": dims, "scope_label": scope_label(user)})
