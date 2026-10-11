"""多后端文档读取：统一成“版面行（rows）+ 阅读序文本（flow）”两种视图。

PDF 后端顺序：PyMuPDF（坐标行聚类）→ poppler pdftotext（-layout 行 + 阅读序）→ pdfplumber。
表格解析只依赖 rows；正文陈述抽取只依赖 flow。任何后端失败都降级而不是猜测。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree

from . import PARSER_VERSION
from .common import norm

_DATE_GLUE = re.compile(r'(?<=\d) {1,2}(?=(?:年|月|日|季度|年度|年末|年底))|(?<=[年月]) {1,2}(?=\d{1,2} {0,2}[月日])')
_CJK = r'\u4e00-\u9fff'
_CJK_GLUE = re.compile(rf'(?<=[{_CJK}])[ \t]+(?=[{_CJK}])')


def tidy(text: str) -> str:
    """只做不改变语义的版面归一：半角化、日期内空格、汉字间空格。"""
    text = norm(text)
    text = _DATE_GLUE.sub('', text)
    return text


@dataclass
class Cell:
    text: str
    x0: float
    x1: float


@dataclass
class Row:
    page: int
    index: int
    cells: list[Cell]
    y0: float | None = None
    y1: float | None = None
    geometry: bool = False

    @property
    def text(self) -> str:
        return '  '.join(cell.text for cell in self.cells)

    @property
    def bbox(self):
        if not self.geometry or not self.cells:
            return None
        return [min(c.x0 for c in self.cells), self.y0, max(c.x1 for c in self.cells), self.y1]


@dataclass
class Page:
    number: int
    rows: list[Row] = field(default_factory=list)
    flow: str = ''
    vector_lines: list = field(default_factory=list)
    vector_rectangles: list = field(default_factory=list)
    geometry_lines: list = field(default_factory=list)


@dataclass
class Document:
    path: str
    name: str
    sha256: str
    kind: str
    backend: str
    pages: list[Page] = field(default_factory=list)
    parser_version: str = PARSER_VERSION

    def to_json(self) -> dict:
        return {'path': self.path, 'name': self.name, 'sha256': self.sha256, 'kind': self.kind, 'backend': self.backend,
                'parser_version': self.parser_version,
                'pages': [{'number': p.number, 'flow': p.flow,
                           'vector_lines': p.vector_lines, 'vector_rectangles': p.vector_rectangles,
                           'geometry_lines': p.geometry_lines,
                           'rows': [{'i': r.index, 'y0': r.y0, 'y1': r.y1, 'g': r.geometry,
                                     'c': [[c.text, c.x0, c.x1] for c in r.cells]} for r in p.rows]}
                          for p in self.pages]}

    @classmethod
    def from_json(cls, data: dict) -> 'Document':
        pages = [Page(p['number'], [Row(p['number'], r['i'], [Cell(*c) for c in r['c']], r.get('y0'), r.get('y1'), bool(r.get('g')))
                                    for r in p['rows']], p.get('flow', ''), p.get('vector_lines', []),
                      p.get('vector_rectangles', []), p.get('geometry_lines', [])) for p in data['pages']]
        return cls(data['path'], data['name'], data['sha256'], data['kind'], data['backend'], pages, data.get('parser_version', ''))

    @property
    def char_count(self) -> int:
        return sum(len(p.flow) for p in self.pages)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_path(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


# ───────────────────────── 行聚类（坐标后端共用） ─────────────────────────
def _DATE_PIECE(left: str, right: str) -> bool:
    """坐标后端会把“2023 年 12 月 31 日”切成碎片；只合并日期/期间碎片，不合并普通单元格。"""
    left, right = left.strip(), right.strip()
    if re.fullmatch(r'(?:.*\D)?(?:19|20)\d{2}', left) and re.match(r'(?:年|年度|年末|Q[1-4]|H[12])', right):
        return True
    if re.search(r'[年月]$', left) and re.fullmatch(r'\d{1,2}(?:月|日|月\d{1,2}日?)?(?:[)）])?', right):
        return True
    if re.search(r'\d{1,2}$', left) and re.search(r'[年月]\d{1,2}$', left) and re.match(r'(?:月|日)', right):
        return True
    return bool(re.fullmatch(r'.*[年月]\d{1,2}', left) and right in ('月', '日', '月份'))


def rows_from_chunks(page: int, chunks: list[tuple[str, float, float, float, float]]) -> list[Row]:
    """把带坐标的文本块（PyMuPDF line / pdfplumber word）按纵向重叠聚成版面行。"""
    items = [(tidy(text).strip(), x0, y0, x1, y1) for text, x0, y0, x1, y1 in chunks if text and text.strip()]
    items.sort(key=lambda item: ((item[2] + item[4]) / 2, item[1]))
    groups: list[list] = []
    for item in items:
        _, _, y0, _, y1 = item
        height = max(y1 - y0, 0.1)
        placed = False
        for group in reversed(groups[-3:]):
            g0, g1 = group[0], group[1]
            overlap = min(y1, g1) - max(y0, g0)
            if overlap >= 0.5 * min(height, g1 - g0):
                group[2].append(item)
                group[0], group[1] = min(g0, y0), max(g1, y1) if (y1 - y0) < 2.5 * (g1 - g0) else g1
                placed = True
                break
        if not placed:
            groups.append([y0, y1, [item]])
    rows: list[Row] = []
    for index, (g0, g1, members) in enumerate(sorted(groups, key=lambda g: (g[0] + g[1]) / 2)):
        members.sort(key=lambda item: item[1])
        cells: list[Cell] = []
        for text, x0, y0, x1, y1 in members:
            gap_limit = 0.45 * max(y1 - y0, 1)
            if cells and x0 - cells[-1].x1 < 1.6 * max(y1 - y0, 1) and _DATE_PIECE(cells[-1].text, text):
                cells[-1] = Cell(tidy(cells[-1].text + text), cells[-1].x0, max(cells[-1].x1, x1))
                continue
            if cells and x0 - cells[-1].x1 < gap_limit:
                joiner = '' if re.search(rf'[{_CJK}]$', cells[-1].text) and re.match(rf'[{_CJK}]', text) else ' '
                cells[-1] = Cell(cells[-1].text + joiner + text, cells[-1].x0, max(cells[-1].x1, x1))
            else:
                cells.append(Cell(text, x0, x1))
        rows.append(Row(page, index, cells, g0, g1, True))
    return rows


def rows_from_layout_text(page: int, text: str) -> list[Row]:
    rows = []
    for index, line in enumerate(text.splitlines()):
        line = tidy(line.rstrip())
        if not line.strip():
            continue
        cells = [Cell(match[0].strip(), float(match.start()), float(match.end()))
                 for match in re.finditer(r'\S+(?: \S+)*', line)]
        rows.append(Row(page, index, cells, None, None, False))
    return rows


# ───────────────────────── PDF 后端 ─────────────────────────
def _pdf_fitz(path: Path) -> list[Page] | None:
    try:
        import fitz  # noqa: F401
    except Exception:
        return None
    from ..parser import extract_text
    pages = []
    for raw in extract_text(path):
        lines = raw.get('lines')
        if lines is None:
            return None
        chunks = [(line['text'], *line['bbox']) for line in lines if line.get('bbox')]
        pages.append(Page(raw['page'], rows_from_chunks(raw['page'], chunks), tidy(raw.get('text', '')),
                          raw.get('vector_lines', []), raw.get('vector_rectangles', []), lines))
    return pages


def _run(command: list[str]) -> str:
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    result = subprocess.run(command, capture_output=True, check=True, timeout=300, creationflags=flags)
    return result.stdout.decode('utf-8', errors='replace')


def _pdf_poppler(path: Path) -> list[Page] | None:
    if not shutil.which('pdftotext'):
        return None
    try:
        layout = _run(['pdftotext', '-layout', str(path), '-']).split('\f')
        flow = _run(['pdftotext', str(path), '-']).split('\f')
    except Exception:
        return None
    count = max(len(layout), len(flow))
    pages = []
    for number in range(1, count + 1):
        layout_text = layout[number - 1] if number <= len(layout) else ''
        flow_text = flow[number - 1] if number <= len(flow) else layout_text
        if number == count and not layout_text.strip() and not flow_text.strip():
            continue
        pages.append(Page(number, rows_from_layout_text(number, layout_text), tidy(flow_text)))
    return pages


def _pdf_plumber(path: Path) -> list[Page] | None:
    try:
        import pdfplumber
    except Exception:
        return None
    pages = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for number, page in enumerate(pdf.pages, 1):
                words = page.extract_words(keep_blank_chars=False, x_tolerance=2, y_tolerance=2)
                chunks = [(w['text'], w['x0'], w['top'], w['x1'], w['bottom']) for w in words]
                pages.append(Page(number, rows_from_chunks(number, chunks), tidy(page.extract_text() or '')))
    except Exception:
        return None
    return pages


def _docx_pages(path: Path) -> list[Page]:
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read('word/document.xml'))
    body = root.find(ns + 'body')
    rows, flow, index = [], [], 0

    def para_text(node):
        return ''.join(t.text or '' for t in node.iter(ns + 't'))
    for child in list(body) if body is not None else []:
        if child.tag == ns + 'p':
            text = tidy(para_text(child)).strip()
            if text:
                rows.append(Row(1, index, [Cell(text, 0.0, float(len(text)))])); flow.append(text); index += 1
        elif child.tag == ns + 'tbl':
            for tr in child.iter(ns + 'tr'):
                cells, x = [], 0.0
                for tc in tr.findall(ns + 'tc'):
                    text = tidy(' '.join(para_text(p) for p in tc.iter(ns + 'p'))).strip()
                    cells.append(Cell(text or '-', x, x + max(len(text), 1))); x += 100
                if cells:
                    rows.append(Row(1, index, cells)); flow.append('  '.join(c.text for c in cells)); index += 1
    return [Page(1, rows, '\n'.join(flow))]


def _text_pages(path: Path) -> list[Page]:
    raw = path.read_text(encoding='utf-8-sig', errors='replace')
    pages = []
    for number, chunk in enumerate(raw.split('\f'), 1):
        rows = []
        for index, line in enumerate(chunk.splitlines()):
            line = tidy(line)
            if not line.strip() or re.fullmatch(r'\s*\|?[\s:|-]+\|?\s*', line):
                continue
            if line.count('|') >= 2:   # Markdown 表格行
                parts = [part.strip() for part in line.strip().strip('|').split('|')]
                cells = [Cell(part or '-', float(i * 100), float(i * 100 + len(part))) for i, part in enumerate(parts)]
            else:
                cells = [Cell(m[0].strip(), float(m.start()), float(m.end())) for m in re.finditer(r'\S+(?: \S+)*', line)]
            rows.append(Row(number, index, cells))
        pages.append(Page(number, rows, tidy(chunk)))
    return pages


def body_lines(page: Page) -> list[str]:
    """按版面列重建正文阅读序：识别左侧行情/目录侧栏，只把正文列的单元格按行拼接。

    研报首页常见“左侧栏 + 右侧正文”的双栏版式，阅读序文本会把侧栏数字插进正文句子中间。
    这里用正文长单元格的起始横坐标众数确定正文列，侧栏内容单独成行附在页末。
    """
    rows = page.rows
    if not rows:
        return [line for line in page.flow.splitlines() if line.strip()]
    geometry = any(row.geometry for row in rows)
    unit = 12.0 if geometry else 3.0
    starts = [round(cell.x0 / unit) for row in rows for cell in row.cells if len(re.findall(r'[\u4e00-\u9fff]', cell.text)) >= 8]
    if len(starts) < 4:
        return [row.text for row in rows]
    from collections import Counter
    counts = Counter(starts)
    bucket, hits = max(counts.items(), key=lambda kv: (kv[1] + counts.get(kv[0] - 1, 0) + counts.get(kv[0] + 1, 0), -kv[0]))
    share = (hits + counts.get(bucket - 1, 0) + counts.get(bucket + 1, 0)) / len(starts)
    left_edge = min(cell.x0 for row in rows for cell in row.cells)
    body_x0 = (bucket - 1) * unit
    if share < 0.45 or body_x0 - left_edge < 6 * unit:
        return [row.text for row in rows]
    body, side = [], []
    for row in rows:
        main = [cell.text for cell in row.cells if cell.x0 >= body_x0 - unit]
        rest = [cell.text for cell in row.cells if cell.x0 < body_x0 - unit]
        if main:
            body.append(' '.join(main))
        if rest:
            side.append(' '.join(rest))
    return body + [''] + side


SUPPORTED = {'.pdf', '.md', '.txt', '.docx', '.json'}


def available_backends() -> list[str]:
    """当前环境可用的 PDF 文本后端（按优先顺序）。"""
    names = []
    try:
        import fitz  # noqa: F401
        names.append('fitz')
    except Exception:
        pass
    if shutil.which('pdftotext'):
        names.append('poppler')
    try:
        import pdfplumber  # noqa: F401
        names.append('plumber')
    except Exception:
        pass
    return names


def load_document(path: str | Path, *, sha256: str | None = None, prefer: str | None = None) -> Document:
    path = Path(path)
    suffix = path.suffix.lower()
    digest = sha256 or sha256_path(path)
    prefer = prefer or os.environ.get('MINGJING_PDF_BACKEND') or None     # fitz / poppler / plumber
    if suffix == '.pdf':
        order = {'fitz': _pdf_fitz, 'poppler': _pdf_poppler, 'plumber': _pdf_plumber}
        names = [prefer] if prefer in order else []
        names += [name for name in order if name not in names]
        for name in names:
            pages = order[name](path)
            if pages and any(page.rows or page.flow.strip() for page in pages):
                return Document(str(path), path.name, digest, 'pdf', name, pages)
        return Document(str(path), path.name, digest, 'pdf', 'none', [])
    if suffix == '.docx':
        return Document(str(path), path.name, digest, 'docx', 'docx-xml', _docx_pages(path))
    if suffix in {'.md', '.txt'}:
        return Document(str(path), path.name, digest, 'text', 'text', _text_pages(path))
    if suffix == '.json':
        return Document(str(path), path.name, digest, 'json', 'json', [Page(1, [], path.read_text(encoding='utf-8-sig', errors='replace')[:2000])])
    raise ValueError(f'不支持的文件类型：{suffix or path.name}')


def dump_document(doc: Document, target: Path, code: str = '') -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    data = doc.to_json()
    data['code'] = code
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
