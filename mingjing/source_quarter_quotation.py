"""Source-addressed cloud quarterly shares, with exact temporal denominators.

Neither a matching percentage nor issuer-wide statements selects the source.
The disclosure's cover, physical header lanes, year/quarter labels and bounded
continuation identify its table.  Draft observations keep every occurrence.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path
import re

from .models import Fact
from .source_product_financial import (compact, digest, _lines, _evidence,
    _geometry, _valid_geometry, _reference, _mid)

SCHEMA = 'source-quarter-quotation.v1'
NARRATIVE_SCHEMA = SCHEMA
DEFINITION = 'source:quarter-cloud-revenue-share'
MODE_DEFINITION = 'source:quarter-cloud-business-mode-revenue-share'
KINDS = {'quarter_revenue_share_narrative', 'business_mode_quarter_share_caption'}
_NUMBER = r'[+\-－−]?\d[\d,，]*(?:\.\d+)?'
_PERCENT = re.compile(rf'(?P<number>{_NUMBER})(?P<unit>[%％])')
_REJECT = re.compile(r'模拟|假设|预计|预测|示例|并非|不是|不引用|未引用|子公司|母公司|关联方|另一公司|其他公司|同行公司')
_DRAFT_SERIES = re.compile(rf'由于公司超算云服务具有一定季节性，第四季度普遍实现收入较多，'
    rf'(?P<first>20\d{{2}})[-－—至](?P<last>20\d{{2}})年第[四4]季度占超算云服务全年收入的比例分别为'
    rf'(?P<a>{_NUMBER}[%％])、(?P<b>{_NUMBER}[%％])和(?P<c>{_NUMBER}[%％])')
_SOURCE_SERIES = re.compile(rf'由于公司超算云服务具有一定季节性，第四季度普遍实现收入较多，'
    rf'占各期超算云服务的比例分别为(?P<a>{_NUMBER}[%％])、(?P<b>{_NUMBER}[%％])和(?P<c>{_NUMBER}[%％])')
_CAPTION = re.compile(rf'图\d+[:：]共建模式收入占比逐渐提高，(?P<year>20\d{{2}})Q(?P<quarter>[1-4])达到(?P<value>{_NUMBER}[%％])')
_TITLE = re.compile(r'公司超算云服务(?P<first>20\d{2})年至(?P<last>20\d{2})年各季度总体毛利率、外购和共建模式毛利率情况如下[:：]')
_SECTION = re.compile(r'（二）说明(?P<a>20\d{2})年、(?P<b>20\d{2})年和(?P<c>20\d{2})年第[四4]季度毛利率高于其他季度的原因。')
_THRESHOLD = re.compile(rf'至(?P<year>20\d{{2}})年，公司以共建模式提供的超算云服务收入占总体超算云服务收入的比例已超过(?P<value>{_NUMBER}[%％])')
_THRESHOLD_REASON = '官方披露共建模式收入占总体超算云服务收入的严格下界；按来源界限断言核查，不将下界当成精确比例；专业规则签发待完成'


def _compact_source(text):
    positions=[index for index,char in enumerate(text) if not char.isspace()]
    return ''.join(text[index] for index in positions),positions


def _raw_span(match,positions,group=0):
    left,right=match.span(group)
    return positions[left],positions[right-1]+1


def _raw_ref(text,match,positions,group=0):
    left,right=_raw_span(match,positions,group)
    return {'start':left,'end':right,'text':text[left:right]}


def _subject(text,company):
    if not company or _REJECT.search(text):return False
    body=compact(text)
    if not body.startswith('公司逐步加大了对专用设备的采购以丰富共建模式下自有算力资源池，'):return False
    # The whole original paragraph is available; an intervening corporate
    # subject may not inherit the host's issuer pronoun.
    residual=body.replace('公司','')
    return not re.search(r'股份有限公司|集团|科技|证券|银行|发行人',residual)


def narrative_series(text):
    source,positions=_compact_source(text)
    for match in _DRAFT_SERIES.finditer(source):
        first,last=int(match['first']),int(match['last'])
        if last-first!=2 or _REJECT.search(source[:match.end()]):continue
        cells=[]
        for index,group in enumerate(('a','b','c')):
            left,right=_raw_span(match,positions,group)
            cells.append({'start':left,'end':right,'account':'REVENUE_SHARE',
                'period':f'{first+index}Q4','relation':'value','unit':'%',
                'dimension_kind':'business','dimension_id':'超算云服务','definition_id':DEFINITION})
        yield match,cells


def caption_series(text,company,*,narrative):
    if not _subject(narrative,company) or _REJECT.search(text):return
    series=list(narrative_series(narrative));source,positions=_compact_source(text)
    match=_CAPTION.fullmatch(source)
    if not match or len(series)!=1:return
    if not int(series[0][0]['first'])<=int(match['year'])<=int(series[0][0]['last']):return
    left,right=_raw_span(match,positions,'value')
    yield match,[{'start':left,'end':right,'account':'REVENUE_SHARE',
        'period':match['year']+'Q'+match['quarter'],'relation':'value','unit':'%',
        'dimension_kind':'business','dimension_id':'超算云服务/共建模式','definition_id':MODE_DEFINITION}]


def narrative_identity(claim):
    context=claim.table_context;dimension=context.get('dimension_source')
    if not isinstance(dimension,dict) or dimension.get('schema_version')!=SCHEMA:return None
    narrative=dimension.get('narrative');kind=dimension.get('observation_kind')
    if not _reference(narrative) or not _subject(narrative['text'],claim.company):return None
    if kind=='paragraph':
        body=narrative;series=narrative_series(body['text']);quotation='quarter_revenue_share_narrative'
    elif kind=='caption':
        body=dimension.get('caption');quotation='business_mode_quarter_share_caption'
        if not _reference(body) or context.get('title_source')!=body:return None
        series=caption_series(body['text'],claim.company,narrative=narrative['text'])
    else:return None
    if (body['text']!=claim.text or type(claim.source_numeric_start) is not int
            or body['start']+claim.source_numeric_start!=claim.start
            or claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]!=claim.numeric_text):return None
    cells=[cell for _,rows in series for cell in rows
        if body['start']+cell['start']==claim.start and body['start']+cell['end']==claim.end]
    if len(cells)!=1:return None
    cell=cells[0]
    expected={'account_hint':cell['account'],'period_hint':cell['period'],'unit_hint':'%',
        'relation_hint':'value','dimension_kind':'business','dimension_id':cell['dimension_id'],
        'definition_id':cell['definition_id']}
    if any(context.get(key)!=value for key,value in expected.items()):return None
    if any(dimension.get(key)!=cell[key] for key in ('period','account','relation','dimension_id','definition_id')):return None
    return {**expected,'account':cell['account'],'period':cell['period'],'relation':'value',
        'quotation_kind':quotation,'dimension_source':dimension,
        'company_provenance':'host_locked_issuer_context','company_source_explicit':False}


def _cover(pages,company):
    from .issuer_identity import same_issuer
    covers=[p for p in pages if p['page']==1]
    if len(covers)!=1:return []
    page=covers[0];lines=_lines(page)
    issuer=[]
    for line in lines:
        match=re.fullmatch(r'关于(?P<issuer>.+股份有限公司)',compact(line['text']))
        if match and same_issuer(match['issuer'],company):issuer.append(line)
    family=[line for line in lines if compact(line['text'])=='第二轮审核问询函的回复']
    application=[line for line in lines if compact(line['text'])=='公开发行股票并在北交所上市申请文件的']
    return [*issuer,*application,*family] if len(issuer)==len(application)==len(family)==1 else []


def _span_lines(lines,reference):
    chosen=[line for line in lines if line['start']<reference['end'] and reference['start']<line['end']]
    if any(not char.isspace() and not any(line['start']<=reference['start']+i<line['end'] for line in chosen)
           for i,char in enumerate(reference['text'])):return []
    return chosen


def _make_fact(*,cell,period,dimension,definition,proof,title,table_id,doc_sha,path,company,
               disclosure_date,disclosure_version,label):
    match=_PERCENT.fullmatch(compact(cell['text']))
    if not match:return None
    number=match['number'].replace(',','').replace('，','').replace('－','-').replace('−','-')
    return Fact(doc_sha[:12]+'-quarter-'+digest([table_id,period,dimension,cell['bbox']])[:20],
        company,'REVENUE_SHARE',period,scope='未知',nr='未扣非',version=disclosure_version,
        source_doc=doc_sha[:12],publish_date=disclosure_date,value=Decimal(number)*Decimal('.01'),
        decimals=len(number.partition('.')[2]),display_unit='%',evidence=_evidence(doc_sha,[cell,*proof]),
        label=label,source_path=str(Path(path)),source_type='financial_source_table',
        table_label=title,column_label=period+':REVENUE_SHARE:value',parse_conf='0.99',
        extractor_version='quarter-cloud-source-v1',version_group=doc_sha[:12],
        dimension_kind='business',dimension_id=dimension,definition_id=definition,
        source_table_id=table_id,source_table_evidence=_evidence(doc_sha,proof),source_relation='value')


def extract_source_facts(pages,*,path,company,doc_sha,disclosure_date,disclosure_version,**control):
    """Original quarterly grid and its adjacent year-ordered continuation.

    The annual-share sentence says ``各期``. It is admitted only when the
    same section explicitly orders three years, the immediately preceding
    table covers exactly those years/quarters, and its explanation continues
    across the page without an intervening heading or different business.
    """
    from .parser import _parse_checkpoint
    cover=_cover(pages,company)
    if not cover:return []
    facts=[];factory=dict(doc_sha=doc_sha,path=path,company=company,
        disclosure_date=disclosure_date,disclosure_version=disclosure_version)
    by_page={page['page']:page for page in pages}
    for page in pages:
        _parse_checkpoint(**control)
        lines=_lines(page);raw=page['text'];source,positions=_compact_source(raw)
        titles=list(_TITLE.finditer(source));sections=list(_SECTION.finditer(source))
        if len(titles)!=1 or len(sections)!=1:continue
        title,section=titles[0],sections[0]
        first,last=int(title['first']),int(title['last'])
        years=[int(section[k]) for k in ('a','b','c')]
        if years!=list(range(first,last+1)) or len(years)!=3 or section.end()>title.start():continue
        title_ref=_raw_ref(raw,title,positions);section_ref=_raw_ref(raw,section,positions)
        heading=_span_lines(lines,title_ref);section_lines=_span_lines(lines,section_ref)
        if not heading or not section_lines:continue
        top=max(line['bbox'][3] for line in heading)
        first_rows=[line for line in lines if top<line['bbox'][1]<top+65
            and re.fullmatch(r'20\d{2}年第[一二三四]季度',compact(line['text']))]
        if not first_rows:continue
        first_row_top=min(line['bbox'][1] for line in first_rows)
        local=[line for line in lines if top<line['bbox'][1]<first_row_top]
        heads=[]
        for label in ('季度','总体毛利率','外购模式毛利率','外购模式收入占比','共建模式毛利率','共建模式收入占比'):
            candidates=[]
            for line in local:
                leaf=[other for other in local if abs(_mid(other,0)-_mid(line,0))<12]
                leaf.sort(key=lambda other:other['bbox'][1])
                if compact(''.join(other['text'] for other in leaf))==label:candidates.append(leaf)
            unique={tuple((l['start'],l['end']) for l in group):group for group in candidates}
            if len(unique)!=1:break
            heads.append(next(iter(unique.values())))
        if len(heads)!=6:continue
        centers=[sum(_mid(l,0) for l in group)/len(group) for group in heads]
        if any(centers[i+1]-centers[i]<35 for i in range(5)):continue
        boundaries=[(centers[i]+centers[i+1])/2 for i in range(5)]
        grid_top=max(l['bbox'][3] for group in heads for l in group)
        endings=[line for line in lines if line['bbox'][1]>grid_top and compact(line['text']).startswith('由上表可知，公司超算云服务第四季度毛利率')]
        if len(endings)!=1:continue
        bottom=endings[0]['bbox'][1]
        rows=[]
        for line in lines:
            match=re.fullmatch(r'(?P<year>20\d{2})年第(?P<q>[一二三四])季度',compact(line['text']))
            if match and grid_top<line['bbox'][1]<bottom and _mid(line,0)<boundaries[0]:
                rows.append((line,match['year']+'Q'+str('一二三四'.index(match['q'])+1)))
        expected={f'{year}Q{q}' for year in years for q in range(1,5)}
        if len(rows)!=len(expected) or {period for _,period in rows}!=expected:continue
        grid_proof=[*cover,*section_lines,*heading,*[l for group in heads for l in group],*[row for row,_ in rows]]
        table_id='source-table-'+digest([doc_sha,page['page'],title_ref,'quarter-business-mode'])[:24]
        for row,period in rows:
            cells=[line for line in lines if grid_top<line['bbox'][1]<bottom
                and abs(_mid(line,1)-_mid(row,1))<3 and _PERCENT.fullmatch(compact(line['text']))]
            for lane,mode in ((3,'外购模式'),(5,'共建模式')):
                left=boundaries[lane-1];right=boundaries[lane] if lane<5 else centers[5]+50
                choices=[cell for cell in cells if left<_mid(cell,0)<right]
                if len(choices)!=1:continue
                fact=_make_fact(cell=choices[0],period=period,dimension='超算云服务/'+mode,
                    definition=MODE_DEFINITION,proof=grid_proof,title=title_ref['text'],table_id=table_id,
                    label='超算云服务/'+mode+'收入占比',**factory)
                if fact:facts.append(fact)
        next_page=by_page.get(page['page']+1)
        if next_page is None:continue
        next_lines=_lines(next_page)
        content=sorted((l for l in next_lines if 60<l['bbox'][1]<780),key=lambda l:l['start'])
        if not content:continue
        next_text=next_page['text'];next_source,next_positions=_compact_source(next_text)
        observed=list(_SOURCE_SERIES.finditer(next_source))
        if len(observed)!=1:continue
        observation=observed[0];narrative_ref=_raw_ref(next_text,observation,next_positions)
        prefix=next_text[content[0]['start']:narrative_ref['start']]
        previous=sorted((l for l in lines if 60<l['bbox'][1]<780),key=lambda l:l['start'])
        if (not compact(prefix).startswith('外部算力结转算力成本相比，具有较为平稳的特点，')
                or not compact(previous[-1]['text']).startswith('共建模式的主要成本为固定资产折旧、IDC租赁费等，与外购模式按实际消耗')
                or _REJECT.search(prefix+narrative_ref['text'])):continue
        explanation=_span_lines(next_lines,narrative_ref)
        if not explanation:continue
        continuation=[previous[-1],*explanation]
        share_table='source-table-'+digest([doc_sha,page['page'],next_page['page'],title_ref,DEFINITION])[:24]
        source_proof=[*grid_proof,*continuation]
        for index,group in enumerate(('a','b','c')):
            reference=_raw_ref(next_text,observation,next_positions,group)
            contained=[line for line in explanation if line['start']<=reference['start']<reference['end']<=line['end']]
            if len(contained)!=1:continue
            cell={**contained[0],**reference}
            # The evidence ID uses its complete original PDF block; the
            # reference still preserves the selected substring's offsets.
            fact=_make_fact(cell=cell,period=f'{years[index]}Q4',dimension='超算云服务',
                definition=DEFINITION,proof=source_proof,title=narrative_ref['text'],table_id=share_table,
                label='超算云服务第四季度收入占全年超算云服务收入',**factory)
            if fact:facts.append(fact)
    return facts


def reference_bounds(proof,context,claim):
    observed=narrative_identity(claim)
    if not observed or proof.get('quotation_kind')!=observed['quotation_kind']:return False
    dimension=observed['dimension_source'];layout=proof.get('source_geometry')
    narrative,caption,citation=(proof.get(key) for key in ('narrative','caption','citation'))
    if (not all(_reference(ref) for ref in (narrative,caption,citation)) or not isinstance(layout,dict)
            or narrative!=dimension.get('narrative') or caption!=context.get('title_source')
            or dimension.get('source_geometry')!=layout or proof.get('source_geometry_sha256')!=digest(layout)
            or not all(_valid_geometry(ref,layout.get(key)) for key,ref in
                       [('narrative',narrative),('caption',caption),('citation',citation)])
            or narrative['end']>caption['start'] or caption['end']>citation['start']):return False
    body_page,cap_page,cite_page=(layout[key]['page'] for key in ('narrative','caption','citation'))
    if (cap_page!=cite_page or cap_page not in {body_page,body_page+1}
            or not layout['caption']['bbox'][3]<layout['citation']['bbox'][1]
            or body_page==cap_page and not layout['narrative']['bbox'][3]<layout['caption']['bbox'][1]):return False
    if dimension.get('observation_kind')=='paragraph':return narrative['start']<=claim.start<claim.end<=narrative['end']
    return dimension.get('caption')==caption and caption['start']<=claim.start<claim.end<=caption['end']


validate_reference_bounds=reference_bounds


def extract_source_gaps(pages,*,path,company,doc_sha,disclosure_date):
    """Archive a literal lower-bound disclosure, never a 70% point Fact."""
    from .source_gap_catalog import validate_source_gap
    cover=_cover(pages,company)
    if not cover:return []
    output=[]
    for page in pages:
        raw=page['text'];source,positions=_compact_source(raw);lines=_lines(page)
        titles=list(_TITLE.finditer(source));sections=list(_SECTION.finditer(source))
        if len(titles)!=1 or len(sections)!=1:continue
        title,section=titles[0],sections[0]
        for match in _THRESHOLD.finditer(source):
            if not title.end()<match.start() or not int(title['first'])<=int(match['year'])<=int(title['last']):continue
            quote=_raw_ref(raw,match,positions)
            selected=_span_lines(lines,quote)
            headings=_span_lines(lines,_raw_ref(raw,title,positions))+_span_lines(lines,_raw_ref(raw,section,positions))
            if not selected or not headings:continue
            evidence=_evidence(doc_sha,[*cover,*headings,*selected])
            identifier=digest([doc_sha,page['page'],quote,'cloud-co-build-strict-lower-bound'])[:24]
            record={'id':'sg-'+identifier,'resource_id':'source-gap:'+identifier,
                'doc_sha256':doc_sha,'source_sha256':doc_sha,'source_doc':doc_sha[:12],
                'source_path':str(Path(path)),'publish_date':disclosure_date,'company':company,'page':page['page'],
                'period':match['year']+'Y','dimension_kind':'business','dimension_id':'超算云服务/共建模式',
                'currency':'CNY','account':'REVENUE_SHARE','scope':'未知',
                'gap_kind':'source-threshold-disclosure','reason':_THRESHOLD_REASON,
                'needed_method':'核对来源引用、年度收入分子和总体超算云服务收入分母，执行登记的严格界限蕴含规则；专业签发待完成',
                'table_label':'共建模式超算云服务收入占总体超算云服务收入的年度严格下界',
                'source_context':{'source_quote':quote,'quote_kind':'cloud_co_build_annual_strict_lower_bound',
                    'source_page':page['page'],'source_fragments':[{'text':l['text'],'bbox':l['bbox']} for l in selected],
                    'source_numerator':'共建模式提供的超算云服务收入','source_denominator':'总体超算云服务收入',
                    'source_operator':'strict_greater_than','eligible_as_point_fact':False},
                'evidence':list(evidence),'status':'source_threshold_disclosure','eligible_as_fact':False,
                'assertion_origin':'host_pdf_extract',
                'missing_dimensions':[], 'professional_signoff_status':'pending', 'blocking_reasons':[]}
            validate_source_gap(record);output.append(record)
    return output


def attach_threshold_references(claims,text,pages,reports,cutoff,source_gaps):
    """Bind diagnostic provenance to the exact unchanged lower-bound token."""
    from .source_table_reference import _date,_citation_kind,_citation_report_matches,_declared_report_matches
    claims=list(claims)
    if not _date(cutoff) or not pages:return claims
    registry={r.get('sha256'):r for r in reports if _date(r.get('publish_date')) and r['publish_date']<=cutoff}
    replacements={}
    for claimed in claims:
        if claimed.table_context.get('source_threshold_reference'):continue
        # Complete draft paragraph and its actual next figure/citation were
        # already validated for the quarterly occurrences; reconstruct the
        # same grammar and geometry to keep this metadata independent of them.
        peers=[other for other in claims if isinstance(other.table_context.get('source_table_reference'),dict)
            and other.table_context['source_table_reference'].get('quotation_kind') in KINDS]
        for other in peers:
            bound=other.table_context['source_table_reference'];body=bound.get('narrative')
            if not _reference(body) or not _subject(body['text'],claimed.company):continue
            compact_body,positions=_compact_source(body['text']);matches=list(_THRESHOLD.finditer(compact_body))
            if len(matches)!=1:continue
            match=matches[0];left,right=_raw_span(match,positions,'value')
            if claimed.start!=body['start']+left or claimed.end!=body['start']+right:continue
            if text[claimed.start:claimed.end]!=claimed.numeric_text:continue
            if _citation_kind(bound['citation']['text'])!='second_inquiry':continue
            available=[]
            for gap in source_gaps or []:
                report=registry.get(gap.get('doc_sha256'))
                if (not report or gap.get('gap_kind') not in {'source-threshold-rule-pending','source-threshold-disclosure'}
                        or gap.get('assertion_origin')!='host_pdf_extract'
                        or gap.get('company')!=claimed.company or gap.get('period')!=match['year']+'Y'
                        or gap.get('source_context',{}).get('quote_kind')!='cloud_co_build_annual_strict_lower_bound'
                        or gap.get('doc_sha256')!=bound.get('report_sha256')
                        or gap.get('publish_date')!=report['publish_date'] or not _citation_report_matches(bound['citation']['text'],report)
                        or claimed.query_strategy in {'pinned','same_report'} and not claimed.declared_source
                        or claimed.declared_source and not _declared_report_matches(claimed.declared_source,report)):continue
                quote=gap.get('source_context',{}).get('source_quote')
                if not _reference(quote) or not _THRESHOLD.fullmatch(compact(quote['text'])):continue
                available.append(gap)
            if len(available)!=1:continue
            gap=available[0]
            proof={'schema_version':'source-threshold-reference.v1','company':claimed.company,
                'claim_start':claimed.start,'claim_end':claimed.end,'numeric_text':claimed.numeric_text,
                'source_text_sha256':hashlib.sha256(claimed.text.encode('utf8')).hexdigest(),
                'narrative':body,'caption':bound['caption'],'citation':bound['citation'],
                'source_geometry':bound['source_geometry'],'source_geometry_sha256':bound['source_geometry_sha256'],
                'source_quote':gap['source_context']['source_quote'],'source_gap_id':gap['id'],
                'report_sha256':gap['doc_sha256'],'source_doc':gap['doc_sha256'][:12],
                'publish_date':gap['publish_date'],'cutoff':cutoff,'period':gap['period'],
                'dimension_kind':'business','dimension_id':'超算云服务/共建模式','account':'REVENUE_SHARE',
                'source_table_evidence':gap['evidence'],'source_operator':'strict_greater_than',
                'eligible_as_point_fact':False}
            proof['sha256']=digest(proof)
            context={k:v for k,v in claimed.table_context.items() if k not in {'source_dimensions','source_table_reference'}}
            context.update(source_threshold_reference=proof, account_hint='REVENUE_SHARE',
                period_hint=gap['period'], relation_hint='gt', scope_hint='未知', unit_hint='%',
                dimension_kind='business', dimension_id='超算云服务/共建模式',
                definition_id='source:annual-cloud-business-mode-revenue-share', currency='CNY',
                evaluation_scope='issuer_historical_financial', layout_source='source_threshold_quotation')
            candidate=replace(claimed,text=body['text'],source_numeric_start=left,
                account='REVENUE_SHARE',period=gap['period'],relation='gt',base_period=None,
                candidate_value=str(Decimal(compact(claimed.numeric_text).rstrip('%％'))*Decimal('.01')),
                unit='%',table_context=context,declared_source=gap['doc_sha256'][:12],
                query_strategy='pinned',caliber_hint=('来源表','未扣非'),
                parse_status='parsed',binding_status='unresolved')
            # The proof binds the complete paragraph, so freeze its hash
            # after rebuilding the source text instead of retaining a parser
            # fragment's hash. Candidate numbers never choose the source.
            proof['source_text_sha256']=hashlib.sha256(candidate.text.encode('utf8')).hexdigest()
            proof['sha256']=digest({k:v for k,v in proof.items() if k!='sha256'})
            if validated_threshold_reference(candidate):replacements[claimed.claim_id]=candidate
            break
    return [replacements.get(c.claim_id,c) for c in claims]


def validated_threshold_reference(claim):
    """Validate a source assertion without turning its bound into a Fact."""
    from .source_table_reference import _date,_citation_kind
    proof=claim.table_context.get('source_threshold_reference')
    if not isinstance(proof,dict):return None
    payload={k:v for k,v in proof.items() if k!='sha256'}
    if (proof.get('schema_version')!='source-threshold-reference.v1' or digest(payload)!=proof.get('sha256')
            or proof.get('company')!=claim.company or proof.get('claim_start')!=claim.start or proof.get('claim_end')!=claim.end
            or proof.get('numeric_text')!=claim.numeric_text
            or proof.get('source_text_sha256')!=hashlib.sha256(claim.text.encode('utf8')).hexdigest()
            or not re.fullmatch(r'[a-f0-9]{64}',str(proof.get('report_sha256','')))
            or proof.get('source_doc')!=proof['report_sha256'][:12]
            or not _date(proof.get('publish_date')) or not _date(proof.get('cutoff'))
            or proof['publish_date']>proof['cutoff'] or proof.get('eligible_as_point_fact') is not False
            or proof.get('source_operator')!='strict_greater_than'
            or proof.get('account')!='REVENUE_SHARE' or proof.get('dimension_kind')!='business'
            or proof.get('dimension_id')!='超算云服务/共建模式'
            or not re.fullmatch(r'sg-[a-f0-9]{24}',str(proof.get('source_gap_id','')))):return None
    body,caption,citation,quote=(proof.get(k) for k in ('narrative','caption','citation','source_quote'))
    geometry=proof.get('source_geometry')
    if (not all(_reference(ref) for ref in (body,caption,citation,quote)) or not _subject(body['text'],claim.company)
            or not isinstance(geometry,dict) or proof.get('source_geometry_sha256')!=digest(geometry)
            or not all(_valid_geometry(ref,geometry.get(k)) for k,ref in [('narrative',body),('caption',caption),('citation',citation)])
            or _citation_kind(citation['text'])!='second_inquiry'
            or body['end']>caption['start'] or caption['end']>citation['start']):return None
    c,p=_compact_source(body['text']);matches=list(_THRESHOLD.finditer(c));official=_THRESHOLD.fullmatch(compact(quote['text']))
    if len(matches)!=1 or not official:return None
    match=matches[0];left,right=_raw_span(match,p,'value')
    if (claim.text!=body['text'] or type(claim.source_numeric_start) is not int or claim.source_numeric_start!=left
            or body['start']+claim.source_numeric_start!=claim.start
            or body['start']+left!=claim.start or body['start']+right!=claim.end or body['text'][left:right]!=claim.numeric_text
            or official['year']!=match['year'] or proof.get('period')!=match['year']+'Y'):return None
    evidence=proof.get('source_table_evidence')
    if (not isinstance(evidence,list) or not evidence or any(not isinstance(e,str) or not re.fullmatch(
            re.escape(proof['source_doc'])+r':p[1-9]\d*:[^:]+:[a-f0-9]+',e) for e in evidence)):return None
    if (claim.account!='REVENUE_SHARE' or claim.period!=proof['period'] or claim.relation!='gt'
            or claim.base_period is not None or claim.unit!='%'
            or claim.query_strategy!='pinned' or claim.declared_source!=proof['source_doc']
            or claim.table_context.get('dimension_kind')!='business'
            or claim.table_context.get('dimension_id')!=proof['dimension_id']
            or claim.table_context.get('definition_id')!='source:annual-cloud-business-mode-revenue-share'):
        return None
    return proof


def source_threshold_issue(claim):
    """Missing execution rule is distinct from pending professional review."""
    if not validated_threshold_reference(claim):return None
    from .rules import load_rule_cards
    if not any(card['id']=='SOURCE-threshold-entailment' for card in load_rule_cards()):
        return 'X2','来源严格界限蕴含规则尚未登记，不能把下界当成精确比例'
    return None


def threshold_operands(claim):
    """Only host-reconstructed source and draft bounds enter the rule."""
    proof=validated_threshold_reference(claim)
    if not proof:return None
    from .thresholds import ThresholdAssertion
    source=_THRESHOLD.fullmatch(compact(proof['source_quote']['text']))
    draft=_THRESHOLD.search(compact(proof['narrative']['text']))
    number=lambda match:Decimal(match['value'].rstrip('%％').replace(',','').replace('，',''))*Decimal('.01')
    source_bound,claim_bound=number(source),number(draft)
    return (ThresholdAssertion.from_operator('gt',source_bound),
            ThresholdAssertion.from_operator('gt',claim_bound),
            {'operand_type':'source_threshold_assertion','assertion_id':proof['source_gap_id'],
             'company':claim.company,'account':claim.account,'period':claim.period,'scope':'来源表',
             'dimension_kind':'business','dimension_id':proof['dimension_id'],
             'definition_id':claim.table_context['definition_id'],'currency':'CNY',
             'source_doc':proof['source_doc'],'publish_date':proof['publish_date'],
             'operator':'gt','bound':str(source_bound),'display_unit':'%',
             'source_quote':proof['source_quote']['text'],'evidence':proof['source_table_evidence'],
             'eligible_as_point_fact':False})


def allowed_reference(proof,context,claim):
    from .source_table_reference import _citation_kind
    observed=narrative_identity(claim)
    return bool(observed and proof.get('quotation_kind')==observed['quotation_kind']
        and _citation_kind((proof.get('citation') or {}).get('text'))=='second_inquiry'
        and proof.get('source_report_family')=='second_inquiry'
        and proof.get('source_definition_id')==context.get('definition_id')==observed['definition_id']
        and proof.get('dimension_kind')==context.get('dimension_kind')=='business'
        and proof.get('source_dimension_id')==proof.get('dimension_id')==observed['dimension_id']
        and proof.get('account')==observed['account']==claim.account
        and proof.get('period')==observed['period']==claim.period
        and proof.get('relation')==claim.relation=='value'
        and proof.get('source_account_unit')==claim.unit=='%' and reference_bounds(proof,context,claim))


def _family(report):
    return 'second_inquiry' if re.search(r'第(?:二|2)轮.*问询.*回复',report.get('name','')) else ''


def link(claims,text,facts,reports,cutoff,pages=None):
    from .source_table_reference import (_date,_citation_kind,_citation_report_matches,_declared_report_matches,
        SCHEMA as REFERENCE_SCHEMA,TABLE_SCOPE)
    claims=list(claims)
    if not _date(cutoff) or not pages:return claims,[]
    registry={report['sha256'][:12]:report for report in reports
        if re.fullmatch(r'[a-f0-9]{64}',str(report.get('sha256','')))
        and _date(report.get('publish_date')) and report['publish_date']<=cutoff}
    replacements={};diagnostics=[]
    heads=list(re.finditer(r'(?m)^[ \t]*公司逐步加大了对专用设备的采购以丰富共建模式下自有算力资源池，',text))
    for head in heads:
        suffix=text[head.start():head.start()+900];series=list(narrative_series(suffix))
        if len(series)!=1:continue
        match,cells=series[0];_,positions=_compact_source(suffix);_,right=_raw_span(match,positions)
        body={'start':head.start(),'end':head.start()+right,'text':suffix[:right]}
        boundaries=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[body['end']:]))
        if not boundaries:continue
        boundary=boundaries[0];left=body['end']+boundary.start()+len(boundary[0])-len(boundary[0].lstrip())
        caption={'start':left,'end':left+len(boundary[0].strip()),'text':boundary[0].strip()}
        originals=[claim for claim in claims if claim.start==body['start']+cells[0]['start']]
        if len(originals)!=1:continue
        company=originals[0].company
        if not _subject(body['text'],company):continue
        caption_observed=list(caption_series(caption['text'],company,narrative=body['text']))
        if len(caption_observed)!=1:continue
        following=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[caption['end']:]))
        if not following:continue
        boundary=following[0];left=caption['end']+boundary.start()+len(boundary[0])-len(boundary[0].lstrip())
        citation={'start':left,'end':left+len(boundary[0].strip()),'text':boundary[0].strip()}
        if _citation_kind(citation['text'])!='second_inquiry':continue
        geometry={key:_geometry(ref,pages,text) for key,ref in [('narrative',body),('caption',caption),('citation',citation)]}
        if any(value is None for value in geometry.values()):continue
        observations=[('paragraph',body,cell) for cell in cells]
        observations.extend(('caption',caption,cell) for cell in caption_observed[0][1])
        for kind,source,cell in observations:
            originals=[claim for claim in claims if claim.start==source['start']+cell['start'] and claim.end==source['start']+cell['end']]
            if len(originals)!=1:continue
            original=originals[0]
            if original.company!=company or original.query_strategy in {'pinned','same_report'} and not original.declared_source:continue
            raw=source['text'][cell['start']:cell['end']];parsed=_PERCENT.fullmatch(compact(raw))
            if not parsed:continue
            dimension={'schema_version':SCHEMA,'observation_kind':kind,'narrative':body,'source_geometry':geometry,
                **{key:cell[key] for key in ('period','account','relation','dimension_id','definition_id')}}
            if kind=='caption':dimension['caption']=caption
            context={k:v for k,v in original.table_context.items() if k not in {'source_dimensions','source_table_reference'}}
            context.update(title=caption['text'],title_source=caption,period_source='source_header',
                account_hint='REVENUE_SHARE',period_hint=cell['period'],relation_hint='value',scope_hint='未知',
                unit_hint='%',dimension_kind='business',dimension_id=cell['dimension_id'],definition_id=cell['definition_id'],
                dimension_source=dimension,layout_source='source_quarter_quotation',row_label=cell['dimension_id'],
                currency='CNY',evaluation_scope='issuer_historical_financial')
            number=parsed['number'].replace(',','').replace('，','').replace('－','-').replace('−','-')
            typed=replace(original,text=source['text'],numeric_text=raw,source_numeric_start=cell['start'],
                account='REVENUE_SHARE',period=cell['period'],relation='value',base_period=None,
                candidate_value=str(Decimal(number)*Decimal('.01')),unit='%',display_decimals=len(number.partition('.')[2]),
                table_context=context,parse_status='parsed',binding_status='unresolved')
            groups={}
            for fact in facts:
                report=registry.get(fact.source_doc)
                if (not report or _family(report)!='second_inquiry' or fact.company!=company
                        or fact.definition_id!=cell['definition_id'] or fact.dimension_kind!='business'
                        or fact.source_type!='financial_source_table' or fact.publish_date!=report['publish_date']
                        or company not in (report.get('companies') or [report.get('company')])
                        or not fact.source_table_id or not fact.source_table_evidence
                        or not _citation_report_matches(citation['text'],report)
                        or original.declared_source and not _declared_report_matches(original.declared_source,report)):continue
                groups.setdefault((fact.source_doc,fact.source_table_id),[]).append(fact)
            eligible=[(registry[doc]['publish_date'],doc,table,rows) for (doc,table),rows in groups.items()
                if any(row.dimension_id==cell['dimension_id'] and row.period==cell['period'] for row in rows)]
            if not eligible:
                diagnostics.append({'kind':'missing_quarter_source_table','claim_id':original.claim_id,'period':cell['period'],'account':'REVENUE_SHARE'})
                replacements[typed.claim_id]=typed;continue
            latest=max(item[0] for item in eligible);eligible=[item for item in eligible if item[0]==latest]
            if len({item[1] for item in eligible})!=1:
                diagnostics.append({'kind':'ambiguous_quarter_source_document','claim_id':original.claim_id})
                replacements[typed.claim_id]=typed;continue
            document=eligible[0][1];report=registry[document];tables=sorted(item[2] for item in eligible)
            proof={'schema_version':REFERENCE_SCHEMA,'company':company,'claim_start':typed.start,'claim_end':typed.end,
                'source_text_sha256':hashlib.sha256(typed.text.encode('utf8')).hexdigest(),
                'caption':caption,'citation':citation,'narrative':body,
                'quotation_kind':'quarter_revenue_share_narrative' if kind=='paragraph' else 'business_mode_quarter_share_caption',
                'source_geometry':geometry,'source_geometry_sha256':digest(geometry),'period':typed.period,
                'account':'REVENUE_SHARE','relation':'value','dimension_kind':'business','dimension_id':cell['dimension_id'],
                'source_dimension_id':cell['dimension_id'],'source_definition_id':cell['definition_id'],
                'source_row_index':0,'source_doc':document,'report_sha256':report['sha256'],
                'report_id':report.get('report_id',''),'publish_date':report['publish_date'],'cutoff':cutoff,
                'source_table_id':tables[0],'source_table_ids':tables,
                'source_table_evidence':sorted({eid for item in eligible for fact in item[3] for eid in fact.source_table_evidence}),
                'source_report_family':'second_inquiry','source_account_unit':'%',
                'source_selection':'explicit_second_inquiry_citation_quarter_definition_and_latest_eligible_disclosure',
                'dimension_mapping_basis':'original_cloud_revenue_narrative_adjacent_mode_caption_and_source_quarter_headers',
                'accounting_scope':'未从引用或金额推断合并或母公司'}
            proof['sha256']=digest(proof)
            fresh=replace(typed,table_context={**context,'source_table_reference':proof},declared_source=document,
                query_strategy='pinned',caliber_hint=(TABLE_SCOPE,'未扣非'))
            if allowed_reference(proof,fresh.table_context,fresh):replacements[fresh.claim_id]=fresh
    return [replacements.get(claim.claim_id,claim) for claim in claims],diagnostics
