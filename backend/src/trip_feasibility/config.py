from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # The repo-root .env (next to .env.example) when run from backend/, then a local .env;
    # missing files are skipped, and real environment variables win over both.
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://trip:trip@localhost:5432/trip"
    port: int = 8000

    # External data sources used by the check tools. OSM asks for an identifying User-Agent.
    http_user_agent: str = (
        "trip-feasibility/0.1 (+https://github.com/vincentgoh0902/trip-feasibility)"
    )
    http_timeout_s: float = 30.0
    nager_base_url: str = "https://date.nager.at/api/v3"
    # OSM place tags (opening hours) come from Nominatim's /lookup. Its usage policy allows at
    # most 1 request per second and blocks clients that ignore it.
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    nominatim_min_interval_s: float = 1.0
    nominatim_timeout_s: float = 10.0
    # Walking/cycling/driving times from the FOSSGIS OSRM servers (car, bike, foot profiles).
    # Policy: at most 1 request per second, no heavy use, OSM attribution shown in the UI.
    routing_base_url: str = "https://routing.openstreetmap.de"
    routing_min_interval_s: float = 1.0
    routing_timeout_s: float = 15.0
    # After a rate-limit or overload response, stop calling that service for this long.
    rate_limit_cooldown_s: float = 60.0
    cache_ttl_holidays_days: int = 30
    cache_ttl_osm_days: int = 7
    cache_ttl_routes_days: int = 30

    # Parse step 1 (LLM extraction). The key is a secret: .env locally, app.env on the server.
    anthropic_api_key: SecretStr | None = None
    extraction_model: str = "claude-haiku-5-5"
    extraction_effort: str = "low"


@lru_cache
def get_settings() -> Settings:
    return Settings()
