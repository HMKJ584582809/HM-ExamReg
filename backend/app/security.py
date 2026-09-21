# -*- coding: utf-8 -*-
"""安全组件：密码哈希、JWT 令牌、图形验证码、登录限流。"""
import base64
import hashlib
import hmac
import io
import os
import random
import string
import time
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from PIL import Image, ImageDraw, ImageFont

from .config import (ACCESS_TOKEN_EXPIRE_MINUTES, REFRESH_TOKEN_EXPIRE_DAYS,
                     SECRET_PATH, ensure_dirs)

_ALGO = "HS256"
_PBKDF2_ROUNDS = 200_000


# ------------------------------------------------------------- 密钥

def _load_secret() -> str:
    ensure_dirs()
    if SECRET_PATH.exists():
        s = SECRET_PATH.read_text(encoding="utf-8").strip()
        if s:
            return s
    s = base64.urlsafe_b64encode(os.urandom(48)).decode()
    SECRET_PATH.write_text(s, encoding="utf-8")
    return s


SECRET_KEY = _load_secret()


# ------------------------------------------------------------- 密码

def hash_password(password: str) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希（等价于 bcrypt 的存储安全强度，无第三方依赖）。"""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return "pbkdf2_sha256${}${}${}".format(
        _PBKDF2_ROUNDS, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(rounds))
        return hmac.compare_digest(dk, base64.b64decode(hash_b64))
    except Exception:
        return False


def password_strength_error(password: str):
    if len(password or "") < 8:
        return "密码长度不能少于 8 位"
    kinds = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if kinds < 2:
        return "密码需包含字母、数字或符号中的至少两类"
    return None


# ------------------------------------------------------------- JWT

def create_token(user_id: int, role: str, kind: str = "access", minutes: int = 0) -> str:
    """签发令牌。minutes 为 0 时按 kind 取默认有效期；「不记住我」时前端会传入较短时长。"""
    if minutes:
        exp_minutes = minutes
    else:
        exp_minutes = (ACCESS_TOKEN_EXPIRE_MINUTES if kind == "access"
                       else REFRESH_TOKEN_EXPIRE_DAYS * 1440)
    payload = {
        "sub": str(user_id),
        "role": role,
        "kind": kind,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_minutes * 60,
        "jti": uuid.uuid4().hex[:12],
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=_ALGO)


def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[_ALGO])
    except jwt.ExpiredSignatureError:
        raise ValueError("登录已过期，请重新登录")
    except Exception:
        raise ValueError("登录凭证无效")


# ------------------------------------------------------------- 验证码

_captcha_store = {}
_CAPTCHA_TTL = 300


def _rand_color(lo=40, hi=170):
    return (random.randint(lo, hi), random.randint(lo, hi), random.randint(lo, hi))


def gen_captcha() -> dict:
    """生成 4 位图形验证码，返回 captcha_id 与 base64 图片。"""
    code = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
    w, h = 120, 40
    img = Image.new("RGB", (w, h), (245, 247, 250))
    draw = ImageDraw.Draw(img)
    font = None
    for path in (r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\arial.ttf",
                 r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            font = ImageFont.truetype(path, 26)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    for i, ch in enumerate(code):
        draw.text((10 + i * 26 + random.randint(-3, 3), random.randint(4, 10)),
                  ch, font=font, fill=_rand_color())
    for _ in range(6):
        draw.line([(random.randint(0, w), random.randint(0, h)),
                   (random.randint(0, w), random.randint(0, h))], fill=_rand_color(120, 200), width=1)
    for _ in range(60):
        draw.point((random.randint(0, w), random.randint(0, h)), fill=_rand_color(120, 210))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()

    cid = uuid.uuid4().hex
    _clean_captcha()
    _captcha_store[cid] = (code.upper(), time.time() + _CAPTCHA_TTL)
    result = {"captcha_id": cid, "image": f"data:image/png;base64,{b64}"}
    from .config import DEV_MODE
    if DEV_MODE:
        result["debug_code"] = code.upper()
    return result


def _clean_captcha():
    now = time.time()
    for k in [k for k, v in _captcha_store.items() if v[1] < now]:
        _captcha_store.pop(k, None)


def verify_captcha(captcha_id: str, code: str) -> bool:
    _clean_captcha()
    item = _captcha_store.get(captcha_id or "")
    if not item:
        return False
    stored, expire = item
    _captcha_store.pop(captcha_id, None)  # 一次性
    return expire >= time.time() and (code or "").strip().upper() == stored


# ------------------------------------------------- 手机 / 邮箱验证码
# 提示词要求「验证码（手机/邮箱二选一）」。桌面单机场景下若无短信/邮件网关，
# 通道自动降级为「演示模式」：验证码写入服务端控制台，便于管理员转告或自测。

_code_store = {}   # (target_type, target, purpose) -> {code, expire, last_send, send_count}


def gen_numeric_code(length: int = 6) -> str:
    return "".join(random.choices(string.digits, k=length))


def _mask(target: str) -> str:
    if "@" in target:
        name, _, domain = target.partition("@")
        return (name[:2] + "***@" + domain) if len(name) > 2 else ("***@" + domain)
    return target[:3] + "****" + target[-4:] if len(target) >= 7 else "***"


def code_send_blocked(target_type: str, target: str, purpose: str):
    """重发间隔与每日次数限制，返回错误文案或 None。"""
    from .config import load_settings
    cfg = load_settings()
    rec = _code_store.get((target_type, target, purpose))
    if not rec:
        return None
    now = time.time()
    gap = cfg.get("code_resend_seconds", 60)
    if now - rec.get("last_send", 0) < gap:
        return f"验证码已发送，请 {int(gap - (now - rec['last_send']))} 秒后再试"
    if rec.get("send_count", 0) >= cfg.get("code_daily_limit", 10):
        return "该联系方式今日获取验证码次数已达上限，请明日再试"
    return None


def issue_code(target_type: str, target: str, purpose: str) -> dict:
    """生成并投递验证码，返回投递结果（含 delivery 方式）。"""
    from .config import load_settings, mail_configured
    cfg = load_settings()
    ttl = int(cfg.get("code_ttl_seconds", 300))
    code = gen_numeric_code()
    rec = _code_store.get((target_type, target, purpose), {})
    _code_store[(target_type, target, purpose)] = {
        "code": code,
        "expire": time.time() + ttl,
        "last_send": time.time(),
        "send_count": rec.get("send_count", 0) + 1,
    }

    delivery, detail = "console", ""
    if target_type == "email" and mail_configured():
        ok, detail = _send_mail(target, code, ttl)
        delivery = "smtp" if ok else "console"
    elif target_type == "sms":
        from .config import sms_configured
        if sms_configured():
            ok, detail = _send_sms(target, code, ttl)
            # 发送失败必须诚实降级为演示模式并打印原因，不能谎报「已发送」
            delivery = "sms" if ok else "console"
        else:
            detail = "未配置短信网关"

    masked = _mask(target)
    if delivery == "console":
        print(f"\n[验证码] {target_type} {masked} 用途={purpose} 验证码={code} "
              f"（{ttl // 60} 分钟内有效；当前未配置{'邮件' if target_type == 'email' else '短信'}网关，"
              f"此为演示模式，验证码仅在本控制台输出）\n", flush=True)
    return {"delivery": delivery, "target_masked": masked, "expires_in": ttl, "detail": detail,
            "resend_after": int(cfg.get("code_resend_seconds", 60)),
            "code": code if delivery == "console" else None}


def _send_mail(target: str, code: str, ttl: int):
    import smtplib
    from email.header import Header
    from email.mime.text import MIMEText
    from .config import APP_NAME, load_settings
    cfg = load_settings()["smtp"]
    try:
        msg = MIMEText(
            f"您正在注册「{APP_NAME}」，验证码为：{code}\n"
            f"有效期 {ttl // 60} 分钟，请勿转发他人。如非本人操作请忽略本邮件。",
            "plain", "utf-8")
        msg["Subject"] = Header(f"【{APP_NAME}】注册验证码", "utf-8")
        msg["From"] = cfg.get("sender") or cfg["user"]
        msg["To"] = target
        port = int(cfg.get("port", 465))
        if cfg.get("ssl", True):
            server = smtplib.SMTP_SSL(cfg["host"], port, timeout=10)
        else:
            server = smtplib.SMTP(cfg["host"], port, timeout=10)
            server.starttls()
        server.login(cfg["user"], cfg["password"])
        server.sendmail(cfg.get("sender") or cfg["user"], [target], msg.as_string())
        server.quit()
        return True, ""
    except Exception as e:
        print(f"[邮件发送失败] {target}: {e}（已降级为演示模式）", flush=True)
        return False, str(e)


def _send_sms(target: str, code: str, ttl: int):
    """真实投递短信验证码。

    只用标准库（urllib / hmac / uuid），避免给 PyInstaller 增加第三方依赖。
    支持两种网关：

    - ``aliyun``：阿里云短信服务，api_key 形如 ``AccessKeyId:AccessKeySecret``，
      另需 ``sign``（短信签名）与 ``template``（模板 CODE）；
    - ``generic``：自建/第三方 HTTP 网关，向其 ``endpoint`` POST JSON
      ``{"phone","code","sign","template"}``，用 ``Authorization: Bearer <api_key>`` 鉴权。

    返回 ``(是否成功, 失败原因)``；任何异常都不向上抛出。
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request
    from .config import APP_NAME, load_settings

    cfg = load_settings()["sms"]
    provider = (cfg.get("provider") or "").strip().lower()
    minutes = max(1, ttl // 60)
    text = (f"您正在使用「{APP_NAME}」，验证码为：{code}"
            f"（{minutes} 分钟内有效，请勿转发他人）。")
    try:
        if provider == "aliyun":
            ak_id, _, ak_secret = (cfg.get("api_key") or "").partition(":")
            params = {
                "AccessKeyId": ak_id,
                "Action": "SendSms",
                "Format": "JSON",
                "PhoneNumbers": target,
                "RegionId": "cn-hangzhou",
                "SignName": cfg.get("sign") or "",
                "SignatureMethod": "HMAC-SHA1",
                "SignatureNonce": uuid.uuid4().hex,
                "SignatureVersion": "1.0",
                "TemplateCode": cfg.get("template") or "",
                "TemplateParam": _json.dumps({"code": code}, ensure_ascii=False),
                "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "Version": "2017-05-25",
            }
            # 阿里云签名规则：参数按字典序排序后做 HMAC-SHA1
            canon = "&".join(
                f"{_pct(k)}={_pct(params[k])}" for k in sorted(params))
            to_sign = "POST" + "&" + _pct("/") + "&" + _pct(canon)
            signed = hmac.new((ak_secret + "&").encode(), to_sign.encode(),
                              hashlib.sha1).digest()
            params["Signature"] = base64.b64encode(signed).decode()
            body = urllib.parse.urlencode(params).encode()
            req = urllib.request.Request(
                cfg.get("endpoint") or "https://dysmsapi.aliyuncs.com/",
                data=body, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = _json.dumps({"phone": target, "code": code, "text": text,
                                "sign": cfg.get("sign") or "",
                                "template": cfg.get("template") or ""},
                               ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(cfg["endpoint"], data=body, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", "Bearer " + (cfg.get("api_key") or ""))

        timeout = int(cfg.get("timeout") or 10)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "ignore")
        if "Code" in raw and '"Code":"OK"' not in raw and '"Code": "OK"' not in raw:
            return False, f"网关返回异常：{raw[:200]}"
        return True, ""
    except urllib.error.HTTPError as e:
        err = f"网关返回 HTTP {e.code}"
        print(f"[短信发送失败] {target}: {err}（已降级为演示模式）", flush=True)
        return False, err
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        print(f"[短信发送失败] {target}: {err}（已降级为演示模式）", flush=True)
        return False, err


def _pct(s) -> str:
    """阿里云签名用的百分号编码（空格编码为 %20 而非 +）。"""
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


def verify_code(target_type: str, target: str, purpose: str, code: str) -> bool:
    key = (target_type, target, purpose)
    rec = _code_store.get(key)
    if not rec:
        return False
    if rec["expire"] < time.time():
        _code_store.pop(key, None)
        return False
    if (code or "").strip() != rec["code"]:
        return False
    _code_store.pop(key, None)   # 一次性
    return True


def peek_code(target_type: str, target: str, purpose: str):
    """仅供开发/自测读取当前验证码。"""
    rec = _code_store.get((target_type, target, purpose))
    return rec["code"] if rec and rec["expire"] >= time.time() else None


# ------------------------------------------------------------- 登录限流

_login_fail = {}
_MAX_FAIL = 5
_LOCK_SECONDS = 300


def login_blocked(username: str):
    rec = _login_fail.get(username)
    if not rec:
        return None
    count, first = rec
    if count >= _MAX_FAIL and time.time() - first < _LOCK_SECONDS:
        left = int(_LOCK_SECONDS - (time.time() - first))
        return f"登录失败次数过多，请 {left} 秒后重试"
    if time.time() - first >= _LOCK_SECONDS:
        _login_fail.pop(username, None)
    return None


def record_login_fail(username: str):
    count, first = _login_fail.get(username, (0, time.time()))
    if time.time() - first >= _LOCK_SECONDS:
        count, first = 0, time.time()
    _login_fail[username] = (count + 1, first)


def clear_login_fail(username: str):
    _login_fail.pop(username, None)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
