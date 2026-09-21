# -*- coding: utf-8 -*-
"""本地人像抠图（ONNX / MODNet）。

算法与预处理源自开源项目 **HivisionIDPhotos**（Apache License 2.0）
    https://github.com/Zeyi-Lin/HivisionIDPhotos
版权归原作者 Zeyi-Lin 所有，许可证全文见
    backend/app/assets/idphoto/LICENSE-HivisionIDPhotos.txt

改写说明
--------
原实现依赖 OpenCV（cv2.resize / cv2.split / cv2.merge）。为了避免为一个
抠图功能引入 opencv、rembg、scipy 等一整条重型依赖链（它们会让单文件 exe
膨胀上百 MB、且部分包在受限环境装不上），这里用 **Pillow + numpy** 等价替换：

    cv2.resize(..., INTER_AREA)  →  Image.resize(..., Resampling.BOX)
    cv2.split / cv2.merge        →  numpy 切片与 dstack

模型：hivision_modnet.onnx（24.7MB，随程序内置，离线可用，照片不出本机）。
"""
import os
import sys
import threading
from pathlib import Path

import numpy as np
from PIL import Image

# 冻结（exe）时资源在 _MEIPASS 下；开发时在 backend/app/assets 下
if getattr(sys, "frozen", False):  # pragma: no cover - 仅打包态
    _BASE = Path(sys._MEIPASS) / "app" / "assets" / "idphoto"
else:
    _BASE = Path(__file__).resolve().parent.parent / "assets" / "idphoto"

MODEL_PATH = _BASE / "hivision_modnet.onnx"

REF_SIZE = 512
_SESS = None
_SESS_LOCK = threading.Lock()
_LOAD_ERR = ""


def available() -> tuple:
    """(是否可用, 不可用的原因)。"""
    if not MODEL_PATH.exists():
        return False, "抠图模型未随程序打包：hivision_modnet.onnx"
    try:
        import onnxruntime  # noqa: F401
    except Exception as e:
        return False, "未安装推理运行时 onnxruntime：%s" % type(e).__name__
    return True, ""


def _session():
    """按需加载并缓存推理会话（首次约 1~2 秒）。"""
    global _SESS, _LOAD_ERR
    if _SESS is not None:
        return _SESS
    with _SESS_LOCK:
        if _SESS is not None:
            return _SESS
        import onnxruntime
        try:
            _SESS = onnxruntime.InferenceSession(
                str(MODEL_PATH), providers=["CPUExecutionProvider"])
            _LOAD_ERR = ""
        except Exception as e:
            _LOAD_ERR = "%s: %s" % (type(e).__name__, e)
            _SESS = None
    return _SESS


def _resize(im: Image.Image, size: tuple) -> Image.Image:
    """等价 cv2.INTER_AREA：缩小时用 BOX，放大时用 BICUBIC（BOX 放大会块状化）。"""
    w, h = size
    if w < im.width or h < im.height:
        return im.resize((w, h), Image.Resampling.BOX)
    return im.resize((w, h), Image.Resampling.BICUBIC)


def _to_rgb(arr: np.ndarray) -> np.ndarray:
    """原 human_matting.image2bgr：灰度扩成 3 通道、RGBA 取前 3 通道。
    注意它并没有真的做 RGB→BGR 转换，这里保持同样行为。"""
    if arr.ndim == 2:
        arr = arr[:, :, None]
    if arr.shape[2] == 1:
        return np.repeat(arr, 3, axis=2)
    if arr.shape[2] == 4:
        return arr[:, :, 0:3]
    return arr


def refine(rgba: Image.Image, threshold: int = 55, feather: int = 10,
           bottom_fill: int = 0) -> Image.Image:
    """抠图后处理：把半透明边缘「压实」，避免合成后透出原背景色（漏色）。

    模型输出的 alpha 在人物边缘是一段渐变（0→255），直接贴到底色上时这部分
    会与原背景混色，表现为轮廓发灰 / 底部残留一条原背景色。这里做两件事：

    1. 阈值 + 羽化：alpha < threshold 一律当背景（置 0），
       threshold ~ threshold+feather 之间线性拉伸到 0~255，之上直接置 255。
    2. 底部补底：底部 bottom_fill% 的行内，凡非实心的像素一律置 0，
       让选定底色完全透上来（专治「底部漏色」）。
    """
    threshold = int(max(0, min(255, threshold or 0)))
    feather = int(max(0, min(60, feather or 0)))
    bottom_fill = int(max(0, min(60, bottom_fill or 0)))
    if threshold <= 0 and feather <= 0 and bottom_fill <= 0:
        return rgba

    arr = np.asarray(rgba)
    a = arr[:, :, 3].astype(np.float32)

    if threshold > 0 or feather > 0:
        lo = float(threshold)
        hi = float(min(255, lo + max(feather, 1)))
        # (a - lo) / (hi - lo) * 255，再截断到 0~255
        a = np.clip((a - lo) * (255.0 / max(hi - lo, 1.0)), 0.0, 255.0)

    if bottom_fill > 0:
        rows = max(1, int(round(arr.shape[0] * bottom_fill / 100.0)))
        tail = a[-rows:, :]
        tail[tail < 255] = 0.0

    arr = arr.copy()
    arr[:, :, 3] = a.astype(np.uint8)
    return Image.fromarray(arr, mode="RGBA")


def matte(image: Image.Image) -> Image.Image:
    """人像抠图：输入 PIL 图像，输出带 alpha 的 RGBA 图像。

    失败时抛出 RuntimeError（调用方决定降级策略）。
    """
    sess = _session()
    if sess is None:
        raise RuntimeError(_LOAD_ERR or "抠图模型加载失败")

    src = image.convert("RGB")
    arr = np.asarray(src)
    arr = _to_rgb(arr)

    # 缩放至 512×512 并归一化到 [-1, 1]：(x/255 - 0.5)/0.5
    small = _resize(Image.fromarray(arr), (REF_SIZE, REF_SIZE))
    x = np.asarray(small, dtype=np.float32) / 127.5 - 1.0
    x = np.transpose(x, (2, 0, 1))[None, :, :, :].astype(np.float32)

    in_name = sess.get_inputs()[0].name
    out_name = sess.get_outputs()[0].name
    out = sess.run([out_name], {in_name: x})[0]

    mask = np.squeeze((out[0] * 255).astype(np.uint8))
    mask = np.asarray(_resize(Image.fromarray(mask), (src.width, src.height)))
    return Image.fromarray(np.dstack((np.asarray(src), mask)), mode="RGBA")
