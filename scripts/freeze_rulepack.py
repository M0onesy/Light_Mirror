"""Freeze rule-card metadata and primary knowledge-source provenance."""
import hashlib
import json
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
kb = root.parent / '存放会计准则'
rule_root = root / 'rulepack'

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()

cards = json.loads((rule_root / 'cards.json').read_text(encoding='utf-8'))
cards.extend([
    {'id':'E2-period','error_type':'E2','name':'期间匹配','formula':'declared_period == evidence_period','basis':'原始报告列标题；缺少期间材料本身不能证明期间错误','examples':{'positive':'同年同累计期间','negative':'把2022年数值引为2023年','boundary':'缺少半年报时弃权'}},
    {'id':'E3-caliber','error_type':'E3','name':'口径匹配','formula':'company/account/scope/nr must all match','basis':'合并与母公司、归母与净利润、扣非与未扣非口径不能混用','examples':{'positive':'同为合并归母未扣非','negative':'把母公司数值用于合并','boundary':'科目歧义时弃权'}},
    {'id':'E6-citation','error_type':'E6','name':'引用匹配','formula':'declared_evidence in supporting_fact.evidence','basis':'冻结材料及证据定位','examples':{'positive':'引用对应事实同页单元格','negative':'引用不存在或无关证据','boundary':'扫描件不可定位时弃权'}},
    {'id':'E7-comparison','error_type':'E7','name':'逻辑比较','formula':'current gt/lt/ge/le/eq base','basis':'同公司、同科目、同口径比较','examples':{'positive':'120大于100','negative':'声称120小于100','boundary':'缺少任一比较操作数时弃权'}},
    {'id':'FIN-gross-margin','error_type':'signal','name':'毛利率','formula':'(revenue-cost)/revenue','basis':'合并利润表营业收入与营业成本','examples':{'positive':'(100-75)/100=25%','negative':'使用分部成本和公司收入','boundary':'分母非正弃权'}},
    {'id':'FIN-net-margin','error_type':'signal','name':'净利率','formula':'net_profit/revenue','basis':'合并利润表净利润（含少数股东）与营业收入','examples':{'positive':'13/100=13%','negative':'未说明便用归母替代净利润','boundary':'分母非正弃权'}},
    {'id':'FIN-debt-ratio','error_type':'signal','name':'资产负债率','formula':'liabilities/assets','basis':'同一时点同口径资产负债表','examples':{'positive':'40/100=40%','negative':'不同日期资产和负债','boundary':'缺材料弃权'}},
    {'id':'FIN-ROE','error_type':'signal','name':'净资产收益率','formula':'disclosed_weighted_ROE; do not guess weighted_equity','basis':'加权净资产收益率需要明确加权计算口径','examples':{'positive':'使用同报告已披露加权ROE','negative':'期末净资产当加权平均净资产','boundary':'缺少加权操作数则弃权'}},
    {'id':'FIN-change','error_type':'signal','name':'会计变更与差错更正','formula':'require explicit disclosure of change type and applicable effective date','basis':'企业会计准则第28号；政策变更、估计变更与差错更正分开处理','examples':{'positive':'明确披露政策变更和追溯信息','negative':'把估计变更一律追溯','boundary':'仅有标题不足以断言财务影响'}},
])
for card in cards:
    card['version'] = '0.3.0'
    card['applicable_caliber'] = '同公司、同科目、同期间、同合并/母公司口径、同扣非口径、截止报告日期适用披露版本'
    card['dimension'] = 'ratio' if any(x in card['id'] for x in ('margin','ratio','ROE','yoy','cash-content')) else 'base_currency_or_disclosure_identity'
    card['implementation'] = 'deterministic_kernel' if card['error_type'] != 'signal' or card['id'] in {'FIN-gross-margin','FIN-net-margin','FIN-debt-ratio'} else 'formula_or_review_guidance'
    card['version_hash'] = digest({k:v for k,v in card.items() if k != 'version_hash'})
(rule_root / 'cards.json').write_text(json.dumps(cards, ensure_ascii=False, indent=2), encoding='utf-8')
sources = []
for number in (28,30,31,33):
    path = next(kb.rglob(f'*第{number}号*'), None)
    if path and path.is_file() and path.suffix == '.md':
        raw = path.read_text(encoding='utf-8')
        url = re.search(r'- 来源：(\S+)', raw)
        effective = re.search(r'- 生效：(\S+)', raw)
        published = re.search(r'- 发布：(\S+)', raw)
        sources.append({'id':f'CAS{number}', 'name':raw.splitlines()[0].lstrip('# '), 'path':path.relative_to(root.parent).as_posix(), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'source_url':url[1] if url else None, 'published_at':published[1] if published else None, 'effective_at':effective[1] if effective else None, 'root':'mainProj/存放会计准则'})
nr = kb / '非经常性损益_2023修订_正文.md'
if nr.is_file():
    sources.append({'id':'NON_RECURRING_2023','name':'非经常性损益（2023年修订）','path':nr.relative_to(root.parent).as_posix(), 'sha256':hashlib.sha256(nr.read_bytes()).hexdigest(), 'source_url':None, 'effective_at':None, 'provenance_note':'现有原文未登记官方URL与生效日期；不能据此对外宣称条文时效已核实','root':'mainProj/存放会计准则'})
registry = {'version':'0.3.0','primary_root':'mainProj/存放会计准则','sources':sources}
registry['version_hash'] = digest(registry)
(rule_root/'sources.json').write_text(json.dumps(registry,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'cards':len(cards),'sources':len(sources),'version':'0.3.0'}))
