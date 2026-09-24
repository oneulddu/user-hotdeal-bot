import json
import shutil
from datetime import datetime
from pathlib import Path


def v1_to_v2(dump_file_path: str):
    with open(dump_file_path, "r") as f:
        old: dict[str, dict[str, dict]] = json.load(f)
    if isinstance(old, dict) and "version" in old:
        raise ValueError("dump file is already migrated")
    _crawler = {}
    _bot = {}
    for c_name, c_msgs in old.items():
        for msg_id, msg in c_msgs.items():
            msg_obj: dict = msg.pop("message")
            if c_name not in _crawler:
                _crawler[c_name] = {}
            _crawler[c_name][msg_id] = msg
            _crawler[c_name][msg_id]["crawler_name"] = c_name
            for bot_name, bot_chat in msg_obj.items():
                if bot_name not in _bot:
                    _bot[bot_name] = {"queue": [], "cache": {}}
                if c_name not in _bot[bot_name]["cache"]:
                    _bot[bot_name]["cache"][c_name] = {}
                _bot[bot_name]["cache"][c_name][msg_id] = bot_chat

    new = {"version": "v2.0.0", "crawler": _crawler, "bot": _bot}

    with open(dump_file_path, "w") as f:
        json.dump(new, f, ensure_ascii=False, indent=2)
    return new


def backup_dump(path: str) -> str:
    backup = f"{path}.bak"
    if Path(backup).exists():
        backup = f"{path}.{datetime.now():%Y%m%d_%H%M%S}.bak"
    # Exclusive creation also prevents overwrites on timestamp collisions.
    with open(path, "rb") as source, open(backup, "xb") as destination:
        shutil.copyfileobj(source, destination)
    return backup


if __name__ == "__main__":
    backup_dump("dump.json")
    v1_to_v2("dump.json")
