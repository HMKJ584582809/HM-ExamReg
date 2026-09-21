# -*- coding: utf-8 -*-
"""全局配置与路径解析（兼容 PyInstaller 打包后的运行环境）。"""
import json
import os
import sys
from pathlib import Path


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


# 资源目录：打包后资源位于 _MEIPASS/app（--add-data 目标为 app/static、app/data）
if _is_frozen():
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "app"
else:
    RESOURCE_DIR = Path(__file__).resolve().parent

APP_DIR = RESOURCE_DIR
STATIC_DIR = APP_DIR / "static"
DATA_DIR_INTERNAL = APP_DIR / "data"

# 内置文档（使用说明 / 开发文档）：打包时 --add-data 到 docs，与 app 平级
if _is_frozen():
    DOCS_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "docs"
else:
    DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"

# 运行期数据目录：exe 同级 data/（可写），开发时为项目根 data/
if _is_frozen():
    RUNTIME_DIR = Path(sys.executable).resolve().parent
else:
    RUNTIME_DIR = APP_DIR.parents[1]

# --------------------------------------------------------------------------- #
# 本地部署覆盖（local_defaults.json）
#
# 源码是公开的，任何具体院校的名称 / 院系 / 部门都不能写进仓库。
# 但本单位自己部署时又希望开箱即用 —— 所以把这些值放到**仓库外**的
# `local_defaults.json`（已 gitignore），只有本地构建会把它一起打进 exe。
#
# 查找位置：打包后取 exe 同级目录，开发时取项目根。文件不存在就是空字典，
# 行为与开源版完全一致（不做预填、不预置字典）。
# --------------------------------------------------------------------------- #
LOCAL_DEFAULTS_PATH = RUNTIME_DIR / "local_defaults.json"


def load_local_defaults() -> dict:
    """读取本地部署覆盖；读不到就返回空字典（绝不能因此报错影响启动）。"""
    try:
        if LOCAL_DEFAULTS_PATH.exists():
            data = json.loads(LOCAL_DEFAULTS_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


LOCAL_DEFAULTS = load_local_defaults()

DATA_DIR = RUNTIME_DIR / "data"
DB_PATH = DATA_DIR / "app.db"
SECRET_PATH = DATA_DIR / "secret.key"
LOG_PATH = DATA_DIR / "server.log"
EXPORT_DIR = DATA_DIR / "exports"
SETTINGS_PATH = DATA_DIR / "settings.json"
# 证件照：文件存磁盘、库里只存文件名（u{user_id}.{ext}），避免库膨胀与敏感信息入文件名
PHOTO_DIR = DATA_DIR / "photos"
PHOTO_TMP_DIR = PHOTO_DIR / "_tmp"
# 汇总导出用的官方模板：内置一份，管理员可上传覆盖版（存在数据目录，升级不丢）
TEMPLATE_DIR = DATA_DIR / "templates"
PHOTO_MAX_BYTES = 5 * 1024 * 1024
PHOTO_EXTS = ("jpg", "jpeg", "png")

APP_NAME = "考试报名信息采集与审核管理系统"
APP_VERSION = "1.1.0"
DEFAULT_PORT = int(os.environ.get("EXAM_PORT", "8765"))
# 监听地址：默认仅本机；设为 0.0.0.0 监听全部网卡（也可用 --lan / --host 覆盖）
HOST = os.environ.get("EXAM_HOST", "127.0.0.1")

# 开发/自测模式：EXAM_DEV=1 时验证码接口会附带明文验证码，便于自动化测试
DEV_MODE = os.environ.get("EXAM_DEV", "") == "1"

# 演示数据开关：默认关闭（正式交付版本不含任何测试数据与测试账号）。
# 仅在开发/演示时设置 EXAM_SEED_DEMO=1 才会写入演示考试、演示报名与演示账号。
SEED_DEMO = os.environ.get("EXAM_SEED_DEMO", "") == "1"

# 初始管理员账号（首次初始化数据库时创建，可用 EXAM_ADMIN_PASSWORD 覆盖）
DEFAULT_ADMIN_USERNAME = os.environ.get("EXAM_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("EXAM_ADMIN_PASSWORD", "Admin@123")

# JWT
ACCESS_TOKEN_EXPIRE_MINUTES = 12 * 60      # 勾选「记住我」：12 小时
SESSION_TOKEN_EXPIRE_MINUTES = 2 * 60      # 未勾选「记住我」：2 小时（会话级）
REFRESH_TOKEN_EXPIRE_DAYS = 7

_dicts_cache = None


def load_dicts() -> dict:
    """加载由官方模板解析出的字典数据（进程内缓存）。"""
    global _dicts_cache
    if _dicts_cache is None:
        path = DATA_DIR_INTERNAL / "dicts.json"
        with open(path, "r", encoding="utf-8") as f:
            _dicts_cache = json.load(f)
    return _dicts_cache


def ensure_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    PHOTO_TMP_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


# 验证码通道配置（邮箱 SMTP / 短信网关）。未配置时对应通道走「演示模式」：
# 验证码写入服务端控制台日志，不真正外发。
DEFAULT_SETTINGS = {
    "smtp": {"host": "", "port": 465, "ssl": True, "user": "", "password": "", "sender": ""},
    "sms": {"provider": "", "endpoint": "", "api_key": "", "sign": "", "template": ""},
    "code_ttl_seconds": 300,
    "code_resend_seconds": 60,
    "code_daily_limit": 10,
    # AI 能力：默认关闭外部大模型，本地规则引擎零配置即可用
    "ai": {"enable": 0, "provider": "custom", "base_url": "", "api_key": "",
           "model": "", "timeout": 30, "system_prompt": "", "proxy": ""},
    # 局域网访问：默认开启（集中采集场景需要同事也能打开）；关闭则只监听 127.0.0.1
    # 由启动器读取，改动需重启程序生效
    "network": {"lan_access": 1},
    # 报名表单默认值：考生打开表单时预填（留空表示不预填）。
    # 多数院校是整班/整校统一报名，所在单位几乎相同，预填可省掉大量重复录入。
    # ⚠ 默认留空：这份代码是公开的，预填具体单位名等于把使用单位的名称写进公开仓库。
    #   本单位自己部署时可放 local_defaults.json（gitignore）覆盖，本地构建会带上它。
    "apply": {"employer_default": LOCAL_DEFAULTS.get("employer_default", "")},
    # 模拟模式（压力测试）：仅管理员可开关；开启时按 count 批量灌入模拟报名数据，
    # 只填必要字段、不追求完整，用于列表 / 导出 / 大屏的性能验证。
    # 关闭时自动清除（按用户名前缀 mock_ 识别），保证正式环境不留测试数据。
    "mock": {"enable": 0, "count": 5000},
    # 证件照制作：默认走本地内置模型（离线、照片不出本机）；
    # remote_url 留空表示禁用远程引擎，填了则在本地不可用时回退到它。
    # params 是「初始化参数」——管理员在这里定默认值，用户在制作页上可临时覆盖。
    #   alpha_threshold 抠图阈值：alpha 低于它一律当背景（治半透明边缘漏色）
    #   edge_feather    边缘羽化：阈值之上的过渡带宽度，避免硬边锯齿
    #   bottom_fill     底部补底：底部 N% 行内非实心的像素强制透明（治底部漏色）
    #   blank_fill      留白填充：contain 排版留边时填「边缘色」还是「纯白」
    "idphoto": {
        "remote_url": "", "prefer": "local",
        "params": {"alpha_threshold": 55, "edge_feather": 10,
                   "bottom_fill": 12, "blank_fill": "edge"},
    },
    # 注册：验证方式默认只开图形验证码；短信/邮箱需管理员显式开启（且网关已配置）。
    # 注册页只显示已开启的方式——没开的方式连入口都不给，避免用户填完了才发现发不出码。
    "register": {"captcha": 1, "sms": 0, "email": 0},
    # 找回密码：总开关默认开启；验证方式默认只开图形验证码，
    # 短信/邮箱需管理员在后台显式开启（且网关已配置）才可用。
    "reset_password": {"enable": 1, "captcha": 1, "sms": 0, "email": 0},
    # 实名信息：默认开启「用户自填 + 管理员审核」。
    # 第三方通道（支付宝 / 微信）需要商户资质与公网回调地址，默认关闭；
    # 未配置齐全时只能走内置模拟通道（用于联调演示，不会真的核验证件真伪）。
    "realname": {
        "enable": 1,            # 总开关：关闭后考生看不到实名入口
        "require_approved": 0,  # 是否要求实名通过才允许提交报名
        "self_fill": 1,         # 允许用户自己填写并提交审核
        "alipay": {"enable": 0, "app_id": "", "private_key": "",
                   "alipay_public_key": ""},
        "wechat": {"enable": 0, "app_id": "", "app_secret": "",
                   "mch_id": "", "api_v3_key": ""},
    },
}


def load_settings() -> dict:
    """读取运行期配置；文件不存在时返回默认值（不落盘）。"""
    data = json.loads(json.dumps(DEFAULT_SETTINGS))
    if SETTINGS_PATH.exists():
        try:
            user_cfg = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for k, v in user_cfg.items():
                if isinstance(v, dict) and isinstance(data.get(k), dict):
                    data[k].update(v)
                else:
                    data[k] = v
        except Exception:
            pass
    return data


def save_settings(cfg: dict):
    ensure_dirs()
    SETTINGS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def mail_configured() -> bool:
    s = load_settings()["smtp"]
    return bool(s.get("host") and s.get("user") and s.get("password"))


def sms_configured() -> bool:
    """短信网关是否已配置到「可以真正发送」的程度。

    阿里云需要 AccessKeyId:AccessKeySecret、签名与模板号；
    通用 HTTP 网关需要 endpoint 与 api_key。缺任一项都不算配置完成，
    否则会出现「界面提示已发送、实际一个字都没发出去」的假象。
    """
    s = load_settings()["sms"]
    p = (s.get("provider") or "").strip().lower()
    key = (s.get("api_key") or "").strip()
    if p == "aliyun":
        return bool(":" in key and (s.get("sign") or "").strip()
                    and (s.get("template") or "").strip())
    if p == "generic":
        return bool((s.get("endpoint") or "").strip() and key)
    return False
