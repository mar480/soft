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
    groups: list[dict[str, Any]] = []
    for row in rows:
        group = dict(row)
        runs = batch_runs(group["id"])
        group["runs"] = runs
        group["outcome_summary"] = aggregate_outcome_summary(runs)
        group["coverage_summary"] = aggregate_coverage_summary(runs)
        groups.append(group)
    return groups


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
            validation_runs.result_json_path,
            validation_runs.created_at,
            validation_runs.html_report_path,
            source_files.display_name AS source_name,
            source_files.stored_path AS source_path,
            batch_groups.display_name AS batch_name
        FROM validation_runs
        JOIN source_files ON source_files.id = validation_runs.source_file_id
        LEFT JOIN batch_groups ON batch_groups.id = validation_runs.batch_group_id
        ORDER BY validation_runs.created_at DESC
        """
    ).fetchall()
    return [with_run_metrics(dict(row)) for row in rows]


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
    concept_balances = load_concept_balances()
    result = dict(row)
    result["payload"] = payload
    enriched_results = enrich_results(payload["results"], report, concept_balances)
    result["grouped_results"] = group_results(enriched_results)
    result["family_breakdown"] = family_breakdown(enriched_results)
    result["outcome_summary"] = outcome_summary(enriched_results)
    result["coverage_summary"] = coverage_summary(enriched_results)
    result["total_pass"] = result["outcome_summary"]["pass"]
    result["total_fail"] = result["outcome_summary"]["fail"]
    result["score_percent"] = compute_score(result["outcome_summary"])
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
    group["outcome_summary"] = aggregate_outcome_summary(group["runs"])
    group["coverage_summary"] = aggregate_coverage_summary(group["runs"])
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
            validation_runs.result_json_path,
            validation_runs.created_at,
            source_files.display_name AS source_name,
            source_files.stored_path AS source_path
        FROM validation_runs
        JOIN source_files ON source_files.id = validation_runs.source_file_id
        WHERE validation_runs.batch_group_id = ?
        ORDER BY validation_runs.created_at ASC
        """,
        (batch_group_id,),
    ).fetchall()
    return [with_run_metrics(dict(row)) for row in rows]


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
    report = load_report_model(Path(source_record["stored_path"]))
    enriched_results = enrich_results(payload["results"], report, load_concept_balances())
    summarized_outcomes = outcome_summary(enriched_results)
    score_percent = compute_score(summarized_outcomes)
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
                "family_breakdown": family_breakdown(enriched_results),
                "grouped_results": group_results(enriched_results),
                "outcome_summary": summarized_outcomes,
                "coverage_summary": coverage_summary(enriched_results),
                "summary": summarized_outcomes,
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
            summarized_outcomes["pass"],
            summarized_outcomes["fail"],
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
        concepts_payload=_load_json(Path(current_app.config["CONCEPTS_FILE"])),
        roles_payload=_load_json(Path(current_app.config["ROLES_FILE"])),
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
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"pass": 0, "fail": 0, "not_applied": 0})
    for result in results:
        bucket = counts[result["type"]]
        bucket[result.get("outcome_status", result["status"])] += 1
    return [
        {
            "family": family,
            "pass": values["pass"],
            "fail": values["fail"],
            "not_applied": values["not_applied"],
            "total": values["pass"] + values["fail"] + values["not_applied"],
        }
        for family, values in sorted(counts.items())
    ]


def outcome_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "not_applied": 0, "total": len(results)}
    for result in results:
        summary[result.get("outcome_status", result["status"])] += 1
    return summary


def coverage_summary(results: list[dict[str, Any]]) -> dict[str, float | int]:
    summary = outcome_summary(results)
    applied = summary["pass"] + summary["fail"]
    total = summary["total"]
    coverage_percent = round((applied / total) * 100, 1) if total else 0.0
    return {
        "applied": applied,
        "not_applied": summary["not_applied"],
        "total": total,
        "coverage_percent": coverage_percent,
    }


def with_run_metrics(run: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(Path(run["result_json_path"]).read_text(encoding="utf-8"))
    report = load_report_model(Path(run["source_path"]))
    enriched_results = enrich_results(payload["results"], report, load_concept_balances())
    run["outcome_summary"] = outcome_summary(enriched_results)
    run["coverage_summary"] = coverage_summary(enriched_results)
    run["total_pass"] = run["outcome_summary"]["pass"]
    run["total_fail"] = run["outcome_summary"]["fail"]
    run["total_not_applied"] = run["outcome_summary"]["not_applied"]
    run["score_percent"] = compute_score(run["outcome_summary"])
    return run


def aggregate_outcome_summary(runs: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "not_applied": 0, "total": 0}
    for run in runs:
        run_summary = run.get("outcome_summary", {})
        summary["pass"] += run_summary.get("pass", 0)
        summary["fail"] += run_summary.get("fail", 0)
        summary["not_applied"] += run_summary.get("not_applied", 0)
        summary["total"] += run_summary.get("total", 0)
    return summary


def aggregate_coverage_summary(runs: list[dict[str, Any]]) -> dict[str, float | int]:
    summary = aggregate_outcome_summary(runs)
    applied = summary["pass"] + summary["fail"]
    total = summary["total"]
    coverage_percent = round((applied / total) * 100, 1) if total else 0.0
    return {
        "applied": applied,
        "not_applied": summary["not_applied"],
        "total": total,
        "coverage_percent": coverage_percent,
    }


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
                    {
                        "family": family,
                        "results": entries,
                        "outcome_sections": build_outcome_sections(entries),
                    }
                    for family, entries in sorted(families.items())
                ],
            }
        )
    return grouped


def build_outcome_sections(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = ["pass", "fail", "not_applied"]
    labels = {"pass": "Passes", "fail": "Fails", "not_applied": "Not Applied"}
    sections: list[dict[str, Any]] = []
    for status in order:
        items = [result for result in results if result.get("outcome_status") == status]
        if items:
            sections.append({"status": status, "label": labels[status], "results": items})
    return sections


def load_concept_balances() -> dict[str, str | None]:
    concepts = _load_json(Path(current_app.config["CONCEPTS_FILE"]))
    return {concept["qname"]: concept.get("balance") for concept in concepts if concept.get("qname")}


def enrich_results(results: list[dict[str, Any]], report: ReportModel, concept_balances: dict[str, str | None]) -> list[dict[str, Any]]:
    fact_index = {fact.fact_id: fact for fact in report.facts}
    enriched: list[dict[str, Any]] = []
    for result in results:
        result_copy = dict(result)
        result_copy["evidence"] = enrich_evidence(result.get("evidence", {}), fact_index, report.contexts, concept_balances)
        result_copy["outcome_status"] = classify_outcome_status(result_copy)
        result_copy["support"] = build_support_information(result_copy)
        enriched.append(result_copy)
    return enriched


def enrich_evidence(
    evidence: dict[str, Any],
    fact_index: dict[str, ReportFact],
    contexts: dict[str, ReportContext],
    concept_balances: dict[str, str | None],
) -> dict[str, Any]:
    enriched = dict(evidence)

    if "trigger_fact_ids" in evidence:
        enriched["trigger_facts"] = [fact_summary(fact_index[fact_id], contexts, concept_balances) for fact_id in evidence["trigger_fact_ids"] if fact_id in fact_index]
    if "topic_fact_ids" in evidence:
        enriched["topic_facts"] = [fact_summary(fact_index[fact_id], contexts, concept_balances) for fact_id in evidence["topic_fact_ids"] if fact_id in fact_index]
    if "invalid_fact_ids" in evidence:
        enriched["invalid_facts"] = [fact_summary(fact_index[fact_id], contexts, concept_balances) for fact_id in evidence["invalid_fact_ids"] if fact_id in fact_index]
    if "valid_fact_ids" in evidence:
        enriched["valid_facts"] = [fact_summary(fact_index[fact_id], contexts, concept_balances) for fact_id in evidence["valid_fact_ids"] if fact_id in fact_index]
    if "invalid_dimension_members" in evidence:
        enriched["invalid_dimension_member_details"] = [
            {
                **entry,
                "fact": fact_summary(fact_index[entry["fact_id"]], contexts, concept_balances) if entry["fact_id"] in fact_index else None,
            }
            for entry in evidence["invalid_dimension_members"]
        ]
    if "checked_dimension_members" in evidence:
        enriched["checked_dimension_member_details"] = [
            {
                **entry,
                "fact": fact_summary(fact_index[entry["fact_id"]], contexts, concept_balances) if entry["fact_id"] in fact_index else None,
            }
            for entry in evidence["checked_dimension_members"]
        ]
    if "topic_fact_reasons" in evidence:
        enriched["topic_fact_reason_details"] = [
            {
                "fact": fact_summary(fact_index[fact_id], contexts, concept_balances),
                "reasons": reasons,
            }
            for fact_id, reasons in evidence["topic_fact_reasons"].items()
            if fact_id in fact_index
        ]
    if "matching_fact_ids" in evidence:
        enriched["matching_facts"] = [fact_summary(fact_index[fact_id], contexts, concept_balances) for fact_id in evidence["matching_fact_ids"] if fact_id in fact_index]
    if "comparisons" in evidence:
        if evidence.get("taxonomy_basis", {}).get("reason_type") == "movement_bridge":
            enriched["comparison_details"] = [movement_comparison_summary(item, fact_index, contexts, concept_balances) for item in evidence["comparisons"]]
        else:
            enriched["comparison_details"] = [rollup_comparison_summary(item, fact_index, contexts, concept_balances) for item in evidence["comparisons"]]
    if "mismatches" in evidence:
        if evidence.get("taxonomy_basis", {}).get("reason_type") == "movement_bridge":
            enriched["mismatch_details"] = [movement_comparison_summary(item, fact_index, contexts, concept_balances) for item in evidence["mismatches"]]
        else:
            enriched["mismatch_details"] = [rollup_comparison_summary(item, fact_index, contexts, concept_balances) for item in evidence["mismatches"]]
    if "scope_ambiguous" in evidence:
        enriched["scope_ambiguous_details"] = [rollup_comparison_summary(item, fact_index, contexts, concept_balances) for item in evidence["scope_ambiguous"]]
    if "skipped_comparisons" in evidence:
        reason_counts: dict[str, int] = defaultdict(int)
        for item in evidence["skipped_comparisons"]:
            reason_counts[item.get("reason", "unknown")] += 1
        enriched["skipped_reason_counts"] = [
            {"reason": reason, "count": count}
            for reason, count in sorted(reason_counts.items())
        ]
    return enriched


def rollup_comparison_summary(
    comparison: dict[str, Any],
    fact_index: dict[str, ReportFact],
    contexts: dict[str, ReportContext],
    concept_balances: dict[str, str | None],
) -> dict[str, Any]:
    return {
        **comparison,
        "head_fact": fact_summary(fact_index[comparison["head_fact_id"]], contexts, concept_balances) if comparison["head_fact_id"] in fact_index else None,
        "sign_error_candidates": [
            {
                **candidate,
                "fact": fact_summary(fact_index[candidate["fact_id"]], contexts, concept_balances) if candidate.get("fact_id") in fact_index else None,
            }
            for candidate in comparison.get("sign_error_candidates", [])
        ],
        "component_details": [
            {
                **component,
                "fact": fact_summary(fact_index[component["fact_id"]], contexts, concept_balances) if component["fact_id"] in fact_index else None,
            }
            for component in comparison.get("component_facts", [])
        ],
        "cross_dimension_same_concept_details": [
            {
                **item,
                "fact": fact_summary(fact_index[item["fact_id"]], contexts, concept_balances) if item.get("fact_id") in fact_index else None,
            }
            for item in comparison.get("cross_dimension_same_concept_facts", [])
        ],
        "scope_adjustment_details": [
            {
                **adjustment,
                "excluded_facts": [
                    {
                        **item,
                        "fact": fact_summary(fact_index[item["fact_id"]], contexts, concept_balances) if item.get("fact_id") in fact_index else None,
                    }
                    for item in adjustment.get("excluded_facts", [])
                ],
            }
            for adjustment in comparison.get("scope_adjustments", [])
        ],
    }


def movement_comparison_summary(
    comparison: dict[str, Any],
    fact_index: dict[str, ReportFact],
    contexts: dict[str, ReportContext],
    concept_balances: dict[str, str | None],
) -> dict[str, Any]:
    return {
        **comparison,
        "head_fact": fact_summary(fact_index[comparison["head_fact_id"]], contexts, concept_balances) if comparison.get("head_fact_id") in fact_index else None,
        "start_fact": fact_summary(fact_index[comparison["start_fact_id"]], contexts, concept_balances) if comparison.get("start_fact_id") in fact_index else None,
        "end_fact": fact_summary(fact_index[comparison["end_fact_id"]], contexts, concept_balances) if comparison.get("end_fact_id") in fact_index else None,
        "sign_error_candidates": [
            {
                **candidate,
                "fact": fact_summary(fact_index[candidate["fact_id"]], contexts, concept_balances) if candidate.get("fact_id") in fact_index else None,
            }
            for candidate in comparison.get("sign_error_candidates", [])
        ],
        "component_details": [
            {
                **component,
                "fact": fact_summary(fact_index[component["fact_id"]], contexts, concept_balances) if component["fact_id"] in fact_index else None,
            }
            for component in comparison.get("movement_facts", [])
        ],
    }


def fact_summary(fact: ReportFact, contexts: dict[str, ReportContext], concept_balances: dict[str, str | None]) -> dict[str, Any]:
    context = contexts.get(fact.context_id or "")
    period = None
    if context:
        if context.period_type == "duration":
            period = f"{context.start_date} to {context.end_date}".strip()
        else:
            period = context.instant
    return {
        "fact_id": fact.fact_id,
        "name": fact.concept_qname or fact.tag,
        "value": fact.value,
        "period": period,
        "period_type": context.period_type if context else None,
        "dimensions": dict(sorted(context.dimensions.items())) if context else {},
        "unit": fact.unit,
        "balance": concept_balances.get(fact.concept_qname or ""),
    }


def build_support_information(result: dict[str, Any]) -> dict[str, Any]:
    builders = {
        "topic_note_presence": support_topic_note_presence,
        "hypercube_conformity": support_hypercube_conformity,
        "expected_dimension_usage": support_expected_dimension_usage,
        "dimension_member_validity": support_member_validity,
        "dimensional_aggregation_relationship": support_dimensional_aggregation_relationship,
        "movement_reconciliation": support_movement_reconciliation,
        "concept_arithmetic_relationship": support_concept_arithmetic_relationship,
        "dimension_member_rollup_candidate": support_rollup_candidate,
    }
    builder = builders.get(result["type"], support_generic)
    return builder(result)


def support_topic_note_presence(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    trigger_count = evidence.get("trigger_fact_count", len(evidence.get("trigger_facts", [])))
    topic_count = evidence.get("topic_fact_count", len(evidence.get("topic_facts", [])))
    return {
        "what_test_is": "Checks whether statement-level trigger concepts imply that a supporting disclosure note should exist for this topic.",
        "what_it_proves": "A pass shows that when the statement trigger appeared, the engine also found evidence of the corresponding note topic in the report.",
        "outcome_summary": (
            "No statement trigger facts were found, so this rule was not applied."
            if result["outcome_status"] == "not_applied"
            else f"Found {trigger_count} trigger fact(s) and {topic_count} topic fact(s)."
            if result["outcome_status"] == "pass"
            else f"Found {trigger_count} trigger fact(s) but only {topic_count} topic fact(s), so the expected note evidence was not established."
        ),
    }


def support_hypercube_conformity(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    checked = evidence.get("checked_fact_count", 0)
    return {
        "what_test_is": "Checks that the dimensional combinations actually used by topic facts fit at least one allowed taxonomy hypercube for the topic.",
        "what_it_proves": "A pass shows that each checked fact uses only dimension combinations permitted by the discovered topic hypercubes.",
        "outcome_summary": (
            "No dimensional topic facts were available to check against the allowed hypercubes, so this rule was not applied."
            if result["outcome_status"] == "not_applied"
            else
            f"Checked {checked} fact(s) against {evidence.get('allowed_hypercube_count', 0)} allowed hypercube shape(s); all checked facts conformed."
            if result["outcome_status"] == "pass"
            else f"Checked {checked} fact(s); at least one fact used dimensions outside the allowed hypercube shapes."
        ),
    }


def support_expected_dimension_usage(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    topic_count = evidence.get("topic_fact_count", len(evidence.get("topic_facts", [])))
    matching_count = evidence.get("matching_fact_count", len(evidence.get("matching_facts", [])))
    return {
        "what_test_is": "Checks whether facts for the topic actually use one of the dimensions the taxonomy suggests should appear for that disclosure.",
        "what_it_proves": "A pass shows that at least one topic fact used one of the expected taxonomy dimensions for that disclosure.",
        "outcome_summary": (
            "No topic facts were present, so this rule was not applied."
            if result["outcome_status"] == "not_applied"
            else f"Found {matching_count} fact(s) using expected dimensions out of {topic_count} topic fact(s)."
            if result["outcome_status"] == "pass"
            else f"Found {topic_count} topic fact(s), but none used the expected dimensions."
        ),
    }


def support_member_validity(result: dict[str, Any]) -> dict[str, Any]:
    evidence = result["evidence"]
    checked_count = evidence.get("checked_dimension_member_count", len(evidence.get("checked_dimension_member_details", [])))
    checked_fact_count = evidence.get("checked_fact_count", len({item["fact"]["fact_id"] for item in evidence.get("checked_dimension_member_details", []) if item.get("fact")}))
    invalid_count = len(evidence.get("invalid_dimension_member_details", []))
    return {
        "what_test_is": "Checks that the members used on topic dimensions belong to the allowed taxonomy member trees for those dimensions.",
        "what_it_proves": "A pass shows that every checked dimension-member combination matched the discovered taxonomy domain structure, not merely that no invalid list was returned.",
        "outcome_summary": (
            "No dimension-member uses were available to check for this topic, so this rule was not applied."
            if result["outcome_status"] == "not_applied"
            else f"Checked {checked_count} dimension-member use(s) across {checked_fact_count} fact(s); all were valid."
            if result["outcome_status"] == "pass"
            else f"Found {invalid_count} invalid dimension-member use(s) alongside {checked_count} valid checked use(s)."
        ),
    }


def support_rollup_candidate(result: dict[str, Any]) -> dict[str, Any]:
    comparisons = result["evidence"].get("comparison_details", [])
    mismatches = result["evidence"].get("mismatch_details", [])
    likely_sign_error_count = result["evidence"].get("likely_sign_error_count", 0)
    skipped_count = len(result["evidence"].get("skipped_comparisons", []))
    return {
        "what_test_is": "Checks a safe taxonomy-derived roll-up candidate: for the same primary item, period, unit, and all other dimensions, the head member should reconcile to the sum of its component members.",
        "what_it_proves": "This rule is trying to prove that the disclosure behaves like a dimensional total/subtotal relationship suggested by the member tree, without assuming missing values are zero, and it now also diagnoses likely sign inversions against taxonomy balance expectations.",
        "outcome_summary": (
            f"No comparable head/component pairs were present in the report, so this rule was not applied. {skipped_count} candidate bucket(s) were skipped."
            if result["outcome_status"] == "not_applied"
            else f"Observed {len(comparisons)} comparable head/component roll-up case(s); {len(comparisons) - len(mismatches)} reconciled."
            if result["outcome_status"] == "pass"
            else f"Observed {len(comparisons)} comparable head/component roll-up case(s); {len(mismatches)} did not reconcile and {likely_sign_error_count} mismatch(es) look like likely sign inversions."
        ),
    }


def support_concept_arithmetic_relationship(result: dict[str, Any]) -> dict[str, Any]:
    comparisons = result["evidence"].get("comparison_details", [])
    mismatches = result["evidence"].get("mismatch_details", [])
    likely_sign_error_count = result["evidence"].get("likely_sign_error_count", 0)
    skipped_count = len(result["evidence"].get("skipped_comparisons", []))
    return {
        "what_test_is": "Checks a taxonomy-derived concept arithmetic candidate: for the same period, unit, and full dimensional context, the head concept should reconcile to the sum of its component concepts.",
        "what_it_proves": "A pass shows that the observed concept set behaves like a subtotal or arithmetic composition implied by the taxonomy presentation structure, without treating missing components as zero.",
        "outcome_summary": (
            f"No comparable head/component concept sets were present in the report, so this rule was not applied. {skipped_count} candidate bucket(s) were skipped."
            if result["outcome_status"] == "not_applied"
            else f"Observed {len(comparisons)} comparable concept arithmetic case(s); {len(comparisons) - len(mismatches)} reconciled."
            if result["outcome_status"] == "pass"
            else f"Observed {len(comparisons)} comparable concept arithmetic case(s); {len(mismatches)} did not reconcile and {likely_sign_error_count} mismatch(es) look like likely sign inversions."
        ),
    }


def support_dimensional_aggregation_relationship(result: dict[str, Any]) -> dict[str, Any]:
    comparisons = result["evidence"].get("comparison_details", [])
    mismatches = result["evidence"].get("mismatch_details", [])
    scope_ambiguous = result["evidence"].get("scope_ambiguous_details", [])
    likely_sign_error_count = result["evidence"].get("likely_sign_error_count", 0)
    skipped_count = len(result["evidence"].get("skipped_comparisons", []))
    dimension = result["evidence"].get("dimension")
    head_member = result["evidence"].get("head_member")
    aggregation_type = result["evidence"].get("aggregation_type", "plain")
    scope_dimensions = result["evidence"].get("scope_dimensions", [])
    scoped_summary = ""
    if aggregation_type == "scoped" and scope_dimensions:
        scope_bits = []
        for scope in scope_dimensions:
            members = ", ".join(scope.get("excluded_members", []))
            scope_bits.append(f"{scope.get('dimension')} excluding {members}")
        scoped_summary = " Scope applied: " + "; ".join(scope_bits) + "."
    return {
        "what_test_is": "Checks a taxonomy-derived dimensional aggregation candidate: for the same concept, period, unit, and all other dimensions, the total/default member should reconcile to the sum of its component members."
        if aggregation_type == "plain"
        else "Checks a taxonomy-derived scoped dimensional aggregation candidate: for the same concept, period, unit, and all other relevant dimensions, the scoped total/default member should reconcile to the sum of its component members after applying the declared scope exclusions.",
        "what_it_proves": "A pass shows that the disclosure behaves like a dimensional aggregation relationship supported by the taxonomy member tree, without treating missing members as zero."
        if aggregation_type == "plain"
        else "A pass shows that the disclosure behaves like a scoped dimensional aggregation relationship supported by the taxonomy member tree and scope exclusions, without treating missing members as zero.",
        "outcome_summary": (
            f"No comparable dimensional aggregation sets were present in the report for {dimension or 'the target dimension'}, so this rule was not applied. {skipped_count} candidate bucket(s) were skipped.{scoped_summary}"
            if result["outcome_status"] == "not_applied"
            else f"Observed {len(comparisons)} dimensional aggregation case(s), but all apparent mismatches were explained by same-concept facts in other dimensional scopes, so this candidate was treated as ambiguous rather than failed."
            if scope_ambiguous and not mismatches
            else f"Observed {len(comparisons)} comparable dimensional aggregation case(s) for {dimension or 'the target dimension'} using head member {head_member or '-'}; {len(comparisons) - len(mismatches)} reconciled.{scoped_summary}"
            if result["outcome_status"] == "pass"
            else f"Observed {len(comparisons)} comparable dimensional aggregation case(s) for {dimension or 'the target dimension'}; {len(mismatches)} did not reconcile and {likely_sign_error_count} mismatch(es) look like likely sign inversions.{scoped_summary}"
        ),
    }


def support_movement_reconciliation(result: dict[str, Any]) -> dict[str, Any]:
    comparisons = result["evidence"].get("comparison_details", [])
    mismatches = result["evidence"].get("mismatch_details", [])
    likely_sign_error_count = result["evidence"].get("likely_sign_error_count", 0)
    skipped_count = len(result["evidence"].get("skipped_comparisons", []))
    return {
        "what_test_is": "Checks a movement bridge candidate: for the same concept, unit, entity, and dimensional context, the signed movement facts for the duration should reconcile to the closing instant minus the opening instant.",
        "what_it_proves": "A pass shows that the disclosure behaves like a movement reconciliation, with opening and closing balances bridged by signed duration movements using taxonomy balance semantics.",
        "outcome_summary": (
            f"No comparable opening/closing movement bridges were present in the report, so this rule was not applied. {skipped_count} candidate bridge(s) were skipped."
            if result["outcome_status"] == "not_applied"
            else f"Observed {len(comparisons)} comparable movement bridge(s); {len(comparisons) - len(mismatches)} reconciled."
            if result["outcome_status"] == "pass"
            else f"Observed {len(comparisons)} comparable movement bridge(s); {len(mismatches)} did not reconcile and {likely_sign_error_count} mismatch(es) look like likely sign inversions."
        ),
    }


def support_generic(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "what_test_is": "Checks the rule condition for the current topic and family.",
        "what_it_proves": "A pass means the observed facts were consistent with the rule condition; a fail means the rule condition was contradicted.",
        "outcome_summary": result.get("message", ""),
    }


def classify_outcome_status(result: dict[str, Any]) -> str:
    evidence = result.get("evidence", {})
    if result["type"] == "topic_note_presence":
        return "not_applied" if not evidence.get("trigger_facts") else result["status"]
    if result["type"] == "expected_dimension_usage":
        return "not_applied" if not evidence.get("topic_facts") else result["status"]
    if result["type"] == "hypercube_conformity":
        checked = evidence.get("checked_fact_count", len(evidence.get("valid_facts", [])) + len(evidence.get("invalid_facts", [])))
        return "not_applied" if checked == 0 else result["status"]
    if result["type"] == "dimension_member_validity":
        checked = evidence.get("checked_dimension_member_count", len(evidence.get("checked_dimension_member_details", [])))
        invalid = len(evidence.get("invalid_dimension_member_details", []))
        return "not_applied" if checked == 0 and invalid == 0 else result["status"]
    if result["type"] == "dimension_member_rollup_candidate":
        return "not_applied" if not evidence.get("comparison_details") else result["status"]
    if result["type"] == "dimensional_aggregation_relationship":
        if not evidence.get("comparison_details"):
            return "not_applied"
        if evidence.get("scope_ambiguous_details") and not evidence.get("mismatch_details"):
            return "not_applied"
        return result["status"]
    if result["type"] == "concept_arithmetic_relationship":
        return "not_applied" if not evidence.get("comparison_details") else result["status"]
    if result["type"] == "movement_reconciliation":
        return "not_applied" if not evidence.get("comparison_details") else result["status"]
    return result["status"]


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
