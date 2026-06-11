"""Top-level package metadata."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGE_NAME = "user-hotdeal-bot"


def get_version() -> str:
    try:
        return version(PACKAGE_NAME)
    except PackageNotFoundError:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        with open(pyproject_path, "rb") as f:
            pyproject = tomllib.load(f)
        return pyproject["project"]["version"]


__version__ = get_version()
