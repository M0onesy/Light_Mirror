"""Deterministically register engineering rules, preserving pending human review."""
import hashlib
import json
from pathlib import Path

def refresh_rulepack_lock(root):
    """Explicit build-time lock; never carry approval from an earlier batch."""
    root = Path(root)
    sources = json.loads((root/'sources.json').read_text(encoding='utf-8'))
    files = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(root.rglob('*'))
             if path.is_file() and path.suffix in {'.json', '.md'}}
    lock = {'version': sources['version'], 'files': files,
            'sha256': hashlib.sha256(json.dumps(files, ensure_ascii=False, sort_keys=True,
                                               separators=(',', ':')).encode('utf-8')).hexdigest()}
    temporary = root/'rulepack.lock.tmp'
    temporary.write_text(json.dumps(lock, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(root/'rulepack.lock')
    return lock

def _extend(root):
    cards=json.loads((root/'cards.json').read_text(encoding='utf-8'))
    template=next(x for x in cards if x['id']=='FIN-gross-margin')
    specs=[
      ('FIN-parent-net-margin','归母净利率','NP_PARENT/REVENUE',['NP_PARENT','REVENUE'],['REVENUE'],{'NP_PARENT':'-10','REVENUE':'100'},'-0.1'),
      ('FIN-current-ratio','流动比率','CURRENT_ASSETS/CURRENT_LIABILITIES',['CURRENT_ASSETS','CURRENT_LIABILITIES'],['CURRENT_LIABILITIES'],{'CURRENT_ASSETS':'200','CURRENT_LIABILITIES':'100'},'2'),
      ('FIN-quick-ratio','速动比率：流动资产减存货','(CURRENT_ASSETS-INVENTORY)/CURRENT_LIABILITIES',['CURRENT_ASSETS','INVENTORY','CURRENT_LIABILITIES'],['CURRENT_LIABILITIES'],{'CURRENT_ASSETS':'200','INVENTORY':'50','CURRENT_LIABILITIES':'100'},'1.5'),
    ]
    for code,account,title in [('selling','EXP_SELLING','销售'),('admin','EXP_ADMIN','管理'),('rd','EXP_RD','研发'),('finance','EXP_FINANCE','财务')]:
        specs.append(('FIN-'+code+'-expense-ratio',title+'费用率',account+'/REVENUE',[account,'REVENUE'],['REVENUE'],{account:'5','REVENUE':'100'},'0.05'))
    specs += [
      ('FIN-receivables-notes-sum','应收票据及应收账款合计','AR+NOTES_RECEIVABLE',['AR','NOTES_RECEIVABLE'],[],{'AR':'40','NOTES_RECEIVABLE':'2'},'42'),
      ('FIN-payables-notes-sum','应付票据及应付账款合计','AP+NOTES_PAYABLE',['AP','NOTES_PAYABLE'],[],{'AP':'60','NOTES_PAYABLE':'20'},'80'),
      ('FIN-consolidated-equity-sum','合并所有者权益合计','EQUITY_PARENT+EQUITY_MINORITY',['EQUITY_PARENT','EQUITY_MINORITY'],[],{'EQUITY_PARENT':'90','EQUITY_MINORITY':'10'},'100'),
      ('FIN-liabilities-equity-sum','负债和所有者权益合计','LIABILITIES+EQUITY',['LIABILITIES','EQUITY'],[],{'LIABILITIES':'60','EQUITY':'40'},'100'),
      ('FIN-period-expense-ratio','明确四项期间费用率','(EXP_SELLING+EXP_ADMIN+EXP_RD+EXP_FINANCE)/REVENUE',['EXP_SELLING','EXP_ADMIN','EXP_RD','EXP_FINANCE','REVENUE'],['REVENUE'],{'EXP_SELLING':'5','EXP_ADMIN':'5','EXP_RD':'5','EXP_FINANCE':'5','REVENUE':'100'},'0.2'),
      ('FIN-asset-turnover','总资产周转率：平均资产','REVENUE/((ASSETS_START+ASSETS_END)*HALF)',['REVENUE','ASSETS_START','ASSETS_END'],['ASSETS_START','ASSETS_END'],{'REVENUE':'200','ASSETS_START':'100','ASSETS_END':'100'},'2'),
      ('FIN-inventory-turnover','存货周转率：平均存货','COST/((INVENTORY_START+INVENTORY_END)*HALF)',['COST','INVENTORY_START','INVENTORY_END'],['INVENTORY_START','INVENTORY_END'],{'COST':'200','INVENTORY_START':'100','INVENTORY_END':'100'},'2'),
      ('FIN-ar-turnover','应收账款周转率：平均应收账款','REVENUE/((AR_START+AR_END)*HALF)',['REVENUE','AR_START','AR_END'],['AR_START','AR_END'],{'REVENUE':'200','AR_START':'100','AR_END':'100'},'2'),
      ('FIN-revenue-share','收入占公司营业收入比例','PART_REVENUE/TOTAL_REVENUE',['PART_REVENUE','TOTAL_REVENUE'],['TOTAL_REVENUE'],{'PART_REVENUE':'25','TOTAL_REVENUE':'100'},'0.25'),
      ('FIN-broker-other-current-liabilities','券商简表流动负债残项','CURRENT_LIABILITIES-SHORT_TERM_BORROWING-AP_NOTES',['CURRENT_LIABILITIES','SHORT_TERM_BORROWING','AP_NOTES'],[],{'CURRENT_LIABILITIES':'100','SHORT_TERM_BORROWING':'10','AP_NOTES':'20'},'70'),
      ('FIN-broker-other-noncurrent-liabilities','券商简表非流动负债残项','NONCURRENT_LIABILITIES-LONG_TERM_BORROWING',['NONCURRENT_LIABILITIES','LONG_TERM_BORROWING'],[],{'NONCURRENT_LIABILITIES':'100','LONG_TERM_BORROWING':'20'},'80'),
      ('FIN-broker-other-current-assets','券商简表流动资产残项：原文现金定义','CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-INVENTORY',['CURRENT_ASSETS','CASH_SOURCE','AR_NOTES','INVENTORY'],[],{'CURRENT_ASSETS':'100','CASH_SOURCE':'20','AR_NOTES':'10','INVENTORY':'5'},'65'),
      ('FIN-broker-other-noncurrent-assets','券商简表非流动资产残项：原文投资定义','NONCURRENT_ASSETS-PPE-INTANGIBLES-INVESTMENTS_SOURCE',['NONCURRENT_ASSETS','PPE','INTANGIBLES','INVESTMENTS_SOURCE'],[],{'NONCURRENT_ASSETS':'100','PPE':'20','INTANGIBLES':'10','INVESTMENTS_SOURCE':'5'},'65'),
      ('FIN-non-recurring-net','归母税后净额扣非重算','NP_PARENT-NONRECURRING_NET',['NP_PARENT','NONRECURRING_NET'],[],{'NP_PARENT':'100','NONRECURRING_NET':'10'},'90'),
      ('FIN-cash-bridge','现金净增桥接：含明确汇率影响','CFO+CFI+CFF+FX_CASH_EFFECT',['CFO','CFI','CFF','FX_CASH_EFFECT'],[],{'CFO':'10','CFI':'-5','CFF':'3','FX_CASH_EFFECT':'0'},'8'),
    ]
    aggregates={'FIN-receivables-notes-sum','FIN-payables-notes-sum','FIN-consolidated-equity-sum','FIN-liabilities-equity-sum'}
    amount={'FIN-broker-other-current-liabilities','FIN-broker-other-noncurrent-liabilities','FIN-broker-other-current-assets','FIN-broker-other-noncurrent-assets','FIN-non-recurring-net','FIN-cash-bridge',*aggregates}
    for identifier,name,formula,inputs,positive,operands,expected in specs:
        card=json.loads(json.dumps(template))
        card.update(id=identifier,name=name,formula=formula,inputs={x:'currency' for x in inputs},positive_inputs=positive,dimension='currency' if identifier in amount else 'ratio',version='0.6.0',conflict_key=identifier,drafted_by='extend_coverage_rules.py')
        card['constants']={'HALF':{'value':'0.5','dimension':'ratio'}} if 'HALF' in formula else {}
        boundary=dict(operands)
        if positive:
            boundary[positive[0]]='0'
            boundary_expected=None
        else:
            boundary={x:'0' for x in inputs};boundary_expected='0'
        card['examples']={'positive':{'operands':operands,'expected':expected},'negative':{'operands':operands,'expected':'999','expect_match':False},'boundary':{'operands':boundary,'expected':boundary_expected}}
        card.pop('version_hash',None)
        card['version_hash']=hashlib.sha256(json.dumps(card,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        cards=[x for x in cards if x['id']!=identifier]+[card]
    source_path=root/'sources/MJ-COVERAGE-METHOD.md'
    source={'id':'MJ-COVERAGE-METHOD','kind':'methodology','name':'金融覆盖数学方法工程草案',
            'snapshot':'sources/MJ-COVERAGE-METHOD.md','sha256':hashlib.sha256(source_path.read_bytes()).hexdigest(),
            'source_url':None,'effective_at':None,'validation':'engineering_validated_human_review_pending'}
    sources=json.loads((root/'sources.json').read_text(encoding='utf-8'))
    sources['sources']=[x for x in sources['sources'] if x['id']!=source['id']]+[source]
    sources['version']='0.6.0'
    sources.pop('version_hash',None)
    sources['version_hash']=hashlib.sha256(json.dumps(sources,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    (root/'sources.json').write_text(json.dumps(sources,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    clauses=json.loads((root/'source_clauses.json').read_text(encoding='utf-8'))
    for identifier in aggregates:
        word=('票据账款合计' if 'notes-sum' in identifier else
              '合并权益' if identifier=='FIN-consolidated-equity-sum' else '负债权益')
        quote=next(x for x in source_path.read_text(encoding='utf-8').splitlines() if x.startswith(word))
        clause={'id':identifier+'-method','source_id':source['id'],'quote':quote,
                'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'normalization':'UTF8_LF'}
        clauses=[x for x in clauses if x['id']!=clause['id']]+[clause]
        card=next(x for x in cards if x['id']==identifier)
        card.update(basis=quote,basis_refs=[{**{k:clause[k] for k in ('source_id','quote','quote_sha256')},'clause_id':clause['id']}])
        card['applicable_caliber']='原文明确科目组成；同公司、期间、币种、报表口径及同报告版本；每项有原始证据；缺失不默认零'
        if identifier=='FIN-consolidated-equity-sum':
            card['applicable_caliber']+='；只适用于合并报表，母公司口径不可使用归母与少数股东合成'
        card.pop('version_hash',None)
        card['version_hash']=hashlib.sha256(json.dumps(card,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    for identifier,formula,inputs,dimension,positive,operands,expected,word in [
       ('FIN-cagr','cagr(current,base,years)',{'current':'currency','base':'currency','years':'ratio'},'ratio',['current','base','years'],{'current':'121','base':'100','years':'2'},'0.1','CAGR'),
       ('FIN-range','lower<=actual_lower and actual_upper<=upper',{'lower':'currency','upper':'currency','actual_lower':'currency','actual_upper':'currency'},'boolean',[],{'lower':'1','upper':'3','actual_lower':'1.5','actual_upper':'2.5'},True,'范围断言')]:
        quote=next(x for x in source_path.read_text(encoding='utf-8').splitlines() if x.startswith(word))
        clause={'id':identifier+'-method','source_id':source['id'],'quote':quote,'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'normalization':'UTF8_LF'}
        clauses=[x for x in clauses if x['id']!=clause['id']]+[clause]
        card=json.loads(json.dumps(template));card.update(id=identifier,name=word,formula=formula,inputs=inputs,dimension=dimension,positive_inputs=positive,
                           version='0.6.0',conflict_key=identifier,basis=quote,basis_refs=[{**{k:clause[k] for k in ('source_id','quote','quote_sha256')},'clause_id':clause['id']}])
        card['examples']={'positive':{'operands':operands,'expected':expected},'negative':{'operands':operands,'expected':False if isinstance(expected,bool) else '999','expect_match':False},
                         'boundary':{'operands':{'current':'0','base':'100','years':'2'} if identifier=='FIN-cagr' else {'lower':'1','upper':'3','actual_lower':'0','actual_upper':'2'},'expected':None if identifier=='FIN-cagr' else False}}
        card.pop('version_hash',None);card['version_hash']=hashlib.sha256(json.dumps(card,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        cards=[x for x in cards if x['id']!=identifier]+[card]
    (root/'source_clauses.json').write_text(json.dumps(clauses,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (root/'cards.json').write_text(json.dumps(cards,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    refresh_rulepack_lock(root)

def main():
    import sys
    project = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project))
    from scripts.rulepack_build_guard import assert_rebuild_compatible, exclusive_rulepack_build
    root = project/'rulepack'
    with exclusive_rulepack_build(root):
        assert_rebuild_compatible(root, '0.6.0')
        _extend(root)


if __name__=='__main__':main()
