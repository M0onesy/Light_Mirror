from __future__ import annotations

import argparse
import json
import sys

from mingjing.mcp import MingJingTools


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--corpus", required=True); args = parser.parse_args(); tools = MingJingTools(args.corpus)
    for line in sys.stdin:
        request = json.loads(line); params = request.get("params", {}); result = tools.lookup_rule(params.get("keyword", "")); print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

