from puppyrun_api.config import Settings, normalize_database_url


def test_normalize_database_url_keeps_asyncpg_url() -> None:
    url = "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == url


def test_normalize_database_url_converts_postgresql_url() -> None:
    url = "postgresql://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"


def test_normalize_database_url_converts_legacy_postgres_url() -> None:
    url = "postgres://user:pass@postgres:5432/puppyrun"

    assert normalize_database_url(url) == "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"


def test_normalize_database_url_leaves_non_postgres_url_unchanged() -> None:
    url = "sqlite+aiosqlite:///:memory:"

    assert normalize_database_url(url) == url


def test_settings_reads_platform_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.delenv("PUPPYRUN_API_PORT", raising=False)

    settings = Settings()

    assert settings.api_port == 10000


def test_settings_prefers_explicit_puppyrun_api_port(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "10000")
    monkeypatch.setenv("PUPPYRUN_API_PORT", "9000")

    settings = Settings()

    assert settings.api_port == 9000


def test_settings_exposes_normalized_sqlalchemy_database_url(monkeypatch) -> None:
    monkeypatch.setenv("PUPPYRUN_DATABASE_URL", "postgresql://user:pass@postgres:5432/puppyrun")

    settings = Settings()

    assert settings.sqlalchemy_database_url == (
        "postgresql+asyncpg://user:pass@postgres:5432/puppyrun"
    )


def test_phase5_demo_safety_defaults_are_local_safe(monkeypatch) -> None:
    for key in (
        "PUPPYRUN_DEMO_SAFETY_ENABLED",
        "PUPPYRUN_LIVE_DEMO_ENABLED",
        "PUPPYRUN_ADMIN_TOKEN",
        "PUPPYRUN_LIVE_RUN_DAILY_LIMIT",
        "PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP",
        "PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP",
        "PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP",
        "PUPPYRUN_CLIENT_IP_HEADER",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings()

    assert settings.demo_safety_enabled is False
    assert settings.live_demo_enabled is False
    assert settings.admin_token is None
    assert settings.live_run_daily_limit == 20
    assert settings.live_run_daily_limit_per_ip == 3
    assert settings.session_create_daily_limit_per_ip == 10
    assert settings.read_rate_limit_per_minute_per_ip == 120
    assert settings.client_ip_header is None


def test_phase5_demo_safety_reads_public_demo_env(monkeypatch) -> None:
    monkeypatch.setenv("PUPPYRUN_DEMO_SAFETY_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_LIVE_DEMO_ENABLED", "true")
    monkeypatch.setenv("PUPPYRUN_ADMIN_TOKEN", "private-admin-token")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT", "21")
    monkeypatch.setenv("PUPPYRUN_LIVE_RUN_DAILY_LIMIT_PER_IP", "4")
    monkeypatch.setenv("PUPPYRUN_SESSION_CREATE_DAILY_LIMIT_PER_IP", "11")
    monkeypatch.setenv("PUPPYRUN_READ_RATE_LIMIT_PER_MINUTE_PER_IP", "121")
    monkeypatch.setenv("PUPPYRUN_CLIENT_IP_HEADER", "X-Forwarded-For")

    settings = Settings()

    assert settings.demo_safety_enabled is True
    assert settings.live_demo_enabled is True
    assert settings.admin_token == "private-admin-token"
    assert settings.live_run_daily_limit == 21
    assert settings.live_run_daily_limit_per_ip == 4
    assert settings.session_create_daily_limit_per_ip == 11
    assert settings.read_rate_limit_per_minute_per_ip == 121
    assert settings.client_ip_header == "X-Forwarded-For"
