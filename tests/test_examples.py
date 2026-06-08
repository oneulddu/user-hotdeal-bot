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


def test_default_database_url_uses_data_directory_and_creates_parent(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_session._config_cache.clear()

    try:
        database_url = db_session.get_database_url("missing-config.yaml")
        db_session.ensure_sqlite_database_parent(database_url)
    finally:
        db_session._config_cache.clear()

    assert database_url == "sqlite+aiosqlite:///./data/hotdeal.db"
    assert (tmp_path / "data").is_dir()


def test_database_echo_parses_string_false_from_config(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text('database:\n  echo: "false"\n', encoding="utf-8")
    db_session._config_cache.clear()

    try:
        assert db_session.get_database_echo(str(config)) is False
    finally:
        db_session._config_cache.clear()


def test_database_echo_parses_env_values(monkeypatch):
    monkeypatch.setenv("DATABASE_ECHO", "yes")
    db_session._config_cache.clear()

    try:
        assert db_session.get_database_echo("missing-config.yaml") is True
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


def test_readme_standalone_docker_path_runs_migrations():
    readme = Path("README.md").read_text(encoding="utf-8")

    assert "user-hotdeal-bot-migrate" in readme
    assert "alembic upgrade head" in readme
    assert readme.index("alembic upgrade head") < readme.index("--name user-hotdeal-bot-crawler")
