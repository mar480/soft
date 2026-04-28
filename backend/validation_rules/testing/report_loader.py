from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report_loader_core import load_report_model


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract contexts and facts from synthetic or real inline XBRL HTML files."
    )
    parser.add_argument("input", help="Path to an XHTML / iXBRL file.")
    parser.add_argument("--output", default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    payload = load_report_model(Path(args.input)).to_dict()
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
