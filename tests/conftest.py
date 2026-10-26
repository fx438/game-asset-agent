"""测试环境：SQLite + 同步任务 + 本地存储 + mock AI，不依赖 PostgreSQL / Redis / MinIO / 百炼。

必须在导入 app 之前设置环境变量（配置和数据库引擎在导入时创建）。
"""
import io
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="gaa-test-"))
os.environ.update(
    DATABASE_URL=f"sqlite:///{(_tmp / 'test.db').as_posix()}",
    TASK_MODE="sync",
    STORAGE_BACKEND="local",
    LOCAL_STORAGE_DIR=str(_tmp / "storage"),
    AI_MOCK="true",
    DASHSCOPE_API_KEY="",
    IMAGE_SIZE="256*256",
    MAX_AUTO_RETRIES="2",
    MAX_HUMAN_ROUNDS="3",
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def make_xlsx(rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
