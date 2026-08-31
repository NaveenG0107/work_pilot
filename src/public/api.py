from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from src.config import get_logger
from src.database import get_db
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


def get_public_service(db: Session = Depends(get_db)) -> PublicService:
    return PublicService(db=db, country_cache=country_cache)


@router.get("/health", response_model=HealthResponse | FullHealthResponse, tags=["Public"])
def health_check(full: bool = Query(default=False), db: Session = Depends(get_db)):
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    if not full:
        return HealthResponse(
            status="healthy",
            version="v1",
            timestamp=timestamp,
        )

    try:
        db.execute(text("SELECT 1"))

        return FullHealthResponse(
            status="healthy",
            version="v1",
            timestamp=timestamp,
            dependencies=HealthDependencies(database="healthy"),
        )

    except SQLAlchemyError:
        logger.exception("Database health check failed")

        response = FullHealthResponse(
            status="unhealthy",
            version="v1",
            timestamp=timestamp,
            dependencies=HealthDependencies(database="unhealthy"),
        )

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )

    except Exception:
        logger.exception("Unexpected health check error")

        response = FullHealthResponse(
            status="unhealthy",
            version="v1",
            timestamp=timestamp,
            dependencies=HealthDependencies(database="unhealthy"),
        )

        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=response.model_dump(),
        )


@router.get("/countries", response_model=CountriesResponse, tags=["Lookup"])
def get_all_countries(name: str | None = Query(default=None, description="Filter countries by name"), service: PublicService = Depends(get_public_service),):
    try:
        countries = service.get_countries(name=name)

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
