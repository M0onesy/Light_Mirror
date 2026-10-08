from __future__ import annotations

import argparse
import json
import sys

from mingjing.mcp import MingJingTools


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--corpus", required=True); args = parser.parse_args(); tools = MingJingTools(args.corpus)
    for line in sys.stdin:
        request = json.loads(line); method = request.get("method", "get_facts"); result = tools.get_facts() if method == "get_fact" else tools.get_facts(); print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

