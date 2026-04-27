from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..taxonomy.entrypoints import generated_output_dir
from ..taxonomy.taxonomy_loader import load_taxonomy_entrypoint
from .cube_discoverer import discover_topics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover taxonomy topics from hypercubes and presentation context.")
    parser.add_argument("--taxonomy-year", type=int, default=2026)
    parser.add_argument("--entrypoint", default="FRS-102")
    parser.add_argument("--taxonomy-root", default="backend/taxonomies")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    model = load_taxonomy_entrypoint(
        taxonomy_year=args.taxonomy_year,
        entrypoint_name=args.entrypoint,
        taxonomy_root=args.taxonomy_root,
    )
    try:
        topics = discover_topics(
            model_xbrl=model.model_xbrl,
            taxonomy_year=args.taxonomy_year,
            entrypoint=model.entrypoint_name,
        )
    finally:
        model.close()

    output_path = Path(args.output) if args.output else generated_output_dir(
        taxonomy_year=args.taxonomy_year,
        entrypoint_name=args.entrypoint,
    ) / "topics.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "taxonomy_year": args.taxonomy_year,
        "entrypoint": args.entrypoint,
        "topic_count": len(topics),
        "topics": [topic.to_dict() for topic in topics],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output_path), "topic_count": len(topics)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
