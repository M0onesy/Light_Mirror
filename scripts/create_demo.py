"""Generate a redistributable fictional annual report and ten-claim demo."""
import json
import sys
from decimal import Decimal
from pathlib import Path
import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.models import Fact

root = Path(__file__).resolve().parents[1] / 'samples'
corpus = root / 'corpus'
corpus.mkdir(parents=True, exist_ok=True)
doc = fitz.open()
page = doc.new_page()
page.insert_text((50, 50), '明镜示例公司 2024 年年度报告（虚构教学样例）', fontname='china-s', fontsize=14)
page.insert_text((50, 85), '以下数值仅供功能演示，不对应任何真实上市公司。单位：元', fontname='china-s', fontsize=10)
page.insert_text((50, 120), '科目 / 期间 / 口径', fontname='china-s', fontsize=10)
page.insert_text((320, 120), '2024年', fontname='china-s', fontsize=10)
page.insert_text((450, 120), '2023年', fontname='china-s', fontsize=10)
rows = [
    ('REVENUE', '营业收入', '1000000000.00', '900000000.00', '合并', '未扣非'),
    ('NP_PARENT', '归母净利润', '120000000.00', '100000000.00', '合并', '未扣非'),
    ('CFO', '经营现金流', '80000000.00', '70000000.00', '合并', '未扣非'),
    ('NP_PARENT_ADJ', '扣非净利润', '100000000.00', '90000000.00', '合并', '扣非'),
    ('NP', '净利润', '130000000.00', '110000000.00', '合并', '未扣非'),
    ('COST', '营业成本', '750000000.00', '720000000.00', '合并', '未扣非'),
    ('REVENUE', '母公司营业收入', '500000000.00', '400000000.00', '母公司', '未扣非'),
]
for i, (account, label, current, prior, scope, nr) in enumerate(rows):
    y = 155+i*38
    page.insert_text((50, y), label, fontname='china-s', fontsize=10)
    for x, value in ((320, current), (450, prior)):
        page.insert_text((x, y), format(Decimal(value), ',.2f'), fontsize=9)
page.insert_text((50, 470), '披露日期：2025年3月31日；虚构示例原始披露版本。', fontname='china-s', fontsize=10)
path = corpus / 'demo_annual.pdf'
doc.save(path, deflate=True)
doc.close()
sha = sha256_file(path)
facts = []
with fitz.open(path) as pdf:
    for row, (account, label, current, prior, scope, nr) in enumerate(rows, 1):
        for year, value, col in ((2024, current, 1), (2023, prior, 2)):
            snippet = format(Decimal(value), ',.2f')
            boxes = pdf[0].search_for(snippet)
            y = 155+(row-1)*38
            bbox = min(boxes, key=lambda rect: abs(rect.y1-y))
            ev = make_evidence_id(sha, 1, snippet, tuple(bbox))
            facts.append(Fact(f'demo-{account}-{year}-{scope}', '明镜示例公司', account, f'{year}Y', scope=scope, nr=nr,
                publish_date='2025-03-31', value=Decimal(value), source_doc=sha[:12], source_path='demo_annual.pdf',
                evidence=(ev,), label=label, source_type='structured', table_label='虚构示例财务表', column_label=f'{year}年/第{col}列').to_dict())
(corpus / 'facts.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
(root / 'demo_draft.md').write_text('''# 明镜示例公司核查草稿（虚构）

1. 公司2024年营业收入10.00亿元。
2. 公司2024年归母净利润1.20亿元。
3. 公司2024年经营现金流0.80亿元。
4. 公司2024年扣非净利润0.95亿元。
5. 公司2024年营业收入同比增长10.0%。
6. 公司2024年营业收入10.00万元。
7. 公司2024年毛利率30.0%。
8. 公司2024年净利率13.0%。
9. 公司2024年上半年经营现金流2000万元。
10. 公司2024年营业收入低于2023年。
''', encoding='utf-8')
print(json.dumps({'pdf_sha256': sha, 'facts': len(facts), 'draft_statements': 10}))
