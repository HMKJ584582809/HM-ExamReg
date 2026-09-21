# -*- coding: utf-8 -*-
"""把 npm 下载的前端依赖复制到 static/vendor（保证 exe 离线可用）。"""
import shutil
from pathlib import Path

SRC = Path(r"C:\Users\Administrator\AppData\Local\Temp\fe\node_modules")
if not SRC.exists():
    SRC = Path("/tmp/fe/node_modules")

DST = Path(__file__).resolve().parents[1] / "backend" / "app" / "static" / "vendor"
DST.mkdir(parents=True, exist_ok=True)

FILES = {
    "vue/dist/vue.global.prod.js": "vue.global.prod.js",
    "vue-router/dist/vue-router.global.prod.js": "vue-router.global.prod.js",
    "element-plus/dist/index.full.min.js": "element-plus.full.min.js",
    "element-plus/dist/index.css": "element-plus.css",
    "@element-plus/icons-vue/dist/index.iife.min.js": "element-plus-icons.iife.min.js",
    "echarts/dist/echarts.min.js": "echarts.min.js",
}

for src_rel, dst_name in FILES.items():
    s = SRC / src_rel
    if not s.exists():
        print("[MISS]", src_rel)
        continue
    shutil.copy2(s, DST / dst_name)
    print("[OK]", dst_name, round((DST / dst_name).stat().st_size / 1024, 1), "KB")
