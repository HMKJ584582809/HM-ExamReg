# -*- coding: utf-8 -*-
"""
考试报名信息采集与审核管理系统 · 桌面启动器（exe 入口）

职责：
  1. 初始化数据库与运行目录（exe 同级 data/）
  2. 启动 Web 服务（默认 127.0.0.1:8765 仅本机；--lan / --host 0.0.0.0 可开放局域网）
  3. 自动打开系统默认浏览器进入系统首页
  4. 控制台输出访问地址、初始管理员账号与角色说明，Ctrl+C 退出
"""
import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

# 网络工具无副作用（只依赖 os/socket），可在模块级导入；
# 注意 config 不能在模块级导入——它会创建 data/ 目录，且测试脚本依赖 L.is_wildcard 等属性
from app.netutil import is_wildcard, lan_ips, valid_host

def _safe_console():
    """控制台兜底：Windows 中文环境是 GBK，输出里出现 ⚠ 这类字符会 UnicodeEncodeError 直接崩溃。

    保持原编码不变（改成 utf-8 会让中文在 GBK 控制台乱码），只把无法编码的字符换成 ?。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


BANNER = r"""
==============================================================
        考试报名信息采集与审核管理系统  v{version}
   Windows 桌面版（exe 内核 + Web 操作）
==============================================================
  访问地址 : {local_url}
  数据目录 : {data_dir}
  初始账号 : {admin_user} / {admin_pwd}
             （首次运行自动创建，请登录后立即修改密码）
  其他账号 : 由管理员在「用户与权限管理」中开通
             考生也可在登录页自助注册
  角色说明 : 管理员 / 审核员 / 二级学院审核 / 班主任 / 考生
  关闭服务 : 在本窗口按 Ctrl+C
==============================================================
"""

# 监听全部网卡时的附加说明（局域网地址 + 安全提醒）
LAN_BANNER = """  监听地址 : {host}:{port}（全部网卡，局域网可访问）
{lan_lines}
  [!] 安全提醒 : 局域网内任何设备都可打开本系统，请确认已修改初始管理员密码；
                若同事无法访问，请在 Windows 防火墙放行本程序或 {port} 端口。
==============================================================
"""

def port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def pick_port(host: str, start: int) -> int:
    for p in range(start, start + 30):
        if port_free(host, p):
            return p
    raise SystemExit("找不到可用端口，请使用 --port 指定其他端口")


def main():
    _safe_console()
    parser = argparse.ArgumentParser(description="考试报名信息采集与审核管理系统")
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXAM_PORT", "8765")),
                        help="服务端口（默认 8765）")
    parser.add_argument("--host", default=os.environ.get("EXAM_HOST"),
                        help="监听地址（默认 127.0.0.1 仅本机；0.0.0.0 监听全部网卡）")
    parser.add_argument("--lan", action="store_true",
                        help="局域网模式：等价于 --host 0.0.0.0，允许同一局域网内其他设备访问")
    parser.add_argument("--no-browser", action="store_true", help="启动后不自动打开浏览器")
    parser.add_argument("--seed-demo", action="store_true",
                        help="演示模式：额外写入演示考试批次、演示报名数据与演示账号")
    args = parser.parse_args()

    if args.seed_demo:
        os.environ["EXAM_SEED_DEMO"] = "1"

    import uvicorn
    from app.config import (APP_VERSION, DATA_DIR, DEFAULT_ADMIN_PASSWORD,
                            DEFAULT_ADMIN_USERNAME, HOST, SEED_DEMO, load_settings)
    from app.main import app

    # 优先级：命令行 --lan > --host > 环境变量 EXAM_LAN > 系统维护页保存的开关 > 默认值
    if args.lan:
        host = "0.0.0.0"
    elif args.host:
        host = args.host
    else:
        env_lan = (os.environ.get("EXAM_LAN") or "").strip()
        if env_lan in ("0", "1"):
            lan_on = env_lan == "1"          # 环境变量显式指定，便于自动化测试
        else:
            lan_on = bool((load_settings().get("network") or {}).get("lan_access", 0))
        host = "0.0.0.0" if lan_on else HOST

    if not valid_host(host):
        raise SystemExit(f"监听地址无效：{host}\n"
                         f"可填本机某个 IP（如 192.168.1.20）、127.0.0.1 或 0.0.0.0（全部网卡）")

    port = pick_port(host, args.port)
    # 供后端 /api/system/info 回显当前监听地址（开发模式直跑 uvicorn 时没有这两个变量）
    os.environ["EXAM_LISTEN_HOST"] = host
    os.environ["EXAM_LISTEN_PORT"] = str(port)
    # 监听全部网卡时不能用 0.0.0.0 当访问地址（浏览器打不开），本机一律走回环
    local_url = f"http://{'127.0.0.1' if is_wildcard(host) else host}:{port}"

    extra = ""
    if is_wildcard(host):
        ips = lan_ips()
        # 首个是默认出口网卡，标注为首选；其余可能是虚拟机/WSL 的虚拟网卡
        lan_lines = ("\n".join("      http://%s:%d%s" % (ip, port, "（首选）" if i == 0 else "")
                               for i, ip in enumerate(ips))
                     or "      （未检测到局域网地址，请检查网络连接）")
        extra = LAN_BANNER.format(host=host, port=port, lan_lines=lan_lines)

    # flush=True：控制台下也要立刻显示，别被 stdout 缓冲吞掉
    print(BANNER.format(version=APP_VERSION, local_url=local_url, port=port, data_dir=DATA_DIR,
                        admin_user=DEFAULT_ADMIN_USERNAME, admin_pwd=DEFAULT_ADMIN_PASSWORD),
          end="", flush=True)
    if extra:
        print(extra, end="", flush=True)
    if SEED_DEMO:
        print("  [演示模式] 已写入演示数据与演示账号：")
        print("    审核员 reviewer / Reviewer@123    考生 candidate / Candidate@123")
        print("    班主任 teacher / Teacher@123 （管理多班级，如 计算机2101,软件工程2202）")
        print("=" * 62)

    if not args.no_browser:
        def _open():
            time.sleep(1.5)
            try:
                webbrowser.open(local_url)
            except Exception:
                pass
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    sys.exit(main())
