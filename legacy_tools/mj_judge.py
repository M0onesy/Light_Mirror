from __future__ import annotations

import argparse
import json
import sys

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.models import AtomicClaim


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--corpus", required=True); args = parser.parse_args(); judge = Judge(FactStore.from_corpus(args.corpus))
    for line in sys.stdin:
        payload = json.loads(line); result = []
        for item in payload.get("claims", []):
            claim = AtomicClaim(**item); result.append(judge.verify(claim).to_dict())
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()

