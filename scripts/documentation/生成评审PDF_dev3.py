"""Render the Word-refreshed proposal through Chromium when Office PDF stalls.

This reads only the revised DOCX. Media are embedded from its relationships;
paragraphs, tables, hyperlinks, bookmarks, and image occurrences retain order.
Word TOC hyperlinks retain their headings but omit Word-specific page numbers.
"""
from pathlib import Path, PurePosixPath
import argparse
import base64
import hashlib
import html
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from lxml import etree as E
from playwright.sync_api import sync_playwright

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / "docs" / "project"
OUT = PROJECT / "evidence" / "documentation"
DOCX = ROOT / "明镜MingJing_项目计划书.docx"
PDF = PROJECT / "artifacts" / "documents" / "明镜MingJing_项目计划书_初赛评审版.pdf"
HTML = OUT / "文档视觉" / "proposal-render.html"
NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "m": "http://schemas.openxmlformats.org/officeDocument/2006/math",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def q(prefix, name):
    return "{" + NS[prefix] + "}" + name


def value(parent, path, default=None):
    el = parent.find(path, NS) if parent is not None else None
    return el.get(q("w", "val"), default) if el is not None else default


class Converter:
    def __init__(self, archive):
        self.z = archive
        relationships = E.fromstring(archive.read("word/_rels/document.xml.rels"))
        self.rels = {x.get("Id"): x.get("Target") for x in relationships}
        styles = E.fromstring(archive.read("word/styles.xml"))
        self.styles = {x.get(q("w", "styleId")): x for x in styles.findall("w:style", NS)}
        self.counts = Counter()
        self.bookmarks = set()
        self.chapters = []
        self.in_cover = True

    def paragraph_properties(self, p):
        props = p.find("w:pPr", NS)
        sid = value(props, "w:pStyle")
        st = self.styles.get(sid)
        name = value(st, "w:name", "")
        return props, st, name

    def drawing(self, node):
        self.counts["images"] += 1
        blip = node.find(".//a:blip", NS)
        if blip is None:
            raise ValueError("Drawing without a bitmap relationship")
        target = self.rels[blip.get(q("r", "embed"))]
        member = str(PurePosixPath("word") / target)
        data = self.z.read(member)
        suffix = PurePosixPath(target).suffix.lower()
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}[suffix]
        extent = node.find(".//wp:extent", NS)
        width = int(extent.get("cx")) / 914400 if extent is not None else 6
        attrs = node.find(".//wp:docPr", NS)
        label = attrs.get("descr", attrs.get("name", "项目插图")) if attrs is not None else "项目插图"
        encoded = base64.b64encode(data).decode("ascii")
        anchor = node.find("wp:anchor", NS)
        css_class = ' class="cover-background"' if anchor is not None and anchor.get("behindDoc") == "1" else ""
        return '<img%s src="data:%s;base64,%s" alt="%s" style="width:%.3fin">' % (
            css_class, mime, encoded, html.escape(label, quote=True), width)

    def inline(self, node):
        tag = E.QName(node).localname
        if tag == "t":
            return html.escape(node.text or "")
        if tag in {"instrText", "fldChar", "rPr", "pPr", "sectPr", "proofErr", "bookmarkEnd"}:
            return ""
        if tag == "tab":
            return '<span class="tab">　</span>'
        if tag == "br":
            return "" if node.get(q("w", "type")) == "page" else "<br>"
        if tag == "lastRenderedPageBreak":
            return ""
        if tag == "drawing":
            return self.drawing(node)
        if tag == "AlternateContent":
            chosen = node.find("mc:Choice", NS)
            if chosen is None:
                chosen = node.find("mc:Fallback", NS)
            return "".join(self.inline(x) for x in chosen) if chosen is not None else ""
        if tag == "bookmarkStart":
            name = node.get(q("w", "name"), "")
            if name and name not in self.bookmarks:
                self.bookmarks.add(name)
                return '<span id="%s"></span>' % html.escape(name, quote=True)
            return ""
        if tag == "hyperlink":
            anchor = node.get(q("w", "anchor"))
            target = "#" + anchor if anchor else self.rels.get(node.get(q("r", "id")), "")
            content = "".join(self.inline(x) for x in node)
            return '<a href="%s">%s</a>' % (html.escape(target, quote=True), content)
        content = "".join(self.inline(x) for x in node)
        if tag == "r":
            props = node.find("w:rPr", NS)
            css = []
            if props is not None:
                if props.find("w:b", NS) is not None and value(props, "w:b", "1") not in {"0", "false"}:
                    css.append("font-weight:bold")
                if props.find("w:i", NS) is not None:
                    css.append("font-style:italic")
                size = value(props, "w:sz")
                if size:
                    css.append(f"font-size:{int(size)/2:g}pt")
                color = value(props, "w:color")
                if color and re.fullmatch(r"[0-9A-Fa-f]{6}", color):
                    css.append("color:#" + color)
                align = value(props, "w:vertAlign")
                if align == "superscript":
                    css.append("vertical-align:super;font-size:80%")
                elif align == "subscript":
                    css.append("vertical-align:sub;font-size:80%")
            return '<span style="%s">%s</span>' % (";".join(css), content) if css else content
        return content

    def paragraph(self, p, in_table=False):
        self.counts["paragraphs"] += 1
        props, style, name = self.paragraph_properties(p)
        text = "".join(p.xpath(".//w:t/text()", namespaces=NS))
        content = "".join(self.inline(x) for x in p)
        classes, css, tag = [], [], "p"
        meaningful = bool(text.strip()) or p.find(".//w:drawing", NS) is not None
        if meaningful and p.xpath(".//w:br[@w:type='page']", namespaces=NS):
            css.append("break-after:page")
        style_lower = name.lower().replace(" ", "")
        if style_lower in {"toc1", "toc2", "toc3"} or name in {"目录 1", "目录 2", "目录 3"}:
            link = p.find("w:hyperlink", NS)
            anchor = link.get(q("w", "anchor")) if link is not None else None
            # TOC text uses tabs before the PAGEREF; these page numbers belong
            # to Word pagination and must not be reused by a different renderer.
            parts = []
            for x in p.iter():
                if x.tag == q("w", "tab"):
                    break
                if x.tag == q("w", "t"):
                    parts.append(x.text or "")
            title = "".join(parts)
            content = '<a href="#%s">%s</a>' % (html.escape(anchor or "", quote=True), html.escape(title))
            classes.append("toc-entry")
            if style_lower.endswith("2"):
                css.append("padding-left:20pt")
            self.counts["toc_entries"] += 1
        elif re.match(r"^第[一二三四五六七八九十]+章 ", text):
            tag = "h1"
            classes.append("chapter")
            self.chapters.append(text)
        elif style_lower.startswith("heading") or name.startswith("标题"):
            level = re.search(r"[1-6]", style_lower)
            tag = "h" + (level.group() if level else "2")
        elif name.startswith("TOC 标题") or text.strip() in {"摘　　要", "项目全景", "目　　录"}:
            tag = "h1"
        elif re.match(r"^[图表]\s*\d+-\d+", text):
            classes.append("caption")
        if props is not None:
            if self.in_cover:
                spacing = props.find("w:spacing", NS)
                if spacing is not None:
                    before = spacing.get(q("w", "before"))
                    line = spacing.get(q("w", "line"))
                    if before:
                        css.append(f"margin-top:{int(before)/20:g}pt")
                    if line and spacing.get(q("w", "lineRule")) == "exact":
                        css.append(f"line-height:{int(line)/20:g}pt")
            align = value(props, "w:jc")
            if align in {"center", "right", "left"}:
                css.append("text-align:" + align)
            before = props.find("w:pageBreakBefore", NS)
            if meaningful and before is not None and before.get(q("w", "val"), "1") not in {"0", "false"}:
                css.append("break-before:page")
            indent = props.find("w:ind", NS)
            if indent is not None and tag == "p" and not in_table:
                first = indent.get(q("w", "firstLine"))
                if first:
                    css.append(f"text-indent:{int(first)/20:g}pt")
        if p.find(".//w:drawing", NS) is not None:
            classes.append("figure")
        if not content.strip():
            content = "&#160;"
            classes.append("empty")
        elif not meaningful and not self.in_cover:
            classes.append("empty")
        return '<%s class="%s" style="%s">%s</%s>' % (tag, " ".join(classes), ";".join(css), content, tag)

    def table(self, table):
        self.counts["tables"] += 1
        widths = [int(x.get(q("w", "w"))) for x in table.findall("w:tblGrid/w:gridCol", NS)]
        cols = ""
        if widths:
            total = sum(widths)
            cols = "<colgroup>" + "".join(f'<col style="width:{w/total*100:.4f}%">' for w in widths) + "</colgroup>"
        rows = []
        for row in table.findall("w:tr", NS):
            cells = []
            for cell in row.findall("w:tc", NS):
                colspan = value(cell.find("w:tcPr", NS), "w:gridSpan", "1")
                # Word vMerge continuations remain explicit cells so no source
                # paragraph or content is removed by HTML table normalization.
                cells.append('<td colspan="%s">%s</td>' % (colspan, self.blocks(cell, True)))
            rows.append("<tr>" + "".join(cells) + "</tr>")
        return "<table>" + cols + "<tbody>" + "".join(rows) + "</tbody></table>"

    def blocks(self, parent, in_table=False):
        result = []
        body = parent.tag == q("w", "body")
        if body:
            result.append('<section class="cover">')
        for node in parent:
            tag = E.QName(node).localname
            if body and self.in_cover and tag == "p" and "".join(node.xpath(".//w:t/text()", namespaces=NS)).strip() == "摘　　要":
                result.append("</section>")
                self.in_cover = False
            if tag == "p":
                result.append(self.paragraph(node, in_table))
            elif tag == "tbl":
                result.append(self.table(node))
            elif tag in {"sdt", "sdtContent", "customXml"}:
                result.append(self.blocks(node, in_table))
        return "\n".join(result)


CSS = """
@page { size:A4; margin:20mm 25mm 16mm; }
* { box-sizing:border-box; }
html { -webkit-print-color-adjust:exact; print-color-adjust:exact; }
body { margin:0; color:#111; font-family:'SimSun','宋体','Microsoft YaHei',serif;
       font-size:10.5pt; line-height:1.55; }
p { margin:0 0 6pt; orphans:2; widows:2; text-align:justify; }
p.empty { min-height:5pt; margin:0; line-height:1; }
body > p.empty { height:0; min-height:0; line-height:0; margin:0; overflow:hidden; }
h1,h2,h3,h4,h5,h6 { font-family:'Microsoft YaHei','微软雅黑',sans-serif;
 margin:12pt 0 7pt; line-height:1.45; break-after:avoid; }
h1 { font-size:17pt; } h2 { font-size:13pt; } h3 { font-size:11.5pt; }
.chapter { break-before:page; }
.cover { position:relative; isolation:isolate; min-height:260mm; break-after:page; }
.cover-background { position:absolute; top:-20mm; left:-25mm; z-index:-1;
 width:210mm !important; height:297mm !important; max-width:none; }
.toc-entry { margin:0 0 4pt; font-size:10.5pt; text-align:left; }
a { color:inherit; text-decoration:none; overflow-wrap:anywhere; }
.tab { display:inline-block; width:1.5em; }
.pagebreak { display:block; break-before:page; }
.figure { text-align:center; text-indent:0 !important; break-inside:avoid; }
img { max-width:100%; height:auto; object-fit:contain; vertical-align:middle; }
.caption { text-align:center; text-indent:0 !important; font-size:10pt;
 margin:6pt 0 10pt; break-before:avoid; }
table { width:100%; table-layout:fixed; border-collapse:collapse; margin:8pt 0 10pt;
 font-size:9pt; line-height:1.4; }
td { border:0.5pt solid #888; padding:4pt 5pt; vertical-align:top;
 overflow-wrap:anywhere; }
td p { margin:0 0 2pt; text-indent:0; text-align:left; }
td span { font-size:inherit !important; }
tr { break-inside:avoid; } tr:first-child td { background:#f1f3f5; }
"""


def main():
    global DOCX, PDF, OUT, HTML
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docx", type=Path, default=DOCX)
    parser.add_argument("--out", type=Path, required=True, help="New output directory for PDF, HTML and render receipt")
    args = parser.parse_args()
    DOCX = args.docx.resolve(strict=True)
    OUT = args.out.resolve()
    if OUT.exists():
        raise FileExistsError("Render output must be new; frozen proposal/PDF is never overwritten")
    OUT.mkdir(parents=True)
    PDF = OUT / "明镜MingJing_项目计划书_初赛评审版.pdf"
    HTML = OUT / "proposal-render.html"
    with zipfile.ZipFile(DOCX) as archive:
        xml = E.fromstring(archive.read("word/document.xml"))
        converter = Converter(archive)
        content = converter.blocks(xml.find("w:body", NS))
        expected = {"paragraphs": len(xml.findall(".//w:p", NS)),
                    "tables": len(xml.findall(".//w:tbl", NS)),
                    "images": len(xml.findall(".//w:drawing", NS))}
        for key, count in expected.items():
            assert converter.counts[key] == count, (key, converter.counts[key], count)
        assert len(converter.chapters) == 14
    HTML.parent.mkdir(parents=True, exist_ok=True)
    HTML.write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                    '<title>明镜MingJing 项目计划书</title><style>' + CSS + '</style><body>'
                    + content + '</body></html>', encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(HTML.as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready")
        assert page.locator("img").count() == expected["images"]
        assert page.locator("table").count() == expected["tables"]
        image_state = page.locator("img").evaluate_all("xs=>xs.every(x=>x.complete&&x.naturalWidth>0)")
        assert image_state
        page.pdf(path=str(PDF), format="A4", print_background=True,
                 prefer_css_page_size=True, display_header_footer=True,
                 header_template='<div></div>',
                 footer_template='<div style="width:100%;font:8px Arial;color:#555;text-align:center">'
                                 '<span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                 margin={"top": "20mm", "bottom": "16mm", "left": "25mm", "right": "25mm"},
                 tagged=True, outline=True)
        browser_version = browser.version
        browser.close()
    import fitz
    pdf = fitz.open(PDF)
    links = [link for page in pdf for link in page.get_links()]
    internal_links = [x for x in links if x.get("kind") in {1, 4} and x.get("page", -1) >= 0]
    assert internal_links and all(x["page"] < len(pdf) for x in internal_links)
    receipt = {
        "schema": "mingjing-proposal-pdf-render.v1", "version": "1.0.0.dev3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pdf_render_passed_visual_review_pending",
        "renderer": "Playwright Chromium from Word-refreshed DOCX",
        "browser_version": browser_version, "source": str(DOCX), "pdf": str(PDF),
        "source_sha256": hashlib.sha256(DOCX.read_bytes()).hexdigest(),
        "pdf_sha256": hashlib.sha256(PDF.read_bytes()).hexdigest(), "pdf_bytes": PDF.stat().st_size,
        "pages": len(pdf), "converted_counts": dict(converter.counts),
        "resolved_internal_toc_links": len(internal_links),
        "source_counts": expected, "all_images_loaded": image_state,
        "chapter_headings": converter.chapters,
        "toc_policy": "preserved clickable headings; omitted Word page numbers; PDF footer uses actual Chromium pages",
        "word_native_pdf_export": "failed: three timed-out methods; retained word-attempt-01/02/03-timeout.json",
        "word_field_refresh": "saved DOCX retained; native Word page count 70 differs from Chromium pagination",
        "member_names": "awaiting_real_team", "official_submission": "not_performed",
    }
    (OUT / "pdf-render.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
