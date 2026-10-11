"""Validate OOXML preservation and render the final Word PDF for review."""
from pathlib import Path
import argparse
import hashlib
import json
import zipfile
from lxml import etree as E

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / "docs" / "project"
OUT = PROJECT / "evidence" / "documentation"
OLD = None
NEW = ROOT / "明镜MingJing_项目计划书.docx"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def package(path):
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if name.endswith(".xml") or name.endswith(".rels"):
                E.fromstring(z.read(name))
        doc = E.fromstring(z.read("word/document.xml"))
        media = {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()
                 if n.startswith("word/media/") and not n.endswith("/")}
        return doc, media


def main():
    global ROOT, OUT, OLD, NEW
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True, help="Historical original DOCX, never changed")
    parser.add_argument("--documents", type=Path, default=ROOT)
    parser.add_argument("--out", type=Path, required=True, help="New review evidence directory")
    args = parser.parse_args()
    OLD = args.original.resolve(strict=True)
    ROOT = args.documents.resolve(strict=True)
    NEW = ROOT / "明镜MingJing_项目计划书.docx"
    OUT = args.out.resolve()
    if OUT.exists():
        raise FileExistsError("Review evidence output must be new")
    OUT.mkdir(parents=True)
    orig, old_media = package(OLD)
    doc, new_media = package(NEW)
    paras = ["".join(p.xpath(".//w:t/text()", namespaces=NS)) for p in doc.findall(".//w:p", NS)]
    text = "\n".join(paras)
    forbidden = ["627", "492", "579", "2,961", "10.5%", "13.7%", "1.0.0.dev2", "mainProj/mingjing-next", "任何一条被改动、删除或插入"]
    found = [x for x in forbidden if x in text]
    headings = [p for p in paras if p.startswith("第") and "章 " in p and not p[-1:].isdigit()]
    report = {
        "schema": "mingjing-docx-content-review.v1", "version": "1.0.0.dev3",
        "xml_all_parse": True, "media_bytes_preserved": old_media == new_media,
        "tables_before": len(orig.findall(".//w:tbl", NS)), "tables_after": len(doc.findall(".//w:tbl", NS)),
        "drawings_before": len(orig.findall(".//w:drawing", NS)), "drawings_after": len(doc.findall(".//w:drawing", NS)),
        "stale_current_claims_found": found, "chapter_headings": headings,
        "member_information": "awaiting_real_team", "professional_metrics": "targets_not_accepted",
    }
    assert report["media_bytes_preserved"] and report["tables_before"] == report["tables_after"]
    assert report["drawings_before"] == report["drawings_after"] and not found
    pdf = ROOT / "明镜MingJing_项目计划书_初赛评审版.pdf"
    if pdf.exists():
        import fitz
        from PIL import Image, ImageDraw
        visual = OUT / "文档视觉"
        visual.mkdir(parents=True, exist_ok=True)
        page_documents = fitz.open(pdf)
        selected = set([0, 1, 2, 3])
        hit_terms = ["图3-2", "3.5 使用模式", "5.7 弃权", "5.10 直传", "7.4 重放", "表10-4", "11.1 团队分工", "表14-1"]
        term_pages = {}
        for n, page in enumerate(page_documents):
            page_text = page.get_text()
            for term in hit_terms:
                # Skip table of contents pages where several terms co-occur.
                if term in page_text and n > 4:
                    term_pages.setdefault(term, []).append(n + 1)
                    selected.add(n)
        for n in sorted(selected):
            page_documents[n].get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False).save(visual / f"page-{n+1:02d}.png")
        # Overview retains every page; full-size selected pages support readable review.
        cell_w, cell_h, cols, batch = 225, 330, 4, 16
        for start in range(0, len(page_documents), batch):
            chunk = list(range(start, min(start + batch, len(page_documents))))
            sheet = Image.new("RGB", (cols * cell_w, ((len(chunk) + cols - 1)//cols) * cell_h), "#e4e7eb")
            draw = ImageDraw.Draw(sheet)
            for j, n in enumerate(chunk):
                pix = page_documents[n].get_pixmap(matrix=fitz.Matrix(.4, .4), alpha=False)
                thumb = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                thumb.thumbnail((cell_w - 12, cell_h - 25))
                x, y = (j % cols)*cell_w + 6, (j // cols)*cell_h + 20
                sheet.paste(thumb, (x, y)); draw.text((x, y-15), str(n+1), fill="#111111")
            sheet.save(visual / f"overview-{start+1:02d}-{chunk[-1]+1:02d}.png")
        report["pdf_pages"] = len(page_documents)
        report["selected_readable_pages"] = [n+1 for n in sorted(selected)]
        report["term_pages"] = term_pages
        report["visual_assets"] = str(visual)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "docs-structure.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
