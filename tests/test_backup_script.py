import os
import subprocess
from pathlib import Path


SCRIPT = Path("scripts/mariadb_backup.sh")


def test_mariadb_backup_script_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_mariadb_backup_rejects_invalid_retention_days():
    env = {
        **os.environ,
        "MARIADB_HOST": "db",
        "MARIADB_USER": "hotdeal",
        "MARIADB_PASSWORD": "password",
        "MARIADB_DATABASE": "hotdeal",
        "S3_BUCKET": "bucket",
        "RETENTION_DAYS": "0",
    }

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "RETENTION_DAYS must be a positive integer" in result.stderr
