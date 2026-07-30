import redis

from app.core.config import settings

# decode_responses=True so we work with str everywhere instead of bytes.
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)

CLICK_BUFFER_PREFIX = "clicks:pending:"  # + short_code -> int (INCR target)
URL_CACHE_PREFIX = "url:cache:"  # + short_code -> long_url string
