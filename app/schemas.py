from datetime import datetime

from pydantic import BaseModel, HttpUrl, ConfigDict


class ShortenRequest(BaseModel):
    long_url: HttpUrl


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str
    long_url: str


class StatsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    short_code: str
    long_url: str
    click_count: int
    created_at: datetime
