# pyrefly: ignore [missing-import]
from sqlalchemy import Column, BigInteger, String, Integer, DateTime, func

from app.core.db import Base


class ShortURL(Base):
    __tablename__ = "urls"

    # BigInteger id: auto-increment in v1 (naive), Snowflake-assigned in v2.
    # Either way it's the primary key and the thing we base62-encode.
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    # Nullable at the DB level: the v1 naive path inserts a row first to
    # learn the auto-increment id, then encodes and fills short_code in a
    # second UPDATE. v2 always sets it before insert since Snowflake ids
    # are known upfront. Either way it's never null once a request completes.
    short_code = Column(String(16), unique=True, index=True, nullable=True)
    long_url = Column(String(2048), nullable=False)
    click_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
