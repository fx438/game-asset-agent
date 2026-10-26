from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("game_asset_agent", broker=settings.redis_url, backend=settings.redis_url, include=["app.tasks.jobs"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    task_acks_late=True,                 # 执行完才确认，worker 崩溃时任务会重新投递
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,        # 生图任务耗时长，避免一个 worker 囤积任务
    task_routes={
        "jobs.generate_asset": {"queue": "images"},
        "jobs.advance_workflow": {"queue": "workflow"},
    },
)
