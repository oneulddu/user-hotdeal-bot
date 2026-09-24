import json
import runpy
from pathlib import Path

import pytest

from src.tools import migration


@pytest.mark.parametrize("use_main", [False, True])
def test_migration_preserves_messages_for_same_crawler(tmp_path, monkeypatch, use_main):
    old = {
        "deals": {
            "123": {"title": "첫 상품", "message": {"telegram": {"chat": 10}}},
            "124": {"title": "둘째 상품", "message": {"telegram": {"chat": 20}}},
        }
    }
    dump = tmp_path / "dump.json"
    original = json.dumps(old, ensure_ascii=False).encode()
    dump.write_bytes(original)
    if use_main:
        monkeypatch.chdir(tmp_path)
        runpy.run_path(migration.__file__, run_name="__main__")
        assert (tmp_path / "dump.json.bak").read_bytes() == original
    else:
        migration.v1_to_v2(str(dump))
    migrated = json.loads(dump.read_text())
    assert migrated["bot"]["telegram"] == {
        "queue": [],
        "cache": {"deals": {"123": {"chat": 10}, "124": {"chat": 20}}},
    }
    assert set(migrated["crawler"]["deals"]) == {"123", "124"}
    assert all(msg["crawler_name"] == "deals" for msg in migrated["crawler"]["deals"].values())


@pytest.mark.parametrize("use_main", [False, True])
def test_migration_rejects_migrated_dump_without_changing_it(tmp_path, monkeypatch, use_main):
    dump = tmp_path / "dump.json"
    original = b'{"version": "v2.0.0", "crawler": {}, "bot": {}}'
    dump.write_bytes(original)
    backup = tmp_path / "dump.json.bak"
    backup.write_bytes(b"original v1 backup")

    with pytest.raises(ValueError, match="^dump file is already migrated$"):
        if use_main:
            monkeypatch.chdir(tmp_path)
            runpy.run_path(migration.__file__, run_name="__main__")
        else:
            migration.v1_to_v2(str(dump))

    assert dump.read_bytes() == original
    assert backup.read_bytes() == b"original v1 backup"


def test_backup_dump_preserves_existing_backup(tmp_path):
    dump = tmp_path / "dump.json"
    dump.write_bytes(b"original v1 data")
    first_backup = Path(migration.backup_dump(str(dump)))
    assert first_backup == tmp_path / "dump.json.bak"
    dump.write_bytes(b"migrated v2 data")
    second_backup = Path(migration.backup_dump(str(dump)))
    assert second_backup != first_backup
    assert second_backup.match("dump.json.????????_??????.bak")
    assert first_backup.read_bytes() == b"original v1 data"
    assert second_backup.read_bytes() == b"migrated v2 data"
