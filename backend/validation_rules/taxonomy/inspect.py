from __future__ import annotations

import argparse
import json
from pathlib import Path

from .entrypoints import generated_output_dir
from .taxonomy_loader import load_taxonomy_entrypoint, load_taxonomy_entrypoint_from_path
from .taxonomy_repository import index_taxonomy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect and index UK taxonomy entrypoints.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--entrypoint-path", default=None)
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output-dir", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()

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

    output_dir = Path(args.output_dir) if args.output_dir else generated_output_dir(
        taxonomy_year=args.taxonomy_year,
        entrypoint_name=model.entrypoint_name,
    )

    try:
        summary = index_taxonomy(model, output_dir=output_dir)
    finally:
        model.close()

    print(json.dumps(summary.__dict__, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
