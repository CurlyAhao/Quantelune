"""StatForge API — FastAPI application entry point."""

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import async_session_factory, initialize_database, shutdown_database
from app.desktop import ensure_desktop_user
from app.exceptions import StatForgeError
from app.research.supervisor import Supervisor, _await_terminal
from app.routers import (
    agent_router,
    ai_router,
    auth_router,
    datasets_router,
    health_router,
    ml_router,
    profiling_router,
    projects_router,
    quality_router,
    reporting_router,
    statistics_router,
    workflow_router,
)
from app.routers.analysis_jobs import router as analysis_jobs_router
from app.routers.draft_advice import router as draft_advice_router
from app.routers.journals import router as journals_router
from app.routers.materials import router as materials_router
from app.routers.media_tools import router as media_tools_router
from app.routers.multimodal import router as multimodal_router
from app.routers.multimodal_cases import router as media_cases_router
from app.routers.physiocgm import router as physiocgm_router
from app.routers.plots import router as plots_router
from app.routers.preparation import router as preparation_router
from app.routers.preview import router as preview_router
from app.routers.project_assistant import router as project_assistant_router
from app.routers.project_files import router as project_files_router
from app.routers.project_research import router as project_research_router
from app.routers.project_sources import router as project_sources_router
from app.routers.research import router as research_router
from app.routers.research_connections import router as research_connections_router
from app.routers.research_input import router as research_input_router
from app.routers.research_security import router as research_security_router
from app.routers.source_updates import router as source_updates_router
from app.routers.text_coding import router as text_coding_router
from app.routers.textgrid import router as textgrid_router
from app.routers.visual_analysis import router as visual_router
from app.routers.workspace import router as workspace_router
from app.routers.workspace_imports import router as workspace_imports_router
from app.services import storage_service
from app.startup_diagnostics import note

_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
if settings.log_path:
    _log_directory = Path(settings.log_path).resolve()
    _log_directory.mkdir(parents=True, exist_ok=True)
    _log_handlers.append(
        logging.FileHandler(_log_directory / "statforge-sidecar.log", encoding="utf-8")
    )

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger("statforge")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and shutdown lifecycle."""
    note("lifespan_enter")
    # Startup
    if settings.environment == "production":
        if settings.jwt_secret_key == "statforge-dev-jwt-secret-change-in-production":
            logger.critical("Production JWT secret must not be the default value.")
            raise SystemExit("Set JWT_SECRET_KEY in production.")
        settings.cookie_secure = True
    try:
        supervisor = None
        planner_supervisor = None
        reader_supervisor = None
        reviewer_supervisor = None
        plan_coordinator = None
        note("database_enter")
        await initialize_database()
        note("database_ready")
        if settings.desktop_mode:
            note("desktop_user_enter")
            async with async_session_factory() as session:
                await ensure_desktop_user(session)
                await session.commit()
            note("desktop_user_ready")
        try:
            storage_service.ensure_bucket()
        except Exception as exc:
            logger.warning("Storage bucket init skipped: %s", exc)
        note("storage_ready")
        from app.workflow.output_recovery import reconcile_startup

        note("workflow_output_recovery_enter")
        await reconcile_startup(async_session_factory)
        note("workflow_output_recovery_ready")
        from app.services.research_gateway_service import get_gateway

        get_gateway().configure_runtime_catalog(
            Path(settings.app_data_path) if settings.app_data_path else None
        )
        note("supervisors_enter")
        supervisor = Supervisor(async_session_factory)
        from app.research.formal_tools import runtime_factory

        supervisor.formal_runtime = runtime_factory(
            async_session_factory, supervisor._business_work
        )
        supervisor.start()
        app.state.research_supervisor = supervisor
        from app.research.planner_supervisor import PlannerSupervisor

        planner_supervisor = PlannerSupervisor(async_session_factory)
        planner_supervisor.start()
        app.state.planner_supervisor = planner_supervisor
        from app.research.reader_supervisor import ReaderSupervisor

        reader_supervisor = ReaderSupervisor(async_session_factory)
        reader_supervisor.start()
        app.state.reader_supervisor = reader_supervisor
        from app.research.reviewer_supervisor import ReviewerSupervisor

        reviewer_supervisor = ReviewerSupervisor(async_session_factory)
        reviewer_supervisor.start()
        app.state.reviewer_supervisor = reviewer_supervisor
        from app.research.plan_coordinator import PlanCoordinator

        plan_coordinator = PlanCoordinator(async_session_factory)
        plan_coordinator.start()
        app.state.plan_coordinator = plan_coordinator
        note("supervisors_ready")
        note("lifespan_ready")
        yield
    finally:
        from app.services.redis_service import close_redis

        try:

            async def drain_supervisors():
                from app.services.analysis_jobs import close as close_analysis_jobs
                from app.services.plots import close as close_plots
                from app.services.project_assistant import close as close_project_assistant

                await close_project_assistant()
                await close_plots()
                await close_analysis_jobs()
                coordinator_error = None
                if plan_coordinator is not None:
                    try:
                        await plan_coordinator.close()
                    except BaseException as error:
                        coordinator_error = error
                results = await asyncio.gather(
                    *(
                        item.close()
                        for item in (
                            reviewer_supervisor,
                            reader_supervisor,
                            planner_supervisor,
                            supervisor,
                        )
                        if item is not None
                    ),
                    return_exceptions=True,
                )
                errors = [result for result in results if isinstance(result, BaseException)]
                if coordinator_error is not None:
                    errors.insert(0, coordinator_error)
                if len(errors) == 1:
                    raise errors[0]
                if errors:
                    raise BaseExceptionGroup("Research shutdown failures", errors)

            await _await_terminal(
                asyncio.create_task(drain_supervisors(), name="research-shutdown")
            )
        finally:
            try:
                await close_redis()
            finally:
                await shutdown_database()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "StatForge — An Open-Source AI-Assisted Applied Statistics "
        "and Machine Learning Workbench API"
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

if settings.desktop_mode:
    from app.sidecar_middleware import SidecarAuthMiddleware

    app.add_middleware(SidecarAuthMiddleware)

# CORS — credentials required for HttpOnly cookie auth
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Session-Token"],
)


@app.exception_handler(StatForgeError)
async def statforge_error_handler(request: Request, exc: StatForgeError) -> JSONResponse:
    """Uniform error envelope for all StatForge exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail, "error_code": exc.error_code},
    )


# Routers
app.include_router(health_router)
app.include_router(agent_router)
app.include_router(ai_router)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(datasets_router)
app.include_router(profiling_router)
app.include_router(quality_router)
app.include_router(statistics_router)
app.include_router(ml_router)
app.include_router(workflow_router)
app.include_router(reporting_router)
app.include_router(preview_router)
app.include_router(workspace_router)
app.include_router(project_files_router)
app.include_router(research_connections_router)
app.include_router(research_input_router)
app.include_router(project_research_router)
app.include_router(project_assistant_router)
app.include_router(plots_router)
app.include_router(draft_advice_router)
app.include_router(preparation_router)
app.include_router(materials_router)
app.include_router(workspace_imports_router)

app.include_router(multimodal_router)

app.include_router(project_sources_router)

app.include_router(physiocgm_router)

app.include_router(media_cases_router)

app.include_router(media_tools_router)

app.include_router(visual_router)
app.include_router(text_coding_router)
app.include_router(journals_router)

app.include_router(textgrid_router)
app.include_router(analysis_jobs_router)
app.include_router(source_updates_router)
app.include_router(research_router)
app.include_router(research_security_router)
