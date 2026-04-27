from __future__ import annotations

import argparse
import json
from pathlib import Path

from arelle import XbrlConst

from backend.validation_rules.rule_generation.pfs_statement_basis import STATEMENT_ROLE_URIS, build_statement_basis
from backend.validation_rules.taxonomy.concept_index import format_qname
from backend.validation_rules.taxonomy.entrypoints import generated_output_dir
from backend.validation_rules.taxonomy.taxonomy_loader import load_taxonomy_entrypoint


def _normalise_text(value: str | None) -> str:
    return " ".join((value or "").replace("-", " ").replace("_", " ").lower().split())


def _load_topics(topics_path: Path) -> dict:
    return json.loads(topics_path.read_text(encoding="utf-8"))


def _is_business_facing_topic(topic: dict) -> bool:
    return topic.get("priority") != "deprioritised" and topic.get("topic_kind") == "disclosure_family"


def _statement_concept_index(statement_basis: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for statement in statement_basis["statements"]:
        role_name = statement["statement_role"]
        headline_qnames = {item["qname"] for item in statement["headline_concepts"]}
        for concept in statement["all_trigger_candidates"]:
            entry = index.setdefault(
                concept["qname"],
                {
                    "qname": concept["qname"],
                    "label": concept["label"],
                    "statement_roles": [],
                    "headline_statement_roles": [],
                    "min_depth_by_statement_role": {},
                },
            )
            entry["statement_roles"].append(role_name)
            entry["min_depth_by_statement_role"][role_name] = concept["depth"]
            if concept["qname"] in headline_qnames:
                entry["headline_statement_roles"].append(role_name)

    for entry in index.values():
        entry["statement_roles"].sort()
        entry["headline_statement_roles"].sort()
    return index


def _topic_label_index(topics_payload: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = {}
    for topic in topics_payload["topics"]:
        if not _is_business_facing_topic(topic):
            continue
        key = _normalise_text(topic.get("topic_label"))
        if not key:
            continue
        index.setdefault(key, []).append(
            {
                "topic_id": topic["topic_id"],
                "topic_label": topic["topic_label"],
                "priority": topic["priority"],
                "topic_kind": topic["topic_kind"],
            }
        )
    for matches in index.values():
        matches.sort(key=lambda item: item["topic_id"])
    return index


def _presentation_role_classification(role_uri: str, definition: str | None) -> str:
    text = _normalise_text(f"{role_uri} {definition or ''}")
    if "notes and detailed disclosures" in text:
        return "notes_and_detailed_disclosures"
    if "additional industry sector data" in text:
        return "additional_industry_sector_data"
    if "directors strategic report" in text or "directors report" in text or "/direp/" in role_uri:
        return "directors_report"
    if "full detailed profit and loss" in text:
        return "detailed_profit_and_loss"
    return "other_presentation_role"


def _non_statement_role_appearances(model_xbrl: object, tracked_qnames: set[str]) -> dict[str, list[dict]]:
    appearances: dict[str, list[dict]] = {qname: [] for qname in tracked_qnames}
    statement_role_uris = set(STATEMENT_ROLE_URIS.values())

    for role_uri in sorted(model_xbrl.roleTypes):
        if role_uri in statement_role_uris:
            continue
        relationship_set = model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri)
        relationships = list(relationship_set.modelRelationships)
        if not relationships:
            continue

        seen_qnames: set[str] = set()
        for relationship in relationships:
            from_qname = format_qname(getattr(getattr(relationship, "fromModelObject", None), "qname", None))
            to_qname = format_qname(getattr(getattr(relationship, "toModelObject", None), "qname", None))
            if from_qname in tracked_qnames:
                seen_qnames.add(from_qname)
            if to_qname in tracked_qnames:
                seen_qnames.add(to_qname)

        if not seen_qnames:
            continue

        role_types = model_xbrl.roleTypes.get(role_uri, [])
        definition = getattr(role_types[0], "definition", None) if role_types else None
        role_record = {
            "role_uri": role_uri,
            "definition": definition,
            "classification": _presentation_role_classification(role_uri, definition),
        }
        for qname in sorted(seen_qnames):
            appearances[qname].append(role_record)

    for records in appearances.values():
        records.sort(key=lambda item: ((item["classification"] or ""), (item["definition"] or ""), item["role_uri"]))
    return appearances


def build_pfs_note_linkages(*, taxonomy_year: int, entrypoint: str, taxonomy_root: str | Path) -> dict:
    output_dir = generated_output_dir(taxonomy_year=taxonomy_year, entrypoint_name=entrypoint)
    statement_basis = build_statement_basis(
        taxonomy_year=taxonomy_year,
        entrypoint=entrypoint,
        taxonomy_root=taxonomy_root,
    )
    topics_payload = _load_topics(output_dir / "topics.json")
    statement_index = _statement_concept_index(statement_basis)
    topic_index = _topic_label_index(topics_payload)

    model = load_taxonomy_entrypoint(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint,
        taxonomy_root=taxonomy_root,
    )
    try:
        appearances = _non_statement_role_appearances(model.model_xbrl, set(statement_index))
    finally:
        model.close()

    allowed_role_classes = {
        "notes_and_detailed_disclosures",
        "additional_industry_sector_data",
        "directors_report",
    }

    linked_concepts: list[dict] = []
    included_linked_concepts: list[dict] = []
    exact_topic_match_count = 0
    for qname in sorted(statement_index):
        statement_entry = statement_index[qname]
        non_statement_roles = appearances.get(qname, [])
        allowed_non_statement_roles = [
            role for role in non_statement_roles if role["classification"] in allowed_role_classes
        ]
        exact_topic_matches = topic_index.get(_normalise_text(statement_entry["label"]), [])
        if not non_statement_roles and not exact_topic_matches:
            continue
        entry = {
            "qname": qname,
            "label": statement_entry["label"],
            "source_statement_roles": statement_entry["statement_roles"],
            "headline_statement_roles": statement_entry["headline_statement_roles"],
            "min_depth_by_statement_role": statement_entry["min_depth_by_statement_role"],
            "non_statement_presentation_roles": non_statement_roles,
            "non_statement_role_count": len(non_statement_roles),
            "allowed_non_statement_presentation_roles": allowed_non_statement_roles,
            "allowed_non_statement_role_count": len(allowed_non_statement_roles),
            "has_notes_and_detailed_disclosures_role": any(
                role["classification"] == "notes_and_detailed_disclosures" for role in allowed_non_statement_roles
            ),
            "supplementary_non_statement_presentation_roles": [
                role for role in allowed_non_statement_roles if role["classification"] != "notes_and_detailed_disclosures"
            ],
            "exact_topic_label_matches": exact_topic_matches,
        }
        linked_concepts.append(entry)
        if allowed_non_statement_roles:
            included_linked_concepts.append(entry)
            if exact_topic_matches:
                exact_topic_match_count += 1

    family_1_exact_topic_candidates = [
        {
            "qname": entry["qname"],
            "label": entry["label"],
            "source_statement_roles": entry["source_statement_roles"],
            "headline_statement_roles": entry["headline_statement_roles"],
            "allowed_non_statement_presentation_roles": entry["allowed_non_statement_presentation_roles"],
            "supplementary_non_statement_presentation_roles": entry["supplementary_non_statement_presentation_roles"],
            "exact_topic_label_matches": entry["exact_topic_label_matches"],
        }
        for entry in included_linked_concepts
        if entry["has_notes_and_detailed_disclosures_role"] and entry["exact_topic_label_matches"]
    ]

    family_1_linked_note_candidates = [
        {
            "qname": entry["qname"],
            "label": entry["label"],
            "source_statement_roles": entry["source_statement_roles"],
            "headline_statement_roles": entry["headline_statement_roles"],
            "notes_and_detailed_disclosures_roles": [
                role
                for role in entry["allowed_non_statement_presentation_roles"]
                if role["classification"] == "notes_and_detailed_disclosures"
            ],
            "supplementary_non_statement_presentation_roles": entry["supplementary_non_statement_presentation_roles"],
            "exact_topic_label_matches": entry["exact_topic_label_matches"],
        }
        for entry in included_linked_concepts
        if entry["has_notes_and_detailed_disclosures_role"] and not entry["exact_topic_label_matches"]
    ]

    review_payload = {
        "taxonomy_year": taxonomy_year,
        "entrypoint": entrypoint,
        "review_scope": {
            "primary_required_role_classification": "notes_and_detailed_disclosures",
            "supplementary_role_classifications": [
                "additional_industry_sector_data",
                "directors_report",
            ],
            "excluded_role_classifications_from_family_1_review": [
                "detailed_profit_and_loss",
                "other_presentation_role",
            ],
        },
        "bucket_1_exact_topic_candidates": family_1_exact_topic_candidates,
        "bucket_2_notes_linked_candidates_without_exact_topic_match": family_1_linked_note_candidates,
    }

    return {
        "taxonomy_year": taxonomy_year,
        "entrypoint": entrypoint,
        "statement_role_count": len(statement_basis["statements"]),
        "statement_roles": [statement["statement_role"] for statement in statement_basis["statements"]],
        "included_role_classifications": sorted(allowed_role_classes),
        "trigger_concept_count": len(statement_index),
        "linked_concept_count": len(linked_concepts),
        "linked_concepts_with_non_statement_role_count": sum(
            1 for entry in linked_concepts if entry["non_statement_role_count"] > 0
        ),
        "included_linked_concept_count": len(included_linked_concepts),
        "exact_topic_match_count": exact_topic_match_count,
        "family_1_exact_topic_candidate_count": len(family_1_exact_topic_candidates),
        "family_1_linked_note_candidate_count": len(family_1_linked_note_candidates),
        "linked_concepts": linked_concepts,
        "included_linked_concepts": included_linked_concepts,
        "family_1_exact_topic_candidates": family_1_exact_topic_candidates,
        "family_1_linked_note_candidates": family_1_linked_note_candidates,
        "family_1_review": review_payload,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Link PFS/SOCI trigger concepts to non-statement presentation roles and exact discovered topics."
    )
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_pfs_note_linkages(
        taxonomy_year=args.taxonomy_year,
        entrypoint=args.entrypoint,
        taxonomy_root=args.taxonomy_root,
    )

    output_path = (
        Path(args.output)
        if args.output
        else generated_output_dir(
            taxonomy_year=args.taxonomy_year,
            entrypoint_name=args.entrypoint,
        )
        / "pfs_note_linkages.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    review_output_path = output_path.with_name("pfs_note_linkages_review.json")
    review_output_path.write_text(json.dumps(payload["family_1_review"], indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(output_path),
                "review_output": str(review_output_path),
                "linked_concept_count": payload["linked_concept_count"],
                "family_1_exact_topic_candidate_count": payload["family_1_exact_topic_candidate_count"],
                "family_1_linked_note_candidate_count": payload["family_1_linked_note_candidate_count"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
