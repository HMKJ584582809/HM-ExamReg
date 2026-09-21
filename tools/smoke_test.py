# -*- coding: utf-8 -*-
"""后端接口自测脚本（对应开发提示词第九节验收标准）。"""
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
PASS, FAIL = [], []


def upload(path, token, fields, filename, content):
    """multipart/form-data 上传（用于批量导入接口）。"""
    boundary = "----wb" + uuid.uuid4().hex
    buf = io.BytesIO()
    for k, v in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n"
                  .encode("utf-8"))
    buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\";"
              f" filename=\"{filename}\"\r\nContent-Type: application/vnd.openxmlformats-"
              f"officedocument.spreadsheetml.sheet\r\n\r\n".encode("utf-8"))
    buf.write(content)
    buf.write(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(BASE + path, data=buf.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        content_b = e.read()
        try:
            return e.code, json.loads(content_b.decode("utf-8")), dict(e.headers)
        except Exception:
            return e.code, {"raw": content_b[:200].decode("utf-8", "ignore")}, dict(e.headers)


def call(method, path, token=None, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            content = r.read()
            if raw:
                return r.status, content, dict(r.headers)
            return r.status, json.loads(content.decode("utf-8")), dict(r.headers)
    except urllib.error.HTTPError as e:
        content = e.read()
        try:
            return e.code, json.loads(content.decode("utf-8")), dict(e.headers)
        except Exception:
            return e.code, {"raw": content[:200].decode("utf-8", "ignore")}, dict(e.headers)


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("" if cond else f"  -> {detail}"))


def main():
    print("=" * 70)
    print("接口自测 ·", BASE)

    print("\n[1] 注册登录闭环")
    st, r, _ = call("POST", "/api/auth/login", body={"account": "admin", "password": "Admin@123"})
    check("管理员登录成功", st == 200 and r.get("code") == 0, r)
    admin = r["data"]["access_token"]

    st, r, _ = call("POST", "/api/auth/login", body={"account": "admin", "password": "wrong-pwd"})
    check("密码错误被拒绝", st == 401, r)

    st, r, _ = call("GET", "/api/exams")
    check("未登录访问受保护接口被拦截(401)", st == 401, r)

    st, r, _ = call("GET", "/api/auth/captcha")
    cap = r["data"]
    check("验证码接口返回图片", st == 200 and cap["image"].startswith("data:image/png;base64"), r)

    import random
    uname = f"test{random.randint(100000, 999999)}"
    st, r, _ = call("POST", "/api/auth/register", body={
        "username": uname, "phone": f"139{random.randint(10000000, 99999999)}",
        "email": f"{uname}@test.com", "real_name": "自测考生", "password": "Test@1234",
        "confirm_password": "Test@1234", "captcha_id": cap["captcha_id"],
        "captcha_code": cap.get("debug_code", "")})
    check("新用户注册成功", st == 200 and r.get("code") == 0, r)
    new_user = uname

    st, r, _ = call("POST", "/api/auth/register", body={
        "username": new_user, "phone": "13912345678", "password": "Test@1234",
        "confirm_password": "Test@1234", "captcha_id": "x", "captcha_code": "x"})
    check("重复用户名被拦截", st == 400, r)

    st, r, _ = call("POST", "/api/auth/login", body={"account": new_user, "password": "Test@1234"})
    check("新用户登录成功", st == 200 and r.get("code") == 0, r)
    cand = r["data"]["access_token"]
    cand_id = r["data"]["user"]["id"]

    st, r, _ = call("GET", "/api/auth/me", token=cand)
    check("获取当前用户信息", st == 200 and r["data"]["username"] == new_user, r)

    st, r, _ = call("POST", "/api/auth/change-password", token=cand, body={
        "old_password": "Test@1234", "new_password": "Test@5678",
        "confirm_password": "Test@5678"})
    check("修改密码成功", st == 200, r)
    st, r, _ = call("POST", "/api/auth/login", body={"account": new_user, "password": "Test@5678"})
    check("新密码可登录", st == 200, r)
    cand = r["data"]["access_token"]

    st, r, _ = call("POST", "/api/auth/login", body={"account": "reviewer", "password": "Reviewer@123"})
    rev = r["data"]["access_token"]
    check("审核员登录成功", st == 200, r)

    print("\n[2] 新建考试（按月开放）")
    st, r, _ = call("GET", "/api/exams", token=admin)
    exams = r["data"]["list"]
    check("管理员可见全部批次", st == 200 and r["data"]["total"] >= 5, r["data"].get("total"))
    comp = [e for e in exams if e["exam_type"] == "computer" and e["status"] == "open"][0]
    md = [e for e in exams if e["exam_type"] == "mandarin" and e["status"] == "open"][0]
    draft = [e for e in exams if e["status"] == "draft"][0]
    check("存在开放中的计算机批次", bool(comp))
    check("存在开放中的普通话批次", bool(md))

    st, r, _ = call("GET", "/api/exams", token=cand)
    check("考生仅可见开放中的批次",
          st == 200 and all(e["status"] == "open" and e["open_now"] for e in r["data"]["list"]), r)

    st, r, _ = call("POST", "/api/exams", token=admin, body={
        "exam_type": "computer", "exam_year": 2030, "exam_month": 3,
        "signup_start_at": "2030-03-01 00:00:00", "signup_end_at": "2030-03-20 23:59:59",
        "description": "自测批次", "status": "draft"})
    check("按月新建考试成功", st == 200 and "2030年3月" in r["data"]["name"], r)
    new_exam = r["data"]["id"]

    st, r, _ = call("PUT", f"/api/exams/{new_exam}", token=admin, body={"status": "open"})
    check("draft -> open 状态流转", st == 200 and r["data"]["status"] == "open", r)
    st, r, _ = call("PUT", f"/api/exams/{new_exam}", token=admin, body={"status": "draft"})
    check("非法状态流转被拒绝(open->draft)", st == 400, r)
    st, r, _ = call("PUT", f"/api/exams/{new_exam}", token=admin, body={"status": "closed"})
    check("open -> closed 状态流转", st == 200, r)
    st, r, _ = call("PUT", f"/api/exams/{new_exam}", token=admin, body={"status": "archived"})
    check("closed -> archived 状态流转", st == 200, r)
    st, r, _ = call("DELETE", f"/api/exams/{new_exam}", token=admin)
    check("非草稿批次禁止删除", st == 400, r)
    st, r, _ = call("DELETE", f"/api/exams/{draft['id']}", token=admin)
    check("草稿批次可删除", st == 200, r)

    st, r, _ = call("POST", "/api/exams", token=cand, body={
        "exam_type": "computer", "exam_year": 2031, "exam_month": 1,
        "signup_start_at": "2031-01-01 00:00:00", "signup_end_at": "2031-01-02 00:00:00"})
    check("考生无权限新建考试(403)", st == 403, r)

    print("\n[3] 字典接口")
    for path, key in [("/api/dicts/org", "机构"), ("/api/dicts/subject", "科目"),
                      ("/api/dicts/occupation", "职业"), ("/api/dicts/id-type", "证件类型"),
                      ("/api/dicts/region/tree", "省市区树")]:
        st, r, _ = call("GET", path, token=cand)
        check(f"字典-{key}加载", st == 200 and len(r["data"]) > 0, r)
    st, r, _ = call("GET", "/api/dicts/meta", token=cand)
    check("模板元信息：计算机13列表头", len(r["data"]["computer"]["headers"]) == 13, r)
    check("模板元信息：普通话20列表头", len(r["data"]["mandarin"]["headers"]) == 20, r)

    print("\n[4] 计算机类报名（13 字段 + 校验）")
    orgs = call("GET", "/api/dicts/org", token=cand)[1]["data"]
    subjects = call("GET", "/api/dicts/subject", token=cand)[1]["data"]
    base = {
        "exam_id": comp["id"], "org_code": orgs[0]["code"], "exam_site_code": "110101",
        "name": "自测考生", "gender": "男", "id_type": "1",
        "id_number": "320102199501011239", "subject": subjects[0], "school": "示例院校1",
        "class_name": "计算机2101", "education": "本科", "phone": "13800001111",
        "email": "test@exam.local", "address": "江苏省南京市鼓楼区1号"}

    bad = dict(base, phone="1380000")
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("手机号非11位被拦截", st == 400 and "手机" in r["message"], r)
    bad = dict(base, email="not-an-email")
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("Email 格式校验生效", st == 400, r)
    bad = dict(base, org_code="9999")
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("非法考试机构编码被拦截", st == 400, r)
    bad = dict(base, subject="不存在的科目")
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("非法报考科目被拦截", st == 400, r)

    # 身份证合法性校验（GB 11643：省份代码 + 出生日期 + 校验位）
    bad = dict(base, id_number="320102199501011230")   # 末位校验位错误（正确应为 9）
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("身份证校验位错误被拦截", st == 400 and "校验位" in r["message"], r["message"])
    bad = dict(base, id_number="990102199501011239")   # 省份代码 99 不存在
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("身份证省份代码无效被拦截", st == 400 and "省份" in r["message"], r["message"])
    bad = dict(base, id_number="320102199513011239")   # 出生月份 13
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("身份证出生日期无效被拦截", st == 400 and "出生日期" in r["message"], r["message"])
    bad = dict(base, id_number="32010219950101123")    # 仅 17 位
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("身份证位数不足被拦截", st == 400 and "18" in r["message"], r["message"])
    bad = dict(base, id_type="8", id_number="12345")   # 台湾证件长度不足
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("港澳台证件号格式被拦截", st == 400, r["message"])

    st, r, _ = call("POST", "/api/applications", token=cand, body=base)
    check("计算机报名提交成功(状态 pending)", st == 200 and r["data"]["audit_status"] == "pending", r)
    comp_app = r["data"]["id"]

    st, r, _ = call("POST", "/api/applications", token=cand, body=base)
    check("同批次重复报名被拦截", st == 400 and "重复" in r["message"], r)

    st, r, _ = call("GET", "/api/applications/mine", token=cand)
    check("我的报名可见新记录", st == 200 and any(x["id"] == comp_app for x in r["data"]), r)

    print("\n[5] 普通话报名（20 字段 + 三级联动）")
    tree = call("GET", "/api/dicts/region/tree", token=cand)[1]["data"]
    prov = [p for p in tree if p["cities"] and p["cities"][0]["counties"]][0]
    city = prov["cities"][0]
    county = city["counties"][0]
    occs = call("GET", "/api/dicts/occupation", token=cand)[1]["data"]
    mbase = {
        "exam_id": md["id"], "name": "自测考生", "gender": "2", "ethnicity": "汉族",
        "id_type": "1", "id_number": "320102199501011239", "occupation": occs[0],
        "employer": "自测单位", "phone": "13800001111", "student_no": "20220001",
        "class_name": "汉语言2101", "department": "文学院", "contact_address": "测试地址",
        "mail_address": "测试邮寄地址", "postcode": "210000",
        "birth_province": prov["province"], "birth_city": city["name"], "birth_county": county,
        "live_province": prov["province"], "live_city": city["name"], "live_county": county}
    st, r, _ = call("POST", "/api/applications", token=cand, body=mbase)
    check("普通话报名提交成功", st == 200, r)
    md_app = r["data"]["id"]

    bad = dict(mbase, birth_county="不存在的县区")
    bad["exam_id"] = comp["id"]
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("三级联动不匹配被拦截", st == 400, r)
    bad = dict(mbase, postcode="123")
    st, r, _ = call("POST", "/api/applications", token=cand, body=bad)
    check("邮编非6位被拦截", st == 400, r)

    print("\n[6] 信息审核")
    st, r, _ = call("GET", f"/api/applications?exam_id={comp['id']}&app_type=computer&audit_status=pending",
                    token=rev)
    check("审核列表按条件筛选", st == 200 and r["data"]["total"] >= 1, r["data"].get("total"))

    st, r, _ = call("PUT", f"/api/applications/{comp_app}/audit", token=rev,
                    body={"app_type": "computer", "action": "rejected", "comment": ""})
    check("驳回时审核意见必填", st == 400, r)

    st, r, _ = call("PUT", f"/api/applications/{comp_app}/audit", token=rev,
                    body={"app_type": "computer", "action": "returned", "comment": "请补充学历信息"})
    check("退回修改成功", st == 200 and r["data"]["audit_status"] == "returned", r)

    st, r, _ = call("GET", f"/api/applications/{comp_app}?app_type=computer", token=cand)
    check("考生端可见退回意见", st == 200 and r["data"]["audit_comment"] == "请补充学历信息", r)
    check("退回后可编辑", r["data"]["can_edit"] is True, r)

    st, r, _ = call("PUT", f"/api/applications/{comp_app}?app_type=computer", token=cand,
                    body=dict(base, education="硕士研究生"))
    check("退回后修改并重新提交", st == 200 and r["data"]["audit_status"] == "pending", r)

    st, r, _ = call("PUT", f"/api/applications/{comp_app}/audit", token=rev,
                    body={"app_type": "computer", "action": "approved", "comment": "信息无误"})
    check("审核通过成功", st == 200 and r["data"]["audit_status"] == "approved", r)

    st, r, _ = call("GET", f"/api/applications/{comp_app}?app_type=computer", token=cand)
    check("审核记录写入 audits 表", len(r["data"]["audits"]) >= 2, r["data"].get("audits"))

    st, r, _ = call("PUT", f"/api/applications/{md_app}/audit", token=cand,
                    body={"app_type": "mandarin", "action": "approved", "comment": "x"})
    check("考生无审核权限(403)", st == 403, r)

    pending = call("GET", f"/api/applications?exam_id={md['id']}&app_type=mandarin&audit_status=pending",
                   token=rev)[1]["data"]["list"]
    items = [{"app_type": "mandarin", "id": x["id"]} for x in pending[:3]]
    if items:
        st, r, _ = call("POST", "/api/applications/batch-audit", token=rev,
                        body={"items": items, "action": "approved", "comment": "批量通过"})
        check("批量审核生效", st == 200 and r["data"]["success"] == len(items), r)

    print("\n[7] 汇总导出")
    st, r, _ = call("GET", f"/api/export/preview?exam_id={comp['id']}&audit_status=approved", token=rev)
    check("导出预览：表头与官方模板一致",
          st == 200 and r["data"]["headers"][0] == "考试机构编码" and len(r["data"]["headers"]) == 13, r)
    st, r, _ = call("GET", f"/api/export/preview?exam_id={md['id']}&audit_status=approved", token=rev)
    check("普通话导出预览：20 列表头",
          st == 200 and len(r["data"]["headers"]) == 20 and r["data"]["headers"][0] == "考生姓名", r)

    # 关键字过滤：预览必须与导出接口一致。
    # 曾漏传 keyword，导致「预览不过滤、导出却过滤」，用户看到的就是搜索框无效。
    st, lr, _ = call("GET", f"/api/applications?exam_id={comp['id']}&page_size=1", token=rev)
    kw_name = lr["data"]["list"][0]["name"] if st == 200 and lr["data"]["list"] else ""
    qk = urllib.parse.quote(kw_name)
    st, r0, _ = call("GET", f"/api/export/preview?exam_id={comp['id']}&audit_status=all", token=rev)
    st, r1, _ = call("GET", f"/api/export/preview?exam_id={comp['id']}&audit_status=all"
                            f"&keyword={qk}", token=rev)
    st, r2, _ = call("GET", f"/api/export/preview?exam_id={comp['id']}&audit_status=all"
                            "&keyword=%E7%BB%9D%E4%B8%8D%E5%AD%98%E5%9C%A8XYZ123", token=rev)
    check("导出预览：关键字命中时条数收敛",
          st == 200 and bool(kw_name) and 0 < r1["data"]["total"] <= r0["data"]["total"], r1)
    check("导出预览：关键字无命中时为 0", st == 200 and r2["data"]["total"] == 0, r2)
    check("导出预览：回显 keyword（供前端提示过滤生效）", r1["data"].get("keyword") == kw_name, r1)

    st, content, headers = call("GET", f"/api/export/download?exam_id={comp['id']}&audit_status=all",
                                token=rev, raw=True)
    check("计算机导出文件下载成功", st == 200 and content[:2] == b"PK", st)
    open("/tmp/export_computer.xlsx", "wb").write(content)
    st, content, headers = call("GET", f"/api/export/download?exam_id={md['id']}&audit_status=all",
                                token=rev, raw=True)
    check("普通话导出文件下载成功", st == 200 and content[:2] == b"PK", st)
    open("/tmp/export_mandarin.xlsx", "wb").write(content)

    print("\n[8] 数据分析 / 大屏")
    st, r, _ = call("GET", "/api/analysis/summary", token=rev)
    check("汇总指标计算", st == 200 and r["data"]["total"] > 0 and 0 <= r["data"]["pass_rate"] <= 100, r)
    st, r, _ = call("GET", "/api/analysis/charts", token=rev)
    d = r["data"]
    check("图表数据完整", st == 200 and len(d["status_pie"]) == 4 and len(d["exam_bar"]) > 0
          and len(d["month_line"]) > 0 and len(d["gender_pie"]) > 0, list(d.keys()))
    st, r, _ = call("GET", "/api/analysis/charts?exam_id=" + str(comp["id"]), token=rev)
    check("图表按批次筛选联动", st == 200 and all(x["exam_id"] == comp["id"] for x in r["data"]["exam_bar"]), r)

    st, r, _ = call("GET", "/api/dashboard", token=rev)
    # 权限细化后审核员默认带 dashboard（原来只有管理员有），这里改为断言可访问
    check("审核员可访问大屏", st == 200, r)
    st, r, _ = call("GET", "/api/dashboard", token=admin)
    d = r["data"]
    check("大屏数据完整",
          st == 200 and d["kpi"]["total"] > 0 and len(d["trend"]) == 14
          and len(d["type_pie"]) == 2 and len(d["funnel"]) == 3, list(d.keys()))
    check("大屏漏斗数据一致", d["funnel"][0]["value"] >= d["funnel"][1]["value"] >= d["funnel"][2]["value"])

    print("\n[9] 用户管理（管理员开通审核员 / 管理员账号）")
    st, r, _ = call("GET", "/api/users/stats", token=admin)
    check("账号统计接口", st == 200 and r["data"]["_total"] > 0, r)

    st, r, _ = call("GET", "/api/users?page_size=100", token=admin)
    check("用户列表加载（按角色排序）", st == 200 and len(r["data"]["list"]) > 0, r["data"].get("total"))
    check("管理员排在最前", r["data"]["list"][0]["role"] == "admin", r["data"]["list"][0])

    st, r, _ = call("GET", "/api/users?role=reviewer", token=admin)
    check("按角色筛选", st == 200 and all(u["role"] == "reviewer" for u in r["data"]["list"]), r)

    st, r, _ = call("GET", "/api/users", token=cand)
    check("考生无权限访问用户管理(403)", st == 403, r)
    st, r, _ = call("GET", "/api/users", token=rev)
    check("审核员无权限访问用户管理(403)", st == 403, r)

    import random as _rd
    nu = f"rev{_rd.randint(100000, 999999)}"
    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": nu, "real_name": "新审核员", "phone": f"137{_rd.randint(10000000, 99999999)}",
        "email": f"{nu}@exam.local", "role": "reviewer"})
    check("开通审核员账号成功", st == 200 and r["data"]["role"] == "reviewer", r)
    new_uid = r["data"]["id"]
    init_pwd = r["data"]["initial_password"]
    check("返回初始密码", bool(init_pwd) and len(init_pwd) >= 8, init_pwd)

    st, r, _ = call("POST", "/api/auth/login", body={"account": nu, "password": init_pwd})
    check("新审核员可用初始密码登录", st == 200, r)
    new_rev = r["data"]["access_token"] if st == 200 else None
    if new_rev:
        st2, r2, _ = call("GET", "/api/applications?page_size=1", token=new_rev)
        check("新审核员具备审核权限", st2 == 200, r2)

    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": nu, "phone": "13712345678", "role": "reviewer"})
    check("重复用户名被拦截", st == 400, r)
    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": f"x{_rd.randint(10000, 99999)}", "phone": "111", "role": "reviewer"})
    check("非法手机号被拦截", st == 400, r)

    # 需求8：建号只填用户名 + 手机号（密码留空自动生成），姓名/邮箱都不是必填
    nu8 = f"u8{_rd.randint(100000, 999999)}"
    ph8 = f"138{_rd.randint(10000000, 99999999)}"
    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": nu8, "phone": ph8, "role": "candidate"})
    check("仅凭用户名 + 手机号即可建号（初始密码自动生成）",
          st == 200 and bool((r.get("data") or {}).get("initial_password")), r)
    if st == 200:
        st8, r8, _ = call("POST", "/api/auth/login", body={
            "account": nu8, "password": r["data"]["initial_password"]})
        check("自动生成的初始密码可直接登录", st8 == 200, r8)

    st, r, _ = call("PUT", f"/api/users/{new_uid}", token=admin, body={"status": 0})
    check("禁用账号成功", st == 200 and r["data"]["status"] == 0, r)
    st, r, _ = call("POST", "/api/auth/login", body={"account": nu, "password": init_pwd})
    check("禁用后无法登录(403)", st == 403, r)

    st, r, _ = call("PUT", f"/api/users/{new_uid}", token=admin, body={"status": 1, "role": "admin"})
    check("重新启用并调整为管理员", st == 200 and r["data"]["role"] == "admin", r)

    st, r, _ = call("POST", f"/api/users/{new_uid}/reset-password", token=admin, body={})
    check("重置密码返回新密码", st == 200 and r["data"]["new_password"], r)
    npwd = r["data"]["new_password"]
    st, r, _ = call("POST", "/api/auth/login", body={"account": nu, "password": npwd})
    check("新密码可登录", st == 200, r)

    me_id = call("GET", "/api/auth/me", token=admin)[1]["data"]["id"]
    st, r, _ = call("PUT", f"/api/users/{me_id}", token=admin, body={"status": 0})
    check("不能禁用自己的账号", st == 400, r)
    st, r, _ = call("PUT", f"/api/users/{me_id}", token=admin, body={"role": "candidate"})
    check("不能修改自己的角色", st == 400, r)

    st, r, _ = call("GET", f"/api/users/{new_uid}/applications", token=admin)
    check("查看用户报名记录", st == 200 and isinstance(r["data"]["list"], list), r)

    # ---------------------------------------------------------------
    print("\n[10] 记住我与会话有效期")
    st, r, _ = call("POST", "/api/auth/login", body={
        "account": "admin", "password": "Admin@123", "remember": True})
    check("记住我：发放 refresh_token", st == 200 and r["data"].get("refresh_token"), r)
    check("记住我：有效期 12 小时", r["data"]["expires_in"] == 12 * 3600, r["data"]["expires_in"])
    st, r, _ = call("POST", "/api/auth/login", body={
        "account": "admin", "password": "Admin@123", "remember": False})
    check("不记住我：不发放 refresh_token", st == 200 and not r["data"].get("refresh_token"), r)
    check("不记住我：有效期 2 小时", r["data"]["expires_in"] == 2 * 3600, r["data"]["expires_in"])

    # ---------------------------------------------------------------
    print("\n[11] 邮箱 / 手机验证码注册")
    # 注册验证方式受后台开关控制：默认只开图形验证码，短信/邮箱须管理员显式开启。
    # 这里先开再测，并在末尾复原（否则后面的用例会被这次改动带偏）。
    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"register": {"captcha": 1, "sms": 1, "email": 1}})
    check("开启短信 / 邮箱注册验证", st == 200, r)

    st, r, _ = call("GET", "/api/auth/verify-options")
    opts = r["data"]
    check("验证方式选项接口", st == 200 and "email" in opts and "sms" in opts, opts)
    check("开启后选项里 email 可用", opts["email"]["enabled"] is True, opts)

    _rd2 = __import__("random")
    mail = f"mail{_rd2.randint(100000, 999999)}@exam.local"
    st, r, _ = call("POST", "/api/auth/send-code", body={
        "target_type": "email", "target": mail, "purpose": "register"})
    check("邮箱验证码发送成功", st == 200 and r["data"]["target_masked"].startswith("ma"), r)
    code = r["data"].get("code")
    check("自测模式回显验证码", bool(code) and len(code) == 6, code)

    uname2 = f"code{_rd2.randint(100000, 999999)}"
    st, r, _ = call("POST", "/api/auth/register", body={
        "username": uname2, "phone": f"136{_rd2.randint(10000000, 99999999)}", "email": mail,
        "real_name": "验证码考生", "password": "Code@12345", "confirm_password": "Code@12345",
        "verify_type": "email", "code_target": mail, "code": code})
    check("邮箱验证码注册成功", st == 200 and r["data"]["username"] == uname2, r)

    st, r, _ = call("POST", "/api/auth/send-code", body={
        "target_type": "email", "target": mail, "purpose": "register"})
    check("已注册邮箱发送验证码被拦截", st == 400, r)

    st, r, _ = call("POST", "/api/auth/send-code", body={
        "target_type": "sms", "target": "111", "purpose": "register"})
    check("非法手机号发送验证码被拦截", st == 400, r)

    st, r, _ = call("POST", "/api/auth/register", body={
        "username": f"bad{_rd2.randint(100000, 999999)}", "phone": f"135{_rd2.randint(10000000, 99999999)}",
        "real_name": "错误验证码", "password": "Code@12345", "confirm_password": "Code@12345",
        "verify_type": "email", "code_target": mail, "code": "000000"})
    check("错误验证码注册被拦截", st == 400, r)

    # 关掉短信/邮箱后再发码应被拒（后台开关是硬门禁，不是只影响前端显示）
    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"register": {"captcha": 1, "sms": 0, "email": 0}})
    check("关闭短信 / 邮箱注册验证", st == 200, r)
    st, r, _ = call("POST", "/api/auth/send-code", body={
        "target_type": "email", "target": f"off{_rd2.randint(1000, 9999)}@exam.local",
        "purpose": "register"})
    check("关闭后邮箱发码被拒 403", st == 403, r)
    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"register": {"captcha": 1, "sms": 1, "email": 1}})
    check("复原：重新开启短信 / 邮箱", st == 200, r)

    # ---------------------------------------------------------------
    print("\n[12] 班主任角色与多班级数据范围")
    st, r, _ = call("GET", "/api/users/permission-groups", token=admin)
    check("权限组字典接口", st == 200 and len(r["data"]["roles"]) == 5, r)
    roles = {x["key"]: x for x in r["data"]["roles"]}
    check("班主任为管理级别", roles["head_teacher"]["manage_level"] is True, roles["head_teacher"])
    check("班主任带数据范围标记", roles["head_teacher"]["scoped"] is True, roles["head_teacher"])
    check("审核员非管理级别", roles["reviewer"]["manage_level"] is False, roles["reviewer"])
    check("二级学院审核非管理级别", roles["college_reviewer"]["manage_level"] is False,
          roles["college_reviewer"])
    check("二级学院审核带数据范围标记", roles["college_reviewer"]["scoped"] is True,
          roles["college_reviewer"])

    st, r, _ = call("POST", "/api/auth/login", body={"account": "teacher", "password": "Teacher@123"})
    check("班主任账号可登录", st == 200 and r["data"]["user"]["role"] == "head_teacher", r)
    teacher = r["data"]["access_token"]
    check("班主任数据范围=本班级", r["data"]["user"]["scope"] == "scope_class", r["data"]["user"])
    teacher_classes = [c for c in (r["data"]["user"].get("classes") or "").replace("，", ",").split(",") if c]
    check("班主任支持多班级", len(teacher_classes) >= 2, teacher_classes)

    st, r, _ = call("POST", "/api/auth/login", body={"account": "counselor", "password": "Counselor@123"})
    check("辅导员角色已删除：counselor 无法登录", st != 200, r)

    st, r, _ = call("GET", "/api/applications?page_size=200", token=teacher)
    tl = r["data"]["list"]
    check("班主任可查看报名列表", st == 200 and len(tl) > 0, r["data"].get("total"))
    check("班主任仅见其管理班级数据",
          all(x.get("class_name") in teacher_classes for x in tl),
          [x.get("class_name") for x in tl[:8]])
    check("列表返回数据范围标签", "本班级" in (r["data"].get("scope_label") or ""), r["data"].get("scope_label"))
    check("班主任无审核权限", r["data"].get("can_audit") is False, r["data"].get("can_audit"))

    st, r, _ = call("GET", "/api/analysis/summary", token=teacher)
    t_total = r["data"]["total"]
    st, r, _ = call("GET", "/api/analysis/summary", token=admin)
    a_total = r["data"]["total"]
    check("班主任统计量小于全校", st == 200 and 0 < t_total < a_total, f"{t_total} < {a_total}")

    st, r, _ = call("PUT", "/api/applications/1/audit", token=teacher,
                    body={"app_type": "computer", "action": "approved", "comment": ""})
    check("班主任审核被拒绝(403)", st == 403, r)
    st, r, _ = call("GET", "/api/users", token=teacher)
    check("班主任无权访问用户管理(403)", st == 403, r)
    st, r, _ = call("GET", "/api/dashboard", token=teacher)
    check("班主任可访问大屏", st == 200, r)
    st, r, _ = call("GET", "/api/export/exam-options", token=teacher)
    check("班主任可访问导出", st == 200, r)
    st, r, _ = call("GET", "/api/applications/import-batches", token=teacher)
    check("班主任可访问批量导入", st == 200, r)
    st, r, _ = call("GET", "/api/applications/import-batches", token=rev)
    check("审核员无批量导入权限(403)", st == 403, r)

    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": f"ht{_rd2.randint(100000, 999999)}", "phone": f"134{_rd2.randint(10000000, 99999999)}",
        "role": "head_teacher", "college": "计算机学院"})
    check("班主任未填班级被拦截", st == 400, r)
    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": f"ht{_rd2.randint(100000, 999999)}", "phone": f"132{_rd2.randint(10000000, 99999999)}",
        "role": "head_teacher", "college": "计算机学院", "classes": "计科2101,计科2102"})
    check("班主任多班级可正常开通", st == 200 and "classes" in r["data"], r["data"])
    st, r, _ = call("POST", "/api/users", token=admin, body={
        "username": f"co{_rd2.randint(100000, 999999)}", "phone": f"133{_rd2.randint(10000000, 99999999)}",
        "role": "counselor"})
    check("辅导员角色已删除：无法开通 counselor", st == 400, r)

    # ---------------------------------------------------------------
    print("\n[13] 后台权限组核验")
    st, r, _ = call("GET", "/api/users/verify", token=admin)
    check("权限组核验接口", st == 200 and r["data"]["checked"] > 0, r["data"].get("checked"))
    item = r["data"]["list"][0]
    check("核验结果含权限组与范围",
          "perms" in item and "scope_label" in item and "issues" in item, item)
    st, r, _ = call("GET", "/api/users/verify?only_issues=1", token=admin)
    check("只看异常账号筛选生效",
          st == 200 and all(x["issues"] for x in r["data"]["list"]), r["data"].get("issue_count"))
    st, r, _ = call("GET", "/api/users/verify", token=rev)
    check("审核员无权核验权限组(403)", st == 403, r)

    # ---------------------------------------------------------------
    print("\n[13.5] 单独授权：按功能开关编辑用户权限")
    # 取一个审核员账号（角色默认 audit+export+analysis）
    st, r, _ = call("GET", "/api/users?role=reviewer&page_size=20", token=admin)
    rv = [u for u in r["data"]["list"] if u["username"] != "admin"][0]
    ruid = rv["id"]
    check("默认沿用角色权限（未单独授权）", rv.get("custom_perms") is False, rv.get("perms"))

    st, r, _ = call("GET", f"/api/users/{ruid}/perms", token=admin)
    groups = r["data"].get("groups") or []
    # 权限细化后功能组从 8 个扩到 14 个（新增字典维护 / 系统维护 / 证件照 /
    # 实名 / 实名审核 / AI 助手），这里按当前实际数量断言，避免以后加组又要改
    check("功能开关接口可读取", st == 200 and len(groups) >= 14, len(groups))
    check("每个开关都带说明", all(g.get("desc") for g in groups),
          [g["key"] for g in groups if not g.get("desc")])
    role_reviewer_perms = {"audit", "export", "analysis", "dashboard",
                           "realname_audit", "idphoto", "ai"}
    check("开关按角色默认回显",
          {g["key"] for g in groups if g["on"]} == role_reviewer_perms,
          [g["key"] for g in groups if g["on"]])

    # 关掉 export、加上 dashboard → 单独授权
    st, r, _ = call("PUT", f"/api/users/{ruid}/perms", token=admin,
                    body={"perms": ["audit", "analysis", "dashboard"]})
    check("单独授权保存成功", st == 200 and r["data"]["perms"] == ["audit", "analysis", "dashboard"],
          r["data"].get("perms"))
    check("标记为已单独授权", r["data"]["custom_perms"] is True, r["data"].get("custom_perms"))

    # 后端强制生效：该账号已无 export 权限
    st, r, _ = call("POST", "/api/auth/login",
                    body={"account": rv["username"], "password": "Reviewer@123"})
    rvtk = (r.get("data") or {}).get("access_token")
    check("被授权账号可登录", bool(rvtk), r.get("message"))
    st, r, _ = call("GET", "/api/export/download?exam_id=1", token=rvtk)
    check("关闭导出后访问导出接口被拦截(403)", st == 403, st)

    # 全部关闭：有意为之，不回退角色默认
    st, r, _ = call("PUT", f"/api/users/{ruid}/perms", token=admin, body={"perms": []})
    check("全部关闭后生效权限为空", st == 200 and r["data"]["perms"] == [], r["data"].get("perms"))
    check("全部关闭仍标记为单独授权", r["data"]["custom_perms"] is True,
          r["data"].get("custom_perms"))

    # 传回角色默认功能项 → 清除单独授权，恢复跟随角色
    st, r, _ = call("PUT", f"/api/users/{ruid}/perms", token=admin,
                    body={"perms": sorted(role_reviewer_perms)})
    check("恢复角色默认成功", st == 200 and r["data"]["custom_perms"] is False,
          r["data"].get("custom_perms"))
    check("恢复后含数据范围 scope_all", "scope_all" in r["data"]["perms"], r["data"].get("perms"))

    # 边界守卫
    st, r, _ = call("PUT", f"/api/users/{ruid}/perms", token=admin, body={"perms": ["hack"]})
    check("非法功能项被拦截", st == 400 and "不合法" in r["message"], r["message"])
    adm = [u for u in call("GET", "/api/users?page_size=50", token=admin)[1]["data"]["list"]
           if u["username"] == "admin"][0]
    st, r, _ = call("PUT", f"/api/users/{adm['id']}/perms", token=admin, body={"perms": ["audit"]})
    check("管理员不能改自己的权限", st == 400 and "不能修改自己" in r["message"], r["message"])
    st, r, _ = call("GET", f"/api/users/{ruid}/perms", token=rev)
    check("非管理员无权读取功能开关(403)", st == 403, r)

    # ---------------------------------------------------------------
    print("\n[13.6] 数据范围四档与二级学院审核")

    st, r, _ = call("GET", "/api/users/options", token=admin)
    roles = {x["key"]: x for x in r["data"]}
    check("角色清单含二级学院审核", "college_reviewer" in roles, list(roles))
    check("二级学院审核默认范围为本院系",
          roles.get("college_reviewer", {}).get("scope") == "scope_college",
          roles.get("college_reviewer", {}).get("scope"))
    check("二级学院审核可选范围不超过本院系",
          [s["key"] for s in roles.get("college_reviewer", {}).get("allowed_scopes", [])]
          == ["scope_class", "scope_grade", "scope_college"],
          roles.get("college_reviewer", {}).get("allowed_scopes"))
    check("班主任可选范围仅到本班级",
          [s["key"] for s in roles.get("head_teacher", {}).get("allowed_scopes", [])]
          == ["scope_class"],
          roles.get("head_teacher", {}).get("allowed_scopes"))

    cname = "scope" + str(random.randint(10000, 99999))
    pwd = "Scope@1234"
    st, r, _ = call("POST", "/api/users", token=admin,
                    body={"username": cname, "real_name": "二级学院审核测试",
                          "phone": f"137{random.randint(10000000, 99999999)}",
                          "role": "college_reviewer", "password": pwd, "college": "计算机学院"})
    check("创建二级学院审核账号成功", st == 200, r)
    cuid = r["data"]["id"] if st == 200 else 0
    st, r, _ = call("POST", "/api/auth/login", body={"account": cname, "password": pwd})
    ctok = r["data"]["access_token"] if st == 200 else None
    check("二级学院审核账号可登录", bool(ctok), r)
    st, r, _ = call("GET", "/api/auth/me", token=ctok)
    check("生效范围为本院系", r["data"]["scope"] == "scope_college", r["data"].get("scope"))
    check("范围标签含院系名", "计算机学院" in r["data"]["scope_label"],
          r["data"].get("scope_label"))
    check("无批量导入权限", "import" not in r["data"]["perms"], r["data"].get("perms"))

    st, r, _ = call("GET", "/api/applications?app_type=computer&page_size=1", token=admin)
    all_total = r["data"]["total"] if st == 200 else -1
    st, r, _ = call("GET", "/api/applications?app_type=computer&page_size=1", token=ctok)
    sub_total = r["data"]["total"] if st == 200 else -1
    check("二级学院审核看到的数据少于全校",
          0 <= sub_total < all_total, f"本院系={sub_total} 全校={all_total}")
    st, r, _ = call("GET", "/api/applications?app_type=computer&page_size=50", token=ctok)
    check("每行都是本院系",
          all((x.get("school") or "") == "计算机学院" for x in r["data"]["list"]),
          {x.get("school") for x in r["data"]["list"]})

    st, r, _ = call("PUT", f"/api/users/{cuid}/perms", token=admin,
                    body={"perms": ["audit", "export", "analysis"], "scope": "scope_all"})
    check("把范围放宽到全校被拒（防提权）", st == 400, r)
    st, r, _ = call("PUT", f"/api/users/{cuid}/perms", token=admin,
                    body={"perms": ["audit", "export", "analysis"], "scope": "scope_class"})
    check("把范围收窄到本班级成功", st == 200 and r["data"]["custom_scope"] is True,
          r["data"].get("custom_scope"))
    st, r, _ = call("GET", "/api/applications?app_type=computer&page_size=1", token=ctok)
    check("收窄后可见数据进一步减少",
          r["data"]["total"] < sub_total, f"{r['data']['total']} vs {sub_total}")
    st, r, _ = call("PUT", f"/api/users/{cuid}/perms", token=admin,
                    body={"perms": ["audit", "export", "analysis"], "scope": ""})
    check("清空范围即恢复角色默认", st == 200 and r["data"]["custom_scope"] is False,
          r["data"].get("custom_scope"))
    st, r, _ = call("PUT", f"/api/users/{cuid}/perms", token=admin,
                    body={"perms": ["audit"], "scope": "scope_xxx"})
    check("非法范围值被拒", st == 400, r)

    # ---------------------------------------------------------------
    print("\n[14] 批量导入（管理权限级别）")
    import openpyxl

    st, r, _ = call("GET", "/api/exams?exam_type=computer&page_size=50", token=admin)
    comp_exams = [e for e in r["data"]["list"] if e["exam_type"] == "computer"]
    check("存在计算机类批次可供导入", len(comp_exams) > 0, len(comp_exams))
    cexam = comp_exams[0]["id"]

    st, r, _ = call("GET", "/api/export/preview?exam_id=%d&audit_status=all" % cexam, token=admin)
    headers = r["data"]["headers"]
    check("取得官方模板表头（13 列）", len(headers) == 13, headers)

    orgs = call("GET", "/api/dicts/org", token=admin)[1]["data"]
    subjects = call("GET", "/api/dicts/subject", token=admin)[1]["data"]
    org_code = orgs[0]["code"] if isinstance(orgs[0], dict) else orgs[0]
    subject = subjects[0] if isinstance(subjects[0], str) else subjects[0].get("name")

    def build_xlsx(rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(headers)
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # 生成「校验位合法」的身份证号（江苏 32 / 1995-01-01 / 顺序号 seq）
    _W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    _CC = ['1', '0', 'X', '9', '8', '7', '6', '5', '4', '3', '2']

    def mk_id(seq):
        pre = "32010219950101%03d" % seq
        return pre + _CC[sum(int(pre[i]) * _W[i] for i in range(17)) % 11]

    rows = [[org_code, "100101", f"导入考生{i}", "男", "1",
             mk_id(i), subject, "计算机学院", "计算机2101", "本科",
             f"138{i:08d}"[:11], f"imp{i}@exam.local", "江苏省南京市鼓楼区1号"]
            for i in range(1, 21)]
    rows.append([org_code, "100101", "错误科目考生", "男", "1",
                 mk_id(99), "不存在的科目", "计算机学院", "计算机2101", "本科",
                 "13800000099", "", ""])

    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "1"},
                      "import-test.xlsx", build_xlsx(rows))
    check("批量导入接口返回成功", st == 200 and r["code"] == 0, r)
    d = r["data"]
    check("导入总行数 21", d["total"] == 21, d["total"])
    check("导入成功 20 条", d["success"] == 20, d["success"])
    check("导入失败 1 条", d["failed"] == 1, d["failed"])
    check("失败原因定位到科目", "科目" in d["errors"][0]["reason"], d["errors"])
    check("自动创建考生账号", d["created_account_total"] == 20, d["created_account_total"])
    check("返回导入范围标签", bool(d["scope_label"]), d["scope_label"])

    st, r, _ = call("GET", "/api/applications/import-batches", token=admin)
    check("导入批次记录可查询", st == 200 and r["data"]["total"] >= 1, r["data"].get("total"))
    batch_id = r["data"]["list"][0]["id"]

    st, r, hdrs = call("GET", f"/api/applications/import-batches/{batch_id}/errors",
                       token=admin, raw=True)
    check("失败明细可下载", st == 200 and len(r) > 2000, len(r))

    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "1"},
                      "import-dup.xlsx", build_xlsx(rows[:3]))
    check("重复导入被逐行拦截", st == 200 and r["data"]["success"] == 0, r["data"])
    check("重复导入提示已存在", "已存在" in r["data"]["errors"][0]["reason"],
          r["data"]["errors"])

    # 班主任导入：班级必须在其管理班级内（不再强行改写）
    rows_t = [[org_code, "100101", f"班级导入{i}", "女", "1",
               mk_id(500 + i), subject, "计算机学院", "计算机2101", "本科",
               f"137{i:08d}"[:11], "", ""] for i in range(1, 6)]
    st, r, _ = upload("/api/applications/import", teacher,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "1"},

                      "teacher-import.xlsx", build_xlsx(rows_t))
    check("班主任可批量导入（班级在管理范围内）", st == 200 and r["data"]["success"] == 5, r["data"])
    st, r, _ = call("GET", "/api/applications?exam_id=%d&keyword=%s&page_size=50"
                    % (cexam, urllib.parse.quote("班级导入")), token=teacher)
    check("班主任导入数据归入对应班级",
          st == 200 and all(x["class_name"] == "计算机2101" for x in r["data"]["list"]),
          [x["class_name"] for x in r["data"]["list"][:3]])
    # 越界班级被拒
    rows_bad = [[org_code, "100101", "越界考生", "女", "1", mk_id(999),
                 subject, "计算机学院", "其他班级", "本科", "13700000999", "", ""]]
    st, r, _ = upload("/api/applications/import", teacher,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "1"},
                      "teacher-bad.xlsx", build_xlsx(rows_bad))
    check("班主任越界班级导入被拒",
          st == 200 and r["data"]["success"] == 0 and r["data"]["failed"] == 1, r["data"])
    check("越界原因定位到班级范围", "不在您的管理范围" in r["data"]["errors"][0]["reason"],
          r["data"]["errors"])

    st, r, hdrs = call("GET", f"/api/applications/import-template?exam_id={cexam}",
                       token=admin, raw=True)
    check("导入模板可下载", st == 200 and len(r) > 3000, len(r))
    st, r, _ = call("GET", f"/api/applications/import-template?exam_id={cexam}", token=rev)
    check("审核员无导入模板权限(403)", st == 403, r)

    # ---------------------------------------------------------------
    print("\n[14.5] 智能导入：自定义表头识别与数据自动转换")

    def custom_xlsx(hdr, rows):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(hdr)
        for row in rows:
            ws.append(row)
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()

    # 故意使用「花名册」式表头：乱序、有多余列、值为非标准写法
    rost_hdr = ["序号", "学生姓名", "性别", "身份证号", "联系电话",
                "学院", "专业班级", "报考科目", "文化程度"]
    rost_rows = [
        [1, "花名册甲", "M", " 110101199001011405 ", "138 0013 9001",
         "计算机学院", "计算机2101", subject, "大学本科"],
        [2, "花名册乙", "女", "110101199203051421", "+86 138-0013-9002",
         "计算机学院", "计算机2101", subject, "本科"],
        [3, "花名册丙", "1", "110101199305063431", 13800139003.0,
         "计算机学院", "软件工程2202", subject, "专科"],
    ]
    st, r, _ = upload("/api/applications/import/preview", admin, {"exam_id": str(cexam)},
                      "roster.xlsx", custom_xlsx(rost_hdr, rost_rows))
    check("自定义表头可预览", st == 200, r)
    pv = r.get("data", {})
    mp = {m["header"]: (m["field"], m["confidence"]) for m in pv.get("mapping", [])}
    check("识别 学生姓名→姓名", mp.get("学生姓名", (None,))[0] == "name", mp.get("学生姓名"))
    check("识别 身份证号→证件号码", mp.get("身份证号", (None,))[0] == "id_number",
          mp.get("身份证号"))
    check("识别 联系电话→手机号码", mp.get("联系电话", (None,))[0] == "phone", mp.get("联系电话"))
    check("识别 学院→院校", mp.get("学院", (None,))[0] == "school", mp.get("学院"))
    check("识别 专业班级→班级", mp.get("专业班级", (None,))[0] == "class_name", mp.get("专业班级"))
    check("识别 文化程度→学历", mp.get("文化程度", (None,))[0] == "education", mp.get("文化程度"))
    check("多余列不误识别", mp.get("序号", (None,))[0] is None, mp.get("序号"))
    check("预览返回未识别列", "序号" in (pv.get("unmapped_headers") or []),
          pv.get("unmapped_headers"))
    check("预览返回总行数", pv.get("total_rows") == 3, pv.get("total_rows"))
    pr = (pv.get("preview_rows") or [{}])[0]
    check("样例转换：性别 M→男", pr.get("gender") == "男", pr.get("gender"))
    check("样例转换：手机号去空格", pr.get("phone") == "13800139001", pr.get("phone"))
    check("样例转换：大学本科→本科", pr.get("education") == "本科", pr.get("education"))
    check("样例转换：证件号去空格",
          pr.get("id_number") == "110101199001011405", pr.get("id_number"))

    # 自动识别 + 整批默认值（表里没有考试机构编码/考点编码列）
    dv = json.dumps({"org_code": org_code, "exam_site_code": "100101"})
    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved",
                       "create_accounts": "1", "defaults": dv},
                      "roster.xlsx", custom_xlsx(rost_hdr, rost_rows))
    check("自定义表头导入成功（默认值补齐必填列）",
          st == 200 and r["data"]["success"] == 3, r.get("data"))

    st, r, _ = call("GET", "/api/applications?exam_id=%d&keyword=%s&page_size=50"
                    % (cexam, urllib.parse.quote("花名册")), token=admin)
    lst = r["data"]["list"]
    check("入库：性别已转换（M/1→男，女→女）",
          sorted(x["gender"] for x in lst) == sorted(["男", "男", "女"]),
          [x["gender"] for x in lst])
    check("入库：手机号已清洗", all(x["phone"].isdigit() and len(x["phone"]) == 11 for x in lst),
          [x["phone"] for x in lst])
    check("入库：机构编码来自默认值", all(x["org_code"] == org_code for x in lst),
          [x["org_code"] for x in lst])

    # 手工映射覆盖：把「学院」列改判为班级
    ov = json.dumps({"0": "name", "1": "gender", "2": "id_number", "3": "phone",
                     "4": "class_name", "5": "subject"})
    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "1",
                       "mapping": ov,
                       "defaults": json.dumps({"org_code": org_code, "exam_site_code": "100101",
                                               "school": "计算机学院", "education": "本科"})},
                      "override.xlsx",
                      custom_xlsx(["学生姓名", "性别", "身份证号", "联系电话", "学院", "报考科目"],
                                  [["覆盖考生", "女", "110101199001011501", "13800139011",
                                    "软件工程2202", subject]]))
    check("手工映射覆盖导入成功", st == 200 and r["data"]["success"] == 1, r.get("data"))
    st, r, _ = call("GET", "/api/applications?exam_id=%d&keyword=%s&page_size=10"
                    % (cexam, urllib.parse.quote("覆盖考生")), token=admin)
    one = (r["data"]["list"] or [{}])[0]
    check("映射覆盖生效（学院列→班级）", one.get("class_name") == "软件工程2202", one)
    check("默认值补齐院校", one.get("school") == "计算机学院", one)

    # 缺必填且未给默认值：应返回中文提示，而不是英文堆栈
    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved", "create_accounts": "0"},
                      "nodefault.xlsx",
                      custom_xlsx(["学生姓名", "性别", "身份证号", "联系电话", "报考科目"],
                                  [["缺字段", "男", "110101199001011608", "13800139021", subject]]))
    reason = (r.get("data", {}).get("errors") or [{}])[0].get("reason", "")
    check("缺失必填返回中文提示", "缺少必需字段" in reason, reason)

    # 未知机构编码（如用户自带单位编码 6501）：批量导入应自动登记，而非报错
    unknown_org = "6501"
    dv2 = json.dumps({"org_code": unknown_org, "exam_site_code": "650102",
                     "school": "计算机学院", "class_name": "计算机2101", "education": "本科"})
    st, r, _ = upload("/api/applications/import", admin,
                      {"exam_id": str(cexam), "audit_status": "approved",
                       "create_accounts": "1", "defaults": dv2},
                      "unknown-org.xlsx",
                      custom_xlsx(["学生姓名", "性别", "身份证号", "联系电话", "报考科目"],
                                  [["未知机构考生", "男", "110101199001011704", "13800139031", subject]]))
    check("未知机构编码批量导入自动登记并成功",
          st == 200 and r["data"]["success"] == 1, r.get("data"))
    st, r, _ = call("GET", "/api/dicts/org", token=admin)
    check("未知机构编码已自动登记到字典",
          any(o["code"] == unknown_org for o in r["data"]),
          [o["code"] for o in r["data"]][:6])

    # 官方模板表头必须逐列识别正确，且**不能被新加的「年级」虚拟字段抢走**
    # （grade 只为「本年级」范围服务，官方模板没有这一列）
    official_comp = ["考试机构编码", "考点编码", "姓名", "性别", "证件类型", "证件号码",
                     "报考科目", "就读或者毕业院校", "班级", "学历", "手机号码",
                     "Email", "通讯地址"]
    want_comp = ["org_code", "exam_site_code", "name", "gender", "id_type", "id_number",
                 "subject", "school", "class_name", "education", "phone", "email",
                 "address"]
    st, r, _ = upload("/api/applications/import/preview", admin,
                      {"exam_id": str(cexam)}, "official-comp.xlsx",
                      custom_xlsx(official_comp, [["1001", "100101", "官方模板甲", "男", "1",
                                                   "110101199001011810", subject, "计算机学院",
                                                   "计算机2101", "本科", "13800139041",
                                                   "a@b.com", "某地址"]]))
    check("官方模板（计算机）可预览", st == 200, r.get("message"))
    mp = {m["header"]: m["field"] for m in (r.get("data") or {}).get("mapping", [])}
    check("官方模板 13 列全部识别到位",
          [mp.get(h) for h in official_comp] == want_comp,
          [mp.get(h) for h in official_comp])
    check("官方模板没有被误判出「年级」列",
          "grade" not in mp.values(), [k for k, v in mp.items() if v == "grade"])

    official_md = ["考生姓名", "考生性别", "考生民族", "证件类型", "证件编号", "从事职业",
                   "所在单位", "联系电话", "考生学号", "考生班级", "考生院系", "联系地址",
                   "邮寄地址", "邮政编码", "出生所在省", "出生所在城市", "出生所在县(区)",
                   "现居住省", "现居住城市", "现居住县(区)"]
    want_md = ["name", "gender", "ethnicity", "id_type", "id_number", "occupation",
               "employer", "phone", "student_no", "class_name", "department",
               "contact_address", "mail_address", "postcode", "birth_province",
               "birth_city", "birth_county", "live_province", "live_city", "live_county"]
    occs2 = call("GET", "/api/dicts/occupation", token=admin)[1]["data"]
    occ = occs2[0] if isinstance(occs2[0], str) else occs2[0].get("name")
    st, r, _ = upload("/api/applications/import/preview", admin,
                      {"exam_id": str(md["id"])}, "official-md.xlsx",
                      custom_xlsx(official_md, [["官方模板乙", "女", "汉族", "1",
                                                 "110101199001011828", occ, "某单位",
                                                 "13800139042", "20220001", "汉语2301",
                                                 "文学院", "地址甲", "地址乙", "210000",
                                                 "江苏省", "南京市", "鼓楼区",
                                                 "江苏省", "南京市", "鼓楼区"]]))
    check("官方模板（普通话）可预览", st == 200, r.get("message"))
    mp2 = {m["header"]: m["field"] for m in (r.get("data") or {}).get("mapping", [])}
    check("官方模板 20 列全部识别到位",
          [mp2.get(h) for h in official_md] == want_md,
          [mp2.get(h) for h in official_md])
    check("普通话模板没有被误判出「年级」列",
          "grade" not in mp2.values(), [k for k, v in mp2.items() if v == "grade"])

    # 带「年级」列时则应识别为 grade，且导入后落到考生账号上
    st, r, _ = upload("/api/applications/import/preview", admin,
                      {"exam_id": str(cexam)}, "with-grade.xlsx",
                      custom_xlsx(["姓名", "身份证号", "联系电话", "报考科目", "班级", "年级"],
                                  [["带年级考生", "110101199001011836", "13800139043",
                                    subject, "计算机2101", "2022级"]]))
    mp3 = {m["header"]: m["field"] for m in (r.get("data") or {}).get("mapping", [])}
    check("自定义表的「年级」列被识别为 grade", mp3.get("年级") == "grade", mp3)

    # ---------------------------------------------------------------
    print("\n[15] 分析维度补充与深链兼容")
    st, r, _ = call("GET", "/api/analysis/charts", token=admin)
    d = r["data"]
    check("新增考点维度", "site_rank" in d, list(d.keys()))
    check("新增考试机构维度", "org_rank" in d, list(d.keys()))
    check("新增出生地 省→市", "birth_tree" in d and isinstance(d["birth_tree"], list), d.get("birth_tree"))
    check("新增现居住地 省→市", "live_tree" in d, list(d.keys()))
    check("新增班级维度", "class_rank" in d, list(d.keys()))
    check("新增院系维度", "dept_rank" in d, list(d.keys()))
    check("新增民族维度", "ethnicity_bar" in d, list(d.keys()))
    check("返回数据范围标签", bool(d.get("scope_label")), d.get("scope_label"))
    st, r, _ = call("GET", "/api/analysis/dimensions", token=admin)
    check("分析维度字典接口", st == 200 and len(r["data"]["dimensions"]) >= 14,
          len(r["data"].get("dimensions", [])))

    st, r, _ = call("GET", "/api/applications/1", token=admin)
    check("详情接口可不传 app_type（深链）", st == 200 and r["data"].get("app_type"), r)

    # ---------------------------------------------------------------
    print("\n[16] 报名默认值与前后端必填一致性")
    # 考生没有「系统设置」权限，默认值只能随批次详情下发
    st, r, _ = call("GET", f"/api/exams/{md['id']}", token=admin)
    d = r["data"]
    check("批次详情下发报名默认值（出厂为空，由部署方自行配置）",
          "employer" in (d.get("defaults") or {}) and
          (d.get("defaults") or {}).get("employer") == "", d.get("defaults"))
    fields = {f["key"]: f for f in (d.get("fields") or [])}
    check("所在单位在字段元信息里必填", fields.get("employer", {}).get("required") is True,
          fields.get("employer"))
    check("所在单位标记为系统必填",
          fields.get("employer", {}).get("system_required") is True, fields.get("employer"))
    check("考生民族同样标注必填（后端一直强制）",
          fields.get("ethnicity", {}).get("required") is True, fields.get("ethnicity"))

    st, r, _ = call("GET", "/api/system/settings", token=admin)
    check("系统设置返回 apply 段（出厂默认不预填单位名）",
          (r["data"].get("apply") or {}).get("employer_default") == "",
          r["data"].get("apply"))

    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"apply": {"employer_default": "临时默认单位"}})
    check("可修改所在单位默认值",
          st == 200 and (r["data"].get("apply") or {}).get("employer_default") == "临时默认单位",
          r["data"].get("apply"))
    st, r, _ = call("GET", f"/api/exams/{md['id']}", token=admin)
    check("默认值改动后批次详情同步",
          (r["data"].get("defaults") or {}).get("employer") == "临时默认单位",
          r["data"].get("defaults"))

    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"apply": {"employer_default": "x" * 101}})
    check("默认值超长被拒绝", st == 400, r)
    st, r, _ = call("PUT", "/api/system/settings", token=admin,
                    body={"apply": {"employer_default": ""}})
    check("可把默认值恢复为空（不预填）",
          st == 200 and (r["data"].get("apply") or {}).get("employer_default") == "",
          r["data"].get("apply"))

    # ---------------------------------------------------------------
    print("\n[17] 二级学院 / 部门字典与账号部门")
    st, r, _ = call("GET", "/api/dicts/college", token=admin)
    check("二级学院字典可读取", st == 200 and isinstance(r["data"], list), r)
    st, r, _ = call("GET", "/api/dicts/department", token=admin)
    check("部门字典可读取", st == 200 and isinstance(r["data"], list), r)

    # 中文名也要能命中：之前只有英文键，传「二级学院」会查不到
    st, r, _ = call("GET", "/api/dicts/manage/" + urllib.parse.quote("二级学院"), token=admin)
    check("字典维护支持中文类型名（二级学院）", st == 200 and r["data"]["label"] == "二级学院",
          r.get("message") if st != 200 else r["data"].get("label"))
    st, r, _ = call("GET", "/api/dicts/manage/" + urllib.parse.quote("部门"), token=admin)
    check("字典维护支持中文类型名（部门）", st == 200 and r["data"]["label"] == "部门",
          r.get("message") if st != 200 else r["data"].get("label"))

    st, r, _ = call("POST", "/api/dicts/manage/college", token=admin,
                    body={"name": "信息工程学院"})
    check("新增二级学院成功", st == 200, r)
    st, r, _ = call("POST", "/api/dicts/manage/department", token=admin,
                    body={"name": "测试教务处"})
    check("新增部门成功", st == 200, r)
    st, r, _ = call("GET", "/api/dicts/college", token=admin)
    check("新增的二级学院出现在下拉里", "信息工程学院" in r["data"], r["data"])

    # 账号带部门：建号 → 回读
    st, r, _ = call("POST", "/api/users", token=admin,
                    body={"username": "dept_user", "real_name": "部门测试",
                          "phone": "13800139099", "role": "reviewer",
                          "college": "信息工程学院", "department": "测试教务处"})
    check("建号可写入部门", st == 200 and r["data"].get("department") == "测试教务处", r)
    dept_uid = r["data"]["id"]
    st, r, _ = call("POST", "/api/dicts/manage/department", token=admin,
                    body={"name": "测试学生处"})
    check("新增第二个部门成功", st == 200, r)
    st, r, _ = call("PUT", f"/api/users/{dept_uid}", token=admin, body={"department": "测试学生处"})
    check("可修改账号部门", st == 200 and r["data"].get("department") == "测试学生处", r)

    # 被账号引用的部门不能删除（引用检查要覆盖 users 表，不只是报名表）
    # 关键词必须唯一命中「测试学生处」：部门字典现在有 18+2 条默认值，
    # 搜「学生处」会同时命中未被引用的「学生处」，删它当然放行了
    st, r, _ = call("GET", "/api/dicts/manage/department?keyword="
                    + urllib.parse.quote("测试学生处"), token=admin)
    row = (r["data"]["list"] or [{}])[0]
    st, r, _ = call("DELETE", f"/api/dicts/manage/department/{row.get('id')}", token=admin)
    check("被账号引用的部门禁止删除", st == 400 and "使用" in r["message"], r)

    # ---------------------------------------------------------------
    print("\n[18] 大模型代理设置")
    st, r, _ = call("GET", "/api/ai/config", token=admin)
    check("AI 配置含 proxy 字段", st == 200 and "proxy" in r["data"]["config"], r.get("data"))
    st, r, _ = call("PUT", "/api/ai/config", token=admin,
                    body={"proxy": "http://127.0.0.1:7890"})
    check("可保存 http 代理", st == 200
          and r["data"]["config"]["proxy"] == "http://127.0.0.1:7890", r.get("data"))
    st, r, _ = call("PUT", "/api/ai/config", token=admin, body={"proxy": "none"})
    check("none 表示强制直连", st == 200 and r["data"]["config"]["proxy"] == "none", r.get("data"))
    st, r, _ = call("PUT", "/api/ai/config", token=admin, body={"proxy": "127.0.0.1:7890"})
    check("代理缺协议头被拒绝", st == 400, r)
    st, r, _ = call("PUT", "/api/ai/config", token=admin, body={"proxy": "socks5://127.0.0.1:1080"})
    check("不支持的 socks 代理被拒绝", st == 400, r)
    st, r, _ = call("PUT", "/api/ai/config", token=admin, body={"proxy": ""})
    check("清空代理回到跟随系统", st == 200 and r["data"]["config"]["proxy"] == "", r.get("data"))

    # ---------------------------------------------------------------
    print("\n[19] 系统维护与测试数据清理")
    # 系统维护页内置文档
    st, r, _ = call("GET", "/api/manual", token=admin)
    keys = [x["key"] for x in r["data"]["list"]]
    check("文档列表接口返回 usage + dev", sorted(keys) == ["dev", "usage"], r["data"]["list"])
    st, r, _ = call("GET", "/api/manual/usage", token=admin)
    check("管理员可取使用说明内容",
          st == 200 and r["data"]["title"] == "使用说明" and len(r["data"]["content"]) > 1000,
          len(r["data"].get("content", "")))
    st, r, _ = call("GET", "/api/manual/dev", token=admin)
    check("管理员可取开发文档内容",
          st == 200 and r["data"]["title"] == "开发文档" and len(r["data"]["content"]) > 1000,
          len(r["data"].get("content", "")))
    # 考生只能看到 usage、拿不到 dev
    _cap = call("GET", "/api/auth/captcha")[1]["data"]
    cap_can = call("POST", "/api/auth/login",
                   body={"account": "candidate", "password": "Candidate@123",
                         "captcha_id": _cap["captcha_id"], "captcha_code": _cap["debug_code"]})
    can = cap_can[1]["data"]["access_token"]
    st, r, _ = call("GET", "/api/manual", token=can)
    check("考生文档列表只含 usage",
          [x["key"] for x in r["data"]["list"]] == ["usage"], r["data"]["list"])
    st, r, _ = call("GET", "/api/manual/dev", token=can)
    check("考生取开发文档被 403 拦截", st == 403, r)
    st, r, _ = call("GET", "/api/manual/nope", token=admin)
    check("未知文档 key 被 404", st == 404, r)
    st, r, _ = call("GET", "/api/system/info", token=admin)
    check("系统信息接口", st == 200 and "counts" in r["data"], list(r["data"].keys()))
    check("返回清理范围选项", len(r["data"]["reset_scopes"]) == 5, r["data"].get("reset_scopes"))
    check("演示数据标记可见", r["data"]["seed_demo"] is True, r["data"].get("seed_demo"))
    st, r, _ = call("GET", "/api/system/info", token=rev)
    check("审核员无权查看系统信息(403)", st == 403, r)

    st, r, _ = call("POST", "/api/system/reset", token=admin, body={"scopes": ["applications"]})
    check("未输入确认短语被拦截", st == 400, r)

    st, r, _ = call("POST", "/api/system/reset", token=admin,
                    body={"scopes": ["applications"], "confirm": "确认清空"})
    check("清空报名数据成功", st == 200 and r["data"]["after"]["applications"] == 0, r["data"])
    check("清理后审核记录归零", r["data"]["after"]["audits"] == 0, r["data"]["after"])
    check("清理后账号保留", r["data"]["after"]["users"] > 0, r["data"]["after"])
    check("清理后考试批次保留", r["data"]["after"]["exams"] > 0, r["data"]["after"])

    st, r, _ = call("POST", "/api/system/reset", token=admin,
                    body={"scopes": ["users"], "confirm": "确认清空"})
    check("清理账号成功", st == 200 and r["data"]["after"]["users"] == 1, r["data"]["after"])
    st, r, _ = call("GET", "/api/auth/me", token=admin)
    check("清理后当前管理员仍可访问", st == 200, r)

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
