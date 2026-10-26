import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api import misc, projects, review
from app.config import get_settings
from app.db import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    s = get_settings()
    logging.getLogger("app").info("启动完成：task_mode=%s storage=%s mock_ai=%s", s.task_mode, s.storage_backend, s.mock_ai)
    yield


app = FastAPI(
    title=get_settings().app_name,
    version="1.0.0",
    description="游戏资源图片多 Agent 系统：需求拆解 → 风格母版 → 批量生图 → 自动审查 + 人工审核 → 资源入库",
    lifespan=lifespan,
)
for r in (projects.router, review.router, misc.router):
    app.include_router(r, prefix="/api")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", tags=["通用"])
def health():
    return {"status": "ok"}
