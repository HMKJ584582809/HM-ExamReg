# -*- coding: utf-8 -*-
"""通用考试报名模板（含自定义字段）专项回归（需服务运行）。

用法
----
    rm -rf data
    EXAM_DEV=1 EXAM_SEED_DEMO=1 python -m uvicorn backend.app.main:app --port 8798
    python tools/verify_generic.py http://127.0.0.1:8798

账号与命名：
本脚本**不依赖演示数据**——需要的考生 / 二级学院账号若不存在就用管理员现场创建，
类型编码与批次名带运行时间戳，因此同一库上可重复运行，也能直接在发布版 exe 上跑。
"""
import io
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8798"
PASS, FAIL = [], []

NOW = datetime.now()
RUN = str(int(time.time()) % 100000)          # 同库重跑不与历史数据撞名
TYPE_CODE = f"englishtest{RUN}"
TYPE_NAME = f"英语等级考试{RUN}"
EXAM_NAME = f"2026年英语等级考试{RUN}"
COLLEGE = "外国语学院"      # 导入行与二级学院审核账号统一用它，保证范围断言确定性
START = (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
END = (NOW + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")

# 合法身份证号（GB 11643 校验位已算好）
ID_A = "32010219950101013X"
ID_B = "320102199501010236"
ID_C = "320102199501010930"
ID_D = "320102199501010471"
ID_E = "320102199501010121"
ID_F = "320102199501010746"   # 改名闭环用例专用


def call(method, path, token=None, body=None):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:200]}


def call_raw(method, path, token=None, body=None):
    """返回 (status, bytes) —— 用于文件流接口。"""
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("" if cond else f"  -> {detail}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return r["data"]["access_token"] if st == 200 and r.get("code") == 0 else None


def ensure_user(username, password, role, college="", phone_tail="", admin=""):
    """登录；账号不存在时用管理员创建再登录。

    不依赖 EXAM_SEED_DEMO 的演示账号，因此同一套断言也能跑在发布版 exe 上。
    """
    t = login(username, password)
    if t:
        return t
    st, r = call("POST", "/api/users", token=admin, body={
        "username": username, "real_name": username, "role": role,
        "phone": f"1390{phone_tail}", "password": password,   # 1390 + 7 位 = 11 位
        "college": college, "class_name": "英语2101" if role != "college_reviewer" else "",
    })
    if st != 200:
        print(f"  [提示] 创建账号 {username} 失败：{r.get('message', '')}")
    return login(username, password)


def d_of(r):
    return r.get("data") or {}


# ================================================================== [1] 类型与字段定义
print("\n[1] 通用模板与自定义字段定义")
admin = login("admin", "Admin@123")
check("管理员登录成功", bool(admin))
# 考生与二级学院审核账号：有就复用演示账号，没有就现场建
cand = ensure_user("candidate", "Candidate@123", "candidate", phone_tail="0000111",
                   admin=admin)
check("考生账号可用", bool(cand))
college = ensure_user("college", "College@123", "college_reviewer",
                      college="外国语学院", phone_tail="0000222", admin=admin)
check("二级学院审核账号可用", bool(college))

st, r = call("GET", "/api/exam-types", token=admin)
types = d_of(r).get("list", [])
gn = [t for t in types if t["code"] == "generic"]
check("内置类型含「通用考试报名」", bool(gn), str([t["code"] for t in types]))
check("通用模板有 8 个固定字段", bool(gn) and len(gn[0]["fields"]) == 8,
      str(len(gn[0]["fields"])) if gn else "")
check("非通用类型不返回 custom_fields",
      all(not t.get("custom_fields") for t in types if t["base_type"] != "generic"))

# 基于通用模板新建一个类型
st, r = call("POST", "/api/exam-types", token=admin,
             body={"code": TYPE_CODE, "name": TYPE_NAME, "base_type": "generic",
                   "description": "通用模板演示"})
check("基于通用模板新建类型成功", st == 200, r.get("message", ""))
tid = d_of(r).get("id")
check("新类型 base_type 为 generic", d_of(r).get("base_type") == "generic")

# 往非通用模板加字段应被拒
st, r = call("POST", "/api/exam-types/1/fields", token=admin,
             body={"field_key": "x1", "label": "测试"})
check("非通用模板不支持自定义字段", st != 200, str(st))

# 添加各类自定义字段
FIELDS = [
    ("exam_level", "报考等级", "select", True, ["A级", "B级", "C级"]),
    ("exam_score", "平时成绩", "number", False, []),
    ("signup_date", "意向考试日期", "date", False, []),
    ("remark", "备注", "textarea", False, []),
    ("tags", "擅长方向", "multiselect", False, ["听力", "阅读", "写作"]),
    ("ref_no", "推荐码", "text", False, []),
]
ok_fields = []
for key, label, ftype, req_, opts in FIELDS:
    st, r = call("POST", f"/api/exam-types/{tid}/fields", token=admin,
                 body={"field_key": key, "label": label, "field_type": ftype,
                       "required": req_, "options": opts})
    ok_fields.append(st == 200)
    check(f"添加字段「{label}」", st == 200, r.get("message", ""))
check("六类字段全部添加成功", all(ok_fields))

# 字段定义的合法性校验
for name, body in [
    ("大写开头的字段标识被拒", {"field_key": "Bad", "label": "不合法"}),
    ("保留字 extra 被拒", {"field_key": "extra", "label": "保留"}),
    ("与固定字段 name 冲突被拒", {"field_key": "name", "label": "重名"}),
    ("下拉类型缺选项被拒", {"field_key": "s1", "label": "下拉", "field_type": "select",
                            "options": []}),
    ("未知字段类型被拒", {"field_key": "t1", "label": "类型", "field_type": "unknown"}),
    ("字段名称为空被拒", {"field_key": "t2", "label": ""}),
]:
    st, r = call("POST", f"/api/exam-types/{tid}/fields", token=admin, body=body)
    check(name, st != 200, str(st))

st, r = call("POST", f"/api/exam-types/{tid}/fields", token=admin,
             body={"field_key": "exam_level", "label": "重复的等级"})
check("重复字段标识被拒", st != 200, str(st))

st, r = call("GET", f"/api/exam-types/{tid}/fields", token=admin)
flist = d_of(r).get("list", [])
check("字段列表返回 6 个", len(flist) == 6, str(len(flist)))
check("字段类型字典可用", bool(d_of(r).get("types")))
sel = [f for f in flist if f["key"] == "exam_level"]
check("下拉字段带选项", bool(sel) and sel[0]["options"] == ["A级", "B级", "C级"])

st, r = call("GET", "/api/exam-types", token=admin)
row = [t for t in d_of(r).get("list", []) if t["code"] == TYPE_CODE]
check("类型详情带出自定义字段", bool(row) and len(row[0].get("custom_fields", [])) == 6)

# ================================================================== [2] 报名
print("\n[2] 报名提交与自定义字段校验")
st, r = call("POST", "/api/exams", token=admin,
             body={"name": EXAM_NAME, "exam_type": TYPE_CODE,
                   "exam_year": 2026, "exam_month": 6,
                   "signup_start_at": START, "signup_end_at": END, "status": "open"})
check("建通用类型批次成功", st == 200, r.get("message", ""))
eid = d_of(r).get("id")

st, r = call("GET", f"/api/exams/{eid}", token=admin)
meta = d_of(r).get("fields", [])
custom_meta = [f for f in meta if f.get("custom")]
check("字段元信息含 6 个自定义字段", len(custom_meta) == 6, str(len(custom_meta)))
check("自定义字段标记 custom=True", all(f.get("custom") for f in custom_meta))
check("自定义字段带 type", all(f.get("type") for f in custom_meta))

base_body = {"exam_id": eid, "name": "赵六", "gender": "男", "id_type": "1",
             "id_number": ID_A, "phone": "13900000011", "email": "zhao@example.com",
             "class_name": "英语2101", "college": "外国语学院",
             "extra": {"exam_level": "A级", "exam_score": "88", "signup_date": "2026-06-01",
                       "remark": "无", "tags": ["听力", "写作"], "ref_no": "R001"}}

# 必填的自定义字段缺失
b = dict(base_body)
b["extra"] = dict(base_body["extra"])
b["extra"].pop("exam_level")
st, r = call("POST", "/api/applications", token=cand, body=b)
check("必填自定义字段缺失被拒", st != 200 and "报考等级" in r.get("message", ""),
      r.get("message", ""))

for name, patch, kw in [
    ("数字字段填非数字被拒", {"exam_score": "优秀"}, "数字"),
    ("日期字段格式错被拒", {"signup_date": "2026/06/01"}, "日期"),
    ("下拉值越界被拒", {"exam_level": "D级"}, "选项"),
]:
    b = dict(base_body)
    b["extra"] = dict(base_body["extra"])
    b["extra"].update(patch)
    st, r = call("POST", "/api/applications", token=cand, body=b)
    check(name, st != 200 and kw in r.get("message", ""), r.get("message", ""))

st, r = call("POST", "/api/applications", token=cand, body=base_body)
check("正常提交报名成功", st == 200, r.get("message", ""))
aid = d_of(r).get("id")

st, r = call("GET", f"/api/applications/{aid}?app_type=generic", token=cand)
d = d_of(r)
check("详情返回 app_type=generic", d.get("app_type") == "generic")
ex = d.get("extra") or {}
check("extra 落库正确（下拉）", ex.get("exam_level") == "A级", str(ex))
check("extra 落库正确（数字按字符串存）", ex.get("exam_score") == "88", str(ex))
check("extra 落库正确（日期）", ex.get("signup_date") == "2026-06-01", str(ex))
check("extra 落库正确（多选为数组）", ex.get("tags") == ["听力", "写作"], str(ex))
check("custom_values 用中文标签给出",
      (d.get("custom_values") or {}).get("报考等级") == "A级", str(d.get("custom_values")))
check("多选在 custom_values 里用「、」连接",
      (d.get("custom_values") or {}).get("擅长方向") == "听力、写作",
      str((d.get("custom_values") or {}).get("擅长方向")))

# 伪造字段 key 应被丢弃（同一账号在同一批次只能报一次，这里改用编辑验证）
fake = dict(base_body)
fake["extra"] = dict(base_body["extra"])
fake["extra"]["hack"] = "x"
st, r = call("PUT", f"/api/applications/{aid}?app_type=generic", token=cand, body=fake)
check("编辑报名成功", st == 200, r.get("message", ""))
st, r = call("GET", f"/api/applications/{aid}?app_type=generic", token=cand)
check("伪造字段 key 被丢弃（不写库）",
      "hack" not in (d_of(r).get("extra") or {}), str(d_of(r).get("extra")))

# 编辑后自定义字段的值应更新
upd = dict(base_body)
upd["extra"] = dict(base_body["extra"])
upd["extra"]["exam_score"] = "95"
st, r = call("PUT", f"/api/applications/{aid}?app_type=generic", token=cand, body=upd)
check("再次编辑成功", st == 200, r.get("message", ""))
st, r = call("GET", f"/api/applications/{aid}?app_type=generic", token=cand)
check("编辑后 extra 更新", (d_of(r).get("extra") or {}).get("exam_score") == "95",
      str(d_of(r).get("extra")))

# ================================================================== [3] 批量导入
print("\n[3] 批量导入")
from openpyxl import Workbook  # noqa: E402

st, raw = call_raw("GET", f"/api/applications/import-template?exam_id={eid}", token=admin)
check("下载导入模板成功", st == 200 and raw[:2] == b"PK")
if st == 200 and raw[:2] == b"PK":
    from openpyxl import load_workbook  # noqa: E402
    wt = load_workbook(io.BytesIO(raw))
    heads = [c.value for c in wt.worksheets[0][1]]
    check("模板表头含自定义字段标签", "报考等级" in heads and "擅长方向" in heads, str(heads))
    check("模板表头含固定字段", "姓名" in heads and "手机号码" in heads, str(heads))

# 构造导入文件（列顺序故意打乱，并含多余列）
wb = Workbook()
ws = wb.active
ws.append(["姓名", "报考等级", "证件号码", "手机号码", "性别", "院系", "班级",
           "平时成绩", "擅长方向", "无关列"])
ws.append(["孙七", "B级", ID_C, "13900000022", "女", "外国语学院", "英语2102",
           "77", "阅读", "忽略我"])
# 第二行故意换一个院系，用于验证「本院系」范围过滤
ws.append(["吴九", "A级", ID_D, "13900000044", "男", "计算机学院", "软件2101",
           "91", "听力、写作", "忽略我"])
buf = io.BytesIO()
wb.save(buf)
payload = buf.getvalue()


def post_file(path, files, fields, token):
    """multipart/form-data 上传。"""
    boundary = "----verifygeneric"
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write(f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode())
        out.write(f"{v}\r\n".encode())
    for k, (fn, content) in files.items():
        out.write(f"--{boundary}\r\n".encode())
        out.write((f'Content-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                   "Content-Type: application/vnd.openxmlformats-officedocument."
                   f'spreadsheetml.sheet\r\n\r\n').encode())
        out.write(content)
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(BASE + path, data=out.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"message": raw[:200]}


st, r = post_file("/api/applications/import/preview", {"file": ("in.xlsx", payload)},
                  {"exam_id": str(eid)}, admin)
check("导入预览成功", st == 200, r.get("message", ""))
mapping = d_of(r).get("mapping", [])
picked = {m["field"] for m in mapping if m["field"]}
check("预览识别到自定义字段列（报考等级）", "exam_level" in picked, str(picked))
check("预览识别到自定义字段列（擅长方向）", "tags" in picked, str(picked))
check("预览识别到固定字段（姓名/证件号）",
      {"name", "id_number"} <= picked, str(picked))
check("预览未把无关列识别成字段", "hack" not in picked)

st, r = post_file("/api/applications/import", {"file": ("in.xlsx", payload)},
                  {"exam_id": str(eid), "audit_status": "pending",
                   # 院系列不在导入表里，用整批默认值补齐，供后面「本院系」范围断言使用
                   "defaults": json.dumps({"college": COLLEGE}),
                   "create_accounts": "1"}, admin)
check("批量导入成功", st == 200 and d_of(r).get("success") == 2,
      json.dumps(d_of(r), ensure_ascii=False)[:200])

st, r = call("GET", f"/api/applications?app_type=generic&exam_id={eid}", token=admin)
rows = d_of(r).get("list", [])
imp = [x for x in rows if x.get("id_number") == ID_C]
check("导入记录已写入通用表", bool(imp), str(len(rows)))
if imp:
    check("导入行的自定义字段值正确", imp[0].get("extra", {}).get("exam_level") == "B级",
          str(imp[0].get("extra")))
    check("导入行的多选值正确", imp[0].get("extra", {}).get("tags") == ["阅读"],
          str(imp[0].get("extra")))

# 缺必填自定义字段列时应拒绝
wb = Workbook()
ws = wb.active
ws.append(["姓名", "证件号码", "手机号码"])
ws.append(["周八", ID_E, "13900000033"])
buf = io.BytesIO()
wb.save(buf)
st, r = post_file("/api/applications/import", {"file": ("bad.xlsx", buf.getvalue())},
                  {"exam_id": str(eid), "audit_status": "pending"}, admin)
check("缺必填自定义字段列时拒绝导入", st != 200 and "报考等级" in r.get("message", ""),
      r.get("message", ""))

# 用整批默认值补齐自定义字段
st, r = post_file("/api/applications/import", {"file": ("bad.xlsx", buf.getvalue())},
                  {"exam_id": str(eid), "audit_status": "pending",
                   "defaults": json.dumps({"exam_level": "C级", "college": COLLEGE})}, admin)
check("整批默认值可补齐自定义必填字段", st == 200 and d_of(r).get("success") == 1,
      r.get("message", ""))

# ---------------------------------------------------------------- 改名闭环
# 管理员把「报考等级」改名为「英语等级」后：新表头要能识别，旧表头也要能识别
st, r = call("GET", f"/api/exam-types/{tid}/fields", token=admin)
# 列表返回的字段名是 key（不是 field_key）
f_lv = next((f for f in (d_of(r).get("list") or []) if f.get("key") == "exam_level"), None)
check("能取到待改名的自定义字段", bool(f_lv), str(d_of(r))[:120])
if f_lv:
    st, r = call("PUT", f"/api/exam-types/{tid}/fields/{f_lv['id']}", token=admin,
                 body={"field_key": "exam_level", "label": "英语等级",
                       "field_type": "select", "required": True,
                       "options": ["A级", "B级", "C级"], "sort": 10})
    check("自定义字段改名成功", st == 200, r.get("message", ""))

    # 用新表头导入：只改了显示名，字段标识不变，仍应映射到 exam_level
    wb = Workbook()
    ws = wb.active
    ws.append(["姓名", "英语等级", "证件号码", "手机号码", "院系"])
    ws.append(["郑十", "C级", ID_F, "13900000055", COLLEGE])
    buf = io.BytesIO()
    wb.save(buf)
    st, r = post_file("/api/applications/import/preview",
                      {"file": ("renamed.xlsx", buf.getvalue())},
                      {"exam_id": str(eid)}, admin)
    check("改名后预览仍能识别该列", st == 200
          and "exam_level" in {m["field"] for m in (d_of(r).get("mapping") or [])
                               if m["field"]},
          str([m["field"] for m in (d_of(r).get("mapping") or [])]))

    st, r = post_file("/api/applications/import",
                      {"file": ("renamed.xlsx", buf.getvalue())},
                      {"exam_id": str(eid), "audit_status": "pending",
                       "create_accounts": "1"}, admin)
    check("改名后导入成功", st == 200 and d_of(r).get("success") == 1,
          json.dumps(d_of(r), ensure_ascii=False)[:200])

    # 改名后导出的模板表头应跟着变
    st, raw = call_raw("GET", f"/api/applications/import-template?exam_id={eid}", token=admin)
    if st == 200 and raw[:2] == b"PK":
        from openpyxl import load_workbook  # noqa: E402
        heads2 = [c.value for c in load_workbook(io.BytesIO(raw)).worksheets[0][1]]
        check("改名后模板表头同步为新名称",
              "英语等级" in heads2 and "报考等级" not in heads2, str(heads2))

    # 改回原名，避免影响后续断言
    call("PUT", f"/api/exam-types/{tid}/fields/{f_lv['id']}", token=admin,
         body={"field_key": "exam_level", "label": "报考等级", "field_type": "select",
               "required": True, "options": ["A级", "B级", "C级"], "sort": 10})

# ================================================================== [4] 导出
print("\n[4] 导出")
st, raw = call_raw("POST", "/api/export", token=admin,
                   body={"exam_id": eid, "app_type": "generic", "audit_status": "all",
                         "keyword": ""})
check("导出成功", st == 200 and raw[:2] == b"PK")
if st == 200 and raw[:2] == b"PK":
    from openpyxl import load_workbook  # noqa: E402
    we = load_workbook(io.BytesIO(raw))
    eheads = [c.value for c in we.worksheets[0][1]]
    check("导出表头含自定义字段", "报考等级" in eheads and "擅长方向" in eheads, str(eheads))
    vals = [[c.value for c in row] for row in we.worksheets[0].iter_rows(min_row=2)]
    flat = json.dumps(vals, ensure_ascii=False)
    check("导出含自定义字段的值", "A级" in flat, flat[:200])
    check("多选在导出里用「、」连接", "听力、写作" in flat, flat[:300])

# ================================================================== [5] 范围与收敛
print("\n[5] 数据范围收敛")
# /api/auth/me 的 data 就是用户对象本身（不再套一层 user）
st, r = call("GET", "/api/auth/me", token=college)
college_uid = d_of(r).get("id")
# 把审核账号的院系对齐到一条真实存在的报名记录上，保证断言确定性
# （不这么做的话，演示账号的院系可能与本次造的数据不一致，范围过滤后为空）
if college_uid and (d_of(r).get("college") or "") != COLLEGE:
    call("PUT", f"/api/users/{college_uid}", token=admin, body={"college": COLLEGE})
    college = login("college", "College@123")
st, r = call("GET", "/api/auth/me", token=college)
my_college = d_of(r).get("college") or ""
st, r = call("GET", "/api/applications?app_type=generic", token=college)
check("二级学院审核可查通用模板数据", st == 200, r.get("message", ""))
lst = d_of(r).get("list", [])
st2, r2 = call("GET", "/api/applications?app_type=generic", token=admin)
all_cnt = len(d_of(r2).get("list", []))
# 通用表的院系列叫 college：范围过滤必须命中它，写死 school/department 会漏数据
check("本院系范围按 college 列过滤",
      all((x.get("college") or "") == my_college for x in lst) and bool(lst),
      f"my={my_college} got={[x.get('college') for x in lst]}")
check("本院系范围确实收敛（少于全校）", len(lst) < all_cnt,
      f"{len(lst)} vs {all_cnt}")

st, r = call("GET", "/api/analysis/summary", token=admin)
check("分析接口不因通用模板报错", st == 200, r.get("message", ""))
st, r = call("GET", "/api/analysis/charts", token=admin)
check("图表接口不因通用模板报错", st == 200, r.get("message", ""))

# ================================================================== [6] 删除字段
print("\n[6] 删除字段")
st, r = call("GET", f"/api/exam-types/{tid}/fields", token=admin)
fid = [f for f in d_of(r).get("list", []) if f["key"] == "ref_no"][0]["id"]
st, r = call("DELETE", f"/api/exam-types/{tid}/fields/{fid}", token=admin)
check("删除自定义字段成功", st == 200, r.get("message", ""))
check("删除提示已填写的记录数", "推荐码" in r.get("message", ""), r.get("message", ""))
st, r = call("GET", f"/api/applications/{aid}?app_type=generic", token=cand)
labels = [f.get("label") for f in d_of(r).get("fields", [])]
check("删除后字段元信息不再含该字段", "推荐码" not in labels, str(labels))
check("已存数据不受影响（历史值仍在）",
      (d_of(r).get("extra") or {}).get("ref_no") == "R001")

print("\n" + "=" * 60)
print(f"结果：{len(PASS)} 项通过，{len(FAIL)} 项失败")
if FAIL:
    for f in FAIL:
        print("  FAIL -", f)
sys.exit(1 if FAIL else 0)
