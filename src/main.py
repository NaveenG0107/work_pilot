from contextlib import asynccontextmanager


from fastapi import FastAPI


from src.config import get_logger

# Register all ORM models with SQLAlchemy's registry at startup so that
# string-based relationship references (e.g. AuditLog.task -> "Task") resolve
# before the first query runs. Importing only a subset causes mapper
# configuration to fail with InvalidRequestError: ... failed to locate a name.
import src.audit.models  # noqa: F401
import src.auth.models  # noqa: F401
import src.comments.models  # noqa: F401
import src.custom_status.models  # noqa: F401
import src.favorite.models  # noqa: F401
import src.label.models  # noqa: F401
import src.organization.models  # noqa: F401
import src.project.models  # noqa: F401
import src.public.models  # noqa: F401
import src.serial.models  # noqa: F401
import src.sprint.models  # noqa: F401
import src.task.models  # noqa: F401
import src.user_story.models  # noqa: F401
import src.user_story_status.models  # noqa: F401

from src.organization.api import router as organization_router
# from src.jwt_auth.api import router as auth_router

API_PREFIX = "/api/v1"
from src.database import Base, engine
from src.auth.api import router as auth_router
from src.public.api import router as public_router
from src.project.api import router as project_router
from src.audit.api import router as audit_router
from src.label.api import router as label_router


logger = get_logger(__name__)

# Import every model *module* so they are registered on Base.metadata
# before we create tables. (The packages have no __init__.py, so importing
# the package alone does not load models.py.)
from src.audit import models as _audit_models  # noqa: E402,F401
from src.auth import models as _auth_models  # noqa: E402,F401
from src.comments import models as _comments_models  # noqa: E402,F401
from src.custom_status import models as _custom_status_models  # noqa: E402,F401
from src.favorite import models as _favorite_models  # noqa: E402,F401
from src.label import models as _label_models  # noqa: E402,F401
from src.organization import models as _organization_models  # noqa: E402,F401
from src.project import models as _project_models  # noqa: E402,F401
from src.public import models as _public_models  # noqa: E402,F401
from src.serial import models as _serial_models  # noqa: E402,F401
from src.sprint import models as _sprint_models  # noqa: E402,F401
from src.task import models as _task_models  # noqa: E402,F401
from src.user_story import models as _user_story_models  # noqa: E402,F401
from src.user_story_status import models as _user_story_status_models  # noqa: E402,F401


async def _create_tables() -> None:
    """Create all tables on startup (idempotent)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Work Pilot backend starting up")
    await _create_tables()
    logger.info("WorkPilot API starting up")
    yield
    logger.info("Work Pilot backend shutting down")
    logger.info("WorkPilot API shutting down")


from src.utils.exception_handlers import register_exception_handlers

app = FastAPI(
    title="Work Pilot Backend",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)

# app.include_router(auth_router)
app.include_router(organization_router, prefix=API_PREFIX)
app.include_router(auth_router)
app.include_router(
    public_router,
    prefix="/api/v1",
)

app.include_router(
    project_router,
    prefix="/api/v1",
)

app.include_router(
    audit_router,
    prefix="/api/v1",
)

app.include_router(
    label_router,
    prefix="/api/v1",
)
