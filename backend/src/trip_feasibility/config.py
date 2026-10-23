from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://trip:trip@localhost:5432/trip"
    port: int = 8000

    # External data sources used by the check tools. OSM asks for an identifying User-Agent.
    http_user_agent: str = (
        "trip-feasibility/0.1 (+https://github.com/vincentgoh0902/trip-feasibility)"
    )
    http_timeout_s: float = 30.0
    nager_base_url: str = "https://date.nager.at/api/v3"
    # Tried in order; the next one is used when one is down or overloaded. Mirrors must
    # hold the whole planet (overpass.osm.ch, for example, is Switzerland only).
    overpass_urls: Annotated[list[str], NoDecode] = [
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ]
    # Lookups by ID answer in ~1-2 s when a server is healthy; fail fast and try the next.
    overpass_timeout_s: float = 10.0
    cache_ttl_holidays_days: int = 30
    cache_ttl_osm_days: int = 7

    @field_validator("overpass_urls", mode="before")
    @classmethod
    def _split_urls(cls, v: object) -> object:
        return [u.strip() for u in v.split(",") if u.strip()] if isinstance(v, str) else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
