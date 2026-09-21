# -*- coding: utf-8 -*-
"""模块7：数据看板（大屏）数据接口。

大屏数据分两层
--------------
基础层：总量、趋势、构成、排行 —— 一眼看完，每 30 秒自动刷新。
深度层：审核时效、驳回原因、批次进度、考生画像、提交时段、填报完整度
        —— 回答「卡在哪、为什么、谁最忙」，是给管理者找瓶颈用的。

性能约定
--------
大屏是**轮询**接口（默认 30 秒一次），深度指标一律用 SQL 聚合算，
不能像 /api/analysis/deep 那样把全表行读进内存。
只有驳回原因必须读原文（要做关键词归类），单独 LIMIT 截断。
所有查询都叠加当前账号的数据范围（全校 / 本院系 / 本年级 / 本班级）。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from .. import db
from ..deps import ok
from ..permissions import require_perms, scope_filter, scope_label
from .analysis import (HOUR_BUCKETS, REJECT_KEYWORDS, TABLES, _bucket_age, _bucket_hours,
                       _completeness, _reviewer_rank, build_scope, summary_of)
from .exams import TYPE_LABEL, auto_close_exams, decorate

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# 驳回原因原文最多取多少条做归类：够看趋势即可，避免大表拖慢大屏刷新
REJECT_SCAN_LIMIT = 600


def _efficiency(scope) -> dict:
    """审核时效：均值（SQL 精确）/ 中位数 / P90 / 分布桶。"""
    hours, reviewed, hour_sum = [], 0, 0.0
    for _t, tbl, clause, params in scope:
        r = db.query_one(
            f"SELECT COUNT(*) n, AVG((julianday(reviewed_at)-julianday(created_at))*24) h"
            f" FROM {tbl}{clause} AND reviewed_at<>'' AND reviewed_at IS NOT NULL"
            " AND audit_status<>'pending'", params) or {}
        n = r.get("n") or 0
        reviewed += n
        hour_sum += (r.get("h") or 0.0) * n
        # 中位/P90 需要排序，只能取明细；截断到 5 万条，超大库退化为近似值
        for x in db.query(
                f"SELECT (julianday(reviewed_at)-julianday(created_at))*24 h FROM {tbl}{clause}"
                " AND reviewed_at<>'' AND reviewed_at IS NOT NULL AND audit_status<>'pending'"
                " LIMIT 50000", params):
            if x["h"] is not None:
                hours.append(max(0.0, x["h"]))
    hours.sort()
    n = len(hours)
    buckets = {lab: 0 for _lo, _hi, lab in HOUR_BUCKETS}
    for h in hours:
        buckets[_bucket_hours(h)] = buckets.get(_bucket_hours(h), 0) + 1
    return {
        "reviewed": reviewed,
        "avg_hours": round(hour_sum / reviewed, 1) if reviewed else 0.0,
        "median_hours": round(hours[n // 2], 1) if n else 0.0,
        "p90_hours": round(hours[max(0, int(n * 0.9) - 1)], 1) if n else 0.0,
        "max_hours": round(hours[-1], 1) if n else 0.0,
        "buckets": [{"name": lab, "value": buckets.get(lab, 0)}
                    for _lo, _hi, lab in HOUR_BUCKETS],
    }


def _photo_stats(scope) -> dict:
    """证件照完整率。

    ⚠ 用 EXISTS 子查询而不是 JOIN：数据范围片段里的列名是不带表名的
    （class_name / school …），一旦 JOIN users，两边同名列会让 SQLite 报
    「ambiguous column name」。EXISTS 的子查询是独立作用域，不受影响。
    """
    total = with_photo = 0
    for _t, tbl, clause, params in scope:
        total += (db.query_one(f"SELECT COUNT(*) n FROM {tbl}{clause}", params) or {}).get("n") or 0
        r = db.query_one(
            f"SELECT COUNT(*) n FROM {tbl} p{clause}"
            " AND EXISTS (SELECT 1 FROM users u WHERE u.id=p.user_id"
            " AND u.photo<>'' AND u.photo IS NOT NULL)", params) or {}
        with_photo += r.get("n") or 0
    return {"total": total, "with_photo": with_photo, "missing": total - with_photo,
            "rate": round(with_photo / total * 100, 2) if total else 0.0}


def _trend30(scope, days: int = 30) -> list:
    """近 N 天「新增报名 / 完成审核」双线趋势。"""
    base = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    sub = {d: 0 for d in base}
    rev = {d: 0 for d in base}
    for _t, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT substr(created_at,1,10) d, COUNT(*) c FROM {tbl}{clause}"
                " AND created_at>=? GROUP BY d", params + (base[0],)):
            if r["d"] in sub:
                sub[r["d"]] += r["c"]
        for r in db.query(
                f"SELECT substr(reviewed_at,1,10) d, COUNT(*) c FROM {tbl}{clause}"
                " AND reviewed_at>=? GROUP BY d", params + (base[0],)):
            if r["d"] in rev:
                rev[r["d"]] += r["c"]
    return [{"date": d[5:], "submitted": sub[d], "reviewed": rev[d]} for d in base]


def _profile(scope) -> dict:
    """考生画像：性别构成 + 年龄分段（年龄从身份证号第 7-10 位取出生年）。"""
    now_year = datetime.now().year
    gender, age = {}, {}
    for _t, tbl, clause, params in scope:
        for r in db.query(f"SELECT gender g, COUNT(*) c FROM {tbl}{clause} GROUP BY g", params):
            g = (r["g"] or "").strip()
            if g:
                gender[g] = gender.get(g, 0) + r["c"]
        for r in db.query(
                f"SELECT substr(id_number,7,4) y, COUNT(*) c FROM {tbl}{clause}"
                " AND coalesce(id_type,'1')='1' AND length(id_number)>=10 GROUP BY y", params):
            try:
                y = int(r["y"])
            except (TypeError, ValueError):
                continue
            a = now_year - y
            if not 0 <= a <= 100:
                continue
            lab = _bucket_age(a)
            age[lab] = age.get(lab, 0) + r["c"]
    order = ["17 岁及以下", "18-19 岁", "20-22 岁", "23-25 岁", "26-30 岁", "31-40 岁", "41 岁以上"]
    return {
        "gender": [{"name": k, "value": v} for k, v in sorted(gender.items(), key=lambda x: -x[1])],
        "age": [{"name": k, "value": age[k]} for k in order if age.get(k)],
        "age_known": sum(age.values()),
    }


def _reject_reasons(scope, limit: int = 6) -> list:
    """驳回 / 退回原因归类（关键词归并，附原文样例）。"""
    counts, samples = {}, {}
    for _t, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT audit_comment c FROM {tbl}{clause}"
                f" AND audit_status IN ('rejected','returned') LIMIT {REJECT_SCAN_LIMIT}", params):
            c = (r["c"] or "").strip()
            if not c:
                key = "（未填写意见）"
            else:
                key = ""
                for label, words in REJECT_KEYWORDS:
                    if any(w in c for w in words):
                        key = label
                        break
                key = key or "其他"
                samples.setdefault(key, [])
                if len(samples[key]) < 2 and c not in samples[key]:
                    samples[key].append(c[:30])
            counts[key] = counts.get(key, 0) + 1
    return [{"name": k, "value": v, "samples": samples.get(k, [])}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])[:limit]]


def _batch_progress(scope, limit: int = 8) -> list:
    """各批次报名量与审核进度（大屏只放最靠前的几个）。"""
    exams = {e["id"]: e for e in db.query("SELECT id,name,exam_type,status FROM exams")}
    agg = {}
    for _t, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT exam_id e, audit_status s, COUNT(*) c FROM {tbl}{clause} GROUP BY e, s",
                params):
            node = agg.setdefault(r["e"], {"total": 0, "approved": 0, "pending": 0,
                                           "rejected": 0, "returned": 0})
            node["total"] += r["c"]
            if r["s"] in node:
                node[r["s"]] += r["c"]
    out = []
    for eid, n in agg.items():
        e = exams.get(eid) or {}
        reviewed = n["approved"] + n["rejected"] + n["returned"]
        out.append({
            "exam_id": eid, "name": e.get("name") or f"批次{eid}",
            "type_label": TYPE_LABEL.get(e.get("exam_type"), e.get("exam_type") or ""),
            "status": e.get("status") or "",
            "total": n["total"], "approved": n["approved"], "pending": n["pending"],
            "rejected": n["rejected"], "returned": n["returned"],
            "reviewed_rate": round(reviewed / n["total"] * 100, 1) if n["total"] else 0.0,
            "pass_rate": round(n["approved"] / reviewed * 100, 1) if reviewed else 0.0,
        })
    return sorted(out, key=lambda x: -x["total"])[:limit]


# 单位 / 院校字段在三类模板里列名不同，统一取一个口径
_ORG_SQL = {
    "computer": "coalesce(nullif(trim(school),''), nullif(trim(org_name),''), '未填写')",
    "mandarin": "coalesce(nullif(trim(employer),''), nullif(trim(department),''), '未填写')",
    "generic": "coalesce(nullif(trim(college),''), '未填写')",
}


def _org_class(scope, limit: int = 8) -> list:
    """单位 / 院系 → 班级 两级分布。"""
    tree = {}
    for t, tbl, clause, params in scope:
        org = _ORG_SQL.get(t, "'未填写'")
        for r in db.query(
                f"SELECT {org} o, coalesce(nullif(trim(class_name),''),'未填班级') c, COUNT(*) n"
                f" FROM {tbl}{clause} GROUP BY o, c", params):
            node = tree.setdefault(r["o"], {"name": r["o"], "value": 0, "children": {}})
            node["value"] += r["n"]
            node["children"][r["c"]] = node["children"].get(r["c"], 0) + r["n"]
    out = sorted(tree.values(), key=lambda x: -x["value"])[:limit]
    for n in out:
        n["children"] = [{"name": k, "value": v} for k, v in
                         sorted(n["children"].items(), key=lambda x: -x[1])[:6]]
    return out


def _hourly(scope) -> list:
    """报名提交时段分布（0-23 点），看集中提交的峰值在哪。"""
    m = {h: 0 for h in range(24)}
    for _t, tbl, clause, params in scope:
        for r in db.query(
                f"SELECT substr(created_at,12,2) h, COUNT(*) c FROM {tbl}{clause}"
                " AND created_at<>'' GROUP BY h", params):
            try:
                hh = int(r["h"])
            except (TypeError, ValueError):
                continue
            if 0 <= hh <= 23:
                m[hh] += r["c"]
    return [{"name": f"{h:02d}:00", "value": m[h]} for h in range(24)]


@router.get("")
def dashboard(user=Depends(require_perms("dashboard"))):
    auto_close_exams()
    scope = build_scope(user)
    s = summary_of(scope)

    # 近 14 天报名趋势
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    trend_map = {d: 0 for d in days}
    for _t, tbl, _c, _p in scope:
        frag, sparams = scope_filter(user, tbl)
        for r in db.query(
                "SELECT substr(created_at,1,10) d, COUNT(*) c FROM " + tbl +
                " WHERE substr(created_at,1,10)>=?" + frag, tuple([days[0]] + sparams)):
            if r["d"] in trend_map:
                trend_map[r["d"]] += r["c"]
    trend = [{"date": d, "count": trend_map[d]} for d in days]

    # 类型占比：只列出有数据的类型，否则新增的模板会占一个 0 值扇区
    type_pie = []
    for t, tbl, clause, params in scope:
        c = db.query_one(f"SELECT COUNT(*) c FROM {tbl}{clause}", params)["c"]
        if c:
            type_pie.append({"name": TYPE_LABEL[t], "value": c})

    # 地区分布（普通话出生省）
    frag, sparams = scope_filter(user, TABLES["mandarin"])
    region_map = {}
    for r in db.query("SELECT birth_province p, COUNT(*) c FROM applications_mandarin"
                      " WHERE birth_province<>''" + frag +
                      " GROUP BY p ORDER BY c DESC LIMIT 10", tuple(sparams)):
        region_map[r["p"]] = r["c"]
    region_bar = [{"name": k, "value": v} for k, v in region_map.items()]

    # 科目 / 职业排行
    item_map = {}
    frag, sparams = scope_filter(user, TABLES["computer"])
    for r in db.query("SELECT subject k, COUNT(*) c FROM applications_computer"
                      " WHERE subject<>''" + frag +
                      " GROUP BY k ORDER BY c DESC LIMIT 8", tuple(sparams)):
        item_map[r["k"]] = item_map.get(r["k"], 0) + r["c"]
    frag, sparams = scope_filter(user, TABLES["mandarin"])
    for r in db.query("SELECT occupation k, COUNT(*) c FROM applications_mandarin"
                      " WHERE occupation<>''" + frag +
                      " GROUP BY k ORDER BY c DESC LIMIT 8", tuple(sparams)):
        item_map[r["k"]] = item_map.get(r["k"], 0) + r["c"]
    item_rank = [{"name": k, "value": v} for k, v in
                 sorted(item_map.items(), key=lambda x: -x[1])[:10]]

    # 考点 / 机构 TOP
    frag, sparams = scope_filter(user, TABLES["computer"])
    site_rank = []
    for r in db.query("SELECT exam_site_code k, COUNT(*) c FROM applications_computer"
                      " WHERE exam_site_code<>''" + frag +
                      " GROUP BY k ORDER BY c DESC LIMIT 8", tuple(sparams)):
        site_rank.append({"name": r["k"], "value": r["c"]})
    org_rank = []
    for r in db.query("SELECT org_name k, COUNT(*) c FROM applications_computer"
                      " WHERE org_name<>''" + frag +
                      " GROUP BY k ORDER BY c DESC LIMIT 8", tuple(sparams)):
        org_rank.append({"name": r["k"], "value": r["c"]})

    # 审核漏斗：提交 -> 已审核 -> 通过
    funnel = [
        {"name": "提交报名", "value": s["total"]},
        {"name": "已审核", "value": s["reviewed"]},
        {"name": "审核通过", "value": s["approved"]},
    ]

    open_exams = [decorate(e) for e in
                  db.query("SELECT * FROM exams WHERE status='open' ORDER BY id DESC")]
    recent = []
    for t, tbl, clause, params in scope:
        rows = db.query(f"SELECT * FROM {tbl}{clause} ORDER BY id DESC LIMIT 8", params)
        for r in rows:
            ex = db.query_one("SELECT name FROM exams WHERE id=?", (r["exam_id"],))
            recent.append({"app_type": t, "id": r["id"], "name": r["name"],
                           "exam_name": ex["name"] if ex else "",
                           "audit_status": r["audit_status"], "created_at": r["created_at"]})
    recent.sort(key=lambda x: x["created_at"], reverse=True)

    # ------------------------------------------------------------ 深度层
    eff = _efficiency(scope)
    photo = _photo_stats(scope)

    return ok({
        "kpi": {
            "total": s["total"], "today": s["today"], "approved": s["approved"],
            "pending": s["pending"], "rejected": s["rejected"], "returned": s["returned"],
            "pass_rate": s["pass_rate"], "open_exam_count": len(open_exams),
            "reviewed_rate": round(s["reviewed"] / s["total"] * 100, 2) if s["total"] else 0.0,
            "reject_rate": s["reject_rate"],
            "avg_hours": eff["avg_hours"], "photo_rate": photo["rate"],
        },
        "open_exams": open_exams,
        "trend": trend,
        "trend30": _trend30(scope),
        "type_pie": type_pie,
        "region_bar": region_bar,
        "item_rank": item_rank,
        "site_rank": site_rank,
        "org_rank": org_rank,
        "funnel": funnel,
        "recent": recent[:8],
        # 深度层
        "efficiency": eff,
        "photo": photo,
        "profile": _profile(scope),
        "reject_reasons": _reject_reasons(scope),
        "batch_progress": _batch_progress(scope),
        "org_class": _org_class(scope),
        "reviewer_rank": _reviewer_rank(scope)[:6],
        "hourly": _hourly(scope),
        "completeness": _completeness(scope)[:6],
        "scope_label": scope_label(user),
        "updated_at": db.now_str(),
    })
