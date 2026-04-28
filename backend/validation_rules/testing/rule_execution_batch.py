from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report_loader_core import load_report_model
from .rule_execution import _load_json, evaluate_rule_pack


DEFAULT_SUFFIXES = (".xhtml", ".html", ".htm")


def _iter_input_files(input_dir: Path, *, recursive: bool) -> list[Path]:
    pattern = "**/*" if recursive else "*"
    return sorted(
        path
        for path in input_dir.glob(pattern)
        if path.is_file() and path.suffix.lower() in DEFAULT_SUFFIXES
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated rule packs against all inline XBRL-like HTML files in a directory."
    )
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--split-output-dir", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument("--concepts", default="backend/validation_rules/generated/2026/frs102/concepts.json")
    parser.add_argument("--roles", default="backend/validation_rules/generated/2026/frs102/roles.json")
    parser.add_argument("--output", default=None)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--include-all-topics", action="store_true")
    parser.add_argument("--recursive", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    files = _iter_input_files(input_dir, recursive=args.recursive)
    topics_payload = _load_json(Path(args.topics))
    concepts_payload = _load_json(Path(args.concepts))
    roles_payload = _load_json(Path(args.roles))

    results_dir = Path(args.results_dir) if args.results_dir else None
    if results_dir:
        results_dir.mkdir(parents=True, exist_ok=True)

    file_results: list[dict] = []
    for path in files:
        report = load_report_model(path)
        payload = evaluate_rule_pack(
            report=report,
            split_output_dir=Path(args.split_output_dir),
            topics_payload=topics_payload,
            concepts_payload=concepts_payload,
            roles_payload=roles_payload,
            include_all_topics=args.include_all_topics,
        )
        failing_rules = [result for result in payload["results"] if result["status"] == "fail"]
        file_summary = {
            "input_file": str(path),
            "evaluated_topic_count": payload["evaluated_topic_count"],
            "result_count": payload["result_count"],
            "pass": payload["summary"]["pass"],
            "fail": payload["summary"]["fail"],
            "failing_rule_ids": [result["rule_id"] for result in failing_rules],
        }
        file_results.append(file_summary)
        if results_dir:
            output_path = results_dir / f"{path.stem}_results.json"
            output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    aggregate = {
        "input_dir": str(input_dir),
        "file_count": len(file_results),
        "files_with_failures": sum(1 for item in file_results if item["fail"] > 0),
        "files_without_failures": sum(1 for item in file_results if item["fail"] == 0),
        "total_pass": sum(item["pass"] for item in file_results),
        "total_fail": sum(item["fail"] for item in file_results),
    }
    payload = {
        "topic_scope": "all_topics" if args.include_all_topics else "relevant_topics_only",
        "aggregate": aggregate,
        "files": file_results,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
