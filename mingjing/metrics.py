"""One registry of authorised metric identities and deterministic dependencies.

Definition IDs refer to source-proved methods, never a model's proposed formula.
"""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class MetricInput:
    name: str
    account: str
    dimension: str = 'self'
    prior_year: bool = False
    nr: str | None = None

@dataclass(frozen=True)
class MetricSpec:
    account: str
    label: str
    rule_id: str
    inputs: tuple[MetricInput, ...]
    kind: str = 'ratio'
    definition_id: str = ''
    definition_required: bool = False
    analysis: bool = True
    scopes: tuple[str, ...] = ('合并', '母公司')

def _inputs(*accounts):
    return tuple(MetricInput(account, account) for account in accounts)

_SPECS = (
    MetricSpec('AR_NOTES', '应收票据及应收账款合计', 'FIN-receivables-notes-sum', _inputs('AR', 'NOTES_RECEIVABLE'), kind='amount', analysis=False),
    MetricSpec('AP_NOTES', '应付票据及应付账款合计', 'FIN-payables-notes-sum', _inputs('AP', 'NOTES_PAYABLE'), kind='amount', analysis=False),
    MetricSpec('EQUITY', '合并所有者权益合计', 'FIN-consolidated-equity-sum', _inputs('EQUITY_PARENT', 'EQUITY_MINORITY'), kind='amount', analysis=False, scopes=('合并',)),
    MetricSpec('LIABILITIES_EQUITY', '负债和所有者权益合计', 'FIN-liabilities-equity-sum', _inputs('LIABILITIES', 'EQUITY'), kind='amount', analysis=False),
    MetricSpec('GROSS_MARGIN', '毛利率', 'FIN-gross-margin', _inputs('REVENUE', 'COST')),
    MetricSpec('NET_MARGIN', '净利率（净利润/营业收入）', 'FIN-net-margin', _inputs('NP', 'REVENUE'), definition_required=True),
    MetricSpec('NP_PARENT_MARGIN', '归母净利率', 'FIN-parent-net-margin', _inputs('NP_PARENT', 'REVENUE')),
    MetricSpec('DEBT_RATIO', '资产负债率', 'FIN-debt-ratio', _inputs('LIABILITIES', 'ASSETS')),
    MetricSpec('CASH_CONTENT', '经营现金流含金量', 'FIN-cash-content', _inputs('CFO', 'NP')),
    MetricSpec('CURRENT_RATIO', '流动比率', 'FIN-current-ratio', _inputs('CURRENT_ASSETS', 'CURRENT_LIABILITIES')),
    MetricSpec('QUICK_RATIO', '速动比率（流动资产减存货）', 'FIN-quick-ratio', _inputs('CURRENT_ASSETS', 'INVENTORY', 'CURRENT_LIABILITIES'), definition_id='metric:quick-ratio-inventory', definition_required=True),
    MetricSpec('SELLING_EXPENSE_RATIO', '销售费用率', 'FIN-selling-expense-ratio', _inputs('EXP_SELLING', 'REVENUE')),
    MetricSpec('ADMIN_EXPENSE_RATIO', '管理费用率', 'FIN-admin-expense-ratio', _inputs('EXP_ADMIN', 'REVENUE')),
    MetricSpec('RD_EXPENSE_RATIO', '研发费用率', 'FIN-rd-expense-ratio', _inputs('EXP_RD', 'REVENUE')),
    MetricSpec('FINANCE_EXPENSE_RATIO', '财务费用率', 'FIN-finance-expense-ratio', _inputs('EXP_FINANCE', 'REVENUE')),
    MetricSpec('PERIOD_EXPENSE_RATIO', '期间费用率', 'FIN-period-expense-ratio', _inputs('EXP_SELLING', 'EXP_ADMIN', 'EXP_RD', 'EXP_FINANCE', 'REVENUE'), definition_id='metric:period-expense-four', definition_required=True),
    MetricSpec('ASSET_TURNOVER', '总资产周转率（平均资产）', 'FIN-asset-turnover', (MetricInput('REVENUE','REVENUE'), MetricInput('ASSETS_END','ASSETS'), MetricInput('ASSETS_START','ASSETS',prior_year=True)), definition_id='metric:asset-turnover-average', definition_required=True),
    MetricSpec('INVENTORY_TURNOVER', '存货周转率（平均存货）', 'FIN-inventory-turnover', (MetricInput('COST','COST'), MetricInput('INVENTORY_END','INVENTORY'), MetricInput('INVENTORY_START','INVENTORY',prior_year=True)), definition_id='metric:inventory-turnover-average', definition_required=True),
    MetricSpec('AR_TURNOVER', '应收账款周转率（平均应收账款）', 'FIN-ar-turnover', (MetricInput('REVENUE','REVENUE'), MetricInput('AR_END','AR'), MetricInput('AR_START','AR',prior_year=True)), definition_id='metric:ar-turnover-average', definition_required=True),
    MetricSpec('AP_TURNOVER', '应付账款周转率（平均应付账款）', 'FIN-ap-turnover', (MetricInput('COST','COST'), MetricInput('AP_END','AP'), MetricInput('AP_START','AP',prior_year=True)), definition_id='metric:ap-turnover-average', definition_required=True),
    MetricSpec('REVENUE_SHARE', '业务或客户收入占公司营收', 'FIN-revenue-share', (MetricInput('PART_REVENUE','REVENUE'),MetricInput('TOTAL_REVENUE','REVENUE','total')), definition_id='metric:revenue-share', definition_required=True, analysis=False),
    MetricSpec('BROKER_OTHER_CURRENT_LIABILITIES', '券商简表其他流动负债', 'FIN-broker-other-current-liabilities', _inputs('CURRENT_LIABILITIES','SHORT_TERM_BORROWING','AP_NOTES'), kind='amount',definition_id='metric:broker-other-current-liabilities',definition_required=True,analysis=False),
    MetricSpec('BROKER_OTHER_NONCURRENT_LIABILITIES', '券商简表其他非流动负债', 'FIN-broker-other-noncurrent-liabilities', _inputs('NONCURRENT_LIABILITIES','LONG_TERM_BORROWING'), kind='amount',definition_id='metric:broker-other-noncurrent-liabilities',definition_required=True,analysis=False),
    MetricSpec('BROKER_OTHER_CURRENT_ASSETS', '券商简表其他流动资产', 'FIN-broker-other-current-assets', (MetricInput('CURRENT_ASSETS','CURRENT_ASSETS'),MetricInput('CASH_SOURCE','ASSET_CASH_UNSPECIFIED'),MetricInput('AR_NOTES','AR_NOTES'),MetricInput('OTHER_RECEIVABLES','OTHER_RECEIVABLES'),MetricInput('PREPAYMENTS','PREPAYMENTS'),MetricInput('INVENTORY','INVENTORY')),kind='amount',definition_id='metric:broker-other-current-assets',definition_required=True,analysis=False),
    MetricSpec('BROKER_OTHER_NONCURRENT_ASSETS', '券商简表其他非流动资产', 'FIN-broker-other-noncurrent-assets', (MetricInput('NONCURRENT_ASSETS','NONCURRENT_ASSETS'),MetricInput('PPE','PPE'),MetricInput('INTANGIBLES','INTANGIBLES'),MetricInput('INVESTMENTS_SOURCE','INVESTMENTS_UNSPECIFIED')),kind='amount',definition_id='metric:broker-other-noncurrent-assets',definition_required=True,analysis=False),
    MetricSpec('NP_PARENT_ADJ_NET', '已披露归母税后非经常净额扣非重算', 'FIN-non-recurring-net',_inputs('NP_PARENT','NONRECURRING_NET'),kind='amount',definition_id='metric:nri-parent-after-tax',definition_required=True,analysis=False),
    MetricSpec('NP_PARENT_ADJ', '四输入扣非归母净利润', 'FIN-non-recurring',tuple(MetricInput(x,x,nr='未扣非') for x in ('NP_PARENT','NRI_TOTAL','NRI_TAX_EFFECT','NRI_MINORITY')),kind='amount',analysis=False),
    MetricSpec('CASH_EQUIVALENTS_CHANGE', '现金及现金等价物净增加额桥接', 'FIN-cash-bridge',_inputs('CFO','CFI','CFF','FX_CASH_EFFECT'),kind='amount'),
    # Direct disclosure wins.  The bridge is allowed only when the statement
    # line is absent and all neighbouring rows are from one report/version.
    MetricSpec('NONOPERATING_INCOME', '营业外收入横线派生', 'FIN-nonoperating-income-bridge',
               _inputs('PROFIT_TOTAL','OPERATING_PROFIT','NONOPERATING_EXPENSE'),
               kind='amount', definition_id='metric:nonoperating-income-bridge',
               # The definition identifies the fallback bridge; a raw
               # statement line has no metric definition and must remain
               # queryable as-is.
               definition_required=False, analysis=False),
)
METRIC_SPECS = {item.account: item for item in _SPECS}
RATIO_METRICS = frozenset(item.account for item in _SPECS if item.kind == 'ratio')
AMOUNT_METRICS = frozenset(item.account for item in _SPECS if item.kind == 'amount')
TURNOVER_METRICS = frozenset({'ASSET_TURNOVER','INVENTORY_TURNOVER','AR_TURNOVER','AP_TURNOVER'})

def metric_spec(account, definition_id=''):
    item = METRIC_SPECS.get(account)
    if not item:
        return None
    # Alternate source IDs may express the same registered formula explicitly.
    aliases={'REVENUE_SHARE':{'metric:customer-revenue-share'},'NP_PARENT_ADJ_NET':{'metric:nri-parent-after-tax'}}
    if item.definition_required and definition_id not in {item.definition_id, 'metric:'+account.lower().replace('_','-'), *aliases.get(account,set())}:
        return None
    if item.definition_required and not definition_id:
        return None
    return item

def metric_definitions():
    return [{'account':item.account,'label':item.label,'rule_id':item.rule_id,
             'definition_id':item.definition_id,'definition_required':item.definition_required,'scopes':list(item.scopes),
             'inputs':[{'name':x.name,'account':x.account,'dimension':x.dimension,'prior_year':x.prior_year} for x in item.inputs]}
            for item in _SPECS]
