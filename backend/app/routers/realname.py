# -*- coding: utf-8 -*-
"""模块11：实名信息接口。

权限
----
- ``/me``、``/submit``、``/thirdparty/*``：任意已登录账号（自己的实名）
- ``/list``、``/review``、``/revoke``：用户与权限管理（管理员）
- ``/config`` 读写：用户与权限管理（管理员）

安全约定
--------
- 第三方密钥只写不读，GET 一律回掩码 + ``*_set`` 标记。
- 通道未配置齐全时**只能走模拟通道**，且绝不伪造「认证通过」的结果。
"""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .. import realname as RN
from ..deps import ApiError, get_current_user, ok, page_params
from ..permissions import require_perms, user_scope_filter

router = APIRouter(prefix="/api/realname", tags=["realname"])


class SubmitIn(BaseModel):
    real_name: str = ""
    id_type: str = "1"
    id_number: str = ""


class ThirdIn(BaseModel):
    channel: str = ""
    real_name: str = ""
    id_type: str = "1"
    id_number: str = ""
    callback: str = ""


class MockIn(BaseModel):
    ticket: str = ""
    result: int = 1          # 1 通过 / 0 未通过


class ReviewIn(BaseModel):
    approve: int = 1
    note: str = ""


class ConfigIn(BaseModel):
    enable: int | None = None
    require_approved: int | None = None
    self_fill: int | None = None
    alipay: dict | None = None
    wechat: dict | None = None


@router.get("/config")
def get_config(user=Depends(require_perms("user_manage"))):
    """实名功能开关 + 第三方通道配置（密钥只回掩码）。"""
    return ok(RN.public_realname_config())


@router.put("/config")
def put_config(body: ConfigIn, user=Depends(require_perms("user_manage"))):
    patch = body.model_dump(exclude_unset=True)
    cfg = RN.save_realname_config(patch)
    if int(cfg.get("enable") or 0) == 1 and not int(cfg.get("self_fill") or 0):
        for ch in ("alipay", "wechat"):
            if int((cfg.get(ch) or {}).get("enable") or 0):
                return ok(RN.public_realname_config(), "实名配置已保存")
        raise ApiError("已关闭自行提交，且未启用任何第三方认证通道，考生将无法完成实名认证")
    return ok(RN.public_realname_config(), "实名配置已保存")


@router.get("/me")
def me(user=Depends(require_perms("realname"))):
    """我的实名状态（证件号脱敏回传）。"""
    r = RN.profile_of(user["id"])
    cfg = RN.realname_config()
    return ok({
        "enabled": int(cfg.get("enable") or 0) == 1,
        "self_fill": int(cfg.get("self_fill") or 0) == 1,
        "require_approved": int(cfg.get("require_approved") or 0) == 1,
        "profile": RN._row_to_public(r) if r else None,
        "status": (r or {}).get("status", "") or "",
        "id_types": RN.ID_TYPES,
        "channels": [{"key": c["channel"], "label": c["label"], "enable": c["enable"],
                      "ready": c["ready"], "mode": c["mode"], "reason": c["reason"]}
                     for c in RN.public_realname_config()["channels"]],
    })


@router.post("/submit")
def submit(body: SubmitIn, user=Depends(require_perms("realname"))):
    """提交自填实名信息（进入待审核）。"""
    r = RN.submit(user, body.real_name, body.id_type, body.id_number, channel="self")
    return ok(RN._row_to_public(r), "已提交，等待管理员审核")


@router.post("/thirdparty/start")
def thirdparty_start(body: ThirdIn, user=Depends(require_perms("realname"))):
    """发起第三方实名认证（配置齐全走官方授权，否则走内置模拟通道）。"""
    ch = (body.channel or "").strip()
    r = RN.start_thirdparty(user, ch, body.real_name, body.id_type, body.id_number,
                            body.callback)
    return ok(r, r.get("message", ""))


@router.post("/thirdparty/finish")
def thirdparty_finish(body: MockIn, user=Depends(require_perms("realname"))):
    """完成模拟通道认证（真实通道由回调接口处理，不走这里）。"""
    r = RN.finish_mock(user, (body.ticket or "").strip(), ok_result=int(body.result or 0) == 1)
    return ok(RN._row_to_public(r), "实名认证已通过")


@router.get("/thirdparty/callback")
def thirdparty_callback(channel: str = "", code: str = "", state: str = ""):
    """第三方认证回调（真实通道）。

    注意：真实部署需要公网可访问的地址，单机内网版本收不到回调，
    接口保留用于接入后联调；未配置齐全时会明确报错而不是放行。
    """
    return ok(RN.handle_callback((channel or "").strip(), code, state))


@router.get("/list")
def list_rows(status: str = Query(""), keyword: str = Query(""),
              page: int = 1, page_size: int = 10,
              user=Depends(require_perms("realname_audit"))):
    """实名审核列表（按数据范围收敛）。"""
    page, page_size = page_params(page, page_size)     # 返回 (page, page_size) 元组
    page = {"page": page, "page_size": page_size}
    where, args = [], []
    if status:
        where.append("p.status=?")
        args.append(status)
    kw = (keyword or "").strip()
    if kw:
        where.append("(p.real_name LIKE ? OR p.id_number LIKE ? OR u.username LIKE ?"
                     " OR u.phone LIKE ?)")
        args += [f"%{kw}%"] * 4
    # 范围收敛：二级学院审核只能看到本院系的实名申请（原来看得到全校）
    frag, fargs = user_scope_filter(user, "u")
    if frag:
        where.append(frag.replace(" AND ", "", 1))
        args += fargs
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db_page(clause, args, page)
    counts = {r["status"]: r["c"] for r in RN.db.query(
        "SELECT status, COUNT(*) c FROM realname_profiles GROUP BY status")}
    return ok({"list": rows, "total": _total(clause, args),
               "counts": {"pending": counts.get("pending", 0),
                          "approved": counts.get("approved", 0),
                          "rejected": counts.get("rejected", 0)}})


def _total(clause: str, args: list) -> int:
    r = RN.db.query_one("SELECT COUNT(*) c FROM realname_profiles p"
                        " JOIN users u ON u.id=p.user_id" + clause, tuple(args))
    return (r or {}).get("c", 0)


def db_page(clause: str, args: list, page) -> list:
    sql = ("SELECT p.*, u.username, u.phone, u.role, u.class_name, u.department,"
           " u.college, u.grade FROM realname_profiles p JOIN users u ON u.id=p.user_id"
           + clause + " ORDER BY CASE p.status WHEN 'pending' THEN 0"
           " WHEN 'approved' THEN 1 ELSE 2 END, p.id DESC LIMIT ? OFFSET ?")
    return [RN._row_to_public(r, mask=False) for r in RN.db.query(
        sql, tuple(args) + (page["page_size"], (page["page"] - 1) * page["page_size"]))]


@router.post("/{row_id}/review")
def review(row_id: int, body: ReviewIn, user=Depends(require_perms("realname_audit"))):
    """管理员审核：通过 / 驳回。"""
    r = RN.review(row_id, user, int(body.approve or 0) == 1, body.note)
    return ok(RN._row_to_public(r, mask=False),
              "已通过，实名信息已生效" if int(body.approve or 0) == 1 else "已驳回")


@router.post("/{row_id}/revoke")
def revoke(row_id: int, user=Depends(require_perms("realname_audit"))):
    """撤销该账号的实名信息（发现冒用或信息变更时使用）。"""
    row = RN.db.query_one("SELECT * FROM realname_profiles WHERE id=?", (row_id,))
    if not row:
        raise ApiError("记录不存在")
    RN.revoke(row["user_id"])
    return ok(None, "已撤销，该账号需重新提交实名信息")
