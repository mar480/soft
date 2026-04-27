from __future__ import annotations

import argparse
import json
from collections import deque
from pathlib import Path

from arelle import XbrlConst

from backend.validation_rules.taxonomy.concept_index import format_qname
from backend.validation_rules.taxonomy.entrypoints import generated_output_dir
from backend.validation_rules.taxonomy.taxonomy_loader import load_taxonomy_entrypoint

STATEMENT_ROLE_URIS = {
    "balance_sheet": "http://xbrl.frc.org.uk/fr/core/roles/BalanceSheetFRS102",
    "income_statement": "http://xbrl.frc.org.uk/fr/core/roles/IncomeStatementFRS102",
    "statement_comprehensive_income": "http://xbrl.frc.org.uk/fr/core/roles/StatementComprehensiveIncomeFRS102",
    "statement_changes_equity": "http://xbrl.frc.org.uk/fr/core/roles/StatementChangesEquityFRS102",
    "cash_flow_statement": "http://xbrl.frc.org.uk/fr/core/roles/CashFlowStatementFRS102",
}

_TRIGGER_LABEL_BLOCK_TERMS = (
    "free-text comment",
    "description of ",
    "[true/false]",
)

_TRIGGER_DATA_TYPES = {
    "xbrli:monetaryItemType",
    "num:perShareItemType",
}


def _concept_label(concept: object) -> str | None:
    return concept.label(
        preferredLabel=XbrlConst.standardLabel,
        lang="en-GB",
        fallbackToQname=False,
    ) or concept.label(
        preferredLabel=XbrlConst.standardLabel,
        lang="en",
        fallbackToQname=False,
    )


def _is_reportable_primary_item(concept: object | None) -> bool:
    if concept is None:
        return False
    if not getattr(concept, "isItem", False):
        return False
    if getattr(concept, "isDimensionItem", False) or getattr(concept, "isHypercubeItem", False):
        return False
    return True


def _is_statement_trigger_candidate(concept: object | None) -> bool:
    if not _is_reportable_primary_item(concept):
        return False
    if getattr(concept, "isAbstract", False):
        return False
    if not bool(getattr(concept, "isNumeric", False)):
        return False
    concept_type_qname = format_qname(getattr(getattr(concept, "type", None), "qname", None))
    if concept_type_qname not in _TRIGGER_DATA_TYPES:
        return False
    label = (_concept_label(concept) or "").lower()
    local_name = str(getattr(concept, "name", getattr(concept, "qname", ""))).lower()
    if any(term in label for term in _TRIGGER_LABEL_BLOCK_TERMS):
        return False
    if "truefalse" in local_name:
        return False
    return True


def _role_definition(model_xbrl: object, role_uri: str) -> str | None:
    role_types = model_xbrl.roleTypes.get(role_uri, [])
    if not role_types:
        return None
    return getattr(role_types[0], "definition", None)


def _record_for_concept(concept: object, *, depth: int) -> dict:
    return {
        "qname": format_qname(getattr(concept, "qname", None)),
        "label": _concept_label(concept),
        "abstract": getattr(concept, "isAbstract", False),
        "depth": depth,
    }


def _role_roots(relationships: list[object]) -> list[object]:
    from_concepts: dict[str, object] = {}
    to_qnames: set[str] = set()
    for relationship in relationships:
        from_concept = getattr(relationship, "fromModelObject", None)
        to_concept = getattr(relationship, "toModelObject", None)
        from_qname = format_qname(getattr(from_concept, "qname", None))
        to_qname = format_qname(getattr(to_concept, "qname", None))
        if from_qname and from_concept is not None:
            from_concepts[from_qname] = from_concept
        if to_qname:
            to_qnames.add(to_qname)
    root_qnames = sorted(set(from_concepts) - to_qnames)
    return [from_concepts[qname] for qname in root_qnames]


def _statement_role_basis(model_xbrl: object, role_name: str, role_uri: str) -> dict:
    relationship_set = model_xbrl.relationshipSet(XbrlConst.parentChild, role_uri)
    relationships = list(relationship_set.modelRelationships)
    roots = _role_roots(relationships)

    all_non_abstract: dict[str, dict] = {}
    all_trigger_candidates: dict[str, dict] = {}
    headline_trigger_candidates: dict[str, dict] = {}
    root_entries: list[dict] = []

    for root in roots:
        queue: deque[tuple[object, int]] = deque([(root, 0)])
        visited: set[str] = set()
        root_trigger_candidates: list[dict] = []

        while queue:
            concept, depth = queue.popleft()
            qname = format_qname(getattr(concept, "qname", None))
            if not qname or qname in visited:
                continue
            visited.add(qname)

            if depth > 0 and _is_reportable_primary_item(concept) and not getattr(concept, "isAbstract", False):
                record = _record_for_concept(concept, depth=depth)
                all_non_abstract[qname] = record
                if _is_statement_trigger_candidate(concept):
                    all_trigger_candidates[qname] = record
                    root_trigger_candidates.append(record)

            children = []
            for relationship in relationship_set.fromModelObject(concept):
                child = getattr(relationship, "toModelObject", None)
                child_qname = format_qname(getattr(child, "qname", None))
                if not child_qname or child_qname in visited:
                    continue
                children.append(child)

            for child in children:
                queue.append((child, depth + 1))

        min_depth = min((record["depth"] for record in root_trigger_candidates), default=None)
        root_headline = (
            [record for record in root_trigger_candidates if record["depth"] == min_depth]
            if min_depth is not None
            else []
        )
        for record in root_headline:
            headline_trigger_candidates[record["qname"]] = record

        root_entries.append(
            {
                "root": _record_for_concept(root, depth=0),
                "headline_trigger_candidates": root_headline,
            }
        )

    return {
        "statement_role": role_name,
        "role_uri": role_uri,
        "definition": _role_definition(model_xbrl, role_uri),
        "root_count": len(root_entries),
        "roots": root_entries,
        "headline_concept_count": len(headline_trigger_candidates),
        "headline_concepts": sorted(headline_trigger_candidates.values(), key=lambda item: item["qname"]),
        "all_trigger_candidate_count": len(all_trigger_candidates),
        "all_trigger_candidates": sorted(all_trigger_candidates.values(), key=lambda item: item["qname"]),
        "all_non_abstract_concept_count": len(all_non_abstract),
        "all_non_abstract_concepts": sorted(all_non_abstract.values(), key=lambda item: item["qname"]),
    }


def build_statement_basis(*, taxonomy_year: int, entrypoint: str, taxonomy_root: str | Path) -> dict:
    model = load_taxonomy_entrypoint(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint,
        taxonomy_root=taxonomy_root,
    )
    try:
        statements = [
            _statement_role_basis(model.model_xbrl, role_name, role_uri)
            for role_name, role_uri in STATEMENT_ROLE_URIS.items()
        ]
        return {
            "taxonomy_year": taxonomy_year,
            "entrypoint": model.entrypoint_name,
            "statement_role_count": len(statements),
            "statements": statements,
        }
    finally:
        model.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract PFS/SOCI presentation concept basis for Family 1 design.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = build_statement_basis(
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
        / "pfs_statement_concepts.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "statement_role_count": len(payload["statements"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
