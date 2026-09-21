# -*- coding: utf-8 -*-
"""二级学院审核角色 + 数据范围档位（本年级 / 本院系）专项验证。

覆盖：
  1. 新角色 college_reviewer：权限组、数据范围、只能看本院系、越权功能被拦
  2. 逐账号设定数据范围：可收窄，但**不得超过角色上限**（防提权）
  3. 本年级 / 本院系两档过滤是否真的生效
  4. 老角色（班主任 / 审核员）范围未受影响

用法：
    python tools/verify_college_scope.py http://127.0.0.1:8913
"""
import json
import random
import sys
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8913"
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
            try:
                return r.status, json.loads(raw.decode("utf-8"))
            except Exception:
                # 导出/下载类接口返回的是文件流（xlsx 二进制），不是 JSON
                return r.status, {"_bytes": len(raw)}
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def multipart(path, token, parts):
    """parts: [(field, filename, content, ctype)]；filename 为 None 时按普通字段处理。"""
    import io as _io
    boundary = "----wb" + uuid.uuid4().hex
    buf = _io.BytesIO()
    for field, filename, content, ctype in parts:
        if filename is None:
            buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n'
                      .encode("utf-8"))
            buf.write(str(content).encode("utf-8") + b"\r\n")
        else:
            buf.write(f'--{boundary}\r\nContent-Disposition: form-data; name="{field}";'
                      f' filename="{filename}"\r\nContent-Type: {ctype}\r\n\r\n'.encode("utf-8"))
            buf.write(content + b"\r\n")
    buf.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(BASE + path, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            if (r.headers.get("Content-Type") or "").find("json") < 0:
                return r.status, {"_binary": True, "size": len(raw)}
            return r.status, json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        rawb = e.read()
        try:
            return e.code, json.loads(rawb.decode("utf-8"))
        except Exception:
            return e.code, {"raw": rawb[:200].decode("utf-8", "ignore")}


# 合法身份证号（6 地区 + 8 生日 + 3 顺序 + 1 校验位）
_ID_W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
_ID_C = ["1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2"]


def mk_id(seq):
    base = "320102" + "20030101" + f"{seq:03d}"
    s = sum(int(base[i]) * _ID_W[i] for i in range(17))
    return base + _ID_C[s % 11]


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + ("" if cond else f"  -> {extra}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return (r["data"]["access_token"] if st == 200 and r.get("data") else None), r


def mkuser(admin, role, college="", grade="", classes="", pwd="Scope@1234"):
    body = {"username": f"{role[:6]}{random.randint(10000, 99999)}", "real_name": "范围测试",
            "role": role, "password": pwd,
            "phone": f"137{random.randint(10000000, 99999999)}"}
    if college:
        body["college"] = college
    if grade:
        body["grade"] = grade
    if classes:
        body["classes"] = classes
    st, r = call("POST", "/api/users", token=admin, body=body)
    return st, r, body.get("username")


def main():
    print("=" * 70)
    print(f"二级学院审核与数据范围专项验证 · {BASE}\n")

    print("[0] 准备")
    admin, r = login("admin", "Admin@123")
    check("管理员登录成功", bool(admin), r)
    if not admin:
        return 1
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=admin)
    all_total = r["data"]["total"] if st == 200 else 0
    check("管理员可见全部计算机类报名", all_total > 0, r)
    # 取一个人数最多的院系，便于断言「只能看本院系」
    st, r = call("GET", "/api/applications?app_type=computer&page_size=200", token=admin)
    schools = {}
    for it in r["data"]["list"]:
        schools[it.get("school") or ""] = schools.get(it.get("school") or "", 0) + 1
    college = max(schools, key=schools.get) if schools else ""
    expect = schools.get(college, 0)
    check(f"演示数据含多个院系（取最多的「{college}」共 {expect} 条）",
          len(schools) > 1 and expect > 0, schools)

    print("\n[1] 二级学院审核角色")
    st, r, _ = mkuser(admin, "college_reviewer")
    check("未绑定院系的二级学院审核账号被拒", st == 400, r)

    st, r, uname = mkuser(admin, "college_reviewer", college=college)
    check("绑定院系后可创建", st == 200 and r.get("code") == 0, r)
    if st != 200:
        return 1
    cid = r["data"].get("id") if isinstance(r.get("data"), dict) else None
    if not cid:
        st2, r2 = call("GET", f"/api/users?keyword={uname}", token=admin)
        cid = r2["data"]["list"][0]["id"]

    tok, r = login(uname, "Scope@1234")
    check("二级学院审核账号可登录", bool(tok), r)
    st, r = call("GET", "/api/auth/me", token=tok)
    d = r.get("data", {}) if st == 200 else {}
    perms = d.get("perms", [])
    check("权限组含 审核/导出/分析",
          all(p in perms for p in ("audit", "export", "analysis")), perms)
    check("不含 批量导入（非管理权限级别）", "import" not in perms, perms)
    check("数据范围为本院系", d.get("scope") == "scope_college", d.get("scope"))
    check("范围标签显示院系名",
          college in (d.get("scope_label") or ""), d.get("scope_label"))

    st, r = call("GET", "/api/applications?app_type=computer&page_size=200", token=tok)
    got = r["data"]["total"] if st == 200 else -1
    check("只能看到本院系的报名（数量收敛）",
          st == 200 and got == expect and got < all_total,
          f"本院系={got} 期望={expect} 全校={all_total}")
    rows = r["data"]["list"] if st == 200 else []
    check("返回的每一行都是本院系",
          rows and all((x.get("school") or "") == college for x in rows),
          {x.get("school") for x in rows})

    # 导出按考试批次进行，先取一个计算机类批次
    st, r = call("GET", "/api/exams?exam_type=computer", token=tok)
    exams = (r.get("data") or {}).get("list", []) if st == 200 else []
    exam_id = exams[0]["id"] if exams else 0
    check("可取到计算机类考试批次", exam_id > 0, r)
    st, r = call("POST", "/api/export", token=tok,
                 body={"exam_id": exam_id, "app_type": "computer", "audit_status": ""})
    check("有导出权限（可导出）", st == 200, r)
    st, r = call("POST", "/api/import/preview", token=tok, body={})
    check("无批量导入权限（被拦）", st in (400, 403, 405, 422), r)

    # 越权审核：拿一条「别的院系」的记录，二级学院审核必须审核不了
    st, r = call("GET", "/api/applications?app_type=computer&page_size=200", token=admin)
    other = next((x for x in r["data"]["list"]
                  if (x.get("school") or "") != college and x.get("audit_status") == "pending"),
                 None) if st == 200 else None
    check("找到一条其它院系的待审记录", other is not None,
          [x.get("school") for x in r["data"]["list"]][:6] if st == 200 else r)
    if other:
        st2, r2 = call("PUT", f"/api/applications/{other['id']}/audit", token=tok,
                       body={"app_type": "computer", "action": "approved", "comment": ""})
        check("审核其它院系的记录被拒（范围守卫）", st2 in (403, 404), f"{st2} {r2}")
        st3, r3 = call("GET", "/api/applications?app_type=computer&page_size=200", token=admin)
        row = next((x for x in r3["data"]["list"] if x["id"] == other["id"]), None)
        check("被越权审核的记录状态未被改动",
              row and row.get("audit_status") == "pending", row and row.get("audit_status"))
    # 本院系的记录则应当能正常审核
    mine = next((x for x in r["data"]["list"]
                 if (x.get("school") or "") == college and x.get("audit_status") == "pending"),
                None) if st == 200 else None
    if mine:
        st4, r4 = call("PUT", f"/api/applications/{mine['id']}/audit", token=tok,
                       body={"app_type": "computer", "action": "approved", "comment": ""})
        check("本院系记录可以正常审核", st4 == 200, r4)

    print("\n[2] 逐账号设定范围：可收窄，不得越权")
    st, r = call("PUT", f"/api/users/{cid}/perms", token=admin,
                 body={"perms": ["audit", "export", "analysis"], "scope": "scope_all"})
    check("把二级学院审核的范围提到「全校」被拒（防提权）", st == 400, r)

    st, r, tname = mkuser(admin, "head_teacher", college=college, classes="计科1班")
    check("创建班主任成功", st == 200, r)
    tid = r["data"].get("id") if st == 200 and isinstance(r.get("data"), dict) else None
    if not tid:
        st2, r2 = call("GET", f"/api/users?keyword={tname}", token=admin)
        tid = r2["data"]["list"][0]["id"]
    st, r = call("PUT", f"/api/users/{tid}/perms", token=admin,
                 body={"perms": ["apply", "import", "export", "analysis"],
                       "scope": "scope_college"})
    check("把班主任的范围提到「本院系」被拒（超过本班级上限）", st == 400, r)

    st, r = call("PUT", f"/api/users/{tid}/perms", token=admin,
                 body={"perms": ["apply", "import", "export", "analysis"],
                       "scope": "scope_unknown"})
    check("非法范围值被拒", st == 400, r)

    # 管理员自己不能改自己（既有规则）
    st, r = call("GET", "/api/auth/me", token=admin)
    admin_id = r["data"]["id"]
    st, r = call("PUT", f"/api/users/{admin_id}/perms", token=admin,
                 body={"perms": ["audit"], "scope": "scope_class"})
    check("管理员不能修改自己的权限", st == 400, r)

    # 给审核员收窄为本班级：应生效
    st, r, rname = mkuser(admin, "reviewer")
    check("创建审核员成功", st == 200, r)
    rid = r["data"].get("id") if st == 200 and isinstance(r.get("data"), dict) else None
    if not rid:
        st2, r2 = call("GET", f"/api/users?keyword={rname}", token=admin)
        rid = r2["data"]["list"][0]["id"]
    rev, r = login(rname, "Scope@1234")
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=rev)
    before = r["data"]["total"] if st == 200 else -1
    check("审核员默认可见全校", before == all_total, f"{before} vs {all_total}")

    st, r = call("PUT", f"/api/users/{rid}/perms", token=admin,
                 body={"perms": ["audit", "export", "analysis"], "scope": "scope_class"})
    check("把审核员收窄为「本班级」成功（未超上限）", st == 200 and r.get("code") == 0, r)
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=rev)
    after = r["data"]["total"] if st == 200 else -1
    check("收窄后可见数据变少（范围立即生效）", st == 200 and after < before,
          f"收窄前={before} 收窄后={after}")

    st, r = call("PUT", f"/api/users/{rid}/perms", token=admin,
                 body={"perms": ["audit", "export", "analysis"], "scope": ""})
    check("清空范围即恢复角色默认", st == 200 and r.get("code") == 0, r)
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=rev)
    check("恢复后重新可见全校",
          st == 200 and r["data"]["total"] == all_total, r.get("data", {}).get("total"))

    print("\n[3] 本年级档位（报名表无年级字段，靠关联 users 取同年级班级）")
    st, r = call("PUT", f"/api/users/{rid}/perms", token=admin,
                 body={"perms": ["audit", "export", "analysis"], "scope": "scope_grade"})
    check("可把审核员范围设为「本年级」", st == 200, r)
    call("PUT", f"/api/users/{rid}", token=admin, body={"grade": "2099级"})
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=rev)
    check("年级填了不存在的值 → 看不到任何数据（过滤确实生效）",
          st == 200 and r["data"]["total"] == 0, r.get("data", {}).get("total"))
    call("PUT", f"/api/users/{rid}", token=admin, body={"grade": "2022级"})
    st, r = call("GET", "/api/applications?app_type=computer&page_size=1", token=rev)
    check("年级与演示数据一致 → 可见该年级数据",
          st == 200 and r["data"]["total"] > 0, r.get("data", {}).get("total"))
    call("PUT", f"/api/users/{rid}/perms", token=admin,
         body={"perms": ["audit", "export", "analysis"], "scope": ""})

    print("\n[4] 老角色未受影响")
    st, r = call("GET", "/api/users/verify", token=admin)
    rows = r["data"]["list"] if st == 200 else []
    by_id = {x["id"]: x for x in rows}
    check("班主任仍为本班级", by_id.get(tid, {}).get("scope") == "scope_class",
          by_id.get(tid, {}).get("scope"))
    check("二级学院审核核验无告警", not by_id.get(cid, {}).get("issues"),
          by_id.get(cid, {}).get("issues"))
    check("核验接口覆盖到新角色",
          any(x["role"] == "college_reviewer" for x in rows),
          [x["role"] for x in rows][:8])

    print("\n[5] 本年级覆盖批量导入的考生（导入表带「年级」列）")
    # 报名表没有年级列，年级只能落在考生账号上。导入时若表里有「年级」列
    # （或用整批默认值指定），就该写进账号，否则「本年级」范围会漏掉导入生。
    import io as _io
    import openpyxl

    st, r = call("POST", "/api/exams", token=admin, body={
        "exam_type": "computer", "exam_year": 2026, "exam_month": 9,
        "name": "GRADE-TEST", "signup_start_at": "2026-09-01 00:00",
        "signup_end_at": "2026-12-31 23:59", "status": "draft"})
    check("创建临时批次", st == 200 and r.get("code") == 0, r.get("message"))
    gid = (r.get("data") or {}).get("id") or 0

    g1, g2 = mk_id(201), mk_id(202)
    wb = openpyxl.Workbook()
    ws = wb.active
    # 注意含「年级」列：这是让本年级范围生效的关键
    ws.append(["姓名", "性别", "证件类型", "证件号码", "报考科目", "就读或毕业院校",
               "班级", "年级", "学历", "手机号码"])
    ws.append(["年级甲", "男", "1", g1, "信息处理工程师技术水平",
               "计算机学院", "年级甲班", "2098级", "本科", "13900002001"])
    ws.append(["年级乙", "女", "1", g2, "信息处理工程师技术水平",
               "教育学院", "年级乙班", "2099级", "本科", "13900002002"])
    buf = _io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    st, r = multipart("/api/applications/import", admin, [
        ("exam_id", None, gid, None),
        ("audit_status", None, "pending", None),
        ("create_accounts", None, 1, None),
        ("defaults", None, json.dumps({"org_code": "1001", "exam_site_code": "100101"}), None),
        ("file", "grade.xlsx", buf.getvalue(),
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ])
    data = r.get("data") or {}
    check("导入带年级列的 2 条报名", st == 200 and data.get("success") == 2,
          data.get("errors") or r.get("message"))

    if data.get("success") == 2:
        # 把审核员的范围设为「本年级」并填 2098 级
        st, r = call("PUT", f"/api/users/{rid}", token=admin, body={"grade": "2098级"})
        check("给审核员填年级成功", st == 200, r.get("message"))
        st, r = call("PUT", f"/api/users/{rid}/perms", token=admin,
                     body={"perms": ["audit", "export", "analysis"], "scope": "scope_grade"})
        check("范围设为本年级", st == 200, r.get("message"))
        st, r = call("GET", f"/api/applications?exam_id={gid}&page_size=50", token=rev)
        rows = (r.get("data") or {}).get("list") or []
        check("本年级只看到 2098 级那 1 条",
              st == 200 and len(rows) == 1, [x.get("name") for x in rows])
        check("看到的正是 2098 级考生",
              rows and rows[0].get("name") == "年级甲", rows and rows[0].get("name"))
        # 换成 2099 级应换到另一条
        st, r = call("PUT", f"/api/users/{rid}", token=admin, body={"grade": "2099级"})
        st, r = call("GET", f"/api/applications?exam_id={gid}&page_size=50", token=rev)
        rows2 = (r.get("data") or {}).get("list") or []
        check("换年级后看到 2099 级那 1 条",
              len(rows2) == 1 and rows2[0].get("name") == "年级乙",
              [x.get("name") for x in rows2])
        call("PUT", f"/api/users/{rid}/perms", token=admin,
             body={"perms": ["audit", "export", "analysis"], "scope": ""})

    # 清理
    call("POST", "/api/system/reset", token=admin,
         body={"scopes": ["applications"], "confirm": "确认清空"})
    call("DELETE", f"/api/exams/{gid}", token=admin)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for f in FAIL:
            print("  FAILED:", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
