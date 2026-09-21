# -*- coding: utf-8 -*-
"""内置文档：把《使用说明》《开发文档》打进程序，前端菜单直接打开。

文档文件路径由 config.DOCS_DIR 决定（冻结时为 _MEIPASS/docs，开发时为项目根 docs）。
只暴露白名单里的文档，避免把任意文件读出去。
"""
from fastapi import APIRouter, Depends

from ..config import DOCS_DIR
from ..deps import ApiError, get_current_user, ok
from ..permissions import has_perm

# 注意：不能用 /api/docs —— 那是 FastAPI 自带 Swagger 的路径，会被它吃掉
router = APIRouter(prefix="/api/manual", tags=["docs"])

# key -> (文件名, 标题, 说明, 需要的权限组；None 表示所有登录用户可见)
DOCS = {
    "usage": ("使用说明.md", "使用说明",
              "面向所有使用者：启动、登录、各角色操作指引、常见问题", None),
    "dev": ("开发文档.md", "开发文档",
            "面向管理员与二次开发：架构、接口、权限模型、测试与打包", "user_manage"),
}


def _doc_path(key: str):
    item = DOCS.get(key)
    if not item:
        raise ApiError("文档不存在", code=404, status=404)
    return DOCS_DIR / item[0], item


@router.get("")
def list_docs(user=Depends(get_current_user)):
    """列出当前账号可看的文档。"""
    items = []
    for key, (_, title, desc, perm) in DOCS.items():
        if perm and not has_perm(user, perm):
            continue
        items.append({"key": key, "title": title, "desc": desc})
    return ok({"list": items})


@router.get("/{key}")
def get_doc(key: str, user=Depends(get_current_user)):
    """返回 Markdown 原文，由前端渲染。"""
    path, (_, title, desc, perm) = _doc_path(key)
    if perm and not has_perm(user, perm):
        raise ApiError("无权查看该文档", code=403, status=403)
    if not path.exists():
        raise ApiError(f"文档未随程序打包：{path.name}", code=404, status=404)
    return ok({
        "key": key, "title": title, "desc": desc,
        "content": path.read_text(encoding="utf-8"),
    })
