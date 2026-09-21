# -*- coding: utf-8 -*-
"""自定义考试类型专项回归：类型 CRUD → 建批次 → 报名 → 审核 → 导出 → 分析 全链路。

前置：服务需以 EXAM_DEV=1 EXAM_SEED_DEMO=1 启动，且使用全新数据库。
用法：python tools/verify_exam_types.py http://127.0.0.1:8961
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8791"
PASS, FAIL = [], []


def call(method, path, token=None, body=None, raw=False):
    url = BASE + path
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
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


def login(account, pwd):
    st, r, _ = call("POST", "/api/auth/login", body={"account": account, "password": pwd})
    return r["data"]["access_token"] if st == 200 and r.get("code") == 0 else None


def window(days=20):
    now = datetime.now()
    return (now.strftime("%Y-%m-%d %H:%M:%S"),
            (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"))


def new_exam(token, code, year=2026, month=12, name=""):
    s, e = window()
    body = {"exam_type": code, "exam_year": year, "exam_month": month,
            "signup_start_at": s, "signup_end_at": e, "status": "open"}
    if name:
        body["name"] = name
    st, r, _ = call("POST", "/api/exams", token=token, body=body)
    return st, r


def type_of(rows, code):
    return next((x for x in rows if x["code"] == code), None)


def field_of(t, key):
    return next((f for f in (t.get("fields") or []) if f["key"] == key), None)


def main():
    print("=" * 70)
    print("自定义考试类型专项回归 ·", BASE)

    admin = login("admin", "Admin@123")
    cand = login("candidate", "Candidate@123")
    rev = login("reviewer", "Reviewer@123")
    if not (admin and cand and rev):
        print("演示账号缺失，请以 EXAM_SEED_DEMO=1 启动服务"); return 1

    print("\n[1] 类型列表与内置类型")
    st, r, _ = call("GET", "/api/exam-types")
    check("未登录读取类型列表被拦截(401)", st == 401, r)
    st, r, _ = call("GET", "/api/exam-types", token=admin)
    rows = r.get("data", {}).get("list", [])
    # 本脚本会写入 english / pth_custom 等固定编码，重复跑在同一库上会撞唯一约束
    if any(x["code"] in ("english", "pth_custom") for x in rows):
        print("检测到本脚本的残留类型，请在全新数据库上运行（rm -rf data 后重启服务）")
        return 1
    check("管理员可读取类型列表", st == 200 and len(rows) >= 2, r)
    comp = type_of(rows, "computer")
    mand = type_of(rows, "mandarin")
    check("内置类型 computer 存在且 is_builtin", comp and comp["is_builtin"] is True, comp)
    check("内置类型 mandarin 存在且 is_builtin", mand and mand["is_builtin"] is True, mand)
    check("计算机模板 13 个采集字段", comp and len(comp["fields"]) == 13,
          comp and len(comp["fields"]))
    check("普通话模板 20 个采集字段", mand and len(mand["fields"]) == 20,
          mand and len(mand["fields"]))
    sysreq = [f["key"] for f in (comp or {}).get("fields", []) if f.get("system_required")]
    check("系统必填项已标注", "name" in sysreq and "id_number" in sysreq, sysreq)

    print("\n[2] 参数与编码校验")
    for bad, why in [("English", "大写开头"), ("1abc", "数字开头"), ("a", "长度不足 2"),
                     ("a" * 31, "超过 30 字符"), ("ab-cd", "含非法字符")]:
        st, r, _ = call("POST", "/api/exam-types", token=admin,
                        body={"code": bad, "name": "测试类型", "base_type": "computer"})
        check(f"编码非法被拒（{why}）", st == 400, r)
    st, r, _ = call("POST", "/api/exam-types", token=admin,
                    body={"code": "noname", "name": "  ", "base_type": "computer"})
    check("名称为空被拒", st == 400, r)
    st, r, _ = call("POST", "/api/exam-types", token=admin,
                    body={"code": "badbase", "name": "坏底座", "base_type": "english"})
    check("基础模板非法被拒", st == 400, r)
    st, r, _ = call("POST", "/api/exam-types", token=admin,
                    body={"code": "computer", "name": "重名", "base_type": "computer"})
    check("重复编码被拒", st == 400, r)
    st, r, _ = call("POST", "/api/exam-types", token=rev,
                    body={"code": "byrev", "name": "审核员建的类型", "base_type": "computer"})
    check("非管理员创建类型被拒(403)", st == 403, r)

    print("\n[3] 创建自定义类型（基于计算机模板）")
    cfg = {
        "email": {"enabled": False},                                  # 关闭采集
        "address": {"enabled": True, "required": True, "label": "联系住址"},  # 追加必填 + 改名
        "name": {"enabled": False, "required": False},                 # 系统必填，应被强制拉回
    }
    st, r, _ = call("POST", "/api/exam-types", token=admin, body={
        "code": "english", "name": "英语四级考试", "base_type": "computer",
        "description": "基于计算机模板的自定义类型", "fields_config": cfg, "sort": 30})
    check("创建自定义类型成功", st == 200 and r["data"]["code"] == "english", r)
    eng = r.get("data", {})
    check("返回基础模板为 computer", eng.get("base_type") == "computer", eng.get("base_type"))
    check("返回基础模板名称", eng.get("base_type_label") == "计算机类考试", eng.get("base_type_label"))
    check("关闭的字段 email 生效", (field_of(eng, "email") or {}).get("enabled") is False,
          field_of(eng, "email"))
    check("追加必填的 address 生效", (field_of(eng, "address") or {}).get("required") is True,
          field_of(eng, "address"))
    check("改名生效（address→联系住址）",
          (field_of(eng, "address") or {}).get("label") == "联系住址", field_of(eng, "address"))
    check("系统必填项无法被关闭", (field_of(eng, "name") or {}).get("enabled") is True,
          field_of(eng, "name"))
    check("系统必填项无法取消必填", (field_of(eng, "name") or {}).get("required") is True,
          field_of(eng, "name"))
    check("脏字段被丢弃（模板不存在的字段不入库）",
          all(f["key"] in [x["key"] for x in (comp or {}).get("fields", [])]
              for f in eng.get("fields", [])), eng.get("fields"))
    st, r, _ = call("GET", "/api/exam-types/options", token=admin)
    opts = r.get("data", {}).get("list", [])
    o_eng = next((x for x in opts if x["value"] == "english"), None)
    check("启用类型出现在下拉 options", o_eng and o_eng["base_type"] == "computer", opts)

    print("\n[4] 用自定义类型创建批次")
    st, r = new_exam(admin, "english")
    check("自定义类型可创建批次", st == 200, r)
    e1 = r.get("data", {})
    check("批次 exam_type 保存自定义编码", e1.get("exam_type") == "english", e1.get("exam_type"))
    check("批次 exam_type_base 为基础模板", e1.get("exam_type_base") == "computer",
          e1.get("exam_type_base"))
    check("批次类型显示名为自定义名称", e1.get("exam_type_label") == "英语四级考试",
          e1.get("exam_type_label"))
    check("自动命名使用自定义类型名",
          "英语四级考试" in (e1.get("name") or ""), e1.get("name"))
    st, r, _ = call("GET", "/api/exams/%s" % e1.get("id"), token=admin)
    check("批次详情返回字段元信息 fields", st == 200 and len(r["data"].get("fields", [])) == 13,
          len(r["data"].get("fields", [])))
    check("详情 fields 含自定义改名",
          (field_of(r["data"], "address") or {}).get("label") == "联系住址",
          field_of(r["data"], "address"))

    print("\n[5] 停用 / 启用与新建批次保护")
    st, r, _ = call("PUT", "/api/exam-types/%s" % eng["id"], token=admin, body={"enabled": False})
    check("停用类型成功", st == 200 and r["data"]["enabled"] is False, r)
    st, r, _ = call("GET", "/api/exam-types/options", token=admin)
    check("停用后不出现在下拉",
          "english" not in [x["value"] for x in r["data"]["list"]], r["data"]["list"])
    st, r = new_exam(admin, "english", month=11)
    check("停用类型不能再建批次(400)", st == 400, r)
    st, r = new_exam(admin, "notexist", month=10)
    check("不存在的类型不能建批次(400)", st == 400, r)
    st, r, _ = call("PUT", "/api/exam-types/%s" % eng["id"], token=admin, body={"enabled": True})
    check("重新启用成功", st == 200 and r["data"]["enabled"] is True, r)

    print("\n[6] 报名：字段配置在写入链路生效")
    orgs = call("GET", "/api/dicts/org", token=cand)[1]["data"]
    subjects = call("GET", "/api/dicts/subject", token=cand)[1]["data"]
    org_code = orgs[0]["code"] if isinstance(orgs[0], dict) else orgs[0]
    subject = subjects[0] if isinstance(subjects[0], str) else subjects[0].get("name")
    base_app = {"exam_id": e1["id"], "org_code": org_code, "exam_site_code": "110101",
                "name": "自定义类型考生", "gender": "男", "id_type": "1",
                "id_number": "110101199001011405", "subject": subject,
                "school": "计算机学院", "class_name": "计算机2101",
                "education": "本科", "phone": "13800139001", "email": "a@b.com"}
    st, r, _ = call("POST", "/api/applications", token=cand,
                    body=dict(base_app, address=""))
    check("缺少被追加为必填的 address 被拒(400)", st == 400, r)
    st, r, _ = call("POST", "/api/applications", token=cand,
                    body=dict(base_app, address="北京市海淀区中关村大街1号"))
    check("补全后报名提交成功", st == 200, r)
    app_id = r.get("data", {}).get("id")
    st, r, _ = call("GET", "/api/applications/%s?app_type=computer" % app_id, token=admin)
    check("报名落在 computer 表（基础模板决定存储）", st == 200 and r["data"]["id"] == app_id, r)
    check("报名详情返回 fields 元信息",
          st == 200 and len(r["data"].get("fields", [])) == 13, r.get("data", {}).keys())
    check("详情 fields 沿用自定义改名",
          (field_of(r["data"], "address") or {}).get("label") == "联系住址",
          field_of(r["data"], "address"))
    check("详情 fields 中 email 为关闭态",
          (field_of(r["data"], "email") or {}).get("enabled") is False,
          field_of(r["data"], "email"))
    st, r, _ = call("GET", "/api/applications?exam_id=%s&page_size=20" % e1["id"], token=admin)
    row0 = (r.get("data", {}).get("list") or [{}])[0]
    check("列表 app_type 为基础模板 computer", row0.get("app_type") == "computer", row0)
    check("列表类型显示名为自定义名称",
          row0.get("app_type_label") == "英语四级考试", row0.get("app_type_label"))

    print("\n[7] 基于普通话模板的自定义类型")
    st, r, _ = call("POST", "/api/exam-types", token=admin, body={
        "code": "pth_custom", "name": "普通话专项测试", "base_type": "mandarin"})
    check("创建普通话自定义类型成功", st == 200 and r["data"]["base_type"] == "mandarin", r)
    pth = r["data"]
    st, r = new_exam(admin, "pth_custom", month=9)
    check("普通话自定义类型可建批次", st == 200, r)
    e2 = r["data"]
    check("批次基础模板为 mandarin", e2.get("exam_type_base") == "mandarin", e2)
    occs = call("GET", "/api/dicts/occupation", token=cand)[1]["data"]
    occ = occs[0] if isinstance(occs[0], str) else occs[0].get("name")
    st, r, _ = call("POST", "/api/applications", token=cand, body={
        "exam_id": e2["id"], "name": "普通话考生", "gender": "女", "id_type": "1",
        "id_number": "110101199203051421", "ethnicity": "汉族", "occupation": occ,
        "employer": "某某单位", "phone": "13800139002"})
    check("普通话自定义类型报名成功", st == 200, r)
    m_id = r["data"]["id"]
    st, r, _ = call("GET", "/api/applications?app_type=mandarin&exam_id=%s" % e2["id"],
                    token=admin)
    check("记录落在 mandarin 表", st == 200 and (r["data"]["list"] or [{}])[0]["id"] == m_id, r)

    print("\n[8] 导出与分析按自定义类型收敛")
    st, r, _ = call("GET", "/api/export/preview?exam_id=%s&audit_status=all" % e1["id"],
                    token=admin)
    check("导出预览可用（走计算机模板）", st == 200 and r["data"]["total"] >= 1, r)
    st, r, _ = call("GET", "/api/analysis/summary?exam_type=english", token=admin)
    check("分析可按自定义类型筛选", st == 200, r)
    eng_total = r["data"]["total"]
    st, r, _ = call("GET", "/api/analysis/summary", token=admin)
    all_total = r["data"]["total"]
    check("自定义类型筛选后总量小于全量", 0 < eng_total < all_total,
          {"eng": eng_total, "all": all_total})
    st, r, _ = call("GET", "/api/analysis/summary?exam_type=computer", token=admin)
    check("选基础类型时含其自定义子类型", r["data"]["total"] >= eng_total,
          {"computer": r["data"]["total"], "english": eng_total})

    print("\n[9] 删除保护")
    st, r, _ = call("DELETE", "/api/exam-types/%s" % comp["id"], token=admin)
    check("内置类型不允许删除(400)", st == 400, r)
    st, r, _ = call("DELETE", "/api/exam-types/%s" % eng["id"], token=admin)
    check("使用中的类型不允许删除(400)", st == 400, r)
    st, r, _ = call("POST", "/api/exam-types", token=admin,
                    body={"code": "temp_type", "name": "临时类型", "base_type": "computer"})
    tmp = r["data"]
    st, r, _ = call("DELETE", "/api/exam-types/%s" % tmp["id"], token=admin)
    check("未使用的自定义类型可删除", st == 200, r)
    st, r, _ = call("GET", "/api/exam-types", token=admin)
    check("删除后不再出现在列表",
          "temp_type" not in [x["code"] for x in r["data"]["list"]], r["data"]["list"])

    print("\n[10] 改名后历史批次跟随")
    st, r, _ = call("PUT", "/api/exam-types/%s" % eng["id"], token=admin,
                    body={"name": "大学英语四级"})
    check("改名成功", st == 200 and r["data"]["name"] == "大学英语四级", r)
    st, r, _ = call("GET", "/api/exams/%s" % e1["id"], token=admin)
    check("历史批次类型显示名跟随更新",
          r["data"].get("exam_type_label") == "大学英语四级", r["data"].get("exam_type_label"))

    print("\n[11] 导入模板与导出文件遵循字段配置")
    st, blob, _ = call("GET", "/api/applications/import-template?exam_id=%s" % e1["id"],
                       token=admin, raw=True)
    check("导入模板可下载", st == 200 and isinstance(blob, bytes) and len(blob) > 1000,
          type(blob))
    th = []
    if st == 200:
        import io as _io
        import openpyxl
        wb = openpyxl.load_workbook(_io.BytesIO(blob))
        th = [c.value for c in wb.worksheets[0][1] if c.value is not None]
    check("导入模板去掉被关闭采集的字段", not any("Email" in str(v) for v in th), th)
    check("导入模板使用自定义字段标签", any(str(v) == "联系住址" for v in th), th)
    check("导入模板列数为 12（13 - 1 个关闭）", len(th) == 12, len(th))
    st, r, _ = call("GET", "/api/export/preview?exam_id=%s&audit_status=all" % e1["id"],
                    token=admin)
    hs = (r.get("data") or {}).get("headers") or []
    check("导出表头去掉被关闭采集的字段", not any("Email" in str(v) for v in hs), hs)
    check("导出表头使用自定义字段标签", any(str(v) == "联系住址" for v in hs), hs)
    check("导出预览行数与表头列数一致",
          all(len(row) == len(hs) for row in ((r.get("data") or {}).get("rows") or [])),
          len(hs))

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
