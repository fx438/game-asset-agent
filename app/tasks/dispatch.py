"""任务投递的统一入口，业务代码不关心底层是 Celery 还是线程池。

- celery：投递到 Redis，由独立的 Celery worker 执行（生产）；
- thread：进程内线程池，适合没有 Redis 的本地开发；
- sync：放进队列，由最外层调用者按顺序执行完（测试用，结果确定）。
"""
import logging
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from app.ai.errors import TransientAIError
from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache
def _executor() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=get_settings().thread_workers, thread_name_prefix="gaa")


def _run_asset_with_retry(asset_id: int, project_id: int) -> None:
    from app.tasks.jobs import generate_asset_impl

    for attempt in range(4):
        try:
            generate_asset_impl(asset_id, project_id, final_attempt=attempt == 3)
            return
        except TransientAIError:
            time.sleep(min(2 ** attempt * 5, 40))


def _run_workflow_safely(project_id: int, resume: dict | None) -> None:
    from app.workflow.graph import run_workflow

    try:
        run_workflow(project_id, resume)
    except Exception:  # run_workflow 已经记录日志并把项目置为 FAILED
        pass


# ---- sync 模式：避免在工作流节点内部递归推进同一个工作流 ----
_sync_queue: deque = deque()
_sync_state = threading.local()


def _drain_sync() -> None:
    if getattr(_sync_state, "draining", False):
        return
    _sync_state.draining = True
    try:
        while _sync_queue:
            fn, args = _sync_queue.popleft()
            fn(*args)
    finally:
        _sync_state.draining = False


def _submit(fn, *args) -> None:
    mode = get_settings().task_mode
    if mode == "thread":
        _executor().submit(fn, *args)
    else:
        _sync_queue.append((fn, args))
        _drain_sync()


def enqueue_workflow(project_id: int, resume: dict | None = None) -> None:
    if get_settings().task_mode == "celery":
        from app.tasks.jobs import advance_workflow

        advance_workflow.delay(project_id, resume)
    else:
        _submit(_run_workflow_safely, project_id, resume)


def enqueue_asset(asset_id: int, project_id: int) -> None:
    if get_settings().task_mode == "celery":
        from app.tasks.jobs import generate_asset

        generate_asset.delay(asset_id, project_id)
    else:
        _submit(_run_asset_with_retry, asset_id, project_id)
