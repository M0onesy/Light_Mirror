"""Source-defined notes and segment grids, shared by full/local extraction.

Only explicit scope/units and positioned column headers authorize facts.
Missing cells are absent, never zero; descriptive paragraphs are not rows.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from decimal import Decimal

from .dimensions import normalize_dimension_label
from .evidence import make_evidence_id
from .models import Fact


_CELL = re.compile(r'(?P<num>[+\-－−]?\d[\d,，]*(?:\.\d+)?)(?P<unit>元|万元|百万元|亿元|股|万股|亿股|%|％)?')
_SCALES = {'元': Decimal(1), '万元': Decimal(10000), '百万元': Decimal(1000000),
           '亿元': Decimal(100000000), '股': Decimal(1), '万股': Decimal(10000),
           '亿股': Decimal(100000000), '%': Decimal('.01'), '％': Decimal('.01')}
_BRIDGE = {
    '净利润': 'NP',
    '信用减值损失': 'CF_BRIDGE_CREDIT_IMPAIRMENT',
    '资产减值准备': 'CF_BRIDGE_ASSET_IMPAIRMENT',
    '固定资产折旧': 'CF_BRIDGE_DEPRECIATION',
    '使用权资产折旧': 'CF_BRIDGE_ROU_DEPRECIATION',
    '无形资产摊销': 'CF_BRIDGE_INTANGIBLE_AMORTIZATION',
    '长期待摊费用摊销': 'CF_BRIDGE_PREPAID_AMORTIZATION',
    '处置固定资产、无形资产和其他长期资产的损失': 'CF_BRIDGE_DISPOSAL_LOSS',
    '固定资产报废损失': 'CF_BRIDGE_SCRAP_LOSS',
    '财务费用': 'CF_BRIDGE_FINANCE',
    '递延所得税资产减少': 'CF_BRIDGE_DTA_CHANGE',
    '递延所得税负债增加': 'CF_BRIDGE_DTL_CHANGE',
    '存货的减少': 'CF_BRIDGE_INV_CHANGE',
    '经营性应收项目的减少': 'CF_BRIDGE_REC_CHANGE',
    '经营性应付项目的增加': 'CF_BRIDGE_PAY_CHANGE',
    '其他': 'CF_BRIDGE_OTHER',
    '经营活动产生的现金流量净额': 'CFO',
    '现金的期末余额': 'CASH_BALANCE',
    '现金的期初余额': 'CASH_BALANCE_OPENING',
    '现金及现金等价物净增加额': 'CASH_EQUIVALENTS_CHANGE',
    '期末现金及现金等价物余额': 'CASH_EQUIVALENTS',
}


def _compact(text):
    return re.sub(r'\s+', '', text)


def _source(page, block, digest):
    return make_evidence_id(digest, page['page'], block.get('evidence_text', block['text']).strip(),
                            block.get('evidence_bbox', block.get('bbox')))


def _header_columns(block, *, period, mode):
    """Build leaf account/period columns from explicit original geometry."""
    lines = block.get('geometry_lines') or []
    text = _compact(block['text'])
    year = period[:4] if re.fullmatch(r'20\d{2}(?:Y|H1|9M|Q[1-4])', period) else ''
    anchors = []
    for line in lines:
        content = _compact(line['text'])
        box = line['bbox']
        point = (box[0]+box[2])/2
        explicit = re.fullmatch(r'(20\d{2})年?(年度|末|\d{1,2}月\d{1,2}日|1月?[-—至~～]\d{1,2}月|上半年|半年度)?', content)
        if explicit:
            ending = explicit[2] or ''
            interval = re.fullmatch(r'1月?[-—至~～](\d{1,2})月', ending)
            suffix = ({3: 'Q1', 6: 'H1', 9: '9M', 12: 'Y'}.get(int(interval[1]), '') if interval else
                      'H1' if ending in {'上半年', '半年度', '6月30日'} else
                      'Y' if ending in {'', '年度', '末', '12月31日'} else '')
            if suffix:
                anchors.append((point, explicit[1]+suffix))
        elif year and content in {'本期金额', '本年金额', '期末余额'}:
            anchors.append((point, period))
        elif year and content in {'上期金额', '上年金额', '上年同期金额', '上年年末余额'}:
            anchors.append((point, str(int(year)-1)+period[4:]))
    anchors.sort()
    if not anchors:
        return []
    # Notes can have period parents and revenue/cost children. Associate each
    # child with the source parent's physical span; never positional amount.
    leaves = []
    for line in lines:
        caption = _compact(line['text'])
        if caption not in {'营业收入', '营业成本', '收入', '成本'}:
            continue
        x = (line['bbox'][0]+line['bbox'][2])/2
        closest = min(anchors, key=lambda a: abs(a[0]-x))
        leaves.append((x, closest[1], 'REVENUE' if caption in {'收入', '营业收入'} else 'COST', caption))
    if leaves and len(leaves) == 2*len(anchors):
        return sorted(leaves)
    if len({p for _, p in anchors}) != len(anchors):
        return []
    return [(x, p, '', p) for x, p in anchors]


def _row_cells(block, columns):
    lines = block.get('geometry_lines') or []
    if not lines or not columns:
        return None
    centres = [x for x, _, _, _ in columns]
    gap = min((b-a for a, b in zip(centres, centres[1:])), default=100)
    values, label_parts = {}, []
    for line in lines:
        text = _compact(line['text'])
        if not text:
            continue
        box = line['bbox']
        x = (box[0]+box[2])/2
        match = _CELL.fullmatch(text)
        if x < centres[0]-gap/2:
            label_parts.append(text)
        elif match and centres[0]-gap/2 <= x <= centres[-1]+gap/2:
            index = min(range(len(centres)), key=lambda i: abs(centres[i]-x))
            if index in values:
                return None
            values[index] = match
        elif text not in {'-', '—', '–'}:
            # A paragraph, row with an extra percentage/change column, or an
            # unsupported note index is not silently treated as a simple grid.
            return None
    if not label_parts or not values:
        return None
    return ''.join(label_parts), values


def extract_note_facts(pages, *, path, company, effective_period, doc_sha, disclosure_date,
                       disclosure_version, strict=False, issues=None, **control):
    from .parser import _logical_blocks, _parse_checkpoint
    from .facts import FactStore
    scope = ''
    scope_proof = ()
    unit = ''
    unit_proof = ()
    note_unit = ''
    note_unit_proof = ()
    table_currency = 'CNY'
    mode = ''
    table_name = ''
    columns = []
    column_proof = ()
    output = []
    note_section = False
    # Explicit annual-note convention is valid throughout those notes and
    # accompanies every derived fact. It is not inherited from another PDF.
    for page in pages:
        _parse_checkpoint(**control)
        raw_blocks = page.get('blocks') or []
        blocks = _logical_blocks(sorted(raw_blocks, key=lambda b: (b['bbox'][1], b['bbox'][0])), page.get('lines')) if raw_blocks else []
        for block in blocks:
            text = _compact(block['text'])
            if not text:
                continue
            if re.search(r'除特殊注明外[，,、]?金额单位均为人民币元', text):
                unit = '元'
                unit_proof = (_source(page, block, doc_sha),)
                note_unit, note_unit_proof = unit, unit_proof
                table_currency = 'CNY'
                note_section = True
            heading = re.fullmatch(r'[一二三四五六七八九十]+[、.．]?(合并|母公司)财务报表(?:主要)?项目(?:注释|说明)', text)
            if heading:
                scope = heading[1]
                scope_proof = (_source(page, block, doc_sha),)
                mode = ''
                columns = []
                unit, unit_proof, table_currency = note_unit, note_unit_proof, 'CNY'
                note_section = True
                continue
            if not note_section:
                continue
            # Only entire short section captions activate a mode. A prose
            # reference to the same words cannot unlock a financial table.
            if re.fullmatch(r'(?:[（(][一二三四五六七八九十]+[）)])?营业收入和营业成本', text):
                mode, table_name, columns = 'segment', '营业收入和营业成本附注', []
                unit, unit_proof, table_currency = note_unit, note_unit_proof, 'CNY'
            elif re.fullmatch(r'(?:[（(][一二三四五六七八九十]+[）)])?每股收益', text):
                mode, table_name, columns = 'eps', '每股收益计算附注', []
                unit, unit_proof, table_currency = note_unit, note_unit_proof, 'CNY'
            elif re.fullmatch(r'(?:[（(][一二三四五六七八九十]+[）)])?现金流量表补充资料', text):
                mode, table_name, columns = 'bridge', '现金流量表补充资料', []
                unit, unit_proof, table_currency = note_unit, note_unit_proof, 'CNY'
            elif re.match(r'^[（(][一二三四五六七八九十]+[）)]', text):
                mode, columns = '', []
            if not mode or not scope:
                continue
            from .mentions import source_currency
            currency = source_currency(block['text'])
            if currency:
                table_currency = currency['currency']
                unit_proof = tuple(dict.fromkeys(unit_proof+(_source(page,block,doc_sha),)))
            units = re.search(r'单位[:：](元|万元|百万元|亿元)', text)
            if units:
                unit = units[1]
                unit_proof = (_source(page, block, doc_sha),)
            header = _header_columns(block, period=effective_period, mode=mode)
            if header and len(header) >= 2:
                columns = header
                column_proof = (_source(page, block, doc_sha),)
                continue
            # The second-level header is sometimes its own original block;
            # combine it only with the immediately active explicit parents.
            if mode == 'segment' and columns and re.fullmatch(r'(?:营业收入营业成本|收入成本)+', text):
                leaves = []
                for line in block.get('geometry_lines', ()):
                    caption = _compact(line['text'])
                    if caption not in {'营业收入', '营业成本', '收入', '成本'}:
                        continue
                    x = (line['bbox'][0]+line['bbox'][2])/2
                    parent = min(columns, key=lambda c: abs(c[0]-x))
                    leaves.append((x, parent[1], 'REVENUE' if caption in {'营业收入', '收入'} else 'COST', caption))
                if len(leaves) == 2*len(columns):
                    columns = sorted(leaves)
                    column_proof += (_source(page, block, doc_sha),)
                continue
            row = _row_cells(block, columns)
            if row is None:
                continue
            label, values = row
            label = re.sub(r'^[一二三四五六七八九十]+[、.．]|^(?:加|减)[:：]', '', label)
            label = re.sub(r'[（(](?:收益|增加|减少)以[“"‘\x27]?[－−-][”"’\x27]?号填列[）)]', '', label)
            identities = []
            if mode == 'segment':
                if not all(c[2] in {'REVENUE', 'COST'} for c in columns):
                    continue
                if label in {'合计', '主营业务', '其他业务'}:
                    # These are not business segments; only disclosed total
                    # rows may fill the company's REVENUE/COST identities.
                    if label != '合计':
                        continue
                    identities = [(i, c[2], 'total', '', '') for i, c in enumerate(columns)]
                elif label in {'业务类型：', '按商品转让时间分类：', '类别', '项目'}:
                    continue
                elif label in {'在某一时点确认', '在某一时段内确认'}:
                    # Recognition timing and business types are different
                    # dimensions; timing rows remain outside business facts.
                    continue
                else:
                    identifier = normalize_dimension_label(label)
                    identities = [(i, c[2], 'business', identifier, '') for i, c in enumerate(columns)]
            elif mode == 'eps':
                if label in {'本公司发行在外普通股的加权平均数', '发行在外普通股的加权平均数'}:
                    account, chosen_unit = 'WEIGHTED_SHARES', '股'
                elif label in {'本公司发行在外普通股的加权平均数（稀释）', '本公司发行在外普通股的加权平均数(稀释)'}:
                    account, chosen_unit = 'WEIGHTED_SHARES_DILUTED', '股'
                elif label == '归属于母公司普通股股东的合并净利润':
                    account, chosen_unit = 'NP_PARENT_ORDINARY', unit
                elif label in {'归属于母公司普通股股东的合并净利润（稀释）', '归属于母公司普通股股东的合并净利润(稀释)'}:
                    account, chosen_unit = 'NP_PARENT_ORDINARY_DILUTED', unit
                elif label in {'基本每股收益', '稀释每股收益'} and unit:
                    account, chosen_unit = ('EPS' if label == '基本每股收益' else 'EPS_DILUTED'), '元'
                else:
                    continue
                identities = [(i, account, 'total', '', chosen_unit) for i in range(len(columns))]
            else:
                account = _BRIDGE.get(label)
                if not account:
                    continue
                identities = [(i, account, 'total', '', '') for i in range(len(columns))]
            evidence = tuple(dict.fromkeys((_source(page, block, doc_sha),)+scope_proof+unit_proof+column_proof))
            for index, account, kind, identifier, chosen_unit in identities:
                if index not in values:
                    continue
                match = values[index]
                display = match['unit'] or chosen_unit or unit
                if display not in _SCALES or chosen_unit in {'股', '元'} and match['unit'] and match['unit'] != chosen_unit:
                    continue
                if not re.fullmatch('[A-Z]{3}',table_currency):
                    continue
                raw = match['num'].replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')
                value = Decimal(raw)*_SCALES[display]
                period = columns[index][1]
                data = [account, period, scope, kind, identifier, str(value), evidence]
                fact_id = f'{doc_sha[:12]}-{account}-{period}-p{page["page"]}-n'+hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:16]
                output.append(Fact(fact_id, company, account, period, scope=scope, nr='未扣非',
                    version=disclosure_version, source_doc=doc_sha[:12], publish_date=disclosure_date,
                    value=value, decimals=len(raw.partition('.')[2]), display_unit=display,
                    evidence=evidence, label=label, source_path=str(path), source_type='financial_note',
                    table_label=table_name, column_label=columns[index][3], parse_conf='0.99',
                    extractor_version='financial-notes-v1', version_group=doc_sha[:12],
                    dimension_kind=kind, dimension_id=identifier, currency=table_currency))
    # Listed-company annual reports disclose a "主要客户情况" table in the
    # management discussion section, outside the audited-note headings above.
    # It is still a source-backed financial dimension: each row carries an
    # explicit customer label, sales amount, unit and reporting period.  The
    # old note-only parser silently dropped these rows, which made every
    # customer revenue claim appear as a missing fact.  Parse the table from
    # its own bounded section and never carry the state into supplier or
    # unrelated tables.
    output.extend(_extract_major_customer_facts(
        pages, path=path, company=company, effective_period=effective_period,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, strict=strict, **control))
    output.extend(_extract_procurement_source_facts(
        pages, path=path, company=company, effective_period=effective_period,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, strict=strict, **control))
    from .source_business_mode import extract_source_facts
    output.extend(extract_source_facts(pages, path=path, company=company,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, **control))
    from .source_customer_quotation import extract_source_facts as extract_customer_types
    output.extend(extract_customer_types(pages, path=path, company=company,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, **control))
    from .source_product_financial import extract_source_facts as extract_product_cells
    output.extend(extract_product_cells(pages, path=path, company=company,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, **control))
    from .source_quarter_quotation import extract_source_facts as extract_quarter_shares
    output.extend(extract_quarter_shares(pages, path=path, company=company,
        doc_sha=doc_sha, disclosure_date=disclosure_date,
        disclosure_version=disclosure_version, **control))
    return FactStore(output).facts


def _extract_procurement_source_facts(pages, *, path, company, effective_period,
                                      doc_sha, disclosure_date, disclosure_version,
                                      strict=False, **control):
    """Source-defined cells of the external CPU/GPU procurement disclosure.

    These facts retain unknown accounting scope and are admitted only to an
    explicitly cited source-table query. Reported growth is a separate source
    relation: it must not participate in ordinary amount queries or formulas.
    Both full extraction and local reparsing use the same geometry parser.
    """
    from .source_gap_catalog import _procurement_rows
    output = []
    for cell in _procurement_rows(pages, digest=doc_sha, path=path,
                                  date=disclosure_date, company=company, **control):
        relation = cell['source_relation']
        identity = [doc_sha, cell['source_table_id'], cell['source_context']['row_label'],
                    cell['period'], relation, cell['source_context']['cell_bbox']]
        fact_id = f'{doc_sha[:12]}-source-grid-'+hashlib.sha256(
            json.dumps(identity, ensure_ascii=False).encode('utf-8')).hexdigest()[:20]
        output.append(Fact(
            fact_id, company, cell['registered_account'], cell['period'],
            scope='未知', nr='未扣非', version=disclosure_version,
            source_doc=doc_sha[:12], publish_date=disclosure_date,
            value=Decimal(cell['raw_value']), decimals=cell['decimals'],
            display_unit=cell['display_unit'], evidence=tuple(cell['evidence']),
            label=cell['source_context']['row_label'], source_path=str(path),
            source_type='financial_source_table', table_label=cell['table_label'],
            column_label=cell['column_label'], parse_conf='0.99',
            extractor_version='financial-notes-procurement-source-v1',
            version_group=doc_sha[:12], dimension_kind=cell['dimension_kind'],
            dimension_id=cell['dimension_id'], currency=cell['currency'],
            definition_id='source:procurement-revenue-grid',
            source_table_id=cell['source_table_id'],
            source_table_evidence=tuple(cell['source_table_evidence']),
            source_scope_evidence=(), source_row_index=0,
            source_relation=relation))
    return output


def _extract_major_customer_facts(pages, *, path, company, effective_period,
                                  doc_sha, disclosure_date, disclosure_version,
                                  strict=False, **control):
    """Extract customer revenue rows from the explicitly labelled top-five tables.

    PDF text extraction commonly emits every table cell as a separate line
    (and wraps customer names across several lines).  Parsing a single raw
    block therefore misses nearly every row.  This parser walks the ordered
    page text, carries the table and period state across page breaks, and
    commits a row only after a serial number, a customer label, an amount and
    a percentage have all been observed.  It is deliberately scoped to
    ``前五大客户销售情况``/``前五名客户情况``; supplier, contract-liability and
    user-count tables are never treated as revenue facts.
    """
    from .parser import _parse_checkpoint

    rows = []
    active = False
    header_seen = False
    header_candidate = False
    saw_customer_header = False
    saw_amount_header = False
    table_totals = 0
    unit = None
    period = None
    pending = None
    aggregate_pending = None
    previous_page_number = None
    table_name = ''
    source_table_id = ''
    table_proof = ()
    table_header_proof = []
    table_period_proof = []
    table_unit_proof = []
    unit_proof = ()
    period_proof = ()
    scope = '未知'
    scope_proof = ()
    table_scope_claims = set()
    footnote_proof = []
    table_row_start = 0
    revenue_share_header = False

    def compact(value):
        return re.sub(r'\s+', '', value or '')

    # Captions must be local headings, not a customer phrase in narrative or
    # an unrelated contract-liability section.  A numbered caption may be
    # split so its number occupies the preceding line.
    caption_pattern = re.compile(
        r'^(?:[（(]?\d+[）)、.．]?)*'
        r'(?:前五大客户销售情况|前五名客户情况|前五大客户情况|主要客户情况)'
        r'(?:[（(](?:合并|母公司)(?:口径)?[）)])?[：:]?$')
    boundary_pattern = re.compile(
        r'(?:科目具体情况及(?:分析)?说明|前五[名大]供应商情况|主要供应商|'
        r'采购情况|其他披露事项|营业收入总体分析|营业成本分析|'
        r'募集资金|募投|项目投资概算)')

    def period_from_window(lines, index):
        window = ''.join(lines[index:index + 6])
        m = re.match(r'(20\d{2})(?=年|$)年?(?:度|年)?(?:1月?[-—至~～]6月?|1-6月|上半年|半年度)?', window)
        if not m:
            return None
        year = m.group(1)
        if re.search(r'(?:1月?[-—至~～]6月?|1-6月|上半年|半年度)', m.group(0)):
            return year + 'H1'
        return year + 'Y'

    def source_lines(page):
        """Locate each occurrence in an original block, including repeats.

        An original text offset selects the PDF geometry line; its rectangle
        selects the containing raw block.  Repeated names/amounts therefore
        cannot accidentally cite an earlier period on the same page.
        """
        result, line_offset, occurrences = [], 0, {}
        blocks = page.get('blocks') or []
        geometry = {line['start']: line for line in page.get('lines', ())
                    if type(line.get('start')) is int and line.get('bbox')}
        for raw in (page.get('text') or '').splitlines(keepends=True):
            text = compact(raw)
            start = line_offset + len(raw) - len(raw.lstrip())
            line_offset += len(raw)
            if not text:
                continue
            candidates = [block for block in blocks if text in compact(block.get('text', ''))]
            source_line = geometry.get(start)
            box = source_line.get('bbox') if source_line else None
            positioned = []
            if box:
                centre_x, centre_y = (box[0]+box[2])/2, (box[1]+box[3])/2
                positioned = [block for block in candidates if block.get('bbox') and
                              block['bbox'][0]-1 <= centre_x <= block['bbox'][2]+1 and
                              block['bbox'][1]-1 <= centre_y <= block['bbox'][3]+1]
            if positioned:
                block = min(positioned, key=lambda item:
                            (item['bbox'][2]-item['bbox'][0])*(item['bbox'][3]-item['bbox'][1]))
                evidence = _source(page, block, doc_sha)
            else:
                exact = [block for block in candidates if text == compact(block.get('text', ''))]
                occurrence = occurrences.get(text, 0)
                if exact and occurrence < len(exact):
                    evidence = _source(page, exact[occurrence], doc_sha)
                elif len(candidates) == 1:
                    evidence = _source(page, candidates[0], doc_sha)
                else:
                    # A text-only fallback has no trustworthy cell location.
                    # Retain a reversible original page reference, rather
                    # than inventing a rectangle for a repeated cell.
                    evidence = _source(page, {'text': page.get('text', ''), 'bbox': None}, doc_sha)
                occurrences[text] = occurrence + 1
            result.append((text, evidence))
        return result

    def remember_scope(page, text, source_evidence):
        nonlocal scope, scope_proof
        # A local, affirmative declaration is required.  A footnote saying
        # customer campuses are "合并计算" describes customer aggregation,
        # not the financial reporting entity's consolidation scope.
        explicit = set()
        for candidate in ('合并', '母公司'):
            if (re.search(r'按' + candidate + r'口径(?:披露|列示|统计|编制|计算)', text)
                    or re.search(r'(?:本表|客户(?:销售)?(?:数据|收入|情况))'
                                 r'(?:采用|为|属于|系)?' + candidate + r'口径', text)
                    or re.search(r'[（(]' + candidate + r'(?:口径)?[）)]', text)):
                # Negated, hypothetical and comparison prose cannot sign a
                # source scope merely by containing the positive substring.
                if re.search(r'(?:不|未|非|并非|假设|若|如|改为|而非).{0,6}'
                             + candidate + r'(?:口径)?', text):
                    continue
                explicit.add(candidate)
        if explicit:
            table_scope_claims.update(explicit)
            scope = next(iter(table_scope_claims)) if len(table_scope_claims) == 1 else '未知'
            scope_proof = tuple(dict.fromkeys(scope_proof + (source_evidence,)))

    def finish_table():
        # Footnotes follow the amounts, so attach their reversible evidence
        # after the bounded table is complete.  Never apply them to another
        # customer's table or a later supplier/project section.
        for row_index in range(table_row_start, len(rows)):
            fact = rows[row_index]
            combined = tuple(dict.fromkeys(fact.evidence + scope_proof + tuple(footnote_proof)))
            source_table_evidence = tuple(dict.fromkeys(
                table_proof + tuple(table_header_proof) + tuple(table_period_proof)
                + tuple(table_unit_proof) + tuple(footnote_proof)))
            digest = hashlib.sha256(json.dumps(
                [fact.account, fact.period, scope, 'customer', fact.dimension_id,
                 str(fact.value), combined], ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]
            rows[row_index] = replace(fact, scope=scope, evidence=combined,
                                     source_table_id=source_table_id,
                                     source_table_evidence=source_table_evidence,
                                     source_scope_evidence=scope_proof,
                                     fact_id=f'{fact.source_doc}-{fact.account}-{fact.period}-c{digest}')

    def append_share(amount_fact, row, label):
        if not row.get('share_defined'):
            return
        try:
            share_value = Decimal(row['share']) * Decimal('.01')
        except Exception:
            return
        # The reported percentage is a separate source cell, not a value
        # inferred from the customer amount or a convenient matching total.
        share_evidence = tuple(dict.fromkeys((row['share_evidence'],)
                               + tuple(row.get('label_evidence', ()))
                               + tuple(row.get('index_evidence', ()))
                               + table_proof + row.get('period_proof', ())))
        digest = hashlib.sha256(json.dumps(
            ['REVENUE_SHARE', amount_fact.period, scope, label, str(share_value), share_evidence],
            ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]
        rows.append(replace(amount_fact, fact_id=f'{doc_sha[:12]}-REVENUE_SHARE-{amount_fact.period}-c{digest}',
                    account='REVENUE_SHARE', value=share_value,
                    decimals=len(row['share'].partition('.')[2]), display_unit='%',
                    evidence=share_evidence, column_label='占营业收入比重',
                    definition_id='metric:revenue-share'))

    def commit(page, row):
        if not row or row.get('amount') is None or row.get('share') is None:
            return
        label = normalize_dimension_label(''.join(row.get('label_parts') or []))
        if not label or label in {'客户', '客户名称', '合计', '主要客户'}:
            return
        row_period, row_unit = row.get('period'), row.get('unit')
        if row_period is None or row_unit not in _SCALES:
            return
        raw = row['amount'].replace(',', '').replace('，', '')
        try:
            value = Decimal(raw) * _SCALES[row_unit]
        except Exception:
            return
        evidence = tuple(dict.fromkeys(tuple(row.get('evidence', ()))
                                      + table_proof + row.get('unit_proof', ())
                                      + row.get('period_proof', ()) + scope_proof))
        digest = hashlib.sha256(json.dumps(
            ['REVENUE', row_period, scope, 'customer', label, str(value), evidence],
            ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]
        fact_id = f'{doc_sha[:12]}-REVENUE-{row_period}-p{page["page"]}-c{digest}'
        rows.append(Fact(
            fact_id, company, 'REVENUE', row_period, scope=scope, nr='未扣非',
            version=disclosure_version, source_doc=doc_sha[:12],
            publish_date=disclosure_date, value=value,
            decimals=len(raw.partition('.')[2]), display_unit=row_unit,
            evidence=evidence, label=label, source_path=str(path),
            source_type='financial_dimension_table',
            table_label=table_name, column_label='营业收入',
            parse_conf='0.97', extractor_version='financial-notes-customer-v3',
            version_group=doc_sha[:12], dimension_kind='customer',
            dimension_id=label, currency='CNY', source_row_index=int(row['index'])))
        append_share(rows[-1], row, label)

    def commit_aggregate(page, aggregate):
        if not aggregate or aggregate.get('amount') is None or aggregate.get('share') is None:
            return
        row_period, row_unit = aggregate.get('period'), aggregate.get('unit')
        if row_period is None or row_unit not in _SCALES:
            return
        raw = aggregate['amount'].replace(',', '').replace('，', '')
        try:
            value = Decimal(raw) * _SCALES[row_unit]
        except Exception:
            return
        evidence = tuple(dict.fromkeys(tuple(aggregate.get('evidence', ()))
                                      + table_proof + aggregate.get('unit_proof', ())
                                      + aggregate.get('period_proof', ()) + scope_proof))
        digest = hashlib.sha256(json.dumps(
            ['REVENUE', row_period, scope, 'customer', '\u524d\u4e94\u5927\u5ba2\u6237', str(value), evidence],
            ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()[:16]
        fact_id = f'{doc_sha[:12]}-REVENUE-{row_period}-p{page["page"]}-top5-{digest}'
        rows.append(Fact(
            fact_id, company, 'REVENUE', row_period, scope=scope, nr='\u672a\u6263\u975e',
            version=disclosure_version, source_doc=doc_sha[:12],
            publish_date=disclosure_date, value=value,
            decimals=len(raw.partition('.')[2]), display_unit=row_unit,
            evidence=evidence, label='\u524d\u4e94\u5927\u5ba2\u6237\u5408\u8ba1', source_path=str(path),
            source_type='financial_dimension_table', table_label=table_name,
            column_label='\u8425\u4e1a\u6536\u5165', parse_conf='0.97',
            extractor_version='financial-notes-customer-v3', version_group=doc_sha[:12],
            dimension_kind='customer', dimension_id='\u524d\u4e94\u5927\u5ba2\u6237', currency='CNY',
            source_row_index=0))
        append_share(rows[-1], aggregate, '前五大客户')

    for page in pages:
        _parse_checkpoint(**control)
        # A local reparse may supply disjoint page windows.  Scope a table to
        # consecutive source pages; a distant page cannot continue its rows.
        if (previous_page_number is not None
                and page['page'] != previous_page_number + 1):
            finish_table()
            active, header_seen, header_candidate = False, False, False
            saw_customer_header, saw_amount_header = False, False
            pending, aggregate_pending, unit, period = None, None, None, None
        previous_page_number = page['page']
        located_lines = source_lines(page)
        lines = [text for text, _ in located_lines]
        if not lines:
            continue
        for index, text in enumerate(lines):
            current_evidence = located_lines[index][1]
            # Start only at a customer-sales section.  Carrying ``active`` to
            # the next page is intentional for tables split at a page break.
            if caption_pattern.fullmatch(text):
                commit(page, pending)
                commit_aggregate(page, aggregate_pending)
                finish_table()
                active, header_seen, header_candidate, saw_customer_header = True, False, False, False
                saw_amount_header, table_totals, pending = False, 0, None
                aggregate_pending = None
                # Units and periods belong to this source table, never the
                # previous section.  A split header/period may follow on the
                # next page, so only the new caption resets them.
                unit, period = None, None
                table_name = text
                table_proof = (current_evidence,)
                source_table_id = 'source-table-' + hashlib.sha256(
                    (doc_sha + current_evidence).encode('utf-8')).hexdigest()[:24]
                table_header_proof, table_period_proof, table_unit_proof = [], [], []
                unit_proof, period_proof = (), ()
                scope, scope_proof = '未知', ()
                table_scope_claims = set()
                footnote_proof = []
                table_row_start = len(rows)
                revenue_share_header = False
                remember_scope(page, text, current_evidence)
                continue
            if active and boundary_pattern.search(text):
                commit(page, pending)
                commit_aggregate(page, aggregate_pending)
                finish_table()
                # A row interrupted by another section is incomplete.  It
                # must not consume that section's amount, period or unit.
                active, header_seen, header_candidate = False, False, False
                saw_customer_header, saw_amount_header = False, False
                pending, aggregate_pending, unit, period = None, None, None, None
                continue
            if not active:
                continue

            if not header_seen or re.match(r'^注(?:\d+)?[：:]', text):
                remember_scope(page, text, current_evidence)
            if re.match(r'^注(?:\d+)?[：:]', text):
                # A footnote closes any complete final customer row.  It is
                # evidence about the same table, never part of a customer name.
                commit(page, pending)
                pending = None
                footnote_proof.append(current_evidence)
                header_seen = False
                continue

            detected = period_from_window(lines, index) if re.search(r'20\d{2}', text) else None
            if detected:
                # A new period begins a fresh table; discard an incomplete row.
                commit(page, pending)
                commit_aggregate(page, aggregate_pending)
                pending = None
                aggregate_pending = None
                period = detected
                period_evidence = [current_evidence]
                for cursor in range(index + 1, min(len(lines), index + 5)):
                    if re.fullmatch(r'年(?:度)?|1-6|1月?[-—至~～]6月?|月|上半年|半年度', lines[cursor]):
                        period_evidence.append(located_lines[cursor][1])
                    else:
                        break
                period_proof = tuple(dict.fromkeys(period_evidence))
                table_period_proof.extend(period_proof)
                continue

            if '单位：万元' in text or text == '（万元）' or text == '万元':
                unit = '万元'
                unit_proof = (current_evidence,)
                table_unit_proof.append(current_evidence)
                continue
            if '单位：元' in text or text == '（元）' or text == '元':
                unit = '元'
                unit_proof = (current_evidence,)
                table_unit_proof.append(current_evidence)
                continue
            # Header cells are emitted as separate lines.  Require a nearby
            # serial/header marker and both the customer and amount labels;
            # this prevents narrative sentences later in the report from
            # reopening the table.
            if text in {'序号', '年份', '年度'} or text.startswith('序号客户'):
                header_candidate = True
                header_seen = False
                saw_customer_header, saw_amount_header = False, False
                revenue_share_header = False
                table_header_proof.append(current_evidence)
            if header_candidate and ('客户名称' in text or text == '客户'):
                saw_customer_header = True
                table_header_proof.append(current_evidence)
            if header_candidate and ('营业收入' in text or '销售金额' in text):
                saw_amount_header = True
                table_header_proof.append(current_evidence)
            if re.fullmatch(r'占营业收入(?:比重|比例|占比)?', text):
                revenue_share_header = True
                table_header_proof.append(current_evidence)
                continue
            if header_candidate and saw_customer_header and saw_amount_header:
                header_seen = True
                if '万元' in text or (index + 1 < len(lines) and '万元' in lines[index + 1]):
                    unit = '万元'
                elif text in {'（万元）', '万元'}:
                    unit = '万元'
                elif text in {'（元）', '元'}:
                    unit = '元'
                # Header detection is one-shot.  Keeping the candidate flag
                # set would classify every subsequent row cell as a header.
                header_candidate = False
                continue
            if not header_seen:
                continue

            if (aggregate_pending is None and re.fullmatch(r'[1-5]', text)
                    and (pending is None or (pending['amount'] is not None and pending['share'] is not None))):
                commit(page, pending)
                pending = {'index': text, 'label_parts': [], 'amount': None, 'share': None,
                           'evidence': [current_evidence], 'label_evidence': [],
                           'index_evidence': (current_evidence,),
                           'period': period, 'unit': unit,
                           'unit_proof': unit_proof, 'period_proof': period_proof,
                           'share_defined': revenue_share_header}
                continue
            if aggregate_pending is not None:
                if aggregate_pending['amount'] is None and re.fullmatch(r'[+\-]?\d[\d,，]*(?:\.\d+)?', text):
                    aggregate_pending['amount'] = text
                    aggregate_pending['evidence'].append(current_evidence)
                    continue
                if aggregate_pending['amount'] is not None and aggregate_pending['share'] is None:
                    share = re.match(r'^[+\-]?\d+(?:\.\d+)?%?', text)
                    if share:
                        aggregate_pending['share'] = share.group(0).rstrip('%')
                        aggregate_pending['share_evidence'] = current_evidence
                        aggregate_pending['evidence'].append(current_evidence)
                        commit_aggregate(page, aggregate_pending)
                        aggregate_pending = None
                        continue
            if pending is None:
                # A valid continuation can start with the previous period's
                # aggregate on the next page, after the last row was committed.
                if text in {'合计', '合'}:
                    aggregate_pending = {'amount': None, 'share': None, 'evidence': [current_evidence],
                                         'label_evidence': [current_evidence],
                                         'period': period, 'unit': unit,
                                         'unit_proof': unit_proof, 'period_proof': period_proof,
                                         'share_defined': revenue_share_header}
                continue
            # Totals close the current table and are never customer rows.
            if text.startswith('合计') or text == '合':
                commit(page, pending)
                pending = None
                aggregate_pending = {'amount': None, 'share': None, 'evidence': [current_evidence],
                                     'label_evidence': [current_evidence],
                                     'period': period, 'unit': unit,
                                     'unit_proof': unit_proof, 'period_proof': period_proof,
                                     'share_defined': revenue_share_header}
                table_totals += 1
                continue
            if pending['amount'] is None and re.fullmatch(r'[+\-]?\d[\d,，]*(?:\.\d+)?', text):
                pending['amount'] = text
                pending['evidence'].insert(0, current_evidence)
                continue
            if pending['amount'] is not None and pending['share'] is None:
                share = re.fullmatch(r'[+\-]?\d+(?:\.\d+)?%?', text)
                if share:
                    pending['share'] = text.rstrip('%')
                    pending['share_evidence'] = current_evidence
                    pending['evidence'].append(current_evidence)
                    continue
                # Some text layers concatenate the percentage cell with the
                # following sales-content cell (for example
                # ``2.98%超算云服务``).  The percentage prefix is still an
                # explicit table value; retain it and ignore the content.
                prefix = re.match(r'^[+\-]?\d+(?:\.\d+)?%', text)
                if prefix:
                    pending['share'] = prefix.group(0).rstrip('%')
                    pending['share_evidence'] = current_evidence
                    pending['evidence'].append(current_evidence)
                    continue
            # Related-party markers and sales-content cells are deliberately
            # ignored after the row has acquired its required numeric fields.
            if pending['amount'] is None:
                if text not in {'序号', '客户', '客户名称', '销售金额', '年度销售额占比', '（%）'}:
                    pending['label_parts'].append(text)
                    pending['evidence'].append(current_evidence)
                    pending['label_evidence'].append(current_evidence)

        # Do not commit an incomplete row at an arbitrary page boundary; it
        # may continue on the next page.  A completed row is safe to commit.
        if pending and pending.get('amount') is not None and pending.get('share') is not None:
            commit(page, pending)
            pending = None
        if aggregate_pending and aggregate_pending.get('amount') is not None and aggregate_pending.get('share') is not None:
            commit_aggregate(page, aggregate_pending)
            aggregate_pending = None
    finish_table()
    # The same disclosure often contains both an MD&A customer table and an
    # audited-note table.  They are independent occurrences for claim
    # coverage, but one canonical fact per dimension is sufficient for
    # deterministic queries.  Keep the first source-backed occurrence and
    # remove exact duplicates without merging different customers or periods.
    unique = {}
    for fact in rows:
        key = (fact.company, fact.account, fact.period, fact.scope,
               fact.dimension_kind, fact.dimension_id, fact.value,
               fact.display_unit, fact.currency, fact.source_table_id, fact.source_row_index)
        unique.setdefault(key, fact)
    return list(unique.values())
