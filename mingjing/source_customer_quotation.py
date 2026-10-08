"""Source-defined customer type and concentration quotations.

No amount participates in choosing a document, customer, period or denominator.
Chart identity uses the original colour swatch, bar geometry and year label.
"""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re

from .evidence import make_evidence_id
from .models import AtomicClaim, Fact

SCHEMA='source-customer-quotation.v1'
DEFINITION='source:customer-type-cloud-revenue'
TYPES=('高等院校','科研机构','企业','其他机构','个人')
NUM=r'[+\-－−]?\d[\d,，]*(?:\.\d+)?'
PERCENT=re.compile(rf'(?P<num>{NUM})\s*[%％]')
CONCENTRATION=re.compile(rf'(?P<subject>公司|并行科技)整体客户集中度较低[，,]\s*'
    rf'(?P<current>20\d{{2}}H1)\s*前五大客户营收占总营收的比例为(?P<a>{NUM}[%％])[，,]\s*'
    rf'相比(?P<base>20\d{{2}})\s*年的(?P<b>{NUM}[%％])有所上升')
TYPE_NARRATIVE=re.compile(rf'服务群体主要为高等院校、科研院所、企业用户等。'
    rf'其中企业用户增长较快[，,]\s*(?P<period>20\d{{2}}H1)\s*占比达到(?P<a>{NUM}[%％])。'
    rf'公司个人客户数量较多[，,]但营收占比较低[，,]仅为(?P<b>{NUM}[%％])。')


def compact(text):return re.sub(r'\s+','',text)
def decimal_number(text):return Decimal(text.replace(',','').replace('，','').replace('－','-').replace('−','-'))
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
def ref(item):return {key:item[key] for key in ('start','end','text','bbox')}
def valid_ref(item):
    return (isinstance(item,dict) and type(item.get('start')) is int and type(item.get('end')) is int
        and 0<=item['start']<item['end'] and isinstance(item.get('text'),str)
        and item['end']-item['start']==len(item['text']))
def valid_box(box):
    return (isinstance(box,list) and len(box)==4 and all(type(v) in {int,float} and math.isfinite(v) for v in box)
        and box[0]<box[2] and box[1]<box[3])
def valid_shape(shape):
    return (isinstance(shape,dict) and valid_box(shape.get('bbox')) and isinstance(shape.get('fill'),list)
        and len(shape['fill']) in {1,3,4} and all(type(v) in {int,float} and math.isfinite(v) and 0<=v<=1 for v in shape['fill']))
def center(box):return ((box[0]+box[2])/2,(box[1]+box[3])/2)


def _observations(text,kind):
    expression=CONCENTRATION if kind=='customer_concentration_narrative' else TYPE_NARRATIVE
    for match in expression.finditer(text):
        # An exact source paragraph start cannot be manufactured by dropping
        # a named issuer or a negation before a suffix match on “公司”.
        line_prefix=text[text.rfind('\n',0,match.start())+1:match.start()]
        if line_prefix.strip():continue
        previous=text[max(text.rfind('。',0,match.start())+1,0):match.start()]
        previous=re.split(r'(?:资料|数据)来源[^\n]*\n',previous)[-1]
        if re.search(r'模拟|假设|预计|预测|并非|不是|不引用|未引用',previous):continue
        if kind=='customer_concentration_narrative':
            if match['base']>=match['current'][:4]:continue
            yield match,[{'start':match.start('a')-match.start(),'end':match.end('a')-match.start(),'period':match['current'],'dimension_id':'前五大客户'},
                {'start':match.start('b')-match.start(),'end':match.end('b')-match.start(),'period':match['base']+'Y','dimension_id':'前五大客户'}]
        else:
            yield match,[{'start':match.start('a')-match.start(),'end':match.end('a')-match.start(),'period':match['period'],'dimension_id':'企业'},
                {'start':match.start('b')-match.start(),'end':match.end('b')-match.start(),'period':match['period'],'dimension_id':'个人'}]


def identity(claim):
    context=claim.table_context
    proof=context.get('dimension_source')
    if not isinstance(proof,dict) or proof.get('schema_version')!=SCHEMA:
        return None
    kind=proof.get('quotation_kind')
    if kind in {'customer_concentration_narrative','customer_type_narrative'}:
        narrative=proof.get('narrative')
        if (not valid_ref(narrative) or narrative['text']!=claim.text
                or type(claim.source_numeric_start) is not int
                or narrative['start']+claim.source_numeric_start!=claim.start
                or claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]!=claim.numeric_text):
            return None
        observed=[cell for _,cells in _observations(claim.text,kind) for cell in cells
            if narrative['start']+cell['start']==claim.start and narrative['start']+cell['end']==claim.end]
        if len(observed)!=1:
            return None
        cell=observed[0]
    elif kind=='customer_type_chart':
        raw,legend,year=proof.get('cell'),proof.get('series_label'),proof.get('period_label')
        marker,bar=proof.get('legend_marker'),proof.get('bar_segment')
        shapes=proof.get('chart_shapes')
        caption,citation=proof.get('caption'),proof.get('citation')
        if (not all(valid_ref(item) and valid_box(item.get('bbox')) for item in [raw,legend,year,caption,citation])
                or compact(legend['text']) not in TYPES
                or not all(valid_shape(item) for item in [marker,bar])
                or not isinstance(shapes,list) or not all(valid_shape(shape) for shape in shapes)
                or digest(shapes)!=proof.get('chart_shapes_sha256')
                or marker not in shapes or bar not in shapes or marker['fill']!=bar['fill']
                or claim.start!=raw['start'] or claim.end!=raw['end'] or raw['text']!=claim.numeric_text
                or claim.text!=legend['text']+'\n'+raw['text']
                or claim.source_numeric_start!=len(legend['text'])+1
                or PERCENT.fullmatch(raw['text']) is None):
            return None
        x,y=center(raw['bbox']);lx,ly=center(legend['bbox']);mx,my=center(marker['bbox']);yx,_=center(year['bbox'])
        if (not bar['bbox'][0]<=x<=bar['bbox'][2] or not bar['bbox'][1]-2<=y<=bar['bbox'][3]+2
                or not bar['bbox'][0]<=yx<=bar['bbox'][2] or not 0<=legend['bbox'][0]-marker['bbox'][2]<=8
                or abs(my-ly)>3 or marker['bbox'][2]-marker['bbox'][0]>8 or marker['bbox'][3]-marker['bbox'][1]>8
                or not caption['bbox'][3]<bar['bbox'][1]<bar['bbox'][3]<year['bbox'][1]
                or not year['bbox'][3]<legend['bbox'][1]<legend['bbox'][3]<citation['bbox'][1]):
            return None
        period=compact(year['text'])
        if not re.fullmatch(r'20\d{2}(?:H1)?',period):return None
        cell={'period':period if period.endswith('H1') else period+'Y','dimension_id':compact(legend['text'])}
    else:return None
    definition='metric:revenue-share' if kind=='customer_concentration_narrative' else DEFINITION
    expected={'account':'REVENUE_SHARE','period':cell['period'],'relation':'value',
        'dimension_kind':'customer','dimension_id':cell['dimension_id'],'definition_id':definition}
    if any(context.get(key+'_hint' if key in {'account','period','relation'} else key)!=value for key,value in expected.items()):
        return None
    return {**expected,'quotation_kind':kind,'dimension_source':proof,
            'company_provenance':'host_locked_issuer_context','company_source_explicit':False}


def _chart_caption(text,company):
    from .issuer_identity import same_issuer
    title=compact(text)
    match=re.fullmatch(r'图\d+[:：](?P<issuer>公司|.+?)主要客户为高等院校与科研机构[，,]企业客户增长较快',title)
    return bool(match and (match['issuer']=='公司' or same_issuer(match['issuer'],company)))


def chart_claims(text,page,lines,company):
    """Original label colour -> source bar segment -> source categorical year."""
    shapes=[shape for shape in page.get('vector_rectangles',[]) if valid_shape(shape)]
    output=[]
    for caption in [line for line in lines if _chart_caption(line['text'],company)]:
        previous_sources=list(re.finditer(r'(?m)^\s*(?:资料|数据)来源\s*[:：].*$',text[:caption['start']]))
        paragraph=text[previous_sources[-1].end() if previous_sources else 0:caption['start']]
        if '服务群体主要为' in paragraph and not list(_observations(paragraph,'customer_type_narrative')):
            continue
        citations=[line for line in lines if compact(line['text']).startswith(('数据来源：','资料来源：'))
            and line['bbox'][1]>caption['bbox'][3]]
        if not citations:continue
        citation=min(citations,key=lambda line:line['bbox'][1])
        from .source_table_reference import _citation_kind
        if _citation_kind(citation['text'].strip())!='prospectus':continue
        local=[line for line in lines if caption['bbox'][3]<line['bbox'][1]<citation['bbox'][1]]
        legends=[line for line in local if compact(line['text']) in TYPES]
        if {compact(line['text']) for line in legends}!=set(TYPES) or len(legends)!=len(TYPES):continue
        years=sorted((line for line in local if re.fullmatch(r'20\d{2}(?:H1)?',compact(line['text']))),key=lambda line:line['bbox'][0])
        if len(years)!=4 or len({compact(line['text']) for line in years})!=4:continue
        local_shapes=[shape for shape in shapes if caption['bbox'][3]<shape['bbox'][1]<shape['bbox'][3]<citation['bbox'][1]]
        for legend in legends:
            markers=[shape for shape in local_shapes if shape['bbox'][2]-shape['bbox'][0]<=8
                and shape['bbox'][3]-shape['bbox'][1]<=8 and 0<=legend['bbox'][0]-shape['bbox'][2]<=8
                and abs(center(shape['bbox'])[1]-center(legend['bbox'])[1])<=3]
            if len(markers)!=1:continue
            marker=markers[0]
            for year in years:
                bars=[shape for shape in local_shapes if shape['fill']==marker['fill']
                    and shape['bbox'][2]-shape['bbox'][0]>15 and shape['bbox'][3]<year['bbox'][1]
                    and shape['bbox'][0]<=center(year['bbox'])[0]<=shape['bbox'][2]]
                if len(bars)!=1:continue
                bar=bars[0]
                cells=[line for line in local if PERCENT.fullmatch(line['text'].strip())
                    and bar['bbox'][0]<=center(line['bbox'])[0]<=bar['bbox'][2]
                    and bar['bbox'][1]-2<=center(line['bbox'])[1]<=bar['bbox'][3]+2]
                if len(cells)!=1:continue
                cell=cells[0];raw=cell['text'].strip();pad=len(cell['text'])-len(cell['text'].lstrip())
                original={**ref(cell),'start':cell['start']+pad,'end':cell['start']+pad+len(raw),'text':raw}
                period=compact(year['text']);period=period if period.endswith('H1') else period+'Y'
                body=legend['text']+'\n'+raw;dimension=compact(legend['text'])
                source={'schema_version':SCHEMA,'quotation_kind':'customer_type_chart','caption':ref(caption),
                    'citation':ref(citation),'cell':original,'series_label':ref(legend),'period_label':ref(year),
                    'legend_marker':marker,'bar_segment':bar,'chart_shapes':local_shapes,
                    'chart_shapes_sha256':digest(local_shapes)}
                context={'title':caption['text'],'title_source':ref(caption),'period_source':'source_header',
                    'account_hint':'REVENUE_SHARE','period_hint':period,'relation_hint':'value','scope_hint':'未知',
                    'unit_hint':'%','dimension_kind':'customer','dimension_id':dimension,'definition_id':DEFINITION,
                    'dimension_source':source,'layout_source':'source_customer_quotation','row_label':dimension,
                    'currency':'CNY','evaluation_scope':'issuer_historical_financial'}
                output.append(AtomicClaim('chart-'+str(original['start'])+'-'+str(original['end']),body,
                    original['start'],original['end'],company=company,account='REVENUE_SHARE',period=period,
                    unit='%',candidate_value=str(decimal_number(PERCENT.fullmatch(raw)['num'])*Decimal('.01')),
                    numeric_text=raw,source_numeric_start=len(legend['text'])+1,table_context=context,
                    caliber_hint=('未知','未扣非'),display_decimals=len(PERCENT.fullmatch(raw)['num'].partition('.')[2])))
    return output


def extract_source_facts(pages,*,path,company,doc_sha,disclosure_date,disclosure_version,**control):
    from .issuer_identity import same_issuer
    from .parser import _parse_checkpoint
    # The prospectus lacks an issuer header on each table page. Admit only a
    # complete cover identity, independently present in the same frozen PDF.
    covers=[page for page in pages if page['page']==1]
    if len(covers)!=1:return []
    legal=[line for line in covers[0].get('lines',[]) if compact(line['text']).endswith('股份有限公司')
        and same_issuer(compact(line['text']),company)]
    if len(legal)!=1 or not re.search(r'招股说明书[（(](?:申报稿|注册稿)[）)]',compact(covers[0]['text'])):return []
    facts=[]
    for page in pages:
        _parse_checkpoint(**control)
        raw=page['text'];lines=[line for line in page.get('lines',[]) if valid_ref(line) and valid_box(line.get('bbox'))
            and raw[line['start']:line['end']]==line['text']]
        titles=[line for line in lines if compact(line['text'])=='报告期内，公司超算云服务收入按客户类型划分情况如下：']
        for title in titles:
            local=[line for line in lines if title['bbox'][3]<line['bbox'][1]<title['bbox'][3]+185]
            years=sorted((line for line in local if re.fullmatch(r'20\d{2}年(?:1-6月|度)',compact(line['text']))),key=lambda line:line['bbox'][0])
            labels=sorted((line for line in local if compact(line['text']) in {*TYPES,'合计'}),key=lambda line:line['bbox'][1])
            amounts=sorted((line for line in local if compact(line['text'])=='金额'),key=lambda line:line['bbox'][0])
            shares=sorted((line for line in local if compact(line['text']) in {'占比','占比（%）'}),key=lambda line:line['bbox'][0])
            if len(years)!=4 or len(labels)!=6 or len(amounts)!=4 or len(shares)!=4:continue
            # Each amount's original unit and each proportion's percent label
            # must be independently present in the same physical column.
            units=[line for line in local if compact(line['text'])=='（万元）']
            percentages=[line for line in local if compact(line['text']) in {'（%）','占比（%）'}]
            if any(not any(abs(center(u['bbox'])[0]-center(a['bbox'])[0])<=5 for u in units) for a in amounts):continue
            if any(not any(abs(center(u['bbox'])[0]-center(a['bbox'])[0])<=5 for u in percentages) for a in shares):continue
            columns=sorted([*amounts,*shares],key=lambda line:line['bbox'][0])
            if [compact(c['text']).startswith('金额') for c in columns]!=[True,False]*4:continue
            table_id='source-table-'+digest([doc_sha,page['page'],title['bbox'],'customer-type-cloud-revenue'])[:24]
            from .source_gap_catalog import _proofs
            table_evidence=tuple([*_proofs(covers[0],[legal[0]],doc_sha),
                *_proofs(page,[title,*years,*columns,*units,*percentages,labels[-1]],doc_sha)])
            if not table_evidence:continue
            for label in labels[:-1]:
                row=[line for line in local if abs(center(line['bbox'])[1]-center(label['bbox'])[1])<=2
                    and line['bbox'][0]>label['bbox'][2]+5 and re.fullmatch(NUM,line['text'].strip())]
                for index,column in enumerate(columns):
                    cells=[line for line in row if abs(center(line['bbox'])[0]-center(column['bbox'])[0])<=25]
                    if len(cells)!=1:continue
                    cell=cells[0];num=cell['text'].strip().replace(',','').replace('，','').replace('－','-').replace('−','-')
                    year=compact(years[index//2]['text']);period=year[:4]+('H1' if '1-6月' in year else 'Y')
                    account='REVENUE_SHARE' if index%2 else 'REVENUE';unit='%' if index%2 else '万元'
                    cell_proof=_proofs(page,[cell,label],doc_sha)
                    if not cell_proof:continue
                    evidence=tuple([*cell_proof,*table_evidence])
                    facts.append(Fact(doc_sha[:12]+'-customer-type-'+digest([period,account,cell['bbox']])[:20],
                        company,account,period,scope='未知',nr='未扣非',version=disclosure_version,
                        source_doc=doc_sha[:12],publish_date=disclosure_date,
                        value=Decimal(num)*(Decimal('.01') if index%2 else Decimal('10000')),
                        decimals=len(num.partition('.')[2]),display_unit=unit,evidence=evidence,
                        label=compact(label['text']),source_path=str(Path(path)),source_type='financial_source_table',
                        table_label=title['text'].strip(),column_label=years[index//2]['text'].strip()+':'+column['text'].strip(),
                        parse_conf='0.99',extractor_version='customer-type-source-v1',version_group=doc_sha[:12],
                        dimension_kind='customer',dimension_id=compact(label['text']),definition_id=DEFINITION,
                        source_table_id=table_id,source_table_evidence=table_evidence))
    return facts


def reference_bounds(proof,context,claim):
    observed=identity(claim)
    if not observed or proof.get('quotation_kind')!=observed['quotation_kind']:return False
    if observed['quotation_kind']=='customer_type_chart':
        source=observed['dimension_source']
        return source['caption']==proof.get('caption') and source['citation']==proof.get('citation')
    narrative=proof.get('narrative')
    return bool(valid_ref(narrative) and narrative==observed['dimension_source'].get('narrative')
        and narrative['start']<=claim.start<claim.end<=narrative['end']<=proof['caption']['start']
        and proof['caption']['end']<=proof['citation']['start'])


def _typed_narrative(claim,body,cell,caption,kind):
    raw=body['text'][cell['start']:cell['end']]
    definition='metric:revenue-share' if kind=='customer_concentration_narrative' else DEFINITION
    dimension={'schema_version':SCHEMA,'quotation_kind':kind,'narrative':body}
    context={k:v for k,v in claim.table_context.items() if k!='source_dimensions'}
    context.update(title=caption['text'],title_source=caption,period_source='source_header',
        account_hint='REVENUE_SHARE',period_hint=cell['period'],relation_hint='value',scope_hint='未知',
        unit_hint='%',dimension_kind='customer',dimension_id=cell['dimension_id'],definition_id=definition,
        dimension_source=dimension,layout_source='source_customer_quotation',row_label=cell['dimension_id'])
    number=PERCENT.fullmatch(raw)['num']
    return replace(claim,text=body['text'],numeric_text=raw,source_numeric_start=cell['start'],
        account='REVENUE_SHARE',period=cell['period'],relation='value',base_period=None,
        candidate_value=str(decimal_number(number)*Decimal('.01')),unit='%',display_decimals=len(number.partition('.')[2]),
        table_context=context,parse_status='parsed',binding_status='unresolved')


def link(claims,text,facts,reports,cutoff):
    from .source_table_reference import (_citation_kind,_citation_report_matches,_date,_declared_report_matches,
        _digest,_ref_valid,_table_issuer_matches,SCHEMA as REFERENCE_SCHEMA,TABLE_SCOPE,validated_table_reference)
    if not _date(cutoff):return list(claims),[]
    claims=list(claims);replacements={};diagnostics=[]
    # Concentration shares take the existing complete-table provenance; an
    # unrelated prospectus page or convenient aggregate alone is insufficient.
    anchors={}
    for claim in claims:
        proof=validated_table_reference(claim)
        if proof and proof.get('dimension_kind')=='customer' and proof.get('quotation_kind') is None:
            anchors[proof['caption']['start']]=proof
    for kind in ('customer_concentration_narrative','customer_type_narrative'):
        for match,cells in _observations(text,kind):
            boundaries=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[match.end():]))
            if not boundaries:continue
            boundary=boundaries[0];left=match.end()+boundary.start()+len(boundary[0])-len(boundary[0].lstrip())
            caption={'start':left,'end':left+len(boundary[0].strip()),'text':boundary[0].strip()}
            if kind=='customer_concentration_narrative':
                anchor=anchors.get(left)
                if not anchor or not re.search('前五大客户',caption['text']):continue
                caption=anchor['caption'];citation=anchor['citation']
            else:
                if not _chart_caption(caption['text'],next((c.company for c in claims if c.start==match.start('a')),'')):continue
                following=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[caption['end']:]))
                if not following:continue
                raw=following[0][0].strip();cstart=caption['end']+following[0].start()+len(following[0][0])-len(following[0][0].lstrip())
                citation={'start':cstart,'end':cstart+len(raw),'text':raw}
                if _citation_kind(raw)!='prospectus':continue
            body={'start':match.start(),'end':match.end(),'text':match[0]}
            for cell in cells:
                originals=[c for c in claims if c.start==body['start']+cell['start'] and c.end==body['start']+cell['end']]
                if len(originals)!=1:continue
                original=originals[0]
                if not _table_issuer_matches({'title_source':caption},original.company):continue
                typed=_typed_narrative(original,body,cell,caption,kind)
                if kind=='customer_concentration_narrative':
                    proof={**anchor,'claim_start':typed.start,'claim_end':typed.end,'period':typed.period,
                        'dimension_id':'前五大客户','source_dimension_id':'前五大客户','source_row_index':0,
                        'source_text_sha256':hashlib.sha256(typed.text.encode('utf-8')).hexdigest(),
                        'quotation_kind':kind,'narrative':body,'account':'REVENUE_SHARE','relation':'value',
                        'dimension_mapping_basis':'explicit_original_top_five_and_total_revenue_denominator',
                        'source_selection':'adjacent_complete_quoted_customer_table_exact_period_and_aggregate'}
                    proof.pop('caption_summary',None)
                    proof['sha256']=_digest({k:v for k,v in proof.items() if k!='sha256'})
                    typed=replace(typed,table_context={**typed.table_context,'source_table_reference':proof},
                        declared_source=proof['source_doc'],query_strategy='pinned',caliber_hint=(TABLE_SCOPE,'未扣非'))
                    if validated_table_reference(typed):replacements[typed.claim_id]=typed
                else:replacements[typed.claim_id]=typed
    claims=[replacements.get(c.claim_id,c) for c in claims]
    registry={r['sha256'][:12]:r for r in reports if re.fullmatch('[a-f0-9]{64}',str(r.get('sha256','')))
        and _date(r.get('publish_date')) and r['publish_date']<=cutoff and re.search('招股(?:说明)?书',r.get('name',''))}
    for claim in claims:
        observed=identity(claim)
        if not observed or observed['definition_id']!=DEFINITION:continue
        dimension=observed['dimension_source'];caption=claim.table_context['title_source']
        if observed['quotation_kind']=='customer_type_chart':citation=dimension['citation']
        else:
            following=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[caption['end']:]))
            if not following:continue
            raw=following[0][0].strip();left=caption['end']+following[0].start()+len(following[0][0])-len(following[0][0].lstrip())
            citation={'start':left,'end':left+len(raw),'text':raw}
        if _citation_kind(citation['text'].strip())!='prospectus':continue
        groups={}
        for fact in facts:
            report=registry.get(fact.source_doc)
            if (not report or fact.company!=claim.company or fact.definition_id!=DEFINITION
                    or fact.source_type!='financial_source_table' or fact.publish_date!=report['publish_date']
                    or claim.company not in (report.get('companies') or [report.get('company')])
                    or not fact.source_table_id or not fact.source_table_evidence
                    or not _citation_report_matches(citation['text'],report)
                    or claim.declared_source and not _declared_report_matches(claim.declared_source,report)):
                continue
            groups.setdefault((fact.source_doc,fact.source_table_id),[]).append(fact)
        eligible=[(registry[doc]['publish_date'],doc,table,rows) for (doc,table),rows in groups.items()
            if {row.dimension_id for row in rows}==set(TYPES) and claim.period in {row.period for row in rows}]
        if not eligible:continue
        latest=max(r[0] for r in eligible);eligible=[r for r in eligible if r[0]==latest]
        if len({r[1] for r in eligible})!=1:
            diagnostics.append({'kind':'ambiguous_customer_type_source','claim_id':claim.claim_id});continue
        doc=eligible[0][1];report=registry[doc];tables=sorted(r[2] for r in eligible)
        proof={'schema_version':REFERENCE_SCHEMA,'company':claim.company,'claim_start':claim.start,'claim_end':claim.end,
            'source_text_sha256':hashlib.sha256(claim.text.encode('utf-8')).hexdigest(),'caption':caption,'citation':citation,
            'quotation_kind':observed['quotation_kind'],'period':claim.period,'account':'REVENUE_SHARE','relation':'value',
            'dimension_kind':'customer','dimension_id':observed['dimension_id'],'source_dimension_id':observed['dimension_id'],
            'source_definition_id':DEFINITION,'source_row_index':0,'source_doc':doc,'report_sha256':report['sha256'],
            'report_id':report['report_id'],'publish_date':report['publish_date'],'cutoff':cutoff,
            'source_table_id':tables[0],'source_table_ids':tables,
            'source_table_evidence':sorted({e for item in eligible for row in item[3] for e in row.source_table_evidence}),
            'source_selection':'explicit_prospectus_citation_customer_type_and_original_period',
            'dimension_mapping_basis':'source_legend_colour_bar_and_period_geometry' if observed['quotation_kind']=='customer_type_chart' else 'complete_original_customer_type_paragraph',
            'source_share_denominator':'所引表内超算云服务收入',
            'draft_share_denominator_status':'not_explicit_source_defined_quotation_only',
            'accounting_scope':'未从引用推断合并或母公司'}
        if dimension.get('narrative'):proof['narrative']=dimension['narrative']
        proof['sha256']=_digest(proof)
        context={k:v for k,v in claim.table_context.items() if k!='source_dimensions'}
        fresh=replace(claim,table_context={**context,'source_table_reference':proof},declared_source=doc,
            query_strategy='pinned',caliber_hint=(TABLE_SCOPE,'未扣非'))
        if validated_table_reference(fresh):replacements[claim.claim_id]=fresh
    return [replacements.get(c.claim_id,c) for c in claims],diagnostics
