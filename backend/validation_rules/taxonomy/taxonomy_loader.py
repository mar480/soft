from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .entrypoints import resolve_entrypoint_path


@dataclass(frozen=True)
class TaxonomyLoadRequest:
    taxonomy_year: int
    entrypoint_name: str
    taxonomy_root: Path = Path("backend/taxonomies")


@dataclass(frozen=True)
class TaxonomyModel:
    taxonomy_year: int
    entrypoint_name: str
    entrypoint_path: Path
    taxonomy_base_dir: Path


def load_taxonomy_entrypoint(*, taxonomy_year: int, entrypoint_name: str, taxonomy_root: str | Path = "backend/taxonomies") -> TaxonomyModel:
    root = Path(taxonomy_root)
    entrypoint_path = resolve_entrypoint_path(root, taxonomy_year, entrypoint_name)
    if not entrypoint_path.exists():
        raise FileNotFoundError(
            f"Entrypoint path does not exist: {entrypoint_path}. "
            "Use --entrypoint-path for direct XSD path or place taxonomy under backend/taxonomies/<year>."
        )

    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint_name,
        entrypoint_path=entrypoint_path,
        taxonomy_base_dir=entrypoint_path.parent,
    )


def load_taxonomy_entrypoint_from_path(entrypoint_path: str | Path, *, taxonomy_year: int = 2026, entrypoint_name: str = "custom") -> TaxonomyModel:
    path = Path(entrypoint_path)
    if not path.exists():
        raise FileNotFoundError(f"Entrypoint path does not exist: {path}")

    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint_name,
        entrypoint_path=path,
        taxonomy_base_dir=path.parent,
    )
