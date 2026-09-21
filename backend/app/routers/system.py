# -*- coding: utf-8 -*-
"""模块9：系统维护——运行信息、数据统计、测试数据清理（正式交付前使用）。"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import db
from .. import exam_types as ET
from .. import idphoto as ID
from ..config import (APP_NAME, APP_VERSION, DB_PATH, DEV_MODE, EXPORT_DIR,
                      SEED_DEMO, ensure_dirs, load_settings, mail_configured,
                      save_settings, sms_configured)
from ..deps import ApiError, ok
from ..netutil import access_urls
from ..permissions import require_admin, require_perms

router = APIRouter(prefix="/api/system", tags=["system"])

# 可清理的数据域
RESET_SCOPES = {
    "applications": "清空所有报名数据、审核记录与导入批次（保留账号与考试批次）",
    "users":        "清空所有账号，仅保留当前管理员",
    "exams":        "清空所有考试批次（连带报名数据）",
    "exports":      "清空已生成的导出文件",
    "factory":      "恢复出厂：清空账号、考试批次、报名数据与导出文件",
}


class ResetIn(BaseModel):
    scopes: list
    confirm: str = ""


class SettingsIn(BaseModel):
    """系统设置入参：全字段可选，只传要改的段。

    ⚠ `register` 不能直接做字段名：pydantic 会警告
    「Field name "register" shadows an attribute in parent BaseModel」，
    这条 UserWarning 会原样打在 exe 的控制台窗口上，用户看得见，像是报错。
    改用 alias 保留对外契约（请求体里仍是 `register`），字段名本身改名。
    """
    model_config = ConfigDict(populate_by_name=True)

    smtp: dict = None
    sms: dict = None
    code_ttl_seconds: int = None
    code_resend_seconds: int = None
    code_daily_limit: int = None
    network: dict = None
    apply: dict = None
    idphoto: dict = None
    register_verify: dict = Field(None, alias="register")
    reset_password: dict = None


# 密钥字段：GET 一律回掩码，PUT 遇掩码保持不变、遇 CLEAR 表示删除
SECRET_FIELDS = {"smtp": ("password",), "sms": ("api_key",)}
SECRET_MASK = "******"
SECRET_CLEAR = "__clear__"


def _public_settings() -> dict:
    s = load_settings()
    out = {}
    for section, keys in SECRET_FIELDS.items():
        seg = dict(s.get(section) or {})
        for k in keys:
            seg[k + "_set"] = bool(seg.get(k))
            seg[k] = SECRET_MASK if seg.get(k) else ""
        out[section] = seg
    for k in ("code_ttl_seconds", "code_resend_seconds", "code_daily_limit"):
        out[k] = s.get(k)
    out["network"] = {
        "lan_access": 1 if (s.get("network") or {}).get("lan_access") else 0,
    }
    out["apply"] = {
        "employer_default": (s.get("apply") or {}).get("employer_default") or "",
    }
    ip = s.get("idphoto") or {}
    out["idphoto"] = {
        "remote_url": ip.get("remote_url") or "",
        "prefer": ip.get("prefer") or "local",
        # 初始化参数：管理员在这里定默认，用户在制作页可临时覆盖
        "params": ID.default_params(),
    }
    mk = s.get("mock") or {}
    out["mock"] = {
        "enable": 1 if mk.get("enable") else 0,
        "count": int(mk.get("count") or 5000),
        "current": db.mock_count(),
    }
    rg = s.get("register") or {}
    out["register"] = {
        "captcha": 1 if rg.get("captcha", 1) else 0,
        "sms": 1 if rg.get("sms") else 0,
        "email": 1 if rg.get("email") else 0,
        "sms_ready": sms_configured(),
        "email_ready": mail_configured(),
    }
    rp = s.get("reset_password") or {}
    out["reset_password"] = {
        "enable": 1 if rp.get("enable", 1) else 0,
        "captcha": 1 if rp.get("captcha", 1) else 0,
        "sms": 1 if rp.get("sms") else 0,
        "email": 1 if rp.get("email") else 0,
        # 提醒前端：开关开了但网关没配，照样不可用（防「开了却发不出去」的假象）
        "sms_ready": sms_configured(),
        "email_ready": mail_configured(),
    }
    out["mail_configured"] = mail_configured()
    out["sms_configured"] = sms_configured()
    # 当前实际监听地址与可访问网址（供系统维护页显示）
    acc = access_urls()
    if not acc["host"]:
        # 开发模式（直接 uvicorn 启动，未经 launcher）：回显不出真实监听地址
        acc["host"] = "127.0.0.1"
        acc["wildcard"] = False
    acc["lan_access"] = out["network"]["lan_access"]
    out["listen"] = acc
    return out


@router.get("/settings")
def get_settings(user=Depends(require_perms("system_manage"))):
    """邮件 / 短信网关配置（密钥只回掩码，不回明文）。"""
    return ok(_public_settings())


@router.put("/settings")
def put_settings(body: SettingsIn, user=Depends(require_perms("system_manage"))):
    """局部更新网关配置；密钥三态：掩码=不改、__clear__=删除、其余=写入。"""
    cur = load_settings()
    # by_alias=True：patch 的键要跟请求体一致（register 而不是 register_verify），
    # 否则下面 `if "register" in patch` 永远不成立，注册开关改不动
    patch = body.model_dump(exclude_none=True, by_alias=True)
    for section, keys in SECRET_FIELDS.items():
        if section not in patch:
            continue
        seg = dict(cur.get(section) or {})
        for k, v in (patch[section] or {}).items():
            if k in keys:
                if v == SECRET_CLEAR:
                    seg[k] = ""
                    continue
                if v in ("", SECRET_MASK, None):
                    continue        # 保留已保存的密钥
            seg[k] = v
        cur[section] = seg
    for k in ("code_ttl_seconds", "code_resend_seconds", "code_daily_limit"):
        if k in patch:
            v = int(patch[k] or 0)
            if v <= 0:
                raise ApiError(f"{k} 必须为正整数")
            cur[k] = v
    if "network" in patch:
        seg = dict(cur.get("network") or {})
        for k in ("lan_access",):
            if k in (patch["network"] or {}):
                seg[k] = 1 if patch["network"][k] else 0
        cur["network"] = seg
    if "apply" in patch:
        seg = dict(cur.get("apply") or {})
        # 默认值只做去空白与长度限制：留空表示不预填
        v = ((patch["apply"] or {}).get("employer_default") or "").strip()
        if len(v) > 100:
            raise ApiError("所在单位默认值不能超过 100 个字符")
        seg["employer_default"] = v
        cur["apply"] = seg
    if "idphoto" in patch:
        seg = dict(cur.get("idphoto") or {})
        url = ((patch["idphoto"] or {}).get("remote_url") or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            raise ApiError("证件照远程接口地址必须以 http:// 或 https:// 开头")
        seg["remote_url"] = url
        prefer = ((patch["idphoto"] or {}).get("prefer") or "local").strip()
        seg["prefer"] = "remote" if prefer == "remote" else "local"
        pp = (patch["idphoto"] or {}).get("params") or {}
        if pp:
            ps = dict(seg.get("params") or {})
            for k in ID.PARAM_RANGES:
                if k in pp:
                    ps[k] = ID.clamp_param(k, pp.get(k))
            if pp.get("blank_fill") in ID.BLANK_FILLS:
                ps["blank_fill"] = pp["blank_fill"]
            seg["params"] = ps
        cur["idphoto"] = seg
    if "register" in patch:
        seg = dict(cur.get("register") or {})
        p = patch["register"] or {}
        for k in ("captcha", "sms", "email"):
            if k in p:
                seg[k] = 1 if p[k] else 0
        # 一种都不留 = 考生注册不了，直接拒绝保存而不是留个死局
        if not (seg.get("captcha") or seg.get("sms") or seg.get("email")):
            raise ApiError("注册验证方式至少要保留一种")
        cur["register"] = seg
    if "reset_password" in patch:
        seg = dict(cur.get("reset_password") or {})
        p = patch["reset_password"] or {}
        for k in ("enable", "captcha", "sms", "email"):
            if k in p:
                seg[k] = 1 if p[k] else 0
        # 至少要留一种验证方式，否则开了找回密码却没法验证 = 死路
        if seg.get("enable") and not (seg.get("captcha") or seg.get("sms") or seg.get("email")):
            raise ApiError("开启找回密码时，至少要保留一种验证方式")
        cur["reset_password"] = seg
    if (cur.get("sms") or {}).get("endpoint"):
        ep = (cur["sms"]["endpoint"] or "").strip()
        if ep and not ep.lower().startswith(("http://", "https://")):
            raise ApiError("短信网关地址必须以 http:// 或 https:// 开头")
    save_settings(cur)
    return ok(_public_settings(), "配置已保存")


def _counts() -> dict:
    def c(sql, params=()):
        r = db.query_one(sql, params)
        return r["c"] if r else 0
    return {
        "users": c("SELECT COUNT(*) c FROM users"),
        "admins": c("SELECT COUNT(*) c FROM users WHERE role='admin'"),
        "candidates": c("SELECT COUNT(*) c FROM users WHERE role='candidate'"),
        "exams": c("SELECT COUNT(*) c FROM exams"),
        # 遍历全部报名表：写死两张表会让通用模板的数据统计不到
        "applications": sum(c(f"SELECT COUNT(*) c FROM {t}") for t in ET.ALL_APP_TABLES),
        "audits": c("SELECT COUNT(*) c FROM audits"),
        "import_batches": c("SELECT COUNT(*) c FROM import_batches"),
    }


@router.get("/info")
def info(user=Depends(require_perms("system_manage"))):
    """运行信息与数据统计（管理员可见）。"""
    sizes = {}
    if DB_PATH.exists():
        sizes["db_bytes"] = DB_PATH.stat().st_size
    files = list(EXPORT_DIR.glob("*.xlsx")) if EXPORT_DIR.exists() else []
    sizes["export_files"] = len(files)
    sizes["export_bytes"] = sum(p.stat().st_size for p in files)
    return ok({
        "app_name": APP_NAME, "version": APP_VERSION,
        "dev_mode": DEV_MODE, "seed_demo": SEED_DEMO,
        "db_path": str(DB_PATH), "export_dir": str(EXPORT_DIR),
        "counts": _counts(), "storage": sizes,
        "reset_scopes": [{"key": k, "label": v} for k, v in RESET_SCOPES.items()],
    })


@router.post("/reset")
def reset(body: ResetIn, user=Depends(require_perms("system_manage"))):
    """清理测试数据。需要输入确认短语，避免误操作。"""
    scopes = [s for s in (body.scopes or []) if s in RESET_SCOPES]
    if not scopes:
        raise ApiError("请选择要清理的数据范围")
    if (body.confirm or "").strip() != "确认清空":
        raise ApiError("请在确认框中输入「确认清空」后再执行")

    if "factory" in scopes:
        scopes = list(RESET_SCOPES.keys())

    done, before = [], _counts()
    if "applications" in scopes or "exams" in scopes:
        for tbl in ET.ALL_APP_TABLES:
            db.execute(f"DELETE FROM {tbl}")
        db.execute("DELETE FROM audits")
        db.execute("DELETE FROM import_errors")
        db.execute("DELETE FROM import_batches")
        done.append("已清空报名数据、审核记录与导入批次")
    if "exams" in scopes or "factory" in scopes:
        db.execute("DELETE FROM exams")
        done.append("已清空考试批次")
    if "users" in scopes or "factory" in scopes:
        removed = db.query_one("SELECT COUNT(*) c FROM users WHERE id<>?", (user["id"],))["c"]
        db.execute("DELETE FROM users WHERE id<>?", (user["id"],))
        done.append(f"已清理 {removed} 个账号，仅保留当前管理员 {user['username']}")
    if "exports" in scopes or "factory" in scopes:
        ensure_dirs()
        n = 0
        for p in EXPORT_DIR.glob("*.xlsx"):
            try:
                p.unlink()
                n += 1
            except OSError:
                pass
        done.append(f"已清理 {n} 个导出文件")

    return ok({"done": done, "before": before, "after": _counts()},
              "清理完成：" + "；".join(done))


class MockIn(BaseModel):
    enable: bool = True
    count: int = 5000


def _mock_settings() -> dict:
    m = (load_settings().get("mock") or {})
    return {"enable": 1 if m.get("enable") else 0,
            "count": int(m.get("count") or 5000)}


@router.get("/mock")
def mock_status(user=Depends(require_admin())):
    """模拟模式状态（仅管理员）：开关、目标条数、当前已有模拟考生数。"""
    m = _mock_settings()
    return ok({"enable": m["enable"], "count": m["count"], "existing": db.mock_count()})


@router.post("/mock")
def mock_toggle(body: MockIn, user=Depends(require_admin())):
    """开启 / 关闭模拟模式（仅管理员，用于性能压测）。

    开启：按 count 批量灌入模拟报名数据（只填必要字段，不追求完整）。
    关闭：清除全部模拟数据（按用户名前缀 mock_ 识别），保证正式环境不留测试数据。
    """
    settings = load_settings()
    count = max(1, min(int(body.count or 5000), 50000))
    if body.enable:
        try:
            r = db.seed_mock_data(count)
        except RuntimeError as e:
            raise ApiError(str(e))
        settings["mock"] = {"enable": 1, "count": count}
        save_settings(settings)
        return ok({"enable": 1, "count": db.mock_count(), "generated": r},
                  f"已生成 {r['apps']} 条模拟报名数据（{r['users']} 个模拟考生）")
    removed = db.clear_mock_data()
    settings["mock"] = {"enable": 0, "count": count}
    save_settings(settings)
    return ok({"enable": 0, "count": 0, "removed": removed},
              f"已清除 {removed} 条模拟数据" if removed else "当前没有模拟数据")
