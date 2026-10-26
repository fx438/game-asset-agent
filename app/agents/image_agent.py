"""生图 Agent：生成风格母版；以及单张资源的"生成 -> 自动审查 -> 修正重生成"闭环。

数据库事务只包住读写，不包住调用万相 / qwen-vl 的网络请求，避免长事务占着连接和锁。
"""
import logging
import random

from sqlalchemy import func, select

from app.agents import prompt_agent, review_agent
from app.agents.styles import get_style
from app.ai import wanx_client
from app.ai.errors import AIError
from app.config import get_settings
from app.db import session_scope
from app.models import Asset, AssetStatus, MasterStatus, Project, ProjectStatus, ReviewRecord, StyleMaster
from app.state_machine import compare_and_set
from app.storage import get_storage

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (AssetStatus.PENDING, AssetStatus.GENERATING, AssetStatus.AUTO_REVIEWING)


def approved_master(session, project_id: int) -> StyleMaster:
    master = session.scalar(
        select(StyleMaster).where(StyleMaster.project_id == project_id, StyleMaster.status == MasterStatus.APPROVED)
        .order_by(StyleMaster.version.desc())
    )
    if master is None:
        raise AIError(f"项目 {project_id} 没有已确认的风格母版")
    return master


def generate_master(project_id: int, feedback: str | None) -> int:
    """生成新一版母版，返回 StyleMaster.id。"""
    with session_scope() as s:
        project = s.get(Project, project_id)
        style = get_style(project.style_key)
        first = project.tasks[0]
        card = project.character_cards[prompt_agent.card_key(first.hero, first.role)]
        version = (s.scalar(select(func.max(StyleMaster.version)).where(StyleMaster.project_id == project_id)) or 0) + 1
        hero, role, notes = first.hero, first.role, project.style_notes

    prompt, negative = prompt_agent.build_master_prompt(style, notes, hero, role, card, feedback)
    seed = random.randint(0, 2_147_483_647)
    image = wanx_client.generate(prompt, negative, seed, label=f"MASTER v{version} {style.key}")
    key = get_storage().put_bytes(f"projects/{project_id}/masters/v{version}.png", image.data, "image/png")

    with session_scope() as s:
        master = StyleMaster(project_id=project_id, version=version, style_key=style.key, prompt=image.actual_prompt,
                             negative_prompt=negative, seed=seed, image_key=key)
        s.add(master)
        s.flush()
        return master.id


def process_asset(asset_id: int) -> None:
    """单张资源闭环：生成 -> 自动审查 -> 不通过则按问题修正提示词重生成，直到通过或用尽次数。"""
    settings = get_settings()
    storage = get_storage()
    with session_scope() as s:
        asset = s.get(Asset, asset_id)
        if asset.status not in ACTIVE_STATUSES:   # 重复投递（如 Celery 重试）时直接跳过
            return
        task, project = asset.task, s.get(Project, asset.project_id)
        master = approved_master(s, project.id)
        style = get_style(project.style_key)
        card = project.character_cards[prompt_agent.card_key(task.hero, task.role)]
        ctx = dict(hero=task.hero, role=task.role, view=task.view, notes=project.style_notes,
                   task_desc=f"{task.hero}（{task.role}）{task.action}，{asset.frame_desc}，视角：{task.view or '不限'}",
                   master_key=master.image_key, master_seed=master.seed, project_id=project.id)
        asset.status = AssetStatus.GENERATING

    master_image = storage.get_bytes(ctx["master_key"])
    while True:
        with session_scope() as s:
            asset = s.get(Asset, asset_id)
            asset.version += 1
            prompt, negative, version, attempts = asset.prompt, asset.negative_prompt, asset.version, asset.attempts
            # 首次用母版的 seed 保持风格；自动重试时偏移 seed，跳出同一个坏样本
            seed = (ctx["master_seed"] + attempts) % 2_147_483_648
            asset.seed = seed

        image = wanx_client.generate(prompt, negative, seed, label=f"{ctx['hero']} {asset_id} v{version}")
        key = storage.put_bytes(f"projects/{ctx['project_id']}/assets/{asset_id}/v{version}.png", image.data, "image/png")
        with session_scope() as s:
            asset = s.get(Asset, asset_id)
            asset.image_key, asset.status = key, AssetStatus.AUTO_REVIEWING

        result = review_agent.review(image.data, master_image, ctx["task_desc"])
        with session_scope() as s:
            asset = s.get(Asset, asset_id)
            s.add(ReviewRecord(asset_id=asset_id, asset_version=version, kind="auto", passed=result.passed,
                               content_score=result.content_score, style_score=result.style_score, issues=result.issues))
            asset.last_issues = result.issues
            if result.passed:
                asset.status = AssetStatus.WAITING_HUMAN
                return
            asset.attempts += 1
            if asset.attempts > settings.max_auto_retries:
                asset.status = AssetStatus.AUTO_FAILED
                logger.info("资源 %s 自动重试 %d 次仍未通过，交人工裁决", asset_id, settings.max_auto_retries)
                return
            asset.status = AssetStatus.GENERATING

        fix, new_negative = prompt_agent.refine(prompt, negative, result.issues)
        with session_scope() as s:
            asset = s.get(Asset, asset_id)
            asset.prompt = prompt_agent.compose_prompt(style, ctx["notes"], card, ctx["hero"], ctx["role"], asset.frame_desc,
                                                       ctx["view"], "，".join(x for x in (asset.human_fix, fix) if x))
            asset.negative_prompt = new_negative


def mark_error(asset_id: int, message: str) -> None:
    with session_scope() as s:
        asset = s.get(Asset, asset_id)
        asset.status, asset.error = AssetStatus.ERROR, message[:2000]


def notify_if_batch_done(project_id: int) -> bool:
    """生成屏障：本批最后一张图完成时唤醒工作流进入人工审核。

    每个 worker 先提交自己的终态再来检查，所以最后提交的那个一定能看到"无活跃任务"；
    多个 worker 同时看到时，靠状态机的 compare_and_set 保证只有一个能唤醒。
    """
    from app.tasks import dispatch

    with session_scope() as s:
        active = s.scalar(select(func.count()).select_from(Asset)
                          .where(Asset.project_id == project_id, Asset.status.in_(ACTIVE_STATUSES)))
        if active:
            return False
        won = compare_and_set(s, project_id, ProjectStatus.GENERATING, ProjectStatus.HUMAN_REVIEWING)
        round_no = s.get(Project, project_id).review_round
    if won:
        dispatch.enqueue_workflow(project_id, {"type": "wait_generation", "token": round_no})
    return won
