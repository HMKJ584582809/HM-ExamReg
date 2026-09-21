# -*- coding: utf-8 -*-
"""AI 引擎：本地规则分析 + 智能客服（可选接入外部大模型增强）。

设计原则
--------
1. **零配置可用**：本地规则引擎不联网、不依赖任何第三方 SDK，直接对本库数据做
   统计、体检、异常识别与建议生成。exe 单机分发、无外网环境下开箱即用。
2. **可增强但不依赖**：管理员在「AI 配置」里填好 OpenAI 兼容服务的
   base_url / api_key / model 后，智能分析会附加大模型的自然语言解读，
   客服问答在知识库未命中时由大模型兜底。未配置、超时、报错一律静默回退
   到本地结果，绝不影响软件本身的可用性。
3. **权限与范围不绕过**：所有取数都复用 ``analysis.build_scope()``（内部含
   ``scope_filter``），AI 只能看到当前账号本来就能看到的数据；客服里涉及
   实时数据的意图先做权限判断，看不到就明确说明原因。
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from . import db
from . import exam_types as ET
from .config import load_settings, save_settings
from .permissions import describe, has_perm, scope_label
from .routers.analysis import build_scope, summary_of
from .routers.applications import AUDIT_LABEL, EMAIL_RE, PHONE_RE, FIXED_REQUIRED
from .validators import validate_id_number

# ------------------------------------------------------------------ 1. 配置

AI_DEFAULTS = {
    "enable": 0,            # 0 本地规则引擎 / 1 启用外部大模型
    "provider": "custom",   # 仅作标记（实际按 base_url 反查预设），保存后便于回显
    "base_url": "",         # 例如 https://api.openai.com/v1
    "api_key": "",
    "model": "",            # 例如 gpt-4o-mini / qwen-plus
    "timeout": 30,          # 秒，5~120
    "system_prompt": "",    # 可选：追加的系统提示词
    # 访问境外模型（OpenAI / Claude / Gemini 等）时常用的出口代理，如 http://127.0.0.1:7890
    # 空 = 跟随系统环境变量；none = 强制直连；其余按 http/https 代理使用
    "proxy": "",
}

# 显式声明「不走代理」的取值（大小写不敏感）
PROXY_DIRECT = ("none", "direct", "off", "disable", "0", "false")
# 显式声明「跟随系统环境变量」的取值
PROXY_SYSTEM = ("auto", "system", "default", "env")

# 前端回传该值表示「不修改密钥」，避免明文密钥在页面来回传输
AI_KEY_MASK = "******"
# 前端回传该值表示「删除已保存的密钥」（空串会被当成「不修改」）
AI_KEY_CLEAR = "__clear__"

POSTCODE_RE = re.compile(r"^\d{6}$")
GENDER_OF_DIGIT = {0: "女", 1: "男"}

# ------------------------------------------------------------------ 1.1 预设厂商

# 常见 OpenAI 兼容服务的预设。管理员在「大模型配置」里点一下即可填好
# 服务地址与候选模型，也可以继续手动改、或选「自定义」自己填。
#   no_key  = 本地自建服务，不需要密钥（留空即可）
#   local   = 跑在本机，永远直连，不受代理设置影响
#   overseas= 境外服务，国内网络通常需要代理
PROVIDERS = [
    {"key": "openai", "label": "OpenAI", "overseas": True,
     "base_url": "https://api.openai.com/v1",
     "models": ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"],
     "key_hint": "sk- 开头",
     "note": "境外服务，国内一般需要在「网络代理」里填写本机代理地址才能连通。"},
    {"key": "deepseek", "label": "DeepSeek 深度求索",
     "base_url": "https://api.deepseek.com/v1",
     "models": ["deepseek-chat", "deepseek-reasoner"],
     "key_hint": "sk- 开头",
     "note": "国内直连可用；deepseek-reasoner 为推理模型，响应较慢，建议把超时调到 60 秒以上。"},
    {"key": "doubao", "label": "豆包（火山方舟）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "models": ["doubao-pro-32k", "doubao-lite-32k"],
     "key_hint": "方舟 API Key",
     "note": "模型名请填控制台里的「推理接入点 ID」（形如 ep-xxxxxxxx），不是模型代号。"},
    {"key": "qwen", "label": "通义千问（阿里云百炼）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-plus", "qwen-turbo", "qwen-max"],
     "key_hint": "sk- 开头",
     "note": "使用百炼的 OpenAI 兼容模式地址，注意路径带 /compatible-mode/v1。"},
    {"key": "zhipu", "label": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",
     "models": ["glm-4-flash", "glm-4-air", "glm-4-plus"],
     "key_hint": "形如 xxxxx.yyyyy",
     "note": "glm-4-flash 免费且响应快，适合先做连通性测试。"},
    {"key": "moonshot", "label": "Moonshot Kimi",
     "base_url": "https://api.moonshot.cn/v1",
     "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
     "key_hint": "sk- 开头"},
    {"key": "siliconflow", "label": "硅基流动 SiliconFlow",
     "base_url": "https://api.siliconflow.cn/v1",
     "models": ["Qwen/Qwen2.5-7B-Instruct", "deepseek-ai/DeepSeek-V3",
                "THUDM/glm-4-9b-chat"],
     "key_hint": "sk- 开头",
     "note": "模型名要带厂商前缀，例如 Qwen/Qwen2.5-7B-Instruct。"},
    {"key": "baichuan", "label": "百川智能",
     "base_url": "https://api.baichuan-ai.com/v1",
     "models": ["Baichuan4", "Baichuan3-Turbo", "Baichuan2-Turbo"],
     "key_hint": "sk- 开头"},
    {"key": "ollama", "label": "本地 Ollama", "local": True, "no_key": True,
     "base_url": "http://127.0.0.1:11434/v1",
     "models": ["qwen2.5:7b", "llama3.1:8b", "gemma2:9b"],
     "key_hint": "本地服务无需密钥",
     "note": "先在本机跑起来（ollama serve）并下载模型；始终直连，不受代理设置影响。"},
    {"key": "custom", "label": "自定义（OpenAI 兼容）",
     "base_url": "", "models": [],
     "note": "任何实现了 /chat/completions 的 OpenAI 兼容服务都可以填。"},
]

PROVIDER_MAP = {p["key"]: p for p in PROVIDERS}


def public_providers() -> list:
    """给前端的预设厂商列表（不含任何密钥）。"""
    return [dict(p) for p in PROVIDERS]


def provider_of(base_url: str = "", provider: str = "") -> str:
    """按服务地址反查预设项 key；地址为空时才退回到已保存的 provider 标记。

    **必须以地址为准**：管理员可能选了「自定义」却填了 OpenAI 的地址，也可能选了
    OpenAI 又把地址改成自建服务——只认标记会给错提示（比如拿境内地址去提示配代理）。
    """
    b = (base_url or "").strip().rstrip("/").lower()
    if b:
        for p in PROVIDERS:
            if not p["base_url"]:
                continue
            if b == p["base_url"].rstrip("/").lower():
                return p["key"]
        for p in PROVIDERS:
            host = urllib.parse.urlsplit(p["base_url"] or "").hostname or ""
            if host and host.lower() in b:
                return p["key"]
        return "custom"
    if provider and provider in PROVIDER_MAP:
        return provider
    return "custom"
    for p in PROVIDERS:
        if not p["base_url"]:
            continue
        if b == p["base_url"].rstrip("/").lower():
            return p["key"]
    for p in PROVIDERS:                       # 退一步：按域名包含匹配
        host = (p["base_url"] or "")
        if not host:
            continue
        dom = urllib.parse.urlsplit(host).hostname or ""
        if dom and dom.lower() in b:
            return p["key"]
    return "custom"


def ai_config() -> dict:
    """读取 AI 配置（缺省值 + settings.json 的 ai 段）。"""
    out = dict(AI_DEFAULTS)
    raw = load_settings().get("ai")
    if isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k in AI_DEFAULTS})
    return out


def save_ai_config(patch: dict) -> dict:
    """写入 AI 配置；密钥传掩码或空串表示不修改。"""
    cur = ai_config()
    for k in AI_DEFAULTS:
        if k not in patch:
            continue
        v = patch[k]
        if k == "api_key":
            if v == AI_KEY_CLEAR:
                cur["api_key"] = ""            # 显式清除
                continue
            if v in ("", AI_KEY_MASK, None):
                continue                       # 保留原密钥
        elif k == "enable":
            v = 1 if str(v).lower() in ("1", "true", "yes", "on") else 0
        elif k == "timeout":
            try:
                v = max(5, min(120, int(v)))
            except (TypeError, ValueError):
                continue
        else:
            v = str(v or "").strip()
        cur[k] = v
    s = load_settings()
    s["ai"] = cur
    save_settings(s)
    return cur


def public_ai_config() -> dict:
    """返回给前端的配置：密钥只回是否配置，绝不回明文。"""
    c = dict(ai_config())
    c["api_key_set"] = bool(c.get("api_key"))
    c["api_key"] = AI_KEY_MASK if c["api_key_set"] else ""
    # 已保存的地址能反查到预设时回显厂商，前端据此高亮下拉项
    c["provider"] = provider_of(c.get("base_url") or "")
    return c


def llm_ready(cfg: dict = None) -> bool:
    """是否具备调用外部大模型的条件。"""
    c = cfg or ai_config()
    return (int(c.get("enable") or 0) == 1
            and bool((c.get("base_url") or "").strip())
            and bool((c.get("api_key") or "").strip())
            and bool((c.get("model") or "").strip()))


def _opener_for(url: str, proxy: str = ""):
    """按「目标地址 + 管理员配置的代理」选择 opener。

    判定顺序：
    1. 目标是本机（127.* / localhost / ::1）→ **永远直连**。自建模型
       （Ollama / vLLM / LM Studio）跑在本机，代理访问不到；而且很多单位机器
       设了系统代理，会把 127.* 也送进代理而失败。
    2. 管理员填了具体地址 → 用它（境外模型常见的 Clash / V2Ray 出口）。
    3. 填了 none/direct → 强制直连（忽略系统代理）。
    4. 留空 / auto → 跟随系统环境变量（urllib 默认行为）。
    """
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if host in ("localhost", "::1") or host.startswith("127."):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    p = (proxy or "").strip()
    low = p.lower()
    if not p or low in PROXY_SYSTEM:
        return urllib.request.build_opener()
    if low in PROXY_DIRECT:
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": p, "https": p}))


def proxy_of(cfg: dict = None) -> str:
    """当前生效的代理描述（给「测试连接」回显用，不含任何密钥）。"""
    return ((cfg or ai_config()).get("proxy") or "").strip()


# HTTP 状态码 -> (问题、该怎么办)。管理员按提示就能自己定位，不用翻日志。
HTTP_HINTS = {
    400: ("请求被拒绝（参数错误）",
          "多半是模型名称不被该服务支持，或请求里的参数它不接受；换一个模型试试。"),
    401: ("密钥无效或已失效",
          "检查密钥是否复制完整、是否被撤销；注意别把掩码 ****** 当成密钥提交。"),
    403: ("密钥无权限访问该模型",
          "密钥本身有效，但没有这个模型的调用权限，或账号未实名/未开通该服务。"),
    404: ("地址或模型不存在",
          "服务地址要以 /v1 之类的版本路径结尾但不能再带 /chat/completions；"
          "模型名要与厂商控制台里的完全一致。"),
    408: ("请求超时",
          "服务端处理太久，可把超时时间调大，或换一个更轻量的模型。"),
    409: ("请求冲突",
          "多为服务端限流或账号状态异常，稍后重试。"),
    422: ("请求参数不合法",
          "检查模型名是否带错前缀，或该服务要求额外必填参数。"),
    429: ("被限流或额度不足",
          "调用频率超过套餐上限，或账户余额已用完；充值或降低调用频率后再试。"),
}


def _diag_http(code: int) -> tuple:
    if code in HTTP_HINTS:
        return HTTP_HINTS[code]
    if 500 <= code < 600:
        return (f"服务端错误（HTTP {code}）",
                "厂商服务暂时不可用，稍后重试；持续报错可换一个服务或联系厂商。")
    return (f"服务端返回 HTTP {code}", "请对照该厂商的接口文档核对地址与参数。")


def _diag_exc(e: Exception, url: str) -> tuple:
    """把网络异常翻译成「发生了什么 + 该怎么做」。"""
    name = type(e).__name__
    low = str(e).lower()
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if isinstance(e, TimeoutError) or "timed out" in low or name == "timeout":
        if host.startswith("127.") or host in ("localhost", "::1"):
            return ("连接本机模型超时",
                    "确认本地服务已启动（如 ollama serve）且模型已下载完成。")
        return ("连接超时",
                "目标地址在超时时间内没有响应。境外模型请填写本机代理；"
                "也可以把超时时间调大后重试。")
    if isinstance(e, urllib.error.URLError):
        reason = getattr(e, "reason", None)
        rname = type(reason).__name__ if reason else ""
        if "certificate" in low or rname in ("SSLCertVerificationError", "SSLError"):
            return ("证书校验失败",
                    "常见于代理软件做 HTTPS 中间人解密。请更换代理、导入证书，"
                    "或在代理正常的前提下改用 http:// 的本机出口。")
        if "Name or service not known" in str(reason) or "getaddrinfo" in low:
            return ("域名无法解析",
                    "检查服务地址是否填错，或当前网络无法访问该域名。")
        if "Connection refused" in str(reason) or "refused" in low:
            if host.startswith("127.") or host in ("localhost", "::1"):
                return ("本机端口未监听",
                        "本地模型服务没有启动，或端口与配置不一致。")
            return ("连接被拒绝", "目标端口未开放，检查服务地址与端口。")
        if isinstance(reason, TimeoutError):
            return ("连接超时", "目标地址无响应，检查网络或代理设置。")
        if "proxy" in low:
            return ("代理不可用",
                    "填写的代理地址连不上。确认代理软件在运行，或把代理改为 none 直连试试。")
        return ("网络不可达", f"无法连接到 {host or '目标地址'}，检查网络、代理与服务地址。")
    if "ssl" in low or "certificate" in low:
        return ("证书校验失败", "HTTPS 证书不被信任，检查系统时间或更换网络环境。")
    if isinstance(e, (json.JSONDecodeError, ValueError)):
        return ("返回内容不是合法 JSON",
                "服务地址可能填错（返回了网页而非接口数据），核对地址结尾是否为 /v1 之类。")
    return (f"调用失败（{name}）", "检查服务地址、网络与代理设置后重试。")


def preflight(cfg: dict = None) -> dict:
    """调用前的参数自检：把「缺什么、该怎么补」一次性说清楚。"""
    cfg = cfg or ai_config()
    issues = []
    if int(cfg.get("enable") or 0) != 1:
        issues.append({"field": "enable", "label": "未启用大模型",
                       "hint": "把「启用大模型」开关打开后才会联网调用。"})
    base = (cfg.get("base_url") or "").strip()
    if not base:
        issues.append({"field": "base_url", "label": "未填写服务地址",
                       "hint": "可在上方选一个预设厂商自动填入，或手动填写 OpenAI 兼容地址。"})
    elif not base.lower().startswith(("http://", "https://")):
        issues.append({"field": "base_url", "label": "服务地址格式不对",
                       "hint": "需以 http:// 或 https:// 开头。"})
    elif base.rstrip("/").lower().endswith("/chat/completions"):
        issues.append({"field": "base_url", "label": "服务地址多带了接口路径",
                       "hint": "只填到版本目录即可（如 https://api.openai.com/v1），"
                               "系统会自动补 /chat/completions。"})
    prov_key = provider_of(base)          # 只看地址，避免「选了 A 却填了 B 的地址」
    prov = PROVIDER_MAP.get(prov_key) or {}
    if not (cfg.get("api_key") or "").strip() and not prov.get("no_key"):
        issues.append({"field": "api_key", "label": "未填写密钥",
                       "hint": "到厂商控制台创建密钥后填入，本机 Ollama 之类无需密钥的服务可留空。"})
    if not (cfg.get("model") or "").strip():
        issues.append({"field": "model", "label": "未填写模型名称",
                       "hint": "选了预设厂商后可从候选模型里直接挑一个。"})
    try:
        t = int(cfg.get("timeout") or 0)
    except (TypeError, ValueError):
        t = 0
    if t and not 5 <= t <= 120:
        issues.append({"field": "timeout", "label": "超时时间超出范围",
                       "hint": "需为 5-120 秒之间的整数。"})
    if prov.get("overseas") and not (cfg.get("proxy") or "").strip():
        issues.append({"field": "proxy", "label": f"「{prov.get('label')}」是境外服务",
                       "hint": "国内网络通常连不通，请在「网络代理」里填本机代理地址"
                               "（Clash / V2Ray 一般填 http://127.0.0.1:7890）。"})
    return {
        "ok": not issues,
        "issues": issues,
        "provider": prov_key,
        "provider_label": prov.get("label", ""),
        "provider_note": prov.get("note", ""),
        "candidate_models": list(prov.get("models") or []),
        "local": bool(prov.get("local")),
    }


def llm_call(messages: list, max_tokens: int = 900, temperature: float = 0.3,
             cfg: dict = None, probe: bool = False) -> dict:
    """调用 OpenAI 兼容的 /chat/completions，返回结构化诊断结果。

    ``probe=True`` 时跳过「是否启用」判断，用于管理员点「测试连接」先验证再保存。
    返回 dict：ok / stage / code / message / hint / text / latency_ms / meta。
    任何情况下都不抛异常，也绝不把密钥带进返回内容。
    """
    cfg = cfg or ai_config()
    base = (cfg.get("base_url") or "").strip().rstrip("/")
    key = (cfg.get("api_key") or "").strip()
    model = (cfg.get("model") or "").strip()
    url = base + "/chat/completions"
    meta = {"base_url": base, "model": model, "proxy": proxy_of(cfg) or "（跟随系统 / 直连）",
            "timeout": int(cfg.get("timeout") or 30)}

    if not probe and int(cfg.get("enable") or 0) != 1:
        return {"ok": False, "stage": "preflight", "code": "disabled",
                "message": "未启用大模型", "hint": "打开「启用大模型」开关后才会联网调用。",
                "text": None, "latency_ms": 0, "meta": meta}

    pf = preflight(cfg)
    if not pf["ok"] and not (probe and len(pf["issues"]) == 0):
        if not probe or not (base and model and (key or pf["local"])):
            first = pf["issues"][0]
            return {"ok": False, "stage": "preflight", "code": "incomplete_" + first["field"],
                    "message": first["label"], "hint": first["hint"],
                    "issues": pf["issues"], "text": None, "latency_ms": 0, "meta": meta}

    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature,
         "max_tokens": max_tokens}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)

    t0 = datetime.now()
    try:
        with _opener_for(url, cfg.get("proxy") or "").open(
                req, timeout=int(cfg.get("timeout") or 30)) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            status = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as e:
        ms = int((datetime.now() - t0).total_seconds() * 1000)
        body = ""
        try:
            body = (e.read() or b"").decode("utf-8", "ignore")[:300]
        except Exception:
            body = ""
        msg_, hint = _diag_http(e.code)
        # 厂商一般会在错误体里给出更具体的原因，能解析就一并带上
        detail = ""
        try:
            j = json.loads(body)
            detail = ((j.get("error") or {}).get("message")
                      or j.get("message") or j.get("msg") or "")
        except Exception:
            detail = ""
        if detail:
            msg_ = f"{msg_}：{detail[:120]}"
        if e.code == 404 and "model" in (detail or "").lower():
            # 本机/自建服务最常见的是模型名对不上，提示要指向模型而不是地址
            msg_, hint = ("模型不存在",
                          "模型名与该服务里已有的模型不一致，请核对后重填"
                          "（本地服务可先在命令行用 ollama list 查看已下载的模型）。")
        return {"ok": False, "stage": "request", "code": f"http_{e.code}",
                "message": msg_, "hint": hint, "text": None,
                "latency_ms": ms, "meta": meta}
    except Exception as e:
        ms = int((datetime.now() - t0).total_seconds() * 1000)
        msg_, hint = _diag_exc(e, url)
        return {"ok": False, "stage": "request", "code": type(e).__name__.lower(),
                "message": msg_, "hint": hint, "text": None,
                "latency_ms": ms, "meta": meta}

    ms = int((datetime.now() - t0).total_seconds() * 1000)
    try:
        j = json.loads(raw)
    except ValueError:
        return {"ok": False, "stage": "parse", "code": "bad_json",
                "message": "返回内容不是合法 JSON",
                "hint": "服务地址可能填错了（返回的是网页而不是接口数据）。",
                "text": None, "latency_ms": ms, "meta": meta}
    if isinstance(j, dict) and j.get("error"):
        err = j["error"]
        detail = err.get("message") if isinstance(err, dict) else str(err)
        code = (err.get("code") if isinstance(err, dict) else "") or ""
        return {"ok": False, "stage": "parse", "code": "api_error",
                "message": f"接口返回错误：{str(detail)[:160]}",
                "hint": ("错误码 " + str(code) + "；"
                         if code else "") + "按厂商文档核对模型名与密钥权限。",
                "text": None, "latency_ms": ms, "meta": meta}
    try:
        text = (j["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        return {"ok": False, "stage": "parse", "code": "bad_shape",
                "message": "大模型返回内容格式无法解析",
                "hint": "该服务可能不是标准的 OpenAI 兼容协议（缺少 choices[0].message.content）。",
                "text": None, "latency_ms": ms, "meta": meta}
    if not text:
        return {"ok": False, "stage": "parse", "code": "empty",
                "message": "大模型返回了空内容",
                "hint": "可能是模型把内容放到了 reasoning_content，或命中了内容安全策略；换一个模型试试。",
                "text": None, "latency_ms": ms, "meta": meta}
    return {"ok": True, "stage": "done", "code": "ok", "message": "调用成功",
            "hint": "", "text": text, "latency_ms": ms, "meta": meta}


def llm_chat(messages: list, max_tokens: int = 900, temperature: float = 0.3,
             cfg: dict = None) -> tuple:
    """调用 OpenAI 兼容的 /chat/completions。

    返回 (文本, 错误)；失败时文本为 None。错误里不含密钥。
    """
    r = llm_call(messages, max_tokens=max_tokens, temperature=temperature, cfg=cfg)
    if r["ok"]:
        return r["text"], None
    err = r["message"]
    if r.get("hint"):
        err += f"（{r['hint']}）"
    return None, err


def llm_test(cfg: dict = None) -> dict:
    """管理员点「测试连接」时用：先自检参数，再发一条极短消息验证。

    返回结构化结果，前端据此渲染「缺什么 / 哪里错了 / 该怎么改」。
    """
    cfg = cfg or ai_config()
    pf = preflight(cfg)
    out = {"ok": False, "stage": "preflight", "code": "incomplete",
           "message": "", "hint": "", "latency_ms": 0,
           "meta": {"base_url": (cfg.get("base_url") or "").strip(),
                    "model": (cfg.get("model") or "").strip(),
                    "proxy": proxy_of(cfg) or "（跟随系统 / 直连）",
                    "timeout": int(cfg.get("timeout") or 30)},
           "issues": pf["issues"], "provider": pf["provider"],
           "provider_label": pf["provider_label"], "provider_note": pf["provider_note"],
           "candidate_models": pf["candidate_models"], "engine": "local"}
    if pf["issues"]:
        first = pf["issues"][0]
        out["code"] = "incomplete_" + first["field"]
        out["message"] = first["label"]
        out["hint"] = first["hint"]
        return out
    r = llm_call([{"role": "user", "content": "回复两个字：正常"}],
                 max_tokens=16, cfg=cfg, probe=True)
    out.update({k: r.get(k) for k in ("ok", "stage", "code", "message", "hint",
                                      "latency_ms", "meta")})
    if r["ok"]:
        out["message"] = f"连接成功（{r['latency_ms']} ms）：" + (r["text"] or "")[:40]
        out["engine"] = "local+llm"
    return out


# ------------------------------------------------------------------ 2. 取数

STALE_HOURS = 72          # 超过该时长仍未审核视为积压
MAX_SAMPLES = 8           # 每类问题最多回传的样例条数


def _mask(v: str) -> str:
    """证件号脱敏：保留前 4 位与后 2 位。"""
    s = (v or "").strip()
    if len(s) <= 6:
        return s
    return s[:4] + "*" * (len(s) - 6) + s[-2:]


def _exam_map() -> dict:
    return {r["id"]: r for r in
            db.query("SELECT id,name,exam_type,status,signup_start_at,signup_end_at FROM exams")}


def _collect(scope) -> list:
    """按范围取全部报名行（带 _type 标记所属基础类型）。"""
    rows = []
    for t, tbl, clause, params in scope:
        for r in db.query(f"SELECT * FROM {tbl}{clause}", params):
            r["_type"] = t
            rows.append(r)
    return rows


def _sample(r: dict, exams: dict, note: str = "") -> dict:
    e = exams.get(r.get("exam_id")) or {}
    return {
        "app_type": r.get("_type", ""),
        "app_id": r.get("id"),
        "exam_name": e.get("name", ""),
        "name": r.get("name", ""),
        "id_number": _mask(r.get("id_number")),
        "note": note,
    }


def _level(count: int, total: int) -> str:
    if count <= 0:
        return "ok"
    return "danger" if count * 100 >= max(1, total) * 5 else "warn"


def _hours_ago(created: str) -> float:
    try:
        dt = datetime.strptime((created or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 0.0
    return (datetime.now() - dt).total_seconds() / 3600.0


# ------------------------------------------------------------------ 3. 体检

def _checks(rows: list, exams: dict) -> list:
    """数据体检：逐条扫描报名行，输出各类问题的数量与样例。"""
    total = len(rows)
    denom = max(1, total)

    # 1) 同一批次内证件号重复
    dup = {}
    for r in rows:
        no = (r.get("id_number") or "").strip().upper()
        if not no:
            continue
        dup.setdefault((r.get("exam_id"), no), []).append(r)
    dup_groups = [v for v in dup.values() if len(v) > 1]
    dup_count = sum(len(v) - 1 for v in dup_groups)

    # 2) 同一证件号对应不同姓名
    by_no = {}
    for r in rows:
        no = (r.get("id_number") or "").strip().upper()
        if not no:
            continue
        by_no.setdefault(no, {}).setdefault((r.get("name") or "").strip(), []).append(r)
    name_groups = [v for v in by_no.values() if len(v) > 1]

    # 3) 证件号不合法
    bad_id = []
    for r in rows:
        no = (r.get("id_number") or "").strip()
        if not no:
            continue
        try:
            validate_id_number(r.get("id_type") or "1", no, required=False)
        except Exception:
            bad_id.append(r)

    # 4) 手机号 / 5) 邮箱 / 6) 邮编
    bad_phone = [r for r in rows if (r.get("phone") or "").strip()
                 and not PHONE_RE.match((r.get("phone") or "").strip())]
    bad_email = [r for r in rows if (r.get("email") or "").strip()
                 and not EMAIL_RE.match((r.get("email") or "").strip())]
    bad_post = [r for r in rows if (r.get("postcode") or "").strip()
                and not POSTCODE_RE.match((r.get("postcode") or "").strip())]

    # 7) 必填字段缺失（按该批次所属考试类型的实际必填集合判定）
    miss_map = {}
    miss_rows = []
    for r in rows:
        e = exams.get(r.get("exam_id")) or {}
        code = e.get("exam_type") or r.get("_type") or "computer"
        base = ET.base_type_of(code)
        req = FIXED_REQUIRED[base] | set(ET.required_of(code))
        for f in ET.fields_of(code):
            if f in req and not str(r.get(f, "") or "").strip():
                miss_map.setdefault(f, []).append(r)
                miss_rows.append(r)
                break
    miss_count = len(miss_rows)
    miss_detail = "、".join(
        f"{ET.field_label(code_of(exams, r), f)} {len(v)} 条"
        for f, v in sorted(miss_map.items(), key=lambda x: -len(x[1]))[:5]) or ""

    # 8) 未上传证件照（仅针对已建账号的考生）
    photos = {u["id"]: (u.get("photo") or "") for u in
              db.query("SELECT id,photo FROM users")}
    no_photo = [r for r in rows if r.get("user_id") and r["user_id"] in photos
                and not photos[r["user_id"]]]

    # 9) 审核积压
    stale = [r for r in rows if r.get("audit_status") == "pending"
             and _hours_ago(r.get("created_at")) >= STALE_HOURS]

    # 10) 班级为空（会导致班主任 / 年级范围过滤时看不到这些记录）
    no_class = [r for r in rows if not (r.get("class_name") or "").strip()]

    # 11) 性别与身份证第 17 位不一致
    gender_bad = []
    for r in rows:
        if (r.get("id_type") or "1") != "1":
            continue
        no = (r.get("id_number") or "").strip().upper()
        g = (r.get("gender") or "").strip()
        if len(no) != 18 or g not in ("男", "女") or not no[:17].isdigit():
            continue
        if GENDER_OF_DIGIT[int(no[16]) % 2] != g:
            gender_bad.append(r)

    def mk(key, label, count, detail, advice, samples, level=None):
        return {"key": key, "label": label, "count": count, "detail": detail,
                "advice": advice, "samples": samples[:MAX_SAMPLES],
                "level": level or _level(count, denom)}

    return [
        mk("dup_id", "同批次证件号重复", dup_count,
           f"{len(dup_groups)} 组证件号在同一批次内出现多次",
           "核对是否同人重复提交或录入串号，重复的需退回或删除其中一条",
           [_sample(v[0], exams, f"重复 {len(v)} 次") for v in dup_groups]),
        mk("id_name_conflict", "同一证件号姓名不一致", len(name_groups),
           "同一证件号对应了多个不同姓名，通常是录入错误",
           "按证件号核对姓名，以身份证件为准修正后重新提交",
           [_sample(list(v.values())[0][0], exams,
                    "姓名：" + " / ".join(list(v.keys())[:3])) for v in name_groups]),
        mk("bad_id", "证件号码不合法", len(bad_id),
           "身份证号未通过 GB 11643 校验（省份代码 / 出生日期 / 校验位），"
           "或港澳台证件号不在 6-20 位字母数字范围",
           "导出错误明细通知考生核对；证件类型选错的要一并更正",
           [_sample(r, exams, (r.get("id_number") or "")[:20]) for r in bad_id]),
        mk("bad_phone", "手机号码格式异常", len(bad_phone),
           "手机号码不是 11 位且不以 13-19 开头",
           "联系考生修正，否则无法接收考试与审核通知",
           [_sample(r, exams, "手机号：" + (r.get("phone") or "")) for r in bad_phone]),
        mk("bad_email", "邮箱格式异常", len(bad_email),
           "已填写邮箱但不符合基本格式",
           "提醒考生修正邮箱，避免收不到通知",
           [_sample(r, exams, "邮箱：" + (r.get("email") or "")) for r in bad_email]),
        mk("bad_postcode", "邮政编码异常", len(bad_post),
           "邮政编码不是 6 位数字（普通话类）",
           "邮寄证书会失败，需提醒考生核对",
           [_sample(r, exams, "邮编：" + (r.get("postcode") or "")) for r in bad_post]),
        mk("missing_required", "必填字段缺失", miss_count,
           miss_detail or "存在必填项为空的报名记录",
           "联系考生补全；若是导入数据，检查 Excel 对应列是否漏填",
           [_sample(r, exams) for r in miss_rows],
           level=_level(miss_count, denom)),
        mk("no_photo", "未上传证件照", len(no_photo),
           "考生账号尚未上传证件照",
           "通知考生在个人中心上传，或由管理员批量导入照片",
           [_sample(r, exams) for r in no_photo],
           level="warn" if no_photo else "ok"),
        mk("stale_pending", f"审核积压（超过 {STALE_HOURS} 小时）", len(stale),
           f"{len(stale)} 条报名提交后 {STALE_HOURS} 小时仍未被审核",
           "优先处理等待最久的批次，必要时增派审核人力",
           [_sample(r, exams, "已等待 %.0f 小时" % _hours_ago(r.get("created_at")))
            for r in stale]),
        mk("no_class", "班级信息为空", len(no_class),
           "报名记录未填写班级，按「本班级 / 本年级」过滤时会被漏掉",
           "补全班级信息，否则班主任在自己的数据范围内看不到这些考生",
           [_sample(r, exams) for r in no_class],
           level="warn" if no_class else "ok"),
        mk("gender_mismatch", "性别与证件号不一致", len(gender_bad),
           "身份证第 17 位奇偶性与填写的性别不符",
           "核对姓名、证件号是否张冠李戴",
           [_sample(r, exams, "填写性别：" + (r.get("gender") or "")) for r in gender_bad]),
    ]


def code_of(exams: dict, row: dict) -> str:
    """取报名行所属批次的考试类型编码。"""
    e = exams.get(row.get("exam_id")) or {}
    return e.get("exam_type") or row.get("_type") or "computer"


# ------------------------------------------------------------------ 4. 瓶颈 / 趋势

def _bottlenecks(scope, rows: list, exams: dict) -> dict:
    now = datetime.now()

    pending_by_exam = {}
    for r in rows:
        if r.get("audit_status") != "pending":
            continue
        node = pending_by_exam.setdefault(
            r.get("exam_id"), {"exam_id": r["exam_id"], "pending": 0, "oldest": None})
        node["pending"] += 1
        c = r.get("created_at") or ""
        if node["oldest"] is None or c < node["oldest"]:
            node["oldest"] = c
    pending_list = []
    for node in pending_by_exam.values():
        e = exams.get(node["exam_id"]) or {}
        node["exam_name"] = e.get("name", "")
        node["wait_hours"] = round(_hours_ago(node["oldest"] or ""), 1) if node["oldest"] else 0
        node.pop("oldest", None)
        pending_list.append(node)
    pending_list.sort(key=lambda x: -x["pending"])

    # 平均审核时长（提交 -> 出结果）
    done = [r for r in rows if r.get("reviewed_at") and r.get("created_at")]
    avg_hours = 0.0
    if done:
        avg_hours = max(0.0, round(
            sum(_hours_ago(r["created_at"]) - _hours_ago(r["reviewed_at"])
                for r in done) / len(done), 1))

    # 审核员工作量排行
    rank = {}
    for t, tbl, clause, params in scope:
        for r in db.query(
                "SELECT reviewer_name rn, COUNT(*) c FROM audits WHERE app_type=?"
                " AND app_id IN (SELECT id FROM " + tbl + clause + ")"
                " GROUP BY reviewer_name", (t,) + tuple(params)):
            n = (r["rn"] or "").strip() or "未知"
            rank[n] = rank.get(n, 0) + r["c"]
    reviewer_rank = [{"name": k, "value": v} for k, v in
                     sorted(rank.items(), key=lambda x: -x[1])[:8]]

    # 报名窗口临近截止且仍有待审
    closing = []
    for eid, e in exams.items():
        if e.get("status") != "open":
            continue
        try:
            end = datetime.strptime((e.get("signup_end_at") or "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        left = (end - now).total_seconds() / 86400.0
        if left < 0 or left > 7:
            continue
        p = next((x for x in pending_list if x["exam_id"] == eid), None)
        closing.append({"exam_id": eid, "exam_name": e.get("name", ""),
                        "days_left": round(left, 1), "pending": (p or {}).get("pending", 0)})
    closing.sort(key=lambda x: x["days_left"])

    return {
        "pending_by_exam": pending_list[:8],
        "stale_pending": sum(1 for r in rows if r.get("audit_status") == "pending"
                             and _hours_ago(r.get("created_at")) >= STALE_HOURS),
        "avg_review_hours": avg_hours,
        "reviewed_count": len(done),
        "reviewer_rank": reviewer_rank,
        "closing_soon": closing,
    }


def _trend(rows: list) -> dict:
    days = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    dmap = {d: 0 for d in days}
    mmap = {}
    for r in rows:
        d = (r.get("created_at") or "")[:10]
        if d in dmap:
            dmap[d] += 1
        m = (r.get("created_at") or "")[:7]
        if m:
            mmap[m] = mmap.get(m, 0) + 1
    return {
        "days": [{"date": d, "count": dmap[d]} for d in days],
        "months": [{"month": k, "count": v} for k, v in sorted(mmap.items())],
    }


# ------------------------------------------------------------------ 5. 建议

def _suggestions(ov: dict, checks: list, bn: dict, exams: dict, user) -> list:
    out = []
    total = ov.get("total", 0)
    pending = ov.get("pending", 0)

    if total == 0:
        out.append({"title": "当前范围内还没有报名数据", "level": "info",
                    "detail": "先在「考试批次管理」里发布一个报名批次，或导入历史报名数据后再来分析。",
                    "action": {"label": "去建批次", "path": "/exams"}})
        return out

    if bn.get("stale_pending"):
        out.append({"title": f"优先清理 {bn['stale_pending']} 条积压报名", "level": "danger",
                    "detail": f"有 {bn['stale_pending']} 条报名等待审核已超过 {STALE_HOURS} 小时，"
                              "容易造成考生错过报名窗口。",
                    "action": {"label": "去审核", "path": "/review"}})
    elif pending and pending * 100 >= max(1, total) * 30:
        out.append({"title": f"待审核占比偏高（{ov.get('pending_rate', 0)}%）", "level": "warn",
                    "detail": f"共 {total} 条报名，其中 {pending} 条仍未审核，建议集中时段批量处理。",
                    "action": {"label": "去审核", "path": "/review"}})

    for c in checks:
        if c["level"] == "danger" and c["key"] in ("dup_id", "bad_id", "id_name_conflict"):
            out.append({"title": f"核查「{c['label']}」{c['count']} 处", "level": "danger",
                        "detail": c["advice"], "action": {"label": "查看名单", "path": "/review"}})
        elif c["level"] != "ok" and c["key"] in ("bad_phone", "bad_email", "bad_postcode",
                                                 "missing_required"):
            out.append({"title": f"修正「{c['label']}」{c['count']} 处", "level": "warn",
                        "detail": c["advice"], "action": {"label": "查看名单", "path": "/review"}})

    for e in bn.get("closing_soon", [])[:2]:
        if e["pending"]:
            out.append({"title": f"《{e['exam_name']}》{e['days_left']} 天后截止", "level": "warn",
                        "detail": f"该批次仍有 {e['pending']} 条待审核，截止前需处理完，"
                                  "否则考生无法按时完成报名。",
                        "action": {"label": "去审核", "path": "/review"}})

    if ov.get("reject_rate", 0) >= 20 and ov.get("reviewed"):
        out.append({"title": f"驳回率偏高（{ov['reject_rate']}%）", "level": "warn",
                    "detail": "审核驳回比例较高，说明考生填报时对要求理解不足，"
                              "建议在批次说明里补充填写规范，从源头降低驳回量。",
                    "action": None})

    if bn.get("avg_review_hours", 0) >= 48:
        out.append({"title": f"平均审核耗时 {bn['avg_review_hours']} 小时", "level": "warn",
                    "detail": "从提交到出结果平均超过两天，可考虑增加审核人员或改为分班级并行审核。",
                    "action": None})

    if not out:
        out.append({"title": "当前数据质量良好", "level": "ok",
                    "detail": f"{total} 条报名未发现明显异常，审核进度正常，保持当前节奏即可。",
                    "action": None})
    return out[:8]


def _summary_text(ov: dict, checks: list, bn: dict, total: int, exams: dict) -> str:
    if total == 0:
        return "当前数据范围内还没有报名记录，无法给出分析结论。请先发布考试批次并导入或采集报名数据。"
    issues = [c for c in checks if c["count"] > 0]
    n_issue = sum(c["count"] for c in issues)
    top = max(issues, key=lambda c: c["count"]) if issues else None
    parts = [
        f"当前数据范围内共 {total} 条报名记录，覆盖 {ov.get('exam_count', 0)} 个考试批次。",
        f"已审核 {ov.get('reviewed', 0)} 条，通过率 {ov.get('pass_rate', 0)}%，"
        f"驳回率 {ov.get('reject_rate', 0)}%；仍有 {ov.get('pending', 0)} 条待审核"
        f"（占 {ov.get('pending_rate', 0)}%）。",
    ]
    if n_issue:
        parts.append(f"数据体检发现 {len(issues)} 类问题共 {n_issue} 处"
                     + (f"，其中「{top['label']}」{top['count']} 处最为突出。" if top else "。"))
    else:
        parts.append("数据体检未发现明显问题，完整性良好。")
    if bn.get("stale_pending"):
        parts.append(f"审核侧有 {bn['stale_pending']} 条等待超过 {STALE_HOURS} 小时，建议优先处理。")
    elif bn.get("closing_soon"):
        e = bn["closing_soon"][0]
        parts.append(f"《{e['exam_name']}》将在 {e['days_left']} 天后截止报名。")
    return "".join(parts)


# ------------------------------------------------------------------ 6. 智能分析

def analyze(user, exam_id: int = 0, exam_type: str = "", with_llm: bool = True) -> dict:
    """对当前账号可见范围内的报名数据做智能分析。"""
    scope = build_scope(user, exam_id, exam_type)
    ov = summary_of(scope)
    rows = _collect(scope)
    exams = _exam_map()

    ov["exam_count"] = len({r.get("exam_id") for r in rows})

    checks = _checks(rows, exams)
    anomalies = sorted([c for c in checks if c["count"] > 0],
                       key=lambda c: -c["count"])
    bn = _bottlenecks(scope, rows, exams)
    trend = _trend(rows)

    penalty = 0
    for c in checks:
        if c["count"]:
            penalty += min(20, round(c["count"] * 100 / max(1, len(rows))))
    score = max(0, 100 - penalty)
    health = {
        "score": score,
        "level": "good" if score >= 90 else ("fair" if score >= 70 else "poor"),
        "checks": checks,
    }

    result = {
        "engine": "local",
        "scope_label": scope_label(user),
        "generated_at": db.now_str(),
        "overview": ov,
        "summary": _summary_text(ov, checks, bn, len(rows), exams),
        "health": health,
        "anomalies": anomalies,
        "bottlenecks": bn,
        "trend": trend,
        "suggestions": _suggestions(ov, checks, bn, exams, user),
        "llm": {"enabled": False, "text": None, "error": None, "hint": ""},
    }

    cfg = ai_config()
    if with_llm and llm_ready(cfg):
        result["engine"] = "local+llm"
        result["llm"]["enabled"] = True
        r = _llm_analyze(result, cfg)
        result["llm"]["text"] = r.get("text")
        # ⚠ 成功时 error 必须保持 None（该字段初值就是 None，调用方按 `is None` 判断）：
        #   llm_call() 成功也会带一句「调用成功」的 message，直接塞进来会被当成失败
        result["llm"]["error"] = None if r.get("ok") else (r.get("message") or "")
        result["llm"]["hint"] = r.get("hint") or ""
        result["llm"]["code"] = r.get("code") or ""
    return result


def _llm_analyze(result: dict, cfg: dict) -> tuple:
    ov = result["overview"]
    facts = {
        "数据范围": result["scope_label"],
        "报名总数": ov.get("total", 0),
        "待审核": ov.get("pending", 0),
        "已通过": ov.get("approved", 0),
        "已驳回": ov.get("rejected", 0),
        "已退回": ov.get("returned", 0),
        "通过率(%)": ov.get("pass_rate", 0),
        "批次数量": ov.get("exam_count", 0),
        "数据健康分": result["health"]["score"],
        "问题清单": [{"问题": c["label"], "数量": c["count"]}
                     for c in result["health"]["checks"] if c["count"]],
        "审核瓶颈": {"积压超期": result["bottlenecks"].get("stale_pending", 0),
                     "平均审核小时": result["bottlenecks"].get("avg_review_hours", 0),
                     "待审最多的批次": [x["exam_name"] for x in
                                        result["bottlenecks"].get("pending_by_exam", [])[:3]]},
    }
    sys_p = ("你是「考试报名信息采集与审核管理系统」内置的数据分析助手。"
             "只能依据下面给出的事实数据用中文回答，不得编造或推测未给出的数字。"
             "请输出 3-5 条结论与可执行建议，每条不超过 60 字，用「- 」开头。")
    extra = (cfg.get("system_prompt") or "").strip()
    if extra:
        sys_p += "\n" + extra
    messages = [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": json.dumps(facts, ensure_ascii=False)},
    ]
    return llm_call(messages, max_tokens=900, cfg=cfg)


# ------------------------------------------------------------------ 7. 智能客服

# 关键词 -> 意图。命中越多得分越高；同分时按表内顺序取先出现的。
INTENT_RULES = [
    ("mine", ["我的报名", "我报的名", "报名状态", "我的状态", "我提交了", "报上了吗",
              "报名成功", "我的审核", "我的申请", "我报名了"]),
    ("pending", ["待审核", "待审", "还有多少", "多少条没审", "未审核", "审核积压",
                 "积压多少", "审完了吗"]),
    ("overview", ["报名多少", "多少人报名", "汇总", "总量", "报名总数", "数据概览",
                  "多少条报名"]),
    ("health", ["异常", "有问题", "体检", "错误数据", "数据质量", "哪里有问题", "错填"]),
    ("open_exam", ["开放", "在报", "哪个批次", "批次", "还能报吗", "截止时间", "报名截止"]),
    ("perm", ["我的权限", "权限", "角色", "数据范围", "看不到", "为什么看不到", "能看多少"]),
    ("apply", ["怎么报名", "如何报名", "报名流程", "填写", "报名表", "提交报名", "报考"]),
    ("audit", ["怎么审核", "审核流程", "驳回", "退回", "通过审核", "审核标准", "审核意见"]),
    ("import", ["导入", "excel", "模板", "批量", "上传文件", "批量导入"]),
    ("export", ["导出", "下载", "汇总表", "导出 excel", "导出报表"]),
    ("photo", ["照片", "证件照", "上传照片", "照片格式", "照片太大"]),
    ("account", ["登录", "密码", "注册", "验证码", "账号", "忘记密码", "改密码",
                 "个人资料"]),
    ("exam_type", ["考试类型", "新增类型", "自定义类型", "计算机类", "普通话"]),
    ("system", ["版本", "关于", "系统", "什么软件", "帮助", "能做什么"]),
]


def intent_of(text: str):
    """关键词打分识别意图，返回 (意图, 得分)。"""
    t = (text or "").strip().lower()
    best, score = "", 0
    for intent, words in INTENT_RULES:
        s = sum(1 for w in words if w in t)
        if s > score:
            best, score = intent, s
    return best, score


KNOWLEDGE = [
    {"key": "apply", "title": "报名流程", "keywords": ["怎么报名", "如何报名", "报名流程", "报考"],
     "answer": "报名分三步：① 首页「开放报名批次」里点开一个开放中的批次 → 进入报名表；"
               "② 按表单如实填写（计算机类 13 个字段、普通话类 20 个字段，带 * 为必填，"
               "具体字段以管理员在「考试类型管理」里的配置为准）；"
               "③ 提交后等待审核，状态可在「我的报名」里查看。每个批次每人只能提交一次，"
               "提交后如需修改，要等审核员「退回」后才能编辑。"},
    {"key": "audit_status", "title": "审核状态含义", "keywords": ["审核状态", "待审核", "已通过",
                                                                 "已驳回", "已退回", "状态代表"],
     "answer": "报名有四种状态：待审核（已提交，等待审核员处理）、已通过（审核通过，报名成功）、"
               "已驳回（信息不符合要求且不可修改，需重新报名）、已退回（信息需要修正，"
               "修改后可再次提交）。被退回时请查看审核意见里的具体原因。"},
    {"key": "audit", "title": "审核操作", "keywords": ["怎么审核", "审核流程", "如何审核"],
     "answer": "有「信息审核」权限的账号在「报名信息审核」页按条件筛选（批次 / 状态 / 关键字），"
               "点开一条核对信息后选择通过、驳回或退回，驳回与退回需填写意见，"
               "意见会展示给考生。只能审核自己数据范围内的报名。"},
    {"key": "import", "title": "批量导入", "keywords": ["导入", "excel", "模板", "批量"],
     "answer": "「批量导入」页先选择考试批次，下载该批次的导入模板（表头已按官方模板生成，"
               "不要再改动列顺序），填好后上传。系统会先出预览并自动识别列与字段的对应关系，"
               "确认无误再执行导入。导入结果会给出成功 / 失败条数，失败的可下载错误明细逐条查看原因。"
               "班主任导入时，行内班级必须落在自己管理的班级范围内。"},
    {"key": "export", "title": "汇总导出", "keywords": ["导出", "下载", "汇总表"],
     "answer": "「汇总导出」页可按考试批次、审核状态筛选后导出 Excel，表头与该类型对应的官方模板一致，"
               "可直接上报。导出结果受数据范围限制：全校 / 本院系 / 本年级 / 本班级，"
               "看不到的数据也导不出来。"},
    {"key": "scope", "title": "数据范围与权限", "keywords": ["数据范围", "看不到", "为什么看不到",
                                                            "权限", "角色"],
     "answer": "系统采用「角色 → 权限组 → 数据范围」三层模型。五种角色：考生、班主任、"
               "二级学院审核、审核员、管理员。数据范围四档：本班级（班主任，可管多个班）、"
               "本年级、本院系（二级学院审核）、全校（审核员 / 管理员）。"
               "如果看不到某条数据，通常是它的班级或院系为空，或者不在你的数据范围内——"
               "可联系管理员在「用户与权限管理」里核对范围设置。"},
    {"key": "photo", "title": "证件照", "keywords": ["照片", "证件照", "上传照片"],
     "answer": "考生在「个人中心」上传证件照，支持 JPG / PNG，单张不超过 5MB。"
               "管理员可在「用户与权限管理」里批量导入照片，按证件号 → 用户名 / 学号 → "
               "手机号 → 姓名顺序匹配，命中多人时会拒绝覆盖以免张冠李戴。"
               "需要整包照片时可用「证件照一键导出」，按 考试 / 院系 / 班级 / 证件号 三级目录打包。"},
    {"key": "account", "title": "账号与登录", "keywords": ["登录", "密码", "注册", "验证码",
                                                          "忘记密码"],
     "answer": "登录支持用户名 / 手机号 / 邮箱。忘记密码可在登录页用手机号或邮箱获取验证码重置；"
               "验证码通道由管理员在服务端配置，未配置时验证码只打印在服务端控制台。"
               "考生账号可由管理员在后台开通，也可以自行注册。初始管理员账号见《使用说明》。"},
    {"key": "exam_type", "title": "考试类型", "keywords": ["考试类型", "自定义类型", "新增类型"],
     "answer": "内置两套基础模板：计算机类考试（13 个字段）与普通话水平测试（20 个字段）。"
               "管理员可在「考试类型管理」里基于任一模板新增类型（如英语四六级），"
               "自定义编码、名称，并可关闭不需要的字段、改字段显示名、追加必填项——"
               "报名表单、导入模板、导出表头、审核详情会同步生效，系统必填项不可取消。"},
    {"key": "exam_status", "title": "批次状态流转", "keywords": ["批次状态", "开放", "关闭",
                                                                "草稿", "归档"],
     "answer": "考试批次状态单向流转：草稿 → 开放 → 已关闭 → 已归档，不可回退。"
               "只有草稿状态可以删除；开放状态到达报名截止时间后系统会每分钟自动检查并关闭。"},
    {"key": "system", "title": "系统简介", "keywords": ["版本", "关于", "能做什么", "帮助"],
     "answer": "本系统是「考试报名信息采集与审核管理系统」，覆盖考试批次管理、考试类型管理、"
               "在线报名、批量导入、信息审核、汇总导出、数据分析、数据看板大屏、"
               "用户与权限管理、系统维护，并内置 AI 智能分析与 AI 客服。"
               "单机 exe 部署，数据存在安装目录下的 data 目录。"},
]


def _kb_search(text: str):
    """知识库关键词检索，返回命中的条目。"""
    t = (text or "").strip().lower()
    best, score = None, 0
    for item in KNOWLEDGE:
        s = sum(1 for k in item["keywords"] if k in t)
        if s > score:
            best, score = item, s
    return best


def _my_applications(user) -> list:
    out = []
    for t, tbl in (("computer", "applications_computer"),
                   ("mandarin", "applications_mandarin"),
                   ("generic", "applications_generic")):
        for r in db.query(
                f"SELECT id,exam_id,name,audit_status,audit_comment,created_at FROM {tbl}"
                f" WHERE user_id=? ORDER BY id DESC LIMIT 20", (user["id"],)):
            e = db.query_one("SELECT name FROM exams WHERE id=?", (r["exam_id"],))
            out.append({"app_type": t, "app_id": r["id"], "name": r["name"],
                        "exam_name": (e or {}).get("name", ""),
                        "status": r["audit_status"],
                        "status_label": AUDIT_LABEL.get(r["audit_status"], r["audit_status"]),
                        "comment": r["audit_comment"] or "", "created_at": r["created_at"]})
    return out


def _pending_overview(user) -> dict:
    scope = build_scope(user)
    rows = _collect(scope)
    exams = _exam_map()
    pend = [r for r in rows if r.get("audit_status") == "pending"]
    by_exam = {}
    for r in pend:
        node = by_exam.setdefault(r["exam_id"], {"exam_name": "", "pending": 0})
        node["pending"] += 1
        node["exam_name"] = (exams.get(r["exam_id"]) or {}).get("name", "")
    return {
        "total": len(rows), "pending": len(pend),
        "by_exam": sorted(by_exam.values(), key=lambda x: -x["pending"])[:6],
        "scope_label": scope_label(user),
    }


def _open_exams() -> list:
    now = db.now_str()
    return [{"id": r["id"], "name": r["name"],
             "type_label": ET.label_of(r["exam_type"]),
             "end_at": r["signup_end_at"]}
            for r in db.query("SELECT id,name,exam_type,signup_end_at FROM exams"
                              " WHERE status='open' AND signup_start_at<=? AND signup_end_at>=?"
                              " ORDER BY signup_end_at", (now, now))]


def _answer_data(user, intent: str, text: str):
    """实时数据类问答；返回 (回复文本, 结构化数据)。无权限时明确说明。"""
    if intent == "mine":
        if not has_perm(user, "apply"):
            return "你的账号没有「在线报名」权限，因此没有报名记录。如需报名请联系管理员开通。", None
        rows = _my_applications(user)
        if not rows:
            return "你目前还没有提交过任何报名。可在首页「开放报名批次」里选择一个开放中的批次开始报名。", []
        lines = [f"你共有 {len(rows)} 条报名记录："]
        for r in rows:
            tail = f"，意见：{r['comment']}" if r["comment"] else ""
            lines.append(f"· 《{r['exam_name']}》— {r['status_label']}{tail}")
        return "\n".join(lines), rows

    if intent == "pending":
        if not (has_perm(user, "audit") or has_perm(user, "import")):
            return "查看待审核数量需要「信息审核」或「批量导入」权限，你的账号未开通，请联系管理员。", None
        d = _pending_overview(user)
        if not d["pending"]:
            return f"在你可见的数据范围（{d['scope_label']}）内，没有待审核的报名，全部已处理完。", d
        lines = [f"你可见范围（{d['scope_label']}）内共 {d['total']} 条报名，"
                 f"其中 {d['pending']} 条待审核："]
        for x in d["by_exam"]:
            lines.append(f"· 《{x['exam_name']}》{x['pending']} 条")
        lines.append("建议优先处理等待时间最久的批次。")
        return "\n".join(lines), d

    if intent in ("overview", "health"):
        if not has_perm(user, "analysis"):
            return ("智能数据分析需要「数据分析」权限，你的账号未开通。"
                    "如需开通请联系管理员在「用户与权限管理」里单独授权。"), None
        r = analyze(user, with_llm=False)
        if intent == "overview":
            ov = r["overview"]
            return (f"你可见范围内共 {ov['total']} 条报名，覆盖 {ov.get('exam_count', 0)} 个批次；"
                    f"待审核 {ov['pending']} 条、已通过 {ov['approved']} 条、"
                    f"已驳回 {ov['rejected']} 条、已退回 {ov['returned']} 条，"
                    f"通过率 {ov['pass_rate']}%。今日新增 {ov['today']} 条。"), ov
        if not r["anomalies"]:
            return f"数据体检通过：{r['overview']['total']} 条报名未发现明显异常，健康分 {r['health']['score']}。", r["health"]
        lines = [f"体检发现 {len(r['anomalies'])} 类问题（健康分 {r['health']['score']}）："]
        for a in r["anomalies"][:6]:
            lines.append(f"· {a['label']}：{a['count']} 处 —— {a['advice']}")
        return "\n".join(lines), r["anomalies"]

    if intent == "open_exam":
        rows = _open_exams()
        if not rows:
            return "当前没有处于报名期内的考试批次。可在首页查看全部批次，或联系管理员发布新批次。", []
        lines = [f"当前有 {len(rows)} 个批次正在报名："]
        for r in rows:
            lines.append(f"· 《{r['name']}》（{r['type_label']}）截止 {r['end_at']}")
        return "\n".join(lines), rows

    if intent == "perm":
        d = describe(user)
        # 数据范围不是「功能」，单独说，避免「可用功能：…、数据范围：全校」这种读法
        funcs = [lbl for p, lbl in zip(d["perms"], d["perm_labels"])
                 if not str(p).startswith("scope_")]
        return (f"你的身份是【{d['role_label']}】，数据范围：{d['scope_label']}。"
                f"可用功能：" + "、".join(funcs or ["无"]) + "。"
                "看不到某些数据时，先确认它是否落在你的数据范围内。"), d
    return "", None


def _llm_answer(user, text: str, history: list = None, cfg: dict = None):
    d = describe(user)
    sys_p = ("你是「考试报名信息采集与审核管理系统」里的智能客服，只回答与本系统相关的问题，"
             "用简洁中文作答（不超过 200 字）。\n"
             f"当前用户身份：{d['role_label']}，数据范围：{d['scope_label']}，"
             f"可用功能：" + "、".join(d["perm_labels"] or ["无"]) + "。\n"
             "不得编造系统不存在的功能；涉及实时数据的问题请让用户到对应页面查看。")
    extra = (cfg or ai_config()).get("system_prompt") or ""
    if extra.strip():
        sys_p += "\n" + extra.strip()
    messages = [{"role": "system", "content": sys_p}]
    for h in (history or [])[-6:]:
        role = (h or {}).get("role")
        content = (h or {}).get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": str(content)[:500]})
    messages.append({"role": "user", "content": text[:500]})
    return llm_call(messages, max_tokens=600, cfg=cfg)


FALLBACK = ("这个问题我还没学会。你可以试试问我：怎么报名、审核状态的含义、批量导入模板在哪里下载、"
            "怎么导出汇总表、我的报名状态、现在有多少条待审核、权限和数据范围怎么看。"
            "也可以直接到对应功能页操作。")


def chat(user, message: str, history: list = None, with_llm: bool = True) -> dict:
    """智能客服：意图识别 → 实时数据 / 知识库 →（可选）大模型兜底。"""
    text = (message or "").strip()
    if not text:
        return {"reply": "请输入你想问的问题。", "intent": "", "data": None,
                "engine": "local", "suggestions": quick_questions(user)}
    if len(text) > 300:
        text = text[:300]

    intent, _score = intent_of(text)
    reply, data = _answer_data(user, intent, text)
    engine = "local"

    if not reply:
        hit = _kb_search(text)
        if hit:
            reply = hit["answer"]

    cfg = ai_config()
    llm = {"enabled": bool(with_llm and llm_ready(cfg)), "used": False,
           "error": "", "hint": "", "code": ""}
    if with_llm and not llm["enabled"]:
        pf = preflight(cfg)
        llm["error"] = (pf["issues"][0]["label"] if pf["issues"] else "未启用大模型")
        llm["hint"] = (pf["issues"][0]["hint"] if pf["issues"]
                       else "本问题已由本地规则引擎回答，功能不受影响。")

    if not reply and with_llm and llm["enabled"]:
        r = _llm_answer(user, text, history, cfg)
        if r.get("ok"):
            reply, engine = r["text"], "llm"
            llm["used"] = True
        else:
            llm["error"] = r.get("message") or ""
            llm["hint"] = r.get("hint") or ""
            llm["code"] = r.get("code") or ""

    if not reply:
        reply = FALLBACK
        intent = intent or "unknown"

    return {"reply": reply, "intent": intent, "data": data, "engine": engine,
            "llm": llm, "suggestions": quick_questions(user)[:6]}


def quick_questions(user) -> list:
    """按角色生成推荐提问（前端渲染成快捷按钮）。"""
    qs = ["怎么报名？", "审核状态都代表什么？", "忘记密码怎么办？"]
    if has_perm(user, "apply"):
        qs.insert(1, "我的报名状态是什么？")
    if has_perm(user, "audit") or has_perm(user, "import"):
        qs.append("现在有多少条待审核？")
    if has_perm(user, "import"):
        qs.append("批量导入的模板在哪里下载？")
    if has_perm(user, "export"):
        qs.append("怎么导出汇总表？")
    if has_perm(user, "analysis"):
        qs += ["帮我看看当前数据有什么异常", "待审核是不是积压了？"]
    if has_perm(user, "user_manage"):
        qs.append("权限和数据范围怎么设置？")
    return qs
