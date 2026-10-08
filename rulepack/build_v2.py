"""Rebuild frozen mathematical rules from reviewed local source documents.

No human review, fresh regulatory validation or benchmark result is invented.
"""
from pathlib import Path
from decimal import Decimal
import json
import hashlib
import re

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent.parent
VERSION = "0.5.0"


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def write(name, value):
    (ROOT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def _build():
    old = json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))
    snapshot = ROOT/'sources'
    snapshot.mkdir(exist_ok=True)
    method = ('# 明镜数学与取数方法规范 v3\n\n'
              '金额以Decimal元保存，取数只使用同公司、同科目、同期间、同口径并受研报日截断的证据。\n'
              '金额与增长率按原文显示精度生成区间，不能以模型输出作为事实、公式或裁决。\n'
              '同报告比较数用于同比基数，指定来源只从被引用报告取值。\n'
              '受限公式只允许已登记输入、常数、四则运算、绝对值、比较及比较的逻辑与或，分母区间非正或触零时弃权。\n'
              '按项目计划书第5.5节，两侧披露区间相交即相容核实；四舍五入区间为[v-q/2,v+q/2)，q按原披露单位和小数位计算。\n'
              '授权公式的期望区间由各操作数区间传播；相容核实不表示较细的精确位数已独立核实，来源较粗时修正应返回披露区间。\n'
              '正数约或左右在披露区间两侧额外相对放宽h，默认h=2%；正数逾、超过、多于使用(v,v*(1+h)]，正数近、将近、接近、不足、不到使用[v*(1-h),v)。\n'
              '负数或零的模糊词没有签发区间定义时弃权X2；仅金额巧合不证明期间、口径或披露版本错误。\n'
              '现金含量阈值0.5、扣非占比阈值0.30为团队设定参数，不能称为监管阈值。\n')
    (snapshot/'MJ-METHOD.md').write_bytes(method.encode('utf-8'))
    sources = [{"id":"MJ-METHOD", "kind":"methodology", "name":"明镜数学与取数方法规范v3",
                "snapshot":"sources/MJ-METHOD.md", "sha256":hashlib.sha256(method.encode('utf-8')).hexdigest(),
                "source_url":None, "effective_at":None, "validation":"engineering_validated_human_review_pending"}]
    source_texts = {'MJ-METHOD':method}
    for item in old['sources']:
        # Source snapshots are provenance archives, not a second live KB root.
        if not item.get('path'):
            continue
        path = PROJECT/item['path']
        if not path.exists():
            continue
        raw = path.read_bytes()
        filename = 'sources/'+item['id']+'.md'
        (ROOT/filename).write_bytes(raw)
        item = dict(item, kind='regulatory', snapshot=filename, sha256=hashlib.sha256(raw).hexdigest())
        if item.get('effective_at') == '待正文确认':
            item['effective_at'] = None
        item['validation'] = 'existing_local_register_not_revalidated_online'
        sources.append(item)
        source_texts[item['id']] = raw.decode('utf-8-sig').replace('\r\n','\n')
    clauses = []
    def clause(source_id, identifier, quote):
        entry = {'id':identifier,'source_id':source_id,'quote':quote,'quote_sha256':hashlib.sha256(quote.encode('utf-8')).hexdigest(),'normalization':'UTF8_LF'}
        clauses.append(entry)
        return {key:entry[key] for key in ('source_id','quote','quote_sha256')} | {'clause_id':identifier}
    common = clause('MJ-METHOD','MJ-METHOD-1','受限公式只允许已登记输入、常数、四则运算、绝对值、比较及比较的逻辑与或，分母区间非正或触零时弃权。')
    intervals = clause('MJ-METHOD','MJ-METHOD-intervals','按项目计划书第5.5节，两侧披露区间相交即相容核实；四舍五入区间为[v-q/2,v+q/2)，q按原披露单位和小数位计算。')
    parent_profit = common
    if 'CAS33' in source_texts:
        match = re.search(r'### 第三十六条\s*\n\s*\n([^\n]+)', source_texts['CAS33'])
        if match:
            parent_profit = clause('CAS33','CAS33-三十六',match[1])
    policy = common
    estimate = common
    correction = common
    if 'CAS28' in source_texts:
        for number, name in [('六','policy'),('九','estimate'),('十二','correction')]:
            match = re.search(r'### 第'+number+r'条\s*\n\s*\n([^\n]+)', source_texts['CAS28'])
            if match:
                reference = clause('CAS28','CAS28-'+number,match[1])
                if name == 'policy': policy = reference
                if name == 'estimate': estimate = reference
                if name == 'correction': correction = reference
    nri = common
    if 'NON_RECURRING_2023' in source_texts:
        match = re.search(r'八、公司计算同非经常性损益相关的财务指标时，如涉及少\s*\n数股东损益和所得税影响的，应当予以扣除。',source_texts['NON_RECURRING_2023'])
        if match:
            nri = clause('NON_RECURRING_2023','NRI-8',match[0])
    # id,name,formula,inputs,dimension,positive values,expected,boundary values
    definitions = [
        ('E1-value','两侧披露区间相交',
         '(observed_lower < expected_upper or (observed_lower == expected_upper and observed_lower_closed == ONE and expected_upper_closed == ONE)) and (expected_lower < observed_upper or (expected_lower == observed_upper and expected_lower_closed == ONE and observed_upper_closed == ONE))',
         {'observed_lower':'currency','observed_upper':'currency','expected_lower':'currency','expected_upper':'currency',
          'observed_lower_closed':'boolean','observed_upper_closed':'boolean','expected_lower_closed':'boolean','expected_upper_closed':'boolean'},
         'boolean',{'observed_lower':'119.995','observed_upper':'120.005','expected_lower':'119.999','expected_upper':'120.009',
                    'observed_lower_closed':'1','observed_upper_closed':'0','expected_lower_closed':'1','expected_upper_closed':'0'},True,
         {'observed_lower':'119.995','observed_upper':'120.005','expected_lower':'120.005','expected_upper':'120.015',
          'observed_lower_closed':'1','observed_upper_closed':'0','expected_lower_closed':'1','expected_upper_closed':'0'}),
        ('E2-period','期间一致','declared==actual',{'declared':'identity','actual':'identity'},'boolean',{'declared':'2023','actual':'2023'},True,{'declared':'2023','actual':'2022'}),
        ('E3-caliber','口径一致','declared==actual',{'declared':'identity','actual':'identity'},'boolean',{'declared':'1','actual':'1'},True,{'declared':'1','actual':'0'}),
        ('E4-unit','万元换算','display*TEN_THOUSAND',{'display':'currency'},'currency',{'display':'1'},'10000',{}),
        ('E5-yoy','同比重算','(current-base)/base',{'current':'currency','base':'currency'},'ratio',{'current':'120','base':'100'},'0.2',{'current':'10','base':'0'}),
        ('E6-citation','引用对应','declared==actual',{'declared':'identity','actual':'identity'},'boolean',{'declared':'1','actual':'1'},True,{'declared':'1','actual':'0'}),
        ('E7-comparison','披露区间大小比较','current>base or (current==base and permit_touch==ONE)',{'current':'currency','base':'currency','permit_touch':'boolean'},'boolean',{'current':'120','base':'100','permit_touch':'0'},True,{'current':'100','base':'100','permit_touch':'0'}),
        ('E8-missing','材料就绪','available==ONE',{'available':'boolean'},'boolean',{'available':'1'},True,{'available':'0'}),
        ('E9-version','版本一致','selected==declared',{'selected':'identity','declared':'identity'},'boolean',{'selected':'2','declared':'2'},True,{'selected':'2','declared':'1'}),
        ('FIN-gross-margin','毛利率','(REVENUE-COST)/REVENUE',{'REVENUE':'currency','COST':'currency'},'ratio',{'REVENUE':'100','COST':'75'},'0.25',{'REVENUE':'0','COST':'0'}),
        ('FIN-net-margin','净利率','NP/REVENUE',{'NP':'currency','REVENUE':'currency'},'ratio',{'NP':'13','REVENUE':'100'},'0.13',{'NP':'13','REVENUE':'0'}),
        ('FIN-debt-ratio','资产负债率','LIABILITIES/ASSETS',{'LIABILITIES':'currency','ASSETS':'currency'},'ratio',{'LIABILITIES':'40','ASSETS':'100'},'0.4',{'LIABILITIES':'40','ASSETS':'0'}),
        ('FIN-cash-content','现金含量','CFO/NP',{'CFO':'currency','NP':'currency'},'ratio',{'CFO':'80','NP':'100'},'0.8',{'CFO':'80','NP':'-10'}),
        ('FIN-non-recurring','四输入扣非重算','NP_PARENT-(NRI_TOTAL-NRI_TAX_EFFECT-NRI_MINORITY)',{'NP_PARENT':'currency','NRI_TOTAL':'currency','NRI_TAX_EFFECT':'currency','NRI_MINORITY':'currency'},'currency',{'NP_PARENT':'130','NRI_TOTAL':'20','NRI_TAX_EFFECT':'3','NRI_MINORITY':'2'},'115',{'NP_PARENT':'130','NRI_TOTAL':'20'}),
        ('FIN-non-recurring-share','扣非差额比例','(NP_PARENT-NP_PARENT_ADJ)/NP_PARENT',{'NP_PARENT':'currency','NP_PARENT_ADJ':'currency'},'ratio',{'NP_PARENT':'100','NP_PARENT_ADJ':'60'},'0.4',{'NP_PARENT':'0','NP_PARENT_ADJ':'0'}),
        ('FIN-ROE','加权ROE披露值','ROE',{'ROE':'ratio'},'ratio',{'ROE':'0.12'},'0.12',{}),
        ('FIN-EPS','基本每股收益','NP_PARENT/WEIGHTED_SHARES',{'NP_PARENT':'currency','WEIGHTED_SHARES':'shares'},'currency_per_share',{'NP_PARENT':'100','WEIGHTED_SHARES':'50'},'2',{'NP_PARENT':'100','WEIGHTED_SHARES':'0'}),
        ('FIN-qoq','相邻单季环比','(current-base)/base',{'current':'currency','base':'currency'},'ratio',{'current':'15','base':'10'},'0.5',{'current':'15','base':'-10'}),
        ('FIN-single-quarter','同版本累计推单季','cumulative-prior_cumulative',{'cumulative':'currency','prior_cumulative':'currency'},'currency',{'cumulative':'120','prior_cumulative':'90'},'30',{}),
        ('FIN-pp-difference','百分点差','current-base',{'current':'ratio','base':'ratio'},'ratio',{'current':'0.25','base':'0.20'},'0.05',{}),
        ('FIN-growth-direction','变动方向','current>base',{'current':'currency','base':'currency'},'boolean',{'current':'-80','base':'-114'},True,{'current':'1','base':'1'}),
        ('FIN-divergence','利润现金流方向背离','(NP_CURRENT-NP_BASE)*(CFO_CURRENT-CFO_BASE)<ZERO',{'NP_CURRENT':'currency','NP_BASE':'currency','CFO_CURRENT':'currency','CFO_BASE':'currency'},'boolean',{'NP_CURRENT':'20','NP_BASE':'10','CFO_CURRENT':'5','CFO_BASE':'10'},True,{}),
        ('FIN-low-cash-content','低现金含量登记参数','CFO/NP<threshold',{'CFO':'currency','NP':'currency','threshold':'ratio'},'boolean',{'CFO':'30','NP':'100','threshold':'0.5'},True,{'CFO':'1','NP':'0','threshold':'0.5'}),
        ('FIN-high-nri-share','高扣非差额登记参数','abs((NP_PARENT-NP_PARENT_ADJ)/NP_PARENT)>threshold',{'NP_PARENT':'currency','NP_PARENT_ADJ':'currency','threshold':'ratio'},'boolean',{'NP_PARENT':'100','NP_PARENT_ADJ':'60','threshold':'0.30'},True,{'NP_PARENT':'0','NP_PARENT_ADJ':'0','threshold':'0.30'}),
        ('TIME-latest-as-of','公告日截断','publish_day<=cutoff_day',{'publish_day':'identity','cutoff_day':'identity'},'boolean',{'publish_day':'10','cutoff_day':'11'},True,{'publish_day':'12','cutoff_day':'11'}),
        ('TIME-same-report','同报告比较数','current_doc==base_doc',{'current_doc':'identity','base_doc':'identity'},'boolean',{'current_doc':'1','base_doc':'1'},True,{'current_doc':'1','base_doc':'2'}),
        ('TIME-pinned','声明出处指定报告','declared_doc==fact_doc',{'declared_doc':'identity','fact_doc':'identity'},'boolean',{'declared_doc':'1','fact_doc':'1'},True,{'declared_doc':'1','fact_doc':'2'}),
        ('CHANGE-policy','政策变更触发审查','explicit_disclosure==ONE',{'explicit_disclosure':'boolean'},'boolean',{'explicit_disclosure':'1'},True,{'explicit_disclosure':'0'}),
        ('CHANGE-estimate','估计变更未来适用','explicit_disclosure==ONE',{'explicit_disclosure':'boolean'},'boolean',{'explicit_disclosure':'1'},True,{'explicit_disclosure':'0'}),
        ('CHANGE-error','差错更正触发双版本','explicit_disclosure==ONE',{'explicit_disclosure':'boolean'},'boolean',{'explicit_disclosure':'1'},True,{'explicit_disclosure':'0'}),
    ]
    constants = {'HALF':'0.5','TEN_THOUSAND':'10000','ONE':'1','ZERO':'0','CASH_THRESHOLD':'0.5','NRI_THRESHOLD':'0.30'}
    positive_inputs = {'E5-yoy':['base'],'FIN-gross-margin':['REVENUE'],'FIN-net-margin':['REVENUE'],
                       'FIN-debt-ratio':['ASSETS'],'FIN-cash-content':['NP'],'FIN-non-recurring-share':['NP_PARENT'],
                       'FIN-EPS':['WEIGHTED_SHARES'],'FIN-qoq':['base'],'FIN-low-cash-content':['NP'],'FIN-high-nri-share':['NP_PARENT']}
    # Evaluate boundary examples using the exact production interpreter.
    import sys
    sys.path.insert(0,str(ROOT.parent))
    from mingjing.rules import evaluate_rule
    cards=[]
    for identifier,name,formula,inputs,dimension,values,expected,boundary in definitions:
        used={key:{'value':value,'dimension':'ratio','authority':'MJ-METHOD'} for key,value in constants.items() if re.search(r'\b'+key+r'\b',formula)}
        if identifier == 'FIN-divergence':
            # A sign test uses a squared-currency zero, derived from currency
            # inputs rather than a dimensionless numeric literal.
            formula='(NP_CURRENT-NP_BASE)*(CFO_CURRENT-CFO_BASE)<NP_BASE-NP_BASE'
            # Comparing a product to a currency would be invalid: use ratio
            # sign checks as two factors with positive registered scaling.
            formula='(NP_CURRENT-NP_BASE)/(abs(NP_BASE)+abs(NP_CURRENT))*(CFO_CURRENT-CFO_BASE)/(abs(CFO_BASE)+abs(CFO_CURRENT))<ZERO'
        reference = intervals if identifier == 'E1-value' else nri if identifier == 'FIN-non-recurring' else policy if identifier == 'CHANGE-policy' else estimate if identifier == 'CHANGE-estimate' else correction if identifier == 'CHANGE-error' else common
        card={'id':identifier,'error_type':identifier[:2] if identifier.startswith('E') else 'signal',
              'name':name,'formula':formula,'inputs':inputs,'constants':used,'dimension':dimension,
              'positive_inputs':positive_inputs.get(identifier,[]),'basis':reference['quote'],'basis_refs':[reference],
              'tier':'T2' if identifier=='FIN-non-recurring' else 'T3','version':VERSION,
              'applicable_caliber':'同公司、期间、合并/母公司与扣非口径；实际来源版本受研报日截断',
              'conflict_key':identifier,'implementation':'deterministic_kernel','review_status':'engineering_validated',
              'drafted_by':'deterministic_build_script','approved_by':None,
              'limitations':'规则执行测试不等于真实金融材料验收；专业签发待完成'}
        boundary_result=evaluate_rule(card,boundary)
        serialize=lambda value: value if isinstance(value,(bool,type(None))) else str(value)
        wrong = not expected if isinstance(expected,bool) else str(Decimal(str(expected))+1)
        card['examples']={'positive':{'operands':values,'expected':expected},
                          'negative':{'operands':values,'expected':wrong,'expect_match':False},
                          'boundary':{'operands':boundary,'expected':serialize(boundary_result)}}
        card['version_hash']=digest(card)
        cards.append(card)
    # Sixty actual financial labels, rather than repeated filler aliases.
    groups={
        'REVENUE':['营业收入','营业总收入','营收','主营业务收入','合并营业收入'],
        'COST':['营业成本','合并营业成本'],
        'TOTAL_COST':['营业总成本'],
        'MAIN_COST':['主营业务成本'],
        'NP':['净利润','合并净利润','净利润（净亏损以“-”号填列）','税后净利润'],
        'NP_PARENT':['归母净利润','归属于母公司所有者的净利润','归属于母公司股东的净利润','归属于上市公司股东的净利润','归属于挂牌公司股东的净利润'],
        'NP_PARENT_ADJ':['扣非归母净利润','扣非净利润','归属于上市公司股东的扣除非经常性损益的净利润','归属于上市公司股东的扣除非经常性损益后的净利润','归属于挂牌公司股东的扣除非经常性损益后的净利润','归属于母公司所有者的扣除非经常性损益后的净利润'],
        'CFO':['经营活动产生的现金流量净额','经营现金流','经营性现金流量净额','经营活动现金流量净额','经营活动净现金流'],
        'ASSETS':['资产总计','总资产','资产合计'],
        'LIABILITIES':['负债合计','负债总计','总负债'],
        'EQUITY_PARENT':['归属于母公司所有者权益合计','归属于上市公司股东的净资产','归属于挂牌公司股东的净资产','归母净资产'],
        'EQUITY':['所有者权益合计','股东权益合计','净资产'],
        'EPS':['基本每股收益','基本每股收益（元/股）'],
        'EPS_DILUTED':['稀释每股收益','稀释每股收益（元/股）'],
        'ROE':['加权平均净资产收益率','加权净资产收益率'],
        'GROSS_MARGIN':['毛利率','综合毛利率'],
        'NET_MARGIN':['净利率','销售净利率'],
        'DEBT_RATIO':['资产负债率'],
        'NRI_TOTAL':['非经常性损益合计','非经常性损益总额'],
        'NRI_TAX_EFFECT':['所得税影响额','非经常性损益所得税影响额'],
        'NRI_MINORITY':['少数股东权益影响额（税后）','少数股东损益影响额'],
        'WEIGHTED_SHARES':['发行在外普通股的加权平均数'],
    }
    dictionary=[]
    for account,labels in groups.items():
        for label in labels:
            dictionary.append({'id':f'CAL-{len(dictionary)+1:03d}','label':label,'account':account,
                               'scope':'合并','nr':'扣非' if account=='NP_PARENT_ADJ' else '未扣非',
                               'requires_context': label in {'主营业务收入','主营业务成本','营业总成本','税后净利润','扣非净利润','净资产','综合毛利率','所得税影响额'},
                               'basis_refs':[parent_profit if account=='NP_PARENT' else common], 'approved_by':None,'review_status':'pending_human_signoff'})
    if len(dictionary)!=60:
        raise AssertionError(f'口径数量:{len(dictionary)}')
    metadata={'version':VERSION,'primary_root':'mainProj/存放会计准则','sources':sources,
              'professional_signoff_status':'pending','source_policy':'snapshots_for_provenance_only; primary_live_KB_unchanged',
              'scope_definition_revision':old.get('scope_definition_revision','NP_PARENT-consolidated.v1'),
              'disclosure_interval_revision':'half-open-intersection.v1','hedge_policy_revision':'positive-relative-h.v1'}
    metadata['version_hash']=digest(metadata)
    write('cards.json',cards);write('caliber.json',dictionary);write('source_clauses.json',clauses);write('sources.json',metadata)
    lock={path.relative_to(ROOT).as_posix():hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(ROOT.rglob('*')) if path.is_file() and path.suffix in {'.json','.md'} and path.name!='rulepack.lock'}
    write('rulepack.lock',{'version':VERSION,'files':lock,'sha256':digest(lock)})


def main():
    # This generator contains only the 0.5 base rules. Never let it erase a
    # newer coverage release, even if sources.json was partly overwritten.
    import sys
    sys.path.insert(0, str(ROOT.parent))
    from scripts.rulepack_build_guard import assert_rebuild_compatible, exclusive_rulepack_build
    with exclusive_rulepack_build(ROOT):
        assert_rebuild_compatible(ROOT, VERSION)
        _build()


if __name__=='__main__':
    main()
