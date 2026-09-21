# -*- coding: utf-8 -*-
"""网络工具：局域网地址探测与监听地址判定。

启动器（launcher.py）与后端（routers/system.py）共用同一套逻辑，
避免「启动时显示的地址」与「系统维护页显示的地址」不一致。
"""
import os
import socket

# 视为「监听全部网卡」的地址
WILDCARD_HOSTS = ("0.0.0.0", "::", "0:0:0:0:0:0:0:0", "")


def is_wildcard(host: str) -> bool:
    """是否监听全部网卡。

    此时本机也要用 127.0.0.1 访问——Windows 下浏览器打不开 http://0.0.0.0。
    """
    return (host or "").strip() in WILDCARD_HOSTS


def lan_ips():
    """取本机局域网 IPv4 地址列表（默认出口网卡排在最前）。

    用 UDP connect 取出口网卡：只查路由表，不实际发包，也不受系统代理影响。
    无外网时静默降级为解析主机名。
    """
    ips, seen = [], set()

    def _add(ip):
        if ip and not ip.startswith("127.") and ip not in seen:
            seen.add(ip)
            ips.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("223.5.5.5", 80))
            _add(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            _add(info[4][0])
    except socket.gaierror:
        pass
    return ips


def valid_host(host: str) -> bool:
    """监听地址是否可解析（wildcard 一律放行）。"""
    if is_wildcard(host):
        return True
    try:
        socket.getaddrinfo(host, None)
        return True
    except (socket.gaierror, UnicodeError):
        return False


def listen_host() -> str:
    """当前实际监听地址。由 launcher 在起服务前写入环境变量。"""
    return os.environ.get("EXAM_LISTEN_HOST", "") or ""


def listen_port() -> int:
    try:
        return int(os.environ.get("EXAM_LISTEN_PORT", "0") or 0)
    except ValueError:
        return 0


def access_urls():
    """返回可访问地址：本机地址 + 局域网地址列表。

    结构：{"host", "port", "wildcard", "local_url", "lan_urls"}
    未由 launcher 启动（开发模式直跑 uvicorn）时 host 为空。
    """
    host, port = listen_host(), listen_port()
    wildcard = is_wildcard(host)
    local = f"http://{'127.0.0.1' if wildcard else (host or '127.0.0.1')}:{port}" if port else ""
    return {
        "host": host,
        "port": port,
        "wildcard": wildcard,
        "local_url": local,
        "lan_urls": [f"http://{ip}:{port}" for ip in lan_ips()] if port else [],
    }
