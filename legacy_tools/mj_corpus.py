from __future__ import annotations

import argparse
import json

from mingjing.mcp import MingJingTools


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True)
    args = parser.parse_args()
    tools = MingJingTools(args.corpus)
    for line in __import__("sys").stdin:
        request = json.loads(line)
        method = request.get("method")
        params = request.get("params", {})
        result = getattr(tools, method)(**params)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

