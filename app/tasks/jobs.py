"""Celery 任务定义。函数体都委托给普通函数，thread / sync 模式直接调用同样的逻辑。"""
import logging

from app.agents import image_agent
from app.ai.errors import AIError, TransientAIError
from app.config import get_settings
from app.tasks.celery_app import celery_app
from app.workflow.graph import run_workflow

logger = logging.getLogger(__name__)


def generate_asset_impl(asset_id: int, project_id: int, final_attempt: bool = True) -> None:
    """生成单张资源。临时错误向上抛给调用方重试；彻底失败标记 ERROR。无论结果如何都检查生成屏障。"""
    try:
        image_agent.process_asset(asset_id)
    except TransientAIError as exc:
        if not final_attempt:
            raise
        image_agent.mark_error(asset_id, f"重试后仍失败: {exc}")
    except AIError as exc:
        image_agent.mark_error(asset_id, str(exc))
    except Exception as exc:  # 未预期错误也要落库，否则屏障永远等不到这张图
        logger.exception("资源 %s 生成异常", asset_id)
        image_agent.mark_error(asset_id, f"{exc.__class__.__name__}: {exc}")
    image_agent.notify_if_batch_done(project_id)


@celery_app.task(name="jobs.generate_asset", bind=True, max_retries=3, rate_limit=get_settings().wanx_rate_limit)
def generate_asset(self, asset_id: int, project_id: int) -> None:
    try:
        generate_asset_impl(asset_id, project_id, final_attempt=self.request.retries >= self.max_retries)
    except TransientAIError as exc:
        # 限流 / 超时：指数退避重试 10s、20s、40s
        raise self.retry(exc=exc, countdown=10 * 2 ** self.request.retries)


@celery_app.task(name="jobs.advance_workflow")
def advance_workflow(project_id: int, resume: dict | None = None) -> None:
    run_workflow(project_id, resume)
