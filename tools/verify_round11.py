# -*- coding: utf-8 -*-
"""第 11 轮需求专项验证。

覆盖本轮新增/改动：
  1. 考试照片要求（尺寸 / 底色）：创建、更新、非法值、列表回显 photo_requirement
  2. 证件照可调参数：specs 下发 params、make 携带 params、参数越界被夹紧
  3. 注册验证方式门禁：verify-options 按后台开关、未开启方式 send-code / register 均 403
  4. 系统设置：register 段保存与「至少保留一种」校验、idphoto.params 保存
  5. 字典默认值：二级学院 9 条、部门 18+2 条（学工办/专职教师）
  6. 模拟模式：仅管理员、开启生成、关闭清除

用法：
    python tools/verify_round11.py http://127.0.0.1:8942
需**全新库**（EXAM_DEV=1 EXAM_SEED_DEMO=1）。
"""
import io
import json
import random
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8942"
PASS, FAIL, SKIPPED = [], [], []
SUF = str(random.randint(10000, 99999))


def chk(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name + (("  -> " + str(extra)) if extra and not cond else ""))


def skip(name, why=""):
    """记一条「跳过」：环境不具备条件，不是功能坏了。

    典型场景：对**发布版 exe** 跑本脚本时，图形验证码不回显（EXAM_DEV=0），
    脚本没法自助注册出非管理员账号。这属于测试手段受限，不是被测功能有问题，
    所以既不算 PASS（那会假装验证过）也不算 FAIL（那会误导人去查功能）。
    """
    SKIPPED.append(name)
    print("  SKIP " + name + (("  -> " + str(why)) if why else ""))


def req(method, path, body=None, token=None, raw=False, form=None):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if form is not None:
        data = form
    if token:
        headers["Authorization"] = "Bearer " + token
    r = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(r, timeout=120) as resp:
            rawb = resp.read()
            if raw:
                return resp.status, rawb
            env = json.loads(rawb.decode("utf-8"))
            # 统一解包：成功取 data，失败保留 code/message 便于定位
            return resp.status, (env.get("data") if env.get("code") == 0 else env)
    except urllib.error.HTTPError as e:
        rawb = e.read()
        try:
            j = json.loads(rawb.decode("utf-8"))
        except Exception:
            j = {"detail": rawb.decode("utf-8", "ignore")}
        return e.code, j


def login(acct, pwd):
    st, d = req("POST", "/api/auth/login", {"account": acct, "password": pwd})
    assert st == 200, (st, d)
    return d["access_token"], d["user"]


print("=" * 60)
print("第 11 轮需求专项验证  base=%s" % BASE)
print("=" * 60)

# ---------------------------------------------------------------- 准备账号
ADMIN = ("admin", "Admin@123")
tk, me = login(*ADMIN)
print("管理员：%s / %s" % (me["username"], me["role"]))

print("\n--- 1. 考试照片要求 ---")
st, types = req("GET", "/api/exam-types/options", token=tk)
etype = (types.get("list") or [{"value": "computer"}])[0]["value"]
st, d = req("POST", "/api/exams", {
    "exam_type": etype, "exam_year": 2026, "exam_month": 6,
    "name": "照片要求批次" + SUF, "status": "draft",
    "signup_start_at": "2026-06-01 08:00:00", "signup_end_at": "2026-06-20 18:00:00",
    "photo_size": "two_inch", "photo_color": "blue",
}, token=tk)
chk("创建批次带照片要求", st == 200, d)
pr = (d.get("photo_requirement") or {}) if st == 200 else {}
chk("回显 size=two_inch", pr.get("size") == "two_inch", pr)
chk("回显 color=blue", pr.get("color") == "blue", pr)
chk("中文标签 text", pr.get("text") == "二寸照片、蓝底", pr)
chk("limited=True", pr.get("limited") is True, pr)
eid = d.get("id") if st == 200 else None

st2, d2 = req("POST", "/api/exams", {
    "exam_type": etype, "exam_year": 2026, "exam_month": 7,
    "name": "非法照片要求" + SUF, "status": "draft",
    "signup_start_at": "2026-07-01 08:00:00", "signup_end_at": "2026-07-20 18:00:00",
    "photo_size": "three_inch",
}, token=tk)
chk("非法尺寸被拒 400", st2 == 400, (st2, d2))

if eid:
    st3, d3 = req("PUT", "/api/exams/%s" % eid, {"photo_size": "one_inch", "photo_color": "white"}, token=tk)
    chk("更新照片要求", st3 == 200 and (d3.get("photo_requirement") or {}).get("size") == "one_inch", d3)
    st4, d4 = req("PUT", "/api/exams/%s" % eid, {"photo_size": "", "photo_color": ""}, token=tk)
    chk("清空=不限制", st4 == 200 and (d4.get("photo_requirement") or {}).get("limited") is False, d4)
    st5, d5 = req("GET", "/api/exams?keyword=" + SUF, token=tk)
    row = ((d5.get("list") or [{}])[0])
    chk("列表回显 photo_requirement", "photo_requirement" in row, list(row.keys())[:12])

print("\n--- 2. 证件照可调参数 ---")
st, sp = req("GET", "/api/idphoto/specs", token=tk)
chk("specs 下发 params", isinstance(sp.get("params"), dict), sp.get("params"))
p = sp.get("params") or {}
chk("默认 alpha_threshold=55", p.get("alpha_threshold") == 55, p)
chk("默认 bottom_fill=12", p.get("bottom_fill") == 12, p)
chk("默认 blank_fill=edge", p.get("blank_fill") == "edge", p)
chk("param_ranges 存在", "alpha_threshold" in (sp.get("param_ranges") or {}), sp.get("param_ranges"))

# 造一张测试图（纯色 + 中间一块，模拟人像）
from PIL import Image  # noqa: E402
buf = io.BytesIO()
im = Image.new("RGB", (600, 800), (200, 210, 220))
for y in range(200, 800):
    for x in range(150, 450):
        im.putpixel((x, y), (90, 70, 60))
im.save(buf, "JPEG", quality=90)
img_bytes = buf.getvalue()


def multipart(fields, files):
    b = "----round11boundary"
    out = b""
    for k, v in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b, k, v)).encode()
    for k, (fn, data, ct) in files.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                "Content-Type: %s\r\n\r\n" % (b, k, fn, ct)).encode()
        out += data + b"\r\n"
    out += ("--%s--\r\n" % b).encode()
    return out, "multipart/form-data; boundary=%s" % b


body, ctype = multipart(
    {"size": "one_inch", "color": "blue",
     "params": json.dumps({"alpha_threshold": 40, "edge_feather": 8, "bottom_fill": 15, "blank_fill": "white"})},
    {"file": ("t.jpg", img_bytes, "image/jpeg")})
r = urllib.request.Request(BASE + "/api/idphoto/make", data=body, method="POST",
                           headers={"Content-Type": ctype, "Authorization": "Bearer " + tk})
try:
    with urllib.request.urlopen(r, timeout=180) as resp:
        chk("带 params 制作成功", resp.status == 200 and len(resp.read()) > 1000)
except urllib.error.HTTPError as e:
    chk("带 params 制作成功", False, e.read().decode("utf-8", "ignore")[:200])

# 越界参数应被夹紧而不是报错
body, ctype = multipart(
    {"size": "one_inch", "color": "white",
     "params": json.dumps({"alpha_threshold": 9999, "edge_feather": -5, "bottom_fill": 999, "blank_fill": "xxx"})},
    {"file": ("t.jpg", img_bytes, "image/jpeg")})
r = urllib.request.Request(BASE + "/api/idphoto/make", data=body, method="POST",
                           headers={"Content-Type": ctype, "Authorization": "Bearer " + tk})
try:
    with urllib.request.urlopen(r, timeout=180) as resp:
        chk("越界参数被夹紧不报错", resp.status == 200 and len(resp.read()) > 1000)
except urllib.error.HTTPError as e:
    chk("越界参数被夹紧不报错", False, e.read().decode("utf-8", "ignore")[:200])

print("\n--- 3. 注册验证方式门禁 ---")
st, vo = req("GET", "/api/auth/verify-options?purpose=register")
chk("默认只开图形验证码", vo.get("captcha", {}).get("enabled") is True
    and vo.get("sms", {}).get("enabled") is False
    and vo.get("email", {}).get("enabled") is False, vo)
st, d = req("POST", "/api/auth/send-code",
            {"target_type": "sms", "target": "1390000" + SUF[:4], "purpose": "register"})
chk("未开启短信 → 发码 403", st == 403, (st, d))
st, d = req("POST", "/api/auth/send-code",
            {"target_type": "email", "target": "a%s@test.com" % SUF, "purpose": "register"})
chk("未开启邮箱 → 发码 403", st == 403, (st, d))

st, d = req("POST", "/api/auth/register", {
    "username": "r11_" + SUF, "phone": "1390000" + SUF[:4], "email": "",
    "password": "Abcd12345", "confirm_password": "Abcd12345",
    "verify_type": "sms", "code_target": "1390000" + SUF[:4], "code": "123456",
})
chk("未开启短信 → 注册 403", st == 403, (st, d))

# 图形验证码注册仍应可用（DEV 模式回显验证码）
st, cap = req("GET", "/api/auth/captcha")
chk("图形验证码可获取", st == 200 and bool(cap.get("captcha_id")), cap)
reg_code = cap.get("debug_code") or cap.get("code") or ""
if reg_code:
    st, d = req("POST", "/api/auth/register", {
        "username": "r11_" + SUF, "phone": "1390000" + SUF[:4], "email": "",
        "password": "Abcd12345", "confirm_password": "Abcd12345",
        "verify_type": "captcha", "captcha_id": cap["captcha_id"], "captcha_code": reg_code,
    })
    chk("图形验证码注册成功", st == 200, (st, d))

print("\n--- 4. 系统设置保存 ---")
st, cur = req("GET", "/api/system/settings", token=tk)
chk("settings 下发 register", isinstance(cur.get("register"), dict), cur.get("register"))
chk("settings 下发 idphoto.params", isinstance((cur.get("idphoto") or {}).get("params"), dict),
    (cur.get("idphoto") or {}).get("params"))
chk("settings 下发 mock", isinstance(cur.get("mock"), dict), cur.get("mock"))

st, d = req("PUT", "/api/system/settings", {
    "register": {"captcha": 0, "sms": 0, "email": 0}}, token=tk)
chk("注册方式全关被拒", st == 400, (st, d))

st, d = req("PUT", "/api/system/settings", {
    "register": {"captcha": 1, "sms": 1, "email": 0},
    "idphoto": {"remote_url": "", "prefer": "local",
                "params": {"alpha_threshold": 30, "edge_feather": 6, "bottom_fill": 20, "blank_fill": "white"}},
}, token=tk)
ok_save = st == 200
chk("保存 register + idphoto.params", ok_save, (st, d))
if ok_save:
    chk("register.sms 已开", (d.get("register") or {}).get("sms") == 1, d.get("register"))
    ip2 = (d.get("idphoto") or {}).get("params") or {}
    chk("idphoto.params 已生效", ip2.get("alpha_threshold") == 30 and ip2.get("blank_fill") == "white", ip2)

st, vo2 = req("GET", "/api/auth/verify-options?purpose=register")
chk("开启后 verify-options 同步", (vo2.get("sms") or {}).get("enabled") is True, vo2)
st, d = req("POST", "/api/auth/send-code",
            {"target_type": "sms", "target": "1390001" + SUF[:4], "purpose": "register"})
chk("开启后短信可发码", st == 200, (st, d))

# 复原：关掉短信，只留图形验证码；证件照参数也要还原成出厂默认值。
# ⚠ 不还原的话本脚本**再跑一次**就会看到上次的脏值（默认 alpha_threshold 变成 30），
#    表现为「功能坏了」，实际是脚本自己污染的（本套件会写 settings.json）。
req("PUT", "/api/system/settings", {
    "register": {"captcha": 1, "sms": 0, "email": 0},
    "idphoto": {"remote_url": "", "prefer": "local",
                "params": {"alpha_threshold": 55, "edge_feather": 10,
                           "bottom_fill": 12, "blank_fill": "edge"}},
}, token=tk)

print("\n--- 5. 字典：出厂不预置，且可自行录入 ---")
# ⚠ 断言反过来了：源码公开，出厂**必须**是空的（预置任何具体院校的院系/部门都等于泄密）。
# 所以这里验证「空 + 能自己加 + 加完不被重启覆盖」，而不是验证内置了哪几条。
st, names = req("GET", "/api/dicts/college", token=tk)
names = names or []
chk("二级学院出厂为空", len(names) == 0, names)
st, dnames = req("GET", "/api/dicts/department", token=tk)
dnames = dnames or []
chk("部门出厂为空", len(dnames) == 0, dnames)

# 补录两条自己造的值，验证字典可维护
st, _ = req("POST", "/api/dicts/college", {"name": "示例学院A"}, token=tk)
chk("可新增学院", st == 200, st)
st, _ = req("POST", "/api/dicts/department", {"name": "示例处室A"}, token=tk)
chk("可新增部门", st == 200, st)
st, names = req("GET", "/api/dicts/college", token=tk)
chk("新增后能查到", "示例学院A" in (names or []), names)
# 用完清掉，别把测试脏数据留在库里
for tbl, nm in (("college", "示例学院A"), ("department", "示例处室A")):
    st, lst = req("GET", f"/api/dicts/{tbl}", token=tk)
    for i, v in enumerate(lst or []):
        if v == nm:
            req("DELETE", f"/api/dicts/{tbl}/{i}", token=tk)

print("\n--- 6. 模拟模式（仅管理员） ---")
st, d = req("GET", "/api/system/mock", token=tk)
chk("管理员可查模拟模式状态", st == 200, (st, d))
# 造一个非管理员来测 403（图形验证码一次性：取一次用一次，不能取两次）
st, cap2 = req("GET", "/api/auth/captcha")
st, d = req("POST", "/api/auth/register", {
    "username": "m11_" + SUF, "phone": "1390002" + SUF[:4], "email": "",
    "password": "Abcd12345", "confirm_password": "Abcd12345",
    "verify_type": "captcha",
    "captcha_id": cap2["captcha_id"],
    "captcha_code": cap2.get("debug_code") or "0000",
})
if st == 200:
    tkc, _ = login("m11_" + SUF, "Abcd12345")
    st, d = req("GET", "/api/system/mock", token=tkc)
    chk("非管理员查模拟模式 403", st == 403, (st, d))
    st, d = req("POST", "/api/system/mock", {"enable": True, "count": 10}, token=tkc)
    chk("非管理员开模拟模式 403", st == 403, (st, d))
elif not cap2.get("debug_code"):
    # 发布版 exe 不回显验证码（EXAM_DEV=0），脚本无法自助注册出非管理员账号。
    # 这是测试手段受限，不是「模拟模式没有做管理员门禁」，所以记为跳过。
    why = "发布模式不回显图形验证码，无法自助注册非管理员（源码模式 EXAM_DEV=1 已覆盖）"
    skip("非管理员查模拟模式 403", why)
    skip("非管理员开模拟模式 403", why)
else:
    chk("非管理员账号准备", False, (st, d))

print("\n" + "=" * 60)
print("PASS %d / FAIL %d / SKIP %d" % (len(PASS), len(FAIL), len(SKIPPED)))
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
if SKIPPED:
    print("跳过项（环境不具备条件，功能本身未失败）：")
    for f in SKIPPED:
        print("  - " + f)
print("=" * 60)
sys.exit(1 if FAIL else 0)
