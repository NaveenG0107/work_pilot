from contextlib import asynccontextmanager
from fastapi import FastAPI

from fastapi import FastAPI  # type: ignore

from src.config import get_logger

API_PREFIX = "/api/v1"
from src.database import Base, engine
API_PREFIX = "/api/v1"

from src.sprint.api import router as sprint_router
from src.audit.api import router as audit_router
from src.auth.api import router as auth_router
from src.comments.api import router as comments_router
from src.custom_status.api import router as custom_status_router
from src.label.api import router as label_router
from src.organization.api import router as organization_router
from src.project.api import router as project_router
from src.audit.api import router as audit_router
from src.user_story.api import router as user_story_router
from src.label.api import router as label_router
from src.organization.api import router as organization_router
from src.user_story_status.api import router as user_story_status_router

from src.utils.exception_handlers import register_exception_handlers
from src.public.api import router as public_router
from src.task.api import router as task_router

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


from sqlalchemy import text
from src.audit import models as _audit_models  # noqa: F401
from src.auth import models as _auth_models  # noqa: F401
from src.comments import models as _comments_models  # noqa: F401
from src.custom_status import models as _custom_status_models  # noqa: F401
from src.favorite import models as _favorite_models  # noqa: F401
from src.label import models as _label_models  # noqa: F401
from src.organization import models as _organization_models  # noqa: F401
from src.project import models as _project_models  # noqa: F401
from src.public import models as _public_models  # noqa: F401
from src.serial import models as _serial_models  # noqa: F401
from src.sprint import models as _sprint_models  # noqa: F401
from src.task import models as _task_models  # noqa: F401
from src.user_story import models as _user_story_models  # noqa: F401
from src.user_story_status import models as _user_story_status_models  # noqa: F401


async def _create_tables() -> None:
    """Create all tables on startup (idempotent)."""
    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE SEQUENCE IF NOT EXISTS global_work_item_serial_seq START WITH 1 INCREMENT BY 1"))
        except Exception:
            pass
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Work Pilot backend starting up")
    await _create_tables()
    yield
    logger.info("Work Pilot backend shutting down")



app = FastAPI(
    title="Work Pilot Backend",
    version="0.1.0",
    lifespan=lifespan,
)

register_exception_handlers(app)


app.include_router(auth_router)
app.include_router(sprint_router, prefix=API_PREFIX)
app.include_router(public_router, prefix=API_PREFIX)
app.include_router(audit_router, prefix=API_PREFIX)
app.include_router(organization_router, prefix=API_PREFIX)
app.include_router(auth_router)
app.include_router(user_story_router)
app.include_router(public_router)
app.include_router(project_router)
app.include_router(audit_router)
app.include_router(label_router)
app.include_router(user_story_status_router)


@app.get("/health_check")
def health_check():
    return {"message": "Work Pilot backend is running"}


app.include_router(project_router, prefix=API_PREFIX)
app.include_router(task_router, prefix=API_PREFIX)
app.include_router(label_router, prefix=API_PREFIX)
app.include_router(comments_router, prefix=API_PREFIX)
app.include_router(custom_status_router, prefix=API_PREFIX)