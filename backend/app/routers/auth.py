# -*- coding: utf-8 -*-
"""模块1：用户注册、登录、个人中心。"""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import db
from ..config import (ACCESS_TOKEN_EXPIRE_MINUTES, DEV_MODE,
                      SESSION_TOKEN_EXPIRE_MINUTES, mail_configured,
                      sms_configured)
from ..deps import ApiError, get_current_user, ok
from ..permissions import describe
from ..security import (clear_login_fail, code_send_blocked, create_token, decode_token,
                        gen_captcha, hash_password, issue_code, login_blocked,
                        password_strength_error, record_login_fail, verify_captcha,
                        verify_code, verify_password)

router = APIRouter(prefix="/api/auth", tags=["auth"])

PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
EMAIL_RE = re.compile(r"^[\w.\-+]+@[\w\-]+(\.[\w\-]+)+$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def _reset_settings() -> dict:
    """后台「找回密码」开关：总开关 + 三种验证方式，默认只开图形验证码。"""
    from ..config import load_settings
    rp = (load_settings().get("reset_password") or {})
    return {
        "enable": 1 if rp.get("enable", 1) else 0,
        "captcha": 1 if rp.get("captcha", 1) else 0,
        "sms": 1 if rp.get("sms") else 0,
        "email": 1 if rp.get("email") else 0,
    }


def _register_settings() -> dict:
    """后台「注册」验证方式开关。

    至少保留一种：全关时兜底打开图形验证码——否则考生根本注册不了，
    而管理员往往是在后台误操作才发现。
    """
    from ..config import load_settings
    rg = (load_settings().get("register") or {})
    out = {
        "captcha": 1 if rg.get("captcha", 1) else 0,
        "sms": 1 if rg.get("sms") else 0,
        "email": 1 if rg.get("email") else 0,
    }
    if not any(out.values()):
        out["captcha"] = 1
    return out


class RegisterIn(BaseModel):
    username: str
    phone: str
    email: str = ""
    real_name: str = ""
    password: str
    confirm_password: str
    # 验证方式：captcha 图形验证码 / email 邮箱验证码 / sms 手机验证码
    verify_type: str = "captcha"
    captcha_id: str = ""
    captcha_code: str = ""
    code_target: str = ""
    code: str = ""


class SendCodeIn(BaseModel):
    target_type: str          # email / sms
    target: str
    purpose: str = "register"


class LoginIn(BaseModel):
    account: str
    password: str
    remember: bool = True


class ChangePwdIn(BaseModel):
    old_password: str
    new_password: str
    confirm_password: str


class ResetPwdIn(BaseModel):
    """找回密码。

    三种验证方式由后台开关决定（见 settings.reset_password）：
      * sms / email —— 用已绑定的手机 / 邮箱验证码重置
      * captcha     —— 图形验证码 + 身份证号：核验通过即重置，
                       核验不过（或未登记身份证号）自动转为待管理员审核的申请
    """
    target_type: str = ""            # sms / email / captcha
    target: str = ""                 # sms/email 时的手机号或邮箱
    code: str = ""                   # sms/email 时的验证码
    username: str = ""               # captcha 方式：账号
    id_number: str = ""              # captcha 方式：身份证号
    real_name: str = ""              # captcha 方式（申请用，可留空）
    contact: str = ""                # captcha 方式（申请用，可留空）
    captcha_id: str = ""
    captcha_code: str = ""
    new_password: str = ""
    confirm_password: str = ""


class ProfileIn(BaseModel):
    real_name: str = ""
    phone: str = ""
    email: str = ""


class RefreshIn(BaseModel):
    refresh_token: str


def _public_user(u: dict) -> dict:
    item = {k: u.get(k) for k in ("id", "username", "real_name", "phone", "email", "role",
                                  "status", "grade", "college", "department", "class_name",
                                  "classes", "photo", "scope")}
    # 权限描述随登录/查询接口一并下发，前端据此渲染菜单与按钮（后端仍会二次核验）
    item.update(describe(u))
    return item


@router.get("/captcha")
def captcha():
    return ok(gen_captcha())


@router.get("/verify-options")
def verify_options(purpose: str = "register"):
    """可用验证方式及通道配置状态（前端据此渲染切换项与提示）。

    purpose=reset 时按后台「找回密码」开关返回；关闭时 enabled=False，
    前端应隐藏入口而不是让用户填完才发现不能用。
    """
    if purpose == "reset":
        rp = _reset_settings()
        return ok({
            "enabled": bool(rp["enable"]),
            "message": "" if rp["enable"] else "管理员已关闭找回密码功能，请联系管理员重置",
            "captcha": {"enabled": bool(rp["captcha"]), "label": "图形验证码 + 身份证号",
                        "gateway_ready": True},
            "email": {"enabled": bool(rp["email"]), "label": "邮箱验证码",
                      "gateway_ready": mail_configured()},
            "sms": {"enabled": bool(rp["sms"]), "label": "手机验证码",
                    "gateway_ready": sms_configured()},
        })
    # purpose=register：严格按后台「注册」开关返回，未开启的方式不会出现在前端
    rg = _register_settings()
    return ok({
        "captcha": {"enabled": bool(rg["captcha"]), "label": "图形验证码",
                    "gateway_ready": True},
        "email": {"enabled": bool(rg["email"]), "label": "邮箱验证码",
                  "gateway_ready": mail_configured()},
        "sms": {"enabled": bool(rg["sms"]), "label": "手机验证码",
                "gateway_ready": sms_configured()},
    })


@router.post("/send-code")
def send_code(body: SendCodeIn):
    """发送邮箱 / 手机验证码。未配置网关时走演示模式（服务端控制台输出）。"""
    ttype = (body.target_type or "").strip().lower()
    target = (body.target or "").strip()
    purpose = (body.purpose or "register").strip()
    if ttype not in ("email", "sms"):
        raise ApiError("验证码类型不合法（email / sms）")
    col = "email" if ttype == "email" else "phone"
    if ttype == "email":
        if not EMAIL_RE.match(target):
            raise ApiError("邮箱格式不正确")
    else:
        if not PHONE_RE.match(target):
            raise ApiError("手机号格式不正确")
    # 注册走哪种验证方式由后台开关决定：没开的方式一律不发码，
    # 否则「前端没这个选项、直接调接口却能发」就成了绕过开关的后门
    if purpose != "reset":
        rg = _register_settings()
        if not rg.get(ttype):
            raise ApiError("管理员未开启该验证方式，无法发送验证码", code=403, status=403)

    exists = bool(db.query_one(f"SELECT id FROM users WHERE {col}=?", (target,)))
    # 注册要求「未注册」，找回密码要求「已注册」——同一接口按用途反向校验
    if purpose == "reset":
        if not exists:
            raise ApiError(f"该{'邮箱' if ttype == 'email' else '手机号'}未绑定任何账号")
    elif exists:
        raise ApiError(f"该{'邮箱' if ttype == 'email' else '手机号'}已被注册")

    blocked = code_send_blocked(ttype, target, purpose)
    if blocked:
        raise ApiError(blocked, code=429, status=429)

    result = issue_code(ttype, target, purpose)
    if not DEV_MODE:
        result.pop("code", None)   # 非自测模式不向前端回显验证码
    hint = ("验证码已发送至 " + result["target_masked"] if result["delivery"] != "console"
            else "当前未配置" + ("邮件" if ttype == "email" else "短信") +
                 "网关，验证码已在服务端控制台输出（演示模式）")
    return ok(result, hint)


@router.post("/register")
def register(body: RegisterIn):
    username = (body.username or "").strip()
    phone = (body.phone or "").strip()
    email = (body.email or "").strip()

    if not USERNAME_RE.match(username):
        raise ApiError("用户名需为 3-20 位字母、数字或下划线")
    if not PHONE_RE.match(phone):
        raise ApiError("手机号格式不正确（需 11 位数字且以 1 开头）")
    if email and not EMAIL_RE.match(email):
        raise ApiError("邮箱格式不正确")
    if body.password != body.confirm_password:
        raise ApiError("两次输入的密码不一致")
    err = password_strength_error(body.password)
    if err:
        raise ApiError(err)

    # 验证码：图形验证码 / 邮箱验证码 / 手机验证码 三选一（方式须是后台已开启的）
    verify_type = (body.verify_type or "captcha").strip()
    rg = _register_settings()
    if verify_type == "captcha":
        if not rg.get("captcha"):
            raise ApiError("管理员未开启图形验证码注册", code=403, status=403)
        if not verify_captcha(body.captcha_id, body.captcha_code):
            raise ApiError("图形验证码错误或已过期")
    elif verify_type in ("email", "sms"):
        if not rg.get(verify_type):
            raise ApiError("管理员未开启该验证方式", code=403, status=403)
        target = (body.code_target or "").strip()
        if not target:
            raise ApiError("请先获取验证码")
        if verify_type == "email" and target.lower() != email.lower():
            raise ApiError("验证码邮箱与注册邮箱不一致")
        if verify_type == "sms" and target != phone:
            raise ApiError("验证码手机号与注册手机号不一致")
        if not verify_code(verify_type, target, "register", body.code):
            raise ApiError("验证码错误或已过期")
    else:
        raise ApiError("验证方式不合法（captcha / email / sms）")

    if db.query_one("SELECT id FROM users WHERE username=?", (username,)):
        raise ApiError("用户名已被占用")
    if db.query_one("SELECT id FROM users WHERE phone=?", (phone,)):
        raise ApiError("手机号已被注册")

    ts = db.now_str()
    uid = db.execute(
        "INSERT INTO users(username,password_hash,real_name,phone,email,role,status,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,1,?,?)",
        (username, hash_password(body.password), body.real_name.strip() or username,
         phone, email, "candidate", ts, ts))
    user = db.query_one("SELECT * FROM users WHERE id=?", (uid,))
    return ok(_public_user(user), "注册成功")


@router.post("/login")
def login(body: LoginIn):
    account = (body.account or "").strip()
    blocked = login_blocked(account)
    if blocked:
        raise ApiError(blocked, code=429, status=429)

    user = db.query_one("SELECT * FROM users WHERE username=? OR phone=?", (account, account))
    if not user or not verify_password(body.password or "", user["password_hash"]):
        record_login_fail(account)
        raise ApiError("账号或密码错误", code=401, status=401)
    if user["status"] != 1:
        raise ApiError("账号已被禁用，请联系管理员", code=403, status=403)

    clear_login_fail(account)
    # 记住我：Access 12h + Refresh 7d（前端存 localStorage）
    # 不记住：Access 2h、不发放 Refresh（前端存 sessionStorage，关闭浏览器即失效）
    remember = bool(body.remember)
    access_minutes = ACCESS_TOKEN_EXPIRE_MINUTES if remember else SESSION_TOKEN_EXPIRE_MINUTES
    data = {
        "access_token": create_token(user["id"], user["role"], "access", access_minutes),
        "token_type": "Bearer",
        "remember": remember,
        "expires_in": access_minutes * 60,
        "user": _public_user(user),
    }
    if remember:
        data["refresh_token"] = create_token(user["id"], user["role"], "refresh")
    return ok(data, "登录成功")


@router.post("/refresh")
def refresh(body: RefreshIn):
    try:
        payload = decode_token(body.refresh_token or "")
    except ValueError as e:
        raise ApiError(str(e), code=401, status=401)
    if payload.get("kind") != "refresh":
        raise ApiError("刷新令牌无效", code=401, status=401)
    user = db.query_one("SELECT * FROM users WHERE id=?", (int(payload["sub"]),))
    if not user or user["status"] != 1:
        raise ApiError("账号不可用", code=401, status=401)
    return ok({
        "access_token": create_token(user["id"], user["role"], "access"),
        "refresh_token": create_token(user["id"], user["role"], "refresh"),
        "user": _public_user(user),
    })


@router.get("/me")
def me(user=Depends(get_current_user)):
    return ok(_public_user(user))


@router.put("/profile")
def update_profile(body: ProfileIn, user=Depends(get_current_user)):
    real_name = (body.real_name or "").strip()
    if len(real_name) > 32:
        raise ApiError("姓名不能超过 32 个字符")
    phone = (body.phone or "").strip()
    email = (body.email or "").strip()
    if phone and not PHONE_RE.match(phone):
        raise ApiError("手机号格式不正确")
    if email and (len(email) > 120 or not EMAIL_RE.match(email)):
        raise ApiError("邮箱格式不正确")
    if phone:
        other = db.query_one("SELECT id FROM users WHERE phone=? AND id<>?", (phone, user["id"]))
        if other:
            raise ApiError("手机号已被其他账号使用")
    db.execute("UPDATE users SET real_name=?,phone=?,email=?,updated_at=? WHERE id=?",
               (real_name, phone or user["phone"], email, db.now_str(), user["id"]))
    return ok(_public_user(db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))), "资料已更新")


@router.post("/change-password")
def change_password(body: ChangePwdIn, user=Depends(get_current_user)):
    row = db.query_one("SELECT * FROM users WHERE id=?", (user["id"],))
    if not verify_password(body.old_password or "", row["password_hash"]):
        raise ApiError("原密码不正确")
    if body.new_password != body.confirm_password:
        raise ApiError("两次输入的新密码不一致")
    err = password_strength_error(body.new_password)
    if err:
        raise ApiError(err)
    db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
               (hash_password(body.new_password), db.now_str(), user["id"]))
    return ok(None, "密码修改成功，请重新登录")


@router.post("/reset-password")
def reset_password(body: ResetPwdIn):
    """找回密码（无需登录）。

    方式由后台开关决定：
      * sms / email：验证码校验通过即重置；
      * captcha：图形验证码 + 身份证号，核验通过即重置；
                 核验不过或未登记身份证号 → 转为待管理员审核的申请。
    找不到账号与验证失败返回同一句提示，避免被用来探测账号是否存在。
    """
    rp = _reset_settings()
    if not rp["enable"]:
        raise ApiError("管理员已关闭找回密码功能，请联系管理员重置")

    ttype = (body.target_type or "").strip().lower()
    if ttype not in ("email", "sms", "captcha"):
        raise ApiError("验证方式不合法")
    if not rp.get(ttype):
        raise ApiError("该找回密码方式未开启，请换一种方式或联系管理员")
    if body.new_password != body.confirm_password:
        raise ApiError("两次输入的新密码不一致")
    err = password_strength_error(body.new_password)
    if err:
        raise ApiError(err)

    # ---- 短信 / 邮箱验证码 ----
    if ttype in ("email", "sms"):
        target = (body.target or "").strip()
        if not target:
            raise ApiError(f"请填写{'邮箱' if ttype == 'email' else '手机号'}")
        if not verify_code(ttype, target, "reset", (body.code or "").strip()):
            raise ApiError("验证码不正确或已过期")
        col = "email" if ttype == "email" else "phone"
        row = db.query_one(f"SELECT * FROM users WHERE {col}=?", (target,))
        if not row:
            raise ApiError("验证码不正确或已过期")
        return _do_reset(row, body.new_password)

    # ---- 图形验证码 + 身份证号 ----
    if not verify_captcha(body.captcha_id, body.captcha_code):
        raise ApiError("图形验证码错误或已过期")
    username = (body.username or "").strip()
    idno = (body.id_number or "").strip().upper()
    if not username or not idno:
        raise ApiError("请填写账号与身份证号")
    row = db.query_one(
        "SELECT * FROM users WHERE username=? OR phone=?", (username, username))
    if not row:
        raise ApiError("账号或身份证号不正确")
    if row.get("status") == 0:
        raise ApiError("该账号已停用，请联系管理员")

    # 核验口径：已通过审核的实名信息 > 账号上登记的证件号
    # （实名模块是需求9 的统一来源，账号字段只是历史遗留的兜底）
    from .. import realname as RN
    prof = RN.profile_of(row["id"])
    id_ok = ((prof or {}).get("status") == "approved"
             and (prof or {}).get("id_number", "").strip().upper() == idno)
    if not id_ok and (row.get("id_number") or "").strip().upper() == idno:
        id_ok = True

    if id_ok:
        return _do_reset(row, body.new_password)

    # 核验不过（或未登记身份证号）→ 转人工审核
    db.execute(
        "INSERT INTO password_resets(user_id, username, real_name, id_number, contact,"
        " reason, status, created_at) VALUES(?,?,?,?,?,?, 'pending',?)",
        (row["id"], row.get("username") or username,
         (body.real_name or row.get("real_name") or "").strip(),
         idno, (body.contact or "").strip(),
         "图形验证码方式身份证核验未通过，转人工审核", db.now_str()))
    return ok({"pending": True},
              "身份信息未核验通过，已提交管理员审核，请耐心等待或联系管理员处理")


def _do_reset(row: dict, new_password: str):
    """真正落库改密码（内部复用；不校验验证码，调用方必须先验证过）。"""
    db.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
               (hash_password(new_password), db.now_str(), row["id"]))
    # 令牌是无状态 JWT，无法在服务端单点失效；访问令牌有效期较短，
    # 重置后旧令牌最多在剩余有效期内可用（改密码接口同理）。
    return ok(None, "密码已重置，请使用新密码登录")


@router.post("/logout")
def logout(user=Depends(get_current_user)):
    return ok(None, "已退出登录")
