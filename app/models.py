"""业务表。

索引设计说明：
- projects(status)：看板按状态筛选项目。
- asset_tasks(project_id)：按项目取任务清单。
- style_masters(project_id, version) 唯一：同一项目的母版按版本递增。
- assets(project_id, status) 复合索引：进度统计、"待人工审核列表"、生成屏障判断都按这两列查。
- assets(task_id, seq) 唯一：一个任务的第 N 张图只有一条记录，重生成只更新版本号，避免重复插入。
- assets(archive_key) 唯一：入库路径不能冲突。
- review_records(asset_id, created_at)：按图片取审查历史。
"""
import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


class ProjectStatus(str, enum.Enum):
    CREATED = "CREATED"
    PARSING = "PARSING"                    # 需求拆解中
    PLAN_CONFIRMING = "PLAN_CONFIRMING"    # 等待用户确认任务清单 + 选择风格
    MASTER_GENERATING = "MASTER_GENERATING"
    MASTER_CONFIRMING = "MASTER_CONFIRMING"  # 等待用户确认风格母版
    GENERATING = "GENERATING"              # 批量生图 + 自动审查
    HUMAN_REVIEWING = "HUMAN_REVIEWING"    # 等待人工审核
    ARCHIVING = "ARCHIVING"
    ARCHIVED = "ARCHIVED"
    FAILED = "FAILED"


class AssetStatus(str, enum.Enum):
    PENDING = "PENDING"
    GENERATING = "GENERATING"
    AUTO_REVIEWING = "AUTO_REVIEWING"
    WAITING_HUMAN = "WAITING_HUMAN"        # 自动审查通过，等人工
    AUTO_FAILED = "AUTO_FAILED"            # 自动重试用尽仍不合格，交人工裁决
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ARCHIVED = "ARCHIVED"
    ERROR = "ERROR"                        # 接口调用彻底失败


class MasterStatus(str, enum.Enum):
    READY = "READY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def _now_col():
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[ProjectStatus] = mapped_column(Enum(ProjectStatus, native_enum=False, length=32), default=ProjectStatus.CREATED, index=True)
    source_filename: Mapped[str] = mapped_column(String(255))
    source_key: Mapped[str] = mapped_column(String(500))
    style_key: Mapped[str | None] = mapped_column(String(50))
    style_notes: Mapped[str | None] = mapped_column(Text)
    # 每个"英雄|角色"一张外观设定卡，所有动作复用，保证同一角色跨动作外观一致
    character_cards: Mapped[dict] = mapped_column(JsonType, default=dict)
    parse_issues: Mapped[list] = mapped_column(JsonType, default=list)
    review_round: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    tasks: Mapped[list["AssetTask"]] = relationship(back_populates="project", order_by="AssetTask.sort_order", cascade="all, delete-orphan")


class AssetTask(Base):
    """需求拆解的一行：某英雄 / 某角色 / 某动作 / 几张。"""

    __tablename__ = "asset_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    hero: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    count: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    view: Mapped[str] = mapped_column(String(50), default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    project: Mapped[Project] = relationship(back_populates="tasks")


class StyleMaster(Base):
    __tablename__ = "style_masters"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_master_project_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    style_key: Mapped[str] = mapped_column(String(50))
    prompt: Mapped[str] = mapped_column(Text)
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer)
    image_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[MasterStatus] = mapped_column(Enum(MasterStatus, native_enum=False, length=16), default=MasterStatus.READY)
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()


class Asset(Base):
    """一张待生成 / 已生成的图片。"""

    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("task_id", "seq", name="uq_asset_task_seq"),
        Index("ix_assets_project_status", "project_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    task_id: Mapped[int] = mapped_column(ForeignKey("asset_tasks.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    frame_desc: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    negative_prompt: Mapped[str] = mapped_column(Text, default="")
    seed: Mapped[int] = mapped_column(Integer, default=0)
    image_key: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus, native_enum=False, length=20), default=AssetStatus.PENDING)
    version: Mapped[int] = mapped_column(Integer, default=0)       # 每生成一次 +1
    attempts: Mapped[int] = mapped_column(Integer, default=0)      # 本轮自动重试次数
    last_issues: Mapped[list] = mapped_column(JsonType, default=list)
    human_feedback: Mapped[str | None] = mapped_column(Text)
    # 人工批注转成的提示词修正，后续自动重试的修正叠加在它之上，避免人工意见被覆盖
    human_fix: Mapped[str] = mapped_column(Text, default="")
    archive_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    task: Mapped[AssetTask] = relationship()


class ReviewRecord(Base):
    __tablename__ = "review_records"
    __table_args__ = (Index("ix_review_asset_created", "asset_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"))
    asset_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))   # auto / human
    passed: Mapped[bool] = mapped_column(Boolean)
    content_score: Mapped[int | None] = mapped_column(Integer)
    style_score: Mapped[int | None] = mapped_column(Integer)
    issues: Mapped[list] = mapped_column(JsonType, default=list)
    comment: Mapped[str | None] = mapped_column(Text)
    reviewer: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _now_col()
