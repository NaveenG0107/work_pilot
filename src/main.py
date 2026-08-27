from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.config import get_logger
from src.public.api import router as public_router
from src.project.api import router as project_router
# from src.audit.api import router as audit_router



logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("WorkPilot API starting up")
    yield
    logger.info("WorkPilot API shutting down")


app = FastAPI(
    title="WorkPilot API",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(
    public_router,
    prefix="/api/v1",
)

app.include_router(
    project_router,
    prefix="/api/v1",
)

# app.include_router(
#     audit_router,
#     prefix="/api/v1",
# )
