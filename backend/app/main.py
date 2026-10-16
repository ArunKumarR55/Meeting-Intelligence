import logging
import asyncio
from pathlib import Path
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1.endpoints import audio, calendar, followthrough, meetings, rag, workflows
from app.api.v1.endpoints.assemblyai import router as assemblyai_router
from app.core.config import settings
from app.core.database import Base, engine
from app.db.models import domain as domain_models  # noqa: F401
from app.services.workflows.reminders import send_due_reminders


logger = logging.getLogger(__name__)


async def _reminder_loop() -> None:
    """Run the email reminder digest on a bounded interval in the API process."""
    interval_seconds = settings.reminders_interval_minutes * 60
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await asyncio.to_thread(send_due_reminders)
            if result["sent_digests"] or result["failed_digests"]:
                logger.info(
                    "Follow-through reminder cycle complete: sent=%d failed=%d",
                    result["sent_digests"],
                    result["failed_digests"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Follow-through reminder cycle failed")


@asynccontextmanager
async def lifespan(_application: FastAPI) -> AsyncIterator[None]:
    """Initialize the local database schema before serving requests."""
    try:
        Base.metadata.create_all(bind=engine)
        alembic_config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        alembic_config.set_main_option(
            "sqlalchemy.url",
            engine.url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        command.upgrade(alembic_config, "head")
    except Exception:
        logger.exception("Failed to initialize the AMIP database schema")
        raise
    reminder_task = (
        asyncio.create_task(_reminder_loop(), name="amip-followthrough-reminders")
        if settings.reminders_enabled
        else None
    )
    try:
        yield
    finally:
        if reminder_task is not None:
            reminder_task.cancel()
            with suppress(asyncio.CancelledError):
                await reminder_task


app = FastAPI(
    title=settings.app_name,
    description=(
        "Autonomous Meeting Intelligence Platform API with local manual-audio processing "
        "and AssemblyAI/Gemini-powered live meeting analysis."
    ),
    version="0.1.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meetings.router, prefix="/api/v1/meetings", tags=["meetings"])
app.include_router(audio.router, prefix="/api/v1/audio", tags=["audio"])
app.include_router(rag.router, prefix="/api/v1/rag", tags=["corporate memory"])
app.include_router(workflows.router, prefix="/api/v1/workflows", tags=["workflows"])
app.include_router(followthrough.router, prefix="/api/v1/follow-through", tags=["follow-through"])
app.include_router(calendar.router, prefix="/api/v1/calendar", tags=["calendar"])
app.include_router(assemblyai_router, prefix="/api/assemblyai", tags=["AssemblyAI"])


@app.get("/health", tags=["health"], summary="Check application health")
def health() -> dict[str, str]:
    """Report API and database availability without exposing internal details."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "healthy"}
