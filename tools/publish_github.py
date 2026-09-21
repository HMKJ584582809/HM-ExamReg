#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把本工程一键发布到 GitHub：建仓 → 提交推送 → 发 Release（附 exe）。

用法：
    set GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
    python tools/publish_github.py                       # 全量：建仓+推送+发版
    python tools/publish_github.py --skip-push           # 只发 Release（仓库已推过）
    python tools/publish_github.py --tag v1.1.0 --exe dist/考试报名系统.exe

    python tools/publish_github.py --device-login        # 第 1 次：打印网址+验证码，去浏览器授权
    python tools/publish_github.py --device-login        # 第 2 次：授权完了，直接接着发版

设计要点（踩过的坑都写在注释里，别改回去）：
1. **只用 stdlib**（urllib），跟后端 AI 模块保持一致，不引 requests。
   代理必须显式给 ProxyHandler，urllib 不会自动读 http_proxy 环境变量的
   https 分支在某些版本上不生效，直接给全。
2. **PAT 不落盘**：remote 用 https://<token>@github.com/... 只是为了过一次鉴权，
   推完立刻 `git remote set-url` 改回不带 token 的地址，`.git/config` 里不留密钥。
3. **仓库已存在不报错**：POST /user/repos 返回 422 (name already exists) 时
   改走 GET /repos/{owner}/{repo} 复用，脚本可反复跑。
4. **Release 已存在也不报错**：GET /repos/.../releases/tags/{tag} 命中就复用其 id，
   同名资产先删再传，避免 422 validation_failed。
5. **exe 走 Release 资产，绝不随仓库版本化**：70MB 二进制进 git 会让仓库膨胀，
   且 GitHub 单文件硬上限 100MB，升级几次就顶死了。
6. **设备码登录（--device-login）**：不想把 PAT 贴出来时走 OAuth Device Flow，
   跟 `gh auth login --web` 是同一套机制，浏览器里点一下授权即可，不用装 gh。
   状态落在 `.gh_device.json`（已 gitignore）。第一次跑只申请设备码并打印给用户，
   用户授权后再跑同一条命令，脚本用 device_code 换 access_token 并缓存。
   ⚠ device_code **只能用一次**，换到 token 后必须立刻落盘；否则第二次跑 GitHub
   返回 `expired_token`，表现为「明明在浏览器点过授权了却说没授权」。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROXY = "http://10.26.0.2:666"
API = "https://api.github.com"
UPLOADS = "https://uploads.github.com"

OK, FAIL, INFO, WARN = "[OK]", "[FAIL]", "[..]", "[!!]"
_n_ok = _n_fail = 0

# ghp_/ghu_/gho_/ghs_/ghr_ 是 PAT，github_pat_ 是 fine-grained token
_TOKEN_RE = __import__("re").compile(r"(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})")


def _mask(s: str) -> str:
    """任何要打印到终端/日志的文本都过一遍，别把 token 打出去。"""
    return _TOKEN_RE.sub("***", s)


def say(tag: str, text: str) -> None:
    global _n_ok, _n_fail
    print(f"{tag} {text}", flush=True)
    if tag == OK:
        _n_ok += 1
    elif tag == FAIL:
        _n_fail += 1


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
_opener: urllib.request.OpenerDirector | None = None


def _build_opener(proxy: str) -> urllib.request.OpenerDirector:
    # ⚠ 必须同时给 http 和 https：urllib 只认 https_proxy 时，
    # 重定向到 http 的 URL 会直连（然后超时）。
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def http(method: str, url: str, token: str, body: dict | bytes | None = None,
         ctype: str = "application/json", raw: bool = False,
         timeout: int = 120):
    """返回 (status, dict|bytes)。4xx/5xx 不抛异常，交给调用方判断。"""
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("User-Agent", "exam-signup-publisher")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", ctype)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            payload = resp.read()
            return resp.status, (payload if raw else (json.loads(payload) if payload else {}))
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, (payload if raw else (json.loads(payload) if payload else {}))
        except Exception:
            return e.code, {"message": payload[:400].decode("utf-8", "ignore")}


# --------------------------------------------------------------------------- #
# OAuth 设备码登录（不想把 PAT 贴出来时用，等价于 gh auth login --web）
# --------------------------------------------------------------------------- #
CLIENT_ID = "Iv1.b507a08c87ecfe98"      # GitHub CLI 的公开 OAuth App，第三方工具通用
STATE_FILE = ROOT / ".gh_device.json"
DEVICE_URI = "https://github.com/login/device"
OAUTH_TOKEN_URI = "https://github.com/login/oauth/access_token"


def _form(**kw) -> bytes:
    return urllib.parse.urlencode(kw).encode("utf-8")


def _post_form(url: str, data: bytes, timeout: int = 60) -> dict:
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("User-Agent", "exam-signup-publisher")
    with _opener.open(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(st: dict) -> None:
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def _device_start() -> None:
    """申请设备码 → 打印给用户 → 本次结束（等用户授权后再跑一次）。"""
    r = _post_form(f"{DEVICE_URI}/code", _form(client_id=CLIENT_ID, scope="repo"))
    expires = r.get("expires_in", 900)
    _save_state({"device_code": r["device_code"],
                 "user_code": r.get("user_code", ""),
                 "verification_uri": r.get("verification_uri", DEVICE_URI),
                 "interval": r.get("interval", 5),
                 "expire": time.time() + expires})
    print("\n" + "=" * 64)
    print("   浏览器打开 :", r.get("verification_uri", DEVICE_URI))
    print("   输入验证码 :", r["user_code"])
    print(f"   验证码 {expires // 60} 分钟内有效，授权完成后**再跑一次**本命令")
    print("=" * 64 + "\n", flush=True)


def _device_poll(st: dict, verbose: bool = True) -> str | None:
    if time.time() > st.get("expire", 0):
        say(WARN, "设备码已过期，重新申请")
        STATE_FILE.unlink(missing_ok=True)
        return None
    try:
        r = _post_form(OAUTH_TOKEN_URI,
                       _form(client_id=CLIENT_ID, device_code=st["device_code"],
                             grant_type="urn:ietf:params:oauth:grant-type:device_code"))
    except urllib.error.HTTPError as e:
        say(WARN, f"换 token 请求失败: {e}")
        return None
    if "access_token" in r:
        st["token"] = r["access_token"]
        st.pop("device_code", None)      # ⚠ device_code 一次性，换到 token 立刻丢弃
        _save_state(st)
        say(OK, f"浏览器授权成功（已获 scope: {r.get('scope', '')}）")
        return r["access_token"]
    err = r.get("error")
    if err == "authorization_pending":
        if verbose:
            say(INFO, f"等待浏览器授权 → {st.get('verification_uri', DEVICE_URI)}（{st.get('user_code', '')}）")
        else:
            print(".", end="", flush=True)
        return None
    if err in ("expired_token", "access_denied", "incorrect_device_code", "incorrect_client_credentials"):
        say(WARN, f"设备码失效（{err}），已清除；再跑一次会重新申请")
        STATE_FILE.unlink(missing_ok=True)
        return None
    say(WARN, f"换 token 失败: {r}")
    return None


def device_login(wait: int = 0) -> str:
    """返回可用 token；若还等用户授权，打印网址/验证码后以 0 退出。

    `wait>0` 时在这里轮询等待（省得用户授权完还得再敲一次命令）：
    第一次跑 → 打印验证码退出；用户去浏览器点授权；第二次跑 → 本函数等到授权完成
    再继续后面的建仓/推送/发版。
    """
    st = _load_state()
    if st.get("token"):
        return st["token"]                     # 有效性交给随后的 /user 校验
    if not st.get("device_code"):
        _device_start()
        raise SystemExit(0)

    deadline = time.time() + max(0, wait)
    interval = max(2, int(st.get("interval", 5)))
    first = True
    while True:
        t = _device_poll(st, verbose=first)
        if t:
            print()
            return t
        if time.time() >= deadline:
            print()
            raise SystemExit(0)
        first = False
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# Git
# --------------------------------------------------------------------------- #
def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["git", *args]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        # ⚠ 必须脱敏：push 的 URL 里带 token，git 报错会原样回显，
        #   一崩就把 token 打进日志/终端（真出过一次）
        raise RuntimeError(_mask(f"git {' '.join(args)} 失败:\n{r.stdout}\n{r.stderr}"))
    return r


def ensure_repo(proxy: str, branch: str) -> None:
    if not (ROOT / ".git").exists():
        git("init", "-q")
        git("config", "core.autocrlf", "false")   # 别让 CRLF 把 .sh/.py 全改一遍
        say(OK, "git init 完成")
    if proxy:
        git("config", "http.proxy", proxy)
        git("config", "https.proxy", proxy)
        say(OK, f"已配置代理 {proxy}")

    git("add", "-A")
    # 没有改动就不提交，避免空 commit 让脚本第二次跑失败
    status = git("status", "--porcelain").stdout.strip()
    if status:
        git("commit", "-q", "-m", "feat: 考试报名信息采集与审核管理系统 v1.1.0")
        say(OK, "已提交本地 commit")
    else:
        say(INFO, "工作区无变化，跳过 commit")
    git("branch", "-M", branch, check=False)


def push(token: str, owner: str, repo: str, branch: str) -> None:
    """推完立刻把 remote 里的 token 抹掉。"""
    safe = f"https://github.com/{owner}/{repo}.git"
    with_token = f"https://{token}@github.com/{owner}/{repo}.git"
    remotes = git("remote").stdout.split()
    if "origin" not in remotes:
        git("remote", "add", "origin", safe)
    try:
        git("push", "-q", with_token, f"{branch}:{branch}")
        say(OK, f"已推送 {branch} → {safe}")
    finally:
        # ⚠ finally：push 失败也要清 token，别把 PAT 留在 .git/config
        git("remote", "set-url", "origin", safe)
        git("config", "--unset", "http.proxy", check=False)
        git("config", "--unset", "https.proxy", check=False)
        say(OK, "已清除 remote 中的 token 与代理配置")


# --------------------------------------------------------------------------- #
# GitHub
# --------------------------------------------------------------------------- #
def whoami(token: str) -> dict:
    st, me = http("GET", f"{API}/user", token)
    if st != 200:
        raise SystemExit(f"{FAIL} 认证失败({st}): {me.get('message')}\n  请检查 PAT 是否有效、是否勾选 repo 权限")
    say(OK, f"已认证为 GitHub 用户：{me['login']}")
    return me


def ensure_remote_repo(token: str, name: str, private: bool, desc: str) -> dict:
    st, r = http("POST", f"{API}/user/repos", token, {
        "name": name, "description": desc, "private": private,
        "auto_init": False, "has_issues": True, "has_wiki": False,
    })
    if st == 201:
        say(OK, f"仓库已创建：{r['full_name']}（{'private' if private else 'public'}）")
        return r

    # 建仓失败不一定是错误，先看看仓库是不是已经在那儿了：
    #  - 422 name already exists   → 之前建过，直接复用
    #  - 403 "Resource not accessible by integration" → **GitHub App 令牌不能建仓**
    #    （设备码登录拿到的是 ghu_ 前缀的 App 用户令牌），但推送/发 Release 没问题，
    #    所以只要用户在网页上手动建好空仓，这里复用即可继续走完。
    owner = whoami(token)["login"]
    st2, r2 = http("GET", f"{API}/repos/{owner}/{name}", token)
    if st2 == 200:
        say(INFO, f"仓库已存在，复用：{r2['full_name']}")
        return r2
    raise SystemExit(
        f"{FAIL} 建仓失败({st}): {json.dumps(r, ensure_ascii=False)[:300]}\n"
        f"   请手动在 https://github.com/new 建一个空仓库：\n"
        f"   名称 `{name}`，选 Public，**不要勾** Add a README / .gitignore / license，\n"
        f"   建好后重跑本命令即可（脚本会自动复用）。\n"
        f"   若想让脚本自己建仓，请改用 classic PAT（勾 repo）而不是设备码登录。")


def ensure_release(token: str, full: str, tag: str, title: str, notes: str) -> dict:
    st, rel = http("GET", f"{API}/repos/{full}/releases/tags/{tag}", token)
    if st == 200:
        say(INFO, f"Release {tag} 已存在，复用 #{rel['id']}")
        return rel
    st, rel = http("POST", f"{API}/repos/{full}/releases", token, {
        "tag_name": tag, "name": title, "body": notes,
        "draft": False, "prerelease": False,
    })
    if st not in (200, 201):
        raise SystemExit(f"{FAIL} 创建 Release 失败({st}): {json.dumps(rel, ensure_ascii=False)[:400]}")
    say(OK, f"Release 已创建：{rel['html_url']}")
    return rel


def ascii_asset_name(name: str, fallback: str = "") -> str:
    """GitHub 资产名**必须是 ASCII**，这是实测结论，别再试中文了：
      - URL 里放未编码中文      → 400 Bad Request
      - 放百分号编码的中文      → GitHub 直接忽略 name，文件落成 `default.exe`
      - 上传后 PATCH 改成中文名 → 返回 200 但名字纹丝不动（静默失效）
      - ASCII 名上传/改名       → 正常
    所以非 ASCII 一律降级：优先用 --asset-name 指定的名字，其次按
    「扩展名保留 + 主体拼音化不可能」的处理，直接给一个通用 ASCII 名。
    """
    if name.isascii():
        return name
    ext = Path(name).suffix or ".exe"
    safe = fallback or ("ExamRegistration" + ext)
    print(f"{WARN} 资产名 `{name}` 含非 ASCII 字符，GitHub 会把它存成 `default{ext}`；"
          f"已改用 `{safe}`（想要别的名字请加 --asset-name）")
    return safe


def upload_asset(token: str, full: str, rel: dict, path: Path,
                 asset_name: str = "", tries: int = 3) -> None:
    if not path.exists():
        say(FAIL, f"找不到成品：{path}")
        return
    name = ascii_asset_name(asset_name or path.name)
    assets = rel.get("assets", []) or []
    for a in assets:                       # 同名资产先删，否则 422
        if a["name"] == name:
            http("DELETE", f"{API}/repos/{full}/releases/assets/{a['id']}", token)
            say(INFO, f"已删除同名旧资产 {name}")
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    url = f"{UPLOADS}/repos/{full}/releases/{rel['id']}/assets?name={urllib.parse.quote(name)}"
    size_mb = path.stat().st_size / 1024 / 1024
    # ⚠ 走代理时链路又慢又不稳（实测 ~110 KB/s，70MB 要 10 分钟，且中途常被 reset）。
    #   单次必失败，必须重试；别把 timeout 调小，40 秒的假失败就是这么来的。
    say(INFO, f"上传 {name}（{size_mb:.1f} MB，慢链路请耐心，最多重试 {tries} 次）…")
    for i in range(1, tries + 1):
        t0 = time.time()
        try:
            st, r = http("POST", url, token, body=path.read_bytes(), ctype=ctype, timeout=1800)
        except Exception as e:                       # URLError / ConnectionResetError
            st, r = 0, {"message": str(e)}
        if st in (200, 201) and "browser_download_url" in r:
            say(OK, f"资产已上传（第 {i} 次，{time.time() - t0:.0f}s）：{r['browser_download_url']}")
            return
        say(WARN, f"第 {i} 次上传失败（{st}，{time.time() - t0:.0f}s）："
                  f"{json.dumps(r, ensure_ascii=False)[:200]}")
    say(FAIL, f"{tries} 次都没传上去。可手动兜底：\n"
              f"   curl -x {DEFAULT_PROXY} -X POST --data-binary @{path} \\\n"
              f"     -H \"Authorization: token <PAT>\" -H \"Content-Type: application/octet-stream\" \\\n"
              f"     \"{url}\"")


# --------------------------------------------------------------------------- #
def main() -> int:
    global _opener
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""), help="GitHub PAT（或设 GITHUB_TOKEN）")
    ap.add_argument("--repo", default="HM-ExamReg")
    ap.add_argument("--tag", default="v1.1.0")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--private", action="store_true", help="建私有仓库（默认公开）")
    ap.add_argument("--exe", default="dist/考试报名系统.exe")
    ap.add_argument("--asset-name", default="", help="Release 资产名（必须 ASCII，中文会被存成 default.exe）")
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--skip-release", action="store_true")
    ap.add_argument("--device-login", action="store_true", help="浏览器设备码授权，不用贴 PAT")
    ap.add_argument("--wait", type=int, default=600, help="设备码登录后等待授权的秒数（默认 600）")
    args = ap.parse_args()

    # ⚠ opener 必须在 device_login() 之前建好，否则第一次申请设备码就走不了代理
    _opener = _build_opener(args.proxy)

    if not args.token:
        if args.device_login:
            args.token = device_login(args.wait)
        else:
            raise SystemExit(f"{FAIL} 缺少凭据：set GITHUB_TOKEN=ghp_xxx，或加 --device-login 走浏览器授权")

    try:
        me = whoami(args.token)
    except SystemExit:
        if args.device_login:                  # token 失效就别留着缓存误导下次
            STATE_FILE.unlink(missing_ok=True)
        raise
    owner = me["login"]
    email = me.get("email") or f"{owner}@users.noreply.github.com"
    git("config", "user.name", owner)
    git("config", "user.email", email)

    # ⚠ 顺序不能反：必须先建仓再 push。仓库不存在时 push 报
    #   "remote: Repository not found"，而且这个报错和「token 没权限」长得一模一样，
    #   很容易误判成鉴权问题（真踩过一次）。
    info = ensure_remote_repo(args.token, args.repo, args.private,
                              "考试报名信息采集与审核管理系统（FastAPI + Vue3 + SQLite，Windows 单文件 exe）")

    if not args.skip_push:
        ensure_repo(args.proxy, args.branch)
        push(args.token, owner, args.repo, args.branch)

    if args.skip_release:
        say(INFO, "--skip-release，跳过发版")
    else:
        full = info["full_name"]
        rel = ensure_release(args.token, full, args.tag, f"{args.tag} 考试报名信息采集与审核管理系统",
                             RELEASE_NOTES)
        exe = Path(args.exe) if os.path.isabs(args.exe) else ROOT / args.exe
        upload_asset(args.token, full, rel, exe, args.asset_name)

    print(f"\n完成：{_n_ok} 项成功 / {_n_fail} 项失败")
    return 1 if _n_fail else 0


RELEASE_NOTES = """## 考试报名信息采集与审核管理系统 v1.1.0

Windows 单文件可执行程序，**下载下方 `考试报名系统.exe` 双击即可运行**，无需安装 Python 或数据库。

### 使用
1. 双击 `考试报名系统.exe` → 自动启动本地服务并打开浏览器
2. 默认管理员：`admin` / `Admin@123`（首次登录请立即改密）
3. 关闭控制台窗口即退出服务

### 本版要点
- 权限细化：14 个功能权限组 × 4 档数据范围（班级 / 年级 / 学院 / 全校）
- 二级学院、部门（可归属学院）、学工办与专职教师分层
- 证件照制作：本地 MODNet 抠图 + 换底 + 裁剪，参数可调（留白填充、边缘羽化等）
- 实名认证、AI 智能审核（可配代理）、报名分表、导出模板、数据大屏
- 测试模式（管理员可开）：一键生成 5000 条模拟数据

### 源码
- 仓库含全部源码与文档；`data/`（数据库、证件照、密钥）已被 `.gitignore` 排除
- 抠图模型 `backend/app/assets/idphoto/hivision_modnet.onnx`（25 MB）随源码分发
- 从源码运行见 README「六、从源码运行与构建」

> 说明：仓库版本不含 exe，exe 仅随 Release 分发。
"""

if __name__ == "__main__":
    sys.exit(main())
