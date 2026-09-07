from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from src.config import CORS_ORIGINS, get_logger

API_PREFIX = "/api/v1"

from src.audit.api import router as audit_router
from src.auth.api import router as auth_router
from src.comments.api import router as comments_router
from src.custom_status.api import router as custom_status_router
from src.favorite.api import router as favorite_router
from src.dashboard.api import router as dashboard_router
from src.label.api import router as label_router
from src.organization.api import router as organization_router
from src.project.api import router as project_router
from src.public.api import router as public_router
from src.search.api import router as search_router
from src.sprint.api import router as sprint_router
from src.task.api import router as task_router
from src.user_story.api import router as user_story_router
from src.user_story_status.api import router as user_story_status_router
from src.utils.exception_handlers import register_exception_handlers
from src.work_item.api import router as work_item_router

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Work Pilot backend starting up")
    yield
    logger.info("Work Pilot backend shutting down")



app = FastAPI(
    title="Work Pilot Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

register_exception_handlers(app)

app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(sprint_router, prefix=API_PREFIX)
app.include_router(public_router, prefix=API_PREFIX)
app.include_router(audit_router, prefix=API_PREFIX)
app.include_router(organization_router, prefix=API_PREFIX)
app.include_router(user_story_router, prefix=API_PREFIX)
app.include_router(project_router, prefix=API_PREFIX)
app.include_router(label_router, prefix=API_PREFIX)
app.include_router(user_story_status_router, prefix=API_PREFIX)
app.include_router(comments_router, prefix=API_PREFIX)
app.include_router(favorite_router, prefix=API_PREFIX)
app.include_router(custom_status_router, prefix=API_PREFIX)
app.include_router(dashboard_router, prefix=API_PREFIX)
app.include_router(task_router, prefix=API_PREFIX)
app.include_router(work_item_router, prefix=API_PREFIX)
app.include_router(search_router, prefix=API_PREFIX)


@app.get("/health_check")
def health_check():
    return {"message": "Work Pilot backend is running"}