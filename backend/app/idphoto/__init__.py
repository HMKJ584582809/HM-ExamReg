# -*- coding: utf-8 -*-
"""证件照制作（模块 12）。

对外只用 ``make()`` / ``public_specs()`` / ``engines_status()`` 三个入口。
抠图算法源自 HivisionIDPhotos（Apache-2.0），已内置模型与许可证。
"""
from .maker import (IdPhotoError, engines_status, make, matting_only,
                    remote_url, default_params, clamp_param, PARAM_RANGES,
                    BLANK_FILLS)
from .specs import public_specs

__all__ = ["make", "matting_only", "public_specs", "engines_status",
           "remote_url", "default_params", "clamp_param", "PARAM_RANGES",
           "BLANK_FILLS", "IdPhotoError"]
