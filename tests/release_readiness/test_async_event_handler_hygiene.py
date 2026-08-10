"""Ban `event.currentTarget` after `await` in async DOM handlers.

`event.currentTarget` is only set while the event is being dispatched; after
the first `await` it is null. Touching it then throws — which converted
SUCCESSFUL writes (bulk message send, task assignment) into red failure
banners in the eval run. Handlers must capture the element in a local
before awaiting.
"""

import re
from pathlib import Path

STATIC = Path(__file__).parents[2] / "src" / "sessionbuddy" / "static"

HANDLER = re.compile(r'addEventListener\(\s*"\w+"\s*,\s*async\s*\(event\)\s*=>\s*\{')


def _handler_bodies(source: str):
    for match in HANDLER.finditer(source):
        start = match.end()
        depth, index = 1, start
        while depth and index < len(source):
            if source[index] == "{":
                depth += 1
            elif source[index] == "}":
                depth -= 1
            index += 1
        yield start, source[start:index]


def test_async_handlers_never_touch_current_target_after_await() -> None:
    offenders: list[str] = []
    for path in sorted(STATIC.glob("*.js")):
        source = path.read_text()
        for start, body in _handler_bodies(source):
            first_await = body.find("await ")
            if first_await == -1:
                continue
            for use in re.finditer(r"event\.currentTarget", body):
                if use.start() > first_await:
                    line = source[: start + use.start()].count("\n") + 1
                    offenders.append(f"{path.name}:{line}")
    assert offenders == [], (
        "event.currentTarget used after await (it is null there; capture the "
        f"element before awaiting): {offenders}"
    )
