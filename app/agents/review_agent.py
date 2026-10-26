"""审查 Agent：规则校验 + qwen-vl 看图审查。

是否通过由代码按分数和瑕疵判定，不让模型直接给结论，避免"模型说通过就通过"。
"""
import hashlib
import io
from dataclasses import dataclass, field

from app.ai import dashscope_llm
from app.config import get_settings

VL_SYSTEM = """你是严格的游戏美术质检员。第1张图是已确认的风格母版，第2张图是待审查的生成图。
按以下维度打分（0-10 的整数）并找出问题：
- content_score：第2张图的角色、动作、视角是否符合任务要求；
- style_score：第2张图的画风（线条、上色、光影、材质、像素颗粒度等）与母版是否一致；
- defects：明显瑕疵，如多手指、肢体畸形或缺失、面部崩坏、多余的人物、画面文字或水印、主体被裁切；没有则为空数组；
- issues：给提示词修正用的具体问题描述，没有则为空数组。
只输出 JSON：{"content_score":0,"style_score":0,"defects":[],"issues":[]}"""


@dataclass
class ReviewResult:
    passed: bool
    content_score: int | None = None
    style_score: int | None = None
    issues: list[str] = field(default_factory=list)


def rule_check(image: bytes) -> list[str]:
    from PIL import Image, UnidentifiedImageError

    try:
        img = Image.open(io.BytesIO(image))
        img.verify()
        img = Image.open(io.BytesIO(image))
    except (UnidentifiedImageError, OSError):
        return ["图片文件损坏或格式无法识别"]
    issues = []
    expected = tuple(int(x) for x in get_settings().image_size.split("*"))
    if img.size != expected:
        issues.append(f"尺寸 {img.size[0]}x{img.size[1]} 与规格 {expected[0]}x{expected[1]} 不符")
    if img.format not in ("PNG", "JPEG", "WEBP"):
        issues.append(f"图片格式 {img.format} 不在允许范围内")
    return issues


def _score(value) -> int:
    try:
        return max(0, min(10, int(value)))
    except (TypeError, ValueError):
        return 0


def review(image: bytes, master_image: bytes, task_desc: str) -> ReviewResult:
    issues = rule_check(image)
    if issues:
        return ReviewResult(passed=False, issues=issues)

    threshold = get_settings().review_pass_score
    if get_settings().mock_ai:
        # 离线模式按图片内容哈希给出确定性的分数，约 30% 不通过，用来演示"自动驳回 -> 修正重生成"
        d = hashlib.md5(image).digest()
        content, style = 5 + d[0] % 6, 5 + d[1] % 6
        defects = [] if d[2] % 10 < 8 else ["手部疑似多指"]
        extra = [f"内容符合度 {content} 分偏低"] if content < threshold else []
        extra += [f"风格一致性 {style} 分偏低"] if style < threshold else []
    else:
        data = dashscope_llm.vision_json(VL_SYSTEM, f"任务要求：{task_desc}", [master_image, image])
        content, style = _score(data.get("content_score")), _score(data.get("style_score"))
        defects = [str(x) for x in data.get("defects", []) if str(x).strip()]
        extra = [str(x) for x in data.get("issues", []) if str(x).strip()]

    passed = content >= threshold and style >= threshold and not defects
    return ReviewResult(passed=passed, content_score=content, style_score=style, issues=defects + extra)
