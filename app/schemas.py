from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import AssetStatus, MasterStatus, ProjectStatus


class TaskIn(BaseModel):
    hero: str = Field(min_length=1, max_length=100)
    role: str = Field(default="通用", max_length=100)
    action: str = Field(min_length=1, max_length=100)
    count: int = Field(ge=1, le=64)
    description: str = ""
    view: str = ""


class TaskOut(TaskIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class ProjectBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    status: ProjectStatus
    style_key: str | None
    created_at: datetime


class ProjectDetail(ProjectBrief):
    source_filename: str
    style_notes: str | None
    parse_issues: list[str]
    character_cards: dict[str, str]
    review_round: int
    error: str | None
    tasks: list[TaskOut]
    total_images: int = 0
    progress: dict[str, int] = {}


class PlanConfirm(BaseModel):
    style_key: str
    style_notes: str | None = Field(default=None, max_length=300)
    tasks: list[TaskIn] | None = Field(default=None, description="不传表示沿用拆解结果；传了则整体替换")


class MasterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    version: int
    style_key: str
    prompt: str
    negative_prompt: str
    seed: int
    status: MasterStatus
    feedback: str | None
    image_url: str
    created_at: datetime


class MasterConfirm(BaseModel):
    approved: bool
    feedback: str | None = Field(default=None, max_length=300)


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    asset_version: int
    passed: bool
    content_score: int | None
    style_score: int | None
    issues: list[str]
    comment: str | None
    reviewer: str | None
    created_at: datetime


class AssetOut(BaseModel):
    id: int
    task_id: int
    hero: str
    role: str
    action: str
    seq: int
    frame_desc: str
    prompt: str
    seed: int
    status: AssetStatus
    version: int
    image_url: str | None
    last_issues: list[str]
    human_feedback: str | None
    archive_key: str | None
    error: str | None
    reviews: list[ReviewOut] = []


class HumanReview(BaseModel):
    approved: bool
    comment: str | None = Field(default=None, max_length=300)
    reviewer: str = Field(default="reviewer", max_length=100)


class BatchHumanReview(HumanReview):
    asset_ids: list[int] = Field(min_length=1)


class StyleOut(BaseModel):
    key: str
    name: str
    description: str
    prefix: str
