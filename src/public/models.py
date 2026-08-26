from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy import Text

from uuid6 import uuid7

from src.database import Base


class Country(Base):
    __tablename__ = "countries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    name = Column(String(100), nullable=False)
    iso2 = Column(String(2), nullable=False, unique=True)
    iso3 = Column(String(3), nullable=False, unique=True)
    phone_code = Column(String(10), nullable=True)
    timezone = Column(ARRAY(Text), nullable=False)
    flag_emoji = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))