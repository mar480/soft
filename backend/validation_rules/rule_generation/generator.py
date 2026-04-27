from __future__ import annotations

import argparse
import json
from pathlib import Path

from .rule_ids import build_rule_id
from .rule_schema import CandidateRule


def _load_topics(topics_path: Path) -> dict:
    return json.loads(topics_path.read_text(encoding="utf-8"))


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


def _normalise_head_member(qname: str) -> str:
    return qname[:-7] if qname.endswith("Default") else qname


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


def _dimension_rollup_rules(topic: dict) -> list[CandidateRule]:
    rules: list[CandidateRule] = []
    for index, dimension in enumerate(_topic_dimensions(topic), start=1):
        member_tree = dimension.get("member_tree", [])
        if not member_tree:
            continue

        root = member_tree[0]
        children = root.get("children", [])
        if len(children) < 2:
            continue

        head_member = _normalise_head_member(root["qname"])
        component_members = [_normalise_head_member(child["qname"]) for child in children]
        if head_member in component_members:
            continue

        rules.append(
            CandidateRule(
                id=build_rule_id(topic_id=topic["topic_id"], rule_kind="DIM_ROLLUP", index=len(rules) + 1),
                type="dimension_member_rollup_candidate",
                topic=topic["topic_id"],
                severity="warning",
                confidence="medium",
                requires_review=True,
                payload={
                    "dimension": dimension["dimension_qname"],
                    "head_member": head_member,
                    "component_members": {
                        "from_domain_member_children": True,
                        "members": component_members,
                        "domain_root": root["qname"],
                    },
                    "match": {
                        "primary_item": "same",
                        "period": "same",
                        "unit": "same",
                        "all_other_dimensions": "same",
                    },
                    "missing_policy": "do_not_treat_missing_as_zero",
                    "safe_only": True,
                    "taxonomy_basis": {
                        "domain_root": root["qname"],
                        "dimension_label": dimension.get("dimension_label"),
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


def _topic_rules(topic: dict) -> list[dict]:
    rules: list[CandidateRule] = []
    rules.extend(_cube_conformity_rules(topic))
    expected = _expected_dimension_usage_rule(topic)
    if expected:
        rules.append(expected)
    rules.extend(_member_validity_rules(topic))
    rules.extend(_dimension_rollup_rules(topic))
    modelling = _modelling_suggestion_rule(topic)
    if modelling:
        rules.append(modelling)
    return [rule.to_dict() for rule in rules]


def generate_candidate_pack(*, topics_payload: dict, selected_topic_ids: list[str]) -> dict:
    topic_lookup = {topic["topic_id"]: topic for topic in topics_payload["topics"]}
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
                "candidate_rules": _topic_rules(topic),
            }
        )

    anti_rules = [
        {
            "id": "ANTI.PRESENTATION_SUM.001",
            "type": "blocked_inference",
            "reason": "Do not assume presentation children sum to parent.",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate starter candidate rules from discovered topics.")
    parser.add_argument("--topics", default="backend/validation_rules/generated/2026/frs102/topics.json")
    parser.add_argument("--output", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates.json")
    parser.add_argument("--topic", action="append", dest="topics_filter", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    topics_path = Path(args.topics)
    payload = _load_topics(topics_path)
    selected = args.topics_filter or [topic["topic_id"] for topic in payload["topics"]]
    candidate_pack = generate_candidate_pack(topics_payload=payload, selected_topic_ids=selected)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(candidate_pack, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "topic_count": len(candidate_pack["topics"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
