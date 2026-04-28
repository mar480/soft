from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from .report_loader_core import load_report_model
from .report_model import ReportContext, ReportFact, ReportModel


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_rule_pack(split_output_dir: Path) -> tuple[dict, dict[str, dict]]:
    manifest = _load_json(split_output_dir / "manifest.json")
    rules_by_topic: dict[str, dict] = {}
    for topic in manifest["topics"]:
        topic_dir = split_output_dir / topic["directory"]
        topic_metadata = _load_json(topic_dir / "topic.json")
        family_payloads: dict[str, list[dict]] = {}
        for family in topic["families"]:
            rules_path = topic_dir / family / "rules.json"
            if rules_path.exists():
                family_payloads[family] = _load_json(rules_path)["rules"]
        rules_by_topic[topic["topic_id"]] = {
            "topic_label": topic["topic_label"],
            "topic_metadata": topic_metadata,
            "families": family_payloads,
        }
    return manifest, rules_by_topic


def _allowed_members_by_topic(topics_payload: dict) -> dict[str, dict[str, set[str]]]:
    mapping: dict[str, dict[str, set[str]]] = {}
    for topic in topics_payload["topics"]:
        dimensions: dict[str, set[str]] = defaultdict(set)
        for cube in topic["hypercubes"]:
            for dimension in cube["dimensions"]:
                stack = list(reversed(dimension.get("member_tree", [])))
                while stack:
                    node = stack.pop()
                    dimensions[dimension["dimension_qname"]].add(node["qname"])
                    stack.extend(reversed(node.get("children", [])))
        mapping[topic["topic_id"]] = dimensions
    return mapping


def _augment_allowed_members_from_rules(
    allowed_members_by_topic: dict[str, dict[str, set[str]]],
    rules_by_topic: dict[str, dict],
) -> None:
    for topic_id, topic_rules in rules_by_topic.items():
        topic_allowed = allowed_members_by_topic.setdefault(topic_id, {})
        for rule in topic_rules["families"].get("6 Dimension member roll-up", []):
            members = topic_allowed.setdefault(rule["dimension"], set())
            members.add(rule["head_member"])
            members.update(rule["component_members"]["members"])


def _fact_context(report: ReportModel, fact: ReportFact) -> ReportContext | None:
    return report.contexts.get(fact.context_id or "")


def _anchor_primary_items(fact: ReportFact) -> set[str]:
    raw = fact.attributes.get("data-anchor-primary-items", "")
    return {item for item in raw.split("|") if item}


def _trigger_concepts(topic_rules: dict) -> set[str]:
    concepts: set[str] = set()
    for rule in topic_rules["families"].get("1 Topic note presence", []):
        concepts.update(rule["trigger"]["statement_concepts_present"]["concepts"])
    return concepts


def _topic_primary_items(topic_rules: dict) -> set[str]:
    primary_items: set[str] = set()
    for rule in topic_rules["families"].get("3 Expected dimension usage", []):
        primary_items.update(rule["trigger"]["primary_items_from_topic_present"]["primary_items"])
    for rule in topic_rules["families"].get("1 Topic note presence", []):
        primary_items.update(rule["expect"].get("topic_primary_items", []))
    return primary_items


def _topic_dimensions(topic_rules: dict) -> set[str]:
    dimensions: set[str] = set()
    for rule in topic_rules["families"].get("1 Topic note presence", []):
        dimensions.update(rule["taxonomy_basis"].get("topic_dimensions", []))
    for rule in topic_rules["families"].get("2 Hypercube conformity", []):
        dimensions.update(rule["taxonomy_basis"].get("dimensions", []))
    for rule in topic_rules["families"].get("3 Expected dimension usage", []):
        dimensions.update(rule["taxonomy_basis"].get("dimensions", []))
    return dimensions


def _topic_fact_reason_map(report: ReportModel, topic_id: str, topic_rules: dict) -> dict[str, list[str]]:
    primary_items = _topic_primary_items(topic_rules)
    topic_dimensions = _topic_dimensions(topic_rules)
    trigger_concepts = _trigger_concepts(topic_rules)
    reason_map: dict[str, list[str]] = {}
    for fact in report.facts:
        reasons: list[str] = []
        if fact.attributes.get("data-topic-id") == topic_id:
            reasons.append("synthetic_topic_id")
        if fact.concept_qname in primary_items:
            reasons.append("topic_primary_item_concept")
        if _anchor_primary_items(fact) & primary_items:
            reasons.append("synthetic_anchor_primary_item")

        context = _fact_context(report, fact)
        if context and context.dimensions:
            overlapping_dimensions = sorted(set(context.dimensions) & topic_dimensions)
            if overlapping_dimensions:
                reasons.append(f"topic_dimensions:{'|'.join(overlapping_dimensions)}")
                if fact.concept_qname in trigger_concepts:
                    reasons.append("trigger_concept_with_topic_dimensions")

        if reasons:
            reason_map[fact.fact_id] = reasons
    return reason_map


def _topic_facts(report: ReportModel, topic_id: str, topic_rules: dict) -> list[ReportFact]:
    reason_map = _topic_fact_reason_map(report, topic_id, topic_rules)
    return [fact for fact in report.facts if fact.fact_id in reason_map]


def _has_strong_topic_fact_evidence(report: ReportModel, topic_id: str, topic_rules: dict) -> bool:
    strong_reasons = {
        "synthetic_topic_id",
        "topic_primary_item_concept",
        "synthetic_anchor_primary_item",
    }
    for reasons in _topic_fact_reason_map(report, topic_id, topic_rules).values():
        if any(reason in strong_reasons for reason in reasons):
            return True
    return False


def _topic_trigger_facts(report: ReportModel, topic_rules: dict) -> list[ReportFact]:
    concepts = _trigger_concepts(topic_rules)
    if not concepts:
        return []
    return [fact for fact in report.facts if fact.concept_qname in concepts]


def _topic_is_relevant(report: ReportModel, topic_id: str, topic_rules: dict) -> bool:
    if _has_strong_topic_fact_evidence(report, topic_id, topic_rules):
        return True
    if _topic_trigger_facts(report, topic_rules):
        return True
    return False


def _topic_note_presence_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules)
    reason_map = _topic_fact_reason_map(report, topic_id, topic_rules)
    results: list[dict] = []
    for rule in topic_rules["families"].get("1 Topic note presence", []):
        trigger_facts = [fact for fact in report.facts if fact.concept_qname in rule["trigger"]["statement_concepts_present"]["concepts"]]
        passed = not trigger_facts or bool(topic_facts)
        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if passed else "fail",
                "topic": topic_id,
                "message": (
                    "Statement trigger concepts are present and note evidence was found."
                    if passed
                    else "Statement trigger concepts are present but no topic note evidence was found."
                ),
                "evidence": {
                    "trigger_fact_ids": [fact.fact_id for fact in trigger_facts],
                    "topic_fact_ids": [fact.fact_id for fact in topic_facts],
                    "topic_fact_reasons": {fact_id: reason_map[fact_id] for fact_id in sorted(reason_map)},
                },
            }
        )
    return results


def _expected_dimension_usage_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules)
    results: list[dict] = []
    for rule in topic_rules["families"].get("3 Expected dimension usage", []):
        allowed_dimensions = set(rule["taxonomy_basis"]["dimensions"])
        passed = not topic_facts or any(
            (_fact_context(report, fact) and (set(_fact_context(report, fact).dimensions) & allowed_dimensions))
            for fact in topic_facts
        )
        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if passed else "fail",
                "topic": topic_id,
                "message": (
                    "At least one topic fact uses an expected dimension."
                    if passed
                    else "Topic facts were found but none used an expected dimension."
                ),
                "evidence": {"topic_fact_ids": [fact.fact_id for fact in topic_facts]},
            }
        )
    return results


def _hypercube_conformity_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules)
    allowed_sets = [set(rule["taxonomy_basis"]["dimensions"]) for rule in topic_rules["families"].get("2 Hypercube conformity", [])]
    invalid_fact_ids: list[str] = []
    for fact in topic_facts:
        context = _fact_context(report, fact)
        if context is None or not context.dimensions:
            continue
        fact_dimensions = set(context.dimensions)
        if not any(fact_dimensions <= allowed for allowed in allowed_sets):
            invalid_fact_ids.append(fact.fact_id)
    return [
        {
            "rule_id": f"{topic_id}.HYPERCUBE_CONFORMITY",
            "type": "hypercube_conformity",
            "status": "pass" if not invalid_fact_ids else "fail",
            "topic": topic_id,
            "message": (
                "All topic fact dimensions fit at least one allowed hypercube."
                if not invalid_fact_ids
                else "Some topic facts use dimension combinations that do not fit any allowed hypercube."
            ),
            "evidence": {"invalid_fact_ids": invalid_fact_ids},
        }
    ]


def _member_validity_results(report: ReportModel, topic_id: str, topic_rules: dict, allowed_members_by_topic: dict[str, dict[str, set[str]]]) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules)
    allowed_map = allowed_members_by_topic.get(topic_id, {})
    invalid_dimension_members: list[dict] = []
    for fact in topic_facts:
        context = _fact_context(report, fact)
        if context is None:
            continue
        for dimension, member in context.dimensions.items():
            allowed_members = allowed_map.get(dimension)
            if allowed_members is not None and member not in allowed_members:
                invalid_dimension_members.append({"fact_id": fact.fact_id, "dimension": dimension, "member": member})
    return [
        {
            "rule_id": f"{topic_id}.MEMBER_VALIDITY",
            "type": "dimension_member_validity",
            "status": "pass" if not invalid_dimension_members else "fail",
            "topic": topic_id,
            "message": (
                "All used dimension members are in the discovered member trees."
                if not invalid_dimension_members
                else "Some dimension members are outside the discovered member trees."
            ),
            "evidence": {"invalid_dimension_members": invalid_dimension_members},
        }
    ]


def _context_signature(context: ReportContext, *, excluding_dimension: str) -> tuple:
    remaining_dimensions = tuple(sorted((dimension, member) for dimension, member in context.dimensions.items() if dimension != excluding_dimension))
    return (context.entity, context.period_type, context.instant, remaining_dimensions)


def _rollup_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules)
    results: list[dict] = []
    for rule in topic_rules["families"].get("6 Dimension member roll-up", []):
        grouped: dict[tuple, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)
        for fact in topic_facts:
            context = _fact_context(report, fact)
            if context is None or rule["dimension"] not in context.dimensions:
                continue
            signature = (fact.concept_qname, fact.unit, _context_signature(context, excluding_dimension=rule["dimension"]))
            grouped[signature].append((fact, context))

        mismatches: list[dict] = []
        component_members = set(rule["component_members"]["members"])
        for signature, entries in grouped.items():
            head_fact = None
            component_total = 0.0
            component_count = 0
            for fact, context in entries:
                member = context.dimensions.get(rule["dimension"])
                if member == rule["head_member"]:
                    head_fact = fact
                elif member in component_members:
                    numeric_value = fact.numeric_value()
                    if numeric_value is not None:
                        component_total += numeric_value
                        component_count += 1
            if head_fact is None or component_count == 0:
                continue
            head_value = head_fact.numeric_value()
            if head_value is None:
                continue
            if abs(head_value - component_total) > 0.0001:
                mismatches.append(
                    {
                        "head_fact_id": head_fact.fact_id,
                        "head_value": head_value,
                        "component_total": component_total,
                    }
                )

        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if not mismatches else "fail",
                "topic": topic_id,
                "message": (
                    "Observed roll-up candidates reconcile where head and component facts are present."
                    if not mismatches
                    else "Observed roll-up candidates do not reconcile."
                ),
                "evidence": {"mismatches": mismatches},
            }
        )
    return results


def evaluate_rule_pack(
    *,
    report: ReportModel,
    split_output_dir: Path,
    topics_payload: dict,
    include_all_topics: bool = False,
) -> dict:
    manifest, rules_by_topic = _load_rule_pack(split_output_dir)
    allowed_members_by_topic = _allowed_members_by_topic(topics_payload)
    _augment_allowed_members_from_rules(allowed_members_by_topic, rules_by_topic)
    results: list[dict] = []
    evaluated_topics: list[dict] = []
    for topic_id, topic_rules in rules_by_topic.items():
        relevant = _topic_is_relevant(report, topic_id, topic_rules)
        if not include_all_topics and not relevant:
            continue
        evaluated_topics.append(
            {
                "topic_id": topic_id,
                "topic_label": topic_rules["topic_label"],
                "relevant": relevant,
                "trigger_fact_count": len(_topic_trigger_facts(report, topic_rules)),
                "topic_fact_count": len(_topic_facts(report, topic_id, topic_rules)),
            }
        )
        results.extend(_topic_note_presence_results(report, topic_id, topic_rules))
        results.extend(_hypercube_conformity_results(report, topic_id, topic_rules))
        results.extend(_expected_dimension_usage_results(report, topic_id, topic_rules))
        results.extend(_member_validity_results(report, topic_id, topic_rules, allowed_members_by_topic))
        results.extend(_rollup_results(report, topic_id, topic_rules))
    return {
        "source_path": report.source_path,
        "entrypoint": manifest["entrypoint"],
        "taxonomy_year": manifest["taxonomy_year"],
        "topic_scope": "all_topics" if include_all_topics else "relevant_topics_only",
        "evaluated_topic_count": len(evaluated_topics),
        "evaluated_topics": evaluated_topics,
        "result_count": len(results),
        "summary": {
            "pass": sum(1 for result in results if result["status"] == "pass"),
            "fail": sum(1 for result in results if result["status"] == "fail"),
        },
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated rule packs against synthetic or real inline XBRL HTML files."
    )
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--split-output-dir", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument("--output", default=None)
    parser.add_argument("--include-all-topics", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = load_report_model(args.input_file)
    payload = evaluate_rule_pack(
        report=report,
        split_output_dir=Path(args.split_output_dir),
        topics_payload=_load_json(Path(args.topics)),
        include_all_topics=args.include_all_topics,
    )
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
