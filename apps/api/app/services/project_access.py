"""Shared ownership checks for active research projects."""

from app.exceptions import ProjectAccessDeniedError, ProjectNotFoundError
from app.models.project import Project


def check_access(
    project, user_id, *, missing=ProjectNotFoundError, denied=ProjectAccessDeniedError
):
    if project is None or project.is_deleted:
        raise missing()
    if project.owner_id is None or project.owner_id != user_id:
        raise denied()


async def owned_project(db, project_id, user_id) -> Project:
    project = await db.get(Project, project_id)
    check_access(project, user_id)
    return project
