from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from app.agents.styles import STYLES
from app.config import get_settings
from app.schemas import StyleOut
from app.storage import get_storage, guess_type

router = APIRouter(tags=["通用"])

ALLOWED_PREFIXES = ("projects/", "library/")


@router.get("/styles", response_model=list[StyleOut], summary="可选的风格母版模板")
def list_styles():
    return [StyleOut(key=s.key, name=s.name, description=s.description, prefix=s.prefix) for s in STYLES.values()]


@router.get("/system", summary="运行模式")
def system_info():
    s = get_settings()
    return {"mock_ai": s.mock_ai, "demo_mode": s.demo_mode, "task_mode": s.task_mode, "storage": s.storage_backend,
            "llm_model": s.llm_model, "vl_model": s.vl_model, "image_model": s.image_model,
            "images_per_task_limit": s.images_per_task_limit, "total_images_limit": s.total_images_limit,
            "upload_limit_mb": s.upload_limit_bytes // 1024 // 1024}


@router.get("/files/{key:path}", summary="读取对象存储中的文件")
def get_file(key: str):
    if not key.startswith(ALLOWED_PREFIXES) or ".." in key:
        raise HTTPException(404)
    storage = get_storage()
    if not storage.exists(key):
        raise HTTPException(404)
    return Response(storage.get_bytes(key), media_type=guess_type(key), headers={"Cache-Control": "private, max-age=3600"})
