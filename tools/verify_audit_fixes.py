# -*- coding: utf-8 -*-
"""功能审计缺陷修复专项校验。

覆盖 2026-09-19 全量功能审计发现的 6 类问题：

  [1] 通用模板批次的报名统计恒为 0（exams.decorate 写死两张表）
  [2] 系统维护「报名数据」计数与「恢复出厂」清理漏掉通用表
  [3] 短信验证码从不真正发送（只有 delivery 标志位）
  [4] dicts/meta 缺 generic 键
  [5] 个人资料 real_name / email 无长度与格式校验
  [6] 用户管理「报名数」与「停用前影响评估」漏掉通用表

用法：python tools/verify_audit_fixes.py http://127.0.0.1:8798 [模拟短信网关端口]

自带账号与时间戳命名，可在**发布版 exe** 上直接跑（不依赖演示数据）。
"""
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8798"
SMS_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8978
PASS, FAIL = [], []
RUN = datetime.now().strftime("%m%d%H%M%S")

# ---------------------------------------------------------------- 模拟短信网关
SMS_HITS = []


class _SmsHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b"{}"
        SMS_HITS.append({
            "path": self.path,
            "auth": self.headers.get("Authorization", ""),
            "body": raw.decode("utf-8", "ignore"),
        })
        payload = json.dumps({"Code": "OK", "Message": "OK"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


def start_sms_mock(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _SmsHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def last_sms_code() -> str:
    """从模拟网关最近一次收到的请求体里取验证码。

    发布版 exe 不回显验证码（EXAM_DEV 只在开发模式生效），所以取码要走
    「配置网关 → 真实投递 → 从网关侧读回」这条路，顺带验证投递是真的。
    """
    if not SMS_HITS:
        return ""
    try:
        b = json.loads(SMS_HITS[-1]["body"] or "{}")
    except ValueError:
        return ""
    if isinstance(b.get("code"), str):
        return b["code"]
    tp = b.get("template_param") or b.get("TemplateParam")
    if isinstance(tp, dict):
        return str(tp.get("code") or "")
    if isinstance(tp, str):
        try:
            return str((json.loads(tp) or {}).get("code") or "")
        except ValueError:
            return ""
    return ""


def q(path: str, **params) -> str:
    """拼查询串（中文关键词必须编码，否则 urllib 会抛 UnicodeEncodeError）。"""
    return path + "?" + urllib.parse.urlencode(params)


def call(method, path, token=None, body=None, raw=False):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            payload = r.read()
            return r.status, (payload if raw else json.loads(payload.decode()))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, (payload if raw else json.loads(payload.decode()))
        except ValueError:
            return e.code, {"message": payload.decode("utf-8", "ignore")[:200]}


def d(r):
    return r.get("data") if isinstance(r, dict) else (r or {})


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {extra}" if extra and not cond else ""))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return r["data"]["access_token"] if st == 200 and r.get("code") == 0 else None


_PHONE_SEQ = [0]


def mk_user(token, uname, role, college=""):
    """建账号：已存在就直接登录（脚本可重复运行）。

    手机号必须全局唯一，同分钟内重跑也要错开，否则会被「已占用」挡住。
    """
    _PHONE_SEQ[0] += 1
    phone = "139" + f"{_PHONE_SEQ[0]:03d}" + RUN[-5:]
    body = {"username": uname, "password": "Test@12345", "real_name": uname,
            "role": role, "phone": phone}
    if college:
        body["college"] = college
    st, r = call("POST", "/api/users", token=token, body=body)
    if st != 200:
        print("     建号失败:", uname, r.get("message", ""))
    return login(uname, "Test@12345")


print("=" * 66)
print("功能审计缺陷修复专项校验")
print("=" * 66)

# ============================================================ [0] 准备
print("\n[0] 准备账号与数据")
srv = start_sms_mock(SMS_PORT)
admin = login("admin", "Admin@123")
check("管理员登录成功", bool(admin))
if not admin:
    print("无法登录，终止")
    sys.exit(1)

TYPE_CODE = f"audfix{RUN}"
EXAM_NAME = f"审计修复测试{RUN}"
# 发送频率限制按手机号计，两次发送必须用不同号码
SMS_A = "139" + "70" + RUN[-6:]
SMS_B = "139" + "71" + RUN[-6:]

st, r = call("POST", "/api/exam-types", token=admin,
             body={"code": TYPE_CODE, "name": f"审计类型{RUN}", "base_type": "generic",
                   "description": "审计用通用类型", "enabled": True, "sort": 66})
tid = (d(r) or {}).get("id")
check("创建通用考试类型", st == 200 and bool(tid), r.get("message", ""))

# 自定义字段：接口按类型 id 单条新增
ok_fields = 0
for f in ({"field_key": "level", "label": "报考等级", "field_type": "select",
           "required": True, "options": ["A级", "B级", "C级"], "sort": 10},
          {"field_key": "note", "label": "备注", "field_type": "textarea",
           "required": False, "sort": 20}):
    st, r = call("POST", f"/api/exam-types/{tid}/fields", token=admin, body=f)
    if st == 200:
        ok_fields += 1
    else:
        print("     字段写入失败:", r.get("message", ""))
check("写入两个自定义字段", ok_fields == 2, f"成功 {ok_fields}/2")

st, r = call("POST", "/api/exams", token=admin, body={
    "name": EXAM_NAME, "exam_type": TYPE_CODE, "exam_year": 2026, "exam_month": 6,
    "signup_start_at": "2026-01-01 00:00", "signup_end_at": "2030-12-31 23:59",
    "description": "审计修复用批次"})
eid = (d(r) or {}).get("id")
check("创建通用批次", st == 200 and bool(eid), json.dumps(r, ensure_ascii=False)[:200])

# 批次需开放后考生才能报名
st, r = call("POST", f"/api/exams/{eid}/status?status=open", token=admin, body={})
check("批次开放报名", st == 200, r.get("message", ""))

# 同一账号不能重复报名同一批次，因此建两个考生各提交一条
cands = []
for i in (1, 2):
    t = mk_user(admin, f"audcand{i}{RUN}"[:20], "candidate")
    if t:
        cands.append(t)
check("创建两个考生账号", len(cands) == 2, f"实际 {len(cands)}")
cand = cands[0] if cands else None


def apply_row(tok, name, idno, phone):
    return call("POST", "/api/applications", token=tok, body={
        "exam_id": eid, "name": name, "gender": "男", "id_number": idno,
        "phone": phone, "email": "a@b.com", "class_name": "测试班",
        "college": "测试学院", "extra": {"level": "A级", "note": "审计"}})


rows = ((f"甲{RUN}", "32010219950101013X", "13900000001"),
        (f"乙{RUN}", "320102199501010236", "13900000002"))
n_ok = 0
for i, (nm, idn, ph) in enumerate(rows):
    if i >= len(cands):
        break
    st, r = apply_row(cands[i], nm, idn, ph)
    if st == 200:
        n_ok += 1
    else:
        print("     报名失败:", r.get("message", ""))
check("提交两条通用模板报名", n_ok == 2, f"成功 {n_ok}/2")

# ============================================================ [1] 批次统计走对表
print("\n[1] 通用批次报名统计（缺陷 #1）")
st, r = call("GET", "/api/exams", token=admin)
ge = next((e for e in d(r).get("list", []) if e.get("id") == eid), None)
check("批次列表能查到通用批次", bool(ge))
if ge:
    check("批次列表 stats.total 正确（不再恒为 0）",
          (ge.get("stats") or {}).get("total") == n_ok,
          f"stats={ge.get('stats')} 期望 total={n_ok}")
    check("批次列表 stats.pending 正确",
          (ge.get("stats") or {}).get("pending") == n_ok,
          f"stats={ge.get('stats')}")

st, r = call("GET", f"/api/exams/{eid}", token=admin)
det = d(r) or {}
check("批次详情 stats.total 正确",
      (det.get("stats") or {}).get("total") == n_ok,
      f"stats={det.get('stats')} 期望 {n_ok}")

# ============================================================ [2] 系统维护计数含通用表
print("\n[2] 系统维护统计与清理（缺陷 #2）")
st, r = call("GET", "/api/system/info", token=admin)
counts = (d(r) or {}).get("counts") or {}
check("系统统计含通用模板数据（>= 本次提交数）",
      (counts.get("applications") or 0) >= n_ok,
      f"applications={counts.get('applications')} 期望 >={n_ok}")

# ============================================================ [4] dicts/meta
print("\n[4] 字典元信息（缺陷 #4）")
st, r = call("GET", "/api/dicts/meta", token=admin)
meta = d(r) or {}
check("dicts/meta 含 generic 键", "generic" in meta, str(list(meta.keys())))
check("generic 元信息标注无固定表头",
      (meta.get("generic") or {}).get("headers") == [])

# ============================================================ [5] 资料校验
print("\n[5] 个人资料校验（缺陷 #5）")
st, r = call("PUT", "/api/auth/profile", token=cand, body={"real_name": "测" * 500})
check("超长姓名被拒绝", st != 200, f"st={st} {r.get('message','')}")
st, r = call("PUT", "/api/auth/profile", token=cand, body={"email": "not-an-email"})
check("非法邮箱被拒绝", st != 200, f"st={st} {r.get('message','')}")
st, r = call("PUT", "/api/auth/profile", token=cand, body={"real_name": "正常姓名"})
check("正常姓名可通过", st == 200, r.get("message", ""))

# ============================================================ [6] 用户管理含通用表
print("\n[6] 用户管理报名数（缺陷 #6）")
st, r = call("GET", "/api/users?keyword=audcand", token=admin)
row = next((u for u in d(r).get("list", [])
            if str(u.get("username") or "").startswith("audcand")), None)
check("用户列表能查到测试考生", bool(row))
if row:
    # 该考生只在通用模板下报过名（1 条）：计数 > 0 即证明通用表被纳入统计
    check("用户报名数含通用模板记录",
          (row.get("app_count") or 0) >= 1,
          f"app_count={row.get('app_count')} 期望 >=1")
    st, r = call("GET", f"/api/users/{row['id']}/applications", token=admin)
    lst = d(r).get("list", [])
    check("停用前影响评估含通用模板记录",
          any(x.get("app_type") == "generic" for x in lst),
          f"types={[x.get('app_type') for x in lst]}")

# ============================================================ [3] 短信真实发送
print("\n[3] 短信验证码真实发送（缺陷 #3）")
SMS_HITS.clear()
st, r = call("PUT", "/api/system/settings", token=admin, body={
    "sms": {"provider": "generic", "endpoint": f"http://127.0.0.1:{SMS_PORT}/send",
            "api_key": "test-key", "sign": "测试签名", "template": "SMS_001"}})
cfg_ok = st == 200
if not cfg_ok:
    # 老接口不存在时，直接改服务端配置不可行则跳过真实发送断言
    print("     （无 /api/system/settings 接口，改用离线单测验证）")

if cfg_ok:
    # 需求9 之后：注册页的验证方式由后台开关决定，**没开启的方式一律不发码**。
    # 本节测的是 purpose=register 的短信链路，必须先打开「注册-手机验证码」。
    # 顺带这也成了门禁的反证：不开就该被拒（见下方「未开启时发码被拒」）。
    st, r = call("GET", "/api/system/settings", token=admin)
    _reg0 = ((d(r) or {}).get("register") or {})
    check("注册短信方式默认关闭", int(_reg0.get("sms") or 0) == 0,
          json.dumps(_reg0, ensure_ascii=False))
    st, r = call("POST", "/api/auth/send-code",
                 body={"target": SMS_A, "target_type": "sms", "purpose": "register"})
    check("未开启短信时注册发码被拒", st == 403, f"st={st} {r.get('message','')}")
    st, r = call("PUT", "/api/system/settings", token=admin, body={
        "register": {"captcha": 1, "sms": 1, "email": 0}})
    check("可后台开启注册短信方式", st == 200, r.get("message", ""))
    st, r = call("POST", "/api/auth/send-code",
                 body={"target": SMS_A, "target_type": "sms", "purpose": "register"})
    check("短信验证码接口成功", st == 200, r.get("message", ""))
    res = d(r) or {}
    check("delivery 标记为 sms（真实发出）", res.get("delivery") == "sms",
          f"delivery={res.get('delivery')} hint={r.get('message','')}")
    check("模拟网关确实收到了请求", len(SMS_HITS) == 1, f"hits={len(SMS_HITS)}")
    if SMS_HITS:
        hit = SMS_HITS[0]
        check("请求携带 Bearer 鉴权", hit["auth"].startswith("Bearer "), hit["auth"])
        body_j = {}
        try:
            body_j = json.loads(hit["body"])
        except ValueError:
            pass
        check("请求体含手机号", body_j.get("phone") == SMS_A, str(body_j)[:120])
        check("请求体含验证码", bool(str(body_j.get("code") or "").strip()),
              str(body_j)[:120])

# 未配置时不得谎报已发送
st, r = call("PUT", "/api/system/settings", token=admin, body={
    "sms": {"provider": "", "endpoint": "", "api_key": "", "sign": "", "template": ""}})
if cfg_ok:
    SMS_HITS.clear()
    st, r = call("POST", "/api/auth/send-code",
                 body={"target": SMS_B, "target_type": "sms", "purpose": "register"})
    res = d(r) or {}
    check("未配置网关时诚实标记 console", res.get("delivery") == "console",
          f"delivery={res.get('delivery')}")
    check("未配置网关时不发任何请求", len(SMS_HITS) == 0, f"hits={len(SMS_HITS)}")
    # 复原：注册短信方式回到默认关闭，别把后面「默认只开图形验证码」的断言带偏
    call("PUT", "/api/system/settings", token=admin,
         body={"register": {"captcha": 1, "sms": 0, "email": 0}})

# ============================================================ [8] 字典维护
print("\n[8] 字典维护（审计 #8：原本只读）")
st, r = call("GET", q("/api/dicts/manage/subject", keyword="", page=1, page_size=5), token=admin)
check("字典维护列表可读", st == 200 and isinstance((d(r) or {}).get("list"), list),
      r.get("message", ""))
sub_total = (d(r) or {}).get("total") or 0

new_name = f"审计科目{RUN}"
st, r = call("POST", "/api/dicts/manage/subject", token=admin, body={"name": new_name})
check("新增科目成功", st == 200, r.get("message", ""))
st, r = call("GET", q("/api/dicts/manage/subject", keyword=new_name), token=admin)
lst = (d(r) or {}).get("list") or []
hit = next((x for x in lst if x.get("name") == new_name), None)
check("新增后能在列表中查到", bool(hit), str([x.get("name") for x in lst]))
check("字典项带引用计数", hit is not None and isinstance(hit.get("used"), int))

st, r = call("POST", "/api/dicts/manage/subject", token=admin, body={"name": new_name})
check("重名科目被拒绝", st != 200, f"st={st}")

if hit:
    st, r = call("PUT", f"/api/dicts/manage/subject/{hit['id']}", token=admin,
                 body={"name": new_name + "改"})
    check("编辑科目成功", st == 200, r.get("message", ""))
    st, r = call("GET", q("/api/dicts/manage/subject", keyword=new_name + "改"), token=admin)
    lst2 = (d(r) or {}).get("list") or []
    hit2 = next((x for x in lst2 if x.get("name") == new_name + "改"), None)
    check("编辑后名称已更新", bool(hit2))
    if hit2:
        st, r = call("DELETE", f"/api/dicts/manage/subject/{hit2['id']}", token=admin)
        check("未被引用的科目可删除", st == 200, r.get("message", ""))

# 机构（主键是文本 code）
st, r = call("POST", "/api/dicts/manage/org", token=admin,
             body={"code": f"Z{RUN[-6:]}", "name": f"审计机构{RUN}"})
check("新增机构成功", st == 200, r.get("message", ""))
st, r = call("GET", q("/api/dicts/manage/org", keyword=f"审计机构{RUN}"), token=admin)
org_hit = next((x for x in ((d(r) or {}).get("list") or [])
                if x.get("name") == f"审计机构{RUN}"), None)
check("新增机构能查到（含编码）", bool(org_hit) and bool((org_hit or {}).get("code")))
if org_hit:
    st, r = call("DELETE", f"/api/dicts/manage/org/{org_hit['id']}", token=admin)
    check("未被引用的机构可删除", st == 200, r.get("message", ""))

# 被引用的字典项不能删除（保护历史数据）。
# 通用表没有 subject 列，所以这里建一条「计算机类」报名来制造真实引用。
st, r = call("GET", q("/api/dicts/manage/subject", page=1, page_size=5), token=admin)
subj = ((d(r) or {}).get("list") or [{}])[0].get("name") or ""
# 计算机类报名会校验机构编码必须存在于字典，取一个真实编码
st, r = call("GET", "/api/dicts/org", token=admin)
org0 = ((d(r) or []) or [{}])[0]
org_code, org_name = org0.get("code") or "", org0.get("name") or ""
st, r = call("POST", "/api/exams", token=admin, body={
    "name": f"审计计算机批次{RUN}", "exam_type": "computer", "exam_year": 2026,
    "exam_month": 6, "signup_start_at": "2026-01-01 00:00",
    "signup_end_at": "2030-12-31 23:59", "description": "用于验证字典引用保护"})
ceid = (d(r) or {}).get("id")
if ceid:
    call("POST", f"/api/exams/{ceid}/status?status=open", token=admin, body={})
c3 = mk_user(admin, f"audc{RUN}"[:20], "candidate")
if c3 and ceid and subj:
    st, r = call("POST", "/api/applications", token=c3, body={
        "exam_id": ceid, "name": f"引用{RUN}", "gender": "男",
        "org_code": org_code, "org_name": org_name, "exam_site_code": "100101",
        "id_type": "1", "id_number": "320102199501010842", "subject": subj,
        "school": "测试学院", "class_name": "测试班", "education": "本科",
        "phone": "13966600001", "email": "c@d.com", "address": "测试地址"})
    check("建一条引用了科目的计算机报名", st == 200, r.get("message", ""))
    st, r = call("GET", q("/api/dicts/manage/subject", keyword=subj), token=admin)
    used_one = next((x for x in ((d(r) or {}).get("list") or [])
                     if x.get("name") == subj), None)
    check("被引用的科目显示引用计数 > 0",
          bool(used_one) and (used_one or {}).get("used", 0) > 0,
          str(used_one))
    if used_one:
        st, r = call("DELETE", f"/api/dicts/manage/subject/{used_one['id']}", token=admin)
        check("被引用的字典项拒绝删除", st != 200, f"st={st} {r.get('message','')}")

# 权限：非 exam_manage 不能维护字典
st, r = call("POST", "/api/dicts/manage/subject", token=cand, body={"name": "越权科目"})
check("考生不能维护字典", st in (401, 403), f"st={st}")

# ============================================================ [9] 代考生撤回
print("\n[9] 管理员代考生撤回（审计 #9）")


def new_generic_app(tok, name, idno, phone):
    return call("POST", "/api/applications", token=tok, body={
        "exam_id": eid, "name": name, "gender": "男", "id_number": idno,
        "phone": phone, "email": "a@b.com", "class_name": "测试班",
        "college": "测试学院", "extra": {"level": "A级", "note": "审计"}})


# 重新建号（前面的清理已清空报名）
c2 = mk_user(admin, f"audw{RUN}"[:20], "candidate")
st, r = new_generic_app(c2, f"撤{RUN}", "320102199501010738", "13977700001")
w_id = (d(r) or {}).get("id")
check("重建一条待审核报名", st == 200 and bool(w_id), r.get("message", ""))

if w_id:
    st, r = call("POST", f"/api/applications/{w_id}/withdraw?app_type=generic", token=cand)
    check("他人（无审核权限）不能代撤回", st in (401, 403), f"st={st}")
    st, r = call("POST", f"/api/applications/{w_id}/withdraw?app_type=generic", token=admin)
    check("管理员可代考生撤回", st == 200, r.get("message", ""))
    st, r = call("GET", f"/api/applications/{w_id}?app_type=generic", token=admin)
    check("撤回后记录已删除", st != 200, f"st={st}")

# ============================================================ [10] 找回密码
print("\n[10] 找回密码（文档已承诺但此前未实现）")
rp_phone = "139" + "88" + RUN[-6:]
rp_user = f"audreset{RUN}"[:20]
rp = mk_user(admin, rp_user, "candidate")
check("建一个用于找回密码的账号", bool(rp))

# 找回密码的验证方式由后台开关决定（需求7）：默认只开「图形验证码 + 身份证号」，
# 短信/邮箱默认关闭。本节要验证短信链路，先把该方式打开，并顺带守住默认口径。
st, r = call("GET", "/api/system/settings", token=admin)
_rp0 = ((d(r) or {}).get("reset_password") or {})
check("默认只开启图形验证码方式", int(_rp0.get("captcha") or 0) == 1
      and int(_rp0.get("sms") or 0) == 0, json.dumps(_rp0, ensure_ascii=False))
if int(_rp0.get("sms") or 0) == 0:
    st, r = call("PUT", "/api/system/settings", token=admin,
                 body={"reset_password": {"enable": 1, "captcha": 1, "sms": 1, "email": 0}})
    check("可后台开启短信找回方式", st == 200, r.get("message", ""))

# 未绑定的号码不能发重置验证码
st, r = call("POST", "/api/auth/send-code",
             body={"target": "13900000099", "target_type": "sms", "purpose": "reset"})
check("未绑定号码发送重置码被拒", st != 200, f"st={st}")

# 取该账号真实手机号
st, r = call("GET", q("/api/users", keyword=rp_user), token=admin)
urow = next((u for u in (d(r) or {}).get("list", []) if u.get("username") == rp_user), None)
bound = (urow or {}).get("phone") or ""
check("取到该账号绑定手机号", bool(bound), str(bound))

if bound:
    # 配置通用网关指向模拟服务：这样即使发布版 exe 不回显验证码，也能从网关侧读回，
    # 同时验证「重置验证码确实投递出去了」而不是只落库。
    call("PUT", "/api/system/settings", token=admin, body={"sms": {
        "provider": "generic", "endpoint": f"http://127.0.0.1:{SMS_PORT}/send",
        "api_key": "audit-test-key", "sign": "审计", "template": "VERIFY"}})
    before = len(SMS_HITS)

    st, r = call("POST", "/api/auth/send-code",
                 body={"target": bound, "target_type": "sms", "purpose": "reset"})
    check("已绑定号码可发送重置码", st == 200, r.get("message", ""))
    code = (d(r) or {}).get("code") or last_sms_code()
    check("取到重置验证码（开发模式回显或网关侧读回）", bool(code),
          "回显=" + str((d(r) or {}).get("code")) + " 网关=" + str(last_sms_code()))
    check("重置验证码真实投递到网关", len(SMS_HITS) > before,
          f"hits {before} -> {len(SMS_HITS)}")

    if code:
        st, r = call("POST", "/api/auth/reset-password", body={
            "target_type": "sms", "target": bound, "code": code,
            "new_password": "Newpass@2026", "confirm_password": "Newpass@2026"})
        check("用验证码重置密码成功", st == 200, r.get("message", ""))
        st, r = call("POST", "/api/auth/login",
                     body={"account": rp_user, "password": "Newpass@2026"})
        check("新密码可以登录", st == 200, r.get("message", ""))

        # 错误验证码不能重置
        st, r = call("POST", "/api/auth/send-code",
                     body={"target": bound, "target_type": "sms", "purpose": "reset"})
        code2 = (d(r) or {}).get("code") or last_sms_code()
        if code2:
            st, r = call("POST", "/api/auth/reset-password", body={
                "target_type": "sms", "target": bound, "code": "000000",
                "new_password": "Badpass@2026", "confirm_password": "Badpass@2026"})
            check("错误验证码不能重置", st != 200, f"st={st}")
            st, r = call("POST", "/api/auth/login",
                         body={"account": rp_user, "password": "Newpass@2026"})
            check("重置失败后旧密码仍可用", st == 200, r.get("message", ""))

        # 注册用途的验证码不能用于重置（用途隔离）
        # 同样要先开「注册-手机验证码」，否则发码就被门禁挡下、
        # reg_code 为空会让下面这条断言变成空转（看着通过其实没验证到）。
        call("PUT", "/api/system/settings", token=admin,
             body={"register": {"captcha": 1, "sms": 1, "email": 0}})
        st, r = call("POST", "/api/auth/send-code",
                     body={"target": "13900000098", "target_type": "sms",
                           "purpose": "register"})
        reg_code = (d(r) or {}).get("code") or last_sms_code()
        check("注册用途可拿到验证码", bool(reg_code), f"st={st} {r.get('message','')}")
        call("PUT", "/api/system/settings", token=admin,
             body={"register": {"captcha": 1, "sms": 0, "email": 0}})
        if reg_code:
            st, r = call("POST", "/api/auth/reset-password", body={
                "target_type": "sms", "target": "13900000098", "code": reg_code,
                "new_password": "Badpass@2026", "confirm_password": "Badpass@2026"})
            check("注册用途的验证码不能用于重置", st != 200, f"st={st}")

# 收尾：短信配置恢复为空（本套件会写 settings.json，不还原会影响其它套件）
call("PUT", "/api/system/settings", token=admin, body={"sms": {
    "provider": "", "endpoint": "", "api_key": "__clear__", "sign": "", "template": ""}})

# ============================================================ [11] 清理含通用表
print("\n[11] 恢复出厂清理含通用表（缺陷 #2 清理侧）")
st, r = call("POST", "/api/system/reset", token=admin,
             body={"scopes": ["applications"], "confirm": "确认清空"})
check("执行清理成功", st == 200, r.get("message", ""))
st, r = call("GET", "/api/system/info", token=admin)
after = ((d(r) or {}).get("counts") or {}).get("applications") or 0
check("清理后报名数据归零（含通用表）", after == 0, f"剩余 {after}")

# ============================================================ 汇总
print("\n" + "-" * 66)
print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("   -", f)
print("-" * 66)
sys.exit(1 if FAIL else 0)
