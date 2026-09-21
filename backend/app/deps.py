# -*- coding: utf-8 -*-
"""通用依赖：统一响应、异常、鉴权与角色校验。"""
from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse

from . import db
from .security import decode_token


class ApiError(Exception):
    def __init__(self, message: str, code: int = 400, status: int = 400):
        self.message = message
        self.code = code
        self.status = status
        super().__init__(message)


def ok(data=None, message: str = "操作成功"):
    return {"code": 0, "message": message, "data": data}


def fail(message: str, code: int = 400):
    return {"code": code, "message": message, "data": None}


def register_exception_handlers(app):
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(status_code=exc.status,
                            content={"code": exc.code, "message": exc.message, "data": None})

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500,
                            content={"code": 500, "message": f"服务器内部错误：{exc}", "data": None})


def get_current_user(authorization: str = Header(default="")):
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError("未登录或登录已过期", code=401, status=401)
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except ValueError as e:
        raise ApiError(str(e), code=401, status=401)
    if payload.get("kind") != "access":
        raise ApiError("登录凭证无效", code=401, status=401)
    user = db.query_one(
        "SELECT id,username,real_name,phone,email,role,status,grade,college,department,"
        "class_name,classes,perms,photo,scope FROM users WHERE id=?", (int(payload["sub"]),))
    if not user:
        raise ApiError("账号不存在", code=401, status=401)
    if user["status"] != 1:
        raise ApiError("账号已被禁用，请联系管理员", code=403, status=403)
    return user


def require_roles(*roles):
    """按角色字面量校验（保留兼容）；新代码请优先使用 permissions.require_perms。"""
    def _dep(user=Depends(get_current_user)):
        if roles and user["role"] not in roles:
            raise ApiError("无权访问该功能", code=403, status=403)
        return user
    return _dep


def page_params(page: int = 1, page_size: int = 10):
    page = max(1, int(page or 1))
    page_size = min(200, max(1, int(page_size or 10)))
    return page, page_size
