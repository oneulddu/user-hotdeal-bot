from pathlib import Path

import yaml


def load_yaml(path: str) -> dict:
    with Path(path).open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_config_example_does_not_enable_placeholder_network_options():
    config = load_yaml("config.example.yaml")
    ruliweb_config = config["crawlers"]["ruliweb_user_hotdeal"]

    assert "proxy" not in ruliweb_config
    assert "ssl_ca_cert" not in ruliweb_config


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
