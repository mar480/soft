from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EntrypointSpec:
    name: str
    relative_path: str


ENTRYPOINTS_2026: dict[str, EntrypointSpec] = {
    "FRS-102": EntrypointSpec("FRS-102", "frs-102/frs-102-entry-point.xsd"),
    "IFRS": EntrypointSpec("IFRS", "ifrs/ifrs-entry-point.xsd"),
    "FRS-102-UKSEF": EntrypointSpec("FRS-102-UKSEF", "frs-102-uksef/frs-102-uksef-entry-point.xsd"),
    "IFRS-UKSEF": EntrypointSpec("IFRS-UKSEF", "ifrs-uksef/ifrs-uksef-entry-point.xsd"),
    "CORE": EntrypointSpec("CORE", "core/core-entry-point.xsd"),
    "CORE-FULL": EntrypointSpec("CORE-FULL", "core-full/core-full-entry-point.xsd"),
    "DPL": EntrypointSpec("DPL", "dpl/dpl-entry-point.xsd"),
    "CIC": EntrypointSpec("CIC", "cic/cic-entry-point.xsd"),
    "DSEP": EntrypointSpec("DSEP", "dsep/dsep-entry-point.xsd"),
}


FRIENDLY_ALIASES = {
    "FRS102": "FRS-102",
    "FRS_102": "FRS-102",
    "CORE FULL": "CORE-FULL",
    "COREFULL": "CORE-FULL",
}


def canonicalise_entrypoint_name(name: str) -> str:
    normalized = name.strip().upper().replace("_", "-")
    return FRIENDLY_ALIASES.get(normalized, normalized)


def resolve_entrypoint_path(taxonomy_root: Path, taxonomy_year: int, entrypoint_name: str) -> Path:
    if taxonomy_year != 2026:
        raise ValueError(f"Unsupported taxonomy year: {taxonomy_year}")

    canonical_name = canonicalise_entrypoint_name(entrypoint_name)
    if canonical_name not in ENTRYPOINTS_2026:
        options = ", ".join(sorted(ENTRYPOINTS_2026))
        raise ValueError(f"Unknown entrypoint: {entrypoint_name}. Available: {options}")

    candidate = taxonomy_root / str(taxonomy_year) / ENTRYPOINTS_2026[canonical_name].relative_path
    return candidate
