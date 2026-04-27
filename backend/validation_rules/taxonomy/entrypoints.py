from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import xml.etree.ElementTree as ET


@dataclass(frozen=True)
class EntrypointSpec:
    name: str
    path: Path
    description: str | None = None


def _local_name(tag: str) -> str:
    return tag.split("}")[-1]


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-")
    return cleaned.upper() or "UNKNOWN"


def discover_entrypoints(taxonomy_root: Path, taxonomy_year: int) -> dict[str, EntrypointSpec]:
    package_dir = taxonomy_root / str(taxonomy_year) / "META-INF" / "taxonomyPackage"
    if not package_dir.exists():
        raise FileNotFoundError(f"Taxonomy package directory does not exist: {package_dir}")

    xml_files = sorted(package_dir.rglob("*.xml"))
    entrypoints: dict[str, EntrypointSpec] = {}

    for xml_file in xml_files:
        try:
            tree = ET.parse(xml_file)
        except ET.ParseError:
            continue

        root = tree.getroot()
        for candidate in root.iter():
            if _local_name(candidate.tag) not in {"entryPoint", "entrypoint"}:
                continue

            ep_name: str | None = None
            ep_description: str | None = None
            ep_href: str | None = None

            for child in list(candidate):
                name = _local_name(child.tag)
                text = (child.text or "").strip()
                if not text:
                    continue

                if name in {"name", "entryPointName", "id"} and ep_name is None:
                    ep_name = text
                elif name in {"description", "documentation"} and ep_description is None:
                    ep_description = text
                elif name in {"entryPointDocument", "entrypointDocument", "url", "href", "document"} and ep_href is None:
                    ep_href = text

            if ep_href is None:
                href_attr = candidate.attrib.get("href") or candidate.attrib.get("{http://www.w3.org/1999/xlink}href")
                if href_attr:
                    ep_href = href_attr

            if not ep_href:
                continue

            resolved_path = (xml_file.parent / ep_href).resolve()
            if not resolved_path.exists():
                resolved_path = (taxonomy_root / str(taxonomy_year) / ep_href).resolve()

            if ep_name is None:
                ep_name = Path(ep_href).stem

            canonical = _safe_name(ep_name)
            entrypoints[canonical] = EntrypointSpec(name=ep_name, path=resolved_path, description=ep_description)

    if not entrypoints:
        raise RuntimeError(f"No taxonomy entrypoints discovered under {package_dir}")

    return entrypoints


def resolve_entrypoint_path(taxonomy_root: Path, taxonomy_year: int, entrypoint_name: str) -> EntrypointSpec:
    discovered = discover_entrypoints(taxonomy_root, taxonomy_year)
    key = _safe_name(entrypoint_name)

    if key not in discovered:
        options = ", ".join(sorted(discovered))
        raise ValueError(f"Unknown entrypoint '{entrypoint_name}'. Available keys: {options}")

    return discovered[key]
