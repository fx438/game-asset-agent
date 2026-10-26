"""通义万相文生图（异步接口）：提交任务 -> 按 task_id 轮询 -> 下载图片。

接口：POST /api/v1/services/aigc/text2image/image-synthesis（请求头 X-DashScope-Async: enable）
      GET  /api/v1/tasks/{task_id}
注意：结果图片 URL 只保留 24 小时，所以拿到后立即下载存入自己的对象存储。
该接口没有参考图参数，风格一致性靠"固定风格前缀 + 固定 seed + 角色设定卡"保证。
"""
import hashlib
import logging
import time
from dataclasses import dataclass

import httpx

from app.ai.errors import AIError, TransientAIError
from app.config import get_settings

logger = logging.getLogger(__name__)

PROMPT_MAX_CHARS = 500          # wan2.2 / wanx2.1 系列正向提示词上限
NEGATIVE_MAX_CHARS = 500


@dataclass
class GeneratedImage:
    data: bytes
    task_id: str
    actual_prompt: str


def _clip(text: str, limit: int, label: str) -> str:
    if len(text) <= limit:
        return text
    logger.warning("%s 超过 %d 字符，已截断（原长 %d）", label, limit, len(text))
    return text[:limit]


def _headers(async_mode: bool = False) -> dict[str, str]:
    h = {"Authorization": f"Bearer {get_settings().dashscope_api_key}", "Content-Type": "application/json"}
    if async_mode:
        h["X-DashScope-Async"] = "enable"
    return h


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 429 or resp.status_code >= 500:
        raise TransientAIError(f"万相接口 HTTP {resp.status_code}: {resp.text[:300]}")
    if resp.status_code >= 400:
        raise AIError(f"万相接口 HTTP {resp.status_code}: {resp.text[:300]}")


def submit(prompt: str, negative_prompt: str, seed: int, size: str | None = None) -> str:
    s = get_settings()
    body = {
        "model": s.image_model,
        "input": {
            "prompt": _clip(prompt, PROMPT_MAX_CHARS, "prompt"),
            "negative_prompt": _clip(negative_prompt, NEGATIVE_MAX_CHARS, "negative_prompt"),
        },
        "parameters": {
            "size": size or s.image_size,
            "n": 1,
            "seed": seed,
            "prompt_extend": False,   # 关闭智能改写，保证我们扩写好的提示词原样生效
            "watermark": False,
        },
    }
    url = f"{s.dashscope_base_url.rstrip('/')}/api/v1/services/aigc/text2image/image-synthesis"
    try:
        resp = httpx.post(url, json=body, headers=_headers(async_mode=True), timeout=30)
    except httpx.TransportError as exc:
        raise TransientAIError(f"提交万相任务网络错误: {exc}") from exc
    _raise_for_status(resp)
    return resp.json()["output"]["task_id"]


def poll(task_id: str) -> tuple[str, str]:
    """轮询直到结束，返回 (图片 URL, 实际提示词)。"""
    s = get_settings()
    url = f"{s.dashscope_base_url.rstrip('/')}/api/v1/tasks/{task_id}"
    deadline = time.monotonic() + s.wanx_timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, headers=_headers(), timeout=30)
        except httpx.TransportError as exc:
            raise TransientAIError(f"查询万相任务网络错误: {exc}") from exc
        _raise_for_status(resp)
        output = resp.json().get("output", {})
        status = output.get("task_status")
        if status == "SUCCEEDED":
            result = next((r for r in output.get("results", []) if r.get("url")), None)
            if result is None:
                first = (output.get("results") or [{}])[0]
                raise AIError(f"万相任务成功但无图片: {first.get('code')} {first.get('message')}")
            return result["url"], result.get("actual_prompt") or result.get("orig_prompt", "")
        if status in ("FAILED", "CANCELED", "UNKNOWN"):
            code, message = output.get("code", ""), output.get("message", "")
            # 内容审核不通过等错误重试也没用；其余失败按临时错误处理
            if code in ("DataInspectionFailed", "InvalidParameter", "IPInfringementSuspect"):
                raise AIError(f"万相任务失败 {code}: {message}")
            raise TransientAIError(f"万相任务{status} {code}: {message}")
        time.sleep(s.wanx_poll_interval)
    raise TransientAIError(f"万相任务 {task_id} 超时未完成")


def download(url: str) -> bytes:
    try:
        resp = httpx.get(url, timeout=60)
    except httpx.TransportError as exc:
        raise TransientAIError(f"下载图片失败: {exc}") from exc
    _raise_for_status(resp)
    return resp.content


def generate(prompt: str, negative_prompt: str, seed: int, label: str = "") -> GeneratedImage:
    if get_settings().mock_ai:
        return _mock_generate(prompt, seed, label)
    task_id = submit(prompt, negative_prompt, seed)
    url, actual = poll(task_id)
    return GeneratedImage(data=download(url), task_id=task_id, actual_prompt=actual or prompt)


# ---------------- mock：不消耗额度，用代码画一张像素风示例图 ----------------

def _mock_generate(prompt: str, seed: int, label: str) -> GeneratedImage:
    from app.ai import example_images, pixel_sprites

    w, h = (int(x) for x in get_settings().image_size.split("*"))
    digest = hashlib.md5(f"{prompt}|{seed}".encode()).digest()
    # 示例需求优先用预制图，其余情况用代码现画
    data = example_images.lookup(prompt, seed, label, (w, h)) or pixel_sprites.render(prompt, seed, label, (w, h))
    return GeneratedImage(data=data, task_id=f"mock-{digest.hex()[:12]}", actual_prompt=prompt)
