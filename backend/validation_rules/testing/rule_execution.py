from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from itertools import combinations
from pathlib import Path
import re
from typing import Any

from .report_loader_core import load_report_model
from .report_model import ReportContext, ReportFact, ReportModel


STRONG_TOPIC_REASONS = {
    "synthetic_topic_id",
    "topic_primary_item_concept",
    "synthetic_anchor_primary_item",
    "topic_specific_dimensions",
}

NOTE_PRESENCE_TOPIC_REASONS = {
    "synthetic_topic_id",
    "topic_primary_item_concept",
    "synthetic_anchor_primary_item",
}

GENERIC_TOPIC_DIMENSIONS = {
    "bus:GroupCompanyDataDimension",
    "bus:OriginalRevisedDataDimension",
    "common:X-AnalysisDimension",
    "core:ContinuingDiscontinuedOperationsDimension",
    "core:FinancialInstrumentCurrentNon-currentDimension",
    "core:GeographicSegmentsDimension",
    "core:MajorCustomersDimension",
    "core:OperatingSegmentsDimension",
    "core:ProductsServicesDimension",
    "core:RestatementsFirstTimeAdoptionDimension",
    "core:SegmentReconciliationDimension",
}

MAX_ARITHMETIC_COMPONENT_CONCEPTS = 8
MANDATORY_FAMILY_NAME = "9 Mandatory tags"

CONCEPT_ARITHMETIC_COMPONENT_ALIASES = {
    "core:CashBankOnHand": {"core:CashCashEquivalents"},
    "core:Creditors": {"core:TradeOtherPayables"},
    "core:Debtors": {"core:TradeOtherReceivables"},
    "core:FixedAssets": {"core:Non-currentAssets"},
    "core:IntangibleAssets": {"core:IntangibleAssetsIncludingRight-of-useAssets"},
    "core:OtherDebtorsBalanceSheetSubtotal": {"core:TradeOtherReceivables"},
    "core:PropertyPlantEquipment": {"core:PropertyPlantEquipmentIncludingRight-of-useAssets"},
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _concept_balance_map(concepts_payload: list[dict[str, Any]] | None) -> dict[str, str | None]:
    if not concepts_payload:
        return {}
    return {concept["qname"]: concept.get("balance") for concept in concepts_payload if concept.get("qname")}


def _concept_metadata_map(concepts_payload: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    if not concepts_payload:
        return {}
    return {concept["qname"]: concept for concept in concepts_payload if concept.get("qname")}


def _presentation_parent_child_graph(roles_payload: dict[str, Any] | None) -> dict[str, set[str]]:
    if not roles_payload:
        return {}
    graph: dict[str, set[str]] = defaultdict(set)
    for relationship in roles_payload.get("relationships", []):
        if relationship.get("arcrole_name") != "parent_child":
            continue
        parent = relationship.get("from_qname")
        child = relationship.get("to_qname")
        if not parent or not child or parent == child:
            continue
        graph[parent].add(child)
    return graph


def _presentation_role_graph(roles_payload: dict[str, Any] | None) -> dict[str, dict[str, set[str]]]:
    if not roles_payload:
        return {}
    graph: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for relationship in roles_payload.get("relationships", []):
        if relationship.get("arcrole_name") != "parent_child":
            continue
        role_uri = relationship.get("role_uri")
        parent = relationship.get("from_qname")
        child = relationship.get("to_qname")
        if not role_uri or not parent or not child or parent == child:
            continue
        graph[role_uri][parent].add(child)
    return graph


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
        allowed_members_by_topic.setdefault(topic_id, {})


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


def _topic_anchor_concepts(topic_rules: dict) -> set[str]:
    anchors = set(_trigger_concepts(topic_rules))
    for concept in _topic_primary_items(topic_rules):
        if concept and "PrimaryItems" not in concept:
            anchors.add(concept)
    return anchors


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


def _topic_note_presence_facts(report: ReportModel, topic_id: str, topic_rules: dict) -> list[ReportFact]:
    reason_map = _topic_fact_reason_map(report, topic_id, topic_rules)
    selected_ids = {
        fact_id
        for fact_id, reasons in reason_map.items()
        if any(reason in NOTE_PRESENCE_TOPIC_REASONS for reason in reasons)
    }
    return [fact for fact in report.facts if fact.fact_id in selected_ids]


def _expected_dimension_usage_facts(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    role_graph: dict[str, dict[str, set[str]]],
) -> list[ReportFact]:
    concept_pool = _topic_role_concept_pool(topic_rules, role_graph)
    selected: list[ReportFact] = []
    for fact in report.facts:
        context = _fact_context(report, fact)
        if context is None or not context.dimensions:
            continue
        if fact.attributes.get("data-topic-id") == topic_id:
            selected.append(fact)
            continue
        if fact.concept_qname and fact.concept_qname in concept_pool:
            selected.append(fact)
    return selected


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


def _has_concept_arithmetic_topic_evidence(
    report: ReportModel,
    topic_rules: dict,
    concept_metadata: dict[str, dict[str, Any]],
    role_graph: dict[str, dict[str, set[str]]],
) -> bool:
    if not topic_rules["families"].get("5 Concept arithmetic relationships"):
        return False
    topic_pool = _topic_role_concept_pool(topic_rules, role_graph)
    if not topic_pool:
        return False
    for fact in report.facts:
        if not fact.concept_qname or fact.numeric_value() is None or fact.concept_qname not in topic_pool:
            continue
        if _candidate_component_concepts_for_head(fact.concept_qname, role_graph, concept_metadata):
            return True
        if _semantic_component_candidates_for_head(fact.concept_qname, concept_metadata):
            return True
    return False


def _topic_is_relevant(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    concept_metadata: dict[str, dict[str, Any]],
    role_graph: dict[str, dict[str, set[str]]],
) -> bool:
    if topic_rules.get("topic_metadata", {}).get("always_relevant"):
        return True
    if _has_strong_topic_fact_evidence(report, topic_id, topic_rules):
        return True
    if _topic_trigger_facts(report, topic_rules):
        return True
    if _has_concept_arithmetic_topic_evidence(report, topic_rules, concept_metadata, role_graph):
        return True
    return False


def _mandatory_tag_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    results: list[dict] = []
    for rule in topic_rules["families"].get(MANDATORY_FAMILY_NAME, []):
        concept_qname = rule.get("concept_qname")
        concept_facts = [fact for fact in report.facts if fact.concept_qname == concept_qname]
        if rule["type"] == "mandatory_concept_presence":
            results.append(
                {
                    "rule_id": rule["id"],
                    "type": rule["type"],
                    "status": "pass" if concept_facts else "fail",
                    "topic": topic_id,
                    "message": (
                        "Mandatory concept is present in the filing."
                        if concept_facts
                        else "Mandatory concept is missing from the filing."
                    ),
                    "evidence": {
                        "mandatory_fact_ids": [fact.fact_id for fact in concept_facts],
                        "mandatory_fact_count": len(concept_facts),
                        "concept_qname": concept_qname,
                        "concept_label": rule.get("concept_label"),
                        "required_statement_roles": rule.get("required_statement_roles", []),
                        "taxonomy_basis": rule.get("taxonomy_basis", {}),
                    },
                }
            )
        elif rule["type"] == "mandatory_concept_role_scope":
            required_roles = set(rule.get("required_statement_roles", []))
            taxonomy_roles = set(rule.get("taxonomy_basis", {}).get("statement_roles_for_concept", []))
            passes_scope = required_roles <= taxonomy_roles
            if not concept_facts:
                status = "not_applied"
                message = "Mandatory concept is absent, so statement-role scope was not evaluated."
            elif not taxonomy_roles:
                status = "not_applied"
                message = "Mandatory concept is present but no taxonomy statement-role mapping was available."
            else:
                status = "pass" if passes_scope else "fail"
                message = (
                    "Mandatory concept is present and its taxonomy scope matches the required statement role."
                    if passes_scope
                    else "Mandatory concept is present but its taxonomy scope does not match the required statement role."
                )
            results.append(
                {
                    "rule_id": rule["id"],
                    "type": rule["type"],
                    "status": status,
                    "topic": topic_id,
                    "message": message,
                    "evidence": {
                        "mandatory_fact_ids": [fact.fact_id for fact in concept_facts],
                        "mandatory_fact_count": len(concept_facts),
                        "concept_qname": concept_qname,
                        "concept_label": rule.get("concept_label"),
                        "required_statement_roles": sorted(required_roles),
                        "taxonomy_statement_roles": sorted(taxonomy_roles),
                        "taxonomy_basis": rule.get("taxonomy_basis", {}),
                    },
                }
            )
        elif rule["type"] == "mandatory_concept_dimensional_conformity":
            allowed_dimension_sets = [
                set(item)
                for item in rule.get("taxonomy_basis", {}).get("allowed_dimension_sets", [])
                if item
            ]
            dimensional_facts: list[ReportFact] = []
            invalid_fact_ids: list[str] = []
            valid_fact_ids: list[str] = []
            for fact in concept_facts:
                context = _fact_context(report, fact)
                if context is None or not context.dimensions:
                    continue
                dimensional_facts.append(fact)
                fact_dimensions = set(context.dimensions)
                if allowed_dimension_sets and any(fact_dimensions <= allowed for allowed in allowed_dimension_sets):
                    valid_fact_ids.append(fact.fact_id)
                else:
                    invalid_fact_ids.append(fact.fact_id)
            if dimensional_facts and not allowed_dimension_sets:
                message = "Mandatory concept was used dimensionally but no taxonomy-backed cube pattern was available to confirm it."
                status = "fail"
            elif invalid_fact_ids:
                message = "Observed dimensional mandatory facts do not fit any allowed cube pattern."
                status = "fail"
            elif dimensional_facts:
                message = "Observed dimensional mandatory facts fit an allowed cube pattern."
                status = "pass"
            else:
                message = "Mandatory concept was not used dimensionally in this filing."
                status = "not_applied"
            results.append(
                {
                    "rule_id": rule["id"],
                    "type": rule["type"],
                    "status": status,
                    "topic": topic_id,
                    "message": message,
                    "evidence": {
                        "mandatory_fact_ids": [fact.fact_id for fact in concept_facts],
                        "dimensional_fact_ids": [fact.fact_id for fact in dimensional_facts],
                        "invalid_fact_ids": invalid_fact_ids,
                        "valid_fact_ids": valid_fact_ids,
                        "checked_fact_count": len(dimensional_facts),
                        "concept_qname": concept_qname,
                        "concept_label": rule.get("concept_label"),
                        "required_statement_roles": rule.get("required_statement_roles", []),
                        "taxonomy_basis": rule.get("taxonomy_basis", {}),
                    },
                }
            )
    return results


def _topic_note_presence_results(report: ReportModel, topic_id: str, topic_rules: dict) -> list[dict]:
    topic_facts = _topic_note_presence_facts(report, topic_id, topic_rules)
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
                    "topic_fact_reasons": {
                        fact.fact_id: reason_map.get(fact.fact_id, [])
                        for fact in topic_facts
                        if fact.fact_id in reason_map
                    },
                    "trigger_fact_count": len(trigger_facts),
                    "topic_fact_count": len(topic_facts),
                },
            }
        )
    return results


def _expected_dimension_usage_results(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    role_graph: dict[str, dict[str, set[str]]],
) -> list[dict]:
    topic_facts = _expected_dimension_usage_facts(report, topic_id, topic_rules, role_graph)
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


def _context_signature(context: ReportContext) -> tuple:
    return (
        context.entity,
        context.period_type,
        context.instant,
        context.start_date,
        context.end_date,
        tuple(sorted(context.dimensions.items())),
    )


def _same_other_dimensions_signature(context: ReportContext, *, excluding_dimension: str) -> tuple:
    return (
        context.entity,
        context.period_type,
        context.instant,
        context.start_date,
        context.end_date,
        tuple(sorted((dimension, member) for dimension, member in context.dimensions.items() if dimension != excluding_dimension)),
    )


def _head_mode_matches(context: ReportContext, dimension: str, head_member: str, head_modes: set[str]) -> bool:
    if dimension not in context.dimensions:
        return "dimension_omitted" in head_modes
    return "default_member" in head_modes and context.dimensions.get(dimension) == head_member


def _cross_dimension_same_concept_facts(
    report: ReportModel,
    *,
    concept_qname: str | None,
    unit: str | None,
    base_context: ReportContext,
    excluded_dimension: str,
) -> list[tuple[ReportFact, ReportContext]]:
    if not concept_qname:
        return []
    matches: list[tuple[ReportFact, ReportContext]] = []
    base_signature = _same_other_dimensions_signature(base_context, excluding_dimension=excluded_dimension)
    for fact in report.facts:
        if fact.concept_qname != concept_qname or fact.unit != unit:
            continue
        context = _fact_context(report, fact)
        if context is None:
            continue
        if _same_other_dimensions_signature(context, excluding_dimension=excluded_dimension) == base_signature:
            continue
        if (
            context.entity == base_context.entity
            and context.period_type == base_context.period_type
            and context.instant == base_context.instant
            and context.start_date == base_context.start_date
            and context.end_date == base_context.end_date
        ):
            matches.append((fact, context))
    return matches


def _scope_signature(
    context: ReportContext,
    *,
    excluded_dimensions: set[str],
) -> tuple:
    return (
        context.entity,
        context.period_type,
        context.instant,
        context.start_date,
        context.end_date,
        tuple(sorted((dimension, member) for dimension, member in context.dimensions.items() if dimension not in excluded_dimensions)),
    )


def _parse_iso_date(value: str) -> tuple[int, int, int] | None:
    if not value:
        return None
    try:
        year, month, day = value.split("-")
        return int(year), int(month), int(day)
    except (ValueError, AttributeError):
        return None


def _parse_iso_date_obj(value: str) -> date | None:
    parsed = _parse_iso_date(value)
    if parsed is None:
        return None
    return date(*parsed)


def _movement_bucket_signature(context: ReportContext) -> tuple:
    return (
        context.entity,
        tuple(sorted(context.dimensions.items())),
    )


def _signed_numeric_value(fact: ReportFact, concept_balances: dict[str, str | None]) -> float | None:
    value = fact.numeric_value()
    if value is None:
        return None
    balance = concept_balances.get(fact.concept_qname or "")
    if balance == "credit":
        return round(-value, 6)
    return round(value, 6)


def _duration_matches_instant_bridge(
    start_instant: str,
    end_instant: str,
    duration_start: str,
    duration_end: str,
) -> bool:
    start_date = _parse_iso_date_obj(start_instant)
    end_date = _parse_iso_date_obj(end_instant)
    duration_start_date = _parse_iso_date_obj(duration_start)
    duration_end_date = _parse_iso_date_obj(duration_end)
    if not start_date or not end_date or not duration_start_date or not duration_end_date:
        return False
    if duration_end_date != end_date:
        return False
    return duration_start_date == start_date or duration_start_date == start_date + timedelta(days=1)


def _context_signature_excluding(
    context: ReportContext,
    excluded_dimensions: set[str],
) -> tuple:
    return (
        context.entity,
        context.period_type,
        context.instant,
        context.start_date,
        context.end_date,
        tuple(sorted((dimension, member) for dimension, member in context.dimensions.items() if dimension not in excluded_dimensions)),
    )


def _choose_signed_subset(
    candidates: list[dict[str, Any]],
    target: float,
    *,
    max_candidates: int,
) -> tuple[list[dict[str, Any]], bool]:
    if not candidates:
        return [], abs(target) <= 0.0001
    ordered = sorted(
        candidates,
        key=lambda item: (
            item["fact"].numeric_value() == 0 if item["fact"].numeric_value() is not None else True,
            abs(item["signed_value"]),
        ),
    )
    limited = ordered[:max_candidates]
    best_match: list[dict[str, Any]] = []
    for size in range(1, len(limited) + 1):
        for subset in combinations(limited, size):
            total = round(sum(item["signed_value"] for item in subset), 6)
            if abs(round(total - target, 6)) <= 0.0001:
                return list(subset), True
            if not best_match or abs(round(total - target, 6)) < abs(round(sum(item["signed_value"] for item in best_match) - target, 6)):
                best_match = list(subset)
    return best_match, False


def _descendant_map_for_role(role_graph: dict[str, dict[str, set[str]]], role_uri: str, root: str) -> dict[str, set[str]]:
    graph = role_graph.get(role_uri, {})
    descendants: dict[str, set[str]] = {}

    def visit(node: str) -> set[str]:
        children = graph.get(node, set())
        all_descendants: set[str] = set()
        for child in children:
            all_descendants.add(child)
            all_descendants.update(visit(child))
        descendants[node] = all_descendants
        return all_descendants

    visit(root)
    return descendants


def _candidate_component_concepts_for_head(
    head_concept: str,
    role_graph: dict[str, dict[str, set[str]]],
    concept_metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, tuple[str, ...]]] = set()
    for role_uri, graph in role_graph.items():
        if head_concept not in graph:
            continue
        descendants = _descendant_map_for_role(role_graph, role_uri, head_concept)
        candidate_concepts = sorted(
            concept
            for concept in descendants.get(head_concept, set())
            if not concept_metadata.get(concept, {}).get("abstract")
        )
        if not candidate_concepts:
            continue
        key = (role_uri, tuple(candidate_concepts))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        candidates.append(
            {
                "role_uri": role_uri,
                "component_concepts": candidate_concepts,
                "descendant_map": {concept: sorted(descendants.get(concept, set())) for concept in candidate_concepts},
            }
        )
    return candidates


def _semantic_component_candidates_for_head(
    head_concept: str,
    concept_metadata: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    explicit_candidates = {
        "core:Non-currentAssets": [
            {
                "role_uri": "semantic_noncurrent_assets_full_ifrs",
                "components": [
                    {"concept_qname": "core:PropertyPlantEquipmentIncludingRight-of-useAssets", "expected_sign": 1},
                    {"concept_qname": "core:IntangibleAssetsIncludingRight-of-useAssets", "expected_sign": 1},
                    {"concept_qname": "core:TradeOtherReceivables", "expected_sign": 1},
                ],
            }
        ],
        "core:NetAssetsLiabilities": [
            {
                "role_uri": "semantic_net_assets_liabilities_bridge",
                "components": [
                    {"concept_qname": "core:TotalAssets", "expected_sign": 1},
                    {"concept_qname": "core:CurrentLiabilities", "expected_sign": -1},
                    {"concept_qname": "core:Non-currentLiabilities", "expected_sign": -1},
                ],
            }
        ],
    }
    if head_concept in explicit_candidates:
        candidates: list[dict[str, Any]] = []
        for candidate in explicit_candidates[head_concept]:
            present = [
                component
                for component in candidate["components"]
                if concept_metadata.get(component["concept_qname"], {}).get("is_numeric")
                and not concept_metadata.get(component["concept_qname"], {}).get("abstract")
            ]
            if len(present) >= 2:
                candidates.append(
                    {
                        "role_uri": candidate["role_uri"],
                        "component_concepts": [component["concept_qname"] for component in present],
                        "component_signs": {
                            component["concept_qname"]: component["expected_sign"] for component in present
                        },
                        "descendant_map": {component["concept_qname"]: [] for component in present},
                    }
                )
        if candidates:
            return candidates

    local_name = head_concept.split(":", 1)[-1]
    prefix = head_concept.split(":", 1)[0]
    non_current_prefixes = [f"{prefix}:Non-current{local_name}", f"{prefix}:NonCurrent{local_name}"]
    current_name = f"{prefix}:Current{local_name}"
    candidates = [current_name, *non_current_prefixes]
    present = [
        concept
        for concept in candidates
        if concept_metadata.get(concept, {}).get("is_numeric") and not concept_metadata.get(concept, {}).get("abstract")
    ]
    if len(present) >= 2:
        return [
            {
                "role_uri": "semantic_current_noncurrent_pair",
                "component_concepts": sorted(present),
                "descendant_map": {concept: [] for concept in present},
            }
        ]
    return []


def _component_concept_aliases(component_concept: str) -> set[str]:
    return {component_concept, *CONCEPT_ARITHMETIC_COMPONENT_ALIASES.get(component_concept, set())}


def _local_name_tokens(qname: str | None) -> set[str]:
    if not qname:
        return set()
    local = qname.split(":", 1)[-1]
    words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", local)
    return {word.lower() for word in words if len(word) >= 4}


def _further_item_component_total_qname(head_concept: str) -> str:
    prefix, local = head_concept.split(":", 1)
    return f"{prefix}:FurtherItem{local}ComponentTotal{local}"


def _further_item_component_candidates_for_head(
    head_concept: str,
    concept_metadata: dict[str, dict[str, Any]],
    observed_same_context_concepts: set[str],
    topic_concept_pool: set[str],
    head_balance: str | None,
) -> list[dict[str, Any]]:
    further_item_qname = _further_item_component_total_qname(head_concept)
    if further_item_qname not in concept_metadata and further_item_qname not in observed_same_context_concepts:
        return []

    head_tokens = _local_name_tokens(head_concept)
    component_concepts: set[str] = set()
    for concept in observed_same_context_concepts:
        if concept == head_concept:
            continue
        concept_balance = concept_metadata.get(concept, {}).get("balance")
        if concept in topic_concept_pool:
            if head_balance and concept_balance and concept_balance != head_balance:
                continue
            component_concepts.add(concept)
            continue
        if (_local_name_tokens(concept) & head_tokens) and (not head_balance or not concept_balance or concept_balance == head_balance):
            component_concepts.add(concept)
    if further_item_qname in observed_same_context_concepts:
        component_concepts.add(further_item_qname)
    if len(component_concepts) < 2:
        return []
    return [
        {
            "role_uri": "semantic_further_item_component_total",
            "component_concepts": sorted(component_concepts),
            "descendant_map": {concept: [] for concept in component_concepts},
        }
    ]


def _head_has_component_total_motif(
    head_concept: str,
    concept_metadata: dict[str, dict[str, Any]],
    report_facts_by_concept: dict[str, list[tuple[ReportFact, ReportContext]]],
) -> bool:
    further_item_qname = _further_item_component_total_qname(head_concept)
    return further_item_qname in concept_metadata or further_item_qname in report_facts_by_concept


def _concept_semantically_overlaps_topic_pool(
    concept_qname: str,
    topic_concept_pool: set[str],
) -> bool:
    concept_tokens = _local_name_tokens(concept_qname)
    if not concept_tokens:
        return False
    return any(_local_name_tokens(topic_concept) & concept_tokens for topic_concept in topic_concept_pool)


def _topic_role_concept_pool(
    topic_rules: dict,
    role_graph: dict[str, dict[str, set[str]]],
) -> set[str]:
    pool: set[str] = set()
    for anchor in _topic_anchor_concepts(topic_rules):
        pool.add(anchor)
        for role_uri, graph in role_graph.items():
            if anchor not in graph:
                continue
            descendants = _descendant_map_for_role(role_graph, role_uri, anchor)
            pool.update(descendants.get(anchor, set()))
    if pool:
        return pool

    topic_tokens = _local_name_tokens(topic_rules.get("topic_label"))
    topic_metadata = topic_rules.get("topic_metadata", {})
    for cube_qname in topic_metadata.get("source_hypercubes", []):
        topic_tokens.update(_local_name_tokens(cube_qname))
    if not topic_tokens:
        return pool

    for role_uri, graph in role_graph.items():
        role_tokens = {token.lower() for token in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", role_uri) if len(token) >= 4}
        if not (role_tokens & topic_tokens):
            continue
        pool.update(graph.keys())
        for children in graph.values():
            pool.update(children)
    return pool


def _dedupe_concept_arithmetic_comparisons(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for comparison in comparisons:
        component_facts = comparison.get("component_facts", [])
        component_fact_ids = tuple(
            sorted(
                fact_id
                for component in component_facts
                for fact_id in component.get("fact_ids", [])
            )
        )
        key = (
            comparison.get("concept_qname"),
            comparison.get("head_value"),
            comparison.get("component_total"),
            comparison.get("role_uri"),
            component_fact_ids,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(comparison)
    return deduped


def _prune_observed_component_concepts(
    observed_components: list[dict[str, Any]],
    descendant_map: dict[str, set[str]],
) -> list[dict[str, Any]]:
    by_concept = {item["concept_qname"]: item for item in observed_components}
    removed: set[str] = set()

    concepts = list(by_concept)
    for concept in concepts:
        for other in concepts:
            if concept == other or concept in removed or other in removed:
                continue
            if concept in descendant_map.get(other, set()):
                removed.add(concept)

    fact_id_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for item in observed_components:
        if item["concept_qname"] in removed:
            continue
        fact_id_groups[tuple(sorted(item.get("fact_ids", [])))].append(item)

    for duplicates in fact_id_groups.values():
        if len(duplicates) < 2:
            continue
        winner = min(duplicates, key=lambda item: (len(item["concept_qname"]), item["concept_qname"]))
        for item in duplicates:
            if item is not winner:
                removed.add(item["concept_qname"])

    return [item for item in observed_components if item["concept_qname"] not in removed]


def _dedupe_equivalent_fact_matches(
    matching_facts: list[tuple[ReportFact, ReportContext]],
    allowed_variance_dimensions: set[str],
) -> list[tuple[ReportFact, ReportContext]]:
    deduped: list[tuple[ReportFact, ReportContext]] = []
    seen: set[tuple[Any, ...]] = set()
    for fact, context in matching_facts:
        key = (
            fact.concept_qname,
            fact.unit,
            round(fact.numeric_value() or 0.0, 6),
            _context_signature_excluding(context, allowed_variance_dimensions),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append((fact, context))
    return deduped


def _choose_signed_assignment(
    components: list[dict[str, Any]],
    head_value: float,
    head_balance: str | None,
) -> tuple[list[dict[str, Any]], bool]:
    if not components:
        return [], False
    best_exact: list[dict[str, Any]] | None = None
    best_exact_penalty: int | None = None
    best_near: list[dict[str, Any]] | None = None
    best_near_distance: float | None = None
    component_count = len(components)
    for mask in range(1 << component_count):
        assigned: list[dict[str, Any]] = []
        total = 0.0
        penalty = 0
        for index, component in enumerate(components):
            sign = -1 if (mask & (1 << index)) else 1
            contribution = round(sign * component["value"], 6)
            expected_sign = component.get("expected_sign")
            if expected_sign is None:
                expected_sign = 1
                component_balance = component.get("balance")
                if head_balance and component_balance and component_balance != head_balance:
                    expected_sign = -1
            if sign != expected_sign:
                penalty += 1
            assigned.append({**component, "contribution_sign": sign, "contribution_value": contribution})
            total += contribution
        distance = abs(round(head_value - total, 6))
        if distance <= 0.0001:
            if best_exact is None or penalty < (best_exact_penalty or 10**9):
                best_exact = assigned
                best_exact_penalty = penalty
        elif best_near is None or distance < (best_near_distance or 10**9):
            best_near = assigned
            best_near_distance = distance
    if best_exact is not None:
        return best_exact, True
    return best_near or [], False


def _matching_scope_exclusion_facts(
    report: ReportModel,
    *,
    concept_qname: str | None,
    unit: str | None,
    base_context: ReportContext,
    aggregation_dimension: str,
    scope_dimension: str,
    excluded_members: set[str],
) -> list[tuple[ReportFact, ReportContext]]:
    if not concept_qname or not excluded_members:
        return []
    matches: list[tuple[ReportFact, ReportContext]] = []
    base_signature = _scope_signature(base_context, excluded_dimensions={aggregation_dimension, scope_dimension})
    for fact in report.facts:
        if fact.concept_qname != concept_qname or fact.unit != unit:
            continue
        context = _fact_context(report, fact)
        if context is None:
            continue
        if _scope_signature(context, excluded_dimensions={aggregation_dimension, scope_dimension}) != base_signature:
            continue
        if context.dimensions.get(scope_dimension) in excluded_members:
            matches.append((fact, context))
    return matches


def _dimensional_aggregation_results(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    concept_balances: dict[str, str | None],
) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    results: list[dict] = []
    for rule in topic_rules["families"].get("6 Dimensional aggregation relationships", []):
        grouped: dict[tuple, dict[str, list[tuple[ReportFact, ReportContext, str | None]]]] = defaultdict(lambda: defaultdict(list))
        dimension = rule["dimension"]
        head_member = rule["head_member"]
        component_members = list(rule["component_members"]["members"])
        component_member_set = set(component_members)
        member_descendants = {
            key: set(value)
            for key, value in rule["component_members"].get("member_descendants", {}).items()
        }
        head_modes = set(rule.get("head_modes", []))
        aggregation_type = rule.get("aggregation_type", "plain")
        scope_dimensions = list(rule.get("scope_dimensions", []))
        strong_entries: list[tuple[ReportFact, ReportContext, str | None]] = []
        eligible_concepts: set[str] = set()

        for fact in topic_facts:
            context = _fact_context(report, fact)
            if context is None or fact.numeric_value() is None:
                continue
            member = context.dimensions.get(dimension)
            if not _head_mode_matches(context, dimension, head_member, head_modes) and member not in component_member_set:
                continue
            strong_entries.append((fact, context, member))
            if fact.concept_qname and member in component_member_set:
                eligible_concepts.add(fact.concept_qname)

        for fact in report.facts:
            context = _fact_context(report, fact)
            if context is None or fact.numeric_value() is None or fact.concept_qname not in eligible_concepts:
                continue
            member = context.dimensions.get(dimension)
            if not _head_mode_matches(context, dimension, head_member, head_modes) and member not in component_member_set:
                continue
            signature = (
                fact.concept_qname,
                fact.unit,
                _same_other_dimensions_signature(context, excluding_dimension=dimension),
            )
            grouped[signature]["entries"].append((fact, context, member))

        comparisons: list[dict] = []
        skipped_comparisons: list[dict] = []
        for signature, payload in grouped.items():
            entries = payload["entries"]
            if not any(member in component_member_set for _, _, member in entries):
                continue
            head_facts = [
                (fact, context, member)
                for fact, context, member in entries
                if _head_mode_matches(context, dimension, head_member, head_modes)
            ]
            component_entries = [
                (fact, context, member)
                for fact, context, member in entries
                if member in component_member_set
            ]
            if not head_facts:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "missing_head_fact",
                        "concept_qname": signature[0],
                    }
                )
                continue
            if len(head_facts) > 1:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "duplicate_head_facts",
                        "concept_qname": signature[0],
                        "head_fact_ids": [fact.fact_id for fact, _, _ in head_facts],
                    }
                )
                continue

            head_fact, head_context, observed_head_member = head_facts[0]
            component_by_member: dict[str, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)
            for fact, context, member in component_entries:
                if member:
                    component_by_member[member].append((fact, context))

            duplicate_members = sorted(member for member, facts in component_by_member.items() if len(facts) > 1)
            if duplicate_members:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "duplicate_component_facts",
                        "head_fact_id": head_fact.fact_id,
                        "duplicate_component_members": duplicate_members,
                    }
                )
                continue

            observed_component_members = sorted(component_by_member)
            overlapping_component_pairs: list[tuple[str, str]] = []
            for member in observed_component_members:
                descendants = member_descendants.get(member, set())
                for other in observed_component_members:
                    if member == other:
                        continue
                    if other in descendants:
                        overlapping_component_pairs.append((member, other))
            if overlapping_component_pairs:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "overlapping_component_facts",
                        "head_fact_id": head_fact.fact_id,
                        "overlapping_component_pairs": overlapping_component_pairs,
                    }
                )
                continue
            missing_component_members = sorted(member for member in component_members if member not in component_by_member)
            if len(observed_component_members) < 2:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "insufficient_component_facts",
                        "head_fact_id": head_fact.fact_id,
                        "observed_component_members": observed_component_members,
                        "missing_component_members": missing_component_members,
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
                    }
                )
                continue

            component_facts: list[dict[str, Any]] = []
            component_total = 0.0
            non_numeric_component_members: list[str] = []
            for member in observed_component_members:
                fact, _ = component_by_member[member][0]
                numeric_value = fact.numeric_value()
                if numeric_value is None:
                    non_numeric_component_members.append(member)
                    continue
                component_total += numeric_value
                component_facts.append(
                    {
                        "fact_id": fact.fact_id,
                        "member": member,
                        "value": numeric_value,
                        "balance": concept_balances.get(fact.concept_qname or ""),
                    }
                )
            if non_numeric_component_members:
                skipped_comparisons.append(
                    {
                        "signature": list(signature),
                        "reason": "non_numeric_component_facts",
                        "head_fact_id": head_fact.fact_id,
                        "non_numeric_component_members": non_numeric_component_members,
                    }
                )
                continue
            if len(component_facts) < 2:
                continue

            effective_head_value = head_value
            scope_adjustments: list[dict[str, Any]] = []
            for scope in scope_dimensions:
                if scope.get("policy") != "exclude_members_from_head_total":
                    continue
                scope_dimension = scope.get("dimension")
                excluded_members = set(scope.get("excluded_members", []))
                if not scope_dimension or not excluded_members:
                    continue
                excluded_facts = _matching_scope_exclusion_facts(
                    report,
                    concept_qname=head_fact.concept_qname,
                    unit=head_fact.unit,
                    base_context=head_context,
                    aggregation_dimension=dimension,
                    scope_dimension=scope_dimension,
                    excluded_members=excluded_members,
                )
                excluded_fact_details: list[dict[str, Any]] = []
                excluded_total = 0.0
                for fact, context in excluded_facts:
                    numeric_value = fact.numeric_value()
                    if numeric_value is None:
                        continue
                    excluded_total += numeric_value
                    excluded_fact_details.append(
                        {
                            "fact_id": fact.fact_id,
                            "value": numeric_value,
                            "scope_member": context.dimensions.get(scope_dimension),
                            "dimensions": dict(sorted(context.dimensions.items())),
                        }
                    )
                if excluded_fact_details:
                    effective_head_value = round(effective_head_value - excluded_total, 6)
                    scope_adjustments.append(
                        {
                            "dimension": scope_dimension,
                            "policy": scope.get("policy"),
                            "excluded_members": sorted(excluded_members),
                            "excluded_total": excluded_total,
                            "excluded_facts": excluded_fact_details,
                        }
                    )

            difference = round(effective_head_value - component_total, 6)
            sign_error_candidates: list[dict[str, Any]] = []
            matches = abs(difference) <= 0.0001
            cross_dimension_same_concept_details: list[dict[str, Any]] = []
            cross_dimension_explained = False
            if not matches:
                for component in component_facts:
                    flipped_total = component_total - (2 * component["value"])
                    if abs(round(head_value - flipped_total, 6)) <= 0.0001:
                        sign_error_candidates.append(
                            {
                                "kind": "component_sign_inversion",
                                "fact_id": component["fact_id"],
                                "member": component["member"],
                                "raw_value": component["value"],
                                "difference_explained": round(2 * component["value"], 6),
                            }
                        )
                if abs(round((-head_value) - component_total, 6)) <= 0.0001:
                    sign_error_candidates.append(
                        {
                            "kind": "head_sign_inversion",
                            "fact_id": head_fact.fact_id,
                            "member": observed_head_member or head_member,
                            "raw_value": head_value,
                            "difference_explained": round(2 * head_value, 6),
                        }
                    )
                cross_dimension_candidates = _cross_dimension_same_concept_facts(
                    report,
                    concept_qname=head_fact.concept_qname,
                    unit=head_fact.unit,
                    base_context=head_context,
                    excluded_dimension=dimension,
                )
                for fact, context in cross_dimension_candidates:
                    numeric_value = fact.numeric_value()
                    if numeric_value is None:
                        continue
                    cross_dimension_same_concept_details.append(
                        {
                            "fact_id": fact.fact_id,
                            "value": numeric_value,
                            "dimensions": dict(sorted(context.dimensions.items())),
                        }
                    )
                    if abs(round(difference - numeric_value, 6)) <= 0.0001 or abs(round(difference + numeric_value, 6)) <= 0.0001:
                        cross_dimension_explained = True

            comparisons.append(
                {
                    "head_fact_id": head_fact.fact_id,
                    "head_member": observed_head_member or head_member,
                    "head_value": head_value,
                    "effective_head_value": effective_head_value,
                    "head_balance": concept_balances.get(head_fact.concept_qname or ""),
                    "dimension": dimension,
                    "aggregation_type": aggregation_type,
                    "concept_qname": signature[0],
                    "component_total": component_total,
                    "observed_component_members": observed_component_members,
                    "missing_component_members": missing_component_members,
                    "difference": difference,
                    "component_facts": component_facts,
                    "scope_adjustments": scope_adjustments,
                    "matches": matches,
                    "match_status": (
                        "matches"
                        if matches
                        else "scope_ambiguous"
                        if cross_dimension_explained
                        else "likely_sign_error"
                        if sign_error_candidates
                        else "mismatch"
                    ),
                    "sign_error_candidates": sign_error_candidates,
                    "cross_dimension_same_concept_facts": cross_dimension_same_concept_details,
                    "cross_dimension_explained": cross_dimension_explained,
                }
            )

        mismatches = [
            comparison
            for comparison in comparisons
            if not comparison["matches"] and not comparison.get("cross_dimension_explained")
        ]
        scope_ambiguous = [comparison for comparison in comparisons if comparison.get("cross_dimension_explained")]
        likely_sign_errors = [comparison for comparison in mismatches if comparison["sign_error_candidates"]]
        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if not mismatches else "fail",
                "topic": topic_id,
                "message": (
                    "No comparable dimensional aggregation sets were available for this candidate."
                    if not comparisons
                    else "Observed dimensional aggregation candidates reconcile where head and component facts are present."
                    if not mismatches
                    else "Observed dimensional aggregation candidates are influenced by same-concept facts in other dimensional scopes, so this candidate was not treated as a direct failure."
                    if scope_ambiguous and not likely_sign_errors and len(scope_ambiguous) == len(comparisons)
                    else "Observed dimensional aggregation candidates do not reconcile; at least one mismatch may be explained by sign inversion."
                    if likely_sign_errors
                    else "Observed dimensional aggregation candidates do not reconcile."
                ),
                "evidence": {
                    "aggregation_type": aggregation_type,
                    "dimension": dimension,
                    "head_member": head_member,
                    "component_members": component_members,
                    "scope_dimensions": scope_dimensions,
                    "comparisons": comparisons,
                    "mismatches": mismatches,
                    "scope_ambiguous": scope_ambiguous,
                    "skipped_comparisons": skipped_comparisons,
                    "likely_sign_error_count": len(likely_sign_errors),
                },
            }
        )
    return results


def _movement_reconciliation_results(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    concept_balances: dict[str, str | None],
) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    results: list[dict] = []
    instant_entries: dict[tuple, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)
    duration_entries: dict[tuple, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)

    for fact in topic_facts:
        context = _fact_context(report, fact)
        if context is None or fact.numeric_value() is None or not fact.concept_qname:
            continue
        bucket_signature = _movement_bucket_signature(context)
        if context.period_type == "instant" and context.instant:
            instant_entries[(fact.concept_qname, fact.unit, bucket_signature)].append((fact, context))
        elif context.period_type == "duration" and context.start_date and context.end_date:
            duration_entries[(fact.unit, bucket_signature)].append((fact, context))

    for rule in topic_rules["families"].get("7 Movement reconciliation", []):
        comparisons: list[dict] = []
        skipped_comparisons: list[dict] = []
        max_candidate_facts = rule.get("movement_component_policy", {}).get("max_candidate_facts", 10)
        allow_zero_without_components = rule.get("movement_component_policy", {}).get("allow_zero_movement_without_components", True)

        for (concept_qname, unit, bucket_signature), instants in instant_entries.items():
            ordered_instants = sorted(
                instants,
                key=lambda item: _parse_iso_date(item[1].instant) or (0, 0, 0),
            )
            if len(ordered_instants) < 2:
                continue

            for index in range(1, len(ordered_instants)):
                start_fact, start_context = ordered_instants[index - 1]
                end_fact, end_context = ordered_instants[index]
                if not start_context.instant or not end_context.instant:
                    continue
                candidate_duration_entries = [
                    (fact, context)
                    for fact, context in duration_entries.get((unit, bucket_signature), [])
                    if _duration_matches_instant_bridge(
                        start_context.instant,
                        end_context.instant,
                        context.start_date,
                        context.end_date,
                    )
                ]
                delta = round((end_fact.numeric_value() or 0.0) - (start_fact.numeric_value() or 0.0), 6)

                candidate_movements: list[dict[str, Any]] = []
                for fact, context in candidate_duration_entries:
                    signed_value = _signed_numeric_value(fact, concept_balances)
                    if signed_value is None:
                        continue
                    candidate_movements.append(
                        {
                            "fact": fact,
                            "context": context,
                            "signed_value": signed_value,
                            "raw_value": fact.numeric_value(),
                            "balance": concept_balances.get(fact.concept_qname or ""),
                        }
                    )

                chosen_movements, exact_match = _choose_signed_subset(
                    candidate_movements,
                    delta,
                    max_candidates=max_candidate_facts,
                )

                if not candidate_movements and not (allow_zero_without_components and abs(delta) <= 0.0001):
                    skipped_comparisons.append(
                        {
                            "reason": "missing_movement_facts",
                            "concept_qname": concept_qname,
                            "start_fact_id": start_fact.fact_id,
                            "end_fact_id": end_fact.fact_id,
                        }
                    )
                    continue

                if candidate_movements and not exact_match:
                    skipped_comparisons.append(
                        {
                            "reason": "movement_subset_not_found",
                            "concept_qname": concept_qname,
                            "start_fact_id": start_fact.fact_id,
                            "end_fact_id": end_fact.fact_id,
                            "candidate_fact_ids": [item["fact"].fact_id for item in candidate_movements],
                        }
                    )
                    continue

                component_total = round(sum(item["signed_value"] for item in chosen_movements), 6)
                difference = round(delta - component_total, 6)
                matches = exact_match or (allow_zero_without_components and not chosen_movements and abs(delta) <= 0.0001)

                sign_error_candidates: list[dict[str, Any]] = []
                if not matches:
                    for component in chosen_movements:
                        raw_value = component["raw_value"]
                        if raw_value is None:
                            continue
                        explained = round(2 * abs(raw_value), 6)
                        if abs(abs(difference) - explained) <= 0.0001:
                            sign_error_candidates.append(
                                {
                                    "kind": "movement_component_sign_inversion",
                                    "fact_id": component["fact"].fact_id,
                                    "raw_value": raw_value,
                                    "difference_explained": explained,
                                }
                            )

                comparisons.append(
                    {
                        "concept_qname": concept_qname,
                        "head_fact_id": end_fact.fact_id,
                        "start_fact_id": start_fact.fact_id,
                        "end_fact_id": end_fact.fact_id,
                        "head_balance": concept_balances.get(end_fact.concept_qname or ""),
                        "opening_value": start_fact.numeric_value(),
                        "closing_value": end_fact.numeric_value(),
                        "movement_total": delta,
                        "component_total": component_total,
                        "difference": difference,
                        "matches": matches,
                        "match_status": "matches" if matches else "likely_sign_error" if sign_error_candidates else "mismatch",
                        "movement_facts": [
                            {
                                "fact_id": item["fact"].fact_id,
                                "value": item["raw_value"],
                                "signed_value": item["signed_value"],
                                "balance": item["balance"],
                            }
                            for item in chosen_movements
                        ],
                        "candidate_movement_fact_count": len(candidate_movements),
                        "sign_error_candidates": sign_error_candidates,
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
                    "No comparable movement bridges were available for this candidate."
                    if not comparisons
                    else "Observed movement bridges reconcile between opening, movement facts, and closing values."
                    if not mismatches
                    else "Observed movement bridges do not reconcile; at least one mismatch may be explained by sign inversion."
                    if likely_sign_errors
                    else "Observed movement bridges do not reconcile."
                ),
                "evidence": {
                    "comparisons": comparisons,
                    "mismatches": mismatches,
                    "skipped_comparisons": skipped_comparisons,
                    "likely_sign_error_count": len(likely_sign_errors),
                    "taxonomy_basis": rule.get("taxonomy_basis", {}),
                },
            }
        )
    return results


def _concept_arithmetic_results(
    report: ReportModel,
    topic_id: str,
    topic_rules: dict,
    concept_balances: dict[str, str | None],
    concept_metadata: dict[str, dict[str, Any]],
    role_graph: dict[str, dict[str, set[str]]],
) -> list[dict]:
    topic_facts = _topic_facts(report, topic_id, topic_rules, attribution="strong")
    topic_has_strong_evidence = bool(topic_facts)
    results: list[dict] = []
    topic_concept_pool = _topic_role_concept_pool(topic_rules, role_graph)
    report_facts_by_concept: dict[str, list[tuple[ReportFact, ReportContext]]] = defaultdict(list)
    candidate_head_facts: list[ReportFact] = []
    candidate_head_ids: set[str] = set()
    for fact in report.facts:
        context = _fact_context(report, fact)
        if context is None or fact.numeric_value() is None or not fact.concept_qname:
            continue
        report_facts_by_concept[fact.concept_qname].append((fact, context))
    for fact in topic_facts:
        if fact.fact_id not in candidate_head_ids and fact.numeric_value() is not None and fact.concept_qname:
            candidate_head_facts.append(fact)
            candidate_head_ids.add(fact.fact_id)
    if topic_concept_pool:
        for fact in report.facts:
            if (
                fact.fact_id not in candidate_head_ids
                and fact.concept_qname in topic_concept_pool
                and fact.numeric_value() is not None
            ):
                candidate_head_facts.append(fact)
                candidate_head_ids.add(fact.fact_id)
        if not topic_has_strong_evidence:
            for fact in report.facts:
                if (
                    fact.fact_id in candidate_head_ids
                    or not fact.concept_qname
                    or fact.numeric_value() is None
                    or not _semantic_component_candidates_for_head(fact.concept_qname, concept_metadata)
                    or not _concept_semantically_overlaps_topic_pool(fact.concept_qname, topic_concept_pool)
                ):
                    continue
                candidate_head_facts.append(fact)
                candidate_head_ids.add(fact.fact_id)
        if topic_has_strong_evidence:
            for fact in report.facts:
                if (
                    fact.fact_id in candidate_head_ids
                    or not fact.concept_qname
                    or fact.numeric_value() is None
                    or not _head_has_component_total_motif(fact.concept_qname, concept_metadata, report_facts_by_concept)
                ):
                    continue
                context = _fact_context(report, fact)
                if context is None:
                    continue
                signature = _context_signature_excluding(context, {"common:X-AnalysisDimension"})
                head_tokens = _local_name_tokens(fact.concept_qname)
                supporting_semantic_overlap = False
                if not _concept_semantically_overlaps_topic_pool(fact.concept_qname, topic_concept_pool):
                    continue
                for concept, entries in report_facts_by_concept.items():
                    if concept not in topic_concept_pool:
                        continue
                    if any(_context_signature_excluding(entry_context, {"common:X-AnalysisDimension"}) == signature for _, entry_context in entries):
                        if _local_name_tokens(concept) & head_tokens:
                            supporting_semantic_overlap = True
                            break
                if supporting_semantic_overlap:
                    candidate_head_facts.append(fact)
                    candidate_head_ids.add(fact.fact_id)

    for rule in topic_rules["families"].get("5 Concept arithmetic relationships", []):
        allowed_variance_dimensions = set(rule.get("allowed_component_dimension_variance", []))
        min_components = rule.get("candidate_requirements", {}).get("minimum_component_concepts", 1)
        max_components = rule.get("candidate_requirements", {}).get("maximum_component_concepts", MAX_ARITHMETIC_COMPONENT_CONCEPTS)
        comparisons: list[dict] = []
        mismatches: list[dict] = []
        skipped_comparisons: list[dict] = []

        for head_fact in candidate_head_facts:
            head_context = _fact_context(report, head_fact)
            head_value = head_fact.numeric_value()
            if head_context is None or head_value is None or not head_fact.concept_qname:
                continue

            head_signature = _context_signature_excluding(head_context, allowed_variance_dimensions)
            observed_same_context_concepts = {
                fact.concept_qname
                for fact in report.facts
                if fact.concept_qname
                and fact.numeric_value() is not None
                and fact.unit == head_fact.unit
                and (
                    (context := _fact_context(report, fact)) is not None
                    and _context_signature_excluding(context, allowed_variance_dimensions) == head_signature
                )
            }
            candidate_groups = _candidate_component_concepts_for_head(head_fact.concept_qname, role_graph, concept_metadata)
            candidate_groups.extend(_semantic_component_candidates_for_head(head_fact.concept_qname, concept_metadata))
            if topic_has_strong_evidence:
                candidate_groups.extend(
                    _further_item_component_candidates_for_head(
                        head_fact.concept_qname,
                        concept_metadata,
                        observed_same_context_concepts,
                        topic_concept_pool,
                        concept_balances.get(head_fact.concept_qname),
                    )
                )
            if not candidate_groups:
                continue

            best_exact: dict[str, Any] | None = None
            best_near: dict[str, Any] | None = None

            for group in candidate_groups:
                observed_components: list[dict[str, Any]] = []
                for component_concept in group["component_concepts"]:
                    matching_facts: list[tuple[ReportFact, ReportContext]] = []
                    for candidate_concept in _component_concept_aliases(component_concept):
                        for fact, context in report_facts_by_concept.get(candidate_concept, []):
                            if fact.unit != head_fact.unit:
                                continue
                            if _context_signature_excluding(context, allowed_variance_dimensions) != head_signature:
                                continue
                            matching_facts.append((fact, context))
                    matching_facts = _dedupe_equivalent_fact_matches(matching_facts, allowed_variance_dimensions)
                    if not matching_facts:
                        continue
                    total_value = round(sum(fact.numeric_value() or 0.0 for fact, _ in matching_facts), 6)
                    observed_components.append(
                        {
                            "concept_qname": component_concept,
                            "fact_ids": [fact.fact_id for fact, _ in matching_facts],
                            "value": total_value,
                            "balance": concept_balances.get(component_concept),
                            "expected_sign": group.get("component_signs", {}).get(component_concept),
                        }
                    )

                if len(observed_components) < min_components or len(observed_components) > max_components:
                    continue
                if (
                    topic_concept_pool
                    and not group["role_uri"].startswith("semantic_")
                    and not all(item["concept_qname"] in topic_concept_pool for item in observed_components)
                ):
                    continue

                descendant_map = {key: set(value) for key, value in group.get("descendant_map", {}).items()}
                observed_components = _prune_observed_component_concepts(observed_components, descendant_map)
                if len(observed_components) < min_components or len(observed_components) > max_components:
                    continue
                if not topic_has_strong_evidence and len(observed_components) < 2:
                    continue

                observed_concepts = [item["concept_qname"] for item in observed_components]
                overlapping_pairs: list[tuple[str, str]] = []
                for concept in observed_concepts:
                    descendants = descendant_map.get(concept, set())
                    for other in observed_concepts:
                        if concept != other and other in descendants:
                            overlapping_pairs.append((concept, other))
                if overlapping_pairs:
                    skipped_comparisons.append(
                        {
                            "reason": "overlapping_component_concepts",
                            "head_fact_id": head_fact.fact_id,
                            "role_uri": group["role_uri"],
                            "overlapping_component_pairs": overlapping_pairs,
                        }
                    )
                    continue

                assigned_components, exact_match = _choose_signed_assignment(
                    observed_components,
                    head_value,
                    concept_balances.get(head_fact.concept_qname),
                )
                if not assigned_components:
                    continue
                component_total = round(sum(item["contribution_value"] for item in assigned_components), 6)
                difference = round(head_value - component_total, 6)
                sign_error_candidates: list[dict[str, Any]] = []
                if not exact_match:
                    for component in assigned_components:
                        explained = round(2 * abs(component["value"]), 6)
                        if abs(abs(difference) - explained) <= 0.0001:
                            sign_error_candidates.append(
                                {
                                    "kind": "component_sign_inversion",
                                    "fact_id": component["fact_ids"][0],
                                    "concept_qname": component["concept_qname"],
                                    "raw_value": component["value"],
                                    "difference_explained": explained,
                                }
                            )
                comparison = {
                    "head_fact_id": head_fact.fact_id,
                    "concept_qname": head_fact.concept_qname,
                    "head_value": head_value,
                    "head_balance": concept_balances.get(head_fact.concept_qname),
                    "component_total": component_total,
                    "difference": difference,
                    "matches": exact_match,
                    "match_status": "matches" if exact_match else "likely_sign_error" if sign_error_candidates else "mismatch",
                    "component_facts": [
                        {
                            "fact_id": component["fact_ids"][0],
                            "fact_ids": component["fact_ids"],
                            "concept_qname": component["concept_qname"],
                            "value": component["value"],
                            "balance": component["balance"],
                            "contribution_sign": component["contribution_sign"],
                            "contribution_value": component["contribution_value"],
                        }
                        for component in assigned_components
                    ],
                    "role_uri": group["role_uri"],
                    "sign_error_candidates": sign_error_candidates,
                }
                if exact_match:
                    score = (len(assigned_components), -sum(1 for c in assigned_components if c["contribution_sign"] < 0))
                    if best_exact is None or score > best_exact["score"]:
                        best_exact = {"score": score, "comparison": comparison}
                else:
                    score = (abs(difference), -len(assigned_components))
                    if best_near is None or score < best_near["score"]:
                        best_near = {"score": score, "comparison": comparison}

            if best_exact:
                comparisons.append(best_exact["comparison"])
            elif best_near:
                comparisons.append(best_near["comparison"])
                mismatches.append(best_near["comparison"])

        comparisons = _dedupe_concept_arithmetic_comparisons(comparisons)
        mismatches = _dedupe_concept_arithmetic_comparisons(mismatches)
        likely_sign_errors = [comparison for comparison in mismatches if comparison["sign_error_candidates"]]
        results.append(
            {
                "rule_id": rule["id"],
                "type": rule["type"],
                "status": "pass" if not mismatches else "fail",
                "topic": topic_id,
                "message": (
                    "No comparable concept arithmetic sets were available for this candidate."
                    if not comparisons
                    else "Observed concept arithmetic candidates reconcile where head and component facts are present."
                    if not mismatches
                    else "Observed concept arithmetic candidates do not reconcile; at least one mismatch may be explained by sign inversion."
                    if likely_sign_errors
                    else "Observed concept arithmetic candidates do not reconcile."
                ),
                "evidence": {
                    "comparisons": comparisons,
                    "mismatches": mismatches,
                    "skipped_comparisons": skipped_comparisons,
                    "likely_sign_error_count": len(likely_sign_errors),
                    "taxonomy_basis": rule.get("taxonomy_basis", {}),
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
    roles_payload: dict[str, Any] | None = None,
    include_all_topics: bool = False,
    selected_topics: set[str] | None = None,
    selected_families_by_topic: dict[str, set[str]] | None = None,
) -> dict:
    manifest, rules_by_topic = _load_rule_pack(split_output_dir)
    allowed_members_by_topic = _allowed_members_by_topic(topics_payload)
    concept_balances = _concept_balance_map(concepts_payload)
    concept_metadata = _concept_metadata_map(concepts_payload)
    role_graph = _presentation_role_graph(roles_payload)
    _augment_allowed_members_from_rules(allowed_members_by_topic, rules_by_topic)
    results: list[dict] = []
    evaluated_topics: list[dict] = []
    topic_scope = "all_topics" if include_all_topics else "selected_topics" if selected_topics else "relevant_topics_only"
    for topic_id, topic_rules in rules_by_topic.items():
        if selected_topics is not None and topic_id not in selected_topics:
            continue
        relevant = _topic_is_relevant(report, topic_id, topic_rules, concept_metadata, role_graph)
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
        available_families = set(topic_rules.get("families", {}))
        allowed_families = selected_families_by_topic.get(topic_id) if selected_families_by_topic else None
        if _family_enabled(MANDATORY_FAMILY_NAME, allowed_families, available_families):
            results.extend(_mandatory_tag_results(report, topic_id, topic_rules))
        if _family_enabled("1 Topic note presence", allowed_families, available_families):
            results.extend(_topic_note_presence_results(report, topic_id, topic_rules))
        if _family_enabled("2 Hypercube conformity", allowed_families, available_families):
            results.extend(_hypercube_conformity_results(report, topic_id, topic_rules))
        if _family_enabled("3 Expected dimension usage", allowed_families, available_families):
            results.extend(_expected_dimension_usage_results(report, topic_id, topic_rules, role_graph))
        if _family_enabled("4 Member validity", allowed_families, available_families):
            results.extend(_member_validity_results(report, topic_id, topic_rules, allowed_members_by_topic))
        if _family_enabled("5 Concept arithmetic relationships", allowed_families, available_families):
            results.extend(
                _concept_arithmetic_results(
                    report,
                    topic_id,
                    topic_rules,
                    concept_balances,
                    concept_metadata,
                    role_graph,
                )
            )
        if _family_enabled("6 Dimensional aggregation relationships", allowed_families, available_families):
            results.extend(
                _dimensional_aggregation_results(
                    report,
                    topic_id,
                    topic_rules,
                    concept_balances,
                )
            )
        if _family_enabled("7 Movement reconciliation", allowed_families, available_families):
            results.extend(
                _movement_reconciliation_results(
                    report,
                    topic_id,
                    topic_rules,
                    concept_balances,
                )
            )
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


def _family_enabled(family_name: str, allowed_families: set[str] | None, available_families: set[str]) -> bool:
    if family_name not in available_families:
        return False
    return allowed_families is None or family_name in allowed_families


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate generated rule packs against synthetic or real inline XBRL HTML files."
    )
    parser.add_argument("--input-file", required=True)
    parser.add_argument("--split-output-dir", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument("--concepts", default="backend/validation_rules/generated/2026/frs102/concepts.json")
    parser.add_argument("--roles", default="backend/validation_rules/generated/2026/frs102/roles.json")
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
        roles_payload=_load_json(Path(args.roles)),
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
