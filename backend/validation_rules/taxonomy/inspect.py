from __future__ import annotations

import argparse
import json
from pathlib import Path

from .taxonomy_loader import load_taxonomy_entrypoint, load_taxonomy_entrypoint_from_path, list_taxonomy_entrypoints
from .taxonomy_repository import index_all_entrypoints, index_taxonomy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and index UK taxonomy entrypoints.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--entrypoint-path", default=None)
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--all-entrypoints", action="store_true")
    parser.add_argument("--list-entrypoints", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir) if args.output_dir else None

    if args.list_entrypoints:
        discovered = list_taxonomy_entrypoints(taxonomy_year=args.taxonomy_year, taxonomy_root=args.taxonomy_root)
        payload = {
            key: {"name": spec.name, "path": str(spec.path), "description": spec.description}
            for key, spec in sorted(discovered.items())
        }
        print(json.dumps(payload, indent=2))
        return 0

    if args.all_entrypoints:
        summaries = index_all_entrypoints(
            taxonomy_year=args.taxonomy_year,
            taxonomy_root=args.taxonomy_root,
            output_root=output_dir,
        )
        print(json.dumps({k: v.__dict__ for k, v in sorted(summaries.items())}, indent=2))
        return 0

    if args.entrypoint_path:
        model = load_taxonomy_entrypoint_from_path(
            args.entrypoint_path,
            taxonomy_year=args.taxonomy_year,
            entrypoint_name=args.entrypoint,
        )
    else:
        model = load_taxonomy_entrypoint(
            taxonomy_year=args.taxonomy_year,
            entrypoint_name=args.entrypoint,
            taxonomy_root=args.taxonomy_root,
        )

    summary, _ = index_taxonomy(model, output_dir=output_dir)
    print(json.dumps(summary.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
