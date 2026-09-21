# -*- coding: utf-8 -*-
"""权限体系细化专项验证。

背景（本轮重构发现的问题）
--------------------------
1. **功能「借」门**：字典维护挂在 exam_manage 下 —— 能建考试批次就等于能改
   全校字典；系统维护挂在 user_manage 下 —— 开了用户管理就能清库。
2. **模块无门**：证件照制作、实名认证、AI 助手三个模块对所有登录用户开放，
   没有任何权限组，管理员无法单独关掉某个人的这些能力。
3. **账号维度缺范围校验**：批量导入证件照只看「有没有 import 权限组」，
   班主任可以按文件名给全校任何人换照片。
4. **实名审核只能管理员做**：二级学院审核员审不了本院系的实名申请。

细化后：新增 dict_manage / system_manage / idphoto / realname / realname_audit / ai
六个权限组，并给账号维度的操作补上 can_access_user 范围校验。

用法：
    python tools/verify_perms_refine.py http://127.0.0.1:8944
需**全新库**（EXAM_DEV=1 EXAM_SEED_DEMO=1）。
"""
import json
import random
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8944"
PASS, FAIL = [], []
SUF = str(random.randint(10000, 99999))


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  -> " + str(extra)) if extra and not cond else ""))


def req(method, path, body=None, token=None):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            env = json.loads(resp.read().decode("utf-8"))
            return resp.status, (env.get("data") if env.get("code") == 0 else env)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "ignore")}


def login(acct, pwd):
    st, d = req("POST", "/api/auth/login", {"account": acct, "password": pwd})
    assert st == 200, (st, d)
    return d["access_token"], d["user"]


print("=" * 60)
print("权限体系细化专项验证  base=%s" % BASE)
print("=" * 60)

tk, admin = login("admin", "Admin@123")

print("\n--- 1. 权限组字典 ---")
st, d = req("GET", "/api/users/permission-groups", token=tk)
keys = [g["key"] for g in (d.get("groups") or [])]
acts = [g["key"] for g in (d.get("groups") or []) if g.get("kind") == "action"]
for g in ("dict_manage", "system_manage", "idphoto", "realname", "realname_audit", "ai"):
    chk("新增权限组：" + g, g in keys, keys)
chk("每个功能组都有说明", all((g.get("desc") or "") for g in (d.get("groups") or [])
                          if g.get("kind") == "action"),
    [g["key"] for g in (d.get("groups") or []) if g.get("kind") == "action" and not g.get("desc")])
roles = {r["key"]: r["perms"] for r in (d.get("roles") or [])}
chk("考生可用证件照", "idphoto" in roles.get("candidate", []), roles.get("candidate"))
chk("考生可用实名", "realname" in roles.get("candidate", []), roles.get("candidate"))
chk("二级学院审核可审实名", "realname_audit" in roles.get("college_reviewer", []),
    roles.get("college_reviewer"))
chk("二级学院审核有大屏", "dashboard" in roles.get("college_reviewer", []), roles.get("college_reviewer"))
chk("班主任不能审报名", "audit" not in roles.get("head_teacher", []), roles.get("head_teacher"))

print("\n--- 2. 管理员权限组齐全 ---")
chk("admin 有全部 14 个功能组", all(a in admin.get("perms", []) for a in acts),
    [a for a in acts if a not in admin.get("perms", [])])
chk("登录下发 perm_detail", isinstance(admin.get("perm_detail"), list)
    and len(admin.get("perm_detail") or []) > 0, admin.get("perm_detail"))

print("\n--- 3. 模块门控（关掉权限组后应 403） ---")
# 用 demo 账号测：EXAM_SEED_DEMO=1 已造好各角色账号
st, d = req("GET", "/api/users/stats", token=tk)
print("  账号概况：%s" % (d,))

# 演示账号（EXAM_SEED_DEMO=1 播种）：覆盖审核员 / 二级学院审核 / 班主任 / 考生
DEMO = [("teacher", "Teacher@123"), ("teacher2", "Teacher@123"),
        ("college", "College@123"), ("reviewer", "Reviewer@123"),
        ("candidate", "Candidate@123")]
found = {}
for acct, pwd in DEMO:
    st, u = req("POST", "/api/auth/login", {"account": acct, "password": pwd})
    if st == 200:
        found[acct] = (u["access_token"], u["user"])
print("  可登录演示账号：%s" % list(found.keys()))

# 逐个角色验证模块可用性（能进说明权限组生效）
def can_use(tk_, method, path, body=None):
    st, _ = req(method, path, body, token=tk_)
    return st

for acct, (t, u) in found.items():
    role = u.get("role")
    perms = u.get("perms") or []
    st_id = can_use(t, "GET", "/api/idphoto/specs")
    chk("%s(%s) 证件照 200/403 与 idphoto 一致" % (acct, role),
        (st_id == 200) == ("idphoto" in perms), (st_id, perms))
    st_rn = can_use(t, "GET", "/api/realname/me")
    chk("%s(%s) 实名 200/403 与 realname 一致" % (acct, role),
        (st_rn == 200) == ("realname" in perms), (st_rn, perms))
    st_ai = can_use(t, "GET", "/api/ai/suggestions")
    chk("%s(%s) AI 200/403 与 ai 一致" % (acct, role),
        (st_ai == 200) == ("ai" in perms), (st_ai, perms))
    st_dc = can_use(t, "GET", "/api/dicts/manage/college")
    chk("%s(%s) 字典维护 200/403 与 dict_manage 一致" % (acct, role),
        (st_dc == 200) == ("dict_manage" in perms), (st_dc, perms))
    st_sy = can_use(t, "GET", "/api/system/info")
    chk("%s(%s) 系统维护 200/403 与 system_manage 一致" % (acct, role),
        (st_sy == 200) == ("system_manage" in perms), (st_sy, perms))

print("\n--- 4. 单独关掉某人的证件照 / AI 后确实不能用 ---")
# 新建账号 → 关掉 idphoto 与 ai → 验证 403
st, cap = req("GET", "/api/auth/captcha")
code = cap.get("debug_code") or "0000"
uname = "p11_" + SUF
st, d = req("POST", "/api/auth/register", {
    "username": uname, "phone": "1370000" + SUF[:4], "email": "",
    "password": "Abcd12345", "confirm_password": "Abcd12345",
    "verify_type": "captcha", "captcha_id": cap["captcha_id"], "captcha_code": code})
if st != 200:
    chk("创建测试账号", False, (st, d))
else:
    tkc, uc = login(uname, "Abcd12345")
    uid = uc["id"]
    chk("考生默认有 idphoto", "idphoto" in (uc.get("perms") or []), uc.get("perms"))
    st, _ = req("GET", "/api/idphoto/specs", token=tkc)
    chk("关闭前证件照可用", st == 200, st)
    # 关掉 idphoto（保留其它），应清除单独授权以外的项
    keep = [p for p in (uc.get("perms") or []) if p != "idphoto"]
    st, d = req("PUT", "/api/users/%s/perms" % uid, {"perms": keep}, token=tk)
    chk("管理员关闭该账号 idphoto", st == 200, (st, d))
    st, d = req("GET", "/api/idphoto/specs", token=tkc)
    chk("该账号证件照 403", st == 403, (st, d))

print("\n--- 5. 实名审核的范围收敛 ---")
# 二级学院审核员看实名列表，不应看到别的院系
if "college" in found:
    t, u = found["college"]
    st, d = req("GET", "/api/realname/list", token=t)
    chk("二级学院审核可看实名列表", st == 200, (st, d))
    if st == 200:
        rows = d.get("list") or []
        col = (u.get("college") or "").strip()
        chk("列表只含本院系", all((r.get("college") or "").strip() == col for r in rows),
            [(r.get("username"), r.get("college")) for r in rows][:5])
    chk("二级学院审核不能进系统维护", can_use(t, "GET", "/api/system/info") == 403)
else:
    print("  （无 college1 演示账号，跳过）")

print("\n--- 6. 批量导入证件照的范围校验 ---")
# 班主任（scope_class）导入一个不属于本班的账号照片 → 应判 forbidden
if "teacher" in found:
    t, u = found["teacher"]
    # 找一个不在该班主任班级范围内的账号（用管理员账号本身）
    other = admin["username"]
    from PIL import Image  # noqa: E402
    import io  # noqa: E402
    buf = io.BytesIO()
    Image.new("RGB", (295, 413), (255, 255, 255)).save(buf, "JPEG")
    img = buf.getvalue()
    b = "----permtest"
    out = b""
    out += ('--%s\r\nContent-Disposition: form-data; name="size"\r\n\r\none_inch\r\n' % b).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="color"\r\n\r\nwhite\r\n' % b).encode()
    out += ('--%s\r\nContent-Disposition: form-data; name="files"; filename="%s.jpg"\r\n'
            'Content-Type: image/jpeg\r\n\r\n' % (b, other)).encode()
    out += img + b"\r\n--%s--\r\n" % b.encode()
    r = urllib.request.Request(BASE + "/api/photos/import/preview", data=out, method="POST",
                               headers={"Content-Type": "multipart/form-data; boundary=" + b,
                                        "Authorization": "Bearer " + t})
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            env = json.loads(resp.read().decode("utf-8"))
            d = env.get("data") if env.get("code") == 0 else env
    except urllib.error.HTTPError as e:
        d = {"__status": e.code}
    items = (d or {}).get("items") or []
    statuses = [i.get("status") for i in items]
    chk("越范围账号被判 forbidden（而非 matched）", "forbidden" in statuses,
        statuses)
else:
    print("  （无 teacher1 演示账号，跳过）")

print("\n" + "=" * 60)
print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
