from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

from backend.validation_rules.taxonomy.entrypoints import generated_output_dir


DEFAULT_ENTITY = "synthetic:ExampleEntity"
DEFAULT_UNIT = "iso4217:GBP"
IX_NS = "http://www.xbrl.org/2013/inlineXBRL"
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
XHTML_NS = "http://www.w3.org/1999/xhtml"

PROFILE_PRESETS = {
    "balance_sheet_minimal": {
        "kind": "statement",
        "statement_role": "balance_sheet",
        "concept_values": {
            "core:FixedAssets": 150000,
            "core:CurrentAssets": 90000,
            "core:Creditors": 40000,
            "core:NetCurrentAssetsLiabilities": 50000,
            "core:TotalAssetsLessCurrentLiabilities": 200000,
            "core:NetAssetsLiabilities": 180000,
            "core:Equity": 180000,
        },
    },
    "creditors_missing_note": {
        "kind": "statement",
        "statement_role": "balance_sheet",
        "concept_values": {
            "core:CurrentAssets": 90000,
            "core:Creditors": 40000,
            "core:NetAssetsLiabilities": 50000,
            "core:Equity": 50000,
        },
    },
    "ppe_note_minimal": {
        "kind": "topic",
        "topic_id": "property_plant_equipment",
        "concept_qname": "core:PropertyPlantEquipment",
        "row_mode": "dimensional",
    },
    "ppe_note_missing_dimensions": {
        "kind": "topic",
        "topic_id": "property_plant_equipment",
        "concept_qname": "core:PropertyPlantEquipment",
        "row_mode": "flat",
    },
    "ppe_note_invalid_member": {
        "kind": "topic",
        "topic_id": "property_plant_equipment",
        "concept_qname": "core:PropertyPlantEquipment",
        "row_mode": "invalid_member",
    },
    "ppe_note_invalid_hypercube": {
        "kind": "topic",
        "topic_id": "property_plant_equipment",
        "concept_qname": "core:PropertyPlantEquipment",
        "row_mode": "invalid_hypercube",
    },
    "creditors_note_minimal": {
        "kind": "topic",
        "topic_id": "creditors",
        "concept_qname": "core:Creditors",
        "row_mode": "dimensional",
    },
}

PREFERRED_DIMENSION_TERMS = (
    "classes",
    "class",
    "category",
    "categories",
    "ownership",
    "type",
    "range",
)

BLOCKED_DIMENSION_TERMS = (
    "groupcompanydata",
    "originalreviseddata",
    "restatementsfirsttimeadoption",
    "geographicsegments",
    "majorcustomers",
    "operatingsegments",
    "productsservices",
    "segmentreconciliation",
    "x-analysisdimension",
    "analysisdimension",
    "continuingdiscontinuedoperations",
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalise_text(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _iter_member_nodes(nodes: list[dict]) -> list[dict]:
    collected: list[dict] = []
    stack = list(reversed(nodes))
    while stack:
        node = stack.pop()
        collected.append(node)
        stack.extend(reversed(node.get("children", [])))
    return collected


def _leaf_members(dimension: dict) -> list[dict]:
    leaves: list[dict] = []
    for root in dimension.get("member_tree", []):
        for node in _iter_member_nodes([root]):
            if not node.get("children"):
                label = node.get("label")
                if "deprecated" in (label or "").lower():
                    continue
                leaves.append({"qname": node["qname"], "label": label})
    default_member = dimension.get("default_member")
    if default_member:
        leaves = [leaf for leaf in leaves if leaf["qname"] != default_member]
    return leaves


def _choose_note_dimensions(topic: dict) -> list[dict]:
    ranked: list[tuple[int, dict]] = []
    for cube in topic["hypercubes"]:
        for dimension in cube["dimensions"]:
            text = _normalise_text(f"{dimension.get('dimension_qname')} {dimension.get('dimension_label')}")
            if any(term in text for term in BLOCKED_DIMENSION_TERMS):
                continue
            score = 0
            for index, term in enumerate(PREFERRED_DIMENSION_TERMS):
                if term in text:
                    score = max(score, 100 - index)
            if score <= 0:
                continue
            ranked.append((score, dimension))
    seen: set[str] = set()
    selected: list[dict] = []
    for _, dimension in sorted(ranked, key=lambda item: (-item[0], item[1]["dimension_qname"])):
        key = dimension["dimension_qname"]
        if key in seen:
            continue
        seen.add(key)
        if _leaf_members(dimension):
            selected.append(dimension)
        if len(selected) >= 2:
            break
    return selected


def _context_id(index: int) -> str:
    return f"c{index:03d}"


def _fact_id(index: int) -> str:
    return f"f{index:03d}"


def _build_statement_example(statement_basis: dict, *, statement_role: str, end_date: str, concept_values: dict[str, int]) -> dict:
    statement = next(item for item in statement_basis["statements"] if item["statement_role"] == statement_role)
    concept_index = {item["qname"]: item for item in statement["headline_concepts"]}
    contexts = [
        {
            "context_id": _context_id(1),
            "entity": DEFAULT_ENTITY,
            "period": {"type": "instant", "instant": end_date},
            "dimensions": {},
        }
    ]
    facts: list[dict] = []
    rows: list[dict] = []
    for idx, (qname, value) in enumerate(concept_values.items(), start=1):
        concept = concept_index.get(qname, {"qname": qname, "label": qname})
        facts.append(
            {
                "fact_id": _fact_id(idx),
                "concept_qname": qname,
                "label": concept.get("label"),
                "context_id": _context_id(1),
                "unit": DEFAULT_UNIT,
                "decimals": 0,
                "value": value,
            }
        )
        rows.append(
            {
                "row_label": concept.get("label"),
                "concept_qname": qname,
                "label": concept.get("label"),
                "value": value,
                "dimensions": {},
                "context_id": _context_id(1),
                "unit": DEFAULT_UNIT,
                "decimals": 0,
                "topic_id": None,
                "anchor_primary_item_qnames": [],
            }
        )
    return {
        "example_kind": "statement",
        "statement_role": statement_role,
        "statement_definition": statement.get("definition"),
        "contexts": contexts,
        "facts": facts,
        "tables": [{"table_id": statement_role, "kind": "statement", "rows": rows}],
    }


def _topic_review_entry(review_payload: dict, topic_id: str) -> dict | None:
    for entry in review_payload.get("bucket_1_exact_topic_candidates", []):
        for match in entry.get("exact_topic_label_matches", []):
            if match.get("topic_id") == topic_id:
                return entry
    return None


def _build_topic_row_specs(selected_dimensions: list[dict], *, row_mode: str) -> list[dict]:
    if row_mode == "flat":
        return [
            {"row_label": "Total", "dimension_members": {}},
            {"row_label": "Line 1", "dimension_members": {}},
            {"row_label": "Line 2", "dimension_members": {}},
        ]

    if not selected_dimensions:
        return [{"row_label": "Total", "dimension_members": {}}]

    first_dimension = selected_dimensions[0]
    first_members = _leaf_members(first_dimension)
    second_dimension = selected_dimensions[1] if len(selected_dimensions) >= 2 else None
    second_members = _leaf_members(second_dimension) if second_dimension else []

    if row_mode == "invalid_member":
        return [
            {"row_label": "Total", "dimension_members": {}},
            {
                "row_label": first_members[0]["label"],
                "dimension_members": {
                    first_dimension["dimension_qname"]: "synthetic:InvalidMember",
                    second_dimension["dimension_qname"]: second_members[0]["qname"],
                },
            },
            {
                "row_label": first_members[1]["label"],
                "dimension_members": {
                    first_dimension["dimension_qname"]: first_members[1]["qname"],
                    second_dimension["dimension_qname"]: second_members[0]["qname"],
                },
            },
        ]

    if row_mode == "invalid_hypercube":
        return [
            {"row_label": "Total", "dimension_members": {}},
            {
                "row_label": first_members[0]["label"],
                "dimension_members": {
                    first_dimension["dimension_qname"]: first_members[0]["qname"],
                    "core:OtherRelatedPartyTypeDimension": "core:OtherRelatedPartiesMember",
                },
            },
            {
                "row_label": first_members[1]["label"],
                "dimension_members": {
                    first_dimension["dimension_qname"]: first_members[1]["qname"],
                    second_dimension["dimension_qname"]: second_members[0]["qname"],
                }
                if second_dimension and second_members
                else {
                    first_dimension["dimension_qname"]: first_members[1]["qname"],
                },
            },
        ]

    row_specs = [{"row_label": "Total", "dimension_members": {}}]
    for member in first_members[:2]:
        row_spec = {
            "row_label": member["label"],
            "dimension_members": {first_dimension["dimension_qname"]: member["qname"]},
        }
        if second_dimension and second_members:
            row_spec["dimension_members"][second_dimension["dimension_qname"]] = second_members[0]["qname"]
        row_specs.append(row_spec)
    return row_specs


def _build_topic_example(
    topics_payload: dict,
    review_payload: dict,
    *,
    topic_id: str,
    concept_qname: str | None,
    end_date: str,
    row_mode: str,
) -> dict:
    topic = next(item for item in topics_payload["topics"] if item["topic_id"] == topic_id)
    review_entry = _topic_review_entry(review_payload, topic_id)
    note_concept_qname = concept_qname or (review_entry["qname"] if review_entry else None) or topic["topic_id"]
    note_concept_label = review_entry["label"] if review_entry else topic["topic_label"]
    selected_dimensions = _choose_note_dimensions(topic)
    row_specs = _build_topic_row_specs(selected_dimensions, row_mode=row_mode)

    contexts: list[dict] = []
    facts: list[dict] = []
    rows: list[dict] = []
    anchor_primary_item_qnames = sorted({item["qname"] for cube in topic["hypercubes"] for item in cube["primary_items"]})
    for idx, row_spec in enumerate(row_specs, start=1):
        context = {
            "context_id": _context_id(idx),
            "entity": DEFAULT_ENTITY,
            "period": {"type": "instant", "instant": end_date},
            "dimensions": row_spec["dimension_members"],
        }
        contexts.append(context)
        value = 100000 if idx == 1 else 60000 - (idx - 2) * 15000
        facts.append(
            {
                "fact_id": _fact_id(idx),
                "concept_qname": note_concept_qname,
                "label": note_concept_label,
                "context_id": context["context_id"],
                "unit": DEFAULT_UNIT,
                "decimals": 0,
                "value": value,
                "anchor_primary_item_qnames": anchor_primary_item_qnames,
            }
        )
        rows.append(
            {
                "row_label": row_spec["row_label"],
                "concept_qname": note_concept_qname,
                "label": note_concept_label,
                "value": value,
                "dimensions": row_spec["dimension_members"],
                "context_id": context["context_id"],
                "unit": DEFAULT_UNIT,
                "decimals": 0,
                "topic_id": topic["topic_id"],
                "anchor_primary_item_qnames": anchor_primary_item_qnames,
            }
        )

    return {
        "example_kind": "topic_note",
        "topic_id": topic["topic_id"],
        "topic_label": topic["topic_label"],
        "concept_qname": note_concept_qname,
        "concept_label": note_concept_label,
        "selected_dimensions": [
            {
                "dimension_qname": dimension["dimension_qname"],
                "dimension_label": dimension.get("dimension_label"),
                "default_member": dimension.get("default_member"),
            }
            for dimension in selected_dimensions
        ],
        "contexts": contexts,
        "facts": facts,
        "tables": [{"table_id": topic["topic_id"], "kind": "topic_note", "rows": rows}],
    }


def build_synthetic_model(
    *,
    taxonomy_year: int,
    entrypoint: str,
    profile: str | None,
    statement_role: str | None,
    topic_id: str | None,
    concept_qname: str | None,
    end_date: str,
    generated_root: Path,
) -> dict:
    base_dir = generated_output_dir(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint,
        generated_root=generated_root,
    )
    statement_basis = _load_json(base_dir / "pfs_statement_concepts.json")
    topics_payload = _load_json(base_dir / "topics.json")
    review_payload = _load_json(base_dir / "pfs_note_linkages_review.json")

    concept_values = None
    row_mode = "dimensional"
    if profile:
        preset = PROFILE_PRESETS[profile]
        if preset["kind"] == "statement":
            statement_role = preset["statement_role"]
            concept_values = preset["concept_values"]
        else:
            topic_id = preset["topic_id"]
            concept_qname = preset.get("concept_qname")
            row_mode = preset.get("row_mode", "dimensional")

    if statement_role:
        example = _build_statement_example(
            statement_basis,
            statement_role=statement_role,
            end_date=end_date,
            concept_values=concept_values or PROFILE_PRESETS["balance_sheet_minimal"]["concept_values"],
        )
    elif topic_id:
        example = _build_topic_example(
            topics_payload,
            review_payload,
            topic_id=topic_id,
            concept_qname=concept_qname,
            end_date=end_date,
            row_mode=row_mode,
        )
    else:
        raise ValueError("One of profile, statement_role, or topic_id must be provided.")

    return {
        "taxonomy_year": taxonomy_year,
        "entrypoint": entrypoint,
        "entity": DEFAULT_ENTITY,
        "generated_for_rule_testing_only": True,
        "valid_ixbrl_not_required": False,
        "example": example,
    }


def load_synthetic_spec(
    *,
    spec_path: Path,
    taxonomy_year: int,
    entrypoint: str,
) -> dict:
    payload = _load_json(spec_path)
    if "example" in payload:
        payload.setdefault("taxonomy_year", taxonomy_year)
        payload.setdefault("entrypoint", entrypoint)
        payload.setdefault("entity", DEFAULT_ENTITY)
        payload.setdefault("generated_for_rule_testing_only", True)
        payload.setdefault("valid_ixbrl_not_required", False)
        return payload
    return {
        "taxonomy_year": taxonomy_year,
        "entrypoint": entrypoint,
        "entity": DEFAULT_ENTITY,
        "generated_for_rule_testing_only": True,
        "valid_ixbrl_not_required": False,
        "example": payload,
    }


def _xml_escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _format_dimensions(context: dict) -> str:
    dimensions = context.get("dimensions", {})
    if not dimensions:
        return ""
    lines = []
    for dimension_qname, member_qname in sorted(dimensions.items()):
        lines.append(
            f'          <xbrldi:explicitMember dimension="{_xml_escape(dimension_qname)}">{_xml_escape(member_qname)}</xbrldi:explicitMember>'
        )
    return "\n".join(lines)


def _render_contexts(example: dict) -> str:
    chunks: list[str] = []
    for context in example["contexts"]:
        dimensions_xml = _format_dimensions(context)
        segment_block = (
            "\n        <xbrli:segment>\n"
            f"{dimensions_xml}\n"
            "        </xbrli:segment>"
            if dimensions_xml
            else ""
        )
        chunks.append(
            "\n".join(
                [
                    f'      <xbrli:context id="{_xml_escape(context["context_id"])}">',
                    "        <xbrli:entity>",
                    f'          <xbrli:identifier scheme="https://example.test/entity">{_xml_escape(context["entity"])}</xbrli:identifier>{segment_block}',
                    "        </xbrli:entity>",
                    "        <xbrli:period>",
                    f'          <xbrli:instant>{_xml_escape(context["period"]["instant"])}</xbrli:instant>',
                    "        </xbrli:period>",
                    "      </xbrli:context>",
                ]
            )
        )
    return "\n".join(chunks)


def _render_fact_cell(row: dict, row_index: int) -> str:
    extra_attrs = []
    if row.get("topic_id"):
        extra_attrs.append(f'data-topic-id="{_xml_escape(row["topic_id"])}"')
    anchor_primary_items = row.get("anchor_primary_item_qnames", [])
    if anchor_primary_items:
        extra_attrs.append(
            f'data-anchor-primary-items="{_xml_escape("|".join(anchor_primary_items))}"'
        )
    extra_attr_text = (" " + " ".join(extra_attrs)) if extra_attrs else ""
    return (
        f'<ix:nonFraction id="{_xml_escape(_fact_id(row_index))}" '
        f'name="{_xml_escape(row["concept_qname"])}" '
        f'contextRef="{_xml_escape(row["context_id"])}" '
        f'unitRef="uGBP" decimals="{_xml_escape(row["decimals"])}"{extra_attr_text}>'
        f'{_xml_escape(row["value"])}'
        f'</ix:nonFraction>'
    )


def render_ixbrl_html(payload: dict) -> str:
    example = payload["example"]
    title = example.get("topic_label") or example.get("statement_definition") or example.get("statement_role") or "Synthetic Example"
    table = example["tables"][0]
    row_markup = []
    for index, row in enumerate(table["rows"], start=1):
        dimension_summary = ", ".join(f"{k}={v}" for k, v in sorted(row["dimensions"].items())) or "none"
        row_markup.append(
            "\n".join(
                [
                    "        <tr>",
                    f"          <td>{_xml_escape(row['row_label'])}</td>",
                    f"          <td>{_render_fact_cell(row, index)}</td>",
                    f"          <td>{_xml_escape(dimension_summary)}</td>",
                    "        </tr>",
                ]
            )
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<html xmlns="{XHTML_NS}" xmlns:ix="{IX_NS}" xmlns:xbrli="{XBRLI_NS}" xmlns:xbrldi="{XBRLDI_NS}">',
            "  <head>",
            f"    <title>{_xml_escape(title)}</title>",
            "  </head>",
            "  <body>",
            f"    <h1>{_xml_escape(title)}</h1>",
            "    <ix:header>",
            "      <ix:hidden>",
            _render_contexts(example),
            '        <xbrli:unit id="uGBP">',
            '          <xbrli:measure>iso4217:GBP</xbrli:measure>',
            "        </xbrli:unit>",
            "      </ix:hidden>",
            "    </ix:header>",
            '    <table border="1">',
            "      <thead>",
            "        <tr>",
            "          <th>Row</th>",
            "          <th>Tagged value</th>",
            "          <th>Dimensions</th>",
            "        </tr>",
            "      </thead>",
            "      <tbody>",
            *row_markup,
            "      </tbody>",
            "    </table>",
            "  </body>",
            "</html>",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic iXBRL-like statement or note models for validation-rule testing."
    )
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--generated-root", default="backend/validation_rules/generated")
    parser.add_argument("--spec-file", default=None)
    parser.add_argument("--profile", choices=sorted(PROFILE_PRESETS), default=None)
    parser.add_argument("--statement-role", default=None)
    parser.add_argument("--topic-id", default=None)
    parser.add_argument("--concept-qname", default=None)
    parser.add_argument("--end-date", default="2026-12-31")
    parser.add_argument("--output", default=None)
    parser.add_argument("--json-output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.spec_file:
        payload = load_synthetic_spec(
            spec_path=Path(args.spec_file),
            taxonomy_year=args.taxonomy_year,
            entrypoint=args.entrypoint,
        )
    else:
        payload = build_synthetic_model(
            taxonomy_year=args.taxonomy_year,
            entrypoint=args.entrypoint,
            profile=args.profile,
            statement_role=args.statement_role,
            topic_id=args.topic_id,
            concept_qname=args.concept_qname,
            end_date=args.end_date,
            generated_root=Path(args.generated_root),
        )
    html_output = render_ixbrl_html(payload)
    stem = args.profile or args.statement_role or args.topic_id or Path(args.spec_file).stem
    output_path = (
        Path(args.output)
        if args.output
        else generated_output_dir(taxonomy_year=args.taxonomy_year, entrypoint_name=args.entrypoint)
        / "synthetic_examples"
        / f"{stem}.html"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_output, encoding="utf-8")

    if args.json_output:
        json_output_path = Path(args.json_output)
        json_output_path.parent.mkdir(parents=True, exist_ok=True)
        json_output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(
        json.dumps(
            {
                "output": str(output_path),
                "example_kind": payload["example"]["example_kind"],
                "fact_count": len(payload["example"]["facts"]),
                "context_count": len(payload["example"]["contexts"]),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
