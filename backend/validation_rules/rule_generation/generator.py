from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil

from .rule_ids import build_rule_id
from .rule_schema import CandidateRule

RULE_FAMILY_LAYOUT = {
    "hypercube_conformity": (2, "Hypercube conformity"),
    "expected_dimension_usage": (3, "Expected dimension usage"),
    "dimension_member_validity": (4, "Member validity"),
    "dimension_member_rollup_candidate": (6, "Dimension member roll-up"),
    "modelling_suggestion": (8, "Modelling suggestion"),
    "blocked_inference": (9, "Anti-rules"),
}

_ROLLUP_DIMENSION_ALLOW_TERMS = (
    "class",
    "classes",
    "category",
    "categories",
    "ownership",
)

_ROLLUP_DIMENSION_BLOCK_TERMS = (
    "analysis",
    "continuing",
    "discontinued",
    "geographic",
    "group",
    "grouping",
    "major customer",
    "major customers",
    "operating segment",
    "operating segments",
    "product",
    "products",
    "range",
    "reconciliation",
    "restatement",
    "first time adoption",
    "service",
    "services",
    "segment",
)

_ROLLUP_TOPIC_BLOCKLIST = {
    "accounting_standards_applied",
    "accounts_status",
    "accounts_type",
    "applicable_legislation",
    "audit_firm_contact_info",
    "bonus_pay_information",
    "countries",
    "currencies",
    "diversity_and_inclusion_reporting_board_and_executive_management_by_ethnicity",
    "diversity_and_inclusion_reporting_board_and_executive_management_by_gender",
    "diversity_and_inclusion_reporting_board_and_executive_management_by_sex",
    "entity_contact_info",
    "entity_officers",
    "entity_special_legal_status",
    "entity_trading_status",
    "hourly_pay_information",
    "income_main_by_ethnicity",
    "income_main_by_ethnicity_main",
    "income_main_by_gender",
    "income_main_by_gender_main",
    "income_main_by_sex",
    "income_main_by_sex_main",
    "intermediary_payment",
    "languages",
    "legal_form_of_entity",
    "main_industry_sector",
    "pay_ratio_information",
    "related_parties",
    "related_parties_balances",
    "report_period",
    "scope_of_accounts",
    "segments_text",
    "staff_by_gender",
    "staff_by_sex",
    "streamlined_energy_and_carbon_reporting",
    "third_party_agents",
    "u_s_es",
}


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


def _is_rollup_candidate_dimension(dimension: dict, root: dict, component_members: list[str]) -> bool:
    dimension_text = " ".join(
        filter(
            None,
            (
                _normalise_text(dimension.get("dimension_qname")),
                _normalise_text(dimension.get("dimension_label")),
                _normalise_text(root.get("qname")),
                _normalise_text(root.get("label")),
            ),
        )
    )

    if any(term in dimension_text for term in _ROLLUP_DIMENSION_BLOCK_TERMS):
        return False

    if any(member == "common:NotApplicable" for member in component_members):
        return False

    return any(term in dimension_text for term in _ROLLUP_DIMENSION_ALLOW_TERMS)


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
    if topic["topic_id"] in _ROLLUP_TOPIC_BLOCKLIST:
        return []

    rules: list[CandidateRule] = []
    for dimension in _topic_dimensions(topic):
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
        if not _is_rollup_candidate_dimension(dimension, root, component_members):
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
    parser.add_argument("--output", default="backend/validation_rules/rule_packs/2026/auto/frs102_candidates.json")
    parser.add_argument("--split-output-dir", default=None)
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
