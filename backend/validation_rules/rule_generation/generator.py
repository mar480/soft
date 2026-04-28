from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

from .rule_ids import build_rule_id
from .rule_schema import CandidateRule

RULE_FAMILY_LAYOUT = {
    "topic_note_presence": (1, "Topic note presence"),
    "hypercube_conformity": (2, "Hypercube conformity"),
    "expected_dimension_usage": (3, "Expected dimension usage"),
    "dimension_member_validity": (4, "Member validity"),
    "concept_arithmetic_relationship": (5, "Concept arithmetic relationships"),
    "dimensional_aggregation_relationship": (6, "Dimensional aggregation relationships"),
    "movement_reconciliation": (7, "Movement reconciliation"),
    "modelling_suggestion": (8, "Modelling suggestion"),
    "blocked_inference": (9, "Anti-rules"),
}

_AGGREGATION_BLOCKED_DIMENSIONS = {
    "bus:GroupCompanyDataDimension",
    "bus:OriginalRevisedDataDimension",
    "common:X-AnalysisDimension",
    "core:GeographicSegmentsDimension",
    "core:MajorCustomersDimension",
    "core:OperatingSegmentsDimension",
    "core:ProductsServicesDimension",
    "core:RestatementsFirstTimeAdoptionDimension",
    "core:SegmentReconciliationDimension",
}


def _load_topics(topics_path: Path) -> dict:
    return json.loads(topics_path.read_text(encoding="utf-8"))


def _load_pfs_note_review(review_path: Path) -> dict:
    return json.loads(review_path.read_text(encoding="utf-8"))


def _load_concepts(concepts_path: Path) -> list[dict]:
    return json.loads(concepts_path.read_text(encoding="utf-8"))


def _load_roles(roles_path: Path) -> dict:
    return json.loads(roles_path.read_text(encoding="utf-8"))


def _topic_primary_items(topic: dict) -> list[str]:
    qnames: set[str] = set()
    for cube in topic["hypercubes"]:
        for item in cube["primary_items"]:
            qnames.add(item["qname"])
    return sorted(qnames)


def _topic_dimensions(topic: dict) -> list[dict]:
    seen: dict[str, dict] = {}
    for cube in topic["hypercubes"]:
        for dimension in cube["dimensions"]:
            seen.setdefault(dimension["dimension_qname"], dimension)
    return [seen[key] for key in sorted(seen)]


def _is_business_facing_topic(topic: dict) -> bool:
    return topic.get("priority") != "deprioritised" and topic.get("topic_kind") == "disclosure_family"


def _normalise_text(value: str | None) -> str:
    return (value or "").replace("_", " ").replace("-", " ").lower()


def _title_dir_name(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", text).strip()
    return cleaned or "Unnamed"


def _family_dir_name(rule_type: str) -> str:
    number, label = RULE_FAMILY_LAYOUT.get(rule_type, (0, rule_type.replace("_", " ")))
    return f"{number} {label}" if number else label


def _safe_rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _is_dimensional_aggregation_candidate(dimension: dict) -> bool:
    dimension_qname = dimension.get("dimension_qname")
    if not dimension_qname or dimension_qname in _AGGREGATION_BLOCKED_DIMENSIONS:
        return False

    default_member = dimension.get("default_member")
    if not default_member or default_member == "common:NotApplicableDefault":
        return False

    member_tree = dimension.get("member_tree", [])
    if not member_tree:
        return False

    root = member_tree[0]
    children = root.get("children", [])
    if len(children) < 2:
        return False

    lowered = " ".join(
        filter(
            None,
            [
                _normalise_text(dimension_qname),
                _normalise_text(dimension.get("dimension_label")),
                _normalise_text(default_member),
                _normalise_text(root.get("qname")),
                _normalise_text(root.get("label")),
            ],
        )
    )
    required_terms = ("total", "all", "class", "classes", "officer", "officers", "share", "shares", "provisions", "type", "types")
    return any(term in lowered for term in required_terms)


def _iter_member_nodes(nodes: list[dict]) -> list[dict]:
    collected: list[dict] = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        collected.append(node)
        stack.extend(reversed(node.get("children", [])))
    return collected


def _leaf_component_members(dimension: dict) -> list[str]:
    default_member = dimension.get("default_member")
    leaves: list[str] = []
    for root in dimension.get("member_tree", []):
        for node in _iter_member_nodes([root]):
            if node.get("children"):
                continue
            qname = node.get("qname")
            if not qname or qname == default_member:
                continue
            if "deprecated" in _normalise_text(node.get("label")):
                continue
            leaves.append(qname)
    return sorted(set(leaves))


def _descendant_component_members(dimension: dict) -> list[str]:
    default_member = dimension.get("default_member")
    members: list[str] = []
    for root in dimension.get("member_tree", []):
        for node in _iter_member_nodes(root.get("children", [])):
            qname = node.get("qname")
            if not qname or qname == default_member:
                continue
            if "deprecated" in _normalise_text(node.get("label")):
                continue
            members.append(qname)
    return sorted(set(members))


def _component_member_descendants(dimension: dict) -> dict[str, list[str]]:
    default_member = dimension.get("default_member")
    descendants: dict[str, list[str]] = {}

    def collect(node: dict) -> list[str]:
        qname = node.get("qname")
        child_qnames: list[str] = []
        for child in node.get("children", []):
            child_qname = child.get("qname")
            if child_qname and child_qname != default_member:
                child_qnames.append(child_qname)
            child_qnames.extend(collect(child))
        if qname and qname != default_member:
            descendants[qname] = sorted(set(child_qnames))
        return child_qnames

    for root in dimension.get("member_tree", []):
        collect(root)
    return descendants


def _iter_dimension_nodes(dimension: dict) -> list[dict]:
    nodes: list[dict] = []
    for root in dimension.get("member_tree", []):
        nodes.extend(_iter_member_nodes([root]))
    return nodes


def _ppe_scoped_aggregation(topic: dict, dimension: dict) -> dict | None:
    if dimension.get("dimension_qname") != "core:PropertyPlantEquipmentClassesDimension":
        return None
    ownership_dimension = next(
        (item for item in _topic_dimensions(topic) if item.get("dimension_qname") == "core:PPEOwnershipDimension"),
        None,
    )
    if ownership_dimension is None:
        return None

    excluded_members: list[str] = []
    for node in _iter_dimension_nodes(ownership_dimension):
        qname = node.get("qname")
        if not qname:
            continue
        lowered = " ".join(filter(None, [_normalise_text(qname), _normalise_text(node.get("label"))]))
        if "right of use" in lowered:
            excluded_members.append(qname)

    excluded_members = sorted(set(excluded_members))
    if not excluded_members:
        return None

    return {
        "aggregation_type": "scoped",
        "scope_dimensions": [
            {
                "dimension": ownership_dimension["dimension_qname"],
                "policy": "exclude_members_from_head_total",
                "excluded_members": excluded_members,
                "reason": "The quality-report arithmetic for PPE classes is expressed for all ownership classes except right-of-use assets.",
            }
        ],
        "taxonomy_basis": {
            "reason_type": "scoped_dimension_total",
            "reason": "The taxonomy supports aggregation across PPE classes, and the related ownership axis provides an exclusion scope used by the arithmetic model.",
            "scope_dimension": ownership_dimension["dimension_qname"],
            "excluded_scope_members": excluded_members,
        },
    }


def _dimensional_aggregation_rules(topic: dict) -> list[CandidateRule]:
    rules: list[CandidateRule] = []
    for dimension in _topic_dimensions(topic):
        if not _is_dimensional_aggregation_candidate(dimension):
            continue
        component_members = _descendant_component_members(dimension)
        if len(component_members) < 2:
            continue
        root = (dimension.get("member_tree") or [{}])[0]
        member_descendants = _component_member_descendants(dimension)
        scoped_aggregation = _ppe_scoped_aggregation(topic, dimension)
        taxonomy_basis = {
            "dimension_label": dimension.get("dimension_label"),
            "default_member": dimension["default_member"],
            "domain_root": root.get("qname"),
            "reason_type": "default_member_total",
            "reason": "The taxonomy dimension provides a default or total-style member with multiple component members under the domain tree.",
        }
        payload = {
            "aggregation_type": "plain",
            "dimension": dimension["dimension_qname"],
            "head_member": dimension["default_member"],
            "head_modes": ["dimension_omitted", "default_member"],
            "component_members": {
                "from_domain_descendants": True,
                "members": component_members,
                "domain_root": root.get("qname"),
                "member_descendants": member_descendants,
                "intermediate_total_policy": "allow_intermediate_totals",
            },
            "match": {
                "concept": "same",
                "period": "same",
                "unit": "same",
                "all_other_dimensions": "same",
            },
            "missing_policy": "do_not_treat_missing_as_zero",
            "taxonomy_basis": taxonomy_basis,
        }
        if scoped_aggregation:
            payload["aggregation_type"] = scoped_aggregation["aggregation_type"]
            payload["scope_dimensions"] = scoped_aggregation["scope_dimensions"]
            payload["taxonomy_basis"] = {
                **taxonomy_basis,
                **scoped_aggregation["taxonomy_basis"],
                "dimension_label": dimension.get("dimension_label"),
                "default_member": dimension["default_member"],
                "domain_root": root.get("qname"),
            }
        rules.append(
            CandidateRule(
                id=build_rule_id(topic_id=topic["topic_id"], rule_kind="DIMAGG", index=len(rules) + 1),
                type="dimensional_aggregation_relationship",
                topic=topic["topic_id"],
                severity="warning",
                confidence="medium",
                requires_review=True,
                payload=payload,
            )
        )
    return rules


def _concept_arithmetic_rule(topic: dict) -> CandidateRule | None:
    if not topic.get("hypercubes"):
        return None
    return CandidateRule(
        id=build_rule_id(topic_id=topic["topic_id"], rule_kind="ARITH", index=1),
        type="concept_arithmetic_relationship",
        topic=topic["topic_id"],
        severity="warning",
        confidence="medium",
        requires_review=True,
        payload={
            "discovery_mode": "role_aware_presentation_descendants_from_observed_head_facts",
            "candidate_requirements": {
                "minimum_component_concepts": 1,
                "maximum_component_concepts": 8,
                "numeric_only": True,
                "same_period_type": True,
                "same_unit_kind": True,
                "allow_intermediate_totals": True,
            },
            "match": {
                "period": "same",
                "unit": "same",
                "all_dimensions": "same_except_allowed_component_analysis_dimensions",
            },
            "allowed_component_dimension_variance": [
                "common:X-AnalysisDimension",
            ],
            "missing_policy": "do_not_treat_missing_as_zero",
            "taxonomy_basis": {
                "reason_type": "presentation_subtotal",
                "topic_source": "role_aware_presentation_descendants",
                "reason": "The taxonomy presentation structure for the topic provides concrete descendant concepts that can act as subtotal components when observed in the same scoped context.",
            },
        },
    )


def _movement_reconciliation_rule(topic: dict, concept_index: dict[str, dict]) -> CandidateRule | None:
    primary_items = _topic_primary_items(topic)
    instant_primary_items = sorted(
        concept
        for concept in primary_items
        if concept_index.get(concept, {}).get("period_type") == "instant"
        and not concept_index.get(concept, {}).get("abstract")
    )
    duration_primary_items = sorted(
        concept
        for concept in primary_items
        if concept_index.get(concept, {}).get("period_type") == "duration"
        and not concept_index.get(concept, {}).get("abstract")
    )
    if not topic.get("hypercubes"):
        return None
    return CandidateRule(
        id=build_rule_id(topic_id=topic["topic_id"], rule_kind="MOVE", index=1),
        type="movement_reconciliation",
        topic=topic["topic_id"],
        severity="warning",
        confidence="medium",
        requires_review=True,
        payload={
            "match": {
                "entity": "same",
                "unit": "same",
                "dimensions": "same",
                "period_bridge": "instant_pair_with_matching_duration",
            },
            "movement_component_policy": {
                "selection": "signed_subset_match",
                "max_candidate_facts": 10,
                "allow_zero_movement_without_components": True,
            },
            "taxonomy_basis": {
                "reason_type": "movement_bridge",
                "reason": "The topic is modelled as a disclosure family with reusable dimensional contexts, so a same-scope opening/closing bridge with signed duration movements is a viable arithmetic motif to test when both instant and duration facts are observed in the report.",
                "instant_primary_items": instant_primary_items,
                "duration_primary_items": duration_primary_items,
            },
        },
    )


def _cube_conformity_rules(topic: dict) -> list[CandidateRule]:
    rules: list[CandidateRule] = []
    for index, cube in enumerate(topic["hypercubes"], start=1):
        rules.append(
            CandidateRule(
                id=build_rule_id(topic_id=topic["topic_id"], rule_kind="CUBE_CONFORMITY", index=index),
                type="hypercube_conformity",
                topic=topic["topic_id"],
                severity="warning",
                confidence="high",
                requires_review=False,
                payload={
                    "hypercube": cube["cube_qname"],
                    "check": {
                        "dimensions_must_be_from_cube": True,
                        "members_must_be_in_domain_tree": True,
                    },
                    "taxonomy_basis": {
                        "elr": cube["elr"],
                        "elr_definition": cube["elr_definition"],
                        "dimensions": [dimension["dimension_qname"] for dimension in cube["dimensions"]],
                    },
                },
            )
        )
    return rules


def _expected_dimension_usage_rule(topic: dict) -> CandidateRule | None:
    dimensions = _topic_dimensions(topic)
    primary_items = _topic_primary_items(topic)
    if not dimensions or len(primary_items) < 1:
        return None
    return CandidateRule(
        id=build_rule_id(topic_id=topic["topic_id"], rule_kind="DIM_EXPECTED", index=1),
        type="expected_dimension_usage",
        topic=topic["topic_id"],
        severity="info",
        confidence="medium",
        requires_review=True,
        payload={
            "trigger": {
                "primary_items_from_topic_present": {
                    "minimum_count": 1,
                    "primary_items": primary_items,
                }
            },
            "expect": {
                "at_least_one_dimension_from_topic_used": True,
            },
            "taxonomy_basis": {
                "dimensions": [dimension["dimension_qname"] for dimension in dimensions],
            },
        },
    )


def _member_validity_rules(topic: dict) -> list[CandidateRule]:
    rules: list[CandidateRule] = []
    for index, dimension in enumerate(_topic_dimensions(topic), start=1):
        rules.append(
            CandidateRule(
                id=build_rule_id(topic_id=topic["topic_id"], rule_kind="MEMBER_VALIDITY", index=index),
                type="dimension_member_validity",
                topic=topic["topic_id"],
                severity="warning",
                confidence="high",
                requires_review=False,
                payload={
                    "dimension": dimension["dimension_qname"],
                    "allowed_members": {
                        "from_domain_member_tree": True,
                        "domain_roots": dimension["domain_roots"],
                    },
                },
            )
        )
    return rules


def _modelling_suggestion_rule(topic: dict) -> CandidateRule | None:
    dimensions = _topic_dimensions(topic)
    if not dimensions:
        return None
    return CandidateRule(
        id=build_rule_id(topic_id=topic["topic_id"], rule_kind="MODELLING", index=1),
        type="modelling_suggestion",
        topic=topic["topic_id"],
        severity="info",
        confidence="medium",
        requires_review=True,
        payload={
            "finding": {
                "flat_note_detected": True,
            },
            "suggest": {
                "use_hypercubes": [cube["cube_qname"] for cube in topic["hypercubes"]],
                "dimensions": [dimension["dimension_qname"] for dimension in dimensions],
            },
            "message": (
                f"The taxonomy provides dimensional modelling for {topic['topic_label']}. "
                "Consider using the available hypercube and dimensions where flat tagging is used."
            ),
        },
    )


def _pfs_note_presence_rule(topic: dict, review_entry: dict) -> CandidateRule:
    statement_concepts = sorted({review_entry["qname"]})
    source_statement_roles = sorted(review_entry.get("source_statement_roles", []))
    headline_statement_roles = sorted(review_entry.get("headline_statement_roles", []))
    topic_primary_items = _topic_primary_items(topic)
    topic_dimensions = [dimension["dimension_qname"] for dimension in _topic_dimensions(topic)]
    source_hypercubes = sorted({cube["cube_qname"] for cube in topic["hypercubes"]})
    return CandidateRule(
        id=build_rule_id(topic_id=topic["topic_id"], rule_kind="TOPIC_NOTE", index=1),
        type="topic_note_presence",
        topic=topic["topic_id"],
        severity="info",
        confidence="medium",
        requires_review=True,
        payload={
            "trigger": {
                "statement_concepts_present": {
                    "minimum_count": 1,
                    "concepts": statement_concepts,
                    "source_statement_roles": source_statement_roles,
                    "headline_statement_roles": headline_statement_roles,
                }
            },
            "expect": {
                "topic_note_present": True,
                "topic_primary_items": topic_primary_items,
                "topic_hypercubes": source_hypercubes,
            },
            "taxonomy_basis": {
                "matched_topic_label": topic["topic_label"],
                "matched_statement_concept_label": review_entry["label"],
                "allowed_non_statement_presentation_roles": review_entry["allowed_non_statement_presentation_roles"],
                "supplementary_non_statement_presentation_roles": review_entry[
                    "supplementary_non_statement_presentation_roles"
                ],
                "topic_dimensions": topic_dimensions,
            },
        },
    )


def _topic_rules(
    topic: dict,
    *,
    pfs_note_review_index: dict[str, dict],
    concept_index: dict[str, dict],
    parent_child_graph: dict[str, set[str]],
) -> list[dict]:
    rules: list[CandidateRule] = []
    review_entry = pfs_note_review_index.get(topic["topic_id"])
    if review_entry:
        rules.append(_pfs_note_presence_rule(topic, review_entry))
    rules.extend(_cube_conformity_rules(topic))
    expected = _expected_dimension_usage_rule(topic)
    if expected:
        rules.append(expected)
    rules.extend(_member_validity_rules(topic))
    concept_arithmetic = _concept_arithmetic_rule(topic)
    if concept_arithmetic:
        rules.append(concept_arithmetic)
    rules.extend(_dimensional_aggregation_rules(topic))
    movement = _movement_reconciliation_rule(topic, concept_index)
    if movement:
        rules.append(movement)
    modelling = _modelling_suggestion_rule(topic)
    if modelling:
        rules.append(modelling)
    return [rule.to_dict() for rule in rules]


def _pfs_note_review_index(review_payload: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for entry in review_payload.get("bucket_1_exact_topic_candidates", []):
        for match in entry.get("exact_topic_label_matches", []):
            topic_id = match.get("topic_id")
            if topic_id:
                index[topic_id] = entry
    return index


def generate_candidate_pack(
    *,
    topics_payload: dict,
    selected_topic_ids: list[str],
    pfs_note_review_payload: dict,
    concepts_payload: list[dict],
    roles_payload: dict,
) -> dict:
    topic_lookup = {topic["topic_id"]: topic for topic in topics_payload["topics"]}
    pfs_note_review_index = _pfs_note_review_index(pfs_note_review_payload)
    concept_index = {concept["qname"]: concept for concept in concepts_payload if concept.get("qname")}
    selected_topics = [
        topic_lookup[topic_id]
        for topic_id in selected_topic_ids
        if topic_id in topic_lookup and _is_business_facing_topic(topic_lookup[topic_id])
    ]

    topics = []
    for topic in selected_topics:
        source_hypercubes = sorted({cube["cube_qname"] for cube in topic["hypercubes"]})
        source_hypercube_occurrences = [
            {
                "hypercube": cube["cube_qname"],
                "elr": cube["elr"],
                "elr_definition": cube["elr_definition"],
                "occurrence_type": cube["occurrence_type"],
                "variant_label": cube["variant_label"],
            }
            for cube in topic["hypercubes"]
        ]
        topics.append(
            {
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "source_hypercubes": source_hypercubes,
                "source_hypercube_occurrences": source_hypercube_occurrences,
                "candidate_rules": _topic_rules(
                    topic,
                    pfs_note_review_index=pfs_note_review_index,
                    concept_index=concept_index,
                    parent_child_graph={},
                ),
            }
        )

    anti_rules = [
        {
            "id": "ANTI.PRESENTATION_SUM.001",
            "type": "blocked_inference",
            "reason": "Do not assume every presentation child sums to parent without an explicit generated arithmetic candidate.",
        },
        {
            "id": "ANTI.MISSING_ZERO.001",
            "type": "blocked_inference",
            "reason": "Do not treat missing dimensional facts as zero.",
        },
    ]

    return {
        "taxonomy_year": topics_payload["taxonomy_year"],
        "entrypoint": topics_payload["entrypoint"],
        "topics": topics,
        "anti_rules": anti_rules,
    }


def write_split_candidate_pack(*, candidate_pack: dict, output_root: Path) -> None:
    output_root.mkdir(parents=True, exist_ok=True)

    expected_topic_dirs = {
        _title_dir_name(topic["topic_label"])
        for topic in candidate_pack["topics"]
    }
    expected_root_dirs = expected_topic_dirs | {_family_dir_name("blocked_inference")}
    for child in output_root.iterdir():
        if child.name == "manifest.json":
            continue
        if child.is_dir() and child.name not in expected_root_dirs:
            _safe_rmtree(child)

    manifest = {
        "taxonomy_year": candidate_pack["taxonomy_year"],
        "entrypoint": candidate_pack["entrypoint"],
        "topic_count": len(candidate_pack["topics"]),
        "topics": [
            {
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "directory": _title_dir_name(topic["topic_label"]),
                "rule_count": len(topic["candidate_rules"]),
                "families": sorted(
                    {
                        _family_dir_name(rule["type"])
                        for rule in topic["candidate_rules"]
                    }
                ),
            }
            for topic in candidate_pack["topics"]
        ],
        "anti_rules_directory": _family_dir_name("blocked_inference"),
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for topic in candidate_pack["topics"]:
        topic_dir = output_root / _title_dir_name(topic["topic_label"])
        topic_dir.mkdir(parents=True, exist_ok=True)

        topic_metadata = {
            "topic_id": topic["topic_id"],
            "topic_label": topic["topic_label"],
            "source_hypercubes": topic["source_hypercubes"],
            "source_hypercube_occurrences": topic["source_hypercube_occurrences"],
            "rule_count": len(topic["candidate_rules"]),
        }
        (topic_dir / "topic.json").write_text(json.dumps(topic_metadata, indent=2), encoding="utf-8")

        family_rules: dict[str, list[dict]] = {}
        for rule in topic["candidate_rules"]:
            family_rules.setdefault(_family_dir_name(rule["type"]), []).append(rule)

        expected_family_dirs = set(family_rules)
        for child in topic_dir.iterdir():
            if child.name == "topic.json":
                continue
            if child.is_dir() and child.name not in expected_family_dirs:
                _safe_rmtree(child)

        for family_dir_name, rules in family_rules.items():
            family_dir = topic_dir / family_dir_name
            family_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "rule_family": family_dir_name,
                "rule_count": len(rules),
                "rules": rules,
            }
            (family_dir / "rules.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    anti_rules_dir = output_root / _family_dir_name("blocked_inference")
    anti_rules_dir.mkdir(parents=True, exist_ok=True)
    anti_rules_payload = {
        "entrypoint": candidate_pack["entrypoint"],
        "taxonomy_year": candidate_pack["taxonomy_year"],
        "rule_family": _family_dir_name("blocked_inference"),
        "rule_count": len(candidate_pack["anti_rules"]),
        "rules": candidate_pack["anti_rules"],
    }
    (anti_rules_dir / "rules.json").write_text(json.dumps(anti_rules_payload, indent=2), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate starter candidate rules from discovered topics.")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument(
        "--pfs-note-review",
        default="backend/validation_rules/generated/2026/frs102/pfs_note_linkages_review.json",
    )
    parser.add_argument("--concepts", default="backend/validation_rules/generated/2026/frs102/concepts.json")
    parser.add_argument("--roles", default="backend/validation_rules/generated/2026/frs102/roles.json")
    parser.add_argument("--output", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates.json")
    parser.add_argument("--split-output-dir", default=None)
    parser.add_argument("--topic", action="append", dest="topics_filter", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    topics_path = Path(args.topics)
    payload = _load_topics(topics_path)
    pfs_note_review_payload = _load_pfs_note_review(Path(args.pfs_note_review))
    concepts_payload = _load_concepts(Path(args.concepts))
    roles_payload = _load_roles(Path(args.roles))
    selected = args.topics_filter or [topic["topic_id"] for topic in payload["topics"]]
    candidate_pack = generate_candidate_pack(
        topics_payload=payload,
        selected_topic_ids=selected,
        pfs_note_review_payload=pfs_note_review_payload,
        concepts_payload=concepts_payload,
        roles_payload=roles_payload,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(candidate_pack, indent=2), encoding="utf-8")

    split_output_dir = (
        Path(args.split_output_dir)
        if args.split_output_dir
        else output_path.with_suffix("")
    )
    write_split_candidate_pack(candidate_pack=candidate_pack, output_root=split_output_dir)

    print(
        json.dumps(
            {
                "output": str(output_path),
                "split_output_dir": str(split_output_dir),
                "topic_count": len(candidate_pack["topics"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
