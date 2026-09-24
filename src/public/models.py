from datetime import datetime, timezone

from sqlalchemy import CHAR, Column, DateTime, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from src.database import Base
from uuid6 import uuid7


class Country(Base):
    __tablename__ = "countries"
    __table_args__ = (
        Index("idx_countries_name_trgm", "name", postgresql_using="gin",
              postgresql_ops={"name": "gin_trgm_ops"}),
        UniqueConstraint("iso2", name="countries_iso2_key"),
        UniqueConstraint("iso3", name="countries_iso3_key"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid7()))
    name = Column(String(100), nullable=False)
    iso2 = Column(CHAR(2), nullable=False)
    iso3 = Column(CHAR(3), nullable=False)
    phone_code = Column(String(10), nullable=True)
    timezone = Column(ARRAY(Text), nullable=False)
    flag_emoji = Column(String(10), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), server_default=text("now()"))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), server_default=text("now()"))
