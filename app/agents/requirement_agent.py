"""需求拆解 Agent：需求文档 -> 结构化任务清单（英雄 × 角色 × 动作 × 张数）。

策略：
1. 文档里有规范的资源清单表（能识别出英雄 / 动作 / 数量列）时直接按表读取，结果确定、零成本；
2. 否则把正文 + 表格交给 qwen-plus，用 JSON 输出，再用 Pydantic 校验；
3. 最后统一清洗：去空、合并重复项、数量越界截断，并把所有"自作主张"的地方记进 issues 给用户确认。
"""
import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.ai import dashscope_llm
from app.ai.errors import AIError
from app.config import get_settings
from app.parsers.document_parser import ParsedDocument


class TaskItem(BaseModel):
    hero: str = Field(min_length=1, max_length=100)
    role: str = Field(default="通用", max_length=100)
    action: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1)
    description: str = ""
    view: str = ""

    @field_validator("hero", "role", "action", "description", "view", mode="before")
    @classmethod
    def _strip(cls, v):
        return "" if v is None else str(v).strip()


class RequirementResult(BaseModel):
    tasks: list[TaskItem]
    issues: list[str] = []
    method: str = "llm"

    @property
    def total_images(self) -> int:
        return sum(t.count for t in self.tasks)


COLUMN_ALIASES = {
    "hero": ["英雄", "英雄名", "角色名称", "人物", "hero", "character", "name"],
    "role": ["角色", "职业", "定位", "职能", "role", "class"],
    "action": ["动作", "动画", "状态", "action", "animation", "pose"],
    "count": ["数量", "张数", "帧数", "图片数", "count", "num", "frames", "quantity"],
    "description": ["描述", "说明", "备注", "要求", "description", "note", "notes"],
    "view": ["视角", "朝向", "方向", "view", "direction"],
}

SYSTEM_PROMPT = """你是游戏美术需求分析师。把策划提供的需求文档拆解成出图任务清单。
每个任务 = 一个英雄的一个角色/职业形态的一个动作，以及该动作需要的图片张数（如动作帧数）。
规则：
- hero：英雄或角色名称；role：职业/定位/形态，文档没写就填"通用"；action：动作名称，如 待机、攻击、受击、死亡、行走。
- count：该动作需要的张数，必须是正整数；文档没写时按常见动作帧数估计（待机 4、行走 6、攻击 6、受击 2、死亡 4），并在 issues 中说明是估计值。
- description：该任务的外观或动作补充要求，没有就留空；view：视角或朝向，如 侧面、正面、45度，没有就留空。
- 文档中含糊、矛盾、缺失的信息逐条写进 issues，不要编造英雄。
只输出 JSON：{"tasks":[{"hero":"","role":"","action":"","count":1,"description":"","view":""}],"issues":[""]}"""


def _match_column(header: str) -> str | None:
    h = header.strip().lower()
    for field, aliases in COLUMN_ALIASES.items():
        if any(h == a.lower() or a.lower() in h for a in aliases):
            return field
    return None


def _parse_count(value: str) -> int | None:
    m = re.search(r"\d+", value or "")
    return int(m.group()) if m else None


def extract_from_tables(tables: list[list[list[str]]]) -> tuple[list[TaskItem], list[str]]:
    tasks: list[TaskItem] = []
    issues: list[str] = []
    for t_idx, table in enumerate(tables, 1):
        for h_idx, header in enumerate(table[:5]):   # 表头允许出现在前 5 行
            mapping: dict[int, str] = {}
            for col, cell in enumerate(header):
                field = _match_column(cell) if cell else None
                if field and field not in mapping.values():
                    mapping[col] = field
            if not {"hero", "action", "count"} <= set(mapping.values()):
                continue
            for r_idx, row in enumerate(table[h_idx + 1:], h_idx + 2):
                values = {field: (row[col] if col < len(row) else "") for col, field in mapping.items()}
                if not values.get("hero") and not values.get("action"):
                    continue
                count = _parse_count(values.get("count", ""))
                if count is None:
                    issues.append(f"表格{t_idx} 第{r_idx}行数量无法识别（{values.get('count')!r}），已跳过")
                    continue
                try:
                    tasks.append(TaskItem(**{**values, "count": count, "role": values.get("role") or "通用"}))
                except ValidationError:
                    issues.append(f"表格{t_idx} 第{r_idx}行缺少英雄或动作，已跳过")
            break
    return tasks, issues


def normalize(tasks: list[TaskItem], issues: list[str]) -> list[TaskItem]:
    limit = get_settings().images_per_task_limit
    merged: dict[tuple[str, str, str], TaskItem] = {}
    for t in tasks:
        if t.count > limit:
            issues.append(f"{t.hero}/{t.role}/{t.action} 数量 {t.count} 超过上限 {limit}，已截断")
            t.count = limit
        key = (t.hero, t.role, t.action)
        if key in merged:
            old = merged[key]
            issues.append(f"{t.hero}/{t.role}/{t.action} 重复出现，已合并（取较大数量 {max(old.count, t.count)}）")
            old.count = max(old.count, t.count)
            old.description = "；".join(x for x in (old.description, t.description) if x)
        else:
            merged[key] = t
    return _cap_total(list(merged.values()), issues)


def _cap_total(tasks: list[TaskItem], issues: list[str]) -> list[TaskItem]:
    """演示模式下限制整个项目的总张数，超出部分从后往前截掉。"""
    total_limit = get_settings().total_images_limit
    if total_limit is None or sum(t.count for t in tasks) <= total_limit:
        return tasks
    kept, left = [], total_limit
    for t in tasks:
        if left <= 0:
            break
        t.count = min(t.count, left)
        left -= t.count
        kept.append(t)
    issues.append(f"在线演示每个项目最多 {total_limit} 张，超出的任务已截断")
    return kept


def _llm_extract(doc: ParsedDocument) -> tuple[list[TaskItem], list[str]]:
    data = dashscope_llm.chat_json(SYSTEM_PROMPT, f"需求文档内容如下：\n{doc.as_prompt_text()}")
    tasks, issues = [], [str(x) for x in data.get("issues", []) if x]
    for i, raw in enumerate(data.get("tasks", []), 1):
        try:
            tasks.append(TaskItem(**raw))
        except (ValidationError, TypeError) as exc:
            issues.append(f"模型输出的第{i}个任务不合法，已丢弃：{exc.__class__.__name__}")
    return tasks, issues


_MOCK_LINE = re.compile(r"(?P<hero>[\w一-龥]+)[\s/、,，]+(?P<role>[\w一-龥]+)[\s/、,，]+(?P<action>[\w一-龥]+)[\s/、,，]*(?P<count>\d+)\s*张?")


def _mock_extract(doc: ParsedDocument) -> tuple[list[TaskItem], list[str]]:
    """离线模式：识别 "英雄A 战士 攻击 4张" 这类行。"""
    tasks = [
        TaskItem(hero=m["hero"], role=m["role"], action=m["action"], count=int(m["count"]))
        for m in _MOCK_LINE.finditer(doc.text)
    ]
    return tasks, ["当前为 mock 模式（未配置 DASHSCOPE_API_KEY），自由文本仅按\"英雄 角色 动作 N张\"格式识别"]


def run(doc: ParsedDocument) -> RequirementResult:
    tasks, issues = extract_from_tables(doc.tables)
    method = "table"
    if not tasks:
        method = "mock" if get_settings().mock_ai else "llm"
        tasks, more = _mock_extract(doc) if method == "mock" else _llm_extract(doc)
        issues += more
    tasks = normalize(tasks, issues)
    if not tasks:
        raise AIError("没有从文档中识别出任何出图任务，请检查文档内容或改用资源清单表格")
    return RequirementResult(tasks=tasks, issues=issues, method=method)
