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
    max_upload_mb: int = 20

    # ---- 在线演示 ----
    # 开启后无条件走 mock：即使环境里配了 Key 也会被清空，不会调用任何付费接口；同时收紧单次用量
    demo_mode: bool = False
    demo_max_images_per_task: int = 8
    demo_max_total_images: int = 32
    demo_max_upload_mb: int = 2

    def model_post_init(self, __context) -> None:
        if self.demo_mode:
            self.dashscope_api_key = ""

    @property
    def mock_ai(self) -> bool:
        if self.demo_mode:
            return True
        if self.ai_mock is not None:
            return self.ai_mock
        return not self.dashscope_api_key.strip()

    @property
    def images_per_task_limit(self) -> int:
        if self.demo_mode:
            return min(self.max_images_per_task, self.demo_max_images_per_task)
        return self.max_images_per_task

    @property
    def total_images_limit(self) -> int | None:
        return self.demo_max_total_images if self.demo_mode else None

    @property
    def upload_limit_bytes(self) -> int:
        return (self.demo_max_upload_mb if self.demo_mode else self.max_upload_mb) * 1024 * 1024

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
