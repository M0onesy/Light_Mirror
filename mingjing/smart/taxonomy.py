"""科目与指标词典：财报行标签 → 科目；研报用语 → 指标；指标 → 候选定义。

设计原则
1. 法定科目一名一义；多义用语（净利润/净利率/ROE/EPS/现金等）登记有限候选定义集合，
   核查时逐一计算并披露命中的定义，绝不按金额自由拼公式。
2. 定义集合分“封闭/开放”：封闭集合全部不命中才判有误；开放集合（EBITDA、ROIC 等数据商口径）
   不命中只记“定义缺口”，避免把口径差异误报为错误。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..taxonomy import ACCOUNT_NAMES as _BASE_NAMES, FINANCIAL_LABELS as _BASE_LABELS

# ───────────────────────── 财报行标签 → 科目 ─────────────────────────
FILING_LABELS: dict[str, str] = dict(_BASE_LABELS)
FILING_LABELS.update({
    '营业总收入': 'TOTAL_REVENUE', '营业收入': 'REVENUE', '主营业务收入': 'MAIN_REVENUE',
    '归属于母公司所有者的净利润': 'NP_PARENT', '归属于母公司股东的净利润': 'NP_PARENT',
    '归属于上市公司股东的净利润': 'NP_PARENT', '归属于挂牌公司股东的净利润': 'NP_PARENT',
    '归属于公司股东的净利润': 'NP_PARENT', '归属于本公司股东的净利润': 'NP_PARENT',
    '归属于母公司所有者的净利润或净亏损': 'NP_PARENT',
    '归属于上市公司股东的扣除非经常性损益的净利润': 'NP_PARENT_ADJ',
    '归属于上市公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '归属于挂牌公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '归属于挂牌公司股东的扣除非经常性损益的净利润': 'NP_PARENT_ADJ',
    '归属于母公司股东的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '归属于母公司所有者的扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '扣除非经常性损益后归属于母公司股东的净利润': 'NP_PARENT_ADJ',
    '扣除非经常性损益后的归属于上市公司股东的净利润': 'NP_PARENT_ADJ',
    '扣除非经常性损益后的净利润': 'NP_PARENT_ADJ',
    '基本每股收益': 'EPS', '稀释每股收益': 'EPS_DILUTED', '扣除非经常性损益后的基本每股收益': 'EPS_ADJ',
    '加权平均净资产收益率': 'ROE', '扣除非经常性损益后的加权平均净资产收益率': 'ROE_ADJ',
    '毛利率': 'GROSS_MARGIN', '资产负债率': 'DEBT_RATIO', '流动比率': 'CURRENT_RATIO', '速动比率': 'QUICK_RATIO',
    '利息保障倍数': 'INTEREST_COVERAGE', '应收账款周转率': 'AR_TURNOVER', '存货周转率': 'INVENTORY_TURNOVER',
    '总资产周转率': 'ASSET_TURNOVER', '总资产增长率': 'ASSETS_GROWTH', '营业收入增长率': 'REVENUE_GROWTH',
    '净利润增长率': 'NP_GROWTH', '归属于上市公司股东的每股净资产': 'BPS', '归属于挂牌公司股东的每股净资产': 'BPS',
    '每股净资产': 'BPS', '每股经营活动产生的现金流量净额': 'CFO_PER_SHARE',
    '总资产': 'ASSETS', '资产总额': 'ASSETS', '负债总额': 'LIABILITIES', '净资产': 'EQUITY',
    '归属于上市公司股东的所有者权益': 'EQUITY_PARENT', '归属于母公司所有者权益': 'EQUITY_PARENT',
    '归属于母公司股东权益合计': 'EQUITY_PARENT', '归属于母公司所有者的权益': 'EQUITY_PARENT',
    '所有者权益': 'EQUITY', '股东权益': 'EQUITY', '所有者权益总计': 'EQUITY', '股东权益总计': 'EQUITY',
    '负债和所有者权益总计': 'LIABILITIES_EQUITY', '负债和股东权益总计': 'LIABILITIES_EQUITY',
    '负债及所有者权益总计': 'LIABILITIES_EQUITY', '负债及股东权益总计': 'LIABILITIES_EQUITY',
    '实收资本': 'SHARE_CAPITAL', '实收资本或股本': 'SHARE_CAPITAL', '股本': 'SHARE_CAPITAL',
    '期末总股本': 'TOTAL_SHARES', '总股本': 'TOTAL_SHARES', '普通股总股本': 'TOTAL_SHARES', '股份总数': 'TOTAL_SHARES',
    '交易性金融资产': 'TRADING_FIN_ASSETS', '应收款项融资': 'RECEIVABLES_FINANCING', '债权投资': 'DEBT_INVESTMENTS',
    '其他权益工具投资': 'OTHER_EQUITY_INVESTMENTS', '其他非流动金融资产': 'OTHER_NONCURRENT_FIN_ASSETS',
    '投资性房地产': 'INVESTMENT_PROPERTY', '开发支出': 'DEVELOPMENT_EXPENDITURE', '应付债券': 'BONDS_PAYABLE',
    '预收款项': 'ADVANCE_RECEIPTS', '预计负债': 'PROVISIONS', '库存股': 'TREASURY_STOCK', '其他综合收益': 'OCI',
    '专项储备': 'SPECIAL_RESERVE', '一般风险准备': 'GENERAL_RISK_RESERVE',
    '经营活动产生的现金流量净额': 'CFO', '投资活动产生的现金流量净额': 'CFI', '筹资活动产生的现金流量净额': 'CFF',
    '销售商品、提供劳务收到的现金': 'CF_SALES_CASH', '购买商品、接受劳务支付的现金': 'CF_PURCHASE_CASH',
    '支付给职工以及为职工支付的现金': 'CF_PAYROLL_CASH', '支付的各项税费': 'CF_TAX_CASH',
    '购建固定资产、无形资产和其他长期资产支付的现金': 'CF_CAPEX',
    '吸收投资收到的现金': 'CF_EQUITY_RAISED', '取得借款收到的现金': 'CF_BORROWING', '偿还债务支付的现金': 'CF_REPAYMENT',
    '分配股利、利润或偿付利息支付的现金': 'CF_DIVIDEND_INTEREST',
    '现金及现金等价物净增加额': 'CASH_EQUIVALENTS_CHANGE', '期末现金及现金等价物余额': 'CASH_EQUIVALENTS',
    '期初现金及现金等价物余额': 'CASH_EQUIVALENTS_OPENING',
    '非经常性损益合计': 'NRI_TOTAL', '非经常性损益净额': 'NONRECURRING_NET', '所得税影响数': 'NRI_TAX_EFFECT',
    '归属于母公司所有者权益的非经常性损益净额': 'NONRECURRING_NET',
    '归属于母公司所有者的非经常性损益净额': 'NONRECURRING_NET',
    '所得税影响额': 'NRI_TAX_EFFECT', '少数股东权益影响额': 'NRI_MINORITY', '少数股东权益影响额税后': 'NRI_MINORITY',
    '计入当期损益的政府补助': 'NRI_GOV_GRANTS', '研发投入': 'RD_INVESTMENT', '研发投入金额': 'RD_INVESTMENT',
    '研发投入合计': 'RD_INVESTMENT', '研发支出合计': 'RD_INVESTMENT',
    '现金': 'CASH_BALANCE', '现金的期末余额': 'CASH_BALANCE', '现金等价物的期末余额': 'CASH_EQUIVALENT_PART',
    '利息收入': 'INTEREST_INCOME', '利息费用': 'EXP_INTEREST', '其他业务收入': 'OTHER_BUSINESS_REVENUE',
    '综合收益总额': 'TOTAL_COMPREHENSIVE_INCOME', '持续经营净利润': 'NP_CONTINUING',
})
# 长标签前缀（标签以这些文本开头即归入该科目）
FILING_PREFIXES: list[tuple[str, str]] = [
    ('固定资产折旧', 'CF_BRIDGE_DEPRECIATION'), ('使用权资产折旧', 'CF_BRIDGE_ROU_DEPRECIATION'),
    ('使用权资产摊销', 'CF_BRIDGE_ROU_DEPRECIATION'), ('无形资产摊销', 'CF_BRIDGE_INTANGIBLE_AMORTIZATION'),
    ('长期待摊费用摊销', 'CF_BRIDGE_PREPAID_AMORTIZATION'),
    ('购建固定资产、无形资产和其他长期资产', 'CF_CAPEX'), ('销售商品、提供劳务收到', 'CF_SALES_CASH'),
    ('归属于母公司所有者的净利润', 'NP_PARENT'), ('归属于母公司股东的净利润', 'NP_PARENT'),
    ('加权平均净资产收益率', 'ROE'), ('归属于上市公司股东的扣除非经常性损益', 'NP_PARENT_ADJ'),
    ('归属于挂牌公司股东的扣除非经常性损益', 'NP_PARENT_ADJ'), ('归属于母公司所有者权益', 'EQUITY_PARENT'),
    ('所有者权益（或股东权益）合计', 'EQUITY'), ('所有者权益(或股东权益)合计', 'EQUITY'),
    ('负债和所有者权益', 'LIABILITIES_EQUITY'),
]
CF_SUPPLEMENT_LABELS = {'财务费用': 'CF_BRIDGE_FINANCE', '信用减值损失': 'CF_BRIDGE_CREDIT_IMPAIRMENT',
                        '资产减值准备': 'CF_BRIDGE_ASSET_IMPAIRMENT', '资产减值损失': 'CF_BRIDGE_ASSET_IMPAIRMENT',
                        '投资损失': 'CF_BRIDGE_INVESTMENT_LOSS', '存货的减少': 'CF_BRIDGE_INV_CHANGE',
                        '经营性应收项目的减少': 'CF_BRIDGE_REC_CHANGE', '经营性应付项目的增加': 'CF_BRIDGE_PAY_CHANGE',
                        '递延所得税资产减少': 'CF_BRIDGE_DTA_CHANGE', '递延所得税负债增加': 'CF_BRIDGE_DTL_CHANGE'}

PERCENT_ACCOUNTS = frozenset({'GROSS_MARGIN', 'ROE', 'ROE_ADJ', 'DEBT_RATIO', 'ASSETS_GROWTH', 'REVENUE_GROWTH', 'NP_GROWTH',
                              'NET_MARGIN', 'RD_RATIO'})
PLAIN_ACCOUNTS = frozenset({'EPS', 'EPS_DILUTED', 'EPS_ADJ', 'BPS', 'CFO_PER_SHARE', 'CURRENT_RATIO', 'QUICK_RATIO',
                            'INTEREST_COVERAGE', 'AR_TURNOVER', 'INVENTORY_TURNOVER', 'ASSET_TURNOVER', 'TOTAL_SHARES'})
FLOW_ACCOUNTS = frozenset({'REVENUE', 'TOTAL_REVENUE', 'MAIN_REVENUE', 'COST', 'TOTAL_COST', 'TAX_SURCHARGES', 'EXP_SELLING', 'EXP_ADMIN',
                           'EXP_RD', 'EXP_FINANCE', 'OTHER_INCOME', 'INVESTMENT_INCOME', 'OPERATING_PROFIT', 'PROFIT_TOTAL',
                           'INCOME_TAX_EXPENSE', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'NP_MINORITY', 'CFO', 'CFI', 'CFF',
                           'CF_SALES_CASH', 'CF_CAPEX', 'NONRECURRING_NET', 'GROSS_PROFIT'})
ADJUSTED_ACCOUNTS = frozenset({'NP_PARENT_ADJ', 'ROE_ADJ', 'EPS_ADJ'})
STOCK_ACCOUNTS_HINT = ('ASSETS', 'LIABILITIES', 'EQUITY', 'CASH_FUNDS', 'AR', 'INVENTORY', 'BORROWING', 'PAYABLE', 'RECEIVABLE',
                       'CAPITAL', 'RESERVE', 'PPE', 'INTANGIBLES', 'GOODWILL', 'SHARES', 'BPS', 'DEBT_RATIO', 'CURRENT_RATIO')

ACCOUNT_NAMES: dict[str, str] = dict(_BASE_NAMES)
for _label, _code in FILING_LABELS.items():
    ACCOUNT_NAMES.setdefault(_code, _label)
ACCOUNT_NAMES.update({
    'REVENUE': '营业收入', 'TOTAL_REVENUE': '营业总收入', 'COST': '营业成本', 'NP': '净利润', 'NP_PARENT': '归母净利润',
    'NP_PARENT_ADJ': '扣非归母净利润', 'CFO': '经营活动现金流量净额', 'CFI': '投资活动现金流量净额', 'CFF': '筹资活动现金流量净额',
    'ASSETS': '资产总计', 'LIABILITIES': '负债合计', 'EQUITY': '所有者权益合计', 'EQUITY_PARENT': '归母所有者权益',
    'EPS': '基本每股收益', 'EPS_DILUTED': '稀释每股收益', 'ROE': '加权平均净资产收益率', 'GROSS_MARGIN': '毛利率',
    'NET_MARGIN': '净利率', 'DEBT_RATIO': '资产负债率', 'BPS': '每股净资产', 'TOTAL_SHARES': '总股本',
    'EXP_SELLING': '销售费用', 'EXP_ADMIN': '管理费用', 'EXP_RD': '研发费用', 'EXP_FINANCE': '财务费用',
    'CASH_FUNDS': '货币资金', 'SHARE_CAPITAL': '股本', 'CF_CAPEX': '购建固定资产、无形资产和其他长期资产支付的现金',
    'PE': '市盈率', 'PB': '市净率', 'PS': '市销率', 'MARKET_CAP': '总市值', 'PRICE': '股价', 'EBITDA': 'EBITDA',
    'ROIC': 'ROIC', 'DA': '折旧摊销', 'PERIOD_EXPENSE_RATIO': '期间费用率', 'CASH': '现金', 'RETAINED': '留存收益',
    'NRI': '非经常性损益', 'RD_RATIO': '研发费用率', 'SELLING_RATIO': '销售费用率', 'ADMIN_RATIO': '管理费用率',
    'FINANCE_RATIO': '财务费用率', 'ROA': '总资产收益率', 'CASH_CONTENT': '净现比', 'TAX_RATE': '有效税率',
    'NET_DEBT_RATIO': '净负债比率', 'EV_EBITDA': 'EV/EBITDA', 'AP_TURNOVER': '应付账款周转率',
    'OPERATING_MARGIN': '营业利润率', 'CAPEX': '资本支出', 'AR_NOTES': '应收票据及应收账款', 'AP_NOTES': '应付票据及应付账款',
})


def account_name(code: str) -> str:
    return ACCOUNT_NAMES.get(code, code)


_PREFIX_NUM = re.compile(r'^(?:[一二三四五六七八九十]+[、.．]|[(（][一二三四五六七八九十\d]+[)）]|\d+[.．、](?!\d)|[①-⑩])')
_PREFIX_WORD = re.compile(r'^(?:其中|加|减|加上|减去)[:：]')
_REMARK = re.compile(r'[(（][^()（）]{0,40}?(?:亏损|损失|填列|号填|净额以|以“|以"|元/股|元／股|如适用|或股东权益|或股本|税后|含)[^()（）]{0,30}[)）]')
_UNIT_NOTE = re.compile(r'[(（](?:人民币)?(?:元|千元|万元|百万元|亿元|%|％|倍|次|股)[)）]')


def clean_label(label: str) -> tuple[str, dict]:
    """去掉序号、“其中/加/减”、填列说明与单位批注；返回(净标签, 限定信息)。"""
    text = re.sub(r'\s+', '', label)
    info: dict = {}
    for _ in range(3):
        text = _PREFIX_NUM.sub('', text)
        text = _PREFIX_WORD.sub('', text)
    scope = re.search(r'[(（](合并|母公司)[)）]', text)
    if scope:
        info['scope'] = scope[1]
        text = text.replace(scope[0], '')
    if re.search(r'扣除非经常性损益后|扣非后', text) and not text.startswith('归属'):
        info['adjusted_hint'] = True
    basis = re.search(r'[(（]依?据?[^()（）]*?(扣除非经常性损益后的?)?净利润计算[)）]', text)
    if basis:
        info['adjusted'] = bool(basis[1])
        text = text.replace(basis[0], '')
    unit = _UNIT_NOTE.search(text)
    if unit:
        info['unit'] = unit[0].strip('()（）').replace('人民币', '').replace('％', '%')
        text = text.replace(unit[0], '')
    text = _REMARK.sub('', text)
    if text.endswith(('%', '％')):
        info['unit'] = '%'
        text = text[:-1]
    text = text.rstrip(':：')
    return text, info


def filing_account(label: str, *, section: str = '') -> tuple[str, dict] | None:
    text, info = clean_label(label)
    if not text:
        return None
    if section == 'CF_SUPP' and text in CF_SUPPLEMENT_LABELS:
        return CF_SUPPLEMENT_LABELS[text], info
    code = FILING_LABELS.get(text)
    if code is None:
        code = next((account for prefix, account in FILING_PREFIXES if text.startswith(prefix)), None)
    if code is None:
        return None
    if code == 'ROE' and info.get('adjusted'):
        code = 'ROE_ADJ'
    return code, info


# ───────────────────────── 研报用语 → 指标 ─────────────────────────
# (别名, 指标代码)。长别名优先匹配；英文缩写要求独立成词。
REPORT_ALIASES: list[tuple[str, str]] = [
    ('归属于母公司所有者的净利润', 'NP_PARENT'), ('归属于上市公司股东的净利润', 'NP_PARENT'), ('归属于母公司净利润', 'NP_PARENT'),
    ('归属母公司净利润', 'NP_PARENT'), ('归母净利润', 'NP_PARENT'), ('归母净利', 'NP_PARENT'), ('归母利润', 'NP_PARENT'),
    ('扣非后归母净利润', 'NP_PARENT_ADJ'), ('扣非归母净利润', 'NP_PARENT_ADJ'), ('扣非净利润', 'NP_PARENT_ADJ'),
    ('扣非后净利润', 'NP_PARENT_ADJ'), ('扣非归母', 'NP_PARENT_ADJ'), ('扣除非经常性损益后的净利润', 'NP_PARENT_ADJ'),
    ('营业总收入', 'TOTAL_REVENUE'), ('营业收入', 'REVENUE'), ('主营业务收入', 'REVENUE'), ('营收', 'REVENUE'),
    ('总收入', 'REVENUE'), ('收入', 'REVENUE'),
    ('营业成本', 'COST'), ('营业利润', 'OPERATING_PROFIT'), ('利润总额', 'PROFIT_TOTAL'), ('净利润', 'NP'),
    ('少数股东损益', 'NP_MINORITY'), ('所得税', 'INCOME_TAX_EXPENSE'),
    ('销售费用率', 'SELLING_RATIO'), ('管理费用率', 'ADMIN_RATIO'), ('研发费用率', 'RD_RATIO'), ('财务费用率', 'FINANCE_RATIO'),
    ('期间费用率', 'PERIOD_EXPENSE_RATIO'), ('销售费用', 'EXP_SELLING'), ('营业费用', 'EXP_SELLING'), ('管理费用', 'EXP_ADMIN'),
    ('研发费用', 'EXP_RD'), ('研发投入', 'RD_INVESTMENT'), ('财务费用', 'EXP_FINANCE'), ('营业税金及附加', 'TAX_SURCHARGES'),
    ('税金及附加', 'TAX_SURCHARGES'), ('资产减值损失', 'IMPAIRMENT'), ('信用减值损失', 'CREDIT_IMPAIRMENT'),
    ('其他收益', 'OTHER_INCOME'), ('公允价值变动收益', 'FAIR_VALUE_INCOME'), ('投资净收益', 'INVESTMENT_INCOME'),
    ('投资收益', 'INVESTMENT_INCOME'), ('资产处置收益', 'ASSET_DISPOSAL_INCOME'), ('营业外收入', 'NONOPERATING_INCOME'),
    ('营业外支出', 'NONOPERATING_EXPENSE'),
    ('综合毛利率', 'GROSS_MARGIN'), ('销售毛利率', 'GROSS_MARGIN'), ('毛利率', 'GROSS_MARGIN'), ('毛利', 'GROSS_PROFIT'),
    ('归母净利率', 'NP_PARENT_MARGIN'), ('销售净利率', 'NET_MARGIN'), ('净利润率', 'NET_MARGIN'), ('净利率', 'NET_MARGIN'),
    ('营业利润率', 'OPERATING_MARGIN'),
    ('加权平均净资产收益率', 'ROE_WEIGHTED'), ('加权ROE', 'ROE_WEIGHTED'), ('净资产收益率', 'ROE'), ('ROE', 'ROE'),
    ('总资产收益率', 'ROA'), ('ROA', 'ROA'), ('ROIC', 'ROIC'), ('EBITDA', 'EBITDA'), ('EBIT', 'EBIT'),
    ('资产负债率', 'DEBT_RATIO'), ('净负债比率', 'NET_DEBT_RATIO'), ('净负债率', 'NET_DEBT_RATIO'), ('流动比率', 'CURRENT_RATIO'),
    ('速动比率', 'QUICK_RATIO'), ('总资产周转率', 'ASSET_TURNOVER'), ('应收账款周转率', 'AR_TURNOVER'),
    ('应付账款周转率', 'AP_TURNOVER'), ('存货周转率', 'INVENTORY_TURNOVER'),
    ('每股经营现金流', 'CFO_PER_SHARE'), ('每股净资产', 'BPS'), ('基本每股收益', 'EPS_BASIC'), ('稀释每股收益', 'EPS_DILUTED'),
    ('每股收益', 'EPS'), ('EPS', 'EPS'), ('BPS', 'BPS'),
    ('经营活动产生的现金流量净额', 'CFO'), ('经营活动现金流', 'CFO'), ('经营性现金流', 'CFO'), ('经营现金流', 'CFO'),
    ('投资活动现金流', 'CFI'), ('筹资活动现金流', 'CFF'), ('现金净增加额', 'CASH_EQUIVALENTS_CHANGE'),
    ('净现比', 'CASH_CONTENT'), ('盈利现金比率', 'CASH_CONTENT'), ('现金含量', 'CASH_CONTENT'),
    ('资本支出', 'CAPEX'), ('资本开支', 'CAPEX'), ('折旧摊销', 'DA'),
    ('货币资金', 'CASH_FUNDS'), ('应收票据及应收账款', 'AR_NOTES'), ('应收账款及应收票据', 'AR_NOTES'), ('应收账款', 'AR'),
    ('应收票据', 'NOTES_RECEIVABLE'), ('其他应收款', 'OTHER_RECEIVABLES'), ('预付账款', 'PREPAYMENTS'), ('预付款项', 'PREPAYMENTS'),
    ('存货', 'INVENTORY'), ('合同资产', 'CONTRACT_ASSETS'), ('流动资产', 'CURRENT_ASSETS'), ('非流动资产', 'NONCURRENT_ASSETS'),
    ('长期股权投资', 'LONG_TERM_EQUITY_INVESTMENTS'), ('固定资产', 'PPE'), ('在建工程', 'CONSTRUCTION_IN_PROGRESS'),
    ('无形资产', 'INTANGIBLES'), ('商誉', 'GOODWILL'), ('资产总计', 'ASSETS'), ('总资产', 'ASSETS'), ('资产总额', 'ASSETS'),
    ('短期借款', 'SHORT_TERM_BORROWING'), ('长期借款', 'LONG_TERM_BORROWING'), ('应付票据及应付账款', 'AP_NOTES'),
    ('应付账款', 'AP'), ('合同负债', 'CONTRACT_LIABILITIES'), ('流动负债', 'CURRENT_LIABILITIES'),
    ('非流动负债', 'NONCURRENT_LIABILITIES'), ('负债合计', 'LIABILITIES'), ('负债总额', 'LIABILITIES'), ('总负债', 'LIABILITIES'),
    ('少数股东权益', 'EQUITY_MINORITY'), ('资本公积', 'CAPITAL_RESERVE'), ('留存收益', 'RETAINED'), ('未分配利润', 'RETAINED_UNDISTRIBUTED_PROFIT'),
    ('归属母公司股东权益', 'EQUITY_PARENT'), ('归属于母公司股东权益', 'EQUITY_PARENT'), ('归母净资产', 'EQUITY_PARENT'),
    ('归母权益', 'EQUITY_PARENT'), ('净资产', 'EQUITY_PARENT'), ('负债和股东权益', 'LIABILITIES_EQUITY'), ('股本', 'SHARE_CAPITAL'),
    ('总股本', 'TOTAL_SHARES'), ('非经常性损益', 'NRI'),
    ('P/E', 'PE'), ('PE', 'PE'), ('市盈率', 'PE'), ('P/B', 'PB'), ('PB', 'PB'), ('市净率', 'PB'), ('P/S', 'PS'), ('PS', 'PS'),
    ('市销率', 'PS'), ('EV/EBITDA', 'EV_EBITDA'), ('总市值', 'MARKET_CAP'), ('市值', 'MARKET_CAP'),
    ('当前股价', 'PRICE'), ('收盘价', 'PRICE'), ('目标价', 'TARGET_PRICE'),
]
# 研报三表模板里的简称（仅在对应表内生效）
TEMPLATE_ALIASES = {
    'BS': {'现金': 'CASH', '长期投资': 'LONG_TERM_INVEST', '其他流动资产': 'RESIDUAL', '其他非流动资产': 'RESIDUAL',
           '其他流动负债': 'RESIDUAL', '其他非流动负债': 'RESIDUAL'},
    'IS': {'EPS(元)': 'EPS', '所得税': 'INCOME_TAX_EXPENSE', '归属母公司净利润': 'NP_PARENT'},
    'CF': {'净利润': 'NP', '财务费用': 'EXP_FINANCE', '短期借款': 'DELTA_SHORT_BORROWING', '长期借款': 'DELTA_LONG_BORROWING',
           '普通股增加': 'DELTA_SHARE_CAPITAL', '资本公积增加': 'DELTA_CAPITAL_RESERVE', '长期投资': 'TEMPLATE_ITEM',
           '投资损失': 'TEMPLATE_ITEM', '营运资金变动': 'TEMPLATE_ITEM', '其他经营现金流': 'RESIDUAL',
           '其他投资现金流': 'RESIDUAL', '其他筹资现金流': 'RESIDUAL', '资本支出': 'CAPEX', '折旧摊销': 'DA'},
    'RATIO': {'营业收入(%)': ('REVENUE', 'yoy'), '营业利润(%)': ('OPERATING_PROFIT', 'yoy'),
              '归属于母公司净利润(%)': ('NP_PARENT', 'yoy'), '归属母公司净利润(%)': ('NP_PARENT', 'yoy'),
              '归母净利润(%)': ('NP_PARENT', 'yoy'), '净利润(%)': ('NP', 'yoy'), 'YOY(%)': ('__PREV__', 'yoy'),
              'yoy(%)': ('__PREV__', 'yoy'), '同比(%)': ('__PREV__', 'yoy'), '增长率(%)': ('__PREV__', 'yoy'),
              '每股收益(最新摊薄)': 'EPS_LATEST', '每股经营现金流(最新摊薄)': 'CFO_PER_SHARE', '每股净资产(最新摊薄)': 'BPS',
              'EPS(摊薄/元)': 'EPS_LATEST', 'EPS(摊薄)': 'EPS_LATEST'},
}
_ALIAS_SORTED = sorted(REPORT_ALIASES, key=lambda item: -len(item[0]))


def _alias_pattern(alias: str) -> str:
    escaped = re.escape(alias)
    if re.fullmatch(r'[A-Za-z/]+', alias):
        return r'(?<![A-Za-z])' + escaped + r'(?![A-Za-z])'
    return escaped


ALIAS_RE = re.compile('|'.join(_alias_pattern(alias) for alias, _ in _ALIAS_SORTED))
ALIAS_MAP = {alias: code for alias, code in REPORT_ALIASES}


def metric_from_label(label: str, table: str = '') -> tuple[str, str, str] | None:
    """研报表格行标签 → (指标, 关系, 单位批注)。"""
    text = re.sub(r'\s+', '', label)
    unit = ''
    note = re.search(r'[(（]([^()（）]{1,8})[)）]$', text)
    if note and re.fullmatch(r'(?:人民币)?(?:元|千元|万元|百万元|亿元|%|％|倍|次|x|X|元/股)', note[1]):
        unit = note[1].replace('人民币', '').replace('％', '%')
    template = TEMPLATE_ALIASES.get(table, {})
    for key in (text, text.replace('％', '%')):
        hit = template.get(key) or TEMPLATE_ALIASES['RATIO'].get(key)
        if hit:
            return (hit[0], hit[1], unit or '%') if isinstance(hit, tuple) else (hit, 'value', unit)
    base = text[:note.start()] if note else text
    hit = template.get(base)
    if hit and not isinstance(hit, tuple):
        return hit, 'value', unit
    if re.fullmatch(r'(?:同比|YOY|yoy|YoY|增长率|同比增速|同比增长|同比增长率|同比增长率\(%\)|增长率\(%\)|增速|增速\(%\)|同比\(%\))', base):
        return '__PREV__', 'yoy', unit or '%'
    growth = re.fullmatch(r'(.+?)(?:同比增长率|增长率|同比增速|同比|增速|YOY|yoy)', base)
    if growth and growth[1] in ALIAS_MAP:
        return ALIAS_MAP[growth[1]], 'yoy', unit or '%'
    if base in ALIAS_MAP:
        return ALIAS_MAP[base], 'value', unit
    return None
