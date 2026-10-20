#!/usr/bin/env python3
"""Room bookings, kept in a JSON file.

  booking.py --data-dir DIR book ROOM START END
  booking.py --data-dir DIR cancel ID
  booking.py --data-dir DIR list
"""

import json
import sys
from pathlib import Path


def load(data_dir: Path) -> dict:
    path = data_dir / "bookings.json"
    return json.loads(path.read_text()) if path.exists() else {"next": 0, "bookings": []}


def save(data_dir: Path, db: dict) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "bookings.json").write_text(json.dumps(db))


def fail(category: str) -> int:
    print(json.dumps({"error": category}))
    return 1


def main(argv: list) -> int:
    data_dir, (cmd, *args) = Path(argv[1]), argv[2:]
    db = load(data_dir)
    if cmd == "book":
        room, start, end = args[0], int(args[1]), int(args[2])
        if end <= start:
            return fail("invalid_range")
        if any(b["room"] == room and b["start"] < end and start < b["end"] for b in db["bookings"]):
            return fail("conflict")
        b = {"id": db["next"], "room": room, "start": start, "end": end}
        db["next"] += 1
        db["bookings"].append(b)
        save(data_dir, db)
        print(json.dumps(b))
        return 0
    if cmd == "cancel":
        id = int(args[0])
        if not any(b["id"] == id for b in db["bookings"]):
            return fail("not_found")
        db["bookings"] = [b for b in db["bookings"] if b["id"] != id]
        save(data_dir, db)
        print(json.dumps({"cancelled": id}))
        return 0
    if cmd == "list":
        print(json.dumps(db["bookings"]))
        return 0
    return fail("unknown_command")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
