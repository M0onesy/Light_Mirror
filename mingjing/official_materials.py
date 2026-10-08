"""Controlled, offline-build-only collection of original disclosure materials.

This module is deliberately outside the deterministic kernel and Agent tools.
Publication dates come from an archived official record, never download time.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


OFFICIAL_HOSTS = frozenset({"www.cninfo.com.cn", "static.cninfo.com.cn", "www.bse.cn",
                           "www.neeq.com.cn", "www.neeq.cc", "static.neeq.com.cn"})
IDENTITIES = {
    "gfbj0839493": {"name": "并行科技", "legal_name": "北京并行科技股份有限公司",
                     "codes": ["839493", "920493"]},
}
_MATERIAL_TYPES = (
    ("招股书", r"招股说明书|公开转让说明书"),
    ("问询回复", r"问询.*回复|回复.*问询|反馈.*回复|回复.*反馈"),
    ("更正公告", r"更正|差错|重述|会计政策变更"),
    ("审计报告", r"审计报告|财务报表|财务报告"),
    ("半年报", r"半年度报告"),
    ("年报", r"年度报告"),
)


def material_type(title: str) -> str | None:
    clean = html.unescape(re.sub(r"<[^>]+>", "", title))
    if re.search(r"摘要|英文版|取消|预约|提示性", clean):
        return None
    return next((kind for kind, pattern in _MATERIAL_TYPES if re.search(pattern, clean)), None)


def official_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in OFFICIAL_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise ValueError("material URL must use an approved official HTTPS host")
    if any(part in {".", ".."} for part in urllib.parse.unquote(parsed.path).split("/")):
        raise ValueError("material URL contains a traversal segment")
    return url


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class OfficialCollector:
    def __init__(self, root: str | Path, *, timeout_s: int = 30):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout_s = timeout_s
        self.opener = urllib.request.build_opener(_OfficialRedirect())
        self.cookies: dict[str, str] = {}

    def request(self, url: str, params=None) -> bytes:
        official_url(url)
        headers = {"User-Agent": "Mozilla/5.0", "Referer": url,
                   "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                   "X-Requested-With": "XMLHttpRequest"}
        host = urllib.parse.urlsplit(url).hostname
        if host in self.cookies:
            headers["Cookie"] = self.cookies[host]
        body = urllib.parse.urlencode(params, doseq=True).encode("utf-8") if params is not None else None
        with self.opener.open(urllib.request.Request(url, body, headers), timeout=self.timeout_s) as response:
            payload = response.read(100 * 1024 * 1024 + 1)
        if len(payload) > 100 * 1024 * 1024:
            raise ValueError("official response exceeds collection size limit")
        # BSE's public browser bootstrap sets this cookie in a short script.
        # Parse only the literal cookie, never execute downloaded JavaScript.
        if host in {"www.bse.cn", "www.neeq.com.cn"}:
            match = re.search(rb'C3VK=([A-Za-z0-9]+);', payload[:2048])
            if match and self.cookies.get(host) != "C3VK=" + match[1].decode():
                self.cookies[host] = "C3VK=" + match[1].decode()
                headers["Cookie"] = self.cookies[host]
                with self.opener.open(urllib.request.Request(url, body, headers), timeout=self.timeout_s) as response:
                    payload = response.read(100 * 1024 * 1024 + 1)
        return payload

    def freeze_bytes(self, relative: str, payload: bytes) -> dict:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root) or any(p.is_symlink() for p in [path, *path.parents] if p.is_relative_to(self.root)):
            raise ValueError("collection path escapes controlled root")
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(payload).hexdigest()
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("refusing to overwrite a frozen official material")
        else:
            path.write_bytes(payload)
        return {"file": relative.replace("\\", "/"), "sha256": digest, "size_bytes": len(payload)}

    def cninfo_discover(self, org_id: str, *, cutoff: str, start: str = "2016-01-01") -> list[dict]:
        identity = IDENTITIES[org_id]
        date.fromisoformat(start)
        date.fromisoformat(cutoff)
        url = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
        selected, seen = [], set()
        for page in range(1, 101):
            params = {"pageNum": page, "pageSize": 30, "column": "bj", "tabName": "fulltext",
                      "stock": f"{identity['codes'][-1]},{org_id}", "searchkey": "", "secid": "", "plate": "bj",
                      "category": "", "trade": "", "seDate": f"{start}~{cutoff}", "sortName": "",
                      "sortType": "", "isHLtitle": "true"}
            payload = self.request(url, params)
            receipt = self.freeze_bytes(f"records/cninfo-{org_id}-{start}-{cutoff}-p{page}.json", payload)
            response = json.loads(payload)
            anns = response.get("announcements") or []
            new = 0
            for record in anns:
                key = record.get("announcementId")
                if not key or key in seen:
                    continue
                seen.add(key)
                new += 1
                if record.get("orgId") != org_id or record.get("secCode") not in identity["codes"]:
                    raise ValueError("official announcement issuer identity mismatch")
                kind = material_type(record.get("announcementTitle", ""))
                if not kind:
                    continue
                item = self.cninfo_record(record, cutoff=cutoff)
                item.update(category=kind, name=identity["name"], orgId=org_id,
                            metadata_url=url, metadata_record=receipt, metadata_query=params)
                selected.append(item)
            # totalpages and totalAnnouncement are advisory; terminate on an
            # empty page or repeated page, not unreliable totals.
            if not anns or not new:
                break
        else:
            raise RuntimeError("official collection pagination budget exhausted")
        return selected

    @staticmethod
    def cninfo_record(record: dict, *, cutoff: str) -> dict:
        timestamp = record.get("announcementTime")
        if type(timestamp) not in (int, float) or timestamp <= 0:
            raise ValueError("official disclosure date is missing")
        disclosed = datetime.fromtimestamp(timestamp / 1000, timezone(timedelta(hours=8))).date().isoformat()
        if disclosed > cutoff:
            raise ValueError("official disclosure is after the retrospective cutoff")
        adjunct = record.get("adjunctUrl", "")
        if not re.fullmatch(r"finalpage/\d{4}-\d{2}-\d{2}/\d+\.[pP][dD][fF]", adjunct):
            raise ValueError("invalid official PDF adjunct")
        if record.get("announcementId") != Path(adjunct).stem:
            raise ValueError("official document ID does not match adjunct")
        return {"code": record.get("secCode", ""), "date": disclosed,
                "title": html.unescape(re.sub(r"<[^>]+>", "", record.get("announcementTitle", ""))),
                "announcement_id": record["announcementId"],
                "url": official_url("https://static.cninfo.com.cn/" + adjunct)}

    def exchange_discover(self, org_id: str, *, cutoff: str, start: str = "2016-01-01",
                          exchange: str = "bse", disclosure_type: str = "9530") -> list[dict]:
        identity = IDENTITIES[org_id]
        base = {"bse": "https://www.bse.cn", "neeq": "https://www.neeq.com.cn"}[exchange]
        url = base + "/disclosureInfoController/zoneInfoResult.do"
        selected, seen = [], set()
        for page in range(100):
            params = {"disclosureType": disclosure_type, "companyCd": identity["codes"][0],
                      "page": str(page), "startTime": start, "endTime": cutoff,
                      "keyword": "", "isLink": "0"}
            payload = self.request(url, params)
            receipt = self.freeze_bytes(f"records/{exchange}-{disclosure_type}-{org_id}-{start}-{cutoff}-p{page}.jsonp", payload)
            decoded = payload.decode("utf-8-sig").strip()
            # The official endpoint is JSONP; unwrap only JSON, not JavaScript.
            if re.match(r"^[\w.$]+\(", decoded):
                decoded = decoded[decoded.index("(")+1:].rstrip(";\n\r ")[:-1]
            response = json.loads(decoded)
            listing = response[0]["listInfo"]
            entries = listing.get("content") or []
            new = 0
            for record in entries:
                path = record.get("destFilePath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                new += 1
                if record.get("companyCd") not in identity["codes"] or record.get("companyName") != identity["name"]:
                    raise ValueError("official exchange disclosure issuer mismatch")
                kind = material_type(record.get("disclosureTitle", ""))
                if not kind:
                    continue
                disclosed = date.fromisoformat(record.get("publishDate", "")).isoformat()
                if disclosed > cutoff:
                    raise ValueError("exchange disclosure after retrospective cutoff")
                joined = urllib.parse.urljoin(base, path)
                item = {"code": record["companyCd"], "name": identity["name"], "orgId": org_id,
                        "date": disclosed, "category": kind, "title": record["disclosureTitle"],
                        "announcement_id": Path(urllib.parse.urlsplit(path).path).stem,
                        "project_id": record.get("disclosureCode", ""), "url": official_url(joined),
                        "metadata_url": url, "metadata_record": receipt, "metadata_query": params}
                selected.append(item)
            if not entries or not new or listing.get("lastPage") is True:
                break
        else:
            raise RuntimeError("exchange collection pagination budget exhausted")
        return selected

    def download(self, item: dict) -> dict:
        # Only explicit source metadata is allowed to assign disclosure time.
        disclosed = date.fromisoformat(item["date"])
        if not item.get("metadata_record") or not item.get("announcement_id"):
            raise ValueError("PDF has no frozen official disclosure metadata")
        title = re.sub(r'[\\/:*?"<>|]', "_", item["title"])[:110]
        relative = f"reports/{item['code']}_{item['name']}/{item['category']}/{disclosed.isoformat()}_{item['announcement_id']}_{title}.pdf"
        payload = self.request(item["url"])
        if not payload.startswith(b"%PDF-"):
            raise ValueError("official material response is not PDF")
        result = dict(item)
        result.update(self.freeze_bytes(relative, payload))
        result["retrieved_at"] = datetime.now(timezone.utc).isoformat()
        return result

    def write_manifest(self, documents: list[dict], *, cutoff: str, missing: list[dict] | None = None):
        manifest = {"schema_version": 1, "cutoff": cutoff, "identities": IDENTITIES,
                    "documents": documents, "missing": missing or []}
        self.freeze_bytes("material_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        fields = ["code", "name", "orgId", "category", "date", "title", "url", "file", "size_bytes", "sha256"]
        import io
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(documents)
        self.freeze_bytes("index.csv", output.getvalue().encode("utf-8-sig"))
        return manifest


def combine_collections(root: str | Path, inputs: list[str | Path], *, cutoff: str, missing=None) -> dict:
    """Combine verified material receipts without redownloading large PDFs."""
    collector = OfficialCollector(root)
    documents = []
    for source in map(lambda p: Path(p).resolve(strict=True), inputs):
        manifest = json.loads((source / 'material_manifest.json').read_text(encoding='utf-8'))
        if manifest['cutoff'] != cutoff:
            raise ValueError('collection cutoff mismatch')
        for original in manifest['documents']:
            item = dict(original)
            record = item['metadata_record']
            metadata_path = (source / record['file']).resolve(strict=True)
            if not metadata_path.is_relative_to(source):
                raise ValueError('official metadata source escapes input collection')
            payload = metadata_path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != record['sha256']:
                raise ValueError('official metadata receipt changed')
            item['metadata_record'] = collector.freeze_bytes(record['file'], payload)
            pdf = (source / item['file']).resolve(strict=True)
            if not pdf.is_relative_to(source) or hashlib.sha256(pdf.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('official PDF receipt changed or escapes input collection')
            destination = (collector.root / item['file']).resolve()
            if not destination.is_relative_to(collector.root):
                raise ValueError('combined material escapes output collection')
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                try:
                    os.link(pdf, destination)
                except OSError:
                    collector.freeze_bytes(item['file'], pdf.read_bytes())
            elif hashlib.sha256(destination.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('combined material target already has different bytes')
            documents.append(item)
    return collector.write_manifest(documents, cutoff=cutoff, missing=missing)
