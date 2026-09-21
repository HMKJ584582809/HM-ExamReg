# -*- coding: utf-8 -*-
"""专项验证：账号被「单独授权」后，数据范围 scope_* 不应退化。

背景
----
单独授权把功能权限写进 users.perms（只存功能组，不含 scope_*）。
若 has_perm() 对 scope_* 也走 effective_perms()，则被单独授权过的管理员/审核员
会被误判为「无 scope_all」，后果：
  - analysis.py summary() 把 scope 降级为 "class"；
  - applications.py 列表/统计按班级范围收敛，看到的数据变少。

校验点
------
1. 未单独授权时：审核员可见全校数据、分析 scope=all、可导出；
2. 单独授权为「仅保留 信息审核」后：
   - 数据范围仍为 scope_all（核心回归点）
   - 分析 scope 仍为 all、报名列表可见总量不变
   - 功能权限确实收敛（导出 / 分析 返回 403，列表仍可访问）
3. 恢复角色默认后 custom_perms 归位为 False。
"""
import json
import random
import sys
import urllib.request
import urllib.error

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8906"
# 被测账号的角色：权限组细化后脚本不再写死权限清单，改这个即可
ROLE = "reviewer"
PASS, FAIL = [], []


def call(method, path, token=None, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            # 导出接口返回 xlsx 文件流，不是 JSON
            if (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, {"_binary": True, "size": len(raw)}
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return (r["data"]["access_token"] if st == 200 and r.get("data") else None), r


def main():
    print("=" * 70)
    print(f"数据范围不退化专项校验 · {BASE}\n")
    print("[0] 准备")
    admin, r = login("admin", "Admin@123")
    check("管理员登录成功", bool(admin), r)
    if not admin:
        return 1

    # 找一个有报名数据的批次
    st, r = call("GET", "/api/exams?page_size=50", token=admin)
    exam_id = 0
    for e in r["data"]["list"]:
        st2, a = call("GET", f"/api/applications?exam_id={e['id']}&page_size=1", token=admin)
        if st2 == 200 and a["data"]["total"] > 0:
            exam_id = e["id"]
            break
    check("存在有报名数据的考试批次", exam_id > 0, "需先运行带演示数据的服务（EXAM_SEED_DEMO=1）")
    if not exam_id:
        return 1

    uname = f"scopechk{random.randint(10000, 99999)}"
    st, r = call("POST", "/api/users", token=admin, body={
        "username": uname, "real_name": "范围校验员", "role": ROLE,
        "password": "Scope@1234", "phone": f"137{random.randint(10000000, 99999999)}",
    })
    check("创建审核员账号", st == 200 and r.get("code") == 0, r)
    if st != 200:
        return 1
    uid = r["data"]["id"] if isinstance(r.get("data"), dict) and r["data"].get("id") else None
    if not uid:
        st, r = call("GET", "/api/users?keyword=" + uname, token=admin)
        uid = r["data"]["list"][0]["id"]

    tok, r = login(uname, "Scope@1234")
    check("审核员登录成功", bool(tok), r)
    if not tok:
        return 1

    print("\n[1] 未单独授权（沿用角色默认）")
    st, r = call("GET", f"/api/applications?exam_id={exam_id}&page_size=1", token=tok)
    total_before = r["data"]["total"] if st == 200 and r.get("data") else -1
    check("审核员可见全校报名数据", st == 200 and total_before > 0, r)

    st, r = call("GET", f"/api/analysis/summary?exam_id={exam_id}", token=tok)
    check("分析接口 scope=all（全校）", st == 200 and r["data"].get("scope") == "all", r)
    check("分析接口范围标签为「全部数据」",
          r["data"].get("scope_label") == "全部数据", r["data"].get("scope_label"))

    st, r = call("POST", "/api/export", token=tok, body={"exam_id": exam_id})
    check("审核员默认可导出", st == 200, r)

    print("\n[2] 单独授权：功能权限收敛为「信息审核 + 数据分析」（关掉汇总导出）")
    st, r = call("PUT", f"/api/users/{uid}/perms", token=admin, body={"perms": ["audit", "analysis"]})
    check("单独授权保存成功", st == 200 and r.get("code") == 0, r)

    st, rv = call("GET", "/api/users/verify", token=admin)
    row = next((x for x in rv["data"]["list"] if x["id"] == uid), {}) if st == 200 else {}
    check("后台核验：数据范围仍为 scope_all（核心回归点）",
          row.get("scope") == "scope_all", row)
    check("后台核验：范围标签仍为「全部数据」",
          row.get("scope_label") == "全部数据", row.get("scope_label"))
    check("后台核验：custom_perms 标记为 True", row.get("custom_perms") is True, row)
    check("后台核验：功能权限已收敛（不含 export）",
          row.get("perms") == ["audit", "analysis"], row.get("perms"))
    check("后台核验：范围类无缺失告警", not row.get("issues"), row.get("issues"))

    st, r = call("GET", f"/api/analysis/summary?exam_id={exam_id}", token=tok)
    check("单独授权后分析 scope 仍为 all（未退化成 class）",
          st == 200 and r["data"].get("scope") == "all", r)

    st, r = call("GET", f"/api/applications?exam_id={exam_id}&page_size=1", token=tok)
    total_after = r["data"]["total"] if st == 200 and r.get("data") else -1
    check("单独授权后可见数据总量不变",
          total_after == total_before and total_after > 0,
          f"before={total_before} after={total_after}")
    check("单独授权后列表 can_audit 仍为 True",
          r["data"].get("can_audit") is True, r["data"].get("can_audit"))

    print("\n[3] 功能开关确实生效（不能因范围修复而失效）")
    st, r = call("POST", "/api/export", token=tok, body={"exam_id": exam_id})
    check("已关闭 export：导出接口 403", st == 403, r)
    st, r = call("GET", f"/api/applications?exam_id={exam_id}&page_size=1", token=tok)
    check("仍保留 audit：报名列表可访问", st == 200, r)

    # 再关掉 analysis，确认功能权限确实能逐个关停
    st, r = call("PUT", f"/api/users/{uid}/perms", token=admin, body={"perms": ["audit"]})
    check("二次收敛为仅 audit", st == 200 and r.get("code") == 0, r)
    st, r = call("GET", f"/api/analysis/summary?exam_id={exam_id}", token=tok)
    check("已关闭 analysis：分析接口 403", st == 403, r)
    st, r = call("GET", f"/api/applications?exam_id={exam_id}&page_size=1", token=tok)
    check("仅保留 audit 时列表仍可访问且范围不变",
          st == 200 and r["data"]["total"] == total_before, r["data"].get("total") if st == 200 else r)

    print("\n[4] 恢复角色默认")
    # 角色默认权限组会随权限细化变化（审核员现在是 7 个功能组），
    # 这里从 /api/users/options 现取，不再写死 —— 否则细化一次就要改一次脚本。
    st, opts = call("GET", "/api/users/options", token=admin)
    role_perms = []
    if st == 200:
        for o in (opts.get("data") or []):
            if o.get("key") == ROLE:
                role_perms = sorted(p for p in (o.get("perms") or [])
                                    if not str(p).startswith("scope_"))
    check("取到角色默认权限组", bool(role_perms), role_perms)
    st, r = call("PUT", f"/api/users/{uid}/perms", token=admin,
                 body={"perms": role_perms})
    check("恢复角色默认权限成功", st == 200 and r.get("code") == 0, r)
    st, rv = call("GET", "/api/users/verify", token=admin)
    row = next((x for x in rv["data"]["list"] if x["id"] == uid), {}) if st == 200 else {}
    check("恢复后 custom_perms 归位为 False", row.get("custom_perms") is False, row)
    st, r = call("POST", "/api/export", token=tok, body={"exam_id": exam_id})
    check("恢复后可再次导出", st == 200, r)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
