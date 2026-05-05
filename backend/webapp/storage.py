from __future__ import annotations

from pathlib import Path

from flask import current_app


INSTANCE_FOLDERS = {"uploads", "reports", "rule_snapshots", "rule_admin"}
PROJECT_ANCHORS = {"backend"}


def project_root() -> Path:
    return Path(current_app.root_path).resolve().parents[1]


def instance_root() -> Path:
    return Path(current_app.instance_path).resolve()


def resolve_storage_path(path_value: str) -> Path:
    raw_path = Path(path_value)
    if raw_path.is_absolute():
        if raw_path.exists():
            return raw_path
        remapped = _remap_legacy_absolute_path(raw_path)
        return remapped if remapped is not None else raw_path
    if not raw_path.parts:
        return project_root()

    first_part = raw_path.parts[0].lower()
    if first_part in INSTANCE_FOLDERS:
        return instance_root() / raw_path
    if first_part in PROJECT_ANCHORS:
        return project_root() / raw_path
    return project_root() / raw_path


def storage_path_value(path_value: str | Path) -> str:
    path = Path(path_value)
    if not path.is_absolute():
        return path.as_posix()

    for root in (instance_root(), project_root()):
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue

    remapped = _remap_legacy_absolute_path(path)
    if remapped is not None:
        return storage_path_value(remapped)
    return path.as_posix()


def normalize_persisted_paths(db) -> None:
    _normalize_table_paths(db, "source_files", "id", ("stored_path",))
    _normalize_table_paths(
        db,
        "validation_runs",
        "id",
        ("rule_snapshot_path", "result_json_path", "html_report_path"),
    )


def _normalize_table_paths(db, table_name: str, id_column: str, path_columns: tuple[str, ...]) -> None:
    rows = db.execute(
        f"SELECT {id_column}, {', '.join(path_columns)} FROM {table_name}"
    ).fetchall()
    for row in rows:
        updates: dict[str, str] = {}
        for column in path_columns:
            normalized = storage_path_value(row[column])
            if normalized != row[column]:
                updates[column] = normalized
        if not updates:
            continue
        assignments = ", ".join(f"{column} = ?" for column in updates)
        db.execute(
            f"UPDATE {table_name} SET {assignments} WHERE {id_column} = ?",
            (*updates.values(), row[id_column]),
        )


def _remap_legacy_absolute_path(path: Path) -> Path | None:
    lowered_parts = [part.lower() for part in path.parts]

    if "instance" in lowered_parts:
        instance_index = lowered_parts.index("instance")
        return instance_root().joinpath(*path.parts[instance_index + 1 :])

    for folder_name in INSTANCE_FOLDERS:
        if folder_name in lowered_parts:
            folder_index = lowered_parts.index(folder_name)
            return instance_root().joinpath(*path.parts[folder_index:])

    for anchor in PROJECT_ANCHORS:
        if anchor in lowered_parts:
            anchor_index = lowered_parts.index(anchor)
            return project_root().joinpath(*path.parts[anchor_index:])

    return None
