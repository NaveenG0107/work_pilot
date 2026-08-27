from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.config import get_logger
from src.organization.api import router as organization_router
# from src.jwt_auth.api import router as auth_router

logger = get_logger(__name__)

API_PREFIX = "/api/v1"

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("FastAPI JWT Auth starting up")
    yield
    logger.info("FastAPI JWT Auth shutting down")


app = FastAPI(
    title="FastAPI JWT Auth",
    version="0.1.0",
    lifespan=lifespan,
)

# app.include_router(auth_router)
app.include_router(organization_router, prefix=API_PREFIX)


@app.get("/health_check")
def health_check():
    return {"message": "FastAPI JWT Auth is running"}
