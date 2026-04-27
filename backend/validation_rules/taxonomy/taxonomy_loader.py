from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from arelle import Cntlr, ModelManager

from .entrypoints import resolve_entrypoint_path, resolve_entrypoint_spec


@dataclass(frozen=True)
class TaxonomyLoadRequest:
    taxonomy_year: int
    entrypoint_name: str
    taxonomy_root: Path = Path("backend/taxonomies")


@dataclass
class TaxonomyModel:
    taxonomy_year: int
    requested_entrypoint: str
    entrypoint_name: str
    entrypoint_href: str
    entrypoint_path: Path
    taxonomy_root_dir: Path
    model_xbrl: object
    cntlr: object

    def close(self) -> None:
        if self.model_xbrl is not None:
            self.model_xbrl.close()


def load_taxonomy_entrypoint(*, taxonomy_year: int, entrypoint_name: str, taxonomy_root: str | Path = "backend/taxonomies") -> TaxonomyModel:
    root = Path(taxonomy_root)
    spec = resolve_entrypoint_spec(root, taxonomy_year, entrypoint_name)
    entrypoint_path = resolve_entrypoint_path(root, taxonomy_year, entrypoint_name)
    if not entrypoint_path.exists():
        raise FileNotFoundError(
            f"Entrypoint path does not exist: {entrypoint_path}. "
            "Use --entrypoint-path for direct XSD path or place taxonomy under backend/taxonomies/<year>."
        )

    cntlr = Cntlr.Cntlr(logFileName="logToPrint")
    model_manager = ModelManager.initialize(cntlr)
    model_xbrl = model_manager.load(str(entrypoint_path))
    if model_xbrl is None:
        raise RuntimeError(f"Failed to load taxonomy entrypoint: {entrypoint_path}")

    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        requested_entrypoint=entrypoint_name,
        entrypoint_name=spec.name,
        entrypoint_href=spec.href,
        entrypoint_path=entrypoint_path,
        taxonomy_root_dir=root / str(taxonomy_year),
        model_xbrl=model_xbrl,
        cntlr=cntlr,
    )


def load_taxonomy_entrypoint_from_path(entrypoint_path: str | Path, *, taxonomy_year: int = 2026, entrypoint_name: str = "custom") -> TaxonomyModel:
    path = Path(entrypoint_path)
    if not path.exists():
        raise FileNotFoundError(f"Entrypoint path does not exist: {path}")

    cntlr = Cntlr.Cntlr(logFileName="logToPrint")
    model_manager = ModelManager.initialize(cntlr)
    model_xbrl = model_manager.load(str(path.resolve()))
    if model_xbrl is None:
        raise RuntimeError(f"Failed to load taxonomy entrypoint: {path}")

    return TaxonomyModel(
        taxonomy_year=taxonomy_year,
        requested_entrypoint=entrypoint_name,
        entrypoint_name=entrypoint_name,
        entrypoint_href=path.resolve().as_uri(),
        entrypoint_path=path.resolve(),
        taxonomy_root_dir=path.resolve().parents[2],
        model_xbrl=model_xbrl,
        cntlr=cntlr,
    )
