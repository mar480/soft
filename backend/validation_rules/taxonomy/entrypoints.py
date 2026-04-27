from __future__ import annotations

import argparse
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

TAXONOMY_PACKAGE_NS = {"tp": "http://xbrl.org/2016/taxonomy-package"}
XML_CATALOG_NS = {"cat": "urn:oasis:names:tc:entity:xmlns:xml:catalog"}


@dataclass(frozen=True)
class EntrypointSpec:
    name: str
    canonical_key: str
    href: str
    local_path: Path


def canonicalise_entrypoint_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.strip().lower())


def entrypoint_output_slug(entrypoint_name: str) -> str:
    return canonicalise_entrypoint_name(entrypoint_name)


def _taxonomy_year_root(taxonomy_root: Path, taxonomy_year: int) -> Path:
    return taxonomy_root / str(taxonomy_year)


def _catalog_rewrites(taxonomy_root: Path, taxonomy_year: int) -> list[tuple[str, Path]]:
    year_root = _taxonomy_year_root(taxonomy_root, taxonomy_year)
    catalog_path = year_root / "META-INF" / "catalog.xml"
    root = ET.parse(catalog_path).getroot()

    rewrites: list[tuple[str, Path]] = []
    for rewrite in root.findall("cat:rewriteURI", XML_CATALOG_NS):
        uri_prefix = rewrite.attrib["uriStartString"]
        local_prefix = (catalog_path.parent / rewrite.attrib["rewritePrefix"]).resolve()
        rewrites.append((uri_prefix, local_prefix))

    rewrites.sort(key=lambda item: len(item[0]), reverse=True)
    return rewrites


def resolve_catalog_uri(taxonomy_root: Path, taxonomy_year: int, href: str) -> Path:
    for uri_prefix, local_prefix in _catalog_rewrites(taxonomy_root, taxonomy_year):
        if href.startswith(uri_prefix):
            return (local_prefix / Path(*href.removeprefix(uri_prefix).split("/"))).resolve()
    raise ValueError(f"Could not resolve taxonomy package href to a local path: {href}")


def load_entrypoint_specs(taxonomy_root: Path, taxonomy_year: int) -> dict[str, EntrypointSpec]:
    package_path = _taxonomy_year_root(taxonomy_root, taxonomy_year) / "META-INF" / "taxonomyPackage.xml"
    root = ET.parse(package_path).getroot()

    specs: dict[str, EntrypointSpec] = {}
    for entrypoint in root.findall("tp:entryPoints/tp:entryPoint", TAXONOMY_PACKAGE_NS):
        name = entrypoint.findtext("tp:name", namespaces=TAXONOMY_PACKAGE_NS)
        document = entrypoint.find("tp:entryPointDocument", TAXONOMY_PACKAGE_NS)
        if not name or document is None:
            continue

        href = document.attrib["href"]
        spec = EntrypointSpec(
            name=name,
            canonical_key=canonicalise_entrypoint_name(name),
            href=href,
            local_path=resolve_catalog_uri(taxonomy_root, taxonomy_year, href),
        )
        specs[spec.canonical_key] = spec

    return specs


def list_entrypoints(taxonomy_root: Path, taxonomy_year: int) -> list[EntrypointSpec]:
    return sorted(load_entrypoint_specs(taxonomy_root, taxonomy_year).values(), key=lambda spec: spec.name)


def resolve_entrypoint_path(taxonomy_root: Path, taxonomy_year: int, entrypoint_name_or_href: str) -> Path:
    candidate_path = Path(entrypoint_name_or_href)
    if candidate_path.exists():
        return candidate_path.resolve()

    if "://" in entrypoint_name_or_href:
        return resolve_catalog_uri(taxonomy_root, taxonomy_year, entrypoint_name_or_href)

    specs = load_entrypoint_specs(taxonomy_root, taxonomy_year)
    canonical_name = canonicalise_entrypoint_name(entrypoint_name_or_href)
    if canonical_name not in specs:
        options = ", ".join(sorted(spec.name for spec in specs.values()))
        raise ValueError(f"Unknown entrypoint: {entrypoint_name_or_href}. Available: {options}")

    return specs[canonical_name].local_path


def resolve_entrypoint_spec(taxonomy_root: Path, taxonomy_year: int, entrypoint_name_or_href: str) -> EntrypointSpec:
    specs = load_entrypoint_specs(taxonomy_root, taxonomy_year)

    if "://" in entrypoint_name_or_href:
        local_path = resolve_catalog_uri(taxonomy_root, taxonomy_year, entrypoint_name_or_href)
        for spec in specs.values():
            if spec.local_path == local_path:
                return spec
        return EntrypointSpec(
            name=entrypoint_name_or_href,
            canonical_key=canonicalise_entrypoint_name(entrypoint_name_or_href),
            href=entrypoint_name_or_href,
            local_path=local_path,
        )

    candidate_path = Path(entrypoint_name_or_href)
    if candidate_path.exists():
        resolved = candidate_path.resolve()
        for spec in specs.values():
            if spec.local_path == resolved:
                return spec
        return EntrypointSpec(
            name=resolved.stem,
            canonical_key=canonicalise_entrypoint_name(resolved.stem),
            href=resolved.as_uri(),
            local_path=resolved,
        )

    canonical_name = canonicalise_entrypoint_name(entrypoint_name_or_href)
    if canonical_name not in specs:
        options = ", ".join(sorted(spec.name for spec in specs.values()))
        raise ValueError(f"Unknown entrypoint: {entrypoint_name_or_href}. Available: {options}")
    return specs[canonical_name]


def generated_output_dir(
    *,
    taxonomy_year: int,
    entrypoint_name: str,
    generated_root: str | Path = "backend/validation_rules/generated",
) -> Path:
    return Path(generated_root) / str(taxonomy_year) / entrypoint_output_slug(entrypoint_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List and validate taxonomy package entrypoints.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--entrypoint", default=None, help="Optional entrypoint name, URL, or path to validate.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    taxonomy_root = Path(args.taxonomy_root)

    if args.entrypoint:
        spec = resolve_entrypoint_spec(taxonomy_root, args.taxonomy_year, args.entrypoint)
        payload = {
            "taxonomy_year": args.taxonomy_year,
            "entrypoint": spec.name,
            "canonical_key": spec.canonical_key,
            "href": spec.href,
            "local_path": str(spec.local_path),
            "exists": spec.local_path.exists(),
            "output_slug": entrypoint_output_slug(spec.name),
        }
    else:
        payload = {
            "taxonomy_year": args.taxonomy_year,
            "entrypoint_count": len(list_entrypoints(taxonomy_root, args.taxonomy_year)),
            "entrypoints": [
                {
                    **asdict(spec),
                    "local_path": str(spec.local_path),
                    "exists": spec.local_path.exists(),
                    "output_slug": entrypoint_output_slug(spec.name),
                }
                for spec in list_entrypoints(taxonomy_root, args.taxonomy_year)
            ],
        }

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
