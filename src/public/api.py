import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, status  # type: ignore
from fastapi.responses import JSONResponse  # type: ignore
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_logger
from src.database import get_db, get_redis
from src.public.schema import (
    CountriesResponse,
    FullHealthResponse,
    HealthDependencies,
    HealthResponse,
)
from src.public.service import CountryCache, PublicService

logger = get_logger(__name__)

router = APIRouter()
country_cache = CountryCache(ttl=timedelta(hours=24))


def get_public_service(db: AsyncSession = Depends(get_db)) -> PublicService:
    return PublicService(db=db, country_cache=country_cache)


@router.get("/health", response_model=HealthResponse | FullHealthResponse, tags=["Public"])
async def health_check(
    full: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
):
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if not full:
        return HealthResponse(
            status="healthy",
            version="v1",
            timestamp=timestamp,
        )

    database_result, redis_result = await asyncio.gather(
        db.execute(text("SELECT 1")),
        redis.ping(),
        return_exceptions=True,
    )

    database_status = (
        "unhealthy" if isinstance(database_result, BaseException) else "healthy"
    )
    redis_status = (
        "unhealthy" if isinstance(redis_result, BaseException) else "healthy"
    )

    if database_status == "unhealthy":
        logger.error("Database health check failed: %s", database_result)
    if redis_status == "unhealthy":
        logger.error("Redis health check failed: %s", redis_result)

    is_healthy = database_status == "healthy" and redis_status == "healthy"
    response = FullHealthResponse(
        status="healthy" if is_healthy else "unhealthy",
        version="v1",
        timestamp=timestamp,
        dependencies=HealthDependencies(
            database=database_status,
            redis=redis_status,
        ),
    )

    if not is_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )

    return response


@router.get("/countries", response_model=CountriesResponse, tags=["Lookup"])
async def get_all_countries(
    name: str | None = Query(default=None, description="Filter countries by name"),
    service: PublicService = Depends(get_public_service),
):
    try:
        countries = await service.get_countries(name=name)

        logger.info(
            "Countries retrieved successfully: count=%s filter=%s",
            len(countries),
            name,
        )

        return CountriesResponse(
            success=True,
            status_code=status.HTTP_200_OK,
            message="Countries retrieved successfully",
            data=countries,
        )

    except RuntimeError as exc:
        logger.error(
            "Failed to retrieve countries: %s",
            exc,
        )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "status_code": 500,
                    "message": str(exc),
                },
            },
        )

    except Exception:
        logger.exception("Unexpected error while retrieving countries")

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "status_code": 500,
                    "message": "An unexpected error occurred",
                },
            },
        )
