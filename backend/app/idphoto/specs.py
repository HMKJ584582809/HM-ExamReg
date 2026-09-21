# -*- coding: utf-8 -*-
"""证件照规格：尺寸与底色。

尺寸按国内通用的 300dpi 打印标准（1mm ≈ 11.81px）：
  * 一寸 25×35mm → 295×413
  * 二寸 35×49mm → 413×579
底色取值沿用 HivisionIDPhotos 的 color_list_CN.csv（蓝 #438EDB / 红 #F14E55）。
"""
from typing import Optional

# key, 名称, 宽(px), 高(px), 毫米
SIZES = [
    {"key": "one_inch", "label": "一寸照片", "width": 295, "height": 413, "mm": "25×35mm"},
    {"key": "two_inch", "label": "二寸照片", "width": 413, "height": 579, "mm": "35×49mm"},
    {"key": "original", "label": "保持原图尺寸", "width": 0, "height": 0, "mm": ""},
]

# key, 名称, hex（None 表示保留透明通道）
COLORS = [
    {"key": "white", "label": "白底", "hex": "#FFFFFF"},
    {"key": "blue", "label": "蓝底", "hex": "#438EDB"},
    {"key": "red", "label": "红底", "hex": "#F14E55"},
    {"key": "transparent", "label": "透明底（PNG）", "hex": None},
]

DEFAULT_SIZE = "one_inch"
DEFAULT_COLOR = "white"


def size_of(key: str) -> dict:
    for s in SIZES:
        if s["key"] == key:
            return s
    return SIZES[0]


def color_of(key: str) -> dict:
    for c in COLORS:
        if c["key"] == key:
            return c
    return COLORS[0]


def hex_to_rgb(hex_str: Optional[str]) -> tuple:
    """'#438EDB' / '438EDB' → (67,142,219)；空值返回白色。"""
    s = (hex_str or "").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return (255, 255, 255)
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return (255, 255, 255)


def public_specs() -> dict:
    """给前端渲染选项用。"""
    return {"sizes": SIZES, "colors": COLORS,
            "default_size": DEFAULT_SIZE, "default_color": DEFAULT_COLOR}
