"""项目状态机：所有状态变更都经过这里校验，非法流转直接抛错。

compare_and_set 用一条带条件的 UPDATE 实现"只有一个人能把状态从 A 改成 B"，
批量生图的多个 worker 同时完成时，靠它保证工作流只被唤醒一次。
"""
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models import Project, ProjectStatus as S

TRANSITIONS: dict[S, set[S]] = {
    S.CREATED: {S.PARSING, S.FAILED},
    S.PARSING: {S.PLAN_CONFIRMING, S.FAILED},
    S.PLAN_CONFIRMING: {S.MASTER_GENERATING, S.FAILED},
    S.MASTER_GENERATING: {S.MASTER_CONFIRMING, S.FAILED},
    S.MASTER_CONFIRMING: {S.MASTER_GENERATING, S.GENERATING, S.FAILED},
    S.GENERATING: {S.HUMAN_REVIEWING, S.FAILED},
    S.HUMAN_REVIEWING: {S.GENERATING, S.ARCHIVING, S.FAILED},
    S.ARCHIVING: {S.ARCHIVED, S.FAILED},
    S.ARCHIVED: set(),
    S.FAILED: set(),
}


class InvalidTransition(Exception):
    def __init__(self, current: S, target: S):
        super().__init__(f"非法状态流转: {current.value} -> {target.value}")
        self.current = current
        self.target = target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def transition(session: Session, project: Project, target: S) -> None:
    if not can_transition(project.status, target):
        raise InvalidTransition(project.status, target)
    project.status = target
    session.flush()


def compare_and_set(session: Session, project_id: int, expected: S, target: S) -> bool:
    """原子地把状态从 expected 改成 target，返回是否由本次调用改成功。"""
    if not can_transition(expected, target):
        raise InvalidTransition(expected, target)
    result = session.execute(
        update(Project).where(Project.id == project_id, Project.status == expected).values(status=target)
    )
    return result.rowcount == 1
