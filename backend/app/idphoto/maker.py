# -*- coding: utf-8 -*-
"""证件照制作：抠图 → 换底色 → 标准化尺寸。

双引擎设计
----------
证件照属于敏感个人信息，默认走 **本地引擎**（内置 ONNX 模型，照片不出本机、
断网可用）。管理员可在系统维护里配置 **远程引擎**（HivisionIDPhotos 的 HTTP
接口）作为备选：本地模型缺失或推理失败时自动回退，反之亦然。

远程接口约定（与 HivisionIDPhotos deploy_api 一致）：
    POST {url}
    {"user_id":"...","input_image":"<base64>","height":413,"width":295,"color":"638cce"}
"""
import base64
import io
import json
import urllib.error
import urllib.request
from typing import Optional

from PIL import Image, ImageDraw

from . import matting, specs

# 远程接口的默认地址（管理员可在系统维护里改）
DEFAULT_REMOTE_URL = "https://api.zhukawiki.asia/open/idphoto/make"
REMOTE_TIMEOUT = 60

# 单张输入上限：手机直出照片通常 3~8MB，留足余量
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_SIDE = 6000


class IdPhotoError(Exception):
    """制作失败（携带面向用户的中文原因）。"""


def _settings_idphoto() -> dict:
    try:
        from ..config import load_settings
        return (load_settings().get("idphoto") or {})
    except Exception:
        return {}


def remote_url() -> str:
    return (_settings_idphoto().get("remote_url") or DEFAULT_REMOTE_URL).strip()


# 制作参数的默认值与取值范围；管理员可在「系统维护」改默认值（初始化设置），
# 用户在制作页临时覆盖后随请求传进来。
PARAM_RANGES = {
    "alpha_threshold": (0, 255, 55),
    "edge_feather": (0, 60, 10),
    "bottom_fill": (0, 60, 12),
}
BLANK_FILLS = ("edge", "white")


def clamp_param(name: str, v) -> int:
    """把单个参数夹到合法区间内（非法/缺失回落到默认值）。"""
    lo, hi, dft = PARAM_RANGES[name]
    try:
        n = int(v)
    except (TypeError, ValueError):
        return dft
    return max(lo, min(hi, n))


def default_params() -> dict:
    """系统「初始化参数」：管理员在系统维护里设定的默认值。"""
    p = (_settings_idphoto().get("params") or {})
    return {
        "alpha_threshold": clamp_param("alpha_threshold", p.get("alpha_threshold")),
        "edge_feather": clamp_param("edge_feather", p.get("edge_feather")),
        "bottom_fill": clamp_param("bottom_fill", p.get("bottom_fill")),
        "blank_fill": p.get("blank_fill") if p.get("blank_fill") in BLANK_FILLS else "edge",
    }


def norm_params(p: Optional[dict]) -> dict:
    """把前端传来的参数规范化（缺失项回落到初始化参数）。"""
    d = default_params()
    if not isinstance(p, dict):
        return d
    for k in PARAM_RANGES:
        if k in p:
            d[k] = clamp_param(k, p.get(k))
    if p.get("blank_fill") in BLANK_FILLS:
        d["blank_fill"] = p["blank_fill"]
    return d


def engines_status() -> dict:
    """给前端/接口用：两个引擎当前是否可用。"""
    ok_local, why = matting.available()
    url = remote_url()
    return {
        "local": {"enabled": bool(ok_local), "reason": why, "label": "本地抠图（离线，照片不出本机）"},
        "remote": {"enabled": bool(url), "url": url, "label": "远程接口（需联网）"},
        "default": "local" if ok_local else ("remote" if url else "local"),
    }


def _contain_box(src_w: int, src_h: int, tw: int, th: int) -> tuple:
    """等比缩放并居中（contain）：宁可留边也不裁掉头部，符合证件照安全要求。"""
    if not tw or not th:
        return src_w, src_h
    scale = min(tw / src_w, th / src_h)
    return max(1, int(round(src_w * scale))), max(1, int(round(src_h * scale)))


def _compose(rgba: Image.Image, size: dict, color: dict) -> Image.Image:
    """把抠好的 RGBA 合成到目标尺寸与底色上。

    ⚠ 换底色时**不要**拿照片的边缘色去填留白：底色是用户指定的纯色，
    整幅就该是均匀的，把原照片的背景色填进来反而变成一条色带。
    留白取色（blank_fill）只在「不换底」那档有意义 —— 那里照片是整张贴上去的，
    留白必须跟它自己的背景衔接，填白会白得突兀（见 make() 的 keep_bg 分支）。

    另外：**别拿抠图后的 RGBA 去取边缘色**。被抠掉的区域 alpha=0 且 RGB 通常是
    (0,0,0)，平均出来就是纯黑，留白会填成一圈黑边（真实回归，肉眼一眼可见）。
    """
    tw, th = size.get("width") or 0, size.get("height") or 0
    nw, nh = _contain_box(rgba.width, rgba.height, tw, th)
    if (nw, nh) != (rgba.width, rgba.height):
        rgba = rgba.resize((nw, nh), Image.Resampling.LANCZOS)

    if not tw or not th:                      # 保持原图尺寸
        tw, th = rgba.width, rgba.height

    ox, oy = (tw - rgba.width) // 2, (th - rgba.height) // 2

    hex_str = color.get("hex")
    if hex_str is None:                       # 透明底：输出 PNG
        canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        canvas.paste(rgba, (ox, oy), rgba)
        return canvas

    bg = specs.hex_to_rgb(hex_str)
    canvas = Image.new("RGB", (tw, th), bg)
    canvas.paste(rgba, (ox, oy), rgba)
    return canvas


def _fill_blank_edges(canvas: Image.Image, rgb: Image.Image, ox: int, oy: int) -> None:
    """contain 留白：上/下/左/右分别用照片对应边的平均色填。

    ⚠ 早先的写法是把四条边混在一起求一个平均色再整块填——顶边是背景色、
    底边是衣服/地面色，平均出来的颜色跟谁都不像，填到底部就是一条突兀的
    色带（用户反馈的「底部漏色」）。改成逐边取色后，留白与相邻像素自然衔接。
    """
    top, bottom, left, right = _side_colors(rgb)
    tw, th = canvas.width, canvas.height
    w, h = rgb.width, rgb.height
    dr = ImageDraw.ImageDraw(canvas)
    if oy > 0:
        dr.rectangle([0, 0, tw, oy], fill=top)
    if oy + h < th:
        dr.rectangle([0, oy + h, tw, th], fill=bottom)
    if ox > 0:
        dr.rectangle([0, oy, ox, oy + h], fill=left)
    if ox + w < tw:
        dr.rectangle([ox + w, oy, tw, oy + h], fill=right)


def _side_colors(img: Image.Image) -> tuple:
    """返回 (上, 下, 左, 右) 四条边各自的平均 RGB。"""
    px = img.load()
    w, h = img.width, img.height
    step_x, step_y = max(1, w // 40), max(1, h // 40)

    def _avg(points):
        r = g = b = 0
        for x, y in points:
            p = px[x, y][:3]
            r += p[0]; g += p[1]; b += p[2]
        n = max(1, len(points))
        return (r // n, g // n, b // n)

    return (_avg([(x, 0) for x in range(0, w, step_x)]),
            _avg([(x, h - 1) for x in range(0, w, step_x)]),
            _avg([(0, y) for y in range(0, h, step_y)]),
            _avg([(w - 1, y) for y in range(0, h, step_y)]))


def _edge_color(img: Image.Image) -> tuple:
    """整图边缘平均色（兼容旧调用：不换底时的留白填充）。"""
    return _side_colors(img)[0]


def _sniff_ext(data: bytes) -> str:
    """按文件头判断格式（不信扩展名）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    return "jpg"


def _encode(img: Image.Image, transparent: bool) -> bytes:
    buf = io.BytesIO()
    if transparent:
        img.save(buf, "PNG")
        return buf.getvalue()
    img.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0)
    return buf.getvalue()


# ------------------------------------------------------------------ 远程引擎

def _remote_make(data: bytes, size: dict, color: dict, url: str) -> bytes:
    payload = {
        "user_id": "exam-signup",
        "input_image": base64.b64encode(data).decode("ascii"),
        "height": size.get("height") or 0,
        "width": size.get("width") or 0,
        "color": (color.get("hex") or "FFFFFF").lstrip("#"),
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=REMOTE_TIMEOUT) as resp:
            raw = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        raise IdPhotoError("远程接口返回 HTTP %s" % e.code)
    except Exception as e:
        raise IdPhotoError("无法连接远程接口：%s" % type(e).__name__)

    # 直接返回图片字节
    if ctype.startswith("image/"):
        return raw

    try:
        j = json.loads(raw.decode("utf-8"))
    except Exception:
        raise IdPhotoError("远程接口返回内容无法解析")

    if isinstance(j, dict) and j.get("status") is False:
        raise IdPhotoError("远程接口处理失败：%s" % (j.get("message") or j.get("msg") or "未知原因"))

    # 递归找第一个 base64 图片字段（不同部署版本字段名不一致）
    def _dig(obj, depth=0):
        if depth > 4:
            return None
        if isinstance(obj, str):
            s = obj.strip()
            if len(s) > 200 and not s.startswith("http"):
                try:
                    return base64.b64decode(s, validate=False)
                except Exception:
                    return None
            return None
        if isinstance(obj, dict):
            for k in ("idphoto_base64", "hd_idphoto_base64", "idphoto",
                      "image_base64", "image", "result", "data", "output"):
                if k in obj:
                    got = _dig(obj[k], depth + 1)
                    if got:
                        return got
            for v in obj.values():
                got = _dig(v, depth + 1)
                if got:
                    return got
        return None

    got = _dig(j)
    if not got:
        raise IdPhotoError("远程接口未返回可用的图片数据")
    return got


# ------------------------------------------------------------------ 对外入口

def make(data: bytes, size_key: str = "", color_key: str = "",
         engine: str = "auto", params: Optional[dict] = None) -> dict:
    """制作证件照。

    params 为可调参数（抠图阈值 / 边缘羽化 / 底部补底 / 留白填充），
    缺省时取管理员配置的初始化参数。

    返回 {"data": bytes, "ext": "jpg"/"png", "engine": "local"/"remote",
          "width": int, "height": int}
    """
    P = norm_params(params)
    p_blank = P["blank_fill"]
    if not data:
        raise IdPhotoError("请先选择一张照片")
    if len(data) > MAX_INPUT_BYTES:
        raise IdPhotoError("照片过大（上限 20MB）")

    # 「不换底」：只做尺寸排版，保留原背景。既不抠图也不联网，
    # 上传证件照时最常用——考生给的照片背景本来就是白的，没必要多此一举。
    keep_bg = (color_key or "").strip().lower() == "keep"
    size = specs.size_of(size_key or specs.DEFAULT_SIZE)
    color = specs.color_of(color_key or ("" if keep_bg else specs.DEFAULT_COLOR))
    transparent = (not keep_bg) and color.get("hex") is None

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception:
        raise IdPhotoError("无法读取该图片，请换一张 JPG/PNG 照片")

    if max(img.width, img.height) > MAX_SIDE:
        raise IdPhotoError("照片尺寸过大（边长上限 6000）")

    if keep_bg:
        tw, th = size.get("width") or 0, size.get("height") or 0
        if not tw or not th:                       # 原尺寸 + 不换底 = 原样返回
            return {"data": data, "ext": _sniff_ext(data), "engine": "none",
                    "width": img.width, "height": img.height}
        rgb = img.convert("RGB")
        nw, nh = _contain_box(rgb.width, rgb.height, tw, th)
        if (nw, nh) != (rgb.width, rgb.height):
            rgb = rgb.resize((nw, nh), Image.Resampling.LANCZOS)
        ox, oy = (tw - rgb.width) // 2, (th - rgb.height) // 2
        base = _side_colors(rgb)[0] if p_blank == "edge" else (255, 255, 255)
        canvas = Image.new("RGB", (tw, th), base)
        if p_blank == "edge" and (nw, nh) != (tw, th):
            _fill_blank_edges(canvas, rgb, ox, oy)
        canvas.paste(rgb, (ox, oy))
        return {"data": _encode(canvas, False), "ext": "jpg", "engine": "none",
                "width": tw, "height": th}

    ok_local, why_local = matting.available()
    url = remote_url()
    order = []
    if engine == "local":
        order = ["local"]
    elif engine == "remote":
        order = ["remote"]
    else:
        order = ["local", "remote"] if ok_local else ["remote", "local"]

    errors = []
    for eng in order:
        if eng == "local":
            if not ok_local:
                errors.append("本地：%s" % why_local)
                continue
            try:
                rgba = matting.matte(img)
                # 压实半透明边缘，否则原背景会从轮廓/底部透出来（漏色）
                rgba = matting.refine(rgba, P["alpha_threshold"],
                                      P["edge_feather"], P["bottom_fill"])
                out = _compose(rgba, size, color)
                return {"data": _encode(out, transparent),
                        "ext": "png" if transparent else "jpg",
                        "engine": "local", "width": out.width, "height": out.height}
            except Exception as e:
                errors.append("本地：%s" % (e if isinstance(e, IdPhotoError) else type(e).__name__))
        else:
            if not url:
                errors.append("远程：未配置接口地址")
                continue
            try:
                raw = _remote_make(data, size, color, url)
                # 远程结果已是成品，但仍校验一下能被解析
                probe = Image.open(io.BytesIO(raw))
                probe.load()
                return {"data": raw, "ext": "jpg", "engine": "remote",
                        "width": probe.width, "height": probe.height}
            except IdPhotoError as e:
                errors.append("远程：%s" % e)
            except Exception as e:
                errors.append("远程：%s" % type(e).__name__)

    raise IdPhotoError("证件照制作失败（%s）" % "；".join(errors))


def matting_only(data: bytes) -> Optional[bytes]:
    """只抠图不排版：返回透明 PNG，供前端预览。失败返回 None。"""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        rgba = matting.matte(img)
        buf = io.BytesIO()
        rgba.save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None
