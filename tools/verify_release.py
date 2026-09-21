# -*- coding: utf-8 -*-
"""
发布版「零测试数据」校验
--------------------------------
对指定地址运行中的服务做验收：确认正式版首次启动后
  * 只存在 1 个管理员账号（admin），且不含任何演示/测试账号
  * 考试批次、报名数据、导入批次、导出记录均为空
  * 权限组字典完整（11 个权限组 / 5 个角色）

用法：
  python tools/verify_release.py --base http://127.0.0.1:8799
"""
import argparse
import json
import sys
import urllib.request
import urllib.error

PASS = 0
FAIL = 0
BASE = "http://127.0.0.1:8799"


def req(path, method="GET", body=None, token=None):
    url = BASE.rstrip("/") + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"code": e.code, "message": str(e), "data": None}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  -> " + str(extra) if extra else ""))


def main():
    global BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8799")
    ap.add_argument("--user", default="admin")
    ap.add_argument("--password", default="Admin@123")
    args = ap.parse_args()
    BASE = args.base

    print("=" * 66)
    print("发布版「零测试数据」校验 · " + BASE)
    print("=" * 66)

    # 1. 健康检查
    print("\n[1] 服务与版本")
    h = req("/api/health")
    check("服务在线", h.get("code") == 0, h)
    check("版本为 1.1.0", (h.get("data") or {}).get("version") == "1.1.0",
          (h.get("data") or {}).get("version"))

    # 2. 登录
    print("\n[2] 初始管理员登录")
    lg = req("/api/auth/login", "POST",
             {"account": args.user, "password": args.password, "remember": True})
    check("admin 可登录", lg.get("code") == 0, lg.get("message"))
    token = (lg.get("data") or {}).get("access_token")
    check("返回 access_token", bool(token))
    user = (lg.get("data") or {}).get("user") or {}
    check("角色为 admin", user.get("role") == "admin", user.get("role"))
    perms = user.get("perms") or []
    check("权限组含 user_manage", "user_manage" in perms, perms)
    check("权限组含 import", "import" in perms, perms)
    check("数据范围为全校", user.get("scope") == "scope_all", user.get("scope"))

    # 3. 账号零残留
    print("\n[3] 账号零残留（仅 admin）")
    us = req("/api/users?page=1&page_size=200", token=token)
    check("用户列表可访问", us.get("code") == 0, us.get("message"))
    items = ((us.get("data") or {}).get("list")) or []
    total = ((us.get("data") or {}).get("total"))
    check("用户总数为 1", total == 1, "total=%s" % total)
    names = sorted([i.get("username") for i in items])
    check("唯一账号为 admin", names == ["admin"], names)
    bad = [n for n in names if n in
           ("reviewer", "reviewer2", "teacher", "teacher2", "candidate",
            "stu101", "stu102")]
    check("无任何演示账号", not bad, bad)

    # 4. 业务数据零残留
    print("\n[4] 业务数据零残留")
    ex = req("/api/exams?page=1&page_size=50", token=token)
    check("考试批次可访问", ex.get("code") == 0, ex.get("message"))
    ex_items = ((ex.get("data") or {}).get("list")) or []
    check("考试批次为空", len(ex_items) == 0, "count=%d" % len(ex_items))

    apps = req("/api/applications?page=1&page_size=50", token=token)
    ap_total = ((apps.get("data") or {}).get("total"))
    check("报名数据为空", ap_total in (0, None), "total=%s" % ap_total)

    ib = req("/api/applications/import-batches?page=1&page_size=50", token=token)
    ib_items = ((ib.get("data") or {}).get("list")) or []
    check("导入批次为空", len(ib_items) == 0, "count=%d" % len(ib_items))

    info = req("/api/system/info", token=token)
    check("系统信息可访问", info.get("code") == 0, info.get("message"))
    idata = info.get("data") or {}
    check("演示数据开关为关闭", idata.get("seed_demo") is False, idata.get("seed_demo"))
    scale = idata.get("counts") or {}
    print("       数据规模: " + json.dumps(scale, ensure_ascii=False))
    for k in ("applications", "exams", "users", "import_batches"):
        if k in scale:
            expect = 1 if k == "users" else 0
            check("规模 %s 为 %d" % (k, expect), (scale.get(k) or 0) == expect, scale.get(k))

    # 5. 权限组字典
    print("\n[5] 权限组字典")
    pg = req("/api/users/permission-groups", token=token)
    check("权限组字典可访问", pg.get("code") == 0, pg.get("message"))
    pdata = pg.get("data") or {}
    groups = pdata.get("groups") or []
    roles = pdata.get("roles") or []
    check("权限组数量 >= 10", len(groups) >= 10, "len=%d" % len(groups))
    check("角色数量为 5", len(roles) == 5, "len=%d" % len(roles))
    role_keys = [r.get("key") for r in roles]
    for r in ("admin", "head_teacher", "college_reviewer", "reviewer", "candidate"):
        check("角色存在: " + r, r in role_keys, role_keys)
    manage = [r.get("key") for r in roles if r.get("manage_level")]
    check("管理权限级别含 admin/head_teacher",
          set(manage) == {"admin", "head_teacher"}, manage)

    # 6. 权限组核验接口
    print("\n[6] 后台权限组核验")
    vf = req("/api/users/verify", token=token)
    check("核验接口可访问", vf.get("code") == 0, vf.get("message"))
    vitems = (vf.get("data") or {}).get("list") or []
    check("核验返回 1 条（仅 admin）", len(vitems) == 1, "len=%d" % len(vitems))
    check("核验无权限组异常", (vf.get("data") or {}).get("issue_count") == 0,
          (vf.get("data") or {}).get("issue_count"))

    print("\n" + "=" * 66)
    print("结果：%d 项通过，%d 项失败" % (PASS, FAIL))
    print("=" * 66)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
