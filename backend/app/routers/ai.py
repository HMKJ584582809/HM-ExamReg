# -*- coding: utf-8 -*-
"""模块10：AI 智能分析与 AI 客服。

权限
----
- ``GET/PUT /api/ai/config``、``POST /api/ai/test``：用户与权限管理（管理员）
- ``POST /api/ai/analyze``                        ：数据分析
- ``POST /api/ai/chat``、``GET /api/ai/suggestions``：任意已登录账号（客服）

安全约定
--------
- 密钥只保存、不读出：GET 一律返回掩码，前端回传掩码表示「不修改」。
- 分析与客服取数全部走 ai_engine（内部复用 build_scope + scope_filter），
  AI 不会比当前账号看到更多数据；无权限的数据意图会明确说明原因而不是沉默。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import ai_engine as AI
from ..deps import ApiError, get_current_user, ok
from ..permissions import require_perms

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AiConfigIn(BaseModel):
    enable: int | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    timeout: int | None = None
    system_prompt: str | None = None
    proxy: str | None = None


class AnalyzeIn(BaseModel):
    exam_id: int = 0
    exam_type: str = ""
    with_llm: bool = True


class ChatIn(BaseModel):
    message: str = ""
    history: list | None = None
    with_llm: bool = True


class TestIn(BaseModel):
    """测试连接时可先传一份「待验证配置」，不必先保存。

    base_url / model / proxy / timeout 为空表示沿用已保存的值；
    api_key 为空表示沿用已保存的密钥（掩码与空串都表示不修改）。
    """
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    proxy: str | None = None
    timeout: int | None = None


def _public():
    cfg = AI.public_ai_config()
    ready = AI.llm_ready()
    return {
        "config": cfg,
        "providers": AI.public_providers(),
        "ready": ready,
        "engine": "local+llm" if ready else "local",
        "engine_label": "本地规则引擎 + 外部大模型" if ready else "本地规则引擎（无需配置）",
    }


@router.get("/providers")
def providers(user=Depends(require_perms("user_manage"))):
    """预设厂商列表（OpenAI / DeepSeek / 豆包 / 通义 / 智谱 等）。"""
    return ok({"providers": AI.public_providers()})


@router.get("/config")
def get_config(user=Depends(require_perms("user_manage"))):
    """读取 AI 配置（密钥只回是否配置）。"""
    return ok(_public())


@router.put("/config")
def put_config(body: AiConfigIn, user=Depends(require_perms("user_manage"))):
    """保存 AI 配置；未提交的字段保持原值。"""
    patch = body.model_dump(exclude_unset=True)
    base = (patch.get("base_url") or "").strip()
    if base and not base.lower().startswith(("http://", "https://")):
        raise ApiError("服务地址需以 http:// 或 https:// 开头")
    if "timeout" in patch:
        try:
            patch["timeout"] = int(patch["timeout"])
        except (TypeError, ValueError):
            raise ApiError("超时时间需为 5-120 之间的整数")
        if not 5 <= patch["timeout"] <= 120:
            raise ApiError("超时时间需为 5-120 之间的整数")
    if "proxy" in patch:
        # 空 / auto = 跟随系统；none = 强制直连；其余必须是 http(s)://host:port
        p = (patch["proxy"] or "").strip()
        low = p.lower()
        if p and low not in AI.PROXY_SYSTEM and low not in AI.PROXY_DIRECT:
            # 只支持 http/https 代理：urllib 原生不带 socks 支持，
            # 放行 socks5:// 会让人以为配好了、实际连不通
            if not low.startswith(("http://", "https://")):
                raise ApiError("代理地址需以 http:// 或 https:// 开头"
                               "（本机 Clash / V2Ray 一般填 http://127.0.0.1:7890）")
        patch["proxy"] = p

    cfg = AI.save_ai_config(patch)
    if int(cfg.get("enable") or 0) == 1 and not AI.llm_ready(cfg):
        raise ApiError("启用大模型前，请先完整填写服务地址、密钥与模型名称")
    return ok(_public(), "AI 配置已保存")


@router.post("/test")
def test(body: TestIn | None = None, user=Depends(require_perms("user_manage"))):
    """测试大模型连通性（用一条极短请求，不消耗额度）。

    先做参数自检，缺哪个参数就直接告诉管理员补哪个；再按 HTTP 状态/
    网络异常给出「发生了什么 + 该怎么做」。可传入一份未保存的配置先行验证。
    """
    cfg = dict(AI.ai_config())
    cfg["enable"] = 1                       # 测试时按「已启用」处理，好让自检走到联网
    if body is not None:
        patch = body.model_dump(exclude_unset=True)
        for k in ("base_url", "model", "proxy", "timeout"):
            if k in patch and patch[k] not in (None, ""):
                cfg[k] = patch[k]
        if "api_key" in patch:
            v = (patch["api_key"] or "").strip()
            # 掩码 = 沿用已保存的密钥；__clear__ = 按未配置处理
            if v and v != AI.AI_KEY_MASK:
                cfg["api_key"] = "" if v == AI.AI_KEY_CLEAR else v
    try:
        cfg["timeout"] = int(cfg.get("timeout") or 30)
    except (TypeError, ValueError):
        raise ApiError("超时时间需为 5-120 之间的整数")
    if cfg.get("base_url") and not str(cfg["base_url"]).lower().startswith(("http://", "https://")):
        raise ApiError("服务地址需以 http:// 或 https:// 开头")
    return ok(AI.llm_test(cfg))


@router.get("/suggestions")
def suggestions(user=Depends(require_perms("ai"))):
    """按角色返回推荐提问（前端渲染成快捷按钮）。"""
    return ok({"questions": AI.quick_questions(user)})


@router.post("/analyze")
def analyze(body: AnalyzeIn, user=Depends(require_perms("analysis"))):
    """智能分析：数据概览 + 完整性体检 + 异常预警 + 审核瓶颈 + 建议。"""
    return ok(AI.analyze(user, exam_id=body.exam_id or 0,
                         exam_type=(body.exam_type or "").strip(),
                         with_llm=bool(body.with_llm)))


@router.post("/chat")
def chat(body: ChatIn, user=Depends(require_perms("ai"))):
    """AI 客服：意图识别 → 实时数据 / 知识库 →（可选）大模型兜底。"""
    msg = (body.message or "").strip()
    if not msg:
        raise ApiError("请输入你想问的问题")
    return ok(AI.chat(user, msg, body.history, with_llm=bool(body.with_llm)))
