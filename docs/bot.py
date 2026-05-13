from __future__ import annotations

import json
import sys

from src.shared import bot_choose_action


def main() -> int:
    state: dict = {}
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("STATE "):
            payload = line[6:].strip()
            try:
                state = json.loads(payload)
            except json.JSONDecodeError:
                state = {}
            continue

        if line in {"GO", "BESTMOVE"}:
            command = bot_choose_action(state)
            print(command, flush=True)
            continue

        if line == "QUIT":
            return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
