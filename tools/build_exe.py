# -*- coding: utf-8 -*-
"""
Windows exe 打包脚本（PyInstaller 单文件）。

用法：
    python tools/build_exe.py

产物：
    dist/考试报名系统.exe      可直接双击运行的桌面程序
    双击后自动启动本地 Web 服务并打开浏览器
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
APP_NAME = "考试报名系统"

HIDDEN = [
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan", "uvicorn.lifespan.on",
    "uvicorn.lifespan.off", "anyio._backends._asyncio",
    "multipart", "multipart.multipart", "python_multipart",
    "app.main", "app.permissions", "app.exam_types", "app.ai_engine", "app.netutil",
    "app.realname",          # 实名信息（PyInstaller 静态分析扫不到 from .. import 用法）
    "app.routers.realname",
    "app.routers.auth",
    "app.routers.dicts",
    "app.routers.exams", "app.routers.exam_types", "app.routers.applications",
    "app.routers.export",
    "app.routers.analysis", "app.routers.dashboard", "app.routers.users",
    "app.routers.system", "app.routers.ai", "app.routers.idphoto",
    # 证件照抠图：onnxruntime 的 Python 接口需要 numpy 传张量，
    # 且它的原生 DLL 由 pyinstaller-hooks-contrib 的 hook 收集
    "app.idphoto", "app.idphoto.matting", "app.idphoto.maker", "app.idphoto.specs",
    "onnxruntime", "onnxruntime.capi", "onnxruntime.capi.onnxruntime_pybind11_state",
]

# ⚠ numpy 不能排除：证件照抠图要用它组装模型输入张量
#   （早期为瘦身把它排除了，接入 onnxruntime 后必须移除，否则运行时 ImportError）
EXCLUDE = ["tkinter", "matplotlib", "pandas", "scipy", "PyQt5", "PySide2",
           "notebook", "IPython", "pytest", "setuptools._distutils"]


def main():
    import argparse as _ap
    ap = _ap.ArgumentParser()
    ap.add_argument("--github", action="store_true",
                    help="构建对外开源版本：exe 名带 -GitHub，且**不携带** local_defaults.json")
    a = ap.parse_args()

    name = APP_NAME + ("-GitHub" if a.github else "")
    print("=" * 66)
    print("打包 Windows exe ·", name,
          "（开源版，不含本地配置）" if a.github else "（本地版，含本地配置）")

    # 1) 图标
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)
    icon = ROOT / "build" / "app.ico"

    # 2) 清理旧产物
    for p in (ROOT / "dist", ROOT / "build" / APP_NAME):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    # 3) PyInstaller
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onefile",
        "--name", name,
        "--icon", str(icon),
        "--add-data", f"{BACKEND / 'app' / 'static'};app/static",
        "--add-data", f"{BACKEND / 'app' / 'data'};app/data",
        # 内置文档：前端菜单可直接打开（使用说明 / 开发文档）
        "--add-data", f"{ROOT / 'docs'};docs",
        # 证件照抠图模型 + 第三方许可证（离线可用，照片不出本机）
        "--add-data", f"{BACKEND / 'app' / 'assets' / 'idphoto'};app/assets/idphoto",
        # 汇总导出的官方模板副本：模板模式要按原表头定位列，缺文件会静默退回自建模式
        "--add-data", f"{BACKEND / 'app' / 'assets' / 'templates'};app/assets/templates",
        "--paths", str(BACKEND),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build" / "work"),
        "--specpath", str(ROOT / "build"),
        "--console",
    ]
    for h in HIDDEN:
        cmd += ["--hidden-import", h]
    for e in EXCLUDE:
        cmd += ["--exclude-module", e]
    cmd.append(str(BACKEND / "launcher.py"))

    print("执行：", " ".join(cmd[:8]), "...")
    r = subprocess.run(cmd, cwd=str(BACKEND))
    if r.returncode != 0:
        print("打包失败，返回码", r.returncode)
        return r.returncode

    exe = ROOT / "dist" / f"{name}.exe"
    if not exe.exists():
        print("未找到产物 exe")
        return 1

    # 4) 随包附带使用说明（docs/ 为源文件，避免打包清理 dist 时丢失）
    doc = ROOT / "docs" / "使用说明.md"
    if doc.exists():
        shutil.copy2(doc, ROOT / "dist" / "使用说明.md")
        print("已附带：使用说明.md")

    # 4.5) 本地部署覆盖：与 exe 平级放一份，程序启动时从 exe 同级目录读。
    #      开源版（--github）**绝不能**带它 —— 里面是具体院校的校名/院系/部门。
    local_cfg = ROOT / "local_defaults.json"
    if a.github:
        leaked = list((ROOT / "dist").rglob("local_defaults.json"))
        if leaked:
            for p in leaked:
                p.unlink()
            print("!! 已清除误带入的本地配置：", [str(p) for p in leaked])
        print("开源版：不携带 local_defaults.json（字典与单位预填均为空）")
    elif local_cfg.exists():
        shutil.copy2(local_cfg, ROOT / "dist" / "local_defaults.json")
        print("已附带：local_defaults.json（本地版专用）")

    # 5) 正式交付校验：dist 内不得残留任何测试数据库 / 演示数据
    leftover = [p for p in (ROOT / "dist").rglob("*")
                if p.is_file() and (p.suffix in (".db", ".db-wal", ".db-shm")
                                    or p.name == "secret.key")]
    if leftover:
        print("警告：dist 内存在运行期数据文件，已自动清理：")
        for p in leftover:
            print("   -", p.relative_to(ROOT / "dist"))
            try:
                p.unlink()
            except OSError:
                pass
    print("交付校验：不含测试数据与测试账号（首次运行自动创建管理员账号）")

    print("=" * 66)
    print("打包成功：", exe)
    print("文件大小：%.1f MB" % (exe.stat().st_size / 1024 / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
