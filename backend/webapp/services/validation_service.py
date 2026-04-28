from __future__ import annotations

import json
import shutil
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from flask import current_app, render_template

from backend.validation_rules.testing.report_loader_core import load_report_model
from backend.validation_rules.testing.report_model import ReportContext, ReportFact, ReportModel
from backend.validation_rules.testing.rule_execution import _load_json, evaluate_rule_pack, rule_pack_manifest

from ..db import get_db


def synthetic_example_paths() -> list[Path]:
    examples_dir = Path(current_app.config["SYNTHETIC_EXAMPLES_DIR"])
    return sorted(examples_dir.glob("*.html"))


def validation_topic_options() -> list[dict[str, Any]]:
    manifest = rule_pack_manifest(Path(current_app.config["SPLIT_OUTPUT_DIR"]))
    return [
        {
            "topic_id": topic["topic_id"],
            "topic_label": topic["topic_label"],
            "families": topic["families"],
        }
        for topic in manifest["topics"]
    ]


def stored_source_files() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT id, display_name, source_type, stored_path, created_at
        FROM source_files
        ORDER BY created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def recent_batch_groups() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT
            batch_groups.id,
            batch_groups.display_name,
            batch_groups.created_at,
            COUNT(validation_runs.id) AS run_count
        FROM batch_groups
        LEFT JOIN validation_runs ON validation_runs.batch_group_id = batch_groups.id
        GROUP BY batch_groups.id, batch_groups.display_name, batch_groups.created_at
        ORDER BY batch_groups.created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def recent_validation_runs() -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT
            validation_runs.id,
            validation_runs.batch_group_id,
            validation_runs.report_name,
            validation_runs.taxonomy_year,
            validation_runs.taxonomy_entrypoint,
            validation_runs.total_pass,
            validation_runs.total_fail,
            validation_runs.score_percent,
            validation_runs.created_at,
            validation_runs.html_report_path,
            source_files.display_name AS source_name,
            batch_groups.display_name AS batch_name
        FROM validation_runs
        JOIN source_files ON source_files.id = validation_runs.source_file_id
        LEFT JOIN batch_groups ON batch_groups.id = validation_runs.batch_group_id
        ORDER BY validation_runs.created_at DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def get_validation_run(run_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT
            validation_runs.*,
            source_files.display_name AS source_name,
            source_files.source_type AS source_type,
            source_files.stored_path AS source_path,
            batch_groups.display_name AS batch_name
        FROM validation_runs
        JOIN source_files ON source_files.id = validation_runs.source_file_id
        LEFT JOIN batch_groups ON batch_groups.id = validation_runs.batch_group_id
        WHERE validation_runs.id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None

    payload = json.loads(Path(row["result_json_path"]).read_text(encoding="utf-8"))
    report = load_report_model(Path(row["source_path"]))
    result = dict(row)
    result["payload"] = payload
    enriched_results = enrich_results(payload["results"], report)
    result["grouped_results"] = group_results(enriched_results)
    result["family_breakdown"] = family_breakdown(payload["results"])
    result["gauge_segments"] = gauge_segments(result["score_percent"])
    return result


def get_batch_group(batch_group_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT id, display_name, created_at
        FROM batch_groups
        WHERE id = ?
        """,
        (batch_group_id,),
    ).fetchone()
    if row is None:
        return None
    group = dict(row)
    group["runs"] = batch_runs(batch_group_id)
    return group


def batch_runs(batch_group_id: str) -> list[dict[str, Any]]:
    rows = get_db().execute(
        """
        SELECT
            validation_runs.id,
            validation_runs.report_name,
            validation_runs.taxonomy_year,
            validation_runs.taxonomy_entrypoint,
            validation_runs.total_pass,
            validation_runs.total_fail,
            validation_runs.score_percent,
            validation_runs.created_at,
            source_files.display_name AS source_name
        FROM validation_runs
        JOIN source_files ON source_files.id = validation_runs.source_file_id
        WHERE validation_runs.batch_group_id = ?
        ORDER BY validation_runs.created_at ASC
        """,
        (batch_group_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def prepare_submission(
    *,
    synthetic_path: str | None,
    stored_source_id: str | None,
    upload_files: list,
    validation_scope: str,
    selected_topics: list[str],
    selected_family_keys: list[str],
) -> dict[str, Any]:
    populated = sum(
        1
        for present in (
            bool(synthetic_path),
            bool(stored_source_id),
            bool([file for file in upload_files if getattr(file, "filename", "")]),
        )
        if present
    )
    if populated == 0:
        raise ValueError("Choose a synthetic example, a stored source, or one or more local files.")
    if populated > 1:
        raise ValueError("Choose only one loading method at a time.")
    validation_selection = parse_validation_selection(
        validation_scope=validation_scope,
        selected_topics=selected_topics,
        selected_family_keys=selected_family_keys,
    )

    valid_uploads = [file for file in upload_files if getattr(file, "filename", "")]
    if valid_uploads:
        records = [resolve_source_file(input_mode="upload", selected_path=None, upload=file) for file in valid_uploads]
        return {"mode": "batch" if len(records) > 1 else "single", "source_records": records, "validation_selection": validation_selection}
    if stored_source_id:
        return {
            "mode": "single",
            "source_records": [resolve_source_file(input_mode="stored", selected_path=stored_source_id, upload=None)],
            "validation_selection": validation_selection,
        }
    return {
        "mode": "single",
        "source_records": [resolve_source_file(input_mode="synthetic", selected_path=synthetic_path, upload=None)],
        "validation_selection": validation_selection,
    }


def perform_validation_run(
    *,
    source_file_id: str,
    batch_group_id: str | None = None,
    validation_selection: dict[str, Any] | None = None,
    progress_callback=None,
) -> str:
    source_record = get_source_file(source_file_id)
    if source_record is None:
        raise ValueError("Source file not found for validation job.")

    timestamp = utc_now()
    run_id = uuid.uuid4().hex
    report_name = f"{Path(source_record['display_name']).stem} {timestamp[:19].replace(':', '-')}"
    report_dir = Path(current_app.config["REPORT_FOLDER"]) / run_id
    report_dir.mkdir(parents=True, exist_ok=True)

    notify_progress(progress_callback, 15, "Creating immutable rule snapshot.")
    rule_snapshot_path = snapshot_rule_pack(run_id)
    notify_progress(progress_callback, 35, "Loading report model.")
    payload = evaluate_source_file(source_record["stored_path"], validation_selection=validation_selection)
    notify_progress(progress_callback, 70, "Compiling validation results.")
    score_percent = compute_score(payload["summary"])
    result_json_path = report_dir / "results.json"
    result_json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    notify_progress(progress_callback, 85, "Rendering HTML report.")
    html_report_path = report_dir / "report.html"
    html_report_path.write_text(
        render_template(
            "report_export.html",
            run={
                "report_name": report_name,
                "taxonomy_year": current_app.config["TAXONOMY_YEAR"],
                "taxonomy_entrypoint": current_app.config["TAXONOMY_ENTRYPOINT"],
                "score_percent": score_percent,
                "source_name": source_record["display_name"],
                "created_at": timestamp,
                "family_breakdown": family_breakdown(payload["results"]),
                "grouped_results": group_results(payload["results"]),
                "summary": payload["summary"],
            },
        ),
        encoding="utf-8",
    )

    notify_progress(progress_callback, 95, "Saving validation run.")
    db = get_db()
    db.execute(
        """
        INSERT INTO validation_runs (
            id,
            source_file_id,
            batch_group_id,
            report_name,
            taxonomy_year,
            taxonomy_entrypoint,
            rule_pack_version,
            rule_snapshot_path,
            include_all_topics,
            total_pass,
            total_fail,
            score_percent,
            result_json_path,
            html_report_path,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            source_record["id"],
            batch_group_id,
            report_name,
            current_app.config["TAXONOMY_YEAR"],
            current_app.config["TAXONOMY_ENTRYPOINT"],
            f"{current_app.config['TAXONOMY_YEAR']} {current_app.config['TAXONOMY_ENTRYPOINT']}",
            str(rule_snapshot_path),
            0,
            payload["summary"]["pass"],
            payload["summary"]["fail"],
            score_percent,
            str(result_json_path),
            str(html_report_path),
            timestamp,
        ),
    )
    db.commit()
    return run_id


def create_batch_group(*, source_records: list[dict[str, Any]]) -> dict[str, Any]:
    batch_id = uuid.uuid4().hex
    timestamp = utc_now()[:19].replace(":", "-")
    display_name = f"Batch {timestamp}"
    db = get_db()
    db.execute(
        """
        INSERT INTO batch_groups (id, display_name, created_at)
        VALUES (?, ?, ?)
        """,
        (batch_id, display_name, utc_now()),
    )
    db.commit()
    return {"id": batch_id, "display_name": display_name, "item_count": len(source_records)}


def resolve_source_file(*, input_mode: str, selected_path: str | None, upload) -> dict[str, Any]:
    db = get_db()
    if input_mode == "synthetic":
        if not selected_path:
            raise ValueError("Choose a synthetic example.")
        path = Path(selected_path)
        source_id = uuid.uuid4().hex
        record = {
            "id": source_id,
            "display_name": path.name,
            "source_type": "synthetic",
            "stored_path": str(path),
            "created_at": utc_now(),
        }
        db.execute(
            """
            INSERT INTO source_files (id, display_name, source_type, stored_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record["id"], record["display_name"], record["source_type"], record["stored_path"], record["created_at"]),
        )
        db.commit()
        return record

    if input_mode == "stored":
        if not selected_path:
            raise ValueError("Choose a stored source file.")
        row = db.execute(
            """
            SELECT id, display_name, source_type, stored_path, created_at
            FROM source_files
            WHERE id = ?
            """,
            (selected_path,),
        ).fetchone()
        if row is None:
            raise ValueError("Stored source file not found.")
        return dict(row)

    if input_mode == "upload":
        if upload is None or not upload.filename:
            raise ValueError("Choose a local HTML file to upload.")
        source_id = uuid.uuid4().hex
        destination = Path(current_app.config["UPLOAD_FOLDER"]) / f"{source_id}_{Path(upload.filename).name}"
        upload.save(destination)
        record = {
            "id": source_id,
            "display_name": Path(upload.filename).name,
            "source_type": "upload",
            "stored_path": str(destination),
            "created_at": utc_now(),
        }
        db.execute(
            """
            INSERT INTO source_files (id, display_name, source_type, stored_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (record["id"], record["display_name"], record["source_type"], record["stored_path"], record["created_at"]),
        )
        db.commit()
        return record

    raise ValueError("Unknown input mode.")


def get_source_file(source_file_id: str) -> dict[str, Any] | None:
    row = get_db().execute(
        """
        SELECT id, display_name, source_type, stored_path, created_at
        FROM source_files
        WHERE id = ?
        """,
        (source_file_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def evaluate_source_file(source_path: str, validation_selection: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_selection = validation_selection or default_validation_selection()
    report = load_report_model(Path(source_path))
    return evaluate_rule_pack(
        report=report,
        split_output_dir=Path(current_app.config["SPLIT_OUTPUT_DIR"]),
        topics_payload=_load_json(Path(current_app.config["TOPICS_FILE"])),
        include_all_topics=validation_selection["scope"] == "all",
        selected_topics=set(validation_selection["selected_topics"]) if validation_selection["scope"] == "selected" else None,
        selected_families_by_topic=validation_selection["selected_families_by_topic"] if validation_selection["scope"] == "selected" else None,
    )


def snapshot_rule_pack(run_id: str) -> Path:
    source = Path(current_app.config["SPLIT_OUTPUT_DIR"])
    destination = Path(current_app.config["RULE_SNAPSHOT_FOLDER"]) / run_id
    shutil.copytree(source, destination, dirs_exist_ok=True)
    return destination


def compute_score(summary: dict[str, int]) -> float:
    total = summary["pass"] + summary["fail"]
    if total == 0:
        return 0.0
    return round((summary["pass"] / total) * 100, 1)


def family_breakdown(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0})
    for result in results:
        bucket = counts[result["type"]]
        bucket[result["status"]] += 1
    return [
        {
            "family": family,
            "pass": values["pass"],
            "fail": values["fail"],
            "total": values["pass"] + values["fail"],
        }
        for family, values in sorted(counts.items())
    ]


def group_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    topics: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for result in results:
        topics[result["topic"]][result["type"]].append(result)
    grouped: list[dict[str, Any]] = []
    for topic, families in sorted(topics.items()):
        grouped.append(
            {
                "topic": topic,
                "families": [
                    {"family": family, "results": entries}
                    for family, entries in sorted(families.items())
                ],
            }
        )
    return grouped


def enrich_results(results: list[dict[str, Any]], report: ReportModel) -> list[dict[str, Any]]:
    fact_index = {fact.fact_id: fact for fact in report.facts}
    enriched: list[dict[str, Any]] = []
    for result in results:
        result_copy = dict(result)
        result_copy["evidence"] = enrich_evidence(result.get("evidence", {}), fact_index, report.contexts)
        result_copy["support"] = build_support_information(result_copy)
        enriched.append(result_copy)
    return enriched


def enrich_evidence(
    evidence: dict[str, Any],
    fact_index: dict[str, ReportFact],
    contexts: dict[str, ReportContext],
) -> dict[str, Any]:
    enriched = dict(evidence)

    if "trigger_fact_ids" in evidence:
        enriched["trigger_facts"] = [fact_summary(fact_index[fact_id], contexts) for fact_id in evidence["trigger_fact_ids"] if fact_id in fact_index]
    if "topic_fact_ids" in evidence:
        enriched["topic_facts"] = [fact_summary(fact_index[fact_id], contexts) for fact_id in evidence["topic_fact_ids"] if fact_id in fact_index]
    if "invalid_fact_ids" in evidence:
        enriched["invalid_facts"] = [fact_summary(fact_index[fact_id], contexts) for fact_id in evidence["invalid_fact_ids"] if fact_id in fact_index]
    if "valid_fact_ids" in evidence:
        enriched["valid_facts"] = [fact_summary(fact_index[fact_id], contexts) for fact_id in evidence["valid_fact_ids"] if fact_id in fact_index]
    if "invalid_dimension_members" in evidence:
        enriched["invalid_dimension_member_details"] = [
            {
                **entry,
                "fact": fact_summary(fact_index[entry["fact_id"]], contexts) if entry["fact_id"] in fact_index else None,
            }
            for entry in evidence["invalid_dimension_members"]
        ]
    if "checked_dimension_members" in evidence:
        enriched["checked_dimension_member_details"] = [
            {
                **entry,
                "fact": fact_summary(fact_index[entry["fact_id"]], contexts) if entry["fact_id"] in fact_index else None,
            }
            for entry in evidence["checked_dimension_members"]
        ]
    if "topic_fact_reasons" in evidence:
        enriched["topic_fact_reason_details"] = [
            {
                "fact": fact_summary(fact_index[fact_id], contexts),
                "reasons": reasons,
            }
            for fact_id, reasons in evidence["topic_fact_reasons"].items()
            if fact_id in fact_index
        ]
    if "matching_fact_ids" in evidence:
        enriched["matching_facts"] = [fact_summary(fact_index[fact_id], contexts) for fact_id in evidence["matching_fact_ids"] if fact_id in fact_index]
    if "comparisons" in evidence:
        enriched["comparison_details"] = [rollup_comparison_summary(item, fact_index, contexts) for item in evidence["comparisons"]]
    if "mismatches" in evidence:
        enriched["mismatch_details"] = [rollup_comparison_summary(item, fact_index, contexts) for item in evidence["mismatches"]]
    return enriched


def rollup_comparison_summary(
    comparison: dict[str, Any],
    fact_index: dict[str, ReportFact],
    contexts: dict[str, ReportContext],
) -> dict[str, Any]:
    return {
        **comparison,
        "head_fact": fact_summary(fact_index[comparison["head_fact_id"]], contexts) if comparison["head_fact_id"] in fact_index else None,
        "component_details": [
            {
                **component,
                "fact": fact_summary(fact_index[component["fact_id"]], contexts) if component["fact_id"] in fact_index else None,
            }
            for component in comparison.get("component_facts", [])
        ],
    }


def fact_summary(fact: ReportFact, contexts: dict[str, ReportContext]) -> dict[str, Any]:
    context = contexts.get(fact.context_id or "")
    return {
        "fact_id": fact.fact_id,
        "name": fact.concept_qname or fact.tag,
        "value": fact.value,
        "period": context.instant if context else None,
        "period_type": context.period_type if context else None,
        "dimensions": dict(sorted(context.dimensions.items())) if context else {},
        "unit": fact.unit,
    }


def build_support_information(result: dict[str, Any]) -> dict[str, Any]:
    builders = {
        "topic_note_presence": support_topic_note_presence,
        "hypercube_conformity": support_hypercube_conformity,
        "expected_dimension_usage": support_expected_dimension_usage,
        "dimension_member_validity": support_member_validity,
        "dimension_member_rollup_candidate": support_rollup_candidate,
    }
    builder = builders.get(result["type"], support_generic)
    return builder(result)


def support_topic_note_presence(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    return {
        "what_test_is": "Checks whether statement-level trigger concepts imply that a supporting disclosure note should exist for this topic.",
        "what_it_proves": "A pass shows that when the statement trigger appeared, the engine also found evidence of the corresponding note topic in the report.",
        "outcome_summary": (
            f"Found {evidence.get('trigger_fact_count', 0)} trigger fact(s) and {evidence.get('topic_fact_count', 0)} topic fact(s)."
            if result["status"] == "pass"
            else f"Found {evidence.get('trigger_fact_count', 0)} trigger fact(s) but no topic facts."
        ),
    }


def support_hypercube_conformity(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    checked = evidence.get("checked_fact_count", 0)
    return {
        "what_test_is": "Checks that the dimensional combinations actually used by topic facts fit at least one allowed taxonomy hypercube for the topic.",
        "what_it_proves": "A pass shows that each checked fact uses only dimension combinations permitted by the discovered topic hypercubes.",
        "outcome_summary": (
            f"Checked {checked} fact(s) against {evidence.get('allowed_hypercube_count', 0)} allowed hypercube shape(s); all checked facts conformed."
            if result["status"] == "pass"
            else f"Checked {checked} fact(s); at least one fact used dimensions outside the allowed hypercube shapes."
        ),
    }


def support_expected_dimension_usage(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    return {
        "what_test_is": "Checks whether facts for the topic actually use one of the dimensions the taxonomy suggests should appear for that disclosure.",
        "what_it_proves": "A pass shows either that no topic facts were present to test, or that at least one topic fact used an expected topic dimension.",
        "outcome_summary": (
            f"Found {evidence.get('matching_fact_count', 0)} matching fact(s) out of {evidence.get('topic_fact_count', 0)} topic fact(s)."
            if evidence.get("topic_fact_count", 0)
            else "No topic facts were present, so there was no dimensional usage to test."
        ),
    }


def support_member_validity(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    return {
        "what_test_is": "Checks that the members used on topic dimensions belong to the allowed taxonomy member trees for those dimensions.",
        "what_it_proves": "A pass shows that every checked dimension-member combination matched the discovered taxonomy domain structure, not merely that no invalid list was returned.",
        "outcome_summary": (
            f"Checked {evidence.get('checked_dimension_member_count', 0)} dimension-member use(s) across {evidence.get('checked_fact_count', 0)} fact(s); all were valid."
            if result["status"] == "pass"
            else f"Checked {evidence.get('checked_dimension_member_count', 0)} valid use(s) and found {len(evidence.get('invalid_dimension_member_details', []))} invalid member use(s)."
        ),
    }


def support_rollup_candidate(result: dict[str, Any]) -> dict[str, Any]:
    comparisons = result["evidence"].get("comparison_details", [])
    mismatches = result["evidence"].get("mismatch_details", [])
    return {
        "what_test_is": "Checks a safe taxonomy-derived roll-up candidate: for the same primary item, period, unit, and all other dimensions, the head member should reconcile to the sum of its component members.",
        "what_it_proves": "This rule is trying to prove that the disclosure behaves like a dimensional total/subtotal relationship suggested by the member tree, without assuming missing values are zero.",
        "outcome_summary": (
            f"Observed {len(comparisons)} comparable head/component roll-up case(s); {len(comparisons) - len(mismatches)} reconciled."
            if comparisons
            else "No comparable head/component pairs were present in the report, so this run did not positively prove or disprove the suggested roll-up."
        ),
    }


def support_generic(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "what_test_is": "Checks the rule condition for the current topic and family.",
        "what_it_proves": "A pass means the observed facts were consistent with the rule condition; a fail means the rule condition was contradicted.",
        "outcome_summary": result.get("message", ""),
    }


def gauge_segments(score_percent: float) -> list[dict[str, Any]]:
    active_index = min(int(score_percent // 20), 4) if score_percent > 0 else 0
    palette = ["critical", "high-risk", "warning", "good", "excellent"]
    return [
        {"name": name, "active": index <= active_index if score_percent > 0 else index == 0}
        for index, name in enumerate(palette)
    ]


def notify_progress(progress_callback, percent: int, message: str) -> None:
    if progress_callback is not None:
        progress_callback(percent, message)


def parse_validation_selection(
    *,
    validation_scope: str,
    selected_topics: list[str],
    selected_family_keys: list[str],
) -> dict[str, Any]:
    scope = validation_scope or "auto"
    if scope not in {"auto", "selected", "all"}:
        raise ValueError("Unknown validation scope.")
    selected_topics = [topic for topic in selected_topics if topic]
    if scope == "selected" and not selected_topics:
        raise ValueError("Choose at least one topic when using selected-topics validation.")

    selected_families_by_topic: dict[str, set[str]] = defaultdict(set)
    for key in selected_family_keys:
        if "::" not in key:
            continue
        topic_id, family_name = key.split("::", 1)
        selected_families_by_topic[topic_id].add(family_name)

    if scope == "selected":
        manifest_topics = {topic["topic_id"]: set(topic["families"]) for topic in validation_topic_options()}
        for topic_id in selected_topics:
            if not selected_families_by_topic.get(topic_id):
                selected_families_by_topic[topic_id] = set(manifest_topics.get(topic_id, set()))

    return {
        "scope": scope,
        "selected_topics": selected_topics,
        "selected_families_by_topic": {topic_id: set(families) for topic_id, families in selected_families_by_topic.items()},
    }


def default_validation_selection() -> dict[str, Any]:
    return {"scope": "auto", "selected_topics": [], "selected_families_by_topic": {}}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
