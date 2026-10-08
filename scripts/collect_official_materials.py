"""Build controlled official corpus; production Agent remains offline/read-only."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.official_materials import OfficialCollector


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--issuer", default="gfbj0839493")
    parser.add_argument("--cutoff", default="2024-04-22")
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--sources", default="cninfo,bse")
    args = parser.parse_args()
    collector = OfficialCollector(args.out)
    candidates = []
    if "cninfo" in args.sources.split(","):
        candidates += collector.cninfo_discover(args.issuer, cutoff=args.cutoff, start=args.start)
    if "bse" in args.sources.split(","):
        candidates += collector.exchange_discover(args.issuer, cutoff=args.cutoff, start=args.start)
    documents, failures = [], []
    for candidate in candidates:
        try:
            documents.append(collector.download(candidate))
            print(json.dumps({"status": "frozen", "title": candidate["title"], "date": candidate["date"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            failures.append({"announcement_id": candidate["announcement_id"], "url": candidate["url"],
                             "reason": str(exc), "status": "unavailable"})
    collector.write_manifest(documents, cutoff=args.cutoff, missing=failures)
    print(json.dumps({"documents": len(documents), "failures": len(failures)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
