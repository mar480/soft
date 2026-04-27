from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

from .concept_index import build_concept_index, write_concept_index
from .entrypoints import generated_output_dir
from .relationship_index import (
    build_relationship_index,
    collect_presentation_primary_item_membership,
    write_relationship_stats,
)
from .taxonomy_loader import TaxonomyModel


@dataclass(frozen=True)
class TaxonomySummary:
    entrypoint: str
    concept_count: int
    role_count: int
    hypercube_count: int
    dimension_count: int
    domain_member_relationship_count: int

def write_presentation_primary_item_membership(payload: dict, output_file: Path) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def index_taxonomy(model: TaxonomyModel, *, output_dir: Path | None = None) -> TaxonomySummary:
    concepts = build_concept_index(model.model_xbrl)
    relationship_stats = build_relationship_index(model.model_xbrl)
    presentation_membership = collect_presentation_primary_item_membership(model.model_xbrl)

    summary = TaxonomySummary(
        entrypoint=model.entrypoint_name,
        concept_count=len(concepts),
        role_count=relationship_stats.role_count,
        hypercube_count=relationship_stats.hypercube_count,
        dimension_count=relationship_stats.dimension_count,
        domain_member_relationship_count=relationship_stats.domain_member_relationship_count,
    )

    target_output_dir = output_dir or generated_output_dir(
        taxonomy_year=model.taxonomy_year,
        entrypoint_name=model.entrypoint_name,
    )

    target_output_dir.mkdir(parents=True, exist_ok=True)

    if output_dir:
        target_output_dir = output_dir

    write_concept_index(concepts, target_output_dir / "concepts.json")
    write_presentation_primary_item_membership(
        {
            "entrypoint": model.entrypoint_name,
            "primary_item_count": presentation_membership["primary_item_count"],
            "primary_items": presentation_membership["primary_items"],
            "roles": presentation_membership["roles"],
        },
        target_output_dir / "presentation_primary_items.json",
    )
    write_relationship_stats(relationship_stats, target_output_dir / "roles.json")
    (target_output_dir / "summary.json").write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")

    return summary
