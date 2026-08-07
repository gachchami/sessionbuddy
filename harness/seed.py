from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.factories import build_seed_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic Sessionbuddy seed data")
    parser.add_argument("--seed", type=int, default=20260808)
    parser.add_argument("--submissions", type=int, default=12)
    parser.add_argument("--output", type=Path, default=Path(".local/seed.json"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    payload = build_seed_bundle(seed=args.seed, submission_count=args.submissions).as_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(payload['submissions'])} submissions to {args.output}")


if __name__ == "__main__":
    main()

