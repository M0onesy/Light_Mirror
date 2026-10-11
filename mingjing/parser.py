from __future__ import annotations

import json
import re
import subprocess
import csv
import hashlib
import os
import tempfile
import time
from pathlib import Path
from decimal import Decimal
from dataclasses import replace
from datetime import date
from typing import Iterable

from .evidence import make_evidence_id, sha256_file
from .models import Evidence, Fact
from .taxonomy import FINANCIAL_LABELS, UNDEFINED_ACCOUNTS, SHARE_LABELS


def _parse_checkpoint(cancel_event=None, progress_callback=None, deadline_monotonic=None, phase=None, **fields):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('corpus parsing cancelled')
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError('corpus parsing deadline exceeded')
    if progress_callback is not None and phase:
        progress_callback(phase, **fields)


def _control_options(cancel_event=None, progress_callback=None, deadline_monotonic=None) -> dict:
    return {key: value for key, value in dict(cancel_event=cancel_event, progress_callback=progress_callback,
                                             deadline_monotonic=deadline_monotonic).items() if value is not None}


def _read_text(path: Path, **control) -> str:
    if not control:
        return path.read_text(encoding='utf-8-sig', errors='replace')
    chunks = []
    with path.open(encoding='utf-8-sig', errors='replace') as stream:
        while True:
            _parse_checkpoint(**control)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
    return ''.join(chunks)


def _file_hash(path: Path, **control) -> str:
    if not control:
        return sha256_file(path)
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            _parse_checkpoint(**control)
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _pdftotext(path: Path, **control) -> str:
    command = ['pdftotext', '-layout', str(path), '-']
    if not control:
        return subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                              errors='replace', check=True).stdout
    # File-backed pipes avoid a child blocking on a full stdout buffer while
    # the host polls cancellation. Only this owned converter is terminated.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        child = subprocess.Popen(command, stdout=output, stderr=errors, creationflags=flags)
        try:
            while child.poll() is None:
                _parse_checkpoint(**control)
                event = control.get('cancel_event')
                if event is not None:
                    event.wait(.05)
                else:
                    time.sleep(.05)
            _parse_checkpoint(**control)
            if child.returncode:
                raise subprocess.CalledProcessError(child.returncode, command)
            output.seek(0)
            return output.read().decode('utf-8', errors='replace')
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=1)


def extract_text(path: str | Path, *, cancel_event=None, progress_callback=None,
                 deadline_monotonic=None) -> list[dict]:
    path = Path(path)
    control = _control_options(cancel_event, progress_callback, deadline_monotonic)
    _parse_checkpoint(**control)
    if path.suffix.lower() in {".md", ".txt", ".csv", ".json"}:
        return [{"page": 1, "text": _read_text(path, **control), "blocks": []}]
    if path.suffix.lower() != ".pdf":
        return []
    try:
        import fitz  # type: ignore
        pages = []
        with fitz.open(path) as doc:
            for number, page in enumerate(doc, 1):
                _parse_checkpoint(**control, phase='解析PDF页面' if number == 1 or number == len(doc) or number % 10 == 0 else None,
                                  document=path.name, page=number, pages=len(doc))
                page_text = page.get_text()
                blocks = []
                for block in page.get_text("blocks"):
                    x0, y0, x1, y1, text = block[:5]
                    blocks.append({"bbox": [x0, y0, x1, y1], "text": text})
                lines = []
                cursor = 0
                for block in page.get_text('dict')['blocks']:
                    _parse_checkpoint(**control)
                    if block.get('type') != 0:
                        continue
                    for line in block.get('lines', []):
                        line_text = ''.join(span['text'] for span in line.get('spans', []))
                        if not line_text:
                            continue
                        start = page_text.find(line_text, cursor)
                        if start < 0:
                            # Do not synthesize offsets for unsupported text
                            # ordering or normalize away source characters.
                            continue
                        end = start+len(line_text)
                        lines.append({'text': line_text, 'bbox': list(line['bbox']),
                                      'start': start, 'end': end})
                        cursor = end
                vector_lines, vector_rectangles = [], []
                for drawing in page.get_drawings() if hasattr(page, 'get_drawings') else ():
                    for item in drawing.get('items', ()):
                        if item[0] == 'l':
                            vector_lines.append([float(item[1].x), float(item[1].y),
                                                 float(item[2].x), float(item[2].y)])
                        elif item[0] == 're':
                            rect = item[1]
                            fill = drawing.get('fill')
                            if drawing.get('type') in {'f', 'fs'} and fill is not None:
                                import math
                                rectangle = [float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)]
                                colour = [float(value) for value in fill]
                                if (len(colour) in {1, 3, 4} and all(math.isfinite(value) and 0 <= value <= 1 for value in colour)
                                        and all(math.isfinite(value) for value in rectangle)
                                        and rectangle[0] < rectangle[2] and rectangle[1] < rectangle[3]):
                                    vector_rectangles.append({'bbox': rectangle, 'fill': colour})
                            vector_lines.extend([[rect.x0, rect.y0, rect.x1, rect.y0],
                                                 [rect.x1, rect.y0, rect.x1, rect.y1],
                                                 [rect.x1, rect.y1, rect.x0, rect.y1],
                                                 [rect.x0, rect.y1, rect.x0, rect.y0]])
                pages.append({"page": number, "text": page_text, "blocks": blocks,
                              'lines': lines, 'vector_lines': vector_lines,
                              'vector_rectangles': vector_rectangles})
        return pages
    except (InterruptedError, TimeoutError):
        raise
    except Exception:
        try:
            _parse_checkpoint(**control, phase='PDF文本层降级', document=path.name)
            extracted = _pdftotext(path, **control)
            # pdftotext separates original pages with form feeds. Preserve page
            # identity even though this fallback has no reliable coordinates.
            texts = extracted.split('\f')
            if texts and not texts[-1].strip():
                texts.pop()
            return [{"page": number, "text": text, "blocks": []}
                    for number, text in enumerate(texts, 1)]
        except (InterruptedError, TimeoutError):
            raise
        except Exception:
            return []


def build_evidence(path: str | Path) -> list[Evidence]:
    path = Path(path)
    doc_sha = sha256_file(path)
    output: list[Evidence] = []
    for page in extract_text(path):
        text = page["text"]
        blocks = page.get("blocks") or [{"bbox": None, "text": text}]
        for block in blocks:
            snippet = block["text"].strip()
            if not snippet:
                continue
            output.append(Evidence(make_evidence_id(doc_sha, page["page"], snippet, block.get("bbox")), str(path), page["page"], tuple(block["bbox"]) if block.get("bbox") else None, snippet, document_sha256=doc_sha))
    return output


def _decimal(raw: str) -> str:
    return raw.replace(",", "").replace("，", "").replace("－", "-").replace("−", "-")


_LABELS = {
    "归属于挂牌公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于挂牌公司股东的扣除非经常性损益的净利润": "NP_PARENT_ADJ",
    "归属于上市公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于上市公司股东的扣除非经常性损益的净利润": "NP_PARENT_ADJ",
    "归属于母公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于母公司所有者的净利润": "NP_PARENT",
    "归属于母公司股东的净利润": "NP_PARENT",
    "归属于上市公司股东的净利润": "NP_PARENT",
    "归属于挂牌公司股东的净利润": "NP_PARENT",
    "归属母公司净利润": "NP_PARENT", "归母净利润": "NP_PARENT",
    "经营活动产生的现金流量净额": "CFO",
    "归属于上市公司股东的净资产": "EQUITY_PARENT",
    "归属于挂牌公司股东的净资产": "EQUITY_PARENT",
    "归属于母公司所有者权益合计": "EQUITY_PARENT",
    "营业总收入": "REVENUE", "营业收入": "REVENUE", "营业成本": "COST",
    "营业总成本": "TOTAL_COST", "主营业务成本": "MAIN_COST",
    "资产总计": "ASSETS", "负债总计": "LIABILITIES", "负债合计": "LIABILITIES",
    "净利润": "NP", "基本每股收益": "EPS", "稀释每股收益": "EPS_DILUTED",
    "非经常性损益净额": "NONRECURRING_NET",
    "非经常性损益项目合计": "NRI_TOTAL", "非经常性损益总额": "NRI_TOTAL",
    "非经常性损益合计": "NRI_TOTAL",
    "所得税影响额": "NRI_TAX_EFFECT", "所得税影响数": "NRI_TAX_EFFECT",
    # Many reports print an already-signed bridge adjustment. Preserve its
    # raw identity; subtraction-formula operands require an explicit sign rule.
    "少数股东权益影响额": "NRI_MINORITY_RAW",
    "毛利率": "GROSS_MARGIN", "净利率": "NET_MARGIN", "资产负债率": "DEBT_RATIO",
    "加权平均净资产收益率": "ROE", "加权净资产收益率": "ROE",
    "汇率变动对现金及现金等价物的影响": "FX_CASH_EFFECT",
}
_LABELS.update({label: code for label, code in FINANCIAL_LABELS.items() if code not in UNDEFINED_ACCOUNTS})
_LABELS.update(SHARE_LABELS)
_NUMBER = re.compile(r"(?<![\d.])(?P<num>[+\-－−]?\d[\d,，]*(?:\.\d+)?)(?P<unit>百万元|亿元|万元|元|亿股|万股|股|%|％)?")
_EPS_EXPLICIT_UNIT = re.compile(
    r"(?<![\d.])[+\-－−]?\d[\d,，]*(?:\.\d+)?\s*(?P<unit>百万元|亿元|万元|元|亿股|万股|股|%|％)")
_EPS_CELL_NUMBER = re.compile(
    r"(?<![\d.])(?P<num>[+\-－−]?\d[\d,，]*(?:\.\d+)?)(?:\s*(?P<unit>百万元|亿元|万元|元|亿股|万股|股|%|％))?")
_GROWTH_COLUMN = re.compile(
    r"(?:本(?:年|期)(?:末)?比上(?:年|期)(?:末)?(?:增减|增长|变动|增加)|同比(?:增长|增减|变动)|"
    r"(?:增减|变动)(?:比例|比率|幅度))"
    r"(?:幅度|比率)?[（(]?[%％][）)]?")
_UNITS = {"百万元": Decimal("1000000"), "亿元": Decimal("100000000"), "万元": Decimal("10000"), "元": Decimal("1"),
          '股': Decimal('1'), '万股': Decimal('10000'), '亿股': Decimal('100000000'), "%": Decimal('0.01'), "％": Decimal('0.01')}
_RATIO_ACCOUNTS = {'GROSS_MARGIN', 'NET_MARGIN', 'DEBT_RATIO', 'ROE'}

_VERSION_CAPTION = re.compile(r'调整前|调整后|重述前|重述后|更正前|更正后')
_ROW_NOTE_REFERENCE = re.compile(
    r'[一二三四五六七八九十]+\s*[、.．]?\s*(?:[（(]\s*[一二三四五六七八九十\d]+\s*[）)]'
    r'(?:\s*[、.．]\s*\d+)*|[、.．]\s*\d+(?:\s*[、.．]\s*\d+)*)')


def _inline_header_versions(text: str, periods: list[str], forecasts: list[bool]):
    """Expand only versions explicitly attached to a source period caption."""
    anchors = list(re.finditer(r'20\d{2}(?:[AE]|年(?:\d{1,2}月\d{1,2}日|上半年|半年度|年度|末)?)?', text, re.I))
    if len(anchors) != len(periods):
        return periods, forecasts, ['']*len(periods)
    expanded, flags, versions = [], [], []
    for index, (period, forecast) in enumerate(zip(periods, forecasts)):
        end = anchors[index+1].start() if index+1 < len(anchors) else len(text)
        labels = _VERSION_CAPTION.findall(text[anchors[index].end():end])
        # In a stacked caption all versions can appear after the final year;
        # do not assign that shared row to the final period by proximity.
        if len(labels) > 2 or len(labels) == 2 and '\n' in text[anchors[index].end():end]:
            return periods, forecasts, ['']*len(periods)
        for label in labels or ['']:
            expanded.append(period)
            flags.append(forecast)
            versions.append(label)
    return expanded, flags, versions


def _geometry_header_versions(blocks: list[dict], heading: dict, periods: list[str], forecasts: list[bool]):
    """Resolve a two-level header from original physical column positions."""
    if not heading.get('bbox'):
        return None
    baseline = heading['bbox'][1]
    pool = {}
    for block in blocks:
        for physical in block.get('geometry_lines') or [block]:
            item = {**physical, 'evidence_text': block.get('evidence_text', block['text']),
                    'evidence_bbox': block.get('evidence_bbox', block.get('bbox'))}
            pool[(item['text'], tuple(item.get('bbox') or []))] = item
    leaf = re.compile(r'20\d{2}(?:[AE]|年(?:\d{1,2}月(?:\d{1,2}日?)?|'
                      r'1月?[-—至~～]\d{1,2}月|上半年|下半年|半年度|半年|前三季度|'
                      r'第?[一二三四]季度|度|年度|末)?)?', re.I)
    tolerance = max(3, min(12, (heading['bbox'][3]-heading['bbox'][1])/2))
    row = [block for block in pool.values() if block.get('bbox') and abs(block['bbox'][1]-baseline) <= tolerance
           and leaf.fullmatch(_compact(block['text']))]
    row.sort(key=lambda block: block['bbox'][0])
    if len(row) < 2:
        return None
    # PDF reading order can differ from physical column order. Complete date
    # captions only through a unique, vertically adjacent continuation in the
    # same column; e.g. "2022年12月31" / "日" or "2021年12月" / "31日".
    completed = []
    for block in row:
        text = _compact(block['text'])
        selected = [block]
        if re.fullmatch(r'20\d{2}年\d{1,2}月(?:\d{1,2})?', text):
            box = block['bbox']
            candidates = [item for item in pool.values() if item.get('bbox')
                          and box[3]-1 <= item['bbox'][1] <= box[3]+10
                          and abs((item['bbox'][0]+item['bbox'][2])-(box[0]+box[2])) <= 12
                          and re.fullmatch(r'(?:\d{1,2})?日|[-—至~～]\d{1,2}月', _compact(item['text']))]
            if len(candidates) != 1:
                return None
            selected.append(candidates[0])
            text += _compact(candidates[0]['text'])
        completed.append({**block, 'text':text, 'header_proofs':selected,
                          'bbox':[min(item['bbox'][0] for item in selected), min(item['bbox'][1] for item in selected),
                                  max(item['bbox'][2] for item in selected), max(item['bbox'][3] for item in selected)]})
    row = completed
    captions = [_window_header_periods(_compact(block['text']), periods[0] if periods else '') for block in row]
    if any(len(caption[0]) != 1 for caption in captions):
        return None
    periods, forecasts = [caption[0][0] for caption in captions], [caption[1][0] for caption in captions]
    centers = [(block['bbox'][0]+block['bbox'][2])/2 for block in row]
    if centers != sorted(set(centers)):
        return None
    bottom = max(block['bbox'][3] for block in row)
    children = [block for block in pool.values() if block.get('bbox')
                and bottom-1 <= block['bbox'][1] <= bottom+35
                and _VERSION_CAPTION.fullmatch(_compact(block['text']))]
    proofs = [proof for block in row for proof in block['header_proofs']]
    if not children:
        return periods, forecasts, ['']*len(periods), proofs
    mapped = [[] for _ in row]
    for child in sorted(children, key=lambda block: block['bbox'][0]):
        x = (child['bbox'][0]+child['bbox'][2])/2
        distance = [abs(x-center) for center in centers]
        if distance.count(min(distance)) != 1:
            return None
        mapped[distance.index(min(distance))].append(_compact(child['text']))
    expanded, flags, versions = [], [], []
    for period, forecast, labels in zip(periods, forecasts, mapped):
        if labels and (len(labels) != 2 or len(set(labels)) != 2
                       or labels[0].removesuffix('前') != labels[1].removesuffix('后')):
            return None
        for label in labels or ['']:
            expanded.append(period)
            flags.append(forecast)
            versions.append(label)
    return expanded, flags, versions, [*proofs, *children]


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _growth_column_proofs(blocks: list[dict], heading: dict) -> list[dict]:
    """Prove a wrapped percentage caption in its physical header column."""
    pool = {}
    starts = []
    for block in blocks:
        for physical in block.get('geometry_lines') or [block]:
            item = {**physical, 'evidence_text': block.get('evidence_text', block['text']),
                    'evidence_bbox': block.get('evidence_bbox', block.get('bbox'))}
            pool[(item['text'], tuple(item.get('bbox') or []))] = item
            if block is heading:
                starts.append(item)
    for first in starts:
        text = _compact(first['text'])
        if _GROWTH_COLUMN.fullmatch(text):
            return [first]
        if not first.get('bbox') or not re.match(r'^(?:本(?:年|期)|同比)', text):
            continue
        selected, previous = [first], first
        for _ in range(2):
            box = previous['bbox']
            candidates = [item for item in pool.values() if item.get('bbox')
                and (abs(item['bbox'][0] - first['bbox'][0]) <= 3
                     or abs((item['bbox'][0]+item['bbox'][2])-(first['bbox'][0]+first['bbox'][2])) <= 6)
                and box[1] + 1 < item['bbox'][1] <= box[3] + 8]
            if not candidates:
                break
            nearest_y = min(item['bbox'][1] for item in candidates)
            nearest = [item for item in candidates if item['bbox'][1] == nearest_y]
            if len(nearest) != 1:
                break
            previous = nearest[0]
            text += _compact(previous['text'])
            selected.append(previous)
            if _GROWTH_COLUMN.fullmatch(text):
                return selected
            if len(text) > 40:
                break
    return []


def _logical_blocks(blocks: list[dict], geometry_lines: list[dict] | None = None) -> list[dict]:
    """Split table rows that PyMuPDF coalesces into one original text block.

    Original span text/bounds remain attached for evidence lookup. Alternating
    labels and numeric cells provide the row boundaries; no value is inferred.
    """
    output = []
    numeric_line = re.compile(r"^[\d,，.\s+\-－−%％亿元万/]+$")
    for block in blocks:
        lines = [line.strip() for line in block["text"].splitlines() if line.strip()]
        groups = []
        group = []
        seen_values = False
        group_has_label = False
        for line in lines:
            is_value = bool(numeric_line.fullmatch(line)) and bool(re.search(r"\d|^-+$", line))
            normalized_label = _compact(line)
            starts_financial_row = any(normalized_label.startswith(label) or label.startswith(normalized_label)
                                       for label in _LABELS)
            if group and not seen_values and starts_financial_row and not group_has_label:
                groups.append(group)
                group = []
                group_has_label = False
            if group and seen_values and not is_value:
                groups.append(group)
                group = []
                seen_values = False
                group_has_label = False
            group.append(line)
            seen_values = seen_values or is_value
            group_has_label = group_has_label or starts_financial_row
        if group:
            groups.append(group)
        physical = []
        if geometry_lines and block.get('bbox'):
            x0, y0, x1, y1 = block['bbox']
            physical = [line for line in geometry_lines if x0-0.1 <= line['bbox'][0] <= line['bbox'][2] <= x1+0.1
                        and y0-0.1 <= line['bbox'][1] <= line['bbox'][3] <= y1+0.1]
        cursor = 0
        for group in groups:
            geometry = []
            next_cursor = cursor
            for text in group:
                found = next((i for i in range(next_cursor, len(physical))
                              if physical[i]['text'].strip() == text), None)
                if found is None:
                    geometry = []
                    break
                geometry.append(physical[found]['bbox'])
                next_cursor = found+1
            row_box = block.get('bbox')
            if geometry:
                row_box = [min(b[0] for b in geometry), min(b[1] for b in geometry),
                           max(b[2] for b in geometry), max(b[3] for b in geometry)]
                cursor = next_cursor
            output.append({**block, "text": "\n".join(group), 'bbox': row_box,
                           'evidence_bbox': block.get('evidence_bbox', block.get('bbox')),
                           "evidence_text": block.get("evidence_text", block["text"]),
                           'geometry_lines': [line for line in physical if line['text'].strip() in group]})
    return output


def _row_account(text: str) -> tuple[str, str, str] | None:
    """Match an entire row label, never a financial word inside prose/ratios."""
    # PDF text extraction commonly wraps long metric captions across several
    # physical lines (for example the weighted ROE definition has a line break
    # before ``归属于上市公司股东`` and another one before ``计算``).  The
    # caller normally supplies a compact row, but local reparse and direct
    # parser users may pass the original line-preserving text.  Normalize only
    # whitespace here so the label/qualifier grammar sees one caption while
    # retaining every non-whitespace character for the numeric tail.
    text = _compact(text)
    cleaned = re.sub(r"^(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十\d]+[）)]|\d+[.．])", "", text)
    cleaned = re.sub(r"^(?:其中|加|减)[:：]", "", cleaned)
    for label, account in sorted(_LABELS.items(), key=lambda item: -len(item[0])):
        if not cleaned.startswith(label):
            continue
        tail = cleaned[len(label):]
        # Recognized loss wording may intervene; arbitrary prose, rate suffixes,
        # CAGR, segment descriptors and growth rows may not.
        tail = re.sub(r"^[（(](?:净亏损|亏损|损失)[^）)]{0,24}[）)]", "", tail)
        tail = re.sub(r"^[（(](?:百万元|亿元|万元|元(?:/股)?|亿股|万股|股|%|％)[）)]", "", tail)
        if account == 'NRI_MINORITY_RAW':
            tail = re.sub(r"^[（(]税后[）)]", '', tail)
            if re.search(r"(?:^|[、.])减[:：]", text):
                account = 'NRI_MINORITY'
        if account in _RATIO_ACCOUNTS:
            tail = re.sub(r"^[%％]", '', tail)
            tail = re.sub(r'^[（(](?:合并|母公司|扣非前|扣非后)[）)]', '', tail)
            if account == 'ROE':
                tail = re.sub(r'^[（(](?:依据)?归属于(?:上市公司股东|挂牌公司股东|母公司所有者|母公司股东)'
                              r'的(?:扣除非经常性损益后的)?净利润计算[）)]', '', tail)
        # Compact text can concatenate an index with the next monetary cell.
        # Validate the note's textual prefix here; remove its numeric indexes
        # only from the original whitespace-preserving source below.
        tail = re.sub(r'^[一二三四五六七八九十]+[、.．]?[（(][一二三四五六七八九十\d]+[）)][、.．]?', '', tail)
        tail = re.sub(r'^[一二三四五六七八九十]+[、.．](?=\d)', '', tail)
        if not re.match(r"^[+\-－−—–\d]", tail):
            return None
        # Being inside a financial section does not make prose a table row.
        # Admit only cells/blank dashes after the complete caption. Otherwise
        # two amounts in a paragraph can accidentally match two year columns.
        # Compact text can concatenate adjacent decimal cells. Only check
        # vocabulary here; values/precision are parsed from the original row.
        vocabulary = re.sub(r'(?<=元)/股', '', tail) if account in {'EPS', 'EPS_DILUTED'} else tail
        residual = re.sub(r'百万元|亿元|万元|元|亿股|万股|股|[%％\d.,，+－−\-]', '', vocabulary)
        if residual.strip('—–'):
            return None
        return label, account, tail
    return None


def _document_period(path: Path, supplied: str) -> str:
    match = re.search(r"(20\d{2})年?(?:年度|年年度|半年度|年半年度|第?一季度|第?三季度)", path.stem)
    if match:
        suffix = "H1" if "半年度" in match[0] else "Q1" if "一季度" in match[0] else "9M" if "三季度" in match[0] else "Y"
        return match[1] + suffix
    return supplied


def _company_from_path(path: Path) -> tuple[str, str] | None:
    """The corpus' stock-code/company directory is an authoritative identity."""
    for part in reversed(path.parent.parts):
        match = re.fullmatch(r"(\d{6})_(.+)", part)
        if match:
            return match[1], match[2]
    return None


def _company_matches(requested: str, known: str, code: str = "") -> bool:
    # Do not use substring matching: e.g. 国电 and 国电电力 are different issuers.
    from .issuer_identity import same_issuer
    return not requested or same_issuer(_compact(requested), _compact(known)) or bool(code and same_issuer(_compact(requested), code))


def _indexed_files(root: Path, company: str, **control) -> list[tuple[Path, str, str, str]] | None:
    """Read the existing index without treating its metadata as numeric facts."""
    selected = root.resolve()
    candidates = [selected.parent] if selected.is_file() else [selected]
    candidates += list(candidates[0].parents)
    index = next((candidate / "index.csv" for candidate in candidates if (candidate / "index.csv").is_file()), None)
    if index is None:
        return None
    entries = []
    with index.open(encoding="utf-8-sig", newline="") as stream:
        for number, row in enumerate(csv.DictReader(stream), 1):
            _parse_checkpoint(**control, phase='筛选语料索引' if number == 1 or number % 128 == 0 else None,
                              index_rows=number, selected_documents=len(entries), company=company)
            if not row.get("file"):
                continue
            path = (index.parent / row["file"]).resolve()
            if not path.is_relative_to(index.parent.resolve()):
                raise ValueError("语料 index.csv 包含越过语料根目录的路径")
            if selected.is_file() and path != selected or selected.is_dir() and not path.is_relative_to(selected):
                continue
            known = _company_from_path(path)
            indexed_company = row.get("name", "").strip()
            indexed_code = row.get("code", "").strip()
            if known and indexed_company and (not _company_matches(indexed_company, known[1], known[0])
                    or indexed_code and not _company_matches(indexed_code, known[1], known[0])):
                raise ValueError(f"语料索引公司身份与目录不一致：{path.name}")
            issuer = known[1] if known else indexed_company
            code = known[0] if known else indexed_code
            if company and not _company_matches(company, issuer, code):
                if selected.is_file():
                    raise ValueError(f"所选报告属于 {issuer}，与核查公司 {company} 不一致")
                continue
            if not path.is_file():
                raise ValueError(f"语料索引引用的文件不存在：{path}")
            if row.get("size_bytes") and int(row["size_bytes"]) != path.stat().st_size:
                raise ValueError(f"语料文件大小与索引不一致：{path.name}")
            _parse_checkpoint(**control, phase='校验已选财报', document=path.name, selected_documents=len(entries) + 1)
            if row.get("sha256") and row["sha256"].lower() != _file_hash(path, **control):
                raise ValueError(f"语料文件 SHA-256 与冻结索引不一致：{path.name}")
            category = row.get("category", "")
            indexed_year = row.get("year", "")
            row_period = indexed_year + ("H1" if "半年" in category else "Q1" if "一季" in category else "9M" if "三季" in category else "Y" if "年报" in category else "") if indexed_year else ''
            explicit_period = str(row.get('period') or '').strip()
            if explicit_period:
                if not re.fullmatch(r'20\d{2}(?:Y|H[12]|Q[1-4]|9M)', explicit_period):
                    raise ValueError('语料索引期间必须是登记期间')
                if row_period and row_period != explicit_period:
                    raise ValueError('语料索引显式期间与类别年份冲突')
                row_period = explicit_period
            filename_period = _document_period(path, "")
            if filename_period and row_period and filename_period != row_period:
                raise ValueError(f"语料索引期间与报告文件名不一致：{path.name}")
            filename_date = re.match(r"(20\d{2}-\d{2}-\d{2})", path.name)
            if filename_date and row.get("date") and filename_date[1] != row["date"]:
                raise ValueError(f"语料索引披露日期与报告文件名不一致：{path.name}")
            entries.append((path, issuer, row_period, row.get("date", "")))
    # An existing frozen index is an allowlist. An empty company selection
    # must not silently switch to scanning unindexed reports.
    return entries


def _unindexed_files(root: Path, company: str, **control) -> list[Path]:
    if root.is_file():
        return [root]
    paths = []
    visited = 0
    has_issuer_directories = bool(_company_from_path(root / '_issuer_probe'))
    for directory, folders, names in os.walk(root, followlinks=False):
        visited += 1
        _parse_checkpoint(**control, phase='检索语料目录' if visited == 1 or visited % 128 == 0 else None,
                          scanned_directories=visited, selected_documents=len(paths), company=company)
        current = Path(directory)
        known = _company_from_path(current / '_issuer_probe')
        has_issuer_directories = has_issuer_directories or bool(known)
        if company and known and not _company_matches(company, known[1], known[0]):
            folders[:] = []
            continue
        retained = []
        for name in sorted(folders):
            _parse_checkpoint(**control)
            child = current / name
            known = _company_from_path(child / '_issuer_probe')
            has_issuer_directories = has_issuer_directories or bool(known)
            junction = getattr(os.path, 'isjunction', lambda _: False)(child)
            if child.is_symlink() or junction:
                continue
            if company and known and not _company_matches(company, known[1], known[0]):
                continue
            retained.append(name)
        folders[:] = retained
        for name in sorted(names):
            _parse_checkpoint(**control)
            path = current / name
            if path.suffix.lower() in {'.pdf', '.md', '.txt'} and not path.is_symlink():
                paths.append(path)
    if company and has_issuer_directories:
        # Pruning must not make an anonymous file in a mixed-company root
        # look like an authorized single-company report.
        paths = [path for path in paths if _company_from_path(path)]
    _parse_checkpoint(**control, phase='语料目录筛选完成', scanned_directories=visited,
                      selected_documents=len(paths), company=company)
    return sorted(paths)


def _table_heading(text: str) -> tuple[str, str, str] | None:
    if any(word in text for word in ('计算公式', '预测', '预计', '测算')):
        return None
    if re.search(r"(?:合并|母公司)(?:资产负债表|利润表|现金流量表)(?:[（(]续[）)])?$", text) and len(text) < 45:
        return "primary_statement", "母公司" if "母公司" in text else "合并", text
    if any(label in text for label in ("会计数据和财务指标", "主要财务指标", "主要会计数据", "非经常性损益项目和金额")) and len(text) < 65:
        return "financial_summary", "合并", text
    if re.search(r"20\d{2}年分季度主要财务数据", text):
        return "financial_summary", "合并", text
    return None


def _period_column_centres(block: dict, periods: list[str]) -> list[float]:
    """Actual leaf year captions supply column positions; no amounts involved."""
    source = block.get('geometry_lines', [])
    found = []
    for line in source:
        compact = _compact(line['text'])
        if re.fullmatch(r'20\d{2}(?:年(?:\d{1,2}月\d{1,2}日|度|末)?|[AE])', compact):
            box = line['bbox']
            found.append((float((box[0]+box[2])/2), compact[:4]))
    found.sort()
    if len(found) == len(periods) and [year for _, year in found] == [p[:4] for p in periods]:
        return [x for x, _ in found]
    # A report's explicit current/comparative header is also a source anchor.
    # Preserve its geometry so a blank cell does not shift the other period.
    relative = []
    for line in source:
        compact = _compact(line['text'])
        if compact in {'本期金额', '本期发生额', '本期', '本期期末'}:
            relative.append(((line['bbox'][0]+line['bbox'][2])/2, 0))
        elif compact in {'上期金额', '上期发生额', '上年同期', '上年期末'}:
            relative.append(((line['bbox'][0]+line['bbox'][2])/2, 1))
    relative.sort()
    if len(periods) == 2 and [index for _, index in relative] == [0, 1]:
        return [float(x) for x, _ in relative]
    return []


def _growth_column_position(block: dict, periods: list[str]) -> int | None:
    """Locate one expressly captioned change column among period columns.

    Geometric leaf captions take precedence. A complete single source block
    may also provide an ordered relative/year header. A detached caption or
    an unexplained surplus percentage cell never authorises dropping a cell.
    """
    text = _compact(block['text'])
    growth = list(_GROWTH_COLUMN.finditer(text))
    if len(growth) != 1:
        return None
    lines = block.get('geometry_lines', [])
    positioned_growth = [line for line in lines if _GROWTH_COLUMN.fullmatch(_compact(line['text']))]
    centres = _period_column_centres(block, periods)
    if lines:
        if len(positioned_growth) != 1 or len(centres) != len(periods):
            return None
        box = positioned_growth[0]['bbox']
        center = (box[0]+box[2])/2
        if any(abs(center-value) < 3 for value in centres):
            return None
        return sum(value < center for value in centres)
    years = list(re.finditer(r'20\d{2}(?:[AE]|年(?:\d{1,2}月\d{1,2}日|度|末)?)', text, re.I))
    if len(years) == len(periods) and [match[0][:4] for match in years] == [period[:4] for period in periods]:
        return sum(match.start() < growth[0].start() for match in years)
    # The document anchors the actual periods elsewhere. Both relative leaf
    # captions must be present before the change caption in this source block.
    prefix = text[:growth[0].start()]
    current = list(re.finditer(r'本期(?:金额|发生额|期末)?', prefix))
    previous = list(re.finditer(r'上年同期|上年期末|上期金额', prefix))
    if (len(periods) == 2 and len(current) == len(previous) == 1
            and current[0].start() < previous[0].start()):
        return 2
    return None


def _align_known_cells(original: str, label_end: int, values: list, columns: list[str],
                       centres: list[float], geometry_lines: list[dict],
                       growth_index: int | None = None) -> list | None:
    """Keep independently located values, preserving blanks as absent cells."""
    token_pattern = re.compile(_NUMBER.pattern + r'|(?<![\d.])[-—–]{1,2}(?![\d.])')
    tokens = list(token_pattern.finditer(original[label_end:]))
    if growth_index is not None and len(tokens) == len(columns)+1:
        # Remove the captioned change cell, including a genuine blank, before
        # aligning the remaining historical cells. No missing value becomes 0.
        tokens = tokens[:growth_index] + tokens[growth_index+1:]
    has_blank = any(match.group('num') is None for match in tokens)
    if len(tokens) == len(columns):
        return [match if match.group('num') is not None else None for match in tokens]
    # Financial-summary tables often place an explicitly blank change column
    # immediately after the row caption (for example the ROE rows on page 8
    # of the parallel-computing annual report).  The blank is a real source
    # cell, while the three numeric tokens still map one-to-one to the three
    # historical columns.  When the numeric extractor already found exactly
    # one value per declared column, and the only surplus token is a leading
    # dash, we can remove that change cell without relying on geometry from a
    # wrapped label block.  This does not turn the dash into zero and cannot
    # hide a missing historical value: a missing historical cell leaves fewer
    # numeric values than declared columns.
    if (len(values) == len(columns) and len(tokens) == len(columns) + 1
            and sum(match.group('num') is None for match in tokens) == 1
            and tokens[0].group('num') is None):
        return values
    if len(values) == len(columns) and not has_blank:
        return values
    if len(centres) != len(columns) or not geometry_lines:
        return None
    candidates = []
    for line in geometry_lines:
        content = line['text'].strip()
        match = _NUMBER.fullmatch(content)
        if match:
            box = line['bbox']
            x = (box[0]+box[2])/2
            # Text, dates and note indexes to the left of all actual amount
            # columns never inherit the first column by nearest-neighbour.
            gap = min((abs(b-a) for a, b in zip(centres, centres[1:])), default=100)
            if x < centres[0]-gap/2 or x > centres[-1]+gap/2:
                continue
            nearest = min(range(len(centres)), key=lambda i: abs(centres[i]-x))
            candidates.append((nearest, match))
    output = [None]*len(columns)
    for index, match in candidates:
        if output[index] is not None:
            return None
        output[index] = match
    # Every original numeric token must correspond to exactly one positioned
    # cell; a partial geometry capture cannot hide an unassigned amount.
    if len(candidates) != len(values) or not candidates:
        return None
    return output


def extract_facts(path: str | Path, company: str = "", period: str = "", publish_date: str = "", *,
                  cancel_event=None, progress_callback=None, deadline_monotonic=None) -> list[Fact]:
    """Extract exact rows from identified financial tables and column headers.

    Prose, notes/segment tables, unidentified units, and ambiguous columns stay
    outside the fact store. An unsupported layout results in missing facts, never
    a guessed first number. Supplied structured facts remain supported separately.
    """
    from .facts import FactStore
    path = Path(path)
    control = _control_options(cancel_event, progress_callback, deadline_monotonic)
    _parse_checkpoint(**control, phase='抽取财报事实', document=path.name, company=company)
    identified = _company_from_path(path)
    if identified:
        code, issuer = identified
        if company and not _company_matches(company, issuer, code):
            raise ValueError(f"所选报告属于 {issuer}，与核查公司 {company} 不一致")
        company = issuer
    doc_sha = _file_hash(path, **control)
    effective_period = _document_period(path, period)
    year = effective_period[:4] if re.match(r"20\d{2}", effective_period) else ""
    suffix = effective_period[4:] or "Y"
    date_in_name = re.match(r"(20\d{2}-\d{2}-\d{2})", path.name)
    disclosure_date = date_in_name[1] if date_in_name else publish_date
    disclosure_version = "更正披露" if any(token in path.stem for token in ("更正", "修订", "重述")) else "原披露"
    return _extract_table_facts(extract_text(path, **control), path=path, company=company,
                               effective_period=effective_period, doc_sha=doc_sha,
                               disclosure_date=disclosure_date, disclosure_version=disclosure_version,
                               **control)


def _extract_table_facts(pages, *, path, company, effective_period, doc_sha,
                         disclosure_date, disclosure_version, window_issues=None,
                         period_evidence=(), **control) -> list[Fact]:
    """Shared row parser; the ordinary extractor retains its legacy policy.

    Window mode additionally requires local dimension evidence and records
    unsupported layouts. It cannot borrow document/claim defaults.
    """
    from .facts import FactStore
    from .mentions import source_currency
    year = effective_period[:4] if re.match(r"20\d{2}", effective_period) else ""
    suffix = effective_period[4:] or "Y"
    strict = window_issues is not None
    heading_evidence = unit_evidence = column_evidence = ()

    def source_id(page, block):
        return make_evidence_id(doc_sha, page['page'], block.get('evidence_text', block['text']).strip(),
                                block.get('evidence_bbox', block.get('bbox')))

    def issue(code, page, reason):
        if strict:
            item = {'code': code, 'page': page['page'], 'reason': reason}
            if item not in window_issues:
                window_issues.append(item)

    source_type = scope = table_label = unit = ""
    table_currency = None
    warned_currency = set()
    columns: list[str] = []
    column_forecasts: list[bool] = []
    column_versions: list[str] = []
    note_column = False
    column_centres = []
    facts: list[Fact] = []
    for page in pages:
        _parse_checkpoint(**control)
        # A percentage may be a separately declared change column, but not
        # an EPS unit. Its caption must be local to this page/table.
        eps_growth_evidence = ()
        growth_column_index = None
        if strict and not page['text'].strip():
            source_type = unit = ''
            columns = []
            heading_evidence = unit_evidence = column_evidence = ()
            continue
        blocks = page.get("blocks") or []
        # Without coordinates a complete page is not a trustworthy row. Plain
        # text/Markdown tables use one line per row; PDF fallback uses layout lines.
        if not blocks:
            blocks = [{"bbox": None, "text": line} for line in page["text"].splitlines() if line.strip()]
        else:
            blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
        blocks = _logical_blocks(blocks, page.get('lines'))
        for block in blocks:
            _parse_checkpoint(**control)
            original = block["text"].strip()
            evidence_blocks = [block]
            text = _compact(original).strip("|")
            if not text:
                continue
            heading = _table_heading(text)
            if heading:
                source_type, scope, table_label = heading
                subject = re.sub(r'(?:合并|母公司)(?:资产负债表|利润表|现金流量表)(?:[（(]续[）)])?$', '', text)
                subject = re.sub(r'^[（(]?[一二三四五六七八九十\d]+[）).、．]*', '', subject)
                if subject and source_type == 'primary_statement' and not _company_matches(subject, company):
                    source_type = ''
                    issue('subject_unproven', page, '报表标题明示的公司与已锁定发行主体不一致')
                if strict:
                    declared_scopes = [s for s in ('合并', '母公司') if s in text]
                    if len(declared_scopes) != 1:
                        source_type = ''
                        issue('scope_unproven', page, '窗口中的报表标题没有唯一声明合并或母公司口径')
                    else:
                        scope = declared_scopes[0]
                unit = ""
                table_currency = None
                columns = []
                column_forecasts = []
                column_versions = []
                note_column = False
                column_centres = []
                eps_growth_evidence = ()
                growth_column_index = None
                heading_evidence = (source_id(page, block),) if source_type else ()
                unit_evidence = column_evidence = ()
                continue
            if strict and re.search(r'(?:资产负债表|利润表|现金流量表)', text) and len(text) < 45:
                source_type = unit = ''
                columns = []
                heading_evidence = unit_evidence = column_evidence = ()
                issue('scope_unproven', page, '窗口中的报表标题没有唯一声明合并或母公司口径')
                continue
            # Stop a financial-summary table before unrelated sections, and stop
            # primary statements at notes/changes. No context leaks into segments.
            if any(token in text for token in ("财务报表附注", "会计数据追溯调整", "境内外会计准则", "业绩预告", "业绩快报", "补充财务指标", "董事会报告", "管理层讨论", "公司治理", "审计报告", "审计意见", "所有者权益变动表", "会计政策变更", "会计估计变更", "会计差错更正", "财务报表范围", "募集资金", "项目投资", "项目建设", "计算公式", "首次执行")):
                source_type = ""
                unit = ""
                columns = []
                column_forecasts = []
                column_versions = []
                if strict:
                    heading_evidence = unit_evidence = column_evidence = ()
                continue
            if not source_type:
                continue
            if len(text) < 150 and not _row_account(text) and ('比上' in text or '同比' in text or _GROWTH_COLUMN.search(text)):
                proofs = ([block] if _GROWTH_COLUMN.search(text) else _growth_column_proofs(blocks, block))
                if proofs:
                    eps_growth_evidence = tuple(dict.fromkeys(source_id(page, proof) for proof in proofs))
            currency = source_currency(original)
            if currency:
                table_currency = currency
                if currency['currency'] == 'UNKNOWN':
                    issue('currency_unproven', page, '财报表段声明未知币种或币种冲突，不能建立明确币种事实')
                    key = (page['page'], table_label, currency['currency'])
                    if not strict and key not in warned_currency:
                        import warnings
                        warnings.warn('MingJing：币种未知或冲突的财报表段未进入事实库（页'
                                      +str(page['page'])+'，币种'+currency['currency']+'）。', RuntimeWarning)
                        warned_currency.add(key)
            if '附注' in text and not _row_account(text) and len(text) < 150:
                note_column = True
            units = re.search(r"(?:金额)?单位[:：](百万元|亿元|万元|元)", text)
            if units:
                unit = units[1]
                unit_evidence = (source_id(page, block),)
                if strict:
                    known_units = set(re.findall(r'(?:金额)?单位[:：](百万元|亿元|万元|元)', text))
                    unit_evidence = (source_id(page, block),)
                    if len(known_units) != 1:
                        unit = ''
                        unit_evidence = ()
                        issue('unit_unproven', page, '同一单位来源块包含冲突单位')
            header_periods = []
            header_forecasts = []
            for match in re.finditer(r"(20\d{2})(?:年(?:(末|度)|(\d{1,2})月\d{1,2}日)?|([AE]))", text, re.I):
                ending, month = match[2], match[3]
                if ending in {"末", "度"}:
                    column_suffix = "Y"
                elif month:
                    column_suffix = {"12": "Y", "6": "H1", "9": "9M", "3": "Q1"}.get(month, "")
                else:
                    column_suffix = suffix
                if column_suffix:
                    header_periods.append(match[1] + column_suffix)
                    header_forecasts.append((match[4] or '').upper() == 'E')
            if strict or re.search(r'20\d{2}年(?:1月?[-—至~～]\d{1,2}月|上半年|半年度|半年|前三季度|第?[一二三四]季度)', text):
                # Explicit cumulative/single-quarter captions use the same
                # semantic parser in ordinary and local-window extraction.
                # A report-period hint must not make two such columns equal.
                header_periods, header_forecasts = _window_header_periods(text, effective_period)
            if strict:
                if re.search(r'20\d{2}', text) and not header_periods and not _row_account(text) and len(text) < 120:
                    # A malformed/unknown-period replacement caption cannot
                    # keep old column context from an earlier header.
                    columns = []
                    column_forecasts = []
                    column_versions = []
                    column_evidence = ()
            if header_periods and not _row_account(text):
                growth_column_index = None
                header_periods, header_forecasts, header_versions = _inline_header_versions(original, header_periods, header_forecasts)
                geometry = _geometry_header_versions(blocks, block, header_periods, header_forecasts)
                if geometry:
                    header_periods, header_forecasts, header_versions, header_proofs = geometry
                else:
                    header_proofs = [block]
                    incomplete = [line for line in block.get('geometry_lines', [])
                                  if re.fullmatch(r'20\d{2}年\d{1,2}月(?:\d{1,2})?', _compact(line['text']))]
                    if incomplete:
                        columns = []
                        column_forecasts = column_versions = []
                        column_centres = []
                        column_evidence = ()
                        issue('period_unproven', page, '跨行日期表头缺少唯一连续来源，不能按PDF文字顺序分配期间')
                        continue
                if strict and len(header_periods) == 1 and len(text) < 60:
                    if header_periods[0] not in columns:
                        columns.append(header_periods[0])
                        column_forecasts.append(header_forecasts[0])
                        column_versions.append(header_versions[0])
                        column_evidence += (source_id(page, block),)
                elif len(header_periods) >= 2:
                    columns = header_periods
                    column_centres = _period_column_centres(block, header_periods)
                    if geometry and len(set(header_periods)) == len(header_periods):
                        starts = [proof for proof in header_proofs if re.match(r'^20\d{2}', _compact(proof['text']))]
                        if len(starts) == len(header_periods):
                            column_centres = [(proof['bbox'][0]+proof['bbox'][2])/2 for proof in starts]
                    column_forecasts = header_forecasts
                    column_versions = header_versions
                    column_evidence = tuple(dict.fromkeys(source_id(page, proof) for proof in header_proofs))
                elif len(header_periods) == 1 and len(text) < 60:
                    if header_periods[0] not in columns:
                        columns.append(header_periods[0])
                        column_forecasts.append(header_forecasts[0])
                        column_versions.append(header_versions[0])
                        column_evidence += (source_id(page, block),)
            elif columns and _VERSION_CAPTION.sub('', text).strip('|/()（）') == '':
                labels = _VERSION_CAPTION.findall(text)
                # Repeated periods can be disambiguated by an equally-sized
                # ordered subheader, with all label cells preserved in proof.
                if len(labels) == len(columns) and len(columns) != len(set(columns)):
                    column_versions = labels
                    if strict:
                        column_evidence += (source_id(page, block),)
            # Explicit current/comparative labels are authorized relative to the
            # document's own period, never to a candidate claim's period.
            if not header_periods and year and "本期" in text and any(label in text for label in ("上年同期", "上年期末", "上期金额")) and len(text) < 100:
                growth_column_index = None
                current_suffix = "Y" if "本期期末" in text and suffix == "Y" else suffix
                previous_suffix = "Y" if "上年期末" in text else suffix
                columns = [year + current_suffix, str(int(year) - 1) + previous_suffix]
                column_centres = _period_column_centres(block, columns)
                column_forecasts = [False, False]
                column_versions = ['', '']
                if strict:
                    column_evidence = (source_id(page, block),) + tuple(period_evidence)
            if "分季度" in table_label and "第一季度" in text:
                columns = [year + f"Q{i}" for i in range(1, 5)] if year else []
                column_forecasts = [False]*len(columns)
                column_versions = ['']*len(columns)
                if strict:
                    column_evidence = (source_id(page, block),) + tuple(period_evidence)
            if not _row_account(text) and _GROWTH_COLUMN.search(text):
                growth_column_index = _growth_column_position(block, columns)
            # A wrapped label/value row may be split into adjacent blocks. Join
            # only coordinates that overlap vertically and lie to its right.
            row_text = text.replace("|", "")
            if block.get("bbox") and not _row_account(row_text):
                box = block["bbox"]
                if any(row_text.startswith(label) or label.startswith(row_text) or label in row_text for label in _LABELS):
                    # Join short label continuations in the same left column
                    # before aligning values to the right of that row.
                    continuations = [b for b in blocks if b is not block and b.get("bbox")
                                     and abs(b["bbox"][0] - box[0]) <= 3 and b["bbox"][2] <= box[2] + 4
                                     and box[3] - 1 <= b["bbox"][1] <= box[3] + 12
                                     and 0 < len(_compact(b["text"])) <= 18
                                     and not re.search(r"\d", b["text"])]
                    if continuations:
                        continuations = sorted(continuations, key=lambda b: b["bbox"][1])
                        row_text += "".join(_compact(b["text"]) for b in continuations)
                        original += "\n" + "\n".join(b["text"].strip() for b in continuations)
                        evidence_blocks.extend(continuations)
                        box = [box[0], box[1], max([box[2]] + [b["bbox"][2] for b in continuations]),
                               max([box[3]] + [b["bbox"][3] for b in continuations])]
                    # A wrapped note reference may share the label's source
                    # block. Its bounds extend into the note column and can
                    # overlap the following note/amount block. Use the proven
                    # label line's right edge rather than the full note bounds;
                    # financial cells still have to occupy this physical row.
                    label_lines = [line for line in block.get('geometry_lines', [])
                                   if any(label in _compact(line['text']) for label in _LABELS)]
                    wrapped_reference = (note_column and source_type == 'primary_statement'
                                         and re.search(r'[一二三四五六七八九十]+[、.．]?[（(]', text))
                    row_box = label_lines[0]['bbox'] if wrapped_reference and len(label_lines) == 1 else box
                    right = [b for b in blocks if b is not block and b.get("bbox")
                             and b["bbox"][0] >= row_box[2] - 2
                             and b["bbox"][1] < row_box[3] and b["bbox"][3] > row_box[1]]
                    if right:
                        row_text += "".join(_compact(b["text"]) for b in sorted(right, key=lambda b: b["bbox"][0]))
                        original += "\n" + "\n".join(b["text"].strip() for b in right)
                        evidence_blocks.extend(right)
                        box = [min([box[0]] + [b["bbox"][0] for b in right]), min([box[1]] + [b["bbox"][1] for b in right]),
                               max([box[2]] + [b["bbox"][2] for b in right]), max([box[3]] + [b["bbox"][3] for b in right])]
                    else:
                        box = block["bbox"]
                else:
                    box = block["bbox"]
            else:
                box = block.get("bbox")
            matched = _row_account(row_text)
            if not matched:
                continue
            if not columns:
                issue('period_unproven', page, '财务行在窗口内缺少可识别的列期间或本期来源锚点')
                continue
            if (len(column_versions) != len(columns)
                    or len(set(zip(columns, column_versions))) != len(columns)):
                issue('period_unproven', page, '重复期间缺少唯一调整前后子表头，不能以金额选择披露版本')
                continue
            label, account, tail = matched
            row_currency = source_currency(original)
            if not row_currency:
                row_currency = next((source_currency(original, match.start('num'))
                    for match in _NUMBER.finditer(original)
                    if source_currency(original, match.start('num'))), None)
            governed_currency = row_currency or table_currency
            if governed_currency and governed_currency['currency'] in {'unknown', 'UNKNOWN', 'mixed', 'MIXED'}:
                issue('currency_unproven', page, '原财务行缺少可用于人民币核查的匹配币种事实或授权汇率转换')
                key = (page['page'], table_label, governed_currency['currency'])
                if not strict and key not in warned_currency:
                    import warnings
                    warnings.warn('MingJing：币种未知或冲突的财报行未进入事实库（页'
                                  +str(page['page'])+'，币种'+governed_currency['currency']+'）。', RuntimeWarning)
                    warned_currency.add(key)
                continue
            row_scope = scope
            row_nr = '扣非' if account == 'NP_PARENT_ADJ' else '未扣非'
            if account in _RATIO_ACCOUNTS:
                qualifier = re.search(re.escape(label) + r'[%％]?[（(](母公司|合并|扣非前|扣非后)[）)]', row_text)
                if qualifier and qualifier[1] in {'母公司', '合并'}:
                    row_scope = qualifier[1]
                if account == 'ROE' and ('扣非后' in row_text or '扣除非经常性损益后的净利润计算' in row_text):
                    row_nr = '扣非'
            # A row-local annotation overrides only this row, never subsequent
            # rows or a different statement block. Ratio rows require their
            # own percent marker rather than inheriting a monetary table unit.
            annotation = re.search(re.escape(label) + r"[（(](百万元|亿元|万元|元(?:/股)?|亿股|万股|股|%|％)[）)]", row_text)
            row_unit = annotation[1] if annotation else unit
            if account == 'WEIGHTED_SHARES':
                # Monetary statement units cannot supply a share denominator.
                # Require a local share caption or explicit units on cells.
                cell_share_units = set(re.findall(r'(?:\d|[.,，])(?:亿股|万股|股)', tail))
                cell_share_units = {re.search(r'亿股|万股|股', value)[0] for value in cell_share_units}
                row_unit = (annotation[1] if annotation[1] in {'股', '万股', '亿股'} else '') if annotation else (
                    next(iter(cell_share_units)) if len(cell_share_units) == 1 else '')
            elif account in {'EPS', 'EPS_DILUTED'}:
                # A statutory EPS caption is per-share, so a statement-wide
                # monetary scale is not inherited. Explicit row/cell scales,
                # however, cannot be overwritten by that canonical unit.
                # Use the original row: compacting adjacent decimal cells can
                # concatenate them and hide a trailing explicit unit. Source
                # cells may separate their amount/unit with space or newline.
                eps_cell_units = {match['unit'] for match in _EPS_EXPLICIT_UNIT.finditer(original)}
                if ((annotation and annotation[1] not in {'元', '元/股'})
                        or eps_cell_units - ({'元', '%', '％'} if eps_growth_evidence else {'元'})):
                    issue('unit_conflict', page, '每股收益行或单元格明示了不支持的单位，不能强制转换为元/股')
                    continue
                row_unit = '元'
                if strict and not annotation and unit != '元' and '元' not in eps_cell_units:
                    row_unit = ''
            elif account in _RATIO_ACCOUNTS:
                row_unit = '%' if (annotation and annotation[1] in {'%', '％'}) or re.search(re.escape(label)+r"[%％]", row_text) or re.search(r"[%％]", tail) else ''
            if not row_unit:
                issue('unit_unproven', page, '财务行在窗口内没有可证明的适用单位')
                continue
            # Separate numeric cells before whitespace normalization. For normal
            # PDF blocks numbers are newline-delimited. Percent growth cells are
            # discarded, never mistaken for amounts or extra periods.
            values = list(_NUMBER.finditer(tail))
            values = [m for m in values if account in _RATIO_ACCOUNTS or m["unit"] not in {"%", "％"}]
            if account != 'WEIGHTED_SHARES' and (row_unit in {'股', '万股', '亿股'} or
                    any(match['unit'] in {'股', '万股', '亿股'} for match in values)):
                issue('unit_conflict', page, '金额、比例或每股收益科目不能消费股数单位')
                continue
            # Compacting adjacent cells can concatenate numbers. Parse the
            # original row after removing known label/prefix where possible.
            original_values = list((_EPS_CELL_NUMBER if account in {'EPS', 'EPS_DILUTED'} else _NUMBER).finditer(original))
            # A numeric row prefix (e.g. 2.归属于...) is not a cell. Numbers
            # before the label and within footnote citations are excluded.
            label_end = 0
            accumulated = ""
            for offset, char in enumerate(original):
                if not char.isspace():
                    accumulated += char
                if accumulated.endswith(label):
                    label_end = offset + 1
                    break
            original_values = [m for m in original_values if m.start() >= label_end and (account in _RATIO_ACCOUNTS or m["unit"] not in {"%", "％"})]
            cell_start = label_end
            # Statutory loss wording can sit between the account caption and
            # its note reference. It is already authorized by _row_account;
            # consume the same source suffix before identifying the reference.
            for suffix_pattern in (
                    r'\s*[（(](?:净亏损|亏损|损失)[^）)]{0,24}[）)]',
                    r'\s*[（(](?:百万元|亿元|万元|元(?:/股)?|亿股|万股|股|%|％)[）)]'):
                caption = re.match(suffix_pattern, original[cell_start:])
                if caption:
                    cell_start += caption.end()
            note_reference = re.match(r'\s*' + _ROW_NOTE_REFERENCE.pattern, original[cell_start:])
            if note_reference:
                cell_start += note_reference.end()
                original_values = [match for match in original_values if match.start() >= cell_start]
            if original_values or note_reference:
                values = original_values
            if (note_column and source_type == 'primary_statement' and len(values) == len(columns)+1
                    and re.fullmatch(r'\d{1,2}\.\d{1,2}', values[0]['num']) and not values[0]['unit']):
                # The explicit 附注 caption and surplus first cell establish a
                # reference column; the reference never becomes an operand.
                values = values[1:]
            # Alignment must use the same proven cell boundary as numeric
            # extraction. Re-tokenizing from the label would put a removed
            # Arabic note index back into a blank financial column.
            aligned = _align_known_cells(original, cell_start, values, columns, column_centres,
                                         block.get('geometry_lines', []), growth_column_index)
            if aligned is None:
                issue('unsupported_layout', page, '财务行金额单元格数量与窗口内已证明的列期间不一致')
                continue
            values = aligned
            if strict and any(match['unit'] and match['unit'].replace('％', '%') != row_unit.replace('/股', '').replace('％', '%')
                              for match in values if match is not None):
                issue('unit_conflict', page, '金额单元格单位与行或表头声明单位冲突')
                continue
            # Keep the original individual spans so every id can be found in
            # build_evidence(), including a label/value row split by the PDF.
            evidence_ids = tuple(dict.fromkeys(make_evidence_id(doc_sha, page["page"], b.get("evidence_text", b["text"]).strip(), b.get("evidence_bbox", b.get("bbox")))
                                              for b in evidence_blocks))
            local_unit_proof = evidence_ids if annotation or account in _RATIO_ACCOUNTS or account == 'WEIGHTED_SHARES' else unit_evidence
            evidence_ids = tuple(dict.fromkeys(evidence_ids + heading_evidence + tuple(local_unit_proof)
                                                + column_evidence + tuple(period_evidence)
                                                + (eps_growth_evidence if account in {'EPS', 'EPS_DILUTED'} | _RATIO_ACCOUNTS else ())))
            for col_index, (column, match) in enumerate(zip(columns, values), 1):
                if match is None:
                    issue('missing_cell', page, '缺失单元格不作为零值；同一行其他可定位单元格独立保留')
                    continue
                if column_forecasts[col_index-1]:
                    # Keep the E cell's position for alignment, but never
                    # promote a forecast into disclosed historical facts.
                    continue
                raw = _decimal(match["num"])
                display_unit = match["unit"] or row_unit.replace('/股', '')
                value = Decimal(raw) * _UNITS.get(display_unit, Decimal("1"))
                fact_id = f"{doc_sha[:12]}-{account}-{column}-p{page['page']}-{len(facts)+1}"
                if strict:
                    digest = hashlib.sha256(json.dumps([account, column, scope, str(value), evidence_ids], ensure_ascii=False).encode('utf-8')).hexdigest()[:16]
                    fact_id = f"{doc_sha[:12]}-{account}-{column}-p{page['page']}-w{digest}"
                facts.append(Fact(fact_id, company,
                                  account, column, scope=row_scope, nr=row_nr,
                                  version=disclosure_version + ('/' + column_versions[col_index-1] if column_versions[col_index-1] else ''), source_doc=doc_sha[:12], publish_date=disclosure_date, value=value,
                                  decimals=len(raw.partition(".")[2]), display_unit=display_unit,
                                  evidence=evidence_ids, label=label, source_path=str(path),
                                  source_type=source_type, table_label=table_label, column_label=column + ('（' + column_versions[col_index-1] + '）' if column_versions[col_index-1] else ''),
                                  parse_conf='0.99' if box else '0.95', extractor_version='financial-window-v1' if strict else 'financial-tables-v2',
                                  version_group=doc_sha[:12], currency=(governed_currency or {}).get('currency', 'CNY')))
    from .financial_notes import extract_note_facts
    facts.extend(extract_note_facts(pages, path=path, company=company, effective_period=effective_period,
                                   doc_sha=doc_sha, disclosure_date=disclosure_date,
                                   disclosure_version=disclosure_version, strict=strict, issues=window_issues,
                                   **control))
    return FactStore(facts).facts


def _window_header_periods(text: str, source_period: str) -> tuple[list[str], list[bool]]:
    """Recognize financial column captions without a request-period default."""
    from .mentions import period_from_text
    pattern = re.compile(
        r'(20\d{2})(?:([AE])|年(?:\d{1,2}月\d{1,2}日|1月?[-—至~～]\d{1,2}月|'
        r'上半年|下半年|半年度|半年|前三季度|第?[一二三四]季度|度|末)?)', re.I)
    matches = list(pattern.finditer(text))
    if not matches:
        return [], []
    residue = pattern.sub('', text)
    residue = _GROWTH_COLUMN.sub('', residue)
    residue = re.sub(r'(?:金额)?单位[:：](?:百万元|亿元|万元|元)', '', residue)
    residue = re.sub(r'本年比上年增减|本期比上期增减|本报告期比上年同期增减|'
                     r'上年同期|本报告期|调整前|调整后|重述前|重述后|更正前|更正后|项目|附注|年度|日期|金额|增减|本期|上期', '', residue)
    if residue.strip('|%％:：/()（）+-—'):
        return [], []
    periods, forecasts = [], []
    for match in matches:
        token = match[0]
        if match[2]:
            period = match[1] + 'Y'
        else:
            interval = re.fullmatch(r'20\d{2}年1月?[-—至~～](\d{1,2})月', token)
            if interval:
                suffix = {3: 'Q1', 6: 'H1', 9: '9M', 12: 'Y'}.get(int(interval[1]), '')
                period = match[1] + suffix if suffix else ''
            else:
                period = period_from_text(token)
                # The document period comes only from a qualified report
                # caption physically present in this same page window.
                if token == match[1] + '年' and source_period:
                    period = match[1] + source_period[4:]
        if not period:
            return [], []
        periods.append(period)
        forecasts.append((match[2] or '').upper() == 'E')
    return periods, forecasts


def _window_report_anchor(pages: list[dict], doc_sha: str) -> tuple[str, tuple[str, ...]]:
    """Only source-written report captions authorize relative period labels."""
    from .mentions import period_from_text
    anchors = []
    for page in pages:
        for block in page['blocks']:
            text = _compact(block['text'])
            matches = list(re.finditer(r'20\d{2}年(?:年度|半年度|第?[一二三四]季度|前三季度)报告', text))
            for match in matches:
                period = period_from_text(match[0])
                if period:
                    anchors.append((period, make_evidence_id(doc_sha, page['page'], block['text'].strip(), block['bbox'])))
    if len({period for period, _ in anchors}) != 1:
        return '', ()
    return anchors[0][0], tuple(dict.fromkeys(identity for _, identity in anchors))


def _window_company_guard(path: Path, company: str, pages: list[dict]) -> str:
    """Use the trusted issuer lock, but reject contrary explicit identities."""
    if not isinstance(company, str) or not _compact(company):
        raise ValueError('局部重解析必须有已锁定的公司身份')
    identified = _company_from_path(path)
    if identified:
        if not _company_matches(company, identified[1], identified[0]):
            raise ValueError(f'所选报告属于 {identified[1]}，不能改名为 {company}')
        issuer = identified[1]
    else:
        issuer = company.strip()

    def normalize(name):
        name = _compact(name).strip(':：;；,，。')
        return re.sub(r'(?:股份有限公司|有限责任公司|有限公司)$', '', name)

    for page in pages:
        for block in page['blocks']:
            for line in block['text'].splitlines():
                text = _compact(line)
                explicit = re.search(r'(?:编制单位|报告主体|报告公司|公司名称)[:：]([^;；,，。]+)', text)
                if explicit:
                    name = re.split(r'20\d{2}年|单位[:：]', explicit[1])[0]
                    if normalize(name) != normalize(issuer):
                        raise ValueError(f'窗口明示公司 {name} 与锁定公司 {issuer} 不一致')
                # A named statement title must not be silently assigned to
                # the task issuer. Ordinary ordinal headings contain no name.
                titled = re.match(r'^(.+?)(?:合并|母公司)(?:资产负债表|利润表|现金流量表)$', text)
                if titled:
                    prefix = re.sub(r'^[（(]?[一二三四五六七八九十\d]+[）).、．]*', '', titled[1])
                    prefix = re.sub(r'^(?:合并|母公司)[及、/]', '', prefix)
                    if prefix and normalize(prefix) != normalize(issuer):
                        raise ValueError(f'窗口报表属于 {prefix}，不能改名为 {issuer}')
    return issuer


def extract_fact_window(path: Path, *, pages: Iterable[int], company: str,
                        report_period: str = '', publish_date: str = '',
                        expected_sha256: str, cancel_event=None, progress_callback=None,
                        deadline_monotonic=None) -> dict:
    """Read at most three consecutive original PDF pages into a fact proposal.

    ``company`` and ``publish_date`` come from the host's frozen resource lock.
    ``report_period`` is a retrieval hint, never a default for financial cells.
    This function neither edits the source nor loads the returned facts into a
    live store. Missing local dimension evidence is an issue, not a guessed fact.
    Invalid locks/requests raise ValueError; cancellation/deadline propagate.
    """
    control = _control_options(cancel_event, progress_callback, deadline_monotonic)
    _parse_checkpoint(**control)
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower() != '.pdf':
        raise ValueError('局部重解析只接受已锁定的原始 PDF 文件')
    if not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256):
        raise ValueError('局部重解析必须提供完整 SHA-256 资源锁')
    expected_sha256 = expected_sha256.lower()
    if not isinstance(publish_date, str):
        raise ValueError('局部重解析必须有已知披露日期')
    try:
        if date.fromisoformat(publish_date).isoformat() != publish_date:
            raise ValueError
    except ValueError:
        raise ValueError('局部重解析必须有已知且有效的 ISO 披露日期') from None
    selected = []
    for number in pages:
        if type(number) is not int or number < 1:
            raise ValueError('局部重解析页码必须是正整数物理页号')
        selected.append(number)
        if len(selected) > 3:
            raise ValueError('局部重解析最多读取三张连续物理页面')
    selected.sort()
    if not selected or selected != list(range(selected[0], selected[0] + len(selected))):
        raise ValueError('局部重解析页码必须是非空、无重复且连续的窗口')
    issuer = _window_company_guard(path, company, [])
    _parse_checkpoint(**control, phase='校验局部重解析材料', document=path.name, pages=selected)
    if _file_hash(path, **control) != expected_sha256:
        raise ValueError('局部重解析 PDF SHA-256 与资源锁不一致')
    output = {'facts': [], 'source_pages': [], 'issues': []}
    try:
        import fitz
    except ImportError:
        output['issues'].append({'code': 'parser_unavailable', 'reason': '局部重解析需要 PyMuPDF 坐标文本层；不能降级猜测'})
        return output
    window = []
    try:
        with fitz.open(path) as doc:
            if not doc.is_pdf or doc.needs_pass:
                output['issues'].append({'code': 'unreadable_pdf', 'reason': '材料不是可读取的未加密 PDF'})
                return output
            if selected[-1] > len(doc):
                raise ValueError('局部重解析物理页号超出已锁定 PDF 范围')
            for number in selected:
                _parse_checkpoint(**control, phase='解析局部PDF页面', document=path.name, page=number, pages=len(selected))
                _parse_checkpoint(**control)
                page = doc[number - 1]
                text = page.get_text()
                blocks = []
                for block in page.get_text('blocks'):
                    _parse_checkpoint(**control)
                    # PyMuPDF image blocks are descriptions, not financial
                    # text. They must never establish a scanned page's facts.
                    if len(block) > 6 and block[6] != 0:
                        continue
                    blocks.append({'bbox': list(block[:4]), 'text': block[4]})
                lines = []
                for block in page.get_text('dict')['blocks']:
                    _parse_checkpoint(**control)
                    if block.get('type') != 0:
                        continue
                    for line in block.get('lines', []):
                        line_text = ''.join(span['text'] for span in line.get('spans', []))
                        if line_text:
                            lines.append({'text': line_text, 'bbox': list(line['bbox'])})
                text_layer = bool(text.strip() and any(b['text'].strip() for b in blocks))
                output['source_pages'].append({'page': number, 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'text_layer': text_layer})
                if not text_layer:
                    output['issues'].append({'code': 'no_text_layer', 'page': number, 'reason': '窗口页面没有可靠坐标文本层；不运行 OCR 或猜测事实'})
                window.append({'page': number, 'text': text if text_layer else '', 'blocks': blocks if text_layer else [], 'lines': lines})
    except (InterruptedError, TimeoutError, ValueError):
        raise
    except Exception as exc:
        output['issues'].append({'code': 'pdf_parse_failed', 'reason': '原始 PDF 页面无法可靠解析', 'exception_type': type(exc).__name__})
        return output
    issuer = _window_company_guard(path, issuer, window)
    source_period, period_evidence = _window_report_anchor(window, expected_sha256)
    disclosure_version = '更正披露' if any(token in path.stem for token in ('更正', '修订', '重述')) else '原披露'
    facts = _extract_table_facts(window, path=path, company=issuer, effective_period=source_period,
                                doc_sha=expected_sha256, disclosure_date=publish_date,
                                disclosure_version=disclosure_version, window_issues=output['issues'],
                                period_evidence=period_evidence, **control)
    _parse_checkpoint(**control, phase='校验局部事实提案', document=path.name, selected_facts=len(facts))
    if _file_hash(path, **control) != expected_sha256:
        raise ValueError('局部重解析过程中原始 PDF 已变更，事实提案作废')
    from .facts import FactStore
    store = FactStore(facts)
    rejected = set()
    def fact_dimensions(fact):
        return (fact.company, fact.account, fact.period, fact.scope, fact.nr,
                fact.dimension_kind, fact.dimension_id, fact.currency, fact.definition_id)
    for fact in facts:
        key = fact_dimensions(fact)
        if key in rejected:
            continue
        conflict = store.query_issue(fact.company, fact.account, fact.period, scope=fact.scope, nr=fact.nr,
                                     cutoff=publish_date, dimension_kind=fact.dimension_kind,
                                     dimension_id=fact.dimension_id, currency=fact.currency,
                                     definition_id=fact.definition_id)
        if conflict:
            rejected.add(key)
            output['issues'].append({'code': 'fact_conflict', 'company': fact.company,
                                     'account': fact.account, 'period': fact.period,
                                      'scope': fact.scope, 'nr': fact.nr,
                                      'dimension_kind': fact.dimension_kind, 'dimension_id': fact.dimension_id,
                                      'currency': fact.currency, 'definition_id': fact.definition_id,
                                      'reason': '同一局部披露中的事实内部冲突，不能选择相近值'})
    facts = [fact for fact in facts if fact_dimensions(fact) not in rejected]
    output['facts'] = facts
    if not facts and not output['issues']:
        output['issues'].append({'code': 'no_supported_facts', 'reason': '窗口内没有满足单位、口径、期间和行列证据要求的财务事实'})
    return output


def load_or_extract_facts(corpus: str | Path, company: str = "", period: str = "", publish_date: str = "", *,
                         cancel_event=None, progress_callback=None, deadline_monotonic=None) -> list[Fact]:
    root = Path(corpus)
    control = _control_options(cancel_event, progress_callback, deadline_monotonic)
    _parse_checkpoint(**control, phase='读取语料清单', company=company)
    if root.is_file() and root.suffix.lower() in {'.json', '.jsonl'}:
        store = root
    else:
        store = next((path for path in (root / 'facts.json', root / 'facts.jsonl', root / 'fact_store.json')
                      if path.is_file()), root / 'facts.json')
    indexed = None
    structured: list[Fact] = []
    if store.is_file():
        raw = _read_text(store, **control)
        payload = ([json.loads(line) for line in raw.splitlines() if line.strip()]
                   if store.suffix.lower() == '.jsonl' else json.loads(raw))
        _parse_checkpoint(**control)
        rows = payload.get("facts", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('事实索引必须包含事实对象数组')
        from .source_cache import cache_admission, is_machine_fact
        admission = cache_admission(payload, rows, **control)
        declared_cache = isinstance(payload, dict) and 'source_fact_cache' in payload
        # A directly selected index is still restricted to its own material
        # directory. It cannot bypass admission via the file-input API.
        root = store.parent
        selected_rows = [row for row in rows if not company or row.get('company') == company]
        if admission['status'] == 'accepted':
            source_hashes = {}
            for row in selected_rows:
                if not is_machine_fact(row, declared_cache=declared_cache):
                    continue
                if not row.get('source_path'):
                    admission = {'status': 'reparse', 'reason': 'cache_source_identity_missing'}
                    break
                source = Path(row['source_path'])
                resolved = (root / source).resolve() if not source.is_absolute() else source.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    raise ValueError('facts.json 来源路径越过语料根目录或通过符号链接逃逸')
                if not resolved.is_file():
                    admission = {'status': 'reparse', 'reason': 'cache_source_missing'}
                    break
                if resolved not in source_hashes:
                    source_hashes[resolved] = _file_hash(resolved, **control)
                source_hash = source_hashes[resolved]
                if row.get('source_doc') not in {source_hash, source_hash[:12]}:
                    admission = {'status': 'reparse', 'reason': 'cache_source_bytes_changed'}
                    break
        if admission['status'] == 'reparse':
            indexed = _indexed_files(root, company, **control)
            if indexed is None or not any(path.suffix.lower() == '.pdf' for path, *_ in indexed):
                _parse_checkpoint(**control, phase='事实缓存准入', cache=str(store),
                                  cache_status='rejected', cache_reason=admission['reason'])
                raise ValueError('机器抽取事实缓存缺少有效提取器锁及可重建的冻结 index.csv/PDF；请重新建库')
            allowed = {path.resolve() for path, *_ in indexed}
            for row in selected_rows:
                if not is_machine_fact(row, declared_cache=declared_cache) or not row.get('source_path'):
                    continue
                source = Path(row['source_path'])
                resolved = (root / source).resolve() if not source.is_absolute() else source.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    raise ValueError('facts.json 来源路径越过语料根目录或通过符号链接逃逸')
                if resolved not in allowed or resolved.suffix.lower() != '.pdf':
                    raise ValueError('机器抽取缓存的来源 PDF 未在冻结 index.csv 内，无法可靠重建；请重新建库')
            _parse_checkpoint(**control, phase='事实缓存准入', cache=str(store),
                              cache_status='reparse_read_only', cache_reason=admission['reason'],
                              cache_module=admission.get('module', ''))
            selected_rows = [row for row in selected_rows if not is_machine_fact(row, declared_cache=declared_cache)]
        else:
            _parse_checkpoint(**control, phase='事实缓存准入', cache=str(store),
                              cache_status=admission['status'], cache_reason=admission['reason'])
        from .facts import fact_from_dict
        selected = []
        for number, item in enumerate(selected_rows, 1):
            _parse_checkpoint(**control, phase='读取已选事实索引' if number == 1 or number % 256 == 0 else None,
                              fact_rows=number, selected_facts=len(selected), company=company)
            fact = fact_from_dict(item)
            if fact.source_path:
                source = Path(fact.source_path)
                resolved = (root / source).resolve() if not source.is_absolute() else source.resolve()
                if not resolved.is_relative_to(root.resolve()):
                    raise ValueError("facts.json 来源路径越过语料根目录或通过符号链接逃逸")
                if not resolved.is_file():
                    raise ValueError(f"facts.json 来源文件不存在：{resolved.name}")
                fact = replace(fact, source_path=str(resolved))
            selected.append(fact)
        _parse_checkpoint(**control, phase='事实索引读取完成', selected_facts=len(selected), company=company)
        if admission['status'] != 'reparse':
            return selected
        structured = selected
    facts: list[Fact] = list(structured)
    if indexed is None:
        indexed = _indexed_files(root, company, **control)
    if indexed is not None:
        _parse_checkpoint(**control, phase='语料索引筛选完成', selected_documents=len(indexed), company=company)
        for number, (path, issuer, indexed_period, indexed_date) in enumerate(indexed, 1):
            _parse_checkpoint(**control, phase='读取已选财报', document=path.name,
                              document_index=number, document_count=len(indexed), selected_facts=len(facts))
            facts.extend(extract_facts(path, issuer, indexed_period or period, indexed_date or publish_date, **control))
    else:
        paths = _unindexed_files(root, company, **control)
        for number, path in enumerate(paths, 1):
            _parse_checkpoint(**control)
            identified = _company_from_path(path)
            if company and identified and not _company_matches(company, identified[1], identified[0]):
                if root.is_file():
                    raise ValueError(f"所选报告属于 {identified[1]}，与核查公司 {company} 不一致")
                continue
            if not root.is_file() and len(paths) > 1 and company and not identified:
                # A company supplied for a folder cannot authorize relabeling
                # every unidentified report inside it as that issuer.
                continue
            issuer = identified[1] if identified else company
            _parse_checkpoint(**control, phase='读取已选财报', document=path.name,
                              document_index=number, document_count=len(paths), selected_facts=len(facts))
            facts.extend(extract_facts(path, issuer, period, publish_date, **control))
    from .facts import FactStore
    _parse_checkpoint(**control, phase='财报事实抽取完成', selected_facts=len(facts), company=company)
    return FactStore(facts).facts
