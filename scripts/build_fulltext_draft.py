#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Build a large-check draft from the full text of a broker research PDF.

This script extracts *sentences that quote financial numbers* from a
broker research report PDF (text layer) and writes them as a numbered
draft consumed by ``mingjing.cli check``.

Extraction rules
----------------
* Read every page's text layer with ``pymupdf`` (import name ``pymupdf``).
* Reconstruct prose sentences from the soft-wrapped text layer: lines are
  accumulated into a buffer which is flushed when it ends with a sentence
  terminator (``。！？!?``), so body sentences are not merged with headings.
* Keep only sentences containing a financial-number expression such as
  ``\\d+(\\.\\d+)?\\s*(亿元|万元|%|％|倍|元/股|百万元|元)``.
* Exclude noise:
  - table-of-contents pages (lots of ``....`` dot leaders),
  - figure/table captions (lines starting with ``图N：`` / ``表N：``),
  - page headers / footers / page numbers (``N / 44``, running titles),
  - source lines (``数据来源：`` / ``资料来源：``),
  - chart axis noise (pure numeric / percentage fragments),
  - pure table rows (no CJK / path fragments),
  - too-long (``>MAX``) and too-short (``<MIN``) sentences.
* Output a numbered list ``N. <sentence>`` starting at 1.

Only new artefacts are produced; no source code is modified.

Usage::

    py -3.12 scripts/build_fulltext_draft.py
    py -3.12 scripts/build_fulltext_draft.py --pdf <path> --out <path>
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from typing import Iterable, List, Sequence, Tuple

try:
    import pymupdf  # type: ignore
except ImportError as exc:  # pragma: no cover - environment guard
    sys.stderr.write(
        "pymupdf is required (use py -3.12 with `import pymupdf`): %s\n" % exc
    )
    raise


# ---------------------------------------------------------------------------
# Configuration (all values have explicit defaults)
# ---------------------------------------------------------------------------

PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PDF: str = os.path.join(
    PROJECT_ROOT, "data", "corpus", "reports", "_broker",
    "kysec_839493_20240421.pdf",
)
DEFAULT_OUT: str = os.path.join(
    PROJECT_ROOT, "samples", "real_broker_full_draft.md",
)

# Financial-number expression: value followed by a financial unit.
# The unit set is intentionally broad (including USD and compute units) so the
# full-text draft reaches a statistically meaningful size; the noise filters
# below keep out table blobs and chart labels.
FIN_UNIT_PATTERN: str = (
    r"\d+(?:\.\d+)?\s*(?:亿元|万元|亿|百万元|万美元|亿美元|万|%|％|倍|"
    r"元/股|元|EFLOPS|Pflops|PFlops|核时|卡时|台|个)"
)
FIN_UNIT_RE: re.Pattern = re.compile(FIN_UNIT_PATTERN)

# CJK detection (any Han character).
CJK_RE: re.Pattern = re.compile(r"[\u4e00-\u9fff]")

# Dot-leader line used by tables of contents.
TOC_DOT_RE: re.Pattern = re.compile(r"\.{4,}")

# Figure / table caption prefixes.
CAPTION_RE: re.Pattern = re.compile(r"^\s*(?:图|表)\s*\d+")

# Page number like "12 / 44".
PAGENUM_RE: re.Pattern = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")

# Running headers / footers to drop entirely.
RUNNING_HEADER_RE: re.Pattern = re.compile(r"北交所首次覆盖报告")
DISCLAIMER_RE: re.Pattern = re.compile(r"请务必参阅正文后面的信息披露和法律声明")
SOURCE_LINE_RE: re.Pattern = re.compile(r"^\s*(?:数据来源|资料来源|注)\s*[:：]")

# Chart-axis / pure-numeric fragments (no CJK): digits, signs, %, dots.
NUMERIC_ONLY_RE: re.Pattern = re.compile(r"^[\s\d.,%％+\-()）(单位：]*(?:单位[:：][^\s]*)?$")

# Minimum CJK count for a fragment to be considered "prose" (not a table row).
MIN_CJK_CHARS: int = 4

# Sentence terminators that flush the accumulator.
TERMINATOR_RE: re.Pattern = re.compile(r"[。！？!?]$")

# Split a buffered paragraph into hard sentences (keep the terminator).
HARD_SPLIT_RE: re.Pattern = re.compile(r"(?<=[。！？!?])")

# Clause-level split used to break over-long financial sentences.
CLAUSE_SPLIT_RE: re.Pattern = re.compile(r"[；;]|，(?=[^，]{0,40}$)")

# Length bounds after cleaning.
MIN_SENT_LEN: int = 8
MAX_SENT_LEN: int = 130

# Leading item numbers, section numbers, bullet glyphs, etc.
LEADING_NOISE_RE: re.Pattern = re.compile(
    r"^\s*(?:[■●◆▶▪◦·•☞◆\u3000]|\(?\d+\)?[、.)）]\s*|[一二三四五六七八九十]+[、.]\s*)+"
)

# Section / heading pattern such as "1.2、 业务：..." or "看点一：...".
HEADING_RE: re.Pattern = re.compile(r"^\s*(?:\d+\.\d+[、.]|\d+[、.]|看点[一二三四五六七八九十]+[：:])")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def file_sha256(path: str) -> str:
    """Return the hex SHA-256 digest of *path* (streaming, memory friendly)."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def extract_page_texts(pdf_path: str) -> List[str]:
    """Extract the text layer of every page, in page order."""
    pages: List[str] = []
    with pymupdf.open(pdf_path) as doc:
        for page in doc:
            pages.append(page.get_text("text"))
    return pages


def page_is_toc(text: str) -> bool:
    """True when a page is dominated by a table of contents (dot leaders)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    dot_lines = sum(1 for ln in lines if TOC_DOT_RE.search(ln))
    return dot_lines >= max(5, len(lines) // 4)


def line_is_noise(line: str) -> bool:
    """True when a raw line must never enter the prose accumulator."""
    stripped = line.strip()
    if not stripped:
        return True
    if PAGENUM_RE.match(stripped):
        return True
    if RUNNING_HEADER_RE.search(stripped):
        return True
    if DISCLAIMER_RE.search(stripped):
        return True
    if CAPTION_RE.match(stripped):
        return True
    if SOURCE_LINE_RE.match(stripped):
        return True
    if TOC_DOT_RE.search(stripped):
        return True
    if HEADING_RE.match(stripped):
        return True
    # Pure-numeric / axis fragment or table cell without prose.
    if NUMERIC_ONLY_RE.match(stripped):
        return True
    if len(CJK_RE.findall(stripped)) < MIN_CJK_CHARS:
        return True
    return False


def clean_fragment(fragment: str) -> str:
    """Normalise a candidate fragment (collapse whitespace, drop numbering)."""
    text = fragment.replace("\u3000", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = LEADING_NOISE_RE.sub("", text).strip()
    return text


def iter_sentences(pages: Sequence[str]) -> Iterable[Tuple[int, str]]:
    """Yield ``(page_number_1based, sentence)`` for prose sentences.

    Lines are accumulated into a rolling buffer.  Whenever the buffer contains
    a sentence terminator (``。！？!?``) the completed part is emitted and the
    remainder is carried over, so soft-wrapped body sentences are reassembled
    and multiple sentences on one line are separated.  Headings, captions,
    source lines and pure-numeric fragments never enter the buffer.  Only
    sentences containing a financial-number expression survive downstream.
    """
    for page_index, page_text in enumerate(pages):
        if page_is_toc(page_text):
            continue
        page_no = page_index + 1
        buffer: str = ""

        def emit_ready() -> Iterable[str]:
            """Emit every complete (terminator-delimited) sentence in buffer."""
            nonlocal buffer
            parts = HARD_SPLIT_RE.split(buffer)
            if len(parts) <= 1:
                return
            # All parts except the last are complete sentences.
            for piece in parts[:-1]:
                candidate = clean_fragment(piece)
                if candidate:
                    yield candidate
            buffer = parts[-1]

        for raw_line in page_text.splitlines():
            if line_is_noise(raw_line):
                if not raw_line.strip():
                    # Blank line terminates any pending sentence fragment.
                    candidate = clean_fragment(buffer)
                    buffer = ""
                    if candidate:
                        yield page_no, candidate
                continue
            buffer += (" " if buffer else "") + raw_line.strip()
            for sentence in emit_ready():
                yield page_no, sentence
        candidate = clean_fragment(buffer)
        buffer = ""
        if candidate:
            yield page_no, candidate


# Header / key-data blocks list many "指标(单位)" pairs on one line.
UNIT_TAG_RE: re.Pattern = re.compile(r"\((?:元|亿元|万股|亿股|%|％|倍|百万元)\)")


def accept_sentence(sentence: str) -> bool:
    """Final gate: financial content, CJK prose, sane length."""
    if len(sentence) < MIN_SENT_LEN or len(sentence) > MAX_SENT_LEN:
        return False
    if not FIN_UNIT_RE.search(sentence):
        return False
    if not CJK_RE.search(sentence):
        return False
    # Reject key-data header blocks that list several "指标(单位)" pairs.
    if len(UNIT_TAG_RE.findall(sentence)) >= 3:
        return False
    return True


def dedupe(sentences: Iterable[Tuple[int, str]]) -> List[Tuple[int, str]]:
    """Filter, de-duplicate and preserve order (first occurrence wins)."""
    seen: set = set()
    unique: List[Tuple[int, str]] = []
    for page_no, sentence in sentences:
        if not accept_sentence(sentence):
            continue
        key = re.sub(r"\s+", "", sentence)
        if key in seen:
            continue
        seen.add(key)
        unique.append((page_no, sentence))
    return unique


# ---------------------------------------------------------------------------
# Financial-summary table extraction (page 42)
# ---------------------------------------------------------------------------
#
# The appendix "财务预测摘要" prints each line item as a label followed by five
# period values (2022A / 2023A / 2024E / 2025E / 2026E).  The text layer keeps
# that order, so a label line followed by exactly five numeric lines can be
# deterministically turned into declarative assertions.  Only a curated set of
# meaningful indicators is kept so the draft stays clean and bounded.

PERIOD_LABELS: Tuple[str, ...] = ("2022A", "2023A", "2024E", "2025E", "2026E")
PERIOD_YEARS: Tuple[str, ...] = ("2022", "2023", "2024", "2025", "2026")

# Label -> (display name, unit suffix).  ``unit`` is appended verbatim.
CURATED_INDICATORS: dict = {
    # 利润表（单位：百万元）
    "营业收入": ("营业收入", "百万元"),
    "营业成本": ("营业成本", "百万元"),
    "营业利润": ("营业利润", "百万元"),
    "利润总额": ("利润总额", "百万元"),
    "净利润": ("净利润", "百万元"),
    "归属母公司净利润": ("归母净利润", "百万元"),
    "EBITDA": ("EBITDA", "百万元"),
    # 成长能力（%）
    "营业收入(%)": ("营业收入同比增速", "%"),
    "归属于母公司净利润(%)": ("归母净利润同比增速", "%"),
    # 获利能力（%）
    "毛利率(%)": ("毛利率", "%"),
    "净利率(%)": ("净利率", "%"),
    "ROE(%)": ("ROE", "%"),
    "ROIC(%)": ("ROIC", "%"),
    # 偿债能力
    "资产负债率(%)": ("资产负债率", "%"),
    "净负债比率(%)": ("净负债比率", "%"),
    "流动比率": ("流动比率", ""),
    "速动比率": ("速动比率", ""),
    # 营运能力
    "总资产周转率": ("总资产周转率", ""),
    "应收账款周转率": ("应收账款周转率", ""),
    "应付账款周转率": ("应付账款周转率", ""),
    # 现金流量表（单位：百万元）
    "经营活动现金流": ("经营活动现金流净额", "百万元"),
    "资本支出": ("资本支出", "百万元"),
    "折旧摊销": ("折旧摊销", "百万元"),
    # 每股指标（元）
    "每股收益(最新摊薄)": ("每股收益", "元"),
    "每股经营现金流(最新摊薄)": ("每股经营现金流", "元"),
    "每股净资产(最新摊薄)": ("每股净资产", "元"),
    # 估值比率（倍）
    "P/E": ("P/E", "倍"),
    "P/B": ("P/B", "倍"),
    "EV/EBITDA": ("EV/EBITDA", "倍"),
}

# Assets / equity line items used for balance-sheet completeness.
CURATED_BALANCE: dict = {
    "资产总计": ("资产总计", "百万元"),
    "负债合计": ("负债合计", "百万元"),
    "归属母公司股东权益": ("归属于母公司股东权益", "百万元"),
}

_PURE_NUMBER_RE: re.Pattern = re.compile(r"^-?\d+(?:\.\d+)?$")


def extract_financial_summary(
    page_text: str,
) -> List[Tuple[str, str]]:
    """Parse the appendix financial-summary table into declarative assertions.

    Returns a list of ``(label_key, sentence)`` pairs, un-deduplicated.
    """
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    curated = {}
    curated.update(CURATED_INDICATORS)
    curated.update(CURATED_BALANCE)

    results: List[Tuple[str, str]] = []
    idx = 0
    while idx < len(lines) - 5:
        label = lines[idx]
        value_lines = lines[idx + 1: idx + 6]
        if label in curated and all(_PURE_NUMBER_RE.match(v) for v in value_lines):
            display, unit = curated[label]
            for period, year, raw in zip(PERIOD_LABELS, PERIOD_YEARS, value_lines):
                is_forecast = period.endswith("E")
                prefix = "预计" if is_forecast else ""
                if unit == "%":
                    sentence = "公司%s%s年%s为%s%%。" % (prefix, year, display, raw)
                elif unit:
                    sentence = "公司%s%s年%s为%s%s。" % (prefix, year, display, raw, unit)
                else:
                    sentence = "公司%s%s年%s为%s。" % (prefix, year, display, raw)
                results.append((label, sentence))
            idx += 6
        else:
            idx += 1
    return results


def dedupe_summary(
    summary: Sequence[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """De-duplicate summary assertions while preserving order."""
    seen: set = set()
    unique: List[Tuple[str, str]] = []
    for label, sentence in summary:
        key = re.sub(r"\s+", "", sentence)
        if key in seen:
            continue
        seen.add(key)
        unique.append((label, sentence))
    return unique


def build_markdown(
    prose: Sequence[Tuple[int, str]],
    summary: Sequence[Tuple[str, str]],
    pdf_name: str,
    pdf_sha256: str,
    page_count: int,
) -> str:
    """Render the numbered draft markdown document (prose + summary table)."""
    lines: List[str] = []
    lines.append("# 并行科技（839493.BJ）研报全文核查草稿（扩大基线）")
    lines.append("")
    lines.append(
        "> 来源：开源证券《并行科技（839493.BJ）北交所首次覆盖报告》2024-04-22，"
        "全文 %d 页。" % page_count
    )
    lines.append("> PDF：`data/corpus/reports/_broker/%s`" % pdf_name)
    lines.append("> PDF SHA-256：`%s`" % pdf_sha256)
    lines.append(
        "> 用途：由研报正文引用句与附录财务预测摘要自动抽取，"
        "重建大规模弃权率基线。正文抽取仅保留含金融数字的句子，已排除目录页、"
        "图注/表注、页码、数据来源行、图表坐标轴噪声、纯表格碎片与过短/过长句；"
        "摘要表按“指标 2022A/2023A/2024E/2025E/2026E”五期结构重建为陈述句。"
    )
    lines.append("")

    lines.append("## 一、正文金融表述引用（自动抽取）")
    lines.append("")
    for idx, (page_no, sentence) in enumerate(prose, start=1):
        lines.append("%d. %s" % (idx, sentence))
    lines.append("")

    lines.append("## 二、附录财务预测摘要（第 42 页，结构化重建）")
    lines.append("")
    offset = len(prose)
    for idx, (_label, sentence) in enumerate(summary, start=offset + 1):
        lines.append("%d. %s" % (idx, sentence))
    lines.append("")
    return "\n".join(lines)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="source PDF path")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output draft path")
    parser.add_argument(
        "--max-len",
        type=int,
        default=MAX_SENT_LEN,
        help="maximum sentence length in characters",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    args = parse_args(argv)
    if not os.path.isfile(args.pdf):
        sys.stderr.write("PDF not found: %s\n" % args.pdf)
        return 2

    global MAX_SENT_LEN
    MAX_SENT_LEN = args.max_len

    pdf_sha256 = file_sha256(args.pdf)
    pages = extract_page_texts(args.pdf)

    raw = list(iter_sentences(pages))
    prose = dedupe(raw)

    summary = extract_financial_summary(pages[41]) if len(pages) > 41 else []
    summary = dedupe_summary(summary)

    markdown = build_markdown(
        prose,
        summary,
        os.path.basename(args.pdf),
        pdf_sha256,
        len(pages),
    )
    out_dir = os.path.dirname(os.path.abspath(args.out))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(markdown)

    total = len(prose) + len(summary)
    print("PDF            : %s" % args.pdf)
    print("PDF SHA-256    : %s" % pdf_sha256)
    print("Pages          : %d" % len(pages))
    print("Raw sentences  : %d" % len(raw))
    print("Prose entries  : %d" % len(prose))
    print("Summary entries: %d" % len(summary))
    print("Total entries  : %d" % total)
    print("Output         : %s" % args.out)
    print("--- first 20 entries ---")
    for idx, (page_no, sentence) in enumerate(prose[:20], start=1):
        print("%3d. (p%d) %s" % (idx, page_no, sentence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
