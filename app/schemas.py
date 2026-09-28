"""Request/response shapes. Pydantic validates input for free:
a malformed URL gets a 422 before our code ever runs."""
from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class ShortenRequest(BaseModel):
    long_url: HttpUrl = Field(..., description="The URL to shorten (http/https)")


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str


class StatsResponse(BaseModel):
    short_code: str
    long_url: str
    click_count: int
    created_at: datetime
