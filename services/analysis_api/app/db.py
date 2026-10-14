"""
SQLite 持久化（V6 唯一写入者：本服务）。

FIR-31 §7：自选股 / tombstone / 板块配置 / 设置由本服务唯一写入；GitHub 配置文件只作为
迁移来源与导出备份，不再是 last-writer-wins 数据库。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state_version (user_id TEXT PRIMARY KEY, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist (
  user_id TEXT NOT NULL, code TEXT NOT NULL, added_at TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
  PRIMARY KEY (user_id, code));
CREATE TABLE IF NOT EXISTS watchlist_tombstones (
  user_id TEXT NOT NULL, code TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY (user_id, code));
CREATE TABLE IF NOT EXISTS custom_sectors (
  user_id TEXT NOT NULL, name TEXT NOT NULL, codes TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (user_id, name));
CREATE TABLE IF NOT EXISTS custom_sector_tombstones (
  user_id TEXT NOT NULL, name TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY (user_id, name));
CREATE TABLE IF NOT EXISTS dynamic_sectors (
  user_id TEXT NOT NULL, sector_code TEXT NOT NULL, data TEXT NOT NULL, added_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (user_id, sector_code));
CREATE TABLE IF NOT EXISTS dynamic_sector_tombstones (
  user_id TEXT NOT NULL, sector_code TEXT NOT NULL, deleted_at TEXT NOT NULL, PRIMARY KEY (user_id, sector_code));
CREATE TABLE IF NOT EXISTS settings (user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
  updated_at TEXT NOT NULL, PRIMARY KEY (user_id, key));
CREATE TABLE IF NOT EXISTS applied_ops (user_id TEXT NOT NULL, op_id TEXT NOT NULL, applied_at TEXT NOT NULL,
  result TEXT NOT NULL, PRIMARY KEY (user_id, op_id));
CREATE TABLE IF NOT EXISTS last_good_analysis (
  code TEXT NOT NULL, adjustment TEXT NOT NULL, as_of_key TEXT NOT NULL, data_hash TEXT NOT NULL,
  doc TEXT NOT NULL, stored_at TEXT NOT NULL, PRIMARY KEY (code, adjustment, as_of_key));
CREATE TABLE IF NOT EXISTS scan_jobs (job_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL,
  request TEXT NOT NULL, state TEXT NOT NULL, results TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS migrations (id TEXT PRIMARY KEY, applied_at TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  log TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_DDL)      # executescript 自带提交，不能放在显式事务里
        with self.tx() as c:
            row = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if row is None:
                c.execute("INSERT INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
            elif int(row["value"]) != SCHEMA_VERSION:
                raise RuntimeError(f"数据库 schema 版本 {row['value']} 与服务 {SCHEMA_VERSION} 不一致，拒绝启动")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """串行化的原子事务（BEGIN IMMEDIATE），异常回滚"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return self._conn.execute(sql, args).fetchall()

    def writable(self) -> bool:
        try:
            with self.tx() as c:
                c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('ready_probe', datetime('now'))")
            return True
        except Exception:
            return False

    def schema_version(self) -> int:
        return SCHEMA_VERSION

    # ---- 版本号（ETag） ----
    @staticmethod
    def bump(c: sqlite3.Connection, user_id: str) -> int:
        c.execute("INSERT INTO state_version(user_id, version) VALUES(?, 1) "
                  "ON CONFLICT(user_id) DO UPDATE SET version = version + 1", (user_id,))
        return c.execute("SELECT version FROM state_version WHERE user_id=?", (user_id,)).fetchone()["version"]

    def version(self, user_id: str, c: Optional[sqlite3.Connection] = None) -> int:
        conn = c or self._conn
        with self._lock:
            row = conn.execute("SELECT version FROM state_version WHERE user_id=?", (user_id,)).fetchone()
        return row["version"] if row else 0


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, allow_nan=False)
