from __future__ import annotations

"""Dependency-free JSON tool facade used by the optional MCP adapter."""

import json
from pathlib import Path

from .facts import FactStore
from .parser import build_evidence, extract_text


class MingJingTools:
    def __init__(self, corpus: str | Path):
        self.corpus = Path(corpus)

    def list_documents(self) -> list[dict]:
        paths = [p for p in self.corpus.rglob("*") if p.is_file() and p.suffix.lower() in {".pdf", ".md", ".txt"}]
        return [{"path": str(p), "name": p.name, "suffix": p.suffix.lower()} for p in paths]

    def search_text(self, query: str, limit: int = 20) -> list[dict]:
        hits = []
        for doc in self.list_documents():
            for page in extract_text(doc["path"]):
                if query.lower() in page["text"].lower():
                    hits.append({**doc, "page": page["page"], "snippet": page["text"][:500]})
                    if len(hits) >= limit:
                        return hits
        return hits

    def get_facts(self) -> list[dict]:
        return FactStore.from_corpus(self.corpus).to_list()

    def lookup_rule(self, keyword: str) -> list[dict]:
        kb = Path(__file__).resolve().parents[2] / "存放会计准则" / "cas_kb.json"
        if not kb.exists():
            return []
        data = json.loads(kb.read_text(encoding="utf-8"))
        return [item for item in data if keyword in str(item.get("t", "")) or keyword in str(item.get("d", ""))][:20]

