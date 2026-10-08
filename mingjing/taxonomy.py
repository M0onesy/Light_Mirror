"""Financial account identities shared by source parsers and numeric guards.

These aliases identify accounting captions, not values or a default scope.
Parent-attributable profit/equity, consolidated profit/equity, balance-sheet
stocks and cash-flow movements remain different identities. Undefined broker
captions retain explicit unresolved codes; a similar amount cannot define them.
"""
from __future__ import annotations

import re


FINANCIAL_LABELS = {
    '货币资金': 'CASH_FUNDS',
    '现金及现金等价物余额': 'CASH_EQUIVALENTS',
    '期末现金及现金等价物余额': 'CASH_EQUIVALENTS',
    '期初现金及现金等价物余额': 'CASH_EQUIVALENTS_OPENING',
    '现金及现金等价物净增加额': 'CASH_EQUIVALENTS_CHANGE',
    '现金净增加额': 'CASH_EQUIVALENTS_CHANGE',
    '现金': 'ASSET_CASH_UNSPECIFIED',
    '应收票据及应收账款': 'AR_NOTES',
    '应收账款及应收票据': 'AR_NOTES',
    '应收账款': 'AR', '应收票据': 'NOTES_RECEIVABLE',
    '其他应收款': 'OTHER_RECEIVABLES', '长期应收款': 'LONG_TERM_RECEIVABLES',
    '预付款项': 'PREPAYMENTS', '预付账款': 'PREPAYMENTS',
    '存货': 'INVENTORY', '合同资产': 'CONTRACT_ASSETS',
    '流动资产合计': 'CURRENT_ASSETS', '流动资产': 'CURRENT_ASSETS',
    '非流动资产合计': 'NONCURRENT_ASSETS', '非流动资产': 'NONCURRENT_ASSETS',
    '其他流动资产': 'OTHER_CURRENT_ASSETS', '其他非流动资产': 'OTHER_NONCURRENT_ASSETS',
    '固定资产': 'PPE', '无形资产': 'INTANGIBLES', '在建工程': 'CONSTRUCTION_IN_PROGRESS',
    '使用权资产': 'RIGHT_OF_USE_ASSETS', '商誉': 'GOODWILL',
    '长期待摊费用': 'LONG_TERM_PREPAID_EXPENSES', '递延所得税资产': 'DEFERRED_TAX_ASSETS',
    '长期股权投资': 'LONG_TERM_EQUITY_INVESTMENTS',
    '长期投资': 'INVESTMENTS_UNSPECIFIED',
    '资产总计': 'ASSETS', '资产合计': 'ASSETS',
    '短期借款': 'SHORT_TERM_BORROWING', '长期借款': 'LONG_TERM_BORROWING',
    '应付票据及应付账款': 'AP_NOTES', '应付账款及应付票据': 'AP_NOTES',
    '应付账款': 'AP', '应付票据': 'NOTES_PAYABLE',
    '其他应付款': 'OTHER_PAYABLES', '合同负债': 'CONTRACT_LIABILITIES',
    '应付职工薪酬': 'PAYROLL_PAYABLE', '应交税费': 'TAX_PAYABLE',
    '租赁负债': 'LEASE_LIABILITIES', '长期应付款': 'LONG_TERM_PAYABLES',
    '递延收益': 'DEFERRED_INCOME', '递延所得税负债': 'DEFERRED_TAX_LIABILITIES',
    '一年内到期的非流动负债': 'CURRENT_PORTION_NONCURRENT_LIABILITIES',
    '流动负债合计': 'CURRENT_LIABILITIES', '流动负债': 'CURRENT_LIABILITIES',
    '非流动负债合计': 'NONCURRENT_LIABILITIES', '非流动负债': 'NONCURRENT_LIABILITIES',
    '其他流动负债': 'OTHER_CURRENT_LIABILITIES', '其他非流动负债': 'OTHER_NONCURRENT_LIABILITIES',
    '负债合计': 'LIABILITIES', '负债总计': 'LIABILITIES',
    '归属于母公司所有者权益（或股东权益）合计': 'EQUITY_PARENT',
    '归属于母公司所有者权益(或股东权益)合计': 'EQUITY_PARENT',
    '归属于母公司所有者权益合计': 'EQUITY_PARENT',
    '归属于母公司股东权益': 'EQUITY_PARENT', '归属母公司股东权益': 'EQUITY_PARENT',
    '归属于上市公司股东的净资产': 'EQUITY_PARENT',
    '归属于挂牌公司股东的净资产': 'EQUITY_PARENT',
    '归属于母公司股东的净资产': 'EQUITY_PARENT',
    '所有者权益（或股东权益）合计': 'EQUITY',
    '所有者权益(或股东权益)合计': 'EQUITY',
    '所有者权益合计': 'EQUITY', '股东权益合计': 'EQUITY',
    '少数股东权益': 'EQUITY_MINORITY',
    '负债和所有者权益（或股东权益）总计': 'LIABILITIES_EQUITY',
    '负债和所有者权益(或股东权益)总计': 'LIABILITIES_EQUITY',
    '负债和股东权益': 'LIABILITIES_EQUITY',
    '股本': 'SHARE_CAPITAL', '资本公积': 'CAPITAL_RESERVE',
    '盈余公积': 'SURPLUS_RESERVE', '未分配利润': 'RETAINED_UNDISTRIBUTED_PROFIT',
    '留存收益': 'RETAINED_EARNINGS',
    '营业收入': 'REVENUE', '营业总收入': 'REVENUE',
    '营业成本': 'COST', '营业总成本': 'TOTAL_COST', '主营业务成本': 'MAIN_COST',
    '税金及附加': 'TAX_SURCHARGES', '营业税金及附加': 'TAX_SURCHARGES',
    '销售费用': 'EXP_SELLING', '营业费用': 'EXP_SELLING',
    '管理费用': 'EXP_ADMIN', '研发费用': 'EXP_RD', '财务费用': 'EXP_FINANCE',
    '利息费用': 'EXP_INTEREST', '其他收益': 'OTHER_INCOME',
    '信用减值损失': 'CREDIT_IMPAIRMENT', '资产减值损失': 'ASSET_IMPAIRMENT',
    '公允价值变动收益': 'FAIR_VALUE_INCOME',
    '投资收益': 'INVESTMENT_INCOME', '投资净收益': 'INVESTMENT_INCOME',
    '资产处置收益': 'ASSET_DISPOSAL_INCOME', '营业利润': 'OPERATING_PROFIT',
    '营业外收入': 'NONOPERATING_INCOME', '营业外支出': 'NONOPERATING_EXPENSE',
    '利润总额': 'PROFIT_TOTAL', '所得税费用': 'INCOME_TAX_EXPENSE',
    '所得税': 'INCOME_TAX_EXPENSE', '净利润': 'NP',
    '归属于母公司所有者的净利润': 'NP_PARENT',
    '归属于母公司股东的净利润': 'NP_PARENT',
    '归属于上市公司股东的净利润': 'NP_PARENT',
    '归属于挂牌公司股东的净利润': 'NP_PARENT',
    '归属母公司净利润': 'NP_PARENT', '归属于母公司净利润': 'NP_PARENT', '归母净利润': 'NP_PARENT',
    '未扣非归母净利润': 'NP_PARENT',
    '归属于母公司普通股股东的净利润': 'NP_PARENT_ORDINARY',
    '归属于公司普通股股东的净利润': 'NP_PARENT_ORDINARY',
    '归属于上市公司普通股股东的净利润': 'NP_PARENT_ORDINARY',
    '归属于普通股股东的净利润': 'NP_PARENT_ORDINARY',
    '归属于挂牌公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '归属于上市公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '归属于母公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '扣除非经常性损益后的净利润': 'NP_PARENT_ADJ', '扣非净利润': 'NP_PARENT_ADJ',
    '扣非归母净利润': 'NP_PARENT_ADJ', '扣非后的归母净利润': 'NP_PARENT_ADJ',
    '少数股东损益': 'NP_MINORITY',
    '经营活动产生的现金流量净额': 'CFO', '经营活动现金流': 'CFO', '经营现金流': 'CFO',
    '投资活动产生的现金流量净额': 'CFI', '投资活动现金流': 'CFI',
    '筹资活动产生的现金流量净额': 'CFF', '筹资活动现金流': 'CFF',
    '购建固定资产、无形资产和其他长期资产支付的现金': 'CF_CAPEX',
    '经营活动现金流入小计': 'CFO_INFLOW', '经营活动现金流出小计': 'CFO_OUTFLOW',
    '投资活动现金流入小计': 'CFI_INFLOW', '投资活动现金流出小计': 'CFI_OUTFLOW',
    '筹资活动现金流入小计': 'CFF_INFLOW', '筹资活动现金流出小计': 'CFF_OUTFLOW',
    '汇率变动对现金及现金等价物的影响': 'FX_CASH_EFFECT',
    '采购金额': 'PURCHASE_AMOUNT',
}

UNDEFINED_ACCOUNTS = frozenset({'ASSET_CASH_UNSPECIFIED', 'INVESTMENTS_UNSPECIFIED'})
PER_SHARE_LABELS = {'基本每股收益': 'EPS', '稀释每股收益': 'EPS_DILUTED'}
PER_SHARE_ACCOUNTS = frozenset(PER_SHARE_LABELS.values())
SHARE_LABELS = {'发行在外普通股的加权平均数': 'WEIGHTED_SHARES',
                '发行在外普通股加权平均数': 'WEIGHTED_SHARES',
                '发行在外普通股加权平均股数': 'WEIGHTED_SHARES',
                '加权平均发行在外普通股股数': 'WEIGHTED_SHARES'}
SHARE_ACCOUNTS=frozenset({'WEIGHTED_SHARES','END_SHARES'})
BRIDGE_ACCOUNT_NAMES={
    'CF_BRIDGE_CREDIT_IMPAIRMENT':'现金流补充资料信用减值损失',
    'CF_BRIDGE_ASSET_IMPAIRMENT':'现金流补充资料资产减值准备',
    'CF_BRIDGE_DEPRECIATION':'现金流补充资料固定资产折旧',
    'CF_BRIDGE_ROU_DEPRECIATION':'现金流补充资料使用权资产折旧',
    'CF_BRIDGE_INTANGIBLE_AMORTIZATION':'现金流补充资料无形资产摊销',
    'CF_BRIDGE_PREPAID_AMORTIZATION':'现金流补充资料长期待摊费用摊销',
    'CF_BRIDGE_DISPOSAL_LOSS':'现金流补充资料长期资产处置损失',
    'CF_BRIDGE_SCRAP_LOSS':'现金流补充资料固定资产报废损失',
    'CF_BRIDGE_FINANCE':'现金流补充资料财务费用调整',
    'CF_BRIDGE_DTA_CHANGE':'现金流补充资料递延所得税资产减少',
    'CF_BRIDGE_DTL_CHANGE':'现金流补充资料递延所得税负债增加',
    'CF_BRIDGE_INV_CHANGE':'现金流补充资料存货减少',
    'CF_BRIDGE_REC_CHANGE':'现金流补充资料经营性应收减少',
    'CF_BRIDGE_PAY_CHANGE':'现金流补充资料经营性应付增加',
    'CF_BRIDGE_OTHER':'现金流补充资料其他调整',
    'CASH_BALANCE':'现金期末余额', 'CASH_BALANCE_OPENING':'现金期初余额',
}
AMOUNT_ACCOUNTS = frozenset(FINANCIAL_LABELS.values()) | frozenset({
    'NRI_TOTAL', 'NRI_TAX_EFFECT', 'NRI_MINORITY', 'NRI_MINORITY_RAW', 'NONRECURRING_NET'}) | frozenset(BRIDGE_ACCOUNT_NAMES)
ACCOUNT_NAMES = {code: label for label, code in reversed(list(FINANCIAL_LABELS.items()))}
ACCOUNT_NAMES.update({code: label for label, code in reversed(list(SHARE_LABELS.items()))})
ACCOUNT_NAMES.update(BRIDGE_ACCOUNT_NAMES)
ACCOUNT_NAMES['END_SHARES']='期末普通股股数'


def exact_row_label(text: str) -> tuple[str, str] | None:
    """Identify an entire caption after numbering/units, never a rate suffix."""
    compact = re.sub(r'\s+', '', text)
    compact = re.sub(r'^(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十\d]+[）)]|\d+[.．])', '', compact)
    compact = re.sub(r'^(?:其中|加|减)[:：]', '', compact)
    compact = re.sub(r'[（(](?:百万元|亿元|万元|元)[）)]$', '', compact)
    code = FINANCIAL_LABELS.get(compact)
    return (compact, code) if code else None
