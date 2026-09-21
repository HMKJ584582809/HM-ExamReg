# -*- coding: utf-8 -*-
"""AI 智能分析 / AI 客服专项回归（需服务运行）。

用法
----
    EXAM_DEV=1 EXAM_SEED_DEMO=1 python -m uvicorn backend.app.main:app --port 8992
    python tools/verify_ai.py http://127.0.0.1:8992

注意：本脚本会写 AI 配置并在库里建少量报名数据，请在**全新数据库**上运行
（rm -rf data 后重启服务）。
"""
import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8992"
ROOT = Path(__file__).resolve().parents[1]

PASS, FAIL = [], []

# 合成数据用的时间戳：_now 为「刚提交」，_old 为「5 天前提交」（制造审核积压）
_NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
_OLD = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")


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


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("" if cond else f"  -> {detail}"))


def login(account, password):
    st, r = call("POST", "/api/auth/login", body={"account": account, "password": password})
    return r["data"]["access_token"] if st == 200 and r.get("code") == 0 else None


def chat(token, message):
    st, r = call("POST", "/api/ai/chat", token=token, body={"message": message})
    return st, (r.get("data") if st == 200 else None), r.get("message", "")


# ------------------------------------------------------------------ 模拟大模型

MOCK_REPLY = "【模拟大模型】- 待审核占比偏高，建议集中时段处理\n- 其余维度未见明显异常"
MOCK = {"auth": "", "model": "", "messages": []}


class _MockHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容服务，用于验证「大模型真的被调用且返回被正确解析」"""

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        MOCK["auth"] = self.headers.get("Authorization", "")
        MOCK["model"] = body.get("model", "")
        MOCK["messages"] = body.get("messages", [])
        last = (MOCK["messages"] or [{}])[-1].get("content", "")
        content = "正常" if "正常" in str(last) else MOCK_REPLY
        out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]},
                         ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def start_mock_llm(port: int):
    srv = ThreadingHTTPServer(("127.0.0.1", port), _MockHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


# ------------------------------------------------------------------ 体检单测

def _mkrow(et: str, **kw) -> dict:
    """合成一行报名数据（字段齐备且合法），再按 kw 覆盖出目标问题。"""
    row = {"_type": et, "id": 0, "exam_id": 1, "user_id": 0, "name": "张三",
           "gender": "男", "id_type": "1", "id_number": "320102199501011239",
           "phone": "13800001111", "audit_status": "approved", "created_at": _NOW}
    if et == "computer":
        row.update({"org_code": "1101", "exam_site_code": "110101", "subject": "一级MS Office",
                    "school": "示例院校1", "class_name": "计算机2101", "education": "本科",
                    "email": "a@exam.local", "address": "南京市鼓楼区1号"})
    else:
        row.update({"ethnicity": "汉族", "occupation": "学生", "employer": "某单位",
                    "student_no": "20220001", "class_name": "汉语言2101",
                    "department": "文学院", "contact_address": "联系地址",
                    "mail_address": "邮寄地址", "postcode": "210000",
                    "birth_province": "江苏省", "birth_city": "南京市",
                    "birth_county": "鼓楼区", "live_province": "江苏省",
                    "live_city": "南京市", "live_county": "鼓楼区"})
    row.update(kw)
    return row


def unit_checks(db_path: str = ""):
    """直接调用体检函数跑合成数据：覆盖 HTTP 链路造不出来的脏数据。

    db_path 由 /api/system/info 提供——exe 的数据目录跟随 exe 位置，
    不在项目根，进程内模块必须显式指向同一个库文件。
    """
    sys.path.insert(0, str(ROOT / "backend"))
    from app import ai_engine as AI
    from app import db as appdb
    if db_path:
        appdb.DB_PATH = Path(db_path)

    # 两个合成批次：1 为计算机类，2 为普通话类（必填集合不同，不能混用）
    exams = {1: {"exam_type": "computer", "name": "单测批次-计算机"},
             2: {"exam_type": "mandarin", "name": "单测批次-普通话"}}
    rows = [
        _mkrow("computer", id=1, id_number="32010219950101013X",
               phone="123"),                                         # 手机号非法
        _mkrow("computer", id=2, id_number="320102199501010236",
               email="not-an-email"),                                # 邮箱非法
        _mkrow("computer", id=3, id_number="320102199501010333"),    # 身份证校验位错
        _mkrow("computer", id=4, id_number="320102199501010439",
               subject=""),                                          # 必填缺失
        _mkrow("computer", id=5, id_number="320102199501010535",
               gender="女"),                                         # 性别与证件号不符
        _mkrow("computer", id=6, id_number="320102199501010631",
               class_name=""),                                       # 班级为空
        _mkrow("computer", id=7, id_number="320102199501010738",
               audit_status="pending", created_at=_OLD),             # 审核积压
        _mkrow("mandarin", id=8, exam_id=2, id_number="320102199501010834",
               postcode="12"),                                       # 邮编非法
        # 同一证件号两条不同姓名：同时触发「重复」与「姓名不一致」
        _mkrow("computer", id=9, id_number="320102199501010930", name="李四"),
        _mkrow("computer", id=10, id_number="320102199501010930", name="王五"),
    ]
    m = {c["key"]: c for c in AI._checks(rows, exams)}
    check("体检-手机号格式异常被识别", m["bad_phone"]["count"] == 1, m["bad_phone"]["count"])
    check("体检-邮箱格式异常被识别", m["bad_email"]["count"] == 1, m["bad_email"]["count"])
    check("体检-身份证校验位错误被识别", m["bad_id"]["count"] == 1, m["bad_id"]["count"])
    # 计算机类把班级也列为必填，因此「报考科目为空」与「班级为空」两条都计入
    check("体检-必填字段缺失被识别", m["missing_required"]["count"] == 2,
          m["missing_required"]["count"])
    check("体检-性别与证件号不符被识别", m["gender_mismatch"]["count"] == 1,
          m["gender_mismatch"]["count"])
    check("体检-同批次证件号重复被识别", m["dup_id"]["count"] == 1, m["dup_id"]["count"])
    check("体检-班级为空被识别", m["no_class"]["count"] == 1, m["no_class"]["count"])
    check("体检-审核积压被识别", m["stale_pending"]["count"] == 1, m["stale_pending"]["count"])
    check("体检-邮编异常被识别", m["bad_postcode"]["count"] == 1, m["bad_postcode"]["count"])
    check("体检-同证件号不同姓名被识别", m["id_name_conflict"]["count"] == 1,
          m["id_name_conflict"]["count"])
    check("体检-分级按占比计算（10 行中 1 行异常即为需处理）",
          m["bad_phone"]["level"] == "danger", m["bad_phone"]["level"])
    check("体检-无账号的报名不计入缺照片", m["no_photo"]["count"] == 0, m["no_photo"]["count"])
    check("体检-样例脱敏（证件号中间打码）",
          "*" in (m["dup_id"]["samples"][0]["id_number"] if m["dup_id"]["samples"] else ""),
          m["dup_id"]["samples"])


# ------------------------------------------------------------------ 主流程

def main():
    print("=" * 70)
    print("AI 智能分析 / AI 客服 专项回归 ·", BASE)

    admin = login("admin", "Admin@123")
    cand = login("candidate", "Candidate@123")
    teacher = login("teacher", "Teacher@123")
    college = login("college", "College@123")
    if not all([admin, cand, teacher, college]):
        print("演示账号登录失败：请用 EXAM_DEV=1 EXAM_SEED_DEMO=1 且全新数据库启动服务")
        return 1

    print("\n[1] 鉴权与权限")
    st, r = call("GET", "/api/ai/config")
    check("未登录读取 AI 配置被拦截(401)", st == 401, r)
    st, r = call("POST", "/api/ai/analyze", body={})
    check("未登录调用智能分析被拦截(401)", st == 401, r)
    st, r = call("POST", "/api/ai/chat", body={"message": "你好"})
    check("未登录调用 AI 客服被拦截(401)", st == 401, r)
    st, r = call("GET", "/api/ai/config", token=cand)
    check("考生读取 AI 配置被拒绝(403)", st == 403, r)
    st, r = call("POST", "/api/ai/analyze", token=cand, body={})
    check("考生调用智能分析被拒绝(403)", st == 403, r)
    st, r = call("GET", "/api/ai/suggestions", token=cand)
    check("考生可读取快捷提问", st == 200 and len(r["data"]["questions"]) > 0, r)

    print("\n[2] AI 配置（密钥只写不读）")
    # 先归零到出厂状态，保证脚本可重复运行
    call("PUT", "/api/ai/config", token=admin,
         body={"enable": 0, "base_url": "", "api_key": "__clear__", "model": "",
               "system_prompt": "", "timeout": 30})
    st, r = call("GET", "/api/ai/config", token=admin)
    d0 = r["data"]
    check("默认未启用大模型", st == 200 and d0["ready"] is False and d0["engine"] == "local", d0)
    check("默认密钥为空且标记为未配置",
          d0["config"]["api_key"] == "" and d0["config"]["api_key_set"] is False, d0["config"])

    st, r = call("PUT", "/api/ai/config", token=admin, body={"base_url": "ftp://x.com/v1"})
    check("非法服务地址被拒绝", st == 400 and "http" in r.get("message", ""), r)
    st, r = call("PUT", "/api/ai/config", token=admin, body={"timeout": 999})
    check("超时越界被拒绝", st == 400 and "5-120" in r.get("message", ""), r)
    st, r = call("PUT", "/api/ai/config", token=admin, body={"model": "unit-test-model"})
    check("局部更新只改提交的字段",
          st == 200 and r["data"]["config"]["model"] == "unit-test-model"
          and r["data"]["config"]["timeout"] == 30, r.get("data"))
    st, r = call("PUT", "/api/ai/config", token=admin, body={"enable": 1})
    check("启用但配置不完整被拒绝", st == 400 and "完整" in r.get("message", ""), r)
    st, r = call("PUT", "/api/ai/config", token=admin,
                 body={"enable": 1, "base_url": "https://127.0.0.1:9/v1",
                       "api_key": "sk-unit-test", "model": "unit-test-model", "timeout": 6})
    check("完整配置可启用", st == 200 and r["data"]["ready"] is True, r.get("data"))
    st, r = call("GET", "/api/ai/config", token=admin)
    check("读取时密钥只回掩码，不回明文",
          r["data"]["config"]["api_key"] == "******"
          and r["data"]["config"]["api_key_set"] is True
          and "sk-unit-test" not in json.dumps(r, ensure_ascii=False), r["data"]["config"])
    st, r = call("POST", "/api/ai/test", token=admin)
    check("测试连接返回失败但不报错（本机无该服务）",
          st == 200 and r["data"]["ok"] is False, r.get("data"))
    st, r = call("PUT", "/api/ai/config", token=admin,
                 body={"enable": 0, "api_key": "******"})
    check("回传掩码表示不修改密钥",
          st == 200 and r["data"]["ready"] is False
          and r["data"]["config"]["api_key_set"] is True, r.get("data"))
    st, r = call("PUT", "/api/ai/config", token=admin, body={"api_key": "__clear__"})
    check("可显式清除已保存的密钥",
          st == 200 and r["data"]["config"]["api_key_set"] is False, r.get("data"))
    st, r = call("PUT", "/api/ai/config", token=admin,
                 body={"enable": 0, "base_url": "", "model": "", "system_prompt": ""})
    check("恢复默认配置", st == 200 and r["data"]["engine"] == "local", r.get("data"))

    print("\n[3] 智能分析输出结构")
    st, r = call("POST", "/api/ai/analyze", token=admin, body={})
    d = r["data"]
    check("分析接口返回成功", st == 200, r)
    check("未配置大模型时走本地引擎", d["engine"] == "local" and d["llm"]["enabled"] is False,
          d.get("engine"))
    ov = d["overview"]
    check("概览含核心指标",
          all(k in ov for k in ("total", "pending", "approved", "pass_rate", "exam_count")), ov)
    check("概览含数据范围标签", bool(d.get("scope_label")), d.get("scope_label"))
    checks = d["health"]["checks"]
    check("体检项为 11 类", len(checks) == 11, len(checks))
    check("体检项字段完整",
          all({"key", "label", "level", "count", "detail", "advice", "samples"} <= set(c)
              for c in checks), checks[:1])
    check("异常项按数量降序且均为正数",
          all(a["count"] > 0 for a in d["anomalies"])
          and [a["count"] for a in d["anomalies"]] ==
              sorted((a["count"] for a in d["anomalies"]), reverse=True), d["anomalies"])
    check("趋势含 14 天", len(d["trend"]["days"]) == 14, len(d["trend"]["days"]))
    bn = d["bottlenecks"]
    check("瓶颈字段完整",
          all(k in bn for k in ("pending_by_exam", "stale_pending", "avg_review_hours",
                                "reviewer_rank", "closing_soon")), bn)
    check("建议非空且字段完整",
          len(d["suggestions"]) > 0
          and all({"title", "detail", "level"} <= set(s) for s in d["suggestions"]),
          d["suggestions"])
    check("结论为自然语言段落", len(d["summary"]) > 20 and "报名" in d["summary"], d["summary"])
    check("健康分在 0-100 之间", 0 <= d["health"]["score"] <= 100, d["health"]["score"])

    print("\n[4] 数据范围收敛（AI 不得越权看数据）")
    st, rc = call("POST", "/api/ai/analyze", token=college, body={})
    st, rt = call("POST", "/api/ai/analyze", token=teacher, body={})
    check("二级学院审核看到的数据不超过全校",
          rc["data"]["overview"]["total"] <= ov["total"],
          (rc["data"]["overview"]["total"], ov["total"]))
    check("班主任看到的数据不超过全校",
          rt["data"]["overview"]["total"] <= ov["total"],
          (rt["data"]["overview"]["total"], ov["total"]))
    check("二级学院审核范围标签为本院系", "本院系" in rc["data"]["scope_label"],
          rc["data"]["scope_label"])
    check("班主任范围标签为本班级", "本班级" in rt["data"]["scope_label"],
          rt["data"]["scope_label"])

    print("\n[5] 体检规则（合成脏数据单测）")
    st, r = call("GET", "/api/system/info", token=admin)
    unit_checks((r.get("data") or {}).get("db_path", "") if st == 200 else "")

    print("\n[6] AI 客服：意图识别与知识库")
    st, d1, _ = chat(admin, "我的报名状态是什么")
    check("意图-我的报名", st == 200 and d1["intent"] == "mine", d1.get("intent"))
    st, d2, _ = chat(admin, "现在有多少条待审核")
    check("意图-待审核数量", d2["intent"] == "pending" and d2["data"]["pending"] == ov["pending"],
          (d2.get("intent"), (d2.get("data") or {}).get("pending")))
    st, d3, _ = chat(admin, "批量导入的模板在哪里下载")
    check("意图-批量导入走知识库", "批量导入" in d3["reply"] and d3["engine"] == "local", d3["reply"])
    st, d4, _ = chat(admin, "我的权限和数据范围是什么")
    check("意图-权限", "管理员" in d4["reply"] and "数据范围" in d4["reply"], d4["reply"])
    st, d5, _ = chat(admin, "帮我看看数据有什么异常")
    check("意图-数据体检", d5["intent"] == "health" and len(d5["data"]) >= 0, d5.get("intent"))
    st, d6, _ = chat(admin, "今天天气怎么样")
    check("未命中时给出兜底引导", d6["intent"] == "unknown" and "还没学会" in d6["reply"],
          d6.get("intent"))
    st, r = call("POST", "/api/ai/chat", token=admin, body={"message": "   "})
    check("空消息被拒绝", st == 400, r)

    print("\n[7] AI 客服：权限收敛（不泄露看不到的数据）")
    st, dc, _ = chat(cand, "现在有多少条待审核")
    check("考生问待审核：明确说明无权限",
          "权限" in dc["reply"] and dc["data"] is None, dc["reply"])
    st, dc2, _ = chat(cand, "帮我看看数据有什么异常")
    check("考生问体检：明确说明无权限", "数据分析" in dc2["reply"], dc2["reply"])
    st, dm, _ = chat(cand, "我的报名状态是什么")
    check("考生可查自己的报名", dm["intent"] == "mine" and isinstance(dm["data"], list),
          dm.get("intent"))
    st, dt, _ = chat(teacher, "现在有多少条待审核")
    check("班主任可见范围内的待审核（≤ 全校）",
          dt["data"] and dt["data"]["pending"] <= ov["pending"], dt.get("data"))

    print("\n[8] 快捷提问按角色区分")
    st, qa = call("GET", "/api/ai/suggestions", token=admin)
    st, qc = call("GET", "/api/ai/suggestions", token=cand)
    check("管理员含审核类提问",
          any("待审核" in q for q in qa["data"]["questions"]), qa["data"]["questions"])
    check("考生不含审核类提问",
          not any("待审核" in q for q in qc["data"]["questions"]), qc["data"]["questions"])
    check("考生含报名类提问",
          any("报名" in q for q in qc["data"]["questions"]), qc["data"]["questions"])

    print("\n[9] 外部大模型：成功路径（本地模拟服务）")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8899
    srv = None
    try:
        srv = start_mock_llm(port)
        mock_url = f"http://127.0.0.1:{port}/v1"
        st, r = call("PUT", "/api/ai/config", token=admin,
                     body={"enable": 1, "base_url": mock_url, "api_key": "sk-mock",
                           "model": "mock-model", "timeout": 10})
        check("指向模拟服务后可启用", st == 200 and r["data"]["ready"] is True, r.get("data"))
        st, r = call("POST", "/api/ai/test", token=admin)
        check("测试连接成功（本地地址绕过系统代理）",
              st == 200 and r["data"]["ok"] is True, r.get("data"))
        st, r = call("POST", "/api/ai/analyze", token=admin, body={})
        d = r["data"]
        check("分析走「本地 + 大模型」双引擎", d["engine"] == "local+llm", d.get("engine"))
        check("分析附加大模型解读文本",
              bool(d["llm"]["text"]) and MOCK_REPLY.split("\n")[0] in d["llm"]["text"],
              (d.get("llm") or {}).get("text"))
        check("大模型成功时不返回错误", (d["llm"] or {}).get("error") is None, d.get("llm"))
        st, r = call("POST", "/api/ai/chat", token=admin, body={"message": "量子力学怎么用于报名"})
        d2 = r["data"]
        check("知识库未命中时由大模型兜底", d2["engine"] == "llm", d2.get("engine"))
        check("兜底回复来自大模型", MOCK_REPLY.split("\n")[0] in d2["reply"], d2.get("reply"))
        check("请求携带正确模型名", MOCK["model"] == "mock-model", MOCK["model"])
        check("请求携带 Bearer 密钥（但不回显）",
              MOCK["auth"] == "Bearer sk-mock"
              and "sk-mock" not in json.dumps(call("GET", "/api/ai/config", token=admin)[1],
                                              ensure_ascii=False), MOCK["auth"])
        check("本地结论仍在（大模型只是增强，不是替代）", bool(d.get("summary")), d.get("summary"))
    finally:
        if srv:
            srv.shutdown()
            srv.server_close()
        call("PUT", "/api/ai/config", token=admin,
             body={"enable": 0, "base_url": "", "api_key": "__clear__", "model": ""})
        st, r = call("GET", "/api/ai/config", token=admin)
        check("测试后恢复为仅本地引擎",
              st == 200 and r["data"]["engine"] == "local"
              and r["data"]["config"]["api_key_set"] is False, r.get("data"))

    print("\n" + "=" * 70)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
