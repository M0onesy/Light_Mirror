"""Original cover names and explicit disclosure dates stay independent."""
from decimal import Decimal

import pytest

from mingjing.smart.claims import Claim, ReportModel
from mingjing.smart.detect import DocMeta, detect, same_company
from mingjing.smart.docio import Document, Page, rows_from_layout_text
from mingjing.smart.ingestion import ingest_facts
from mingjing.smart.kb import KnowledgeBase
from mingjing.smart.native import SmartRunAdapter
from mingjing.smart.tables import extract_filing_facts
from mingjing.smart.verify import verify_model


def test_cover_report_title_cannot_join_legal_issuer_or_override_neighbor_identity(tmp_path):
    text = ('2018\n年度报告\n北京镜湖科技股份有限公司\n证券代码：999001\n'
            '重要提示\n公司简介\n合并利润表\n单位：元\n项目  2018年度\n营业收入  100.00\n财务报表附注\n')
    kb = KnowledgeBase(tmp_path / 'kb')
    source = kb.add_bytes(text.encode(), 'annual.txt', hints={
        'company': '镜湖科技', 'period': '2018Y', 'report_date': '2019-04-20', 'doc_type': 'annual_report'})
    meta = detect(Document('/virtual/annual.txt', 'annual.txt', 'a' * 64, 'text', 'text',
                           [Page(1, rows_from_layout_text(1, text), text)]))
    assert meta.company_full == '北京镜湖科技股份有限公司'
    assert meta.company == '镜湖科技' and meta.stock_code == '999001'
    adapter = SmartRunAdapter(None, kb, tmp_path / 'runs')
    adapter.freeze([source], tmp_path / 'selected')
    facts, _ = ingest_facts(tmp_path / 'selected', '镜湖科技', '2018Y')
    assert any(f.account == 'REVENUE' and f.value == Decimal('100') for f in facts)
    neighbor = kb.add_bytes(text.replace('镜湖科技', '邻湖科技').replace('999001', '999002').encode(),
                            'neighbor.txt', hints={'company': '镜湖科技', 'stock_code': '999001',
                            'period': '2018Y', 'report_date': '2019-04-20', 'doc_type': 'annual_report'})
    adapter.freeze([neighbor], tmp_path / 'neighbor')
    with pytest.raises(ValueError, match='原文发行人.*冲突'):
        ingest_facts(tmp_path / 'neighbor', '镜湖科技', '2018Y', task_subject=meta)


def test_same_tail_old_codes_and_unregistered_new_code_alias_do_not_prove_issuer_identity(tmp_path):
    assert not same_company({'stock_code': '839493'}, {'stock_code': '873493'})
    assert not same_company({'stock_code': '839493', 'company': '镜湖科技'},
                            {'stock_code': '873493', 'company': '镜湖科技'})
    kb = KnowledgeBase(tmp_path / 'kb')
    subject_text = '镜湖科技（839493）\n证券研究报告\n2023年营业收入100元。'
    subject = detect(Document('/virtual/research.txt', 'research.txt', 'd' * 64, 'text', 'text',
                       [Page(1, rows_from_layout_text(1, subject_text), subject_text)]))
    adapter = SmartRunAdapter(None, kb, tmp_path / 'runs')
    for code in ('839493', '873493', '920493'):
        text = (f'北京镜湖示范科技股份有限公司2023年年度报告\n证券代码：{code}\n'
                '合并利润表\n单位：元\n项目  2023年度\n营业收入  100.00\n')
        item = kb.add_bytes(text.encode(), code + '.txt', hints={'company': '镜湖科技',
               'stock_code': '839493', 'period': '2023Y', 'report_date': '2024-04-20', 'doc_type': 'annual_report'})
        corpus = tmp_path / code
        adapter.freeze([item], corpus)
        if code == '839493':
            facts, _ = ingest_facts(corpus, '镜湖科技', '2023Y', task_subject=subject)
            assert facts and all(f.company == '镜湖科技' for f in facts)
        else:
            with pytest.raises(ValueError, match='原文发行人.*冲突'):
                ingest_facts(corpus, '镜湖科技', '2023Y', task_subject=subject)


@pytest.mark.parametrize('creation_date', ['D:20240420120000', 'D:20270420120000', ''])
def test_pdf_creation_timestamp_never_supplies_a_missing_publication_date(tmp_path, creation_date):
    fitz = pytest.importorskip('fitz')
    path = tmp_path / 'annual.pdf'
    with fitz.open() as pdf:
        pdf.new_page()
        pdf.set_metadata({'creationDate': creation_date})
        pdf.save(path)
    text = ('2023年年度报告\n北京镜湖科技股份有限公司\n重要提示\n公司简介\n合并利润表\n'
            '单位：元\n项目  2023年度\n营业收入  100.00\n财务报表附注\n')
    doc = Document(str(path), path.name, 'b' * 64, 'pdf', 'fitz',
                   [Page(1, rows_from_layout_text(1, text), text)])
    meta = detect(doc)
    assert meta.period == '2023Y' and meta.report_date == ''
    facts, _ = extract_filing_facts(doc, meta)
    assert facts and all(not f.publish_date for f in facts)
    claim = Claim(cid='history', value='100', unit='元', decimals=2, metric='REVENUE',
                  period='2023Y', category='core', scope='合并')
    report = ReportModel(DocMeta(company='镜湖科技', report_date='2024-05-01', base_year=2023),
                         [claim], [], {}, {'doc_sha256': 'c' * 64})
    result = verify_model(facts, report)[0]
    assert result.status == 'unverifiable' and result.abstain_code == 'X5'
    # An original host-registered publication date remains usable.
    registered = detect(doc, hints={'report_date': '2024-04-21'})
    assert registered.report_date == '2024-04-21'
