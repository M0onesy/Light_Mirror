from decimal import Decimal
from dataclasses import replace
import re

from mingjing.models import Fact


def generate_suite() -> list[dict]:
    """Known synthetic source data and textual errors, not a human benchmark."""
    cases=[]
    for index in range(15):
        company=f'生成测试公司{index:02d}'
        base=Decimal(100000000+index*1000000)
        current=base*Decimal('1.2')
        document=f'generated-report-{index:02d}'
        def fact(identifier,account,period,value,**kwargs):
            return Fact(identifier,company,account,period,value=value,publish_date=kwargs.pop('publish_date','2024-04-01'),
                        source_doc=kwargs.pop('source_doc',document),evidence=(f'synthetic:{identifier}:r1c1',),**kwargs)
        source=[fact(f'{index}-rev-current','REVENUE','2023Y',current),fact(f'{index}-rev-base','REVENUE','2022Y',base),
                fact(f'{index}-mother','REVENUE','2023Y',current*Decimal('0.3'),scope='母公司'),
                fact(f'{index}-np','NP_PARENT','2023Y',Decimal('13000000'))]
        def add(set_,type_,text,status='violated',facts=None,variant='original',**kwargs):
            cases.append({'id':f'{set_}-{type_}-{index:02d}-{variant}-{len(cases):03d}', 'set':set_, 'draft':text,
                          'company':company,'report_date':'2024-04-22','period':'2023Y',
                          'gold_status':status,'gold_error_type':type_ if type_.startswith('E') else None,
                          'gold_origin':'deterministic_generated_fixture','human_reviews':[],
                          'evidence_kind':'synthetic','variant':variant,
                          'facts':[f.to_dict() for f in (source if facts is None else facts)], **kwargs})
        for variant in ('raw','rounded'):
            def amount(value):
                return f'{value:.2f}元' if variant=='raw' else f'{value/Decimal(100000000):.3f}亿元'
            add('B','E1',f'{company}2023年营业收入{amount(current+base*Decimal("0.07"))}。',variant=variant)
            add('B','E2',f'{company}2024年营业收入{amount(current)}【来源:evidence:{source[0].evidence[0]}】。',variant=variant)
            add('B','E3',f'{company}2023年母公司口径营业收入{amount(current)}【来源:evidence:{source[0].evidence[0]}】。',variant=variant)
            add('B','E4',f'{company}2023年营业收入{current:.2f}万元。' if variant=='raw' else f'{company}2023年营业收入{current/Decimal(100000000):.3f}万元。',variant=variant)
            add('B','E5',f'{company}2023年营业收入同比增长25.00%。',variant=variant)
            add('B','E6',f'{company}2023年营业收入{amount(current)}【来源:不存在的来源】。',variant=variant)
            add('B','E7',f'{company}2023年营业收入低于2022年。',variant=variant)
            add('B','E8',f'{company}2023年经营现金流500.00万元。',status='abstained',gold_abstention_code='X1',variant=variant)
            old=replace(source[0],fact_id=f'{index}-old',value=current-base*Decimal('0.05'),source_doc='older-report',publish_date='2024-03-01')
            latest=replace(source[0],version='更正后')
            add('B','E9',f'{company}2023年最新披露的营业收入{amount(old.value)}【来源:older-report】。',facts=[latest,old,*source[1:]],variant=variant)
        # Four independently reproducible examples per company yield C=60.
        for family,text in [('legal_rounding',f'{company}2023年营业收入{current/Decimal(100000000):.3f}亿元。'),
                            ('legal_unit_conversion',f'{company}2023年营业收入{current/Decimal(10000):.2f}万元。'),
                            ('approximate',f'{company}2023年营业收入约{current/Decimal(100000000):.2f}亿元。')]:
            add('C',family,text,status='verified',is_trap=True,trap_cause=family)
        old=replace(source[0],fact_id=f'{index}-declared-old',value=current-base*Decimal('0.05'),source_doc='older-report',publish_date='2024-03-01')
        latest=replace(source[0],version='更正后')
        add('C','declared_old_source',f'{company}2023年营业收入{old.value:.2f}元【来源:older-report】。',status='verified',facts=[latest,old,*source[1:]],is_trap=True,trap_cause='declared_old_source')
    # X=24: mechanism inputs are declared fixture metadata, not model outputs.
    for index in range(4):
        company=f'弃权生成公司{index}'
        f=Fact(f'x-current-{index}',company,'REVENUE','2023Y',value=Decimal('120'),publish_date='2024-04-01',source_doc='x-report',evidence=(f'synthetic:x{index}',))
        for code in ('X1','X2','X3','X4','X5','X6'):
            facts=[f]; text=f'{company}2023年营业收入120元。'; extra={}
            if code=='X1': facts=[]
            if code=='X2':
                text=f'{company}2023年营业收入同比增长20%。'
                facts.append(replace(f,fact_id='base',period='2022Y',value=Decimal('0')))
            if code=='X3': extra['binding_fixture_status']='disagreed'
            if code=='X4': facts=[replace(f,parse_conf='0.2')]
            if code=='X5':
                facts.append(replace(f,fact_id='old',value=Decimal('100'),version='调整后',publish_date='2024-03-01'))
                extra['report_date']=None
            if code=='X6': text=f'{company}2023年营业收入壹佰元。'
            cases.append({'id':f'X-{code}-{index}','set':'X','draft':text,'company':company,
                          'period':'2023Y','report_date':'2024-04-22','gold_status':'abstained',
                          'gold_error_type':'E8','gold_abstention_code':code,'gold_origin':'deterministic_generated_fixture',
                          'human_reviews':[],'evidence_kind':'synthetic','variant':'original','facts':[x.to_dict() for x in facts],**extra})
    return cases


def declared_source(text):
    match=re.search(r'【来源:([^】]+)】',text)
    return match[1] if match else None
