from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json

from .concept_index import build_concept_index, write_concept_index
from .entrypoints import discover_entrypoints
from .relationship_index import build_relationship_stats, write_relationship_stats
from .taxonomy_graph import discover_reachable_files
from .taxonomy_loader import TaxonomyModel, load_taxonomy_entrypoint


@dataclass(frozen=True)
class TaxonomySummary:
    entrypoint: str
    concept_count: int
    role_count: int
    hypercube_count: int
    dimension_count: int
    domain_member_relationship_count: int


def index_taxonomy(model: TaxonomyModel, *, output_dir: Path | None = None) -> tuple[TaxonomySummary, set[str]]:
    reachable_files = discover_reachable_files(model.entrypoint_path)
    concepts = build_concept_index(reachable_files)
    relationship_stats = build_relationship_stats(reachable_files)

    unique_concepts = {c.concept_qname for c in concepts}
    summary = TaxonomySummary(
        entrypoint=model.entrypoint_name,
        concept_count=len(unique_concepts),
        role_count=relationship_stats.role_count,
        hypercube_count=relationship_stats.hypercube_count,
        dimension_count=relationship_stats.dimension_count,
        domain_member_relationship_count=relationship_stats.domain_member_relationship_count,
    )

    if output_dir:
        write_concept_index(concepts, output_dir / "concepts.json")
        write_relationship_stats(relationship_stats, output_dir / "roles.json")
        (output_dir / "summary.json").write_text(json.dumps(asdict(summary), indent=2), encoding="utf-8")
        (output_dir / "unique_concepts.json").write_text(json.dumps(sorted(unique_concepts), indent=2), encoding="utf-8")

    return summary, unique_concepts


def index_all_entrypoints(*, taxonomy_year: int, taxonomy_root: str | Path = "backend/taxonomies", output_root: Path | None = None) -> dict[str, TaxonomySummary]:
    root = Path(taxonomy_root)
    discovered = discover_entrypoints(root, taxonomy_year)
    summaries: dict[str, TaxonomySummary] = {}
    concept_sets: dict[str, set[str]] = {}

    for key, spec in sorted(discovered.items()):
        model = load_taxonomy_entrypoint(taxonomy_year=taxonomy_year, entrypoint_name=spec.name, taxonomy_root=root)
        entrypoint_output = output_root / key.lower() if output_root else None
        summary, concepts = index_taxonomy(model, output_dir=entrypoint_output)
        summaries[key] = summary
        concept_sets[key] = concepts

    if output_root:
        output_root.mkdir(parents=True, exist_ok=True)
        dedupe_payload = {
            "taxonomy_year": taxonomy_year,
            "entrypoints": {k: sorted(v) for k, v in sorted(concept_sets.items())},
        }
        (output_root / "unique_concepts_by_entrypoint.json").write_text(json.dumps(dedupe_payload, indent=2), encoding="utf-8")

    return summaries
