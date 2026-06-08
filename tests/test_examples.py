from pathlib import Path

import yaml

from src.db import session as db_session


def load_yaml(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_config_example_does_not_enable_placeholder_network_options():
    config = load_yaml("config.example.yaml")
    ruliweb_config = config["crawlers"]["ruliweb_user_hotdeal"]

    assert "url" not in config["database"]
    assert "proxy" not in ruliweb_config
    assert "ssl_ca_cert" not in ruliweb_config


def test_config_example_allows_database_url_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql+aiomysql://user:password@db:3306/hotdeal")
    db_session._config_cache.clear()

    try:
        assert db_session.get_database_url("config.example.yaml") == "mysql+aiomysql://user:password@db:3306/hotdeal"
    finally:
        db_session._config_cache.clear()


def test_database_config_cache_is_scoped_by_path(tmp_path):
    first_config = tmp_path / "first.yaml"
    second_config = tmp_path / "second.yaml"
    first_config.write_text("database:\n  url: sqlite+aiosqlite:///./first.db\n", encoding="utf-8")
    second_config.write_text("database:\n  url: sqlite+aiosqlite:///./second.db\n", encoding="utf-8")
    db_session._config_cache.clear()

    try:
        assert db_session.get_database_url(str(first_config)) == "sqlite+aiosqlite:///./first.db"
        assert db_session.get_database_url(str(second_config)) == "sqlite+aiosqlite:///./second.db"
    finally:
        db_session._config_cache.clear()


def test_default_compose_runs_migrations_before_app_services():
    compose = load_yaml("docker-compose.yml")
    services = compose["services"]

    assert services["migrate"]["command"] == ["alembic", "upgrade", "head"]
    assert "image" not in services["migrate"]

    for service_name in ("crawler", "api"):
        assert services[service_name]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"


def test_local_compose_crawler_waits_for_migrations():
    compose = load_yaml("docker-compose.local.example.yml")

    assert compose["services"]["crawler"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
