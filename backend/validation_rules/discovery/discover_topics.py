from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..taxonomy.entrypoints import generated_output_dir
from ..taxonomy.taxonomy_loader import load_taxonomy_entrypoint
from .cube_discoverer import discover_topics


def build_cube_breakdown_payload(*, taxonomy_year: int, entrypoint: str, topics: list) -> dict:
    family_rows: list[dict] = []
    cube_rows: list[dict] = []

    for topic in topics:
        occurrence_counts: dict[str, int] = {}
        for cube in topic.hypercubes:
            occurrence_counts[cube.occurrence_type] = occurrence_counts.get(cube.occurrence_type, 0) + 1
            cube_rows.append(
                {
                    "topic_id": topic.topic_id,
                    "topic_label": topic.topic_label,
                    "priority": topic.priority,
                    "topic_kind": topic.topic_kind,
                    "cube_qname": cube.cube_qname,
                    "cube_label": cube.cube_label,
                    "elr": cube.elr,
                    "elr_definition": cube.elr_definition,
                    "occurrence_type": cube.occurrence_type,
                    "variant_label": cube.variant_label,
                    "variant_dimension_validation": cube.variant_validation["matches_dimension_content"],
                    "variant_evidence_dimensions": cube.variant_validation["evidence_dimensions"],
                    "dimension_count": len(cube.dimensions),
                    "primary_item_count": len(cube.primary_items),
                    "dimensions": [dimension.dimension_qname for dimension in cube.dimensions],
                }
            )

        family_rows.append(
            {
                "topic_id": topic.topic_id,
                "topic_label": topic.topic_label,
                "priority": topic.priority,
                "topic_kind": topic.topic_kind,
                "hypercube_count": len(topic.hypercubes),
                "occurrence_counts": occurrence_counts,
                "source_families": sorted(
                    {
                        cube.source_family_topic_id: cube.source_family_topic_label
                        for cube in topic.hypercubes
                    }.items()
                ),
                "cube_elr_definitions": [cube.elr_definition for cube in topic.hypercubes],
            }
        )

    return {
        "taxonomy_year": taxonomy_year,
        "entrypoint": entrypoint,
        "family_count": len(family_rows),
        "cube_count": len(cube_rows),
        "families": family_rows,
        "cubes": cube_rows,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover taxonomy topics from hypercubes and presentation context.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = load_taxonomy_entrypoint(
        taxonomy_year=args.taxonomy_year,
        entrypoint_name=args.entrypoint,
        taxonomy_root=args.taxonomy_root,
    )
    try:
        topics = discover_topics(
            model_xbrl=model.model_xbrl,
            taxonomy_year=args.taxonomy_year,
            entrypoint=model.entrypoint_name,
        )
    finally:
        model.close()

    output_path = Path(args.output) if args.output else generated_output_dir(
        taxonomy_year=args.taxonomy_year,
        entrypoint_name=args.entrypoint,
    ) / "topics.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "taxonomy_year": args.taxonomy_year,
        "entrypoint": args.entrypoint,
        "topic_count": len(topics),
        "topics": [topic.to_dict() for topic in topics],
    }
    breakdown_payload = build_cube_breakdown_payload(
        taxonomy_year=args.taxonomy_year,
        entrypoint=args.entrypoint,
        topics=topics,
    )
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    breakdown_path = output_path.parent / "cube_breakdown.json"
    breakdown_path.write_text(json.dumps(breakdown_payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "cube_breakdown": str(breakdown_path), "topic_count": len(topics)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
