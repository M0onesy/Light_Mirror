"""Product financial grids, addressed by original labels and column geometry.

The MD&A product table and prospectus sales table are independent disclosure
identities. Neither their amounts nor the issuer's total statements establish
consolidation scope. Their cells may only enter an admitted source-table query.
"""
from __future__ import annotations

from decimal import Decimal
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import re

from .evidence import make_evidence_id
from .models import Fact

DEFINITION = 'source:product-financial-grid'
SHARE_DEFINITION = 'source:product-revenue-share-grid'
NARRATIVE_SCHEMA = 'source-product-financial-narrative.v1'
_NUMBER = r'[+\-－−]?\d[\d,，]*(?:\.\d+)?'
_CELL = re.compile(rf'(?P<number>{_NUMBER})\s*(?P<percent>[%％])?\s*$')
_REJECT = re.compile(r'模拟|假设|预计|预测|示例|并非|不是|不引用|未引用')
_REVENUE_SERIES = re.compile(
    rf'从产品收入结构来看[，,]\s*(?P<base>20\d{{2}})\s*年\s*'
    rf'(?P<business>超算云服务)\s*为主要创收模块[，,]\s*营收\s*'
    rf'(?P<before>{_NUMBER}\s*亿元)\s*[，,]\s*(?P<current>20\d{{2}})\s*年\s*'
    rf'进一步增长\s*(?P<growth>{_NUMBER}\s*[%％])\s*至\s*(?P<after>{_NUMBER}\s*亿元)')
_REVENUE_CAPTION = re.compile(
    rf'图\s*\d+\s*[:：]\s*(?P<current>20\d{{2}})\s*年\s*'
    rf'(?P<business>超算云服务)\s*增长\s*(?P<growth>{_NUMBER}\s*[%％])\s*至\s*'
    rf'(?P<after>{_NUMBER}\s*亿元)\s*[（(]单位\s*[:：]\s*万元[）)]')


def compact(text):
    return re.sub(r'\s+', '', text)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()


def narrative_subject(text, company):
    """Require the complete issuer-product paragraph, not a short-name alias."""
    from .issuer_identity import same_issuer
    head=re.match(r'\s*(?P<subject>公司|.+?股份有限公司|.+?科技)主营业务按产品或服务划分可主要划分为四类：',text)
    if not head or not company or _REJECT.search(text):
        return None
    if head['subject'] != '公司' and not same_issuer(head['subject'],company):
        return None
    tail=text[head.end():]
    if re.search(r'子公司|母公司|关联方|集团|股份有限公司|另一公司|同行公司|其他公司',tail):
        return None
    series=list(narrative_series(text))
    if len(series)!=1:
        return None
    if re.search(r'公司|科技|银行|证券|发行人',text[head.end():series[0][0].start()]):
        return None
    return {'company':company,'subject':head['subject'],'source_prefix':text[:head.end()]}


def narrative_series(text):
    """Source-character identities; candidate amounts never affect selection."""
    for match in _REVENUE_SERIES.finditer(text):
        if int(match['current'])-int(match['base'])!=1 or _REJECT.search(text):
            continue
        cells=[]
        for group,period,relation,unit in [('before',match['base']+'Y','value','亿元'),
                                          ('growth',match['current']+'Y','yoy','%'),
                                          ('after',match['current']+'Y','value','亿元')]:
            cells.append({'start':match.start(group),'end':match.end(group),
                'account':'REVENUE','period':period,'relation':relation,'unit':unit,
                'business':match['business'],'dimension_id':match['business'],
                'base_period':match['base']+'Y' if relation=='yoy' else None})
        yield match,cells


def caption_series(text, company, *, narrative):
    """An adjacent complete income paragraph supplies an omitted measure.

    The host still has to prove original adjacency and citation boundaries.
    This helper compares product/year identities; it never compares amounts.
    """
    if not narrative_subject(narrative,company) or _REJECT.search(text):
        return
    observed=list(narrative_series(narrative))
    match=_REVENUE_CAPTION.fullmatch(text.strip())
    if not match or len(observed)!=1:
        return
    source=observed[0][0]
    if match['business']!=source['business'] or match['current']!=source['current']:
        return
    shift=len(text)-len(text.lstrip())
    cells=[{'start':shift+match.start(group),'end':shift+match.end(group),
        'account':'REVENUE','period':match['current']+'Y','relation':relation,'unit':unit,
        'business':match['business'],'dimension_id':match['business'],
        'base_period':source['base']+'Y' if relation=='yoy' else None}
        for group,relation,unit in [('growth','yoy','%'),('after','value','亿元')]]
    yield match,cells


def _reference(value):
    return (isinstance(value,dict) and type(value.get('start')) is int
        and type(value.get('end')) is int and isinstance(value.get('text'),str)
        and 0<=value['start']<value['end'] and value['end']-value['start']==len(value['text']))


def narrative_identity(claim):
    """Rebuild a host-frozen paragraph/caption binding from source characters.

    ``dimension_source`` contains schema_version, observation_kind, narrative
    reference and, for a caption observation, its separate caption reference.
    The host validates document adjacency/citation and signs its source lock;
    this function authorizes only the exactly enumerated numeric occurrence.
    """
    context=claim.table_context
    proof=context.get('dimension_source')
    if not isinstance(proof,dict) or proof.get('schema_version')!=NARRATIVE_SCHEMA:
        return None
    narrative=proof.get('narrative')
    kind=proof.get('observation_kind')
    if not _reference(narrative) or not narrative_subject(narrative['text'],claim.company):
        return None
    if kind=='paragraph':
        body=narrative
        series=narrative_series(body['text'])
    elif kind=='caption':
        body=proof.get('caption')
        if (not _reference(body) or narrative['end']>body['start']
                or context.get('title_source')!=body):
            return None
        series=caption_series(body['text'],claim.company,narrative=narrative['text'])
    else:
        return None
    if (body['text']!=claim.text or type(claim.source_numeric_start) is not int
            or body['start']+claim.source_numeric_start!=claim.start
            or claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]!=claim.numeric_text):
        return None
    cells=[cell for _,group in series for cell in group
           if body['start']+cell['start']==claim.start and body['start']+cell['end']==claim.end]
    if len(cells)!=1:return None
    cell=cells[0]
    expected={'dimension_kind':'business','dimension_id':cell['dimension_id'],
        'definition_id':DEFINITION,'account_hint':cell['account'],'period_hint':cell['period'],
        'unit_hint':cell['unit'],'relation_hint':cell['relation']}
    if any(context.get(key)!=value for key,value in expected.items()):return None
    if any(proof.get(key)!=cell[key] for key in ('business','dimension_id','period','account','relation')):
        return None
    return {**expected,'account':cell['account'],'period':cell['period'],
        'relation':cell['relation'],'base_period':cell['base_period'],
        'dimension_source':proof,'business':cell['business'],
        'quotation_kind':'product_financial_caption' if kind=='caption' else 'product_financial_narrative',
        'company_provenance':'host_locked_issuer_context','company_source_explicit':False}


def _lines(page):
    """Reject forged offsets; local reparsing may reconstruct exact text offsets."""
    raw = page.get('text', '')
    output, cursor = [], 0
    for line in page.get('lines', []):
        box, text = line.get('bbox'), line.get('text')
        if (not isinstance(text, str) or not text.strip() or not isinstance(box, (list, tuple))
                or len(box) != 4 or any(type(x) not in {int, float} or not math.isfinite(x) for x in box)
                or not box[0] < box[2] or not box[1] < box[3]):
            continue
        start, end = line.get('start'), line.get('end')
        if 'start' in line or 'end' in line:
            if type(start) is not int or type(end) is not int or raw[start:end] != text:
                continue
        else:
            start = raw.find(text, cursor)
            if start < 0:
                continue
            end = start + len(text)
        cursor = end
        blocks = [block for block in page.get('blocks', []) if block.get('bbox')
                  and isinstance(block.get('text'), str) and text.strip() in block['text']
                  and block['text'].strip() in raw
                  and block['bbox'][0]-.2 <= box[0] and block['bbox'][1]-.2 <= box[1]
                  and block['bbox'][2]+.2 >= box[2] and block['bbox'][3]+.2 >= box[3]]
        if not blocks:
            continue
        block = min(blocks, key=lambda b:(b['bbox'][2]-b['bbox'][0])*(b['bbox'][3]-b['bbox'][1]))
        output.append({**line, 'start': start, 'end': end, 'page': page['page'], 'source_block':block})
    return output


def _mid(line, axis):
    return (line['bbox'][axis] + line['bbox'][axis+2]) / 2


def _evidence(doc_sha, lines):
    return tuple(dict.fromkeys(make_evidence_id(doc_sha, line['page'], line['source_block']['text'].strip(), line['source_block']['bbox'])
                               for line in lines))


def _document_identity(pages, company):
    """An exact cover/metadata issuer, never the filename or a body mention."""
    from .issuer_identity import same_issuer
    proof = []
    year_proof = []
    for page in pages:
        if page['page'] > 8:
            continue
        lines = _lines(page)
        for line in lines:
            text = compact(line['text'])
            # Only stand-alone legal names near the front of the document.
            if (page['page'] == 1 and text.endswith('股份有限公司')
                    and len(text) < 60 and company and same_issuer(text, company)):
                proof.append(line)
            if text == '公司中文全称':
                matches = [other for other in lines if abs(_mid(line, 1)-_mid(other, 1)) < 3
                           and other['bbox'][0] > line['bbox'][2]+10
                           and compact(other['text']).endswith('股份有限公司')
                           and company and same_issuer(compact(other['text']), company)]
                if len(matches) == 1:
                    proof.extend([line, matches[0]])
            if text == '公司年度报告':
                matches = [other for other in lines if abs(_mid(line, 1)-_mid(other, 1)) < 3
                           and other['bbox'][0] > line['bbox'][2]+10
                           and re.fullmatch(r'20\d{2}年年度报告', compact(other['text']))]
                if len(matches) == 1:
                    year_proof.extend([line, matches[0]])
    years = {compact(line['text'])[:4] for line in year_proof
             if re.fullmatch(r'20\d{2}年年度报告', compact(line['text']))}
    return proof, (next(iter(years)) if len(years) == 1 else ''), year_proof


def _fact(*, doc_sha, path, company, disclosure_date, disclosure_version, table_id,
          table_proof, title, row, cell, period, account, unit, relation='value', definition=DEFINITION):
    match = _CELL.fullmatch(cell['text'].strip())
    if not match or bool(match['percent']) != (unit == '%'):
        return None
    number = match['number'].replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')
    factor = Decimal('.01') if unit == '%' else Decimal('10000') if unit == '万元' else Decimal(1)
    label = compact(''.join(line['text'] for line in row))
    if not label or _REJECT.search(label) or not re.fullmatch(r'[\u4e00-\u9fffA-Za-z，、]+', label):
        return None
    return Fact(doc_sha[:12]+'-product-'+digest([table_id,label,period,account,relation,cell['bbox']])[:20],
        company, account, period, scope='未知', nr='未扣非', version=disclosure_version,
        source_doc=doc_sha[:12], publish_date=disclosure_date, value=Decimal(number)*factor,
        decimals=len(number.partition('.')[2]), display_unit=unit,
        evidence=_evidence(doc_sha, [cell, *row, *table_proof]), label=label,
        source_path=str(Path(path)), source_type='financial_source_table',
        table_label=title['text'].strip(), column_label=period+':'+account+':'+relation,
        parse_conf='0.99', extractor_version='product-financial-source-v1', version_group=doc_sha[:12],
        dimension_kind='business', dimension_id=label, definition_id=definition,
        source_table_id=table_id, source_table_evidence=_evidence(doc_sha, table_proof),
        source_relation=relation)


def _row_groups(lines, top, bottom, label_right, *, minimum_y_gap=20, vectors=()):
    labels = sorted((line for line in lines if top < _mid(line, 1) < bottom
                     and line['bbox'][2] < label_right and re.search(r'[\u4e00-\u9fffA-Za-z]', line['text'])
                     and not re.search(r'单位|来源|百分|金额|占比', line['text'])),
                    key=lambda line: line['bbox'][1])
    groups = []
    for line in labels:
        # Original ruled row boundaries distinguish a wrapped name from the
        # next product even when both gaps have exactly the same height.
        boundaries = any(abs(vector[1]-vector[3]) < .2 and abs(vector[0]-vector[2]) > 25
                         and min(vector[0],vector[2]) <= line['bbox'][0]+1
                         and max(vector[0],vector[2]) >= line['bbox'][2]-1
                         and groups[-1][-1]['bbox'][3] < vector[1] < line['bbox'][1]
                         for vector in vectors) if groups else False
        if (groups and line['bbox'][1]-groups[-1][-1]['bbox'][3] <= minimum_y_gap
                and not boundaries):
            groups[-1].append(line)
        else:
            groups.append([line])
    return groups


def _annual_grids(pages, identity, year, year_proof, *, controls, factory):
    from .parser import _parse_checkpoint
    output = []
    if not year:
        return output
    for page_index, page in enumerate(pages):
        _parse_checkpoint(**controls)
        lines = _lines(page)
        for title in [line for line in lines if compact(line['text']) == '按产品分类分析：']:
            units = [line for line in lines if compact(line['text']) == '单位：元'
                     and title['bbox'][3] < line['bbox'][1] < title['bbox'][3]+40]
            if len(units) != 1:
                continue
            preceding = [line for line in lines if re.fullmatch(r'20\d{2}年', compact(line['text']))
                         and title['bbox'][1]-150 < line['bbox'][1] < title['bbox'][1]]
            preceding.sort(key=lambda line: line['bbox'][0])
            if len(preceding) != 2 or compact(preceding[0]['text']) != year+'年':
                continue
            head = [line for line in lines if units[0]['bbox'][3] < line['bbox'][1]
                    and line['bbox'][3] < units[0]['bbox'][3]+70]
            exact = {label:[line for line in head if compact(line['text']) == label]
                     for label in ('分产品', '营业收入', '营业成本', '毛利率%')}
            if len(exact['营业成本']) == 2:
                exact['营业成本'] = [min(exact['营业成本'], key=lambda line: line['bbox'][0])]
            if any(len(v) != 1 for v in exact.values()):
                continue
            centres = [_mid(exact[key][0], 0) for key in ('分产品','营业收入','营业成本','毛利率%')]
            growth_heads = [line for line in head if compact(line['text']) == '营业收入比']
            cost_heads = [line for line in head if compact(line['text']) == '营业成本']
            # The exact cost amount header already exists; a distinct lane is
            # required to admit the cost growth column.
            cost_heads = [line for line in cost_heads if _mid(line, 0) > centres[-1]+40]
            if len(growth_heads) != 1 or len(cost_heads) != 1:
                continue
            centres.extend([_mid(growth_heads[0], 0), _mid(cost_heads[0], 0)])
            if any(centres[i+1]-centres[i] < 35 for i in range(5)):
                continue
            for index in (4,5):
                leaf = sorted((line for line in head if abs(_mid(line,0)-centres[index])<25),
                              key=lambda line:line['bbox'][1])
                required = '营业收入比上年同期增减%' if index==4 else '营业成本比上年同期增减%'
                if compact(''.join(line['text'] for line in leaf)) != required:
                    break
            else:
                proof = [*identity, *year_proof, title, units[0], *preceding, *head]
                table_id = 'source-table-'+digest([factory['doc_sha'],page['page'],title['bbox'],'annual-product',year])[:24]
                segments = [(page, lines, max(line['bbox'][3] for line in head), 766)]
                if page_index+1 < len(pages) and pages[page_index+1]['page'] == page['page']+1:
                    next_page=pages[page_index+1]
                    next_lines=_lines(next_page)
                    # Continuation is bounded by the next explicitly titled
                    # grid; do not lend these headers to a new product table.
                    endings=[line['bbox'][1] for line in next_lines
                             if re.fullmatch(r'按.+分类分析：',compact(line['text']))]
                    if endings:
                        segments.append((next_page,next_lines,65,min(endings)))
                for segment_page, segment, top, bottom in segments:
                    groups=_row_groups(segment,top,bottom,(centres[0]+centres[1])/2,minimum_y_gap=5,
                                       vectors=segment_page.get('vector_lines',()))
                    for position,row in enumerate(groups):
                        if compact(''.join(line['text'] for line in row)) in {'合计','总计'}:
                            continue
                        row_top=row[0]['bbox'][1]-2
                        row_bottom=row[-1]['bbox'][3]+2
                        cells=[line for line in segment if row_top <= _mid(line,1) <= row_bottom
                               and line['bbox'][0] > (centres[0]+centres[1])/2]
                        assigned={}
                        for cell in cells:
                            lane=min(range(1,6),key=lambda n:abs(_mid(cell,0)-centres[n]))
                            if abs(_mid(cell,0)-centres[lane]) > 25:
                                continue
                            assigned[lane]=cell if lane not in assigned else None
                        for lane,cell in assigned.items():
                            if cell is None:continue
                            account=('REVENUE','COST','GROSS_MARGIN','REVENUE','COST')[lane-1]
                            fact=_fact(**factory,table_id=table_id,table_proof=proof,title=title,row=row,cell=cell,
                                period=year+'Y',account=account,unit='元' if lane in {1,2} else '%',
                                relation='yoy' if lane in {4,5} else 'value')
                            if fact:output.append(fact)
    return output


def _prospectus_grids(pages, identity, *, controls, factory):
    from .parser import _parse_checkpoint
    output=[]
    for index,page in enumerate(pages):
        _parse_checkpoint(**controls)
        lines=_lines(page)
        titles=[line for line in lines if compact(line['text']) ==
                '报告期内，公司主营业务收入按产品或服务类型划分情况如下：']
        for title in titles:
            parents=[line for line in lines if line['bbox'][1] > title['bbox'][3]
                     and re.fullmatch(r'20\d{2}年(?:度|1[-－]6月)', compact(line['text']))]
            parents.sort(key=lambda line:line['bbox'][0])
            if len(parents)!=4 or len({compact(line['text']) for line in parents})!=4:
                continue
            if index+1>=len(pages) or pages[index+1]['page']!=page['page']+1 or min(p['bbox'][1] for p in parents)<650:
                continue
            next_page=pages[index+1]
            body=_lines(next_page)
            head=[line for line in body if 60<line['bbox'][1]<105]
            amounts=sorted((line for line in head if compact(line['text'])=='金额'),key=lambda l:l['bbox'][0])
            units=sorted((line for line in head if compact(line['text'])=='（万元）'),key=lambda l:l['bbox'][0])
            if len(amounts)!=4 or len(units)!=4 or any(abs(_mid(a,0)-_mid(u,0))>4 for a,u in zip(amounts,units)):
                continue
            shares=[]
            for i,amount in enumerate(amounts):
                lower=_mid(amount,0)+15
                upper=_mid(amounts[i+1],0)-15 if i<3 else 525
                leaf=sorted((line for line in head if lower<_mid(line,0)<upper),key=lambda l:l['bbox'][1])
                if compact(''.join(line['text'] for line in leaf))!='占比（%）':
                    break
                shares.append(leaf)
            if len(shares)!=4:
                continue
            centres=[value for a,s in zip(amounts,shares) for value in (_mid(a,0),_mid(s[0],0))]
            if any(not centres[2*i]<_mid(parent,0)<centres[2*i+1] for i,parent in enumerate(parents)):
                continue
            endings=[line['bbox'][1] for line in body if compact(line['text']).startswith('报告期内，公司主营业务收入按销售模式')]
            if len(endings)!=1:
                continue
            top=max(line['bbox'][3] for line in head)
            groups=_row_groups(body,top,endings[0],min(centres)-20,minimum_y_gap=3,
                               vectors=next_page.get('vector_lines',()))
            table_id='source-table-'+digest([factory['doc_sha'],page['page'],title['bbox'],'prospectus-product'])[:24]
            proof=[*identity,title,*parents,*head]
            for row in groups:
                label=compact(''.join(line['text'] for line in row))
                if label=='合计':continue
                candidates=[line for line in body if row[0]['bbox'][1]-1<=_mid(line,1)<=row[-1]['bbox'][3]+1
                            and _mid(line,0)>min(centres)-20]
                assigned={}
                for cell in candidates:
                    lane=min(range(8),key=lambda n:abs(_mid(cell,0)-centres[n]))
                    if abs(_mid(cell,0)-centres[lane])>18:continue
                    # Percentage source cells need no repeated suffix when
                    # their own original leaf explicitly carries (%).
                    if lane%2 and _CELL.fullmatch(cell['text'].strip()):
                        cell={**cell,'text':cell['text'].strip()+'%','source_original':cell}
                    assigned[lane]=cell if lane not in assigned else None
                for lane,cell in assigned.items():
                    if cell is None:continue
                    parent=compact(parents[lane//2]['text'])
                    period=parent[:4]+('H1' if '1-6月' in parent or '1－6月' in parent else 'Y')
                    original=cell.get('source_original')
                    fact=_fact(**factory,table_id=table_id,table_proof=proof,title=title,row=row,cell=cell,
                        period=period,account='REVENUE_SHARE' if lane%2 else 'REVENUE',
                        unit='%' if lane%2 else '万元',definition=SHARE_DEFINITION if lane%2 else DEFINITION)
                    if fact:
                        if original:
                            from dataclasses import replace
                            fact=replace(fact,evidence=_evidence(factory['doc_sha'],[original,*row,*proof]))
                        output.append(fact)
    return output


def extract_source_facts(pages, *, path, company, doc_sha, disclosure_date, disclosure_version, **control):
    """Extract complete known grids while retaining exact independent cells."""
    identity,year,year_proof=_document_identity(pages,company)
    if not identity:return []
    factory=dict(path=path,company=company,doc_sha=doc_sha,disclosure_date=disclosure_date,
                 disclosure_version=disclosure_version)
    return [*_annual_grids(pages,identity,year,year_proof,controls=control,factory=factory),
            *_prospectus_grids(pages,identity,controls=control,factory=factory)]


def _citation_families(text):
    if (not isinstance(text,str) or not re.fullmatch(r'(?:数据|资料)来源\s*[:：].+',text.strip())
            or _REJECT.search(text) or re.search(r'非公司|非官方|不来自',text)):
        return set()
    families=set()
    if re.search(r'公司招股(?:说明)?书',text):families.add('prospectus')
    if re.search(r'公司(?:公告|年报|年度报告)',text):families.add('annual')
    return families


def _family(report):
    name=report.get('name','')
    if re.search(r'招股(?:说明)?书',name):return 'prospectus'
    if re.search(r'年度报告|年报',name) and not re.search(r'半年|半年度|摘要',name):return 'annual'
    return ''


def _geometry(reference,pages,text):
    candidates=[]
    for page in pages or []:
        selected=[line for line in page.get('lines',[]) if _reference(line)
                  and line['start']<reference['end'] and line['end']>reference['start']
                  and text[line['start']:line['end']]==line['text']
                  and isinstance(line.get('bbox'),list) and len(line['bbox'])==4]
        if selected:
            selected.sort(key=lambda line:line['start'])
            candidates.append({'page':page['page'],'bbox':[min(l['bbox'][0] for l in selected),
                min(l['bbox'][1] for l in selected),max(l['bbox'][2] for l in selected),max(l['bbox'][3] for l in selected)],
                'lines':[{key:line[key] for key in ('start','end','text','bbox')} for line in selected]})
    if len(candidates)!=1:return None
    chosen=candidates[0]
    covered=set()
    for line in chosen['lines']:
        covered.update(range(max(reference['start'],line['start']),min(reference['end'],line['end'])))
    if any(not text[i].isspace() and i not in covered for i in range(reference['start'],reference['end'])):
        return None
    return chosen


def _valid_geometry(reference,layout):
    if (not _reference(reference) or not isinstance(layout,dict) or type(layout.get('page')) is not int
            or layout['page']<1 or not isinstance(layout.get('bbox'),list) or len(layout['bbox'])!=4
            or not isinstance(layout.get('lines'),list) or not layout['lines']):return False
    lines=layout['lines']
    if any(not _reference(line) or not isinstance(line.get('bbox'),list) or len(line['bbox'])!=4
           or any(type(x) not in {int,float} or not math.isfinite(x) for x in line['bbox'])
           or not line['bbox'][0]<line['bbox'][2] or not line['bbox'][1]<line['bbox'][3] for line in lines):
        return False
    bounding=[min(l['bbox'][0] for l in lines),min(l['bbox'][1] for l in lines),
              max(l['bbox'][2] for l in lines),max(l['bbox'][3] for l in lines)]
    if layout['bbox']!=bounding:return False
    for line in lines:
        left,right=max(reference['start'],line['start']),min(reference['end'],line['end'])
        if left>=right or reference['text'][left-reference['start']:right-reference['start']]!=line['text'][left-line['start']:right-line['start']]:
            return False
    return all(char.isspace() or any(line['start']<=reference['start']+i<line['end'] for line in lines)
               for i,char in enumerate(reference['text']))


def validate_reference_bounds(proof,context,claim):
    identity=narrative_identity(claim)
    if not identity or proof.get('quotation_kind')!=identity['quotation_kind']:return False
    dimension=identity['dimension_source']
    narrative,caption,citation=proof.get('narrative'),proof.get('caption'),proof.get('citation')
    layout=proof.get('source_geometry')
    if (not all(_reference(item) for item in (narrative,caption,citation)) or not isinstance(layout,dict)
            or narrative!=dimension.get('narrative') or caption!=context.get('title_source')
            or dimension.get('source_geometry')!=layout
            or proof.get('source_geometry_sha256')!=digest(layout)
            or not all(_valid_geometry(item,layout.get(key)) for key,item in
                       [('narrative',narrative),('caption',caption),('citation',citation)])
            or narrative['end']>caption['start'] or caption['end']>citation['start']):
        return False
    body_page,cap_page,cite_page=(layout[key]['page'] for key in ('narrative','caption','citation'))
    if (cap_page!=cite_page or cite_page not in {body_page,body_page+1}
            or not layout['caption']['bbox'][3]<layout['citation']['bbox'][1]
            or body_page==cap_page and not layout['narrative']['bbox'][3]<layout['caption']['bbox'][1]):
        return False
    if dimension.get('observation_kind')=='paragraph':
        return narrative['start']<=claim.start<claim.end<=narrative['end']
    return (dimension.get('observation_kind')=='caption' and dimension.get('caption')==caption
            and caption['start']<=claim.start<claim.end<=caption['end'])


def allowed_reference(proof,context,claim):
    identity=narrative_identity(claim)
    return bool(identity and proof.get('quotation_kind')==identity['quotation_kind']
        and proof.get('source_report_family') in _citation_families((proof.get('citation') or {}).get('text',''))
        and proof.get('source_definition_id')==context.get('definition_id')==DEFINITION
        and proof.get('dimension_kind')==context.get('dimension_kind')=='business'
        and proof.get('source_dimension_id')==proof.get('dimension_id')==identity['dimension_id']
        and proof.get('account')==identity['account']==claim.account
        and proof.get('period')==identity['period']==claim.period
        and proof.get('relation')==identity['relation']==claim.relation
        and proof.get('source_account_unit')==claim.unit
        and validate_reference_bounds(proof,context,claim))


def link(claims,text,facts,reports,cutoff,pages=None):
    """Admit five original paragraph/caption observations with source geometry.

    Documents and grids are selected by explicit citation families, exact
    product label, period, registered definition and disclosure date. Missing
    cells and conflicts keep their observations; query/Decimal Judge decides.
    """
    from .source_table_reference import (_date,_citation_report_matches,_declared_report_matches,
        SCHEMA as REFERENCE_SCHEMA,TABLE_SCOPE)
    claims=list(claims)
    if not _date(cutoff) or not pages:return claims,[]
    registry={report['sha256'][:12]:report for report in reports
        if re.fullmatch(r'[a-f0-9]{64}',str(report.get('sha256','')))
        and _date(report.get('publish_date')) and report['publish_date']<=cutoff}
    replacements={};diagnostics=[]
    heads=list(re.finditer(r'(?m)^[ \t]*(?:公司|[^\n]+?股份有限公司|[^\n]+?科技)主营业务按产品或服务划分可主要划分为四类：',text))
    for head in heads:
        suffix=text[head.start():head.start()+450]
        series=list(narrative_series(suffix))
        if len(series)!=1:continue
        match,cells=series[0]
        body={'start':head.start(),'end':head.start()+match.end(),'text':suffix[:match.end()]}
        # A new caption or source note ends the paragraph-to-figure relation.
        boundaries=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[body['end']:]))
        if not boundaries:continue
        first=boundaries[0]
        left=body['end']+first.start()+len(first[0])-len(first[0].lstrip())
        caption={'start':left,'end':left+len(first[0].strip()),'text':first[0].strip()}
        company=next((c.company for c in claims if c.start==body['start']+cells[0]['start']),'')
        if not narrative_subject(body['text'],company):continue
        caption_observed=list(caption_series(caption['text'],company,narrative=body['text']))
        if len(caption_observed)!=1:continue
        following=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[caption['end']:]))
        if not following:continue
        first=following[0]
        left=caption['end']+first.start()+len(first[0])-len(first[0].lstrip())
        citation={'start':left,'end':left+len(first[0].strip()),'text':first[0].strip()}
        families=_citation_families(citation['text'])
        if not families:continue
        geometry={key:_geometry(value,pages,text) for key,value in
                  [('narrative',body),('caption',caption),('citation',citation)]}
        if any(value is None for value in geometry.values()):continue
        observations=[('paragraph',body,cell) for cell in cells]
        observations.extend(('caption',caption,cell) for cell in caption_observed[0][1])
        for kind,source,cell in observations:
            originals=[claim for claim in claims if claim.start==source['start']+cell['start']
                       and claim.end==source['start']+cell['end']]
            if len(originals)!=1:continue
            original=originals[0]
            if original.company!=company:continue
            if original.query_strategy in {'pinned','same_report'} and not original.declared_source:continue
            raw=source['text'][cell['start']:cell['end']]
            number=raw.removesuffix(cell['unit']).strip()
            if not re.fullmatch(_NUMBER,number):continue
            dimension={'schema_version':NARRATIVE_SCHEMA,'observation_kind':kind,'narrative':body,
                'source_geometry':geometry,**{key:cell[key] for key in ('business','dimension_id','period','account','relation')}}
            if kind=='caption':dimension['caption']=caption
            context={key:value for key,value in original.table_context.items()
                     if key not in {'source_dimensions','source_table_reference'}}
            context.update(title=caption['text'],title_source=caption,period_source='source_header',
                account_hint=cell['account'],period_hint=cell['period'],relation_hint=cell['relation'],scope_hint='未知',
                unit_hint=cell['unit'],dimension_kind='business',dimension_id=cell['dimension_id'],definition_id=DEFINITION,
                dimension_source=dimension,layout_source='source_product_financial',row_label=cell['dimension_id'],
                currency='CNY',evaluation_scope='issuer_historical_financial')
            value=Decimal(number.replace(',','').replace('，','').replace('－','-').replace('−','-'))
            value*=Decimal('.01') if cell['unit']=='%' else Decimal('100000000')
            typed=replace(original,text=source['text'],numeric_text=raw,source_numeric_start=cell['start'],
                account=cell['account'],period=cell['period'],relation=cell['relation'],base_period=cell['base_period'],
                candidate_value=str(value),unit=cell['unit'],display_decimals=len(number.partition('.')[2]),
                table_context=context,parse_status='parsed',binding_status='unresolved')
            groups={}
            for fact in facts:
                report=registry.get(fact.source_doc)
                if (not report or _family(report) not in families or fact.company!=company
                        or fact.definition_id!=DEFINITION or fact.dimension_kind!='business'
                        or fact.source_type!='financial_source_table' or fact.publish_date!=report['publish_date']
                        or company not in (report.get('companies') or [report.get('company')])
                        or not fact.source_table_id or not fact.source_table_evidence
                        or not _citation_report_matches(citation['text'],report)
                        or original.declared_source and not _declared_report_matches(original.declared_source,report)):
                    continue
                groups.setdefault((fact.source_doc,fact.source_table_id),[]).append(fact)
            eligible=[(registry[doc]['publish_date'],doc,table,rows) for (doc,table),rows in groups.items()
                if any(row.dimension_id==cell['dimension_id'] and row.period==cell['period'] for row in rows)]
            if not eligible:
                diagnostics.append({'kind':'missing_product_source_table','claim_id':original.claim_id,
                    'period':cell['period'],'account':cell['account']})
                replacements[typed.claim_id]=typed
                continue
            latest=max(item[0] for item in eligible)
            eligible=[item for item in eligible if item[0]==latest]
            if len({item[1] for item in eligible})!=1:
                diagnostics.append({'kind':'ambiguous_product_source_document','claim_id':original.claim_id})
                replacements[typed.claim_id]=typed
                continue
            document=eligible[0][1];report=registry[document];tables=sorted(item[2] for item in eligible)
            proof={'schema_version':REFERENCE_SCHEMA,'company':company,'claim_start':typed.start,'claim_end':typed.end,
                'source_text_sha256':hashlib.sha256(typed.text.encode('utf8')).hexdigest(),
                'caption':caption,'citation':citation,'narrative':body,
                'quotation_kind':'product_financial_caption' if kind=='caption' else 'product_financial_narrative',
                'source_geometry':geometry,'source_geometry_sha256':digest(geometry),'period':typed.period,
                'account':typed.account,'relation':typed.relation,'dimension_kind':'business',
                'dimension_id':cell['dimension_id'],'source_dimension_id':cell['dimension_id'],'source_definition_id':DEFINITION,
                'source_row_index':0,'source_doc':document,'report_sha256':report['sha256'],
                'report_id':report.get('report_id',''),'publish_date':report['publish_date'],'cutoff':cutoff,
                'source_table_id':tables[0],'source_table_ids':tables,
                'source_table_evidence':sorted({eid for item in eligible for fact in item[3] for eid in fact.source_table_evidence}),
                'source_report_family':_family(report),'source_account_unit':typed.unit,
                'source_selection':'explicit_product_citation_family_period_and_latest_eligible_disclosure',
                'dimension_mapping_basis':'exact_original_product_revenue_paragraph_and_adjacent_caption',
                'accounting_scope':'未从引用或金额推断合并或母公司'}
            proof['sha256']=digest(proof)
            fresh=replace(typed,table_context={**context,'source_table_reference':proof},declared_source=document,
                query_strategy='pinned',caliber_hint=(TABLE_SCOPE,'未扣非'))
            if allowed_reference(proof,fresh.table_context,fresh):replacements[fresh.claim_id]=fresh
    return [replacements.get(claim.claim_id,claim) for claim in claims],diagnostics
