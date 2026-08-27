from datetime import timedelta
from threading import RLock
from time import monotonic

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from src.config import get_logger
from src.public.models import Country
from src.public.schemas import CountryResponse

logger = get_logger(__name__)


class CountryCache:
    def __init__(self, ttl: timedelta = timedelta(hours=24)):
        self.ttl_seconds = ttl.total_seconds()
        self.countries: tuple[CountryResponse, ...] = ()
        self.fetched_at = 0.0
        self.lock = RLock()

    def is_fresh(self) -> bool:
        return bool(self.countries) and (
            monotonic() - self.fetched_at < self.ttl_seconds
        )


class PublicService:
    def __init__(self, db: Session, country_cache: CountryCache):
        self.db = db
        self.country_cache = country_cache

    def get_countries(
        self,
        name: str | None = None,
    ) -> list[CountryResponse]:
        try:
            with self.country_cache.lock:
                if self.country_cache.is_fresh():
                    countries = list(self.country_cache.countries)
                else:
                    stmt = select(Country).order_by(Country.name.asc())
                    result = self.db.execute(stmt)
                    rows = result.scalars().all()
                    countries = [
                        CountryResponse.model_validate(country) for country in rows
                    ]

                    self.country_cache.countries = tuple(countries)
                    self.country_cache.fetched_at = monotonic()

                    logger.info(
                        "Country cache refreshed: count=%s ttl_hours=24",
                        len(countries),
                    )

            if name:
                search = name.strip().lower()

                countries = [
                    country for country in countries if search in country.name.lower()
                ]

            logger.debug(
                "Retrieved %s countries",
                len(countries),
            )

            return countries

        except SQLAlchemyError as exc:
            logger.exception("Database error while retrieving countries")

            raise RuntimeError("Failed to retrieve countries") from exc

        except Exception as exc:
            logger.exception("Unexpected error while processing countries")

            raise RuntimeError("Failed to process countries") from exc

    def get_country_by_id(
        self,
        country_id: str,
    ) -> CountryResponse:
        try:
            stmt = select(Country).where(Country.id == country_id)

            result = self.db.execute(stmt)

            country = result.scalar_one_or_none()

            if country is None:
                logger.warning(
                    "Country not found: %s",
                    country_id,
                )

                raise ValueError("Country not found")

            return CountryResponse.model_validate(country)

        except ValueError:
            raise

        except SQLAlchemyError as exc:
            logger.exception(
                "Database error while retrieving country: %s",
                country_id,
            )

            raise RuntimeError("Failed to retrieve country") from exc

        except Exception as exc:
            logger.exception(
                "Unexpected error while processing country: %s",
                country_id,
            )

            raise RuntimeError("Failed to process country") from exc
