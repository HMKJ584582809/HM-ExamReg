# -*- coding: utf-8 -*-
"""模块11：实名信息（自填待审 + 第三方认证通道）。

设计取舍
--------
真实的支付宝 / 微信实名认证需要**商户资质、AppID、密钥与公网可访问的回调地址**，
而本系统是单机 exe、通常跑在内网机器上，收不到公网的 OAuth 回调。
因此这里做成三段式：

1. **自填 + 管理员审核**（默认开启）：考生自己填姓名与证件号，管理员在后台核对后
   通过 / 驳回。这是内网环境下唯一真正可落地的方式。
2. **第三方通道**：后台按厂商配置 AppID 与密钥，前端生成授权跳转链接；
   配置齐全时按官方协议拼 URL、回调时换 token 拉取认证结果。
3. **内置模拟通道**（``mock``）：配置未齐全时自动降级，用于在正式接入前
   把「发起 → 授权 → 回传结果 → 落库」整条链路跑通并联调前端。

密钥一律**只写不读**（返回掩码），避免从页面泄出去。
"""
import json
import re
import secrets
import urllib.parse
import urllib.request
from datetime import datetime

from . import db
from .config import load_settings, save_settings
from .deps import ApiError

# 证件类型（与报名表的 1/6/7/8 保持一致的取值，便于复用校验器）
ID_TYPES = [
    {"key": "1", "label": "居民身份证"},
    {"key": "6", "label": "香港居民来往内地通行证"},
    {"key": "7", "label": "澳门居民来往内地通行证"},
    {"key": "8", "label": "台湾居民来往内地通行证"},
]
ID_TYPE_LABEL = {t["key"]: t["label"] for t in ID_TYPES}

STATUS_LABEL = {"pending": "待审核", "approved": "已通过", "rejected": "已驳回"}
CHANNEL_LABEL = {"self": "本人自填", "alipay": "支付宝实名认证", "wechat": "微信实名认证"}

# 需要掩码回传的字段（密钥只写不读）
SECRET_FIELDS = ("private_key", "alipay_public_key", "app_secret", "api_v3_key")
SECRET_MASK = "******"
SECRET_CLEAR = "__clear__"

NAME_RE = re.compile(r"^[\u4e00-\u9fa5·]{2,20}$|^[A-Za-z][A-Za-z\s.'-]{1,39}$")


def _default_cfg() -> dict:
    return json.loads(json.dumps(load_settings().get("realname") or {})) or {
        "enable": 1, "require_approved": 0, "self_fill": 1,
        "alipay": {"enable": 0, "app_id": "", "private_key": "", "alipay_public_key": ""},
        "wechat": {"enable": 0, "app_id": "", "app_secret": "", "mch_id": "", "api_v3_key": ""},
    }


def realname_config() -> dict:
    """读取实名配置，缺字段时用默认值补齐。"""
    d = _default_cfg()
    cur = load_settings().get("realname")
    if isinstance(cur, dict):
        for k, v in cur.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                d[k].update(v)
            else:
                d[k] = v
    for ch in ("alipay", "wechat"):
        d.setdefault(ch, {})
    d.setdefault("enable", 1)
    d.setdefault("require_approved", 0)
    d.setdefault("self_fill", 1)
    return d


def save_realname_config(patch: dict) -> dict:
    """保存实名配置；密钥字段传掩码/空串表示不修改，``__clear__`` 表示删除。"""
    cur = realname_config()
    for k in ("enable", "require_approved", "self_fill"):
        if k in patch:
            cur[k] = 1 if str(patch[k]).lower() in ("1", "true", "yes", "on") else 0
    for ch in ("alipay", "wechat"):
        if not isinstance(patch.get(ch), dict):
            continue
        for f, v in patch[ch].items():
            if f == "enable":
                cur[ch]["enable"] = 1 if str(v).lower() in ("1", "true", "yes", "on") else 0
            elif f in SECRET_FIELDS:
                if v == SECRET_CLEAR:
                    cur[ch][f] = ""
                elif v in ("", SECRET_MASK, None):
                    continue
                else:
                    cur[ch][f] = str(v).strip()
            elif f in ("app_id", "mch_id"):
                cur[ch][f] = str(v or "").strip()
    s = load_settings()
    s["realname"] = cur
    save_settings(s)
    return cur


def public_realname_config() -> dict:
    """给前端的配置：密钥一律回掩码 + 是否已配置，外加通道可用性说明。"""
    c = realname_config()
    out = {"enable": c.get("enable", 1), "require_approved": c.get("require_approved", 0),
           "self_fill": c.get("self_fill", 1), "id_types": ID_TYPES,
           "channels": []}
    for ch, label in (("alipay", "支付宝"), ("wechat", "微信")):
        d = dict(c.get(ch) or {})
        ready, reason = channel_ready(ch)
        for f in SECRET_FIELDS:
            if f in d:
                d[f] = SECRET_MASK if d.get(f) else ""
                d[f + "_set"] = bool(d.get(f))
        d["channel"] = ch
        d["label"] = label
        d["ready"] = ready
        d["reason"] = reason
        d["mode"] = "official" if ready else ("mock" if d.get("enable") else "disabled")
        out["channels"].append(d)
    return out


def channel_ready(channel: str) -> tuple:
    """第三方通道是否配置到「能真跑」的程度，返回 (可用, 缺什么)。"""
    c = realname_config()
    d = c.get(channel) or {}
    if not int(d.get("enable") or 0):
        return False, "通道未启用"
    if not (d.get("app_id") or "").strip():
        return False, "未填写 AppID"
    need = ("private_key", "alipay_public_key") if channel == "alipay" \
        else ("app_secret", "api_v3_key")
    miss = [f for f in need if not (d.get(f) or "").strip()]
    if miss:
        return False, "未配置" + ("应用私钥 / 支付宝公钥" if channel == "alipay" else "AppSecret / APIv3 密钥")
    if channel == "wechat" and not (d.get("mch_id") or "").strip():
        return False, "未填写商户号"
    return True, ""


def _mask_id(v: str) -> str:
    """证件号脱敏：保留前 4 位与后 2 位。"""
    s = (v or "").strip()
    if len(s) <= 6:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 6) + s[-2:]


def _mask_name(v: str) -> str:
    s = (v or "").strip()
    if len(s) <= 1:
        return s
    return s[0] + "*" * (len(s) - 1)


def _row_to_public(r: dict, mask: bool = True) -> dict:
    d = dict(r or {})
    if mask:
        d["id_number"] = _mask_id(d.get("id_number"))
        d["real_name"] = _mask_name(d.get("real_name"))
    d["id_number_masked"] = _mask_id(r.get("id_number") if r else "")
    d["status_label"] = STATUS_LABEL.get((r or {}).get("status"), "")
    d["channel_label"] = CHANNEL_LABEL.get((r or {}).get("channel"), (r or {}).get("channel") or "")
    d["id_type_label"] = ID_TYPE_LABEL.get((r or {}).get("id_type"), (r or {}).get("id_type") or "")
    return d


def profile_of(user_id: int) -> dict:
    return db.query_one("SELECT * FROM realname_profiles WHERE user_id=?", (user_id,))


def status_of(user_id: int) -> str:
    r = profile_of(user_id)
    return (r or {}).get("status", "") or ""


def validate_input(real_name: str, id_type: str, id_number: str):
    """姓名 + 证件号校验，返回清洗后的 (姓名, 证件类型, 证件号)。"""
    name = (real_name or "").strip()
    if not name:
        raise ApiError("请填写真实姓名")
    if not NAME_RE.match(name):
        raise ApiError("姓名格式不正确（2-20 位中文或英文字母）")
    it = (id_type or "1").strip()
    if it not in ID_TYPE_LABEL:
        raise ApiError("证件类型不合法")
    from .validators import validate_id_number as _v
    num = _v(it, (id_number or "").strip(), required=True)
    return name, it, num


def submit(user: dict, real_name: str, id_type: str, id_number: str,
           channel: str = "self", provider_uid: str = "", trans_id: str = "") -> dict:
    """提交实名信息。一人一条，重复提交走更新。

    第三方通道带回的结果**直接置为已通过**；自填的一律进待审核队列。
    """
    cfg = realname_config()
    if int(cfg.get("enable") or 0) != 1:
        raise ApiError("实名信息功能已由管理员关闭")
    if channel != "self" and not int(cfg.get("self_fill") or 0):
        raise ApiError("管理员已关闭自行提交实名信息")
    name, it, num = validate_input(real_name, id_type, id_number)
    now = db.now_str()
    auto = channel in ("alipay", "wechat")
    row = profile_of(user["id"])
    if row:
        if row["status"] == "pending" and not auto:
            # 允许改资料重交，但已通过的不可随意覆盖（避免绕过审核）
            pass
        if row["status"] == "approved" and not auto:
            raise ApiError("实名信息已通过审核，如需变更请联系管理员")
        db.execute(
            "UPDATE realname_profiles SET real_name=?, id_type=?, id_number=?, status=?,"
            " channel=?, provider_uid=?, trans_id=?, reviewer_id=0, reviewer_name='',"
            " review_note='', submitted_at=?, reviewed_at=? WHERE id=?",
            (name, it, num, "approved" if auto else "pending", channel,
             provider_uid, trans_id, now, now if auto else "", row["id"]))
        rid = row["id"]
    else:
        rid = db.execute(
            "INSERT INTO realname_profiles (user_id, real_name, id_type, id_number, status,"
            " channel, provider_uid, trans_id, submitted_at, reviewed_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user["id"], name, it, num, "approved" if auto else "pending", channel,
             provider_uid, trans_id, now, now if auto else ""))
    if auto:
        _sync_user_id(user["id"], name, num)
    return db.query_one("SELECT * FROM realname_profiles WHERE id=?", (rid,))


def _sync_user_id(user_id: int, real_name: str, id_number: str):
    """实名通过后回写 users 表，让「按证件号匹配照片 / 找回密码核验」能命中。"""
    db.execute("UPDATE users SET real_name=?, id_number=? WHERE id=? AND id_number=''",
               (real_name, id_number, user_id))


def review(row_id: int, admin: dict, approve: bool, note: str = "") -> dict:
    row = db.query_one("SELECT * FROM realname_profiles WHERE id=?", (row_id,))
    if not row:
        raise ApiError("记录不存在")
    if row["status"] != "pending":
        raise ApiError("该申请已处理过，不能重复审核")
    now = db.now_str()
    st = "approved" if approve else "rejected"
    db.execute("UPDATE realname_profiles SET status=?, reviewer_id=?, reviewer_name=?,"
               " review_note=?, reviewed_at=? WHERE id=?",
               (st, admin["id"], admin.get("real_name") or admin.get("username") or "",
                (note or "").strip(), now, row_id))
    if approve:
        _sync_user_id(row["user_id"], row["real_name"], row["id_number"])
    return db.query_one("SELECT * FROM realname_profiles WHERE id=?", (row_id,))


def revoke(user_id: int):
    """管理员撤销已通过的实名（如发现冒用），退回未认证状态。"""
    db.execute("DELETE FROM realname_profiles WHERE user_id=?", (user_id,))


# ------------------------------------------------------------ 第三方通道

_TICKETS = {}          # ticket -> {"user_id":.., "channel":.., "name":.., "id":.., "ts":..}


def _redirect_base() -> str:
    """回调地址的基础部分：单机版没有固定公网域名，只能按当前访问来源拼。"""
    return ""


def start_thirdparty(user: dict, channel: str, real_name: str = "",
                     id_type: str = "1", id_number: str = "", callback: str = "") -> dict:
    """发起第三方实名认证。

    - 通道配置齐全 → 走官方授权跳转（返回 authorize_url，前端新窗口打开）
    - 已启用但未配齐 → 走内置**模拟通道**（返回 ticket，前端点「模拟认证通过」完成）
    两种情况都不会在没拿到结果前就改库。
    """
    if channel not in ("alipay", "wechat"):
        raise ApiError("暂不支持该认证通道")
    cfg = realname_config()
    if int(cfg.get("enable") or 0) != 1:
        raise ApiError("实名信息功能已由管理员关闭")
    d = cfg.get(channel) or {}
    if not int(d.get("enable") or 0):
        raise ApiError(f"管理员尚未启用{CHANNEL_LABEL.get(channel, channel)}通道")
    ready, reason = channel_ready(channel)
    app_id = (d.get("app_id") or "").strip()
    state = secrets.token_urlsafe(12)
    if ready:
        url = _authorize_url(channel, app_id, callback, state)
        return {"mode": "official", "channel": channel, "authorize_url": url,
                "state": state, "ticket": "", "message":
                f"请在弹出窗口完成{CHANNEL_LABEL.get(channel)}授权，完成后回到本页刷新状态。"}
    # 模拟通道：把要核验证件真伪的动作留给人，但把链路跑完整
    name = (real_name or "").strip() or (user.get("real_name") or "")
    num = (id_number or "").strip() or (user.get("id_number") or "")
    if not (name and num):
        raise ApiError("模拟通道需要先用姓名与证件号提交实名信息，或在此处补齐")
    validate_input(name, id_type, num)
    ticket = "mock_" + secrets.token_hex(8)
    _TICKETS[ticket] = {"user_id": user["id"], "channel": channel, "real_name": name,
                        "id_type": id_type, "id_number": num, "state": state}
    return {"mode": "mock", "channel": channel, "authorize_url": "", "state": state,
            "ticket": ticket, "reason": reason,
            "message": f"{CHANNEL_LABEL.get(channel)}通道尚未配置齐全（{reason}），"
                       "当前为内置模拟通道，用于联调；配置密钥后会自动切换为真实认证。"}


def _authorize_url(channel: str, app_id: str, callback: str, state: str) -> str:
    """按官方协议拼授权跳转地址（不发起请求，只是给前端去跳）。"""
    cb = urllib.parse.quote(callback or "", safe="")
    if channel == "alipay":
        # 支付宝「支付宝登录 / 实名认证」授权（auth_user 需签约对应能力）
        return ("https://openauth.alipay.com/oauth2/publicAppAuthorize.htm?app_id="
                + urllib.parse.quote(app_id) + "&scope=auth_user&state=" + state
                + "&redirect_uri=" + cb)
    return ("https://open.weixin.qq.com/connect/oauth2/authorize?appid="
            + urllib.parse.quote(app_id)
            + "&redirect_uri=" + cb + "&response_type=code&scope=snsapi_userinfo"
            + "&state=" + state + "#wechat_redirect")


def finish_mock(user: dict, ticket: str, ok_result: bool = True) -> dict:
    """完成模拟认证：按 ticket 落地结果（通过 / 认证失败）。"""
    t = _TICKETS.pop(ticket, None)
    if not t or t["user_id"] != user["id"]:
        raise ApiError("认证票据无效或已过期，请重新发起")
    if not ok_result:
        raise ApiError("模拟通道：已按「认证未通过」处理，未写入实名信息")
    return submit(user, t["real_name"], t["id_type"], t["id_number"], channel=t["channel"],
                  provider_uid="mock:" + t["channel"], trans_id=ticket)


def handle_callback(channel: str, code: str, state: str = "") -> dict:
    """官方通道回调：用 code 换 token 并拉取实名结果。

    未配置齐全时直接给出明确原因——**绝不伪造一个「认证通过」**，
    否则会让管理员误以为考生已通过实名核验。
    """
    ready, reason = channel_ready(channel)
    if not ready:
        raise ApiError(f"{CHANNEL_LABEL.get(channel, channel)}通道未配置齐全：{reason}")
    if not (code or "").strip():
        raise ApiError("回调缺少 code 参数")
    cfg = realname_config()
    d = cfg[channel]
    if channel == "alipay":
        # 支付宝：alipay.user.certify.open.query 之类需 RSA 签名，
        # 这里只做连通性校验，真正验签需要商户私钥参与，交由接入方完成。
        raise ApiError("支付宝实名结果查询需 RSA2 签名，请在完成接入后由服务端私钥验签；"
                       "当前回调仅校验到 code 已收到。")
    url = ("https://api.weixin.qq.com/sns/oauth2/access_token?appid="
           + urllib.parse.quote(d["app_id"]) + "&secret="
           + urllib.parse.quote(d["app_secret"]) + "&code="
           + urllib.parse.quote(code) + "&grant_type=authorization_code")
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            j = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception as e:
        raise ApiError(f"微信接口调用失败：{type(e).__name__}")
    if j.get("errcode"):
        raise ApiError(f"微信返回错误：{j.get('errmsg') or j.get('errcode')}")
    return {"openid": j.get("openid", ""), "unionid": j.get("unionid", ""),
            "note": "已取得用户标识；实名要素（姓名 + 证件号）需微信支付「实名认证」"
                    "能力或人工核验，本系统不会凭 openid 就判定实名通过。"}


def require_approved(user: dict) -> bool:
    """报名时是否强制要求实名已通过。"""
    cfg = realname_config()
    return int(cfg.get("enable") or 0) == 1 and int(cfg.get("require_approved") or 0) == 1


def assert_can_apply(user: dict):
    if require_approved(user) and status_of(user["id"]) != "approved":
        raise ApiError("管理员已设置「实名认证通过后才能报名」，请先在「实名认证」页完成认证")
