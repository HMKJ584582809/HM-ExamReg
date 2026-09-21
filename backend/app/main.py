# -*- coding: utf-8 -*-
"""考试报名信息采集与审核管理系统 · 服务入口（FastAPI 应用）。"""
import threading
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import APP_NAME, APP_VERSION, STATIC_DIR, ensure_dirs
from .deps import register_exception_handlers
from .routers import (ai, analysis, applications, auth, dashboard, dicts, docs, exams,
                      exam_types, export, idphoto, photos, realname, system, users)

app = FastAPI(title=APP_NAME, version=APP_VERSION, docs_url="/api/docs", openapi_url="/api/openapi.json")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(dicts.router)
app.include_router(exams.router)
app.include_router(exam_types.router)
app.include_router(applications.router)
app.include_router(export.router)
app.include_router(analysis.router)
app.include_router(photos.router)
app.include_router(dashboard.router)
app.include_router(system.router)
app.include_router(ai.router)
app.include_router(docs.router)
app.include_router(idphoto.router)
app.include_router(realname.router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup():
    ensure_dirs()
    db.init_db()
    t = threading.Thread(target=_auto_close_loop, daemon=True)
    t.start()


def _auto_close_loop():
    """后台定时任务：报名窗口结束后自动关闭批次。"""
    from .routers.exams import auto_close_exams
    while True:
        try:
            auto_close_exams()
        except Exception:
            pass
        time.sleep(60)


@app.get("/api/health")
def health():
    return {"code": 0, "message": "ok",
            "data": {"app": APP_NAME, "version": APP_VERSION, "time": db.now_str()}}


@app.get("/favicon.ico")
def favicon():
    icon = STATIC_DIR / "favicon.ico"
    if icon.exists():
        return FileResponse(str(icon))
    return JSONResponse(status_code=204, content=None)


@app.get("/{full_path:path}")
def spa(full_path: str, request: Request):
    """前端单页应用路由回退：非 /api 前缀一律返回 index.html。"""
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404,
                            content={"code": 404, "message": "接口不存在", "data": None})
    index = Path(STATIC_DIR) / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return JSONResponse(status_code=500, content={"code": 500, "message": "前端资源缺失", "data": None})
