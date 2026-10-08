"""Frozen business-by-procurement-mode disclosure quotations.

The source table's own business subtotal is its percentage denominator. It
never authorizes an issuer-wide revenue-share formula or consolidation scope.
Source choice and narrative alignment inspect identities and geometry only.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re

from .models import Fact
from .evidence import make_evidence_id

DEFINITION = 'source:business-mode-revenue-grid'
NARRATIVE_SCHEMA = 'source-business-mode-narrative.v1'
BUSINESSES = ('并行通用超算云', '并行行业云', '并行AI云')
MODES = ('外采模式', '共建模式')
_NUM = r'[+\-－−]?\d[\d,，]*(?:\.\d+)?'
_CELL = re.compile(rf'(?P<num>{_NUM})\s*(?P<unit>[%％])?\s*$')
_AMOUNT_SERIES = rf'{_NUM}\s*万元(?:\s*[、，,]\s*|\s*和\s*){_NUM}\s*万元(?:\s*[、，,]\s*|\s*和\s*){_NUM}\s*万元'
_SHARE_SERIES = rf'{_NUM}\s*[%％](?:\s*[、，,]\s*|\s*和\s*){_NUM}\s*[%％](?:\s*[、，,]\s*|\s*和\s*){_NUM}\s*[%％]'
_SERIES = re.compile(
    rf'以(?P<business>{"|".join(BUSINESSES)})为例[，,]\s*'
    rf'(?P<first>20\d{{2}})\s*[-－—至]\s*(?P<last>20\d{{2}})\s*年\s*'
    rf'(?P<mode>{"|".join(MODES)})下形成\s*的?\s*收入分别为\s*'
    rf'(?P<amounts>{_AMOUNT_SERIES})\s*[，,]\s*占比分别为\s*'
    rf'(?P<shares>{_SHARE_SERIES})')


def compact(text):
    return re.sub(r'\s+', '', text)


def digest(data):
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()


def reference(ref):
    return (isinstance(ref,dict) and type(ref.get('start')) is int and type(ref.get('end')) is int
        and isinstance(ref.get('text'),str) and 0 <= ref['start'] < ref['end']
        and ref['end']-ref['start']==len(ref['text']))


def narrative_series(text):
    """Enumerate source occurrences with grammar-defined business/mode/year.

    This grammar deliberately requires complete three-year amount and share
    sequences. Missing units, count/revenue confusion, or unregistered modes
    cannot donate a plausible identity to an isolated number.
    """
    for match in _SERIES.finditer(text):
        first,last=int(match['first']),int(match['last'])
        if last-first!=2:
            continue
        footer=list(re.finditer(r'(?m)^\s*(?:资料|数据)来源\s*[:：].*$', text[:match.start()]))
        prefix_start=max(text.rfind('。',0,match.start())+1,text.rfind('\n\n',0,match.start())+2,
                         footer[-1].end() if footer else 0,0)
        prefix=text[prefix_start:match.start()]
        if re.search(r'模拟|假设|预计|预测|并非|不是|未引用|不引用',prefix+match.group(0)):
            continue
        cells=[]
        for group,account,unit in [('amounts','REVENUE','万元'),('shares','REVENUE_SHARE','%')]:
            values=list(re.finditer(rf'(?P<num>{_NUM})\s*(?:万元|[%％])',match[group]))
            if len(values)!=3:
                break
            for index,value in enumerate(values):
                cells.append({'start':match.start(group)+value.start(),
                    'end':match.start(group)+value.end(), 'period':str(first+index)+'Y',
                    'account':account,'unit':unit,'business':match['business'],'mode':match['mode'],
                    'dimension_id':match['business']+'/'+match['mode']})
        if len(cells)==6:
            yield match,cells


def narrative_subject(text, company):
    """A locked issuer pronoun cannot hide a later corporate subject switch."""
    from .issuer_identity import same_issuer
    first = next(narrative_series(text), None)
    if not first or not company:
        return None
    prefix = compact(text[:first[0].start()])
    head = re.match(r'(?P<subject>.+?)(?P<year>20\d{2})年以来', prefix)
    if not head:
        return None
    subject = head['subject']
    if subject != '公司' and not same_issuer(subject.removesuffix('公司'), company) and not same_issuer(subject, company):
        return None
    tail = prefix[head.end():]
    # The complete leading paragraph is available. A second named company,
    # subsidiary, or parallel construction must not inherit the first pronoun.
    if re.search(r'公司|集团|股份|科技|信息|银行|证券|发行人|子公司|母公司|关联方', tail):
        return None
    if re.search(r'分母|占比[^。]*(?:公司总|总营业收入|总营收|公司营业收入|公司营收)', text):
        return None
    return {'company':company, 'subject':subject, 'prefix':prefix}


def narrative_caption(text, company, business):
    """The adjacent figure names registered businesses of the locked issuer."""
    from .issuer_identity import same_issuer
    title=compact(text)
    title=re.sub(r'^图\d+[:：]', '', title)
    title=re.sub(r'^20\d{2}年', '', title)
    if business not in BUSINESSES or business not in title or '共建模式' not in title:
        return False
    if re.search(r'模拟|假设|预计|预测|示例|并非|不是', title):
        return False
    locations=[(title.find(label),label) for label in BUSINESSES if label in title]
    first=min(locations)[0]
    prefix=title[:first]
    if prefix not in {'','公司'} and not same_issuer(prefix,company):
        return False
    residual=title[first:]
    for label in BUSINESSES:residual=residual.replace(label,'')
    return not re.search(r'公司|集团|股份|科技|信息|银行|证券|发行人|子公司|母公司|关联方', residual)


def narrative_identity(claim):
    """Rebuild only this typed identity from unchanged source characters."""
    source=claim.table_context
    proof=source.get('dimension_source')
    if not isinstance(proof,dict) or proof.get('schema_version')!=NARRATIVE_SCHEMA:
        return None
    body=proof.get('narrative')
    if (not reference(body) or body['text']!=claim.text
            or body['start']+len(claim.text)!=body['end']
            or type(claim.source_numeric_start) is not int
            or body['start']+claim.source_numeric_start!=claim.start
            or claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]!=claim.numeric_text
            or not narrative_subject(claim.text, claim.company)):
        return None
    observations=[cell for _,cells in narrative_series(claim.text) for cell in cells
                  if body['start']+cell['start']==claim.start and body['start']+cell['end']==claim.end]
    if len(observations)!=1:
        return None
    observed=observations[0]
    expected={'dimension_kind':'business','dimension_id':observed['dimension_id'],
        'definition_id':DEFINITION,'account_hint':observed['account'],
        'period_hint':observed['period'],'unit_hint':observed['unit'],'relation_hint':'value'}
    if any(source.get(key)!=value for key,value in expected.items()):
        return None
    if any(proof.get(key)!=observed[key] for key in ('business','mode','dimension_id','period','account')):
        return None
    return {**expected,'account':observed['account'],'period':observed['period'],
        'relation':'value','dimension_source':proof,'business':observed['business'],'mode':observed['mode']}


def extract_source_facts(pages, *, path, company, doc_sha, disclosure_date,
                         disclosure_version, **control):
    """Parse nested business/row groups under explicit year and column labels."""
    from .parser import _parse_checkpoint
    facts=[]
    for page in pages:
        _parse_checkpoint(**control)
        raw=page.get('text','')
        lines=[line for line in page.get('lines',[]) if line.get('bbox') and
            type(line.get('start')) is int and type(line.get('end')) is int and
            raw[line['start']:line['end']]==line.get('text') and line['text'].strip()]
        # Full, geometrically located page header plus a registered exact
        # issuer alias. A filename, subsidiary, or body substring is not proof.
        from .issuer_identity import same_issuer
        headers=[]
        for line in lines:
            if not 0 <= line['bbox'][1] < line['bbox'][3] <= 65:
                continue
            match=re.fullmatch(r'关于(?P<issuer>.+?)公开发行股票并在北交所上市申请文件的审核问询函的回复',
                               compact(line['text']))
            if (match and company and match['issuer'].endswith('股份有限公司')
                    and same_issuer(match['issuer'], company)):
                headers.append(line)
        if len(headers)!=1:
            continue
        titles=[line for line in lines if '结合细分业务收入结构变动量化分析共建模式' in compact(line['text'])]
        if len(titles)!=1:
            continue
        title=titles[0]
        if re.search(r'模拟|假设|预计|预测|示例|并非|不是', title['text']):
            continue
        units=[line for line in lines if compact(line['text'])=='单位：万元'
               and title['bbox'][1]<line['bbox'][1]<title['bbox'][3]+65]
        if len(units)!=1:
            continue
        unit=units[0]
        years=[line for line in lines if re.fullmatch(r'20\d{2}年',compact(line['text']))
               and unit['bbox'][3]<line['bbox'][1]<unit['bbox'][3]+40]
        years.sort(key=lambda line:line['bbox'][0])
        if len(years)!=3 or len({compact(line['text']) for line in years})!=3:
            continue
        leaves=[line for line in lines if compact(line['text']) in {'金额','比例'}
                and max(line['bbox'][3] for line in years)<line['bbox'][1]<unit['bbox'][3]+55]
        leaves.sort(key=lambda line:line['bbox'][0])
        if len(leaves)!=6 or [compact(line['text']) for line in leaves]!=['金额','比例']*3:
            continue
        centres=[(line['bbox'][0]+line['bbox'][2])/2 for line in leaves]
        parent_centres=[(line['bbox'][0]+line['bbox'][2])/2 for line in years]
        if any(not centres[2*i]<parent_centres[i]<centres[2*i+1] for i in range(3)):
            continue
        top=max(line['bbox'][3] for line in leaves)
        labels=[line for line in lines if compact(line['text'])=='外采模式收入' and line['bbox'][1]>top]
        labels.sort(key=lambda line:line['bbox'][1])
        if len(labels)!=3:
            continue
        table_id='source-table-'+digest([doc_sha,page['page'],title['bbox'],
            [compact(y['text']) for y in years],'business-mode-revenue'])[:24]
        for i,start in enumerate(labels):
            end=labels[i+1]['bbox'][1] if i+1<len(labels) else min(
                (line['bbox'][1] for line in lines if line['bbox'][1]>start['bbox'][1]
                 and compact(line['text']).startswith('报告期内，公司超算云服务收入')),default=780)
            group=[line for line in lines if start['bbox'][1]-.5 <= line['bbox'][1]<end]
            business_lines=sorted((line for line in group if line['bbox'][2]<start['bbox'][0]-5),
                                  key=lambda line:line['bbox'][1])
            business=compact(''.join(line['text'] for line in business_lines))
            if business not in BUSINESSES:
                continue
            subtotal=[line for line in group if compact(line['text'])=='总收入']
            if len(subtotal)!=1:
                continue
            rows=[line for line in group if compact(line['text']) in {'外采模式收入','共建模式收入','总收入'}]
            if len(rows)!=3:
                continue
            table_proof=[headers[0],title,unit,*years,*leaves,*business_lines,subtotal[0]]
            table_evidence=tuple(dict.fromkeys(make_evidence_id(doc_sha,page['page'],line['text'].strip(),line['bbox'])
                                             for line in table_proof))
            for row in rows:
                mode=compact(row['text']).removesuffix('收入')
                if mode not in MODES:
                    continue
                y=(row['bbox'][1]+row['bbox'][3])/2
                row_cells=[line for line in group if abs((line['bbox'][1]+line['bbox'][3])/2-y)<=2
                           and line['bbox'][0]>row['bbox'][2]+5]
                assignments={}
                for cell in row_cells:
                    match=_CELL.fullmatch(cell['text'].strip())
                    if not match:continue
                    centre=(cell['bbox'][0]+cell['bbox'][2])/2
                    index=min(range(6),key=lambda n:abs(centres[n]-centre))
                    if abs(centres[index]-centre)>24 or index in assignments:
                        assignments[index]=None
                        continue
                    if bool(match['unit'])!=(index%2==1):
                        continue
                    assignments[index]=(cell,match)
                for index,assigned in assignments.items():
                    if assigned is None:continue
                    cell,match=assigned
                    period=compact(years[index//2]['text'])[:4]+'Y'
                    raw_num=match['num'].replace(',','').replace('，','').replace('－','-').replace('−','-')
                    display='%' if index%2 else '万元'
                    value=Decimal(raw_num)*(Decimal('.01') if index%2 else Decimal('10000'))
                    evidence=tuple(dict.fromkeys([make_evidence_id(doc_sha,page['page'],cell['text'].strip(),cell['bbox']),
                        make_evidence_id(doc_sha,page['page'],row['text'].strip(),row['bbox']),
                        *table_evidence]))
                    account='REVENUE_SHARE' if index%2 else 'REVENUE'
                    identity=[table_id,business,mode,period,account,cell['bbox']]
                    facts.append(Fact(doc_sha[:12]+'-business-mode-'+digest(identity)[:20],
                        company,account,period,scope='未知',nr='未扣非',version=disclosure_version,
                        source_doc=doc_sha[:12],publish_date=disclosure_date,value=value,
                        decimals=len(raw_num.partition('.')[2]),display_unit=display,evidence=evidence,
                        label=business+'/'+compact(row['text']),source_path=str(Path(path)),
                        source_type='financial_source_table',table_label=title['text'].strip(),
                        column_label=compact(years[index//2]['text'])+':'+compact(leaves[index]['text']),
                        parse_conf='0.99',extractor_version='business-mode-source-v1',version_group=doc_sha[:12],
                        dimension_kind='business',dimension_id=business+'/'+mode,definition_id=DEFINITION,
                        source_table_id=table_id,source_table_evidence=table_evidence))
    return facts
