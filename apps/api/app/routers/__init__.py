"""API routers."""

from app.routers.agent import router as agent_router
from app.routers.ai import router as ai_router
from app.routers.auth import router as auth_router
from app.routers.datasets import router as datasets_router
from app.routers.health import router as health_router
from app.routers.ml import router as ml_router
from app.routers.profiling import router as profiling_router
from app.routers.projects import router as projects_router
from app.routers.quality import router as quality_router
from app.routers.reporting import router as reporting_router
from app.routers.statistics import router as statistics_router
from app.routers.workflow import router as workflow_router

__all__ = [
    "agent_router",
    "ai_router",
    "auth_router",
    "datasets_router",
    "health_router",
    "ml_router",
    "profiling_router",
    "projects_router",
    "quality_router",
    "reporting_router",
    "statistics_router",
    "workflow_router",
]
