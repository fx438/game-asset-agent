"""百炼大模型调用（OpenAI 兼容模式）：qwen-plus 做文本推理，qwen-vl 做看图审查。

两个函数都要求模型输出 JSON，并在解析失败时把错误信息回传给模型重试一次。
"""
import base64
import json
import logging
from functools import lru_cache
from typing import Any

from app.ai.errors import AIError, TransientAIError
from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _client():
    from openai import OpenAI

    s = get_settings()
    return OpenAI(api_key=s.dashscope_api_key, base_url=f"{s.dashscope_base_url.rstrip('/')}/compatible-mode/v1", timeout=90, max_retries=2)


def _call(model: str, messages: list[dict], temperature: float, json_mode: bool) -> str:
    import openai

    # JSON Mode 只对文本模型开启；视觉模型靠提示词约束 + 宽松解析
    extra = {"response_format": {"type": "json_object"}} if json_mode else {}
    try:
        resp = _client().chat.completions.create(model=model, messages=messages, temperature=temperature, **extra)
    except (openai.RateLimitError, openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as exc:
        raise TransientAIError(f"{model} 临时不可用: {exc}") from exc
    except openai.APIError as exc:
        raise AIError(f"{model} 调用失败: {exc}") from exc
    return resp.choices[0].message.content or ""


def _parse_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("输出中没有 JSON 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, dict):
        raise ValueError("顶层必须是 JSON 对象")
    return data


def _chat_json(model: str, messages: list[dict], temperature: float, json_mode: bool = True) -> dict[str, Any]:
    raw = _call(model, messages, temperature, json_mode)
    try:
        return _parse_json(raw)
    except ValueError as exc:  # json.JSONDecodeError 是 ValueError 的子类
        logger.warning("模型 %s 输出非法 JSON，带错误信息重试一次: %s", model, exc)
        retry = messages + [
            {"role": "assistant", "content": raw},
            {"role": "user", "content": f"上面的输出不是合法 JSON（{exc}）。请只输出符合要求的 JSON 对象，不要任何解释。"},
        ]
        try:
            return _parse_json(_call(model, retry, temperature, json_mode))
        except ValueError as exc2:
            raise AIError(f"模型连续两次输出非法 JSON: {exc2}") from exc2


def chat_json(system: str, user: str, temperature: float = 0.3) -> dict[str, Any]:
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return _chat_json(get_settings().llm_model, messages, temperature)


def _data_url(image: bytes) -> str:
    mime = "image/png" if image[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(image).decode()}"


def vision_json(system: str, text: str, images: list[bytes], temperature: float = 0.1) -> dict[str, Any]:
    """图片以 base64 data URL 传入，本地 / 内网的 MinIO 地址无需对公网开放。"""
    content: list[dict] = [{"type": "image_url", "image_url": {"url": _data_url(img)}} for img in images]
    content.append({"type": "text", "text": text})
    messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]
    return _chat_json(get_settings().vl_model, messages, temperature, json_mode=False)
