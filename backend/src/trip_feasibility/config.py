from functools import lru_cache

from pydantic import SecretStr, field_validator
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
    # Fix proposals (agent/fixes.py): same provider; untuned until the fix-quality judge exists.
    fix_model: str = "claude-haiku-5-5"
    fix_effort: str = "medium"

    # Sign-in (auth.py): the Next.js server signs a short-lived HS256 token for each API call
    # with this shared secret. Unset: every route that needs a user answers 503.
    api_jwt_secret: SecretStr | None = None
    api_jwt_issuer: str = "trip-feasibility-web"
    api_jwt_audience: str = "trip-feasibility-api"

    # Rate limits (ratelimit.py), per hour. Per user when signed in, per IP for the demo.
    rate_limit_checks_per_hour: int = 20
    rate_limit_parses_per_hour: int = 20
    rate_limit_demo_per_hour: int = 10
    # Check runs in flight at once; more wait their turn (Nominatim/OSRM allow 1 request/s).
    max_concurrent_runs: int = 4
    # Demo runs live in memory only, for this long.
    demo_run_ttl_s: int = 3600

    # Langfuse tracing (tracing.py): off unless both keys are set. Prompts and stops are
    # only sent with trace_content=true; by default traces carry timings, models, tokens
    # and check outcomes, not what the traveller wrote.
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    trace_content: bool = False

    @field_validator(
        "anthropic_api_key",
        "api_jwt_secret",
        "langfuse_public_key",
        "langfuse_secret_key",
        mode="before",
    )
    @classmethod
    def _empty_is_unset(cls, v):
        # .env.example ships `ANTHROPIC_API_KEY=`: an empty value means "not set"
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("api_jwt_secret")
    @classmethod
    def _long_enough(cls, v: SecretStr | None) -> SecretStr | None:
        # HS256 with a short secret can be brute-forced offline from any token
        if v is not None and len(v.get_secret_value()) < 32:
            raise ValueError("API_JWT_SECRET must be at least 32 characters")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()
