from __future__ import annotations

import json
import shutil
import uuid
from collections import defaultdict
from difflib import unified_diff
from pathlib import Path
from typing import Any

from flask import current_app

from ..db import get_db
from .validation_service import utc_now


def topic_inventory(*, family_filter: str | None = None) -> list[dict[str, Any]]:
    manifest = _manifest()
    drafts = active_drafts_by_family()
    topics: list[dict[str, Any]] = []
    for topic in manifest["topics"]:
        families = list(topic["families"])
        if family_filter:
            families = [family for family in families if family == family_filter]
        if not families:
            continue
        topics.append(
            {
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "directory": topic["directory"],
                "rule_count": topic["rule_count"],
                "families": [
                    {
                        "name": family,
                        "has_draft": (topic["topic_id"], family) in drafts,
                    }
                    for family in families
                ],
            }
        )
    return topics


def available_rule_families() -> list[str]:
    families: set[str] = set()
    for topic in _manifest()["topics"]:
        families.update(topic["families"])
    return sorted(families)


def family_stats() -> list[dict[str, Any]]:
    manifest = _manifest()
    published_counts: dict[str, int] = defaultdict(int)
    draft_counts: dict[str, int] = defaultdict(int)
    for topic in manifest["topics"]:
        for family in topic["families"]:
            published_counts[family] += 1
    for _, family in active_drafts_by_family():
        draft_counts[family] += 1
    return [
        {"family": family, "published_topics": published_counts[family], "draft_topics": draft_counts[family]}
        for family in sorted(published_counts)
    ]


def get_family_view(*, topic_id: str, family_name: str) -> dict[str, Any] | None:
    topic = _manifest_topic(topic_id)
    if topic is None or family_name not in topic["families"]:
        return None

    topic_dir = _split_output_dir() / topic["directory"]
    topic_metadata = _read_json(topic_dir / "topic.json")
    published_path = topic_dir / family_name / "rules.json"
    published_payload = _read_json(published_path) if published_path.exists() else {"rule_count": 0, "rules": []}
    return {
        "topic": topic,
        "topic_metadata": topic_metadata,
        "family_name": family_name,
        "published_path": str(published_path),
        "published_payload": published_payload,
        "current_published": get_current_published(topic_id=topic_id, family_name=family_name),
        "active_draft": get_active_draft(topic_id=topic_id, family_name=family_name),
        "archives": archived_versions(topic_id=topic_id, family_name=family_name),
    }


def create_draft(*, topic_id: str, family_name: str) -> str:
    topic = _manifest_topic(topic_id)
    if topic is None or family_name not in topic["families"]:
        raise ValueError("Rule family not found.")

    existing = get_active_draft(topic_id=topic_id, family_name=family_name)
    if existing is not None:
        return existing["id"]

    draft_id = uuid.uuid4().hex
    published_path = _split_output_dir() / topic["directory"] / family_name / "rules.json"
    draft_dir = Path(current_app.config["RULE_ADMIN_FOLDER"]) / "drafts" / topic_id
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / f"{_slug(family_name)}__{draft_id}.json"
    shutil.copyfile(published_path, draft_path)

    db = get_db()
    db.execute(
        """
        INSERT INTO rule_versions (
            id,
            topic_id,
            topic_label,
            topic_directory,
            rule_family,
            state,
            version_number,
            edited_flag,
            changed_by,
            changed_at,
            source_json_path,
            based_on_version_id,
            notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id,
            topic["topic_id"],
            topic["topic_label"],
            topic["directory"],
            family_name,
            "draft",
            next_version_number(topic_id=topic_id, family_name=family_name),
            1,
            "user",
            utc_now(),
            str(draft_path),
            None,
            "Draft created from published rule family.",
        ),
    )
    db.commit()
    return draft_id


def get_draft(draft_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE id = ? AND state = 'draft'
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        return None

    record = dict(row)
    record["payload"] = _read_json(Path(record["source_json_path"]))
    record["published_payload"] = _read_json(
        _split_output_dir() / record["topic_directory"] / record["rule_family"] / "rules.json"
    )
    record["draft_json"] = _pretty_json(record["payload"])
    record["published_json"] = _pretty_json(record["published_payload"])
    record["diff_lines"] = draft_diff_lines(record["published_payload"], record["payload"])
    return record


def save_draft(draft_id: str, raw_json: str) -> None:
    row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE id = ? AND state = 'draft'
        """,
        (draft_id,),
    ).fetchone()
    if row is None:
        raise ValueError("Draft not found.")

    payload = json.loads(raw_json)
    _validate_draft_payload(payload, row["topic_id"], row["rule_family"])

    source_path = Path(row["source_json_path"])
    source_path.write_text(_pretty_json(payload) + "\n", encoding="utf-8")
    db = get_db()
    db.execute(
        """
        UPDATE rule_versions
        SET changed_at = ?, notes = ?
        WHERE id = ?
        """,
        (utc_now(), "Draft JSON updated.", draft_id),
    )
    db.commit()


def publish_draft(draft_id: str) -> None:
    draft_row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE id = ? AND state = 'draft'
        """,
        (draft_id,),
    ).fetchone()
    if draft_row is None:
        raise ValueError("Draft not found.")

    draft = dict(draft_row)
    draft_payload = _read_json(Path(draft["source_json_path"]))
    _validate_draft_payload(draft_payload, draft["topic_id"], draft["rule_family"])

    published_path = _published_file_path(draft["topic_directory"], draft["rule_family"])
    archive_dir = Path(current_app.config["RULE_ADMIN_FOLDER"]) / "archives" / draft["topic_id"]
    archive_dir.mkdir(parents=True, exist_ok=True)

    current_published = get_current_published(topic_id=draft["topic_id"], family_name=draft["rule_family"])
    _archive_current_published(
        topic_id=draft["topic_id"],
        topic_label=draft["topic_label"],
        topic_directory=draft["topic_directory"],
        family_name=draft["rule_family"],
        published_path=published_path,
        current_published=current_published,
        archive_dir=archive_dir,
    )

    shutil.copyfile(draft["source_json_path"], published_path)
    db = get_db()
    db.execute(
        """
        UPDATE rule_versions
        SET state = ?, changed_at = ?, source_json_path = ?, notes = ?
        WHERE id = ?
        """,
        (
            "published",
            utc_now(),
            str(published_path),
            "Draft published to production rule family.",
            draft_id,
        ),
    )
    db.commit()


def restore_archived_version(archive_id: str) -> None:
    archive_row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE id = ? AND state = 'archived'
        """,
        (archive_id,),
    ).fetchone()
    if archive_row is None:
        raise ValueError("Archived version not found.")

    archive = dict(archive_row)
    archive_payload = _read_json(Path(archive["source_json_path"]))
    _validate_draft_payload(archive_payload, archive["topic_id"], archive["rule_family"])

    published_path = _published_file_path(archive["topic_directory"], archive["rule_family"])
    archive_dir = Path(current_app.config["RULE_ADMIN_FOLDER"]) / "archives" / archive["topic_id"]
    archive_dir.mkdir(parents=True, exist_ok=True)

    current_published = get_current_published(topic_id=archive["topic_id"], family_name=archive["rule_family"])
    _archive_current_published(
        topic_id=archive["topic_id"],
        topic_label=archive["topic_label"],
        topic_directory=archive["topic_directory"],
        family_name=archive["rule_family"],
        published_path=published_path,
        current_published=current_published,
        archive_dir=archive_dir,
    )

    shutil.copyfile(archive["source_json_path"], published_path)
    db = get_db()
    db.execute(
        """
        UPDATE rule_versions
        SET state = ?, changed_at = ?, source_json_path = ?, notes = ?
        WHERE id = ?
        """,
        (
            "published",
            utc_now(),
            str(published_path),
            "Archived version restored to production rule family.",
            archive_id,
        ),
    )
    db.commit()


def draft_diff_lines(published_payload: dict[str, Any], draft_payload: dict[str, Any]) -> list[str]:
    published_json = _pretty_json(published_payload).splitlines()
    draft_json = _pretty_json(draft_payload).splitlines()
    return list(
        unified_diff(
            published_json,
            draft_json,
            fromfile="published",
            tofile="draft",
            lineterm="",
        )
    )


def active_drafts_by_family() -> set[tuple[str, str]]:
    rows = get_db().execute(
        """
        SELECT topic_id, rule_family
        FROM rule_versions
        WHERE state = 'draft'
        """
    ).fetchall()
    return {(row["topic_id"], row["rule_family"]) for row in rows}


def get_active_draft(*, topic_id: str, family_name: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE topic_id = ? AND rule_family = ? AND state = 'draft'
        ORDER BY changed_at DESC
        LIMIT 1
        """,
        (topic_id, family_name),
    ).fetchone()
    return dict(row) if row is not None else None


def get_current_published(*, topic_id: str, family_name: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE topic_id = ? AND rule_family = ? AND state = 'published'
        ORDER BY changed_at DESC
        LIMIT 1
        """,
        (topic_id, family_name),
    ).fetchone()
    return dict(row) if row is not None else None


def archived_versions(*, topic_id: str, family_name: str) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT *
        FROM rule_versions
        WHERE topic_id = ? AND rule_family = ? AND state = 'archived'
        ORDER BY changed_at DESC
        """,
        (topic_id, family_name),
    ).fetchall()
    return [dict(row) for row in rows]


def next_version_number(*, topic_id: str, family_name: str) -> int:
    row = get_db().execute(
        """
        SELECT MAX(version_number) AS max_version
        FROM rule_versions
        WHERE topic_id = ? AND rule_family = ?
        """,
        (topic_id, family_name),
    ).fetchone()
    return int(row["max_version"] or 0) + 1


def _manifest() -> dict[str, Any]:
    return _read_json(_split_output_dir() / "manifest.json")


def _manifest_topic(topic_id: str) -> dict[str, Any] | None:
    for topic in _manifest()["topics"]:
        if topic["topic_id"] == topic_id:
            return topic
    return None


def _split_output_dir() -> Path:
    return Path(current_app.config["SPLIT_OUTPUT_DIR"])


def _published_file_path(topic_directory: str, family_name: str) -> Path:
    return _split_output_dir() / topic_directory / family_name / "rules.json"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")


def _archive_current_published(
    *,
    topic_id: str,
    topic_label: str,
    topic_directory: str,
    family_name: str,
    published_path: Path,
    current_published: dict[str, Any] | None,
    archive_dir: Path,
) -> None:
    if not published_path.exists():
        raise ValueError("Published rules.json file was not found.")

    timestamp_slug = utc_now().replace(":", "-")
    archive_path = archive_dir / f"{_slug(family_name)}__{timestamp_slug}.json"
    shutil.copyfile(published_path, archive_path)
    db = get_db()

    if current_published is not None:
        db.execute(
            """
            UPDATE rule_versions
            SET state = ?, changed_at = ?, source_json_path = ?, notes = ?
            WHERE id = ?
            """,
            (
                "archived",
                utc_now(),
                str(archive_path),
                "Published version archived during lifecycle transition.",
                current_published["id"],
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO rule_versions (
                id,
                topic_id,
                topic_label,
                topic_directory,
                rule_family,
                state,
                version_number,
                edited_flag,
                changed_by,
                changed_at,
                source_json_path,
                based_on_version_id,
                notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid.uuid4().hex,
                topic_id,
                topic_label,
                topic_directory,
                family_name,
                "archived",
                0,
                0,
                "user",
                utc_now(),
                str(archive_path),
                None,
                "Baseline published version archived during first publish.",
            ),
        )
    db.commit()


def _pretty_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=True)


def _validate_draft_payload(payload: dict[str, Any], topic_id: str, family_name: str) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Draft JSON must be a JSON object.")
    if payload.get("topic_id") != topic_id:
        raise ValueError("Draft topic_id must match the topic being edited.")
    if payload.get("rule_family") != family_name:
        raise ValueError("Draft rule_family must match the selected family.")
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise ValueError("Draft JSON must contain a rules list.")
    if payload.get("rule_count") != len(rules):
        raise ValueError("rule_count must equal the number of rules in the rules list.")

    required_rule_fields = {"id", "type", "topic", "severity", "confidence", "requires_review"}
    for index, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} must be a JSON object.")
        missing = sorted(required_rule_fields - set(rule))
        if missing:
            raise ValueError(f"Rule {index} is missing required fields: {', '.join(missing)}.")
        if rule["topic"] != topic_id:
            raise ValueError(f"Rule {index} topic must remain {topic_id}.")
