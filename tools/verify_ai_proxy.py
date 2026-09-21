# -*- coding: utf-8 -*-
"""
AI 网络代理「真实生效」专项校验 —— 9 项
------------------------------------------------------------------
`smoke_test` 只验证了代理能**存能读**（配置项），没验证请求**真的走了代理**。
本脚本在进程内起一个极简 HTTP 代理：
  * 目标地址故意用一个**不可解析的域名**（http://mock-llm.invalid/v1）
  * 配置 `ai.proxy` 指向本地代理 → 测试连接应成功，且代理收到 Host 为该域名的请求
  * 配成 `none`（强制直连）→ 应失败（DNS 不可达），反证上一步不是碰巧成功

用法：
    python tools/verify_ai_proxy.py --base http://127.0.0.1:8793

注意：会改写 `data/settings.json` 的 ai 段，跑完自动还原；
     不可与 smoke_test 连跑（两套都会动 AI 配置）。
"""
import argparse
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PASS = 0
FAIL = 0
BASE = "http://127.0.0.1:8793"
TOKEN = ""

# 代理收到的请求记录
SEEN = []


class ProxyHandler(BaseHTTPRequestHandler):
    """极简代理：不真正转发，直接回一条 OpenAI 兼容响应，并记录请求。"""

    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        SEEN.append({
            "path": self.path,
            "host": self.headers.get("Host", ""),
            "auth": self.headers.get("Authorization", ""),
            "body_len": len(body),
        })
        out = json.dumps({
            "choices": [{"message": {"content": "正常"}}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


def call(method, path, body=None, token=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"message": e.read().decode("utf-8", "ignore")[:200]}


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [OK]   " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + ("  → %s" % (extra,) if extra else ""))


def main():
    global TOKEN, BASE
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE)
    ap.add_argument("--proxy-port", type=int, default=0)
    args = ap.parse_args()
    BASE = args.base

    print("=" * 66)
    print("AI 网络代理「真实生效」专项 · " + BASE)
    print("=" * 66)

    # 起代理（端口 0 = 系统分配）
    srv = ThreadingHTTPServer(("127.0.0.1", args.proxy_port), ProxyHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("\n[0] 本地测试代理已启动 127.0.0.1:%d" % port)
    check("代理端口已分配", port > 0, str(port))

    st, lg = call("POST", "/api/auth/login",
                  {"account": "admin", "password": "Admin@123"})
    check("管理员可登录", st == 200 and lg.get("code") == 0, lg.get("message"))
    TOKEN = (lg.get("data") or {}).get("access_token") or ""
    check("取得令牌", bool(TOKEN))
    if not TOKEN:
        srv.shutdown()
        return 1

    st, r = call("GET", "/api/ai/config", token=TOKEN)
    old = ((r.get("data") or {}).get("config") or {})
    check("读取当前 AI 配置", st == 200 and r.get("code") == 0, r.get("message"))

    TARGET = "http://mock-llm.invalid/v1"     # 故意不可解析，只能靠代理
    try:
        # ---- 走代理 ----
        print("\n[1] 配置代理后：请求应经过代理")
        st, r = call("PUT", "/api/ai/config", {
            "enable": 1, "base_url": TARGET, "api_key": "sk-pkg-test",
            "model": "mock-model", "timeout": 15,
            "proxy": "http://127.0.0.1:%d" % port}, token=TOKEN)
        check("写入 AI 配置（含代理）", st == 200 and r.get("code") == 0, r.get("message"))

        SEEN.clear()
        st, r = call("POST", "/api/ai/test", token=TOKEN)
        d = r.get("data") or {}
        check("测试连接成功", st == 200 and d.get("ok") is True,
              d.get("message") or r.get("message"))
        check("代理收到请求", len(SEEN) >= 1, "收到 %d 条" % len(SEEN))
        if SEEN:
            check("请求的 Host 是目标域名（确经代理）",
                  "mock-llm.invalid" in (SEEN[0]["host"] or ""), SEEN[0]["host"])
            check("请求路径为 /chat/completions",
                  SEEN[0]["path"].endswith("/chat/completions"), SEEN[0]["path"])
            check("密钥随请求发出（Authorization 头）",
                  (SEEN[0]["auth"] or "").startswith("Bearer "), SEEN[0]["auth"])

        # ---- 强制直连（反证） ----
        print("\n[2] 强制直连：应失败（反证上一步不是碰巧成功）")
        call("PUT", "/api/ai/config", {"proxy": "none"}, token=TOKEN)
        SEEN.clear()
        st, r = call("POST", "/api/ai/test", token=TOKEN)
        d = r.get("data") or {}
        check("直连不可达时返回失败", d.get("ok") is False, str(d))
        check("直连未经过代理", len(SEEN) == 0, "收到 %d 条" % len(SEEN))
    finally:
        # 还原 AI 配置
        restore = {k: old.get(k) for k in
                   ("enable", "provider", "base_url", "api_key", "model",
                    "timeout", "system_prompt", "proxy")}
        call("PUT", "/api/ai/config", restore, token=TOKEN)
        srv.shutdown()

    print("\n" + "=" * 66)
    print("结果：%d 项通过，%d 项失败" % (PASS, FAIL))
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
