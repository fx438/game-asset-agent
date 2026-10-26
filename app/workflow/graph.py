"""LangGraph 主流程。

    parse_requirements -> confirm_plan(人工) -> generate_master -> confirm_master(人工)
          ┌──────────── 不满意，带意见重生成 ────────────┘
          ↓ 满意，锁定风格
    dispatch_generation -> wait_generation(等待批量生图屏障) -> human_review(人工)
          ↑                                                   │ 有驳回且未超轮数
    regenerate_rejected <──────────────────────────────────────┘
                                                              │ 全部处理完
                                                           archive -> END

人工环节用 interrupt() 暂停，状态由 checkpointer 持久化（生产用 PostgresSaver），
API 收到用户操作后用 Command(resume=...) 唤醒；批量生图由 Celery 并发执行，
最后一张完成时由生成屏障唤醒 wait_generation。
"""
import logging
import re
import threading
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import select
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.agents import image_agent, prompt_agent, requirement_agent
from app.agents.styles import get_style
from app.ai.errors import TransientAIError
from app.config import get_settings
from app.db import session_scope
from app.models import Asset, AssetStatus, AssetTask, MasterStatus, Project, ProjectStatus as PS, StyleMaster
from app.parsers.document_parser import parse_document
from app.state_machine import transition
from app.storage import get_storage

logger = logging.getLogger(__name__)

HUMAN_FIX_MAX = 120


class FlowState(TypedDict, total=False):
    project_id: int
    master_feedback: str | None


# ---------------------------------------------------------------- 节点

def parse_requirements(state: FlowState) -> dict:
    pid = state["project_id"]
    with session_scope() as s:
        project = s.get(Project, pid)
        transition(s, project, PS.PARSING)
        filename, key = project.source_filename, project.source_key

    doc = parse_document(filename, get_storage().get_bytes(key))
    result = requirement_agent.run(doc)

    with session_scope() as s:
        project = s.get(Project, pid)
        for i, t in enumerate(result.tasks):
            s.add(AssetTask(project_id=pid, sort_order=i, **t.model_dump()))
        project.parse_issues = [f"[解析方式: {result.method}]"] + result.issues
        transition(s, project, PS.PLAN_CONFIRMING)
    return {}


def _await(kind: str, token: int | None = None) -> dict[str, Any] | None:
    """暂停等待人工 / 外部事件。唤醒值的 type（以及轮次 token）必须与当前环节一致，
    否则视为过期或重复的唤醒，返回 None 由节点重新等待。

    例如用户连点两次"提交审核"：第二次唤醒可能在重生成完成后才被处理，
    此时轮次已经 +1，token 对不上就被丢弃，不会把新一轮未审核的图直接归档。
    """
    value = interrupt({"type": kind, "token": token})
    if not isinstance(value, dict) or value.get("type") != kind or (token is not None and value.get("token") != token):
        logger.warning("忽略与当前环节 %s(token=%s) 不匹配的唤醒: %r", kind, token, value)
        return None
    return value


def confirm_plan(state: FlowState) -> Command[Literal["confirm_plan", "generate_master"]]:
    """人工确认任务清单 + 选择风格母版。resume 值见 schemas.PlanConfirm。"""
    decision = _await("confirm_plan")
    if decision is None:
        return Command(goto="confirm_plan")
    pid = state["project_id"]
    style = get_style(decision["style_key"])

    with session_scope() as s:
        project = s.get(Project, pid)
        if decision.get("tasks"):
            project.tasks.clear()
            s.flush()
            for i, t in enumerate(decision["tasks"]):
                project.tasks.append(AssetTask(project_id=pid, sort_order=i, **requirement_agent.TaskItem(**t).model_dump()))
        project.style_key, project.style_notes = style.key, decision.get("style_notes") or None
        s.flush()
        pairs = [(t.hero, t.role) for t in project.tasks]
        descs = {prompt_agent.card_key(t.hero, t.role): t.description for t in project.tasks if t.description}

    cards = prompt_agent.build_character_cards(pairs, style, descs)
    with session_scope() as s:
        project = s.get(Project, pid)
        project.character_cards = cards
        transition(s, project, PS.MASTER_GENERATING)
    return Command(goto="generate_master", update={"master_feedback": None})


@retry(retry=retry_if_exception_type(TransientAIError), stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=20), reraise=True)
def _generate_master_with_retry(pid: int, feedback: str | None) -> int:
    return image_agent.generate_master(pid, feedback)


def generate_master(state: FlowState) -> dict:
    pid = state["project_id"]
    _generate_master_with_retry(pid, state.get("master_feedback"))
    with session_scope() as s:
        transition(s, s.get(Project, pid), PS.MASTER_CONFIRMING)
    return {}


def latest_master_version(pid: int) -> int:
    with session_scope() as s:
        return s.scalar(select(StyleMaster.version).where(StyleMaster.project_id == pid).order_by(StyleMaster.version.desc()).limit(1)) or 0


def review_round(pid: int) -> int:
    with session_scope() as s:
        return s.get(Project, pid).review_round


def confirm_master(state: FlowState) -> Command[Literal["confirm_master", "generate_master", "dispatch_generation"]]:
    pid = state["project_id"]
    decision = _await("confirm_master", token=latest_master_version(pid))
    if decision is None:
        return Command(goto="confirm_master")
    with session_scope() as s:
        project = s.get(Project, pid)
        master = s.scalar(select(StyleMaster).where(StyleMaster.project_id == pid).order_by(StyleMaster.version.desc()))
        if decision.get("approved"):
            master.status = MasterStatus.APPROVED
            return Command(goto="dispatch_generation")
        master.status, master.feedback = MasterStatus.REJECTED, decision.get("feedback")
        transition(s, project, PS.MASTER_GENERATING)
    return Command(goto="generate_master", update={"master_feedback": decision.get("feedback")})


def dispatch_generation(state: FlowState) -> dict:
    """风格锁定后，为每个任务拆帧、拼提示词、建资源记录，再并发投递生图。"""
    from app.tasks import dispatch

    pid = state["project_id"]
    with session_scope() as s:
        project = s.get(Project, pid)
        style, notes, cards = get_style(project.style_key), project.style_notes, project.character_cards
        tasks = [(t.id, t.hero, t.role, t.action, t.count, t.view, t.description) for t in project.tasks]

    plans = []
    for task_id, hero, role, action, count, view, desc in tasks:
        frames = prompt_agent.expand_frames(action, count, view, desc)
        card = cards[prompt_agent.card_key(hero, role)]
        for seq, frame in enumerate(frames, 1):
            prompt = prompt_agent.compose_prompt(style, notes, card, hero, role, frame, view)
            plans.append(Asset(project_id=pid, task_id=task_id, seq=seq, frame_desc=frame, prompt=prompt, negative_prompt=style.negative))

    with session_scope() as s:
        s.add_all(plans)
        transition(s, s.get(Project, pid), PS.GENERATING)   # 先提交 GENERATING，生成屏障才能正确判断
        s.flush()
        asset_ids = [a.id for a in plans]
    for asset_id in asset_ids:
        dispatch.enqueue_asset(asset_id, pid)
    return {}


def wait_generation(state: FlowState) -> Command[Literal["wait_generation", "human_review"]]:
    """等待生成屏障：本批最后一张图完成后，由 image_agent.notify_if_batch_done 唤醒。"""
    if _await("wait_generation", token=review_round(state["project_id"])) is None:
        return Command(goto="wait_generation")
    return Command(goto="human_review")


def human_review(state: FlowState) -> Command[Literal["human_review", "regenerate_rejected", "archive"]]:
    pid = state["project_id"]
    if _await("human_review", token=review_round(pid)) is None:
        return Command(goto="human_review")
    with session_scope() as s:
        project = s.get(Project, pid)
        undecided = s.scalar(select(Asset.id).where(Asset.project_id == pid, Asset.status.in_(
            (AssetStatus.WAITING_HUMAN, AssetStatus.AUTO_FAILED, AssetStatus.ERROR, *image_agent.ACTIVE_STATUSES))).limit(1))
        if undecided:   # 防御：还有没审完的图时不推进（接口层已校验，这里兜底）
            logger.warning("项目 %s 仍有未审核资源，忽略提交", pid)
            return Command(goto="human_review")
        rejected = s.scalar(select(Asset.id).where(Asset.project_id == pid, Asset.status == AssetStatus.REJECTED).limit(1))
        if rejected and project.review_round < get_settings().max_human_rounds:
            return Command(goto="regenerate_rejected")
    return Command(goto="archive")


def regenerate_rejected(state: FlowState) -> dict:
    """人工驳回的图带着批注回流到提示词扩写，重新生成。"""
    from app.tasks import dispatch

    pid = state["project_id"]
    with session_scope() as s:
        project = s.get(Project, pid)
        style, notes, cards = get_style(project.style_key), project.style_notes, project.character_cards
        rejected = s.scalars(select(Asset).where(Asset.project_id == pid, Asset.status == AssetStatus.REJECTED)).all()
        items = [(a.id, a.prompt, a.negative_prompt, a.human_feedback, a.human_fix, a.frame_desc, a.task.hero, a.task.role, a.task.view)
                 for a in rejected]

    updates = {}
    for asset_id, prompt, negative, feedback, old_fix, frame, hero, role, view in items:
        fix, new_negative = prompt_agent.refine(prompt, negative, [f"人工审核意见：{feedback or '整体不满意，请重新生成'}"])
        # 多轮驳回的意见累积保留，超长时优先保留最新的
        human_fix = "，".join(x for x in (old_fix, fix) if x)[-HUMAN_FIX_MAX:]
        card = cards[prompt_agent.card_key(hero, role)]
        updates[asset_id] = (prompt_agent.compose_prompt(style, notes, card, hero, role, frame, view, human_fix), new_negative, human_fix)

    with session_scope() as s:
        project = s.get(Project, pid)
        project.review_round += 1
        for asset_id, (prompt, negative, human_fix) in updates.items():
            asset = s.get(Asset, asset_id)
            asset.prompt, asset.negative_prompt, asset.human_fix = prompt, negative, human_fix
            asset.status, asset.attempts, asset.error = AssetStatus.PENDING, 0, None
        transition(s, project, PS.GENERATING)
    for asset_id in updates:
        dispatch.enqueue_asset(asset_id, pid)
    return {}


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_") or "unnamed"


def archive(state: FlowState) -> dict:
    """通过的图按 项目/英雄/角色/动作 规范命名归档到资源库，并写一份清单记录生成参数。"""
    import json

    pid = state["project_id"]
    storage = get_storage()
    with session_scope() as s:
        project = s.get(Project, pid)
        transition(s, project, PS.ARCHIVING)
        root = f"library/{pid}_{_safe(project.name)}"
        master = image_agent.approved_master(s, pid)
        approved = s.scalars(select(Asset).where(Asset.project_id == pid, Asset.status == AssetStatus.APPROVED)
                             .order_by(Asset.task_id, Asset.seq)).all()
        manifest = []
        for a in approved:
            t = a.task
            name = f"{_safe(t.hero)}_{_safe(t.role)}_{_safe(t.action)}_{a.seq:02d}.png"
            dst = f"{root}/{_safe(t.hero)}/{_safe(t.role)}/{_safe(t.action)}/{name}"
            storage.copy(a.image_key, dst)
            a.archive_key, a.status = dst, AssetStatus.ARCHIVED
            manifest.append({"file": dst, "hero": t.hero, "role": t.role, "action": t.action, "seq": a.seq,
                             "frame": a.frame_desc, "prompt": a.prompt, "negative_prompt": a.negative_prompt,
                             "seed": a.seed, "version": a.version, "model": get_settings().image_model,
                             "style": project.style_key, "master_version": master.version})
        storage.put_bytes(f"{root}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode(), "application/json")
        transition(s, project, PS.ARCHIVED)
    return {}


# ---------------------------------------------------------------- 组装与运行

def build_graph() -> StateGraph:
    g = StateGraph(FlowState)
    for node in (parse_requirements, confirm_plan, generate_master, confirm_master, dispatch_generation,
                 wait_generation, human_review, regenerate_rejected, archive):
        g.add_node(node.__name__, node)
    # 固定边；人工环节之后的分支由节点返回的 Command(goto=...) 决定
    g.add_edge(START, "parse_requirements")
    g.add_edge("parse_requirements", "confirm_plan")
    g.add_edge("generate_master", "confirm_master")
    g.add_edge("dispatch_generation", "wait_generation")
    g.add_edge("regenerate_rejected", "wait_generation")
    g.add_edge("archive", END)
    return g


@lru_cache
def _checkpointer():
    settings = get_settings()
    if settings.is_postgres:
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg_pool import ConnectionPool

        conninfo = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        pool = ConnectionPool(conninfo, max_size=10, kwargs={"autocommit": True, "prepare_threshold": 0})
        saver = PostgresSaver(pool)
        saver.setup()
        return saver
    # 非 PostgreSQL（测试 / 单进程本地开发）用内存版，进程重启后流程状态会丢失
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver()


@lru_cache
def get_graph():
    return build_graph().compile(checkpointer=_checkpointer())


_local_locks: dict[int, threading.Lock] = {}
_local_guard = threading.Lock()


@contextmanager
def project_lock(project_id: int):
    """同一项目的流程推进必须串行：Celery 模式用 Redis 分布式锁，其余用进程内锁。"""
    if get_settings().task_mode == "celery":
        import redis

        lock = redis.Redis.from_url(get_settings().redis_url).lock(f"lock:workflow:{project_id}", timeout=900, blocking_timeout=900)
        if not lock.acquire():
            raise TimeoutError(f"项目 {project_id} 流程锁获取超时")
        try:
            yield
        finally:
            lock.release()
    else:
        with _local_guard:
            lock = _local_locks.setdefault(project_id, threading.Lock())
        with lock:
            yield


def _config(project_id: int) -> dict:
    return {"configurable": {"thread_id": f"project-{project_id}"}}


def run_workflow(project_id: int, resume: dict | None = None) -> None:
    with project_lock(project_id):
        graph = get_graph()
        payload = Command(resume=resume) if resume is not None else {"project_id": project_id}
        try:
            graph.invoke(payload, _config(project_id))
        except Exception as exc:
            logger.exception("项目 %s 流程执行失败", project_id)
            with session_scope() as s:
                project = s.get(Project, project_id)
                project.error = f"{exc.__class__.__name__}: {exc}"[:2000]
                if project.status not in (PS.ARCHIVED, PS.FAILED):
                    project.status = PS.FAILED
            raise


def pending_interrupt(project_id: int) -> str | None:
    """当前流程停在哪个人工环节（confirm_plan / confirm_master / wait_generation / human_review）。"""
    snapshot = get_graph().get_state(_config(project_id))
    for task in snapshot.tasks:
        for intr in task.interrupts:
            return intr.value.get("type")
    return None
