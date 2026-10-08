"""Read-only source cells with unresolved issuer scope.

These records are not Facts and must never become a verification truth without
an independently evidenced dimension resolution. Selection uses source labels,
period captions and geometry; the draft's candidate value is never accepted.
"""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal

from .evidence import make_evidence_id
from .dimensions import normalize_dimension_label

_VALUE = re.compile(r'(?P<num>[+\-－−]?\d[\d,，]*(?:\.\d+)?)(?P<percent>%|％)?')
_PERIOD = re.compile(r'(20\d{2})年(?:1月?[-—至~～](\d{1,2})月|度)?')


def _compact(text):
    return re.sub(r'\s+', '', text)


def _x(item):
    return (item['bbox'][0]+item['bbox'][2])/2


def _y(item):
    return (item['bbox'][1]+item['bbox'][3])/2


def _id(page, block, digest):
    return make_evidence_id(digest, page['page'], block.get('evidence_text', block['text']).strip(),
                            block.get('evidence_bbox', block['bbox']))


def _proofs(page, lines, digest, source_pages=None):
    """Evidence identifiers refer to original blocks, not invented fragments."""
    ids = []
    for line in lines:
        original_page = (source_pages or {}).get(line.get('source_page', page['page']), page)
        candidates = [block for block in original_page.get('blocks', ()) if line['text'].strip() in block['text']
                      and block['bbox'][0]-1 <= line['bbox'][0] and block['bbox'][2]+1 >= line['bbox'][2]
                      and block['bbox'][1]-1 <= line['bbox'][1] and block['bbox'][3]+1 >= line['bbox'][3]]
        if not candidates:
            return ()
        selected = min(candidates, key=lambda b: (b['bbox'][2]-b['bbox'][0])*(b['bbox'][3]-b['bbox'][1]))
        ids.append(_id(original_page, selected, digest))
    return tuple(dict.fromkeys(ids))


def _period(caption):
    match = _PERIOD.fullmatch(_compact(caption))
    if not match:
        return ''
    suffix = {3:'Q1',6:'H1',9:'9M',12:'Y'}.get(int(match[2]), '') if match[2] else 'Y'
    return match[1]+suffix if suffix else ''


def _record(*, digest, path, date, company, table_id, page, period, kind, identifier,
            account, relation, value, unit, row_label, row, headers, title,
            method=None, missing=None, source_pages=None):
    proofs = _proofs(page, row+headers+title, digest, source_pages)
    if not proofs or not period:
        return None
    raw = value['num'].replace(',', '').replace('，', '').replace('−', '-').replace('－', '-')
    decimals = len(raw.partition('.')[2])
    number = Decimal(raw)*(Decimal('.01') if unit == '%' else Decimal(10000) if unit == '万元' else Decimal(1))
    numeric = next((line for line in row if value[0] in _compact(line['text'])), None)
    identity = [digest, table_id, period, kind, identifier, account, relation,
                numeric['bbox'] if numeric else None, raw]
    cell_id = 'source-gap:'+hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    return {'id':'sg-'+cell_id.split(':')[1], 'resource_id':cell_id, 'table_id':table_id,
        'doc_sha256':digest, 'source_sha256':digest, 'source_doc':digest[:12],
        'source_path':str(path), 'publish_date':date, 'company':company, 'page':page['page'],
        'period':period,'dimension_kind':kind,'dimension_id':identifier,'currency':'CNY',
        'account':account,'scope':'未知','gap_kind':'source-scope-pending',
        'reason':'原表未声明发行人合并或母公司口径；客户合并归集不构成发行人财务口径证明',
        'table_label':''.join(line['text'].strip() for line in title),
        'dimensions':{'account':account,'period':period,'scope':'未声明','nr':'未扣非',
                      'dimension_kind':kind,'dimension_id':identifier,'currency':'CNY',
                      'definition_id':('customer-source:'+digest[:12]+':'+period if kind in {'customer','customer_group'} else
                                       (method or {}).get('definition_id','')),'relation':relation},
        'raw_value':str(number),'display_unit':unit,'decimals':decimals,
        'source_context':{'row_label':row_label,'table_label':''.join(line['text'].strip() for line in title),
                          'period_caption':period,'method':method or {}, 'cell_bbox':numeric['bbox'] if numeric else None,
                          'source_fragments':[{'text':line['text'],'bbox':line['bbox']} for line in row+headers+title]},
        'evidence':list(proofs),'missing_dimensions':missing or ['issuer_scope'],
        'status':'source_dimension_unresolved','eligible_as_fact':False,
        'blocking_reasons':[{'code':'X3','dimension':'scope','reason':'原表未声明发行人合并或母公司口径；客户合并归集不构成发行人财务口径证明'}]}


def _customer_rows(pages, *, digest, path, date, company):
    """Year cells can span five rows; only adjacent physical pages continue."""
    output = []
    state = None
    previous_page = 0
    source_pages = {page['page']:page for page in pages}
    note_proofs, note_texts = [], []
    for source_page in pages:
        for block in source_page.get('blocks', ()):
            compact = _compact(block['text'])
            if (re.match(r'^注\d*[:：]', compact) and
                    ('院校下设不同院系已合并计算销售额' in compact or '凌空天行包括' in compact)):
                note_proofs.append(_id(source_page,block,digest))
                note_texts.append(block['text'].strip())
    for page in pages:
        lines = sorted([dict(line, source_page=page['page']) for line in page.get('lines', ())], key=lambda line: (_y(line), _x(line)))
        active_titles = [line for line in lines if re.fullmatch(r'(?:\d+[、.．])?前五大客户销售情况', _compact(line['text']))]
        customer_header = '客户名称' in _compact(page['text']) and '占营业收入' in _compact(page['text'])
        if active_titles:
            state = {'title':active_titles,'period':'','headers':[],'table_id':'','last_rank':0,'last_total':False}
        elif state and (page['page'] != previous_page+1 or state['last_total'] and not customer_header):
            state = None
        previous_page = page['page']
        if state is None:
            continue
        # Stop the customer table before procurement and descriptive footnotes.
        stops = [_y(line) for line in lines if re.search(r'^[（(]二[）)]采购|^注\d*[:：]|^公司董事', _compact(line['text']))]
        end = min(stops, default=float('inf'))
        header_lines = [line for line in lines if _compact(line['text']) in {'年度','年份'} and _y(line)<end]
        sections = [(float('-inf'), min((_y(line) for line in header_lines),default=end), None)]
        sections += [(_y(line)-14, _y(header_lines[i+1])-14 if i+1<len(header_lines) else end, line)
                     for i,line in enumerate(header_lines)]
        for top, bottom, year_header in sections:
            if top >= bottom:
                continue
            selected = [line for line in lines if top <= _y(line)<bottom]
            if year_header:
                revenue = next((line for line in selected if _compact(line['text']) == '营业收入'), None)
                unit = next((line for line in selected if _compact(line['text']) in {'（万元）','(万元)'}), None)
                percent = next((line for line in selected if _compact(line['text']) == '占营业收入'), None)
                serial = next((line for line in selected if '序号' in _compact(line['text']) or _compact(line['text'])=='序'), None)
                if not all((revenue,unit,percent,serial)):
                    state['period'] = ''
                    continue
                period_parts = [line for line in sorted(selected,key=lambda x:x['bbox'][1])
                                if _x(line) < year_header['bbox'][2]+5 and
                                (re.fullmatch(r'20\d{2}|年|月|1月?[-—至~～]\d{1,2}',_compact(line['text'])) or _period(line['text']))]
                period = _period(''.join(line['text'] for line in period_parts))
                if not period:
                    state['period'] = ''
                    continue
                state.update(period=period, headers=[year_header,revenue,unit,percent,serial]+period_parts,
                    income_x=_x(revenue), percent_x=_x(percent)+10,
                    label_left=serial['bbox'][0]+15, label_right=revenue['bbox'][0]-8,
                    table_id=digest[:12]+':customer-sales:p'+str(page['page'])+':'+period,
                    last_rank=0,last_total=False)
            if not state['period']:
                continue
            anchors = [line for line in selected if _y(line)>top+18 and
                       (state['label_left']-25 < _x(line) < state['label_left'] and
                        re.fullmatch('[1-5]',_compact(line['text'])) or
                        state['label_left'] < _x(line) < state['label_right'] and _compact(line['text']) in {'合计','合'})]
            for anchor in anchors:
                # Row identity is independent of which financial column exists.
                # One missing amount must not erase its percentage or vice versa.
                gaps = [abs(_y(anchor)-_y(other)) for other in anchors if other is not anchor]
                height = min(min(gaps)/2 if gaps else 12,12)
                row = [line for line in selected if abs(_y(line)-_y(anchor)) < height]
                label_lines = [line for line in row if state['label_left']<_x(line)<state['label_right']
                               and not _VALUE.fullmatch(_compact(line['text']))]
                label = normalize_dimension_label(''.join(line['text'] for line in sorted(label_lines,key=lambda x:x['bbox'][1])))
                ranks = [_compact(line['text']) for line in row if _x(line) < state['label_left']
                         and re.fullmatch('[1-5]',_compact(line['text']))]
                if not label or label not in {'合计','合'} and len(ranks)!=1:
                    continue
                kind, identifier = (('customer_group','前五大客户') if label=='合计' else
                                    ('customer_group','原文表尾标签：合') if label=='合' else ('customer',label))
                method = {'definition_id':'metric:customer-revenue-share','numerator':'客户营业收入',
                          'denominator':'公司营业收入','customer_aggregation_scope':'按原表客户名称及来源脚注归集；匿名客户不跨文件映射',
                          'source_aggregation_notes':note_texts,
                          'issuer_scope':'未声明','scope_distinction':'客户归集方式与发行人财务报表范围独立'}
                income_cells = [(line,_VALUE.fullmatch(_compact(line['text']))) for line in row
                                if abs(_x(line)-state['income_x'])<30 and _VALUE.fullmatch(_compact(line['text']))]
                income_cells = [(line,match) for line,match in income_cells if not match['percent']]
                if len(income_cells)==1:
                    income,value=income_cells[0]
                    record = _record(digest=digest,path=path,date=date,company=company,table_id=state['table_id'],
                        page=page,period=state['period'],kind=kind,identifier=identifier,account='REVENUE',relation='value',
                        value=value,unit='万元',row_label=label,row=[income]+label_lines,headers=state['headers'],
                        title=state['title'],method=method,source_pages=source_pages)
                    if record:
                        record['evidence']=list(dict.fromkeys(record['evidence']+note_proofs))
                        output.append(record)
                percentages = [(line,_VALUE.match(_compact(line['text']))) for line in row
                               if abs(line['bbox'][0]-state['percent_x'])<45 and _VALUE.match(_compact(line['text']))]
                percentages = [(line,match) for line,match in percentages if match['percent']]
                if len(percentages)==1:
                    percent_line,percent_value=percentages[0]
                    record=_record(digest=digest,path=path,date=date,company=company,table_id=state['table_id'],
                        page=page,period=state['period'],kind=kind,identifier=identifier,account='REVENUE_SHARE',relation='ratio',
                        value=percent_value,unit='%',row_label=label,row=[percent_line]+label_lines,
                        headers=state['headers'],title=state['title'],method=method,source_pages=source_pages)
                    if record:
                        record['evidence']=list(dict.fromkeys(record['evidence']+note_proofs))
                        output.append(record)
                state['last_rank']=int(ranks[0]) if ranks else 5
                state['last_total']=label=='合计'
    return output


def _procurement_rows(pages, *, digest, path, date, company, **control):
    """Extract a bounded, source-defined external procurement/revenue grid.

    Its semantic identity comes from the complete issuer caption, actual row
    labels and the period/subcolumn geometry.  Neither a same-page mention,
    a printed amount nor a convenient year/column order selects the table.
    Missing or conflicting cells affect only their own source column.
    """
    from .parser import _parse_checkpoint
    output = []
    labels = {
        '算力资源采购金额': ('PROCUREMENT_AMOUNT', 'PURCHASE_AMOUNT', 'purchase_mode', '外采算力资源'),
        '外采算力资源产生的超算云服务收入': ('REVENUE', 'REVENUE', 'purchase_mode', '外采算力资源'),
        '超算云服务收入': ('REVENUE', 'REVENUE', 'business', '超算云服务'),
    }
    caption_pattern = re.compile(
        r'报告期内[，,]发行人对外采购的CPU及GPU算力资源与超算云服务收入增长的匹配关系如下[:：]')
    for page in pages:
        _parse_checkpoint(**control)
        if '对外采购的CPU及GPU算力资源与超算云服务收入增长的匹配关系如下' not in _compact(page['text']):
            continue
        lines = sorted(page.get('lines', ()), key=lambda line: (line['bbox'][1], line['bbox'][0]))
        if not lines or not page.get('blocks'):
            continue
        titles = []
        for start, line in enumerate(lines):
            if not _compact(line['text']).startswith('报告期内'):
                continue
            for length in range(1, 4):
                selected = lines[start:start+length]
                if len(selected) != length or any(
                        b['bbox'][1]-a['bbox'][3] > 25 for a, b in zip(selected, selected[1:])):
                    break
                if caption_pattern.fullmatch(''.join(_compact(item['text']) for item in selected)):
                    titles.append(selected)
                    break
        for title in titles:
            title_end = max(line['bbox'][3] for line in title)
            parent_candidates = [line for line in lines if _period(line['text'])
                                 and title_end < line['bbox'][1] < title_end+65]
            if len(parent_candidates) != 3:
                continue
            parents = sorted([(_x(line), _period(line['text']), line)
                              for line in parent_candidates])
            if (len({period for _, period, _ in parents}) != 3
                    or any(not period.endswith('Y') for _, period, _ in parents)
                    or max(_y(line) for line in parent_candidates)-min(_y(line) for line in parent_candidates) > 8
                    or min(b[0]-a[0] for a, b in zip(parents, parents[1:])) < 60):
                continue
            parent_bottom = max(line['bbox'][3] for line in parent_candidates)
            children = [line for line in lines if _compact(line['text']) in {'金额', '同比'}
                        and parent_bottom <= line['bbox'][1] < parent_bottom+30]
            if len(children) != 6:
                continue
            period_columns, valid = [], True
            for parent_x, period, parent in parents:
                group = [line for line in children if min(parents, key=lambda value: abs(value[0]-_x(line)))[2] is parent]
                amount = [line for line in group if _compact(line['text']) == '金额']
                growth = [line for line in group if _compact(line['text']) == '同比']
                if len(amount) != 1 or len(growth) != 1 or _x(amount[0]) >= _x(growth[0]):
                    valid = False
                    break
                amount, growth = amount[0], growth[0]
                local_units = [line for line in lines if _compact(line['text']) in {'（万元）', '(万元)'}
                               and 0 <= line['bbox'][1]-amount['bbox'][3] < 12
                               and abs(_x(line)-_x(amount)) < 12]
                changes = [line for line in lines if _compact(line['text']) == '变化'
                           and 0 <= line['bbox'][1]-growth['bbox'][3] < 12
                           and abs(_x(line)-_x(growth)) < 12]
                # A missing local monetary unit cannot borrow another
                # period's unit. The independently labelled growth survives.
                period_columns.extend([
                    (_x(amount), period, 'value', [parent, amount]+local_units, len(local_units) == 1),
                    (_x(growth), period, 'yoy', [parent, growth]+changes, len(changes) == 1),
                ])
            if not valid:
                continue
            columns = sorted(period_columns)
            column_gap = min(b[0]-a[0] for a, b in zip(columns, columns[1:]))
            if column_gap < 20:
                continue
            headers = list(parent_candidates)+children+[line for column in columns for line in column[3][2:]]
            header_end = max(line['bbox'][3] for line in headers)
            label_right = min(line['bbox'][0] for line in children)-5
            # Identify actual row captions within this table body. The table
            # is not re-opened by a later narrative paragraph or adjacent grid.
            label_lines = [line for line in lines if line['bbox'][2] <= label_right
                           and header_end <= line['bbox'][1] < header_end+90]
            row_groups = []
            for start, line in enumerate(label_lines):
                for length in (1, 2):
                    selected = label_lines[start:start+length]
                    label = ''.join(_compact(item['text']) for item in selected)
                    if (len(selected) == length and label in labels and
                            all(b['bbox'][1]-a['bbox'][3] <= 10 for a, b in zip(selected, selected[1:]))):
                        row_groups.append((label, selected))
                        break
            if ([label for label, _ in row_groups] != list(labels)
                    or len(row_groups) != 3):
                continue
            title_proofs = _proofs(page, title, digest)
            table_proofs = _proofs(page, title+headers, digest)
            if not title_proofs or not table_proofs:
                continue
            table_id = digest[:12]+':procurement-revenue:p'+str(page['page'])
            source_table_id = 'source-table-'+hashlib.sha256(
                (digest+title_proofs[0]).encode('utf-8')).hexdigest()[:24]
            for label, row_labels in row_groups:
                row_top, row_bottom = min(line['bbox'][1] for line in row_labels), max(line['bbox'][3] for line in row_labels)
                row_middle = (row_top+row_bottom)/2
                positions = {}
                for cell in lines:
                    value = _VALUE.fullmatch(_compact(cell['text']))
                    if (not value or abs(_y(cell)-row_middle) > 7
                            or not columns[0][0]-column_gap/2 < _x(cell) < columns[-1][0]+column_gap/2):
                        continue
                    index = min(range(len(columns)), key=lambda i: abs(columns[i][0]-_x(cell)))
                    if abs(_x(cell)-columns[index][0]) > column_gap/2:
                        continue
                    positions.setdefault(index, []).append((cell, value))
                legacy_account, registered_account, kind, identifier = labels[label]
                for index, candidates in positions.items():
                    if len(candidates) != 1:
                        continue
                    _, period, relation, own_headers, supported = columns[index]
                    cell, value = candidates[0]
                    if not supported or (relation == 'yoy') != bool(value['percent']):
                        continue
                    record = _record(digest=digest, path=path, date=date, company=company,
                        table_id=table_id, page=page, period=period, kind=kind, identifier=identifier,
                        account=legacy_account, relation=relation, value=value,
                        unit='%' if relation == 'yoy' else '万元', row_label=label,
                        row=[cell]+row_labels, headers=own_headers, title=title,
                        method={'definition_id': 'source:procurement-revenue-grid',
                                'measure': '外采CPU/GPU资源实际采购金额（非营业成本）'
                                           if registered_account == 'PURCHASE_AMOUNT' else label,
                                'issuer_scope': '未声明',
                                'verification_basis': 'source_disclosure_transcription'})
                    if record:
                        record.update(registered_account=registered_account,
                            source_table_id=source_table_id, source_table_evidence=list(table_proofs),
                            source_scope_evidence=[], source_row_index=0,
                            source_relation=relation, column_label='同比变化' if relation == 'yoy' else '金额（万元）')
                        record['reason'] = '原表未声明发行人合并或母公司口径；来源表转述与财务报表范围核实分别验证'
                        output.append(record)
    return output


def extract_source_gaps(pages, *, path, company, doc_sha, disclosure_date):
    from .source_quarter_quotation import extract_source_gaps as quarterly_gaps
    options={'digest':doc_sha,'path':path,'date':disclosure_date,'company':company}
    output=(_customer_rows(pages,**options)+_procurement_rows(pages,**options)
            + quarterly_gaps(pages, path=path, company=company, doc_sha=doc_sha,
                             disclosure_date=disclosure_date))
    output = list({item['resource_id']:item for item in output}.values())
    for item in output:
        validate_source_gap(item)
    return output


def validate_source_gap(item):
    """Validate a frozen diagnostic resource, never promote it to a Fact."""
    from datetime import date
    if not isinstance(item,dict):
        raise ValueError('来源缺口必须为结构化记录')
    for field in ('id','doc_sha256','company','period','dimension_kind','dimension_id','currency','account',
                  'scope','gap_kind','reason','table_label'):
        if not isinstance(item.get(field),str):
            raise ValueError('来源缺口字段缺失或类型错误：'+field)
    if not re.fullmatch(r'sg-[0-9a-f]{24}',item['id']) or not re.fullmatch(r'[0-9a-f]{64}',item['doc_sha256']):
        raise ValueError('来源缺口缺少稳定ID或完整原文SHA-256')
    if item['scope'] not in {'','未知','未声明'} or item.get('eligible_as_fact') is not False:
        raise ValueError('未决来源缺口不得冒充已证明财务口径的Fact')
    if not re.fullmatch(r'20\d{2}(Y|H1|9M|Q[1-4])',item['period']):
        raise ValueError('来源缺口期间必须来自明确列头')
    if item['dimension_kind'] not in {'business','customer','customer_group','purchase_mode','geography'} or not item['dimension_id']:
        raise ValueError('来源缺口必须有明确来源维度')
    if not re.fullmatch(r'[A-Z]{3}',item['currency']):
        raise ValueError('来源缺口币种无效')
    try:
        if date.fromisoformat(item['publish_date']).isoformat()!=item['publish_date']:
            raise ValueError
    except (TypeError,KeyError,ValueError):
        raise ValueError('来源缺口披露日期必须有官方元数据依据') from None
    evidence=item.get('evidence')
    if not isinstance(evidence,list) or not evidence or any(not isinstance(value,str) or not value.startswith(item['doc_sha256'][:12]+':p') for value in evidence):
        raise ValueError('来源缺口证据必须反查已锁定原文')
    if not isinstance(item.get('source_context'),dict) or not item['reason']:
        raise ValueError('来源缺口必须保留上下文和阻断原因')
    if 'raw_value' in item and (not isinstance(item['raw_value'],str) or not Decimal(item['raw_value']).is_finite()):
        raise ValueError('来源查阅金额无效')
    return item
