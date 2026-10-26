"""集中配置：全部从环境变量 / .env 读取，代码里不出现任何密钥。"""
from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Game Asset Agent"

    # ---- 数据库 / 队列 / 存储 ----
    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5434/game_asset_agent"
    redis_url: str = "redis://localhost:6381/0"
    # celery: 走 Redis + Celery Worker；thread: 进程内线程池（无 Redis 的本地开发）；sync: 同步顺序执行（测试用）
    task_mode: Literal["celery", "thread", "sync"] = "celery"
    thread_workers: int = 4

    storage_backend: Literal["minio", "local"] = "minio"
    minio_endpoint: str = "localhost:9010"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "game-assets"
    minio_secure: bool = False
    local_storage_dir: str = "./.data/storage"

    # ---- 阿里云百炼 ----
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com"
    llm_model: str = "qwen-plus"
    vl_model: str = "qwen-vl-max"
    image_model: str = "wan2.2-t2i-flash"
    image_size: str = "1024*1024"
    # 不填 API Key 时自动进入 mock 模式，可离线跑通整条流程
    ai_mock: bool | None = None

    # ---- 流程参数 ----
    wanx_rate_limit: str = "20/m"          # Celery 单 worker 调用万相的限流
    wanx_poll_interval: float = 3.0
    wanx_timeout: float = 180.0
    max_auto_retries: int = 2              # 自动审查不通过时最多重生成几次
    max_human_rounds: int = 3              # 人工驳回后最多回流几轮
    review_pass_score: int = 7             # 内容符合度、风格一致性的及格线（0-10）
    max_images_per_task: int = 16

    @property
    def mock_ai(self) -> bool:
        if self.ai_mock is not None:
            return self.ai_mock
        return not self.dashscope_api_key.strip()

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
