"""提示词扩写 Agent。

设计要点：大模型只负责"内容"（角色外观设定卡、每一帧的动作描述、按审查意见修正），
最终提示词由固定模板拼装：风格前缀 + 用户风格补充 + 角色设定卡 + 动作帧描述。
这样同一项目里每张图的风格部分逐字一致，再配合母版的固定 seed，保证批量出图风格统一。
"""
from app.agents.styles import StyleTemplate
from app.ai import dashscope_llm
from app.config import get_settings

CARD_MAX = 90
FRAME_MAX = 60

CARD_SYSTEM = f"""你是游戏角色美术设定师。为每个"英雄|角色"写一段外观设定卡，用于 AI 生图时保持同一角色在不同动作中外观一致。
要求：每段不超过{CARD_MAX}字，只写可见特征：性别年龄感、发型发色、服装款式与主色、标志性武器或配饰。不写动作、不写背景、不写画风。
只输出 JSON：{{"cards": {{"英雄|角色": "设定卡"}}}}，键必须与输入完全一致。"""

FRAME_SYSTEM = f"""你是游戏动画分镜师。把一个角色动作拆成指定数量的关键帧，每帧一句画面描述（不超过{FRAME_MAX}字），
描述该帧的姿态、肢体位置和动作阶段，帧与帧之间要连贯（如 预备 -> 发力 -> 命中 -> 收招）。不写外观、不写画风。
只输出 JSON：{{"frames": ["第1帧描述", "..."]}}，数组长度必须等于要求的帧数。"""

MASTER_SYSTEM = """你是游戏美术风格设计师，要生成一张"风格母版"图，用来给整个项目定画风。
根据给定的画风和代表角色，写一段画面描述：角色以站立展示姿态出现，突出画风的线条、上色、光影和材质特征。
不超过150字，不要重复画风前缀里已有的词。只输出 JSON：{"scene": "画面描述"}"""

REFINE_SYSTEM = """你是 AI 生图提示词修正专家。根据审查发现的问题，给出需要补充的正向修正描述和需要追加的反向提示词，
以便下一次生成避开这些问题。修正描述不超过60字，反向提示词不超过40字。
只输出 JSON：{"fix": "正向修正描述", "negative_add": "追加的反向提示词"}"""


def card_key(hero: str, role: str) -> str:
    return f"{hero}|{role}"


def build_character_cards(pairs: list[tuple[str, str]], style: StyleTemplate, descriptions: dict[str, str]) -> dict[str, str]:
    keys = list(dict.fromkeys(card_key(h, r) for h, r in pairs))
    if get_settings().mock_ai:
        return {k: f"{k.split('|')[0]}，{k.split('|')[1]}装扮，标志性主色服装与武器" for k in keys}
    lines = [f"- {k}" + (f"（策划描述：{descriptions[k]}）" if descriptions.get(k) else "") for k in keys]
    data = dashscope_llm.chat_json(CARD_SYSTEM, f"画风：{style.name}\n需要设定卡的角色：\n" + "\n".join(lines))
    cards = data.get("cards", {}) if isinstance(data.get("cards"), dict) else {}
    # 模型漏写的角色用兜底描述补齐，保证每个任务都有设定卡
    return {k: str(cards.get(k) or f"{k.replace('|', '，')}，外观特征鲜明")[:CARD_MAX] for k in keys}


def expand_frames(action: str, count: int, view: str, description: str) -> list[str]:
    if get_settings().mock_ai or count == 1:
        if count == 1:
            return [f"{action}，{description}".strip("，")[:FRAME_MAX]]
        return [f"{action} 第{i}/{count}帧" for i in range(1, count + 1)]
    data = dashscope_llm.chat_json(
        FRAME_SYSTEM,
        f"动作：{action}\n帧数：{count}\n视角：{view or '侧面'}\n补充要求：{description or '无'}",
    )
    frames = [str(f)[:FRAME_MAX] for f in data.get("frames", []) if str(f).strip()]
    # 模型给的帧数不对时补齐或截断，数量以任务清单为准
    while len(frames) < count:
        frames.append(f"{action} 第{len(frames) + 1}/{count}帧")
    return frames[:count]


def compose_prompt(style: StyleTemplate, style_notes: str | None, card: str, hero: str, role: str, frame: str, view: str, fix: str = "") -> str:
    parts = [style.prefix, style_notes or "", f"角色：{hero}（{role}），{card}", f"动作：{frame}", f"视角：{view}" if view else "", fix]
    return "，".join(p.strip("，。 ") for p in parts if p and p.strip())


def build_master_prompt(style: StyleTemplate, style_notes: str | None, hero: str, role: str, card: str, feedback: str | None) -> tuple[str, str]:
    scene = f"{hero}（{role}），{card}，站立展示姿态"
    if not get_settings().mock_ai:
        user = f"画风：{style.name}（{style.description}）\n画风前缀：{style.prefix}\n代表角色：{hero}（{role}），{card}"
        if style_notes:
            user += f"\n用户对风格的补充要求：{style_notes}"
        if feedback:
            user += f"\n用户对上一版母版的修改意见（必须体现）：{feedback}"
        scene = str(dashscope_llm.chat_json(MASTER_SYSTEM, user).get("scene") or scene)
    prompt = "，".join(p for p in (style.prefix, style_notes or "", scene, f"修改要求：{feedback}" if feedback and get_settings().mock_ai else "") if p)
    return prompt, style.negative


def refine(prompt: str, negative: str, issues: list[str]) -> tuple[str, str]:
    """按审查问题修正提示词，返回 (修正描述, 新反向提示词)。修正描述由调用方拼进模板。"""
    if not issues:
        return "", negative
    if get_settings().mock_ai:
        return "修正：" + "；".join(issues)[:60], negative
    data = dashscope_llm.chat_json(REFINE_SYSTEM, f"当前提示词：{prompt}\n审查发现的问题：\n" + "\n".join(f"- {i}" for i in issues))
    fix = str(data.get("fix", ""))[:60]
    add = str(data.get("negative_add", ""))[:40]
    return fix, "，".join(x for x in (negative, add) if x)
