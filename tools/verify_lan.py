# -*- coding: utf-8 -*-
"""局域网监听（--lan / --host）专项校验 —— 31 项（exe 模式跑 25 项，F 段 6 项仅源码模式）

用法：
    python tools/verify_lan.py src 8900              # 源码启动器 backend/launcher.py
    python tools/verify_lan.py <exe绝对路径> 8910    # 冻结后的 exe

F 段需要已登录的管理员令牌才能读写 /api/system/settings，因此只在 src 模式跑
（第三个参数给出服务地址，如 http://127.0.0.1:8790；不给则跳过 F 段）。

覆盖：
  A. 纯函数：is_wildcard / valid_host / lan_ips                        (6 项)
  B. 绑定行为：默认仅本机 / --lan 全部网卡 / --host <IP> 单网卡          (3 项)
  C. 横幅内容：访问地址永不为 0.0.0.0、局域网段、首选标记                (4 项)
  D. 局域网真实可达                                                      (2 项)
  E. 非法地址被拦截 + 横幅可被 GBK 编码（防 ⚠ 类控制台崩溃回归）        (3 项)
  F. 系统维护页开关：settings 读写 + 启动器默认行为（需服务，src 模式）  (6 项)

注意：源码模式输出 UTF-8，但**冻结后的 exe 输出是 GBK**（PYTHONIOENCODING 对
PyInstaller 无效，标准流编码在解释器初始化时就定死了）。因此子进程一律取原始
字节，再由 decode_out() 自动探测编码，否则中文断言会全部误判为失败。
"""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

passed, failed, total = 0, 0, 0


def check(name, cond, detail=""):
    global passed, failed, total
    total += 1
    if cond:
        passed += 1
        print(f"  [OK] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def can_connect(host, port, timeout=1.5):
    """能否 TCP 连上（用于判断到底绑了哪张网卡）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_up(host, port, seconds=25):
    """exe 冷启动较慢，轮询等待端口就绪。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        if can_connect(host, port):
            return True
        time.sleep(0.5)
    return False


def decode_out(raw: bytes) -> str:
    """子进程输出解码：源码 UTF-8 / 冻结 exe 为 GBK，严格试一种再退到另一种。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def start(cmd_args, port, cwd, extra_env=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if extra_env:
        env.update(extra_env)
    # 不指定 text/encoding：拿原始字节，交给 decode_out 探测
    p = subprocess.Popen(cmd_args, cwd=cwd, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p


def stop(p):
    if p.poll() is None:
        p.terminate()
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
    try:
        p.stdout.close()
    except Exception:
        pass


def run_case(args, port, wait_host, cwd, wait_seconds=25, extra_env=None):
    """启动并等待就绪，返回 (proc, ok)。"""
    p = start(args, port, cwd, extra_env)
    ok = wait_up(wait_host, port, wait_seconds)
    time.sleep(0.6)
    return p, ok


def read_out(p):
    """尽量取到已输出的内容（进程仍在跑，不能 communicate 阻塞）。"""
    try:
        os.set_blocking(p.stdout.fileno(), False)
    except Exception:
        pass
    try:
        return decode_out(p.stdout.read() or b"")
    except Exception:
        return ""


def main():
    mode = sys.argv[1]
    base = int(sys.argv[2])
    lan_ip = None

    print("=" * 62)
    print(f"局域网监听专项校验  模式={mode}  起始端口={base}")
    print("=" * 62)

    # ---------- A. 纯函数（源码里的 launcher.py 始终可读）----------
    import launcher as L

    print("\n[A] 纯函数")
    check("A1 0.0.0.0 视为监听全部网卡", L.is_wildcard("0.0.0.0"))
    check("A2 :: 视为监听全部网卡", L.is_wildcard("::"))
    check("A3 127.0.0.1 不是全部网卡", not L.is_wildcard("127.0.0.1"))
    check("A4 非法地址被 valid_host 拒绝", not L.valid_host("999.999.999.999"))
    check("A5 wildcard 通过 valid_host", L.valid_host("0.0.0.0") and L.valid_host("::"))

    ips = L.lan_ips()
    lan_ip = ips[0] if ips else None
    check("A6 lan_ips 返回非回环 IPv4",
          bool(ips) and all(not i.startswith("127.") and i.count(".") == 3 for i in ips),
          f"实际={ips}")
    if not lan_ip:
        print("  未检测到局域网地址，B/D 段将跳过")
        print(f"\n结果：{passed}/{total} 通过，{failed} 失败")
        return 1 if failed else 0

    # ---------- 组装命令 ----------
    if mode == "src":
        cwd = str(ROOT / "backend")
        cmd = [sys.executable, "launcher.py"]
    else:
        cwd = str(Path(mode).resolve().parent)
        cmd = [str(Path(mode).resolve())]

    # ---------- B/C. 开关关闭（仅本机）----------
    # 交付默认 lan_access=1，因此用 EXAM_LAN=0 精确构造「关闭」状态，不受 settings 影响
    print("\n[B/C] 开关关闭 → 仅本机")
    p1, up1 = run_case(cmd + ["--port", str(base), "--no-browser"], base, "127.0.0.1", cwd,
                       extra_env={"EXAM_LAN": "0"})
    out1 = read_out(p1)
    check("B1 关闭时本机可连", up1)
    check("B2 关闭时局域网 IP 不可连", not can_connect(lan_ip, base))
    check("C1 关闭时横幅访问地址为 127.0.0.1", f"http://127.0.0.1:{base}" in out1)
    check("C2 关闭时横幅不含局域网段", "全部网卡" not in out1)
    stop(p1)
    time.sleep(1)

    # ---------- B/C. 开关开启（不传任何参数，靠开关默认打开）----------
    print("\n[B/C] 开关开启 → 全部网卡（不传 --lan，验证默认打开）")
    p0, up0 = run_case(cmd + ["--port", str(base + 7), "--no-browser"], base + 7,
                       "127.0.0.1", cwd, extra_env={"EXAM_LAN": "1"})
    out0 = read_out(p0)
    check("B7 开启时局域网 IP 可连", can_connect(lan_ip, base + 7))
    check("C6 开启时横幅声明监听全部网卡", "全部网卡" in out0)
    check("C7 开启时本机访问地址仍用 127.0.0.1", f"http://127.0.0.1:{base + 7}" in out0)
    stop(p0)
    time.sleep(1)

    # ---------- B/C/D. 局域网模式 ----------
    print("\n[B/C/D] --lan（全部网卡）")
    p2, up2 = run_case(cmd + ["--lan", "--port", str(base + 1), "--no-browser"],
                       base + 1, "127.0.0.1", cwd)
    out2 = read_out(p2)
    check("B3 --lan 本机仍可连", up2)
    check("D1 --lan 局域网 IP 可连（真实可达）", can_connect(lan_ip, base + 1))
    check("C3 --lan 横幅声明监听全部网卡", "全部网卡" in out2)
    check("C4 --lan 访问地址仍是 127.0.0.1（不用 0.0.0.0）",
          f"http://127.0.0.1:{base + 1}" in out2)
    check("C5 --lan 横幅标出首选局域网地址", "（首选）" in out2)
    stop(p2)
    time.sleep(1)

    # ---------- --host 0.0.0.0 等价 --lan ----------
    print("\n[B] --host 0.0.0.0 等价 --lan")
    p3, up3 = run_case(cmd + ["--host", "0.0.0.0", "--port", str(base + 2), "--no-browser"],
                       base + 2, "127.0.0.1", cwd)
    check("B4 --host 0.0.0.0 局域网可连", can_connect(lan_ip, base + 2))
    check("D2 --host 0.0.0.0 本机可连", up3)
    stop(p3)
    time.sleep(1)

    # ---------- --host <本机 IP> 只绑单网卡 ----------
    print("\n[B] --host <本机 IP>（单网卡）")
    p4, up4 = run_case(cmd + ["--host", lan_ip, "--port", str(base + 3), "--no-browser"],
                       base + 3, lan_ip, cwd)
    check("B5 指定网卡可连", up4)
    check("B6 指定网卡时回环不可连（确认只绑该网卡）", not can_connect("127.0.0.1", base + 3))
    stop(p4)
    time.sleep(1)

    # ---------- E. 非法地址 + 编码安全 ----------
    print("\n[E] 非法地址拦截与控制台编码安全")
    r = subprocess.run(cmd + ["--host", "999.999.999.999", "--port", str(base + 4),
                              "--no-browser"],
                       cwd=cwd, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    check("E1 非法监听地址非零退出", r.returncode != 0, f"returncode={r.returncode}")
    check("E2 给出中文错误提示", "监听地址无效" in decode_out(r.stdout or b""))

    # 横幅必须能在 GBK 控制台打印（⚠/emoji 会让 exe 直接崩）
    try:
        (L.BANNER + L.LAN_BANNER).encode("gbk")
        gbk_ok = True
    except UnicodeEncodeError as e:
        gbk_ok = False
        print(f"     编码失败：{e}")
    check("E3 横幅可被 GBK 编码（不会在中文控制台崩溃）", gbk_ok)

    # ---------- F. 系统维护页开关（需服务，仅 src 模式）----------
    api_base = sys.argv[3] if len(sys.argv) > 3 else ""
    if mode == "src" and api_base:
        print("\n[F] 系统维护页开关（settings 读写 + 启动器默认行为）")
        import json
        import urllib.parse
        import urllib.request

        def _api(path, body=None, token=None, method=None):
            data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
            req = urllib.request.Request(api_base + path, data=data,
                                         method=method or ("POST" if body else "GET"))
            req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("Authorization", "Bearer " + token)
            return json.loads(urllib.request.urlopen(req, timeout=30).read().decode())

        cap = _api("/api/auth/captcha")
        c = (cap.get("data") or {}).get("debug_code") or ""
        tok = _api("/api/auth/login", {"account": "admin", "password": "Admin@123",
                                       "captcha_id": (cap.get("data") or {}).get("captcha_id"),
                                       "captcha_code": c})["data"]["access_token"]

        s0 = _api("/api/system/settings", token=tok)["data"]
        check("F1 设置接口返回 network 段", "network" in s0 and "lan_access" in s0["network"])
        check("F2 设置接口返回当前监听信息", "listen" in s0
              and {"host", "port", "wildcard", "lan_urls"} <= set(s0["listen"]),
              s0.get("listen"))
        r_off = _api("/api/system/settings", {"network": {"lan_access": 0}},
                     token=tok, method="PUT")["data"]
        check("F3 可关闭局域网开关", r_off["network"]["lan_access"] == 0)
        r_on = _api("/api/system/settings", {"network": {"lan_access": 1}},
                    token=tok, method="PUT")["data"]
        check("F4 可重新开启并回读", r_on["network"]["lan_access"] == 1)

        # 启动器默认行为：开关开 → 全部网卡；开关关 → 仅本机
        _api("/api/system/settings", {"network": {"lan_access": 1}}, token=tok, method="PUT")
        p5, _ = run_case(cmd + ["--port", str(base + 5), "--no-browser"],
                         base + 5, "127.0.0.1", cwd)
        check("F5 开关开启时默认监听全部网卡", can_connect(lan_ip, base + 5))
        stop(p5)
        time.sleep(1)
        _api("/api/system/settings", {"network": {"lan_access": 0}}, token=tok, method="PUT")
        p6, _ = run_case(cmd + ["--port", str(base + 6), "--no-browser"],
                         base + 6, "127.0.0.1", cwd)
        check("F6 开关关闭时默认仅本机（局域网不可连）", not can_connect(lan_ip, base + 6))
        stop(p6)
        time.sleep(1)
        # 还原默认（交付要求是默认开启）
        _api("/api/system/settings", {"network": {"lan_access": 1}}, token=tok, method="PUT")
    else:
        print("\n[F] 跳过（仅源码模式 + 需要服务地址）")

    print("=" * 62)
    print(f"结果：{passed}/{total} 通过，{failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
