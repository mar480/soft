from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .entrypoints import EntrypointSpec, discover_entrypoints, resolve_entrypoint_path


@dataclass(frozen=True)
class TaxonomyModel:
    taxonomy_year: int
    entrypoint_name: str
    entrypoint_path: Path
    taxonomy_root: Path


def load_taxonomy_entrypoint(*, taxonomy_year: int, entrypoint_name: str, taxonomy_root: str | Path = "backend/taxonomies") -> TaxonomyModel:
    root = Path(taxonomy_root)
    spec = resolve_entrypoint_path(root, taxonomy_year, entrypoint_name)
    if not spec.path.exists():
        raise FileNotFoundError(f"Entrypoint does not exist on disk: {spec.path}")

    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        entrypoint_name=spec.name,
        entrypoint_path=spec.path,
        taxonomy_root=root / str(taxonomy_year),
    )


def load_taxonomy_entrypoint_from_path(entrypoint_path: str | Path, *, taxonomy_year: int = 2026, entrypoint_name: str = "custom") -> TaxonomyModel:
    path = Path(entrypoint_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Entrypoint path does not exist: {path}")

    taxonomy_root = path.parents[2] if len(path.parents) >= 3 else path.parent
    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        entrypoint_name=entrypoint_name,
        entrypoint_path=path,
        taxonomy_root=taxonomy_root,
    )


def list_taxonomy_entrypoints(*, taxonomy_year: int, taxonomy_root: str | Path = "backend/taxonomies") -> dict[str, EntrypointSpec]:
    return discover_entrypoints(Path(taxonomy_root), taxonomy_year)
