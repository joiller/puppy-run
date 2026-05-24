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
