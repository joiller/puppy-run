from functools import lru_cache

from pydantic import AliasChoices, AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgresql://')}"
    if url.startswith("postgres://"):
        return f"postgresql+asyncpg://{url.removeprefix('postgres://')}"
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PUPPYRUN_", env_file=".env", extra="ignore")

    env: str = "development"
    api_host: str = "0.0.0.0"
    api_port: int = Field(
        default=8000,
        validation_alias=AliasChoices("PUPPYRUN_API_PORT", "PORT"),
    )
    database_url: str = "postgresql+asyncpg://puppyrun:puppyrun@localhost:5432/puppyrun"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: list[AnyHttpUrl] = Field(default_factory=list)
    github_token: str | None = None
    github_api_base_url: str = "https://api.github.com"
    llm_provider: str = "deterministic"
    openai_model: str = "gpt-5.5"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    tavily_api_key: str | None = None
    enable_reddit: bool = False
    demo_safety_enabled: bool = False
    live_demo_enabled: bool = False
    admin_token: str | None = None
    live_run_daily_limit: int = 20
    live_run_daily_limit_per_ip: int = 3
    session_create_daily_limit_per_ip: int = 10
    read_rate_limit_per_minute_per_ip: int = 120
    client_ip_header: str | None = None
    tool_timeout_seconds: int = 10
    tool_retry_count: int = 1
    phase3_max_results_per_source: int = 5

    @property
    def sqlalchemy_database_url(self) -> str:
        return normalize_database_url(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
