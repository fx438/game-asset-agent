from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.projects import file_url, get_project_or_404, require_status
from app.db import get_db
from app.models import Asset, AssetStatus, ProjectStatus as PS, ReviewRecord
from app.schemas import AssetOut, BatchHumanReview, HumanReview, ReviewOut
from app.tasks import dispatch

router = APIRouter(tags=["资源与审核"])

# 人工审核阶段可以做决定的状态（自动审查通过的、自动重试用尽的、生成失败的都交给人裁决）
DECIDABLE = {AssetStatus.WAITING_HUMAN, AssetStatus.AUTO_FAILED, AssetStatus.ERROR, AssetStatus.APPROVED, AssetStatus.REJECTED}
UNDECIDED = {AssetStatus.WAITING_HUMAN, AssetStatus.AUTO_FAILED, AssetStatus.ERROR}


def to_out(asset: Asset, reviews: list[ReviewRecord] | None = None) -> AssetOut:
    t = asset.task
    return AssetOut(
        id=asset.id, task_id=asset.task_id, hero=t.hero, role=t.role, action=t.action, seq=asset.seq,
        frame_desc=asset.frame_desc, prompt=asset.prompt, seed=asset.seed, status=asset.status, version=asset.version,
        image_url=file_url(asset.archive_key or asset.image_key), last_issues=asset.last_issues or [],
        human_feedback=asset.human_feedback, archive_key=asset.archive_key, error=asset.error,
        reviews=[ReviewOut.model_validate(r) for r in (reviews or [])],
    )


@router.get("/projects/{project_id}/assets", response_model=list[AssetOut], summary="资源列表（可按状态筛选）")
def list_assets(project_id: int, status: list[AssetStatus] | None = Query(default=None), db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    stmt = select(Asset).options(selectinload(Asset.task)).where(Asset.project_id == project_id)
    if status:
        stmt = stmt.where(Asset.status.in_(status))
    return [to_out(a) for a in db.scalars(stmt.order_by(Asset.task_id, Asset.seq)).all()]


@router.get("/assets/{asset_id}", response_model=AssetOut, summary="资源详情（含全部审查记录）")
def get_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, f"资源 {asset_id} 不存在")
    reviews = db.scalars(select(ReviewRecord).where(ReviewRecord.asset_id == asset_id).order_by(ReviewRecord.created_at.desc())).all()
    return to_out(asset, reviews)


def _apply_review(db: Session, asset: Asset, body: HumanReview) -> None:
    if asset.status not in DECIDABLE:
        raise HTTPException(409, f"资源 {asset.id} 当前状态 {asset.status.value} 不能审核")
    if body.approved and asset.status == AssetStatus.ERROR:
        raise HTTPException(400, f"资源 {asset.id} 生成失败没有图片，只能驳回重新生成")
    if not body.approved and not (body.comment or "").strip():
        raise HTTPException(400, "驳回时请填写批注，批注会回流给提示词扩写 Agent")
    asset.status = AssetStatus.APPROVED if body.approved else AssetStatus.REJECTED
    asset.human_feedback = None if body.approved else body.comment
    db.add(ReviewRecord(asset_id=asset.id, asset_version=asset.version, kind="human", passed=body.approved,
                        issues=[] if body.approved else [body.comment], comment=body.comment, reviewer=body.reviewer))


@router.post("/assets/{asset_id}/review", response_model=AssetOut, summary="人工审核单张：通过 / 驳回")
def review_asset(asset_id: int, body: HumanReview, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(404, f"资源 {asset_id} 不存在")
    require_status(get_project_or_404(db, asset.project_id), PS.HUMAN_REVIEWING)
    _apply_review(db, asset, body)
    db.flush()
    return to_out(asset)


@router.post("/projects/{project_id}/review/batch", summary="人工批量审核")
def review_batch(project_id: int, body: BatchHumanReview, db: Session = Depends(get_db)):
    require_status(get_project_or_404(db, project_id), PS.HUMAN_REVIEWING)
    assets = db.scalars(select(Asset).where(Asset.project_id == project_id, Asset.id.in_(body.asset_ids))).all()
    if len(assets) != len(set(body.asset_ids)):
        raise HTTPException(400, "部分资源不属于该项目")
    for asset in assets:
        _apply_review(db, asset, body)
    return {"updated": len(assets)}


@router.post("/projects/{project_id}/review/submit", status_code=202, summary="提交本轮人工审核结果")
def submit_review(project_id: int, db: Session = Depends(get_db)):
    project = get_project_or_404(db, project_id)
    require_status(project, PS.HUMAN_REVIEWING)
    pending = db.scalars(select(Asset.id).where(Asset.project_id == project_id, Asset.status.in_(UNDECIDED))).all()
    if pending:
        raise HTTPException(409, f"还有 {len(pending)} 张图未审核：{pending[:20]}")
    rejected = db.scalar(select(Asset.id).where(Asset.project_id == project_id, Asset.status == AssetStatus.REJECTED).limit(1))
    # 带上轮次号：重复提交的过期唤醒会被工作流丢弃
    dispatch.enqueue_workflow(project_id, {"type": "human_review", "token": project.review_round})
    return {"message": "驳回的图片将带批注重新生成" if rejected else "全部通过，正在归档到资源库"}


@router.get("/projects/{project_id}/library", response_model=list[AssetOut], summary="已入库资源")
def library(project_id: int, db: Session = Depends(get_db)):
    get_project_or_404(db, project_id)
    stmt = (select(Asset).options(selectinload(Asset.task))
            .where(Asset.project_id == project_id, Asset.status == AssetStatus.ARCHIVED).order_by(Asset.archive_key))
    return [to_out(a) for a in db.scalars(stmt).all()]
