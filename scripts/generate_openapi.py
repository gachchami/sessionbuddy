import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from sessionbuddy.api.app import app  # noqa: E402


def main() -> None:
    destination = PROJECT_ROOT / "openapi" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = app.openapi()
    destination.write_text(
        json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
