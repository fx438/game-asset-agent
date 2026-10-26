from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.styles import STYLES
from app.config import get_settings
from app.db import get_db
from app.models import Asset, Project, ProjectStatus as PS, StyleMaster
from app.parsers.document_parser import SUPPORTED_SUFFIXES
from app.schemas import MasterConfirm, MasterOut, PlanConfirm, ProjectBrief, ProjectDetail
from app.storage import get_storage
from app.tasks import dispatch

router = APIRouter(prefix="/projects", tags=["项目流程"])

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def file_url(key: str | None) -> str | None:
    return f"/api/files/{key}" if key else None


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"项目 {project_id} 不存在")
    return project


def require_status(project: Project, expected: PS) -> None:
    if project.status != expected:
        raise HTTPException(409, f"当前项目状态为 {project.status.value}，此操作需要状态 {expected.value}")


@router.post("", response_model=ProjectBrief, status_code=201, summary="上传需求文档并启动流程")
async def create_project(name: str = Form(..., min_length=1, max_length=200), file: UploadFile = File(...), db: Session = Depends(get_db)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(400, f"不支持的文件类型 {suffix or '(无后缀)'}，支持：{', '.join(sorted(SUPPORTED_SUFFIXES))}")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(400, "文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 20MB")

    project = Project(name=name, source_filename=file.filename, source_key="")
    db.add(project)
    db.flush()
    project.source_key = get_storage().put_bytes(f"projects/{project.id}/source/requirement{suffix}", data)
    db.commit()
    dispatch.enqueue_workflow(project.id)
    db.refresh(project)
    return project


@router.get("", response_model=list[ProjectBrief], summary="项目列表")
def list_projects(db: Session = Depends(get_db)):
    return db.scalars(select(Project).order_by(Project.id.desc()).limit(200)).all()


@router.get("/{project_id}", response_model=ProjectDetail, summary="项目详情与进度")
def get_project(project_id: int, db: Session = Depends(get_db)):
    project = db.scalar(select(Project).options(selectinload(Project.tasks)).where(Project.id == project_id))
    if project is None:
        raise HTTPException(404, f"项目 {project_id} 不存在")
    rows = db.execute(select(Asset.status, func.count()).where(Asset.project_id == project_id).group_by(Asset.status)).all()
    detail = ProjectDetail.model_validate(project)
    detail.total_images = sum(t.count for t in project.tasks)
    detail.progress = {status.value: n for status, n in rows}
    return detail


@router.post("/{project_id}/plan", status_code=202, summary="确认任务清单并选择风格母版")
def confirm_plan(project_id: int, body: PlanConfirm, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    require_status(project, PS.PLAN_CONFIRMING)
    if body.style_key not in STYLES:
        raise HTTPException(400, f"未知风格 {body.style_key}")
    if body.tasks is not None and not body.tasks:
        raise HTTPException(400, "任务清单不能为空")
    limit = get_settings().max_images_per_task
    if body.tasks and any(t.count > limit for t in body.tasks):
        raise HTTPException(400, f"单个任务最多 {limit} 张")
    dispatch.enqueue_workflow(project_id, {"type": "confirm_plan", **body.model_dump(mode="json")})
    return {"message": "已确认，正在生成风格母版"}


@router.get("/{project_id}/masters", response_model=list[MasterOut], summary="风格母版历史版本")
def list_masters(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    masters = db.scalars(select(StyleMaster).where(StyleMaster.project_id == project_id).order_by(StyleMaster.version.desc())).all()
    fields = [f for f in MasterOut.model_fields if f != "image_url"]
    return [MasterOut(**{f: getattr(m, f) for f in fields}, image_url=file_url(m.image_key)) for m in masters]


@router.post("/{project_id}/master", status_code=202, summary="确认或驳回风格母版")
def confirm_master(project_id: int, body: MasterConfirm, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    require_status(project, PS.MASTER_CONFIRMING)
    if not body.approved and not (body.feedback or "").strip():
        raise HTTPException(400, "驳回母版时请填写修改意见，以便重新生成")
    version = db.scalar(select(func.max(StyleMaster.version)).where(StyleMaster.project_id == project_id))
    # 带上母版版本号：针对旧版本母版的重复确认会被工作流丢弃
    dispatch.enqueue_workflow(project_id, {"type": "confirm_master", "token": version, **body.model_dump()})
    return {"message": "风格已锁定，开始批量生图" if body.approved else "已按意见重新生成母版"}
