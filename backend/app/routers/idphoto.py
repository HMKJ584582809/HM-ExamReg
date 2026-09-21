# -*- coding: utf-8 -*-
"""模块12：证件照制作（1寸 / 2寸 + 自动换底色）。

权限组 ``idphoto``：默认所有角色都有（考生也要自己做证件照），管理员可单独关闭。
上传的照片**只在本次请求的内存里处理**，不落盘、不写库；
走本地引擎时完全离线，走远程引擎时才会把图片发出去（默认关闭）。
"""
import json
import urllib.parse

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response

from .. import idphoto as ID
from ..deps import ApiError, get_current_user, ok
from ..permissions import require_perms

router = APIRouter(prefix="/api/idphoto", tags=["idphoto"])

MEDIA = {"jpg": "image/jpeg", "png": "image/png"}


@router.get("/specs")
def specs(user=Depends(require_perms("idphoto"))):
    """尺寸 / 底色选项 + 两个引擎的可用状态 + 制作参数（初始化值）。"""
    data = ID.public_specs()
    data["engines"] = ID.engines_status()
    data["params"] = ID.default_params()
    data["param_ranges"] = {k: {"min": v[0], "max": v[1]}
                            for k, v in ID.PARAM_RANGES.items()}
    data["param_ranges"]["blank_fill"] = {"options": list(ID.BLANK_FILLS)}
    return ok(data)


@router.post("/preview")
async def preview(file: UploadFile = File(...), user=Depends(require_perms("idphoto"))):
    """只抠图不排版，返回透明 PNG，供前端预览抠图效果。"""
    data = await file.read()
    if not data:
        raise ApiError("文件内容为空")
    out = ID.matting_only(data)
    if not out:
        raise ApiError("抠图失败，请换一张背景较干净的人像照片")
    return Response(content=out, media_type="image/png")


@router.post("/make")
async def make(file: UploadFile = File(...),
               size: str = Form(""), color: str = Form(""),
               engine: str = Form("auto"), params: str = Form(""),
               user=Depends(require_perms("idphoto"))):
    """制作证件照：抠图 → 换底色 → 标准化尺寸，直接返回图片。

    params 是 JSON 字符串（抠图阈值 / 边缘羽化 / 底部补底 / 留白填充），
    缺省时取管理员配置的初始化参数。
    """
    data = await file.read()
    if not data:
        raise ApiError("文件内容为空")
    p = None
    if params:
        try:
            p = json.loads(params)
        except Exception:
            p = None
    try:
        r = ID.make(data, size, color, engine, p)
    except ID.IdPhotoError as e:
        raise ApiError(str(e))
    except Exception as e:
        raise ApiError("证件照制作失败：%s" % type(e).__name__)

    # ⚠ HTTP 头只能是 latin-1：文件名里的中文必须按 RFC 5987 用 filename* 传送，
    #   直接写 filename="1寸.jpg" 会抛 UnicodeEncodeError（500）。
    label = {"one_inch": "1寸", "two_inch": "2寸", "original": "原尺寸"}.get(size, "1寸")
    ascii_name = "idphoto_%sx%s.%s" % (r["width"], r["height"], r["ext"])
    utf8_name = urllib.parse.quote(f"证件照-{label}.{r['ext']}")
    return Response(
        content=r["data"], media_type=MEDIA.get(r["ext"], "image/jpeg"),
        headers={"Content-Disposition":
                 f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}",
                 "X-IdPhoto-Engine": r["engine"],
                 "X-IdPhoto-Size": f'{r["width"]}x{r["height"]}'})
