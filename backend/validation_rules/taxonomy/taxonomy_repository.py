from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

from .concept_index import build_concept_index, write_concept_index
from .relationship_index import build_relationship_stats, write_relationship_stats
from .taxonomy_loader import TaxonomyModel


@dataclass(frozen=True)
class TaxonomySummary:
    entrypoint: str
    concept_count: int
    role_count: int
    hypercube_count: int
    dimension_count: int
    domain_member_relationship_count: int


def index_taxonomy(model: TaxonomyModel, *, output_dir: Path | None = None) -> TaxonomySummary:
    concepts = build_concept_index(model.taxonomy_base_dir)
    relationship_stats = build_relationship_stats(model.taxonomy_base_dir)

    summary = TaxonomySummary(
        entrypoint=model.entrypoint_name,
        concept_count=len(concepts),
        role_count=relationship_stats.role_count,
        hypercube_count=relationship_stats.hypercube_count,
        dimension_count=relationship_stats.dimension_count,
        domain_member_relationship_count=relationship_stats.domain_member_relationship_count,
    )

    if output_dir:
        write_concept_index(concepts, output_dir / "concepts.json")
        write_relationship_stats(relationship_stats, output_dir / "roles.json")
        (output_dir / "summary.json").write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")

    return summary
