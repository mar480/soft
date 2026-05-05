from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

from .storage import normalize_persisted_paths


SCHEMA = """
CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS validation_runs (
    id TEXT PRIMARY KEY,
    source_file_id TEXT NOT NULL,
    batch_group_id TEXT,
    report_name TEXT NOT NULL,
    taxonomy_year INTEGER NOT NULL,
    taxonomy_entrypoint TEXT NOT NULL,
    rule_pack_version TEXT NOT NULL,
    rule_snapshot_path TEXT NOT NULL,
    include_all_topics INTEGER NOT NULL DEFAULT 0,
    total_pass INTEGER NOT NULL,
    total_fail INTEGER NOT NULL,
    score_percent REAL NOT NULL,
    result_json_path TEXT NOT NULL,
    html_report_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_file_id) REFERENCES source_files (id),
    FOREIGN KEY (batch_group_id) REFERENCES batch_groups (id)
);

CREATE TABLE IF NOT EXISTS batch_groups (
    id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    progress_message TEXT NOT NULL,
    source_file_id TEXT,
    batch_group_id TEXT,
    validation_run_id TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (source_file_id) REFERENCES source_files (id),
    FOREIGN KEY (validation_run_id) REFERENCES validation_runs (id),
    FOREIGN KEY (batch_group_id) REFERENCES batch_groups (id)
);

CREATE TABLE IF NOT EXISTS rule_versions (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    topic_label TEXT NOT NULL,
    topic_directory TEXT NOT NULL,
    rule_family TEXT NOT NULL,
    state TEXT NOT NULL,
    version_number INTEGER NOT NULL,
    edited_flag INTEGER NOT NULL DEFAULT 0,
    changed_by TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    source_json_path TEXT NOT NULL,
    based_on_version_id TEXT,
    notes TEXT
);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=MEMORY;")
        g.db.execute("PRAGMA synchronous=NORMAL;")
    return g.db


def close_db(_: object | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    _ensure_column(db, "validation_runs", "batch_group_id", "TEXT")
    _ensure_column(db, "jobs", "batch_group_id", "TEXT")
    normalize_persisted_paths(db)
    db.commit()


def init_app(app: Flask) -> None:
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["REPORT_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["RULE_SNAPSHOT_FOLDER"]).mkdir(parents=True, exist_ok=True)
    Path(app.config["RULE_ADMIN_FOLDER"]).mkdir(parents=True, exist_ok=True)
    with app.app_context():
        init_db()


def _ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, column_type: str) -> None:
    columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
