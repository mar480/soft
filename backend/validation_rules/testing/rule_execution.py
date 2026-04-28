from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from .report_loader_core import load_report_model
from .report_model import ReportContext, ReportFact, ReportModel


STRONG_TOPIC_REASONS = {
    "synthetic_topic_id",
    "topic_primary_item_concept",
    "synthetic_anchor_primary_item",
    "topic_specific_dimensions",
}

GENERIC_TOPIC_DIMENSIONS = {
    "bus:GroupCompanyDataDimension",
    "bus:OriginalRevisedDataDimension",
    "common:X-AnalysisDimension",
    "core:ContinuingDiscontinuedOperationsDimension",
    "core:GeographicSegmentsDimension",
    "core:MajorCustomersDimension",
    "core:OperatingSegmentsDimension",
    "core:ProductsServicesDimension",
    "core:RestatementsFirstTimeAdoptionDimension",
    "core:SegmentReconciliationDimension",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _concept_balance_map(concepts_payload: list[dict[str, Any]] | None) -> dict[str, str | None]:
    if not concepts_payload:
        return {}
    return {concept["qname"]: concept.get("balance") for concept in concepts_payload if concept.get("qname")}


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


def rule_pack_manifest(split_output_dir: Path) -> dict:
    manifest, _ = _load_rule_pack(split_output_dir)
    return manifest


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
                specific_dimensions = [dimension for dimension in overlapping_dimensions if dimension not in GENERIC_TOPIC_DIMENSIONS]
                if specific_dimensions:
                    reasons.append(f"topic_specific_dimensions:{'|'.join(specific_dimensions)}")
                if fact.concept_qname in trigger_concepts:
                    reasons.append("trigger_concept_with_topic_dimensions")

        if reasons:
            reason_map[fact.fact_id] = reasons
    return reason_map


def _topic_facts(report: ReportModel, topic_id: str, topic_rules: dict, *, attribution: str = "all") -> list[ReportFact]:
    reason_map = _topic_fact_reason_map(report, topic_id, topic_rules)
    selected_ids: set[str] = set()
    for fact_id, reasons in reason_map.items():
        if attribution == "all":
            selected_ids.add(fact_id)
        elif attribution == "strong":
            if any(reason in STRONG_TOPIC_REASONS or reason.startswith("topic_specific_dimensions:") for reason in reasons):
                selected_ids.add(fact_id)
        else:
            raise ValueError(f"Unknown attribution mode: {attribution}")
    return [fact for fact in report.facts if fact.fact_id in selected_ids]


def _has_strong_topic_fact_evidence(report: ReportModel, topic_id: str, topic_rules: dict) -> bool:
    for reasons in _topic_fact_reason_map(report, topic_id, topic_rules).values():
        if any(reason in STRONG_TOPIC_REASONS or reason.startswith("topic_specific_dimensions:") for reason in reasons):
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
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="all")
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
                    "trigger_fact_count": len(trigger_facts),
                    "topic_fact_count": len(topic_facts),
                },
            }
        )
    return results


def _expected_dimension_usage_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    results: list[dict] = []
    for rule in topic_rules["families"].get("3 Expected dimension usage", []):
        allowed_dimensions = set(rule["taxonomy_basis"]["dimensions"])
        matching_fact_ids = [
            fact.fact_id
            for fact in topic_facts
            if (_fact_context(report, fact) and (set(_fact_context(report, fact).dimensions) & allowed_dimensions))
        ]
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
                "evidence": {
                    "topic_fact_ids": [fact.fact_id for fact in topic_facts],
                    "matching_fact_ids": matching_fact_ids,
                    "topic_fact_count": len(topic_facts),
                    "matching_fact_count": len(matching_fact_ids),
                    "allowed_dimensions": sorted(allowed_dimensions),
                },
            }
        )
    return results


def _hypercube_conformity_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    allowed_sets = [set(rule["taxonomy_basis"]["dimensions"]) for rule in topic_rules["families"].get("2 Hypercube conformity", [])]
    invalid_fact_ids: list[str] = []
    valid_fact_ids: list[str] = []
    for fact in topic_facts:
        context = _fact_context(report, fact)
        if context is None or not context.dimensions:
            continue
        fact_dimensions = set(context.dimensions)
        if not any(fact_dimensions <= allowed for allowed in allowed_sets):
            invalid_fact_ids.append(fact.fact_id)
        else:
            valid_fact_ids.append(fact.fact_id)
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
            "evidence": {
                "invalid_fact_ids": invalid_fact_ids,
                "valid_fact_ids": valid_fact_ids,
                "checked_fact_count": len(valid_fact_ids) + len(invalid_fact_ids),
                "allowed_hypercube_count": len(allowed_sets),
            },
        }
    ]


def _member_validity_results(report: ReportModel, topic_id: str, topic_rules: dict, allowed_members_by_topic: dict[str, dict[str, set[str]]]) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    allowed_map = allowed_members_by_topic.get(topic_id, {})
    invalid_dimension_members: list[dict] = []
    checked_dimension_members: list[dict] = []
    for fact in topic_facts:
        context = _fact_context(report, fact)
        if context is None:
            continue
        for dimension, member in context.dimensions.items():
            allowed_members = allowed_map.get(dimension)
            if allowed_members is not None and member not in allowed_members:
                invalid_dimension_members.append({"fact_id": fact.fact_id, "dimension": dimension, "member": member})
            elif allowed_members is not None:
                checked_dimension_members.append({"fact_id": fact.fact_id, "dimension": dimension, "member": member})
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
            "evidence": {
                "invalid_dimension_members": invalid_dimension_members,
                "checked_dimension_members": checked_dimension_members,
                "checked_dimension_member_count": len(checked_dimension_members),
                "checked_fact_count": len({item["fact_id"] for item in checked_dimension_members}),
            },
        }
    ]


def _context_signature(context: ReportContext, *, excluding_dimension: str) -> tuple:
    remaining_dimensions = tuple(sorted((dimension, member) for dimension, member in context.dimensions.items() if dimension != excluding_dimension))
    return (context.entity, context.period_type, context.instant, remaining_dimensions)


def _rollup_results(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    concept_balances: dict[str, str | None],
) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    results: list[dict] = []
    for rule in topic_rules["families"].get("6 Dimension member roll-up", []):
        grouped: dict[tuple, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)
        for fact in topic_facts:
            context = _fact_context(report, fact)
            if context is None or rule["dimension"] not in context.dimensions:
                continue
            signature = (fact.concept_qname, fact.unit, _context_signature(context, excluding_dimension=rule["dimension"]))
            grouped[signature].append((fact, context))

        comparisons: list[dict] = []
        skipped_comparisons: list[dict] = []
        component_members = set(rule["component_members"]["members"])
        for signature, entries in grouped.items():
            head_fact = None
            extra_head_fact_ids: list[str] = []
            component_total = 0.0
            component_count = 0
            component_facts: list[dict] = []
            skipped_components: list[dict] = []
            for fact, context in entries:
                member = context.dimensions.get(rule["dimension"])
                if member == rule["head_member"]:
                    if head_fact is None:
                        head_fact = fact
                    else:
                        extra_head_fact_ids.append(fact.fact_id)
                elif member in component_members:
                    numeric_value = fact.numeric_value()
                    if numeric_value is not None:
                        component_total += numeric_value
                        component_count += 1
                        component_facts.append(
                            {
                                "fact_id": fact.fact_id,
                                "member": member,
                                "value": numeric_value,
                                "balance": concept_balances.get(fact.concept_qname or ""),
                            }
                        )
                    else:
                        skipped_components.append({"fact_id": fact.fact_id, "member": member, "reason": "non_numeric_component"})
            if head_fact is None:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "missing_head_fact",
                        "component_fact_count": component_count,
                    }
                )
                continue
            if component_count == 0:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "missing_numeric_component_facts",
                        "head_fact_id": head_fact.fact_id,
                        "skipped_components": skipped_components,
                    }
                )
                continue
            head_value = head_fact.numeric_value()
            if head_value is None:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "non_numeric_head_fact",
                        "head_fact_id": head_fact.fact_id,
                        "component_fact_count": component_count,
                    }
                )
                continue
            sign_error_candidates: list[dict[str, Any]] = []
            difference = round(head_value - component_total, 6)
            matches = abs(difference) <= 0.0001
            if not matches:
                for component in component_facts:
                    flipped_total = component_total - (2 * component["value"])
                    flipped_difference = round(head_value - flipped_total, 6)
                    if abs(flipped_difference) <= 0.0001:
                        sign_error_candidates.append(
                            {
                                "kind": "component_sign_inversion",
                                "fact_id": component["fact_id"],
                                "member": component["member"],
                                "raw_value": component["value"],
                                "difference_explained": round(2 * component["value"], 6),
                            }
                        )
                flipped_head_difference = round((-head_value) - component_total, 6)
                if abs(flipped_head_difference) <= 0.0001:
                    sign_error_candidates.append(
                        {
                            "kind": "head_sign_inversion",
                            "fact_id": head_fact.fact_id,
                            "member": rule["head_member"],
                            "raw_value": head_value,
                            "difference_explained": round(2 * head_value, 6),
                        }
                    )
            comparisons.append(
                {
                    "head_fact_id": head_fact.fact_id,
                    "head_member": rule["head_member"],
                    "head_value": head_value,
                    "head_balance": concept_balances.get(head_fact.concept_qname or ""),
                    "component_total": component_total,
                    "difference": difference,
                    "component_facts": component_facts,
                    "matches": matches,
                    "match_status": "matches" if matches else "likely_sign_error" if sign_error_candidates else "mismatch",
                    "sign_error_candidates": sign_error_candidates,
                    "extra_head_fact_ids": extra_head_fact_ids,
                    "skipped_components": skipped_components,
                }
            )

        mismatches = [comparison for comparison in comparisons if not comparison["matches"]]
        likely_sign_errors = [comparison for comparison in mismatches if comparison["sign_error_candidates"]]

        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if not mismatches else "fail",
                "topic": topic_id,
                "message": (
                    "No comparable head/component pairs were available for this roll-up candidate."
                    if not comparisons
                    else "Observed roll-up candidates reconcile where head and component facts are present."
                    if not mismatches
                    else "Observed roll-up candidates do not reconcile; at least one mismatch may be explained by sign inversion."
                    if likely_sign_errors
                    else "Observed roll-up candidates do not reconcile."
                ),
                "evidence": {
                    "comparisons": comparisons,
                    "mismatches": mismatches,
                    "skipped_comparisons": skipped_comparisons,
                    "likely_sign_error_count": len(likely_sign_errors),
                },
            }
        )
    return results


def evaluate_rule_pack(
    *,
    report: ReportModel,
    split_output_dir: Path,
    topics_payload: dict,
    concepts_payload: list[dict[str, Any]] | None = None,
    include_all_topics: bool = False,
    selected_topics: set[str] | None = None,
    selected_families_by_topic: dict[str, set[str]] | None = None,
) -> dict:
    manifest, rules_by_topic = _load_rule_pack(split_output_dir)
    allowed_members_by_topic = _allowed_members_by_topic(topics_payload)
    concept_balances = _concept_balance_map(concepts_payload)
    _augment_allowed_members_from_rules(allowed_members_by_topic, rules_by_topic)
    results: list[dict] = []
    evaluated_topics: list[dict] = []
    topic_scope = "all_topics" if include_all_topics else "selected_topics" if selected_topics else "relevant_topics_only"
    for topic_id, topic_rules in rules_by_topic.items():
        if selected_topics is not None and topic_id not in selected_topics:
            continue
        relevant = _topic_is_relevant(report, topic_id, topic_rules)
        if not include_all_topics and not relevant:
            if selected_topics is None:
                continue
        evaluated_topics.append(
            {
                "topic_id": topic_id,
                "topic_label": topic_rules["topic_label"],
                "relevant": relevant,
                "trigger_fact_count": len(_topic_trigger_facts(report, topic_rules)),
                "topic_fact_count": len(_topic_facts(report, topic_id, topic_rules, attribution="all")),
                "strong_topic_fact_count": len(_topic_facts(report, topic_id, topic_rules, attribution="strong")),
            }
        )
        allowed_families = selected_families_by_topic.get(topic_id) if selected_families_by_topic else None
        if _family_enabled("1 Topic note presence", allowed_families):
            results.extend(_topic_note_presence_results(report, topic_id, topic_rules))
        if _family_enabled("2 Hypercube conformity", allowed_families):
            results.extend(_hypercube_conformity_results(report, topic_id, topic_rules))
        if _family_enabled("3 Expected dimension usage", allowed_families):
            results.extend(_expected_dimension_usage_results(report, topic_id, topic_rules))
        if _family_enabled("4 Member validity", allowed_families):
            results.extend(_member_validity_results(report, topic_id, topic_rules, allowed_members_by_topic))
        if _family_enabled("6 Dimension member roll-up", allowed_families):
            results.extend(_rollup_results(report, topic_id, topic_rules, concept_balances))
    return {
        "source_path": report.source_path,
        "entrypoint": manifest["entrypoint"],
        "taxonomy_year": manifest["taxonomy_year"],
        "topic_scope": topic_scope,
        "selected_topics": sorted(selected_topics) if selected_topics else [],
        "selected_families_by_topic": {
            topic_id: sorted(families) for topic_id, families in (selected_families_by_topic or {}).items()
        },
        "evaluated_topic_count": len(evaluated_topics),
        "evaluated_topics": evaluated_topics,
        "result_count": len(results),
        "summary": {
            "pass": sum(1 for result in results if result["status"] == "pass"),
            "fail": sum(1 for result in results if result["status"] == "fail"),
        },
        "results": results,
    }


def _family_enabled(family_name: str, allowed_families: set[str] | None) -> bool:
    return allowed_families is None or family_name in allowed_families


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated rule packs against synthetic or real inline XBRL HTML files."
    )
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--split-output-dir", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument("--concepts", default="backend/validation_rules/generated/2026/frs102/concepts.json")
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
        concepts_payload=_load_json(Path(args.concepts)),
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
