"""Private local favourites and recency. No network activity and no key contents."""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / "state.sqlite3"
        if self.path.is_symlink():
            raise ValueError("Refusing a symlink for the launcher state database.")
        self.db = sqlite3.connect(self.path, timeout=5)
        os.chmod(self.path, 0o600)
        self.db.execute("CREATE TABLE IF NOT EXISTS hosts (alias TEXT PRIMARY KEY, favourite INTEGER NOT NULL DEFAULT 0, opened REAL, opens INTEGER NOT NULL DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS preferences (name TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()

    def preference(self, name: str, default: str) -> str:
        record = self.db.execute("SELECT value FROM preferences WHERE name=?", (name,)).fetchone()
        return record[0] if record else default

    def set_preference(self, name: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO preferences(name, value) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (name, value))

    def metadata(self) -> dict[str, dict]:
        return {alias: {"favourite": bool(favourite), "opened": opened, "opens": opens}
                for alias, favourite, opened, opens in self.db.execute("SELECT alias, favourite, opened, opens FROM hosts")}

    def toggle(self, alias: str) -> bool:
        with self.db:
            self.db.execute("INSERT INTO hosts(alias, favourite) VALUES (?, 1) ON CONFLICT(alias) DO UPDATE SET favourite = 1 - favourite", (alias.casefold(),))
        return bool(self.db.execute("SELECT favourite FROM hosts WHERE alias=?", (alias.casefold(),)).fetchone()[0])

    def opened(self, alias: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO hosts(alias, opened, opens) VALUES (?, ?, 1) ON CONFLICT(alias) DO UPDATE SET opened=excluded.opened, opens=opens+1", (alias.casefold(), time.time()))

    def close(self) -> None:
        self.db.close()