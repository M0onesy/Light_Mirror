"""生成直传模式的虚构演示材料（公司、数据、券商均为虚构，可自由分发）。

产物（samples/smart/）：
  星河智造_2022年年度报告.pdf、星河智造_2023年年度报告.pdf   两份虚构年报（主要指标、分季度、分产品、三大报表、补充资料）
  虚构证券_2024-04-25_星河智造深度报告.pdf                     含预埋错误的研报草稿
  seeded_errors.json                                          预埋错误清单（金标），供回归测试对照
依赖 reportlab 与一款中文 TrueType 字体；仅用于生成样例，运行明镜本身不需要。
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal as D
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = Path(__file__).resolve().parents[1] / 'samples' / 'smart'
FONTS = ['/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc', 'C:/Windows/Fonts/simhei.ttf', 'C:/Windows/Fonts/msyh.ttc', '/System/Library/Fonts/STHeiti Light.ttc']
font = next((f for f in FONTS if Path(f).is_file()), None)
if font is None:
    sys.exit('未找到中文字体，请在 FONTS 中补充路径')
pdfmetrics.registerFont(TTFont('CJK', font, subfontIndex=0))
BODY = ParagraphStyle('body', fontName='CJK', fontSize=10.5, leading=17, firstLineIndent=21)
PLAIN = ParagraphStyle('plain', fontName='CJK', fontSize=10.5, leading=17)
H1 = ParagraphStyle('h1', fontName='CJK', fontSize=20, leading=30, alignment=1, spaceAfter=12)
H2 = ParagraphStyle('h2', fontName='CJK', fontSize=13, leading=22, spaceBefore=10, spaceAfter=6)
H3 = ParagraphStyle('h3', fontName='CJK', fontSize=11, leading=18, spaceBefore=6, spaceAfter=4)
SMALL = ParagraphStyle('small', fontName='CJK', fontSize=9, leading=14, alignment=2)


def money(value) -> str:
    return f'{D(value):,.2f}'


def table(rows, widths, align_from=1, size=8.6):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([('FONTNAME', (0, 0), (-1, -1), 'CJK'), ('FONTSIZE', (0, 0), (-1, -1), size), ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
                           ('ALIGN', (align_from, 0), (-1, -1), 'RIGHT'), ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                           ('BACKGROUND', (0, 0), (-1, 0), colors.Color(.93, .95, .97)), ('TOPPADDING', (0, 0), (-1, -1), 2.5), ('BOTTOMPADDING', (0, 0), (-1, -1), 2.5)]))
    return t


# ───────────── 虚构财务数据（单位：元） ─────────────
def build(year):
    base = {
        2021: dict(rev=880_000_000, cost=596_640_000, tax=5_280_000, sell=52_800_000, adm=44_000_000, rd=61_600_000, fin=7_040_000, oth=9_600_000, inv=1_200_000,
                   cred=-3_100_000, asset=-1_400_000, disp=250_000, nonin=600_000, nonex=900_000, taxexp=14_280_000, minor=3_950_000, nri=12_400_000,
                   cfo=96_500_000, cfi=-118_000_000, cff=41_300_000, fx=-420_000, cash=312_000_000, tfa=20_000_000, notes=31_500_000, ar=205_000_000, prepay=18_600_000,
                   othrec=7_300_000, inventory=176_000_000, othca=12_400_000, lte=15_000_000, ppe=402_000_000, cip=38_000_000, rou=21_000_000, intang=64_000_000,
                   goodwill=12_000_000, ltp=6_500_000, dta=9_800_000, othnca=14_200_000, stb=120_000_000, np_=42_000_000, ap=168_000_000, cl=23_000_000,
                   payroll=28_500_000, taxpay=11_200_000, othpay=16_400_000, cur_ncl=18_000_000, othcl=4_300_000, ltb=96_000_000, lease=15_500_000,
                   defer=12_000_000, dtl=3_600_000, share=400_000_000, capres=186_000_000, surplus=38_000_000, minority=31_000_000, capex=121_000_000,
                   dep=41_200_000, roudep=6_100_000, amort=5_300_000, ltpam=2_100_000),
    }
    grow = {2022: dict(rev=1_052_300_000, cm=D('0.672'), cfo=152_800_000, cfi=-139_500_000, cff=-22_600_000, nri=18_900_000, share=400_000_000),
            2023: dict(rev=1_286_450_000, cm=D('0.655'), cfo=186_300_000, cfi=-162_400_000, cff=18_750_000, nri=11_300_000, share=420_000_000)}
    if year == 2021:
        d = base[2021]
    else:
        p = build(year - 1)
        g = grow[year]
        k = D(g['rev']) / D(p['rev'])
        d = {key: int((D(p[key]) * k).quantize(D('1000'))) for key in base[2021]}
        d.update(rev=g['rev'], cost=int(D(g['rev']) * g['cm']), cfo=g['cfo'], cfi=g['cfi'], cff=g['cff'], nri=g['nri'], share=g['share'],
                 fx=-380_000 if year == 2022 else 265_000, capres=186_000_000 if year == 2022 else 246_000_000, goodwill=12_000_000,
                 stb=150_000_000 if year == 2022 else 138_000_000, ltb=88_000_000 if year == 2022 else 120_000_000)
    for key in d:
        d[key] = D(d[key])
    d['gp'] = d['rev'] - d['cost']
    d['total_cost'] = d['cost'] + d['tax'] + d['sell'] + d['adm'] + d['rd'] + d['fin']
    d['op'] = d['rev'] - d['total_cost'] + d['oth'] + d['inv'] + d['cred'] + d['asset'] + d['disp']
    d['pt'] = d['op'] + d['nonin'] - d['nonex']
    d['np'] = d['pt'] - d['taxexp']
    d['npp'] = d['np'] - d['minor']
    d['adj'] = d['npp'] - d['nri']
    d['ca'] = d['cash'] + d['tfa'] + d['notes'] + d['ar'] + d['prepay'] + d['othrec'] + d['inventory'] + d['othca']
    d['nca'] = d['lte'] + d['ppe'] + d['cip'] + d['rou'] + d['intang'] + d['goodwill'] + d['ltp'] + d['dta'] + d['othnca']
    d['assets'] = d['ca'] + d['nca']
    d['cl_total'] = d['stb'] + d['np_'] + d['ap'] + d['cl'] + d['payroll'] + d['taxpay'] + d['othpay'] + d['cur_ncl'] + d['othcl']
    d['ncl'] = d['ltb'] + d['lease'] + d['defer'] + d['dtl']
    d['liab'] = d['cl_total'] + d['ncl']
    d['equity'] = d['assets'] - d['liab']
    d['eq_parent'] = d['equity'] - d['minority']
    d['undist'] = d['eq_parent'] - d['share'] - d['capres'] - d['surplus']
    d['netcash'] = d['cfo'] + d['cfi'] + d['cff'] + d['fx']
    d['eps'] = (d['npp'] / d['share']).quantize(D('0.01'))
    d['year'] = year
    return d


Y = {year: build(year) for year in (2021, 2022, 2023)}
for year in (2022, 2023):
    Y[year]['roe'] = (Y[year]['npp'] / ((Y[year]['eq_parent'] + Y[year - 1]['eq_parent']) / 2) * 100).quantize(D('0.01'))
Y[2021]['roe'] = D('9.86')
SEG = {2023: [('工业机器人', D('0.56'), D('0.628')), ('智能仓储系统', D('0.30'), D('0.702')), ('软件与服务', D('0.14'), D('0.661'))],
       2022: [('工业机器人', D('0.58'), D('0.650')), ('智能仓储系统', D('0.29'), D('0.712')), ('软件与服务', D('0.13'), D('0.680'))]}
QUARTER = {2023: [D('0.21'), D('0.24'), D('0.26'), D('0.29')], 2022: [D('0.20'), D('0.25'), D('0.26'), D('0.29')]}


def segments(year):
    d, rows, rev_left, cost_left = Y[year], [], Y[year]['rev'], Y[year]['cost']
    for index, (name, share, cost_ratio) in enumerate(SEG[year]):
        last = index == len(SEG[year]) - 1
        rev = rev_left if last else (d['rev'] * share).quantize(D('0.01'))
        cost = cost_left if last else (rev * cost_ratio).quantize(D('0.01'))
        rev_left -= rev; cost_left -= cost
        rows.append((name, rev, cost))
    return rows


def quarters(year, key):
    total, out, left = Y[year][key], [], Y[year][key]
    for index, share in enumerate(QUARTER[year]):
        value = left if index == 3 else (total * share).quantize(D('0.01'))
        left -= value; out.append(value)
    return out


def pct(a, b):
    return f'{((a - b) / abs(b) * 100):.2f}'


def annual_report(year):
    d, p, pp = Y[year], Y[year - 1], Y.get(year - 2)
    story = [Spacer(1, 60 * mm), Paragraph('星河智造股份有限公司', H1), Paragraph(f'{year}年年度报告', H1), Spacer(1, 30 * mm),
             Paragraph('证券代码：999001　　证券简称：星河智造', ParagraphStyle('c', parent=PLAIN, alignment=1)),
             Paragraph(f'披露日期：{year + 1}年3月28日', ParagraphStyle('c2', parent=PLAIN, alignment=1)),
             Paragraph('（本报告为明镜演示用虚构材料，公司与数据均不真实）', ParagraphStyle('c3', parent=PLAIN, alignment=1, textColor=colors.grey)), PageBreak(),
             Paragraph('第一节 重要提示', H2),
             Paragraph('公司董事会、监事会及董事、监事、高级管理人员保证年度报告内容的真实、准确、完整。本报告中的财务报告已经审计，审计机构出具了标准无保留意见的审计报告。', BODY),
             Paragraph('第二节 公司简介和主要财务指标', H2), Paragraph('一、主要会计数据和财务指标', H3), Paragraph('单位：元', SMALL)]
    head = ['主要会计数据', f'{year}年', f'{year - 1}年', '本年比上年增减(%)'] + ([f'{year - 2}年'] if pp else [])
    def row(label, key, as_money=True):
        cells = [label, money(d[key]) if as_money else str(d[key]), money(p[key]) if as_money else str(p[key]), pct(d[key], p[key])]
        return cells + ([money(pp[key]) if as_money else str(pp[key])] if pp else [])
    widths = [62 * mm, 32 * mm, 32 * mm, 26 * mm] + ([30 * mm] if pp else [])
    story.append(table([head, row('营业收入', 'rev'), row('归属于上市公司股东的净利润', 'npp'), row('归属于上市公司股东的扣除非经常性损益的净利润', 'adj'),
                        row('经营活动产生的现金流量净额', 'cfo'), row('基本每股收益（元/股）', 'eps', False), row('稀释每股收益（元/股）', 'eps', False),
                        ['加权平均净资产收益率(%)', str(d['roe']), str(p['roe']), f'{d["roe"] - p["roe"]:.2f}'] + ([str(pp['roe'])] if pp else [])], widths, size=7))
    story.append(Spacer(1, 4 * mm))
    head = ['', f'{year}年末', f'{year - 1}年末', '本年末比上年末增减(%)'] + ([f'{year - 2}年末'] if pp else [])
    story.append(table([head, ['总资产', money(d['assets']), money(p['assets']), pct(d['assets'], p['assets'])] + ([money(pp['assets'])] if pp else []),
                        ['归属于上市公司股东的净资产', money(d['eq_parent']), money(p['eq_parent']), pct(d['eq_parent'], p['eq_parent'])] + ([money(pp['eq_parent'])] if pp else [])], widths, size=7))
    story += [Paragraph('二、分季度主要财务数据', H3), Paragraph('单位：元', SMALL)]
    story.append(table([['', '第一季度', '第二季度', '第三季度', '第四季度']] + [[label] + [money(v) for v in quarters(year, key)] for label, key in
                       (('营业收入', 'rev'), ('归属于上市公司股东的净利润', 'npp'), ('归属于上市公司股东的扣除非经常性损益的净利润', 'adj'), ('经营活动产生的现金流量净额', 'cfo'))],
                       [62 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm]))
    story += [Paragraph('三、非经常性损益项目及金额', H3), Paragraph('单位：元', SMALL)]
    gross = (d['nri'] / D('0.82')).quantize(D('0.01')); taxeff = (gross * D('0.15')).quantize(D('0.01')); mino = gross - taxeff - d['nri']
    story.append(table([['项目', f'{year}年金额'], ['计入当期损益的政府补助', money(gross * D('0.7'))], ['除上述各项之外的其他营业外收入和支出', money(gross * D('0.3'))],
                        ['非经常性损益合计', money(gross)], ['减：所得税影响数', money(taxeff)], ['少数股东权益影响额（税后）', money(mino)], ['非经常性损益净额', money(d['nri'])]], [110 * mm, 50 * mm]))
    story += [PageBreak(), Paragraph('第三节 管理层讨论与分析', H2),
              Paragraph(f'报告期内，公司实现营业收入{d["rev"] / D(1e8):.2f}亿元，同比增长{pct(d["rev"], p["rev"])}%，主要系工业机器人订单交付增加及智能仓储系统项目验收所致；'
                        f'实现归属于上市公司股东的净利润{d["npp"] / D(1e8):.2f}亿元，同比增长{pct(d["npp"], p["npp"])}%。经营活动产生的现金流量净额{d["cfo"] / D(1e8):.2f}亿元，回款情况良好。', BODY),
              Paragraph(f'报告期内，公司前五名客户合计销售金额{d["rev"] * D("0.318") / D(1e4):.2f}万元，占年度销售总额的31.80%；研发投入金额{d["rd"] / D(1e4):.2f}万元，占营业收入的比例为{d["rd"] / d["rev"] * 100:.2f}%。', BODY),
              Paragraph('主营业务分行业、分产品、分地区情况', H3), Paragraph('单位：元', SMALL)]
    prev_seg = {name: rev for name, rev, _ in segments(year - 1)} if year - 1 in SEG else {}
    rows = [['分产品', '营业收入', '营业成本', '毛利率(%)', '营业收入比上年增减(%)']]
    for name, rev, cost in segments(year):
        rows.append([name, money(rev), money(cost), f'{(rev - cost) / rev * 100:.2f}', pct(rev, prev_seg[name]) if name in prev_seg else '-'])
    rows.append(['合计', money(d['rev']), money(d['cost']), f'{d["gp"] / d["rev"] * 100:.2f}', pct(d['rev'], p['rev'])])
    story.append(table(rows, [44 * mm, 36 * mm, 36 * mm, 24 * mm, 34 * mm]))
    story += [Paragraph('会计政策变更：报告期内公司自2023年1月1日起执行《企业会计准则解释第16号》，对单项交易产生的资产和负债相关的递延所得税不适用初始确认豁免，对可比期间财务报表无重大影响。' if year == 2023 else '会计政策变更：不适用。', BODY),
              PageBreak(), Paragraph('第十节 财务报告', H2), Paragraph('二、财务报表', H3), Paragraph('合并资产负债表', H3), Paragraph(f'{year}年12月31日', SMALL), Paragraph('编制单位：星河智造股份有限公司　　单位：元', SMALL)]
    bs = [('流动资产：', None, None), ('货币资金', 'cash', '七、1'), ('交易性金融资产', 'tfa', '七、2'), ('应收票据', 'notes', '七、3'), ('应收账款', 'ar', '七、4'), ('预付款项', 'prepay', '七、5'),
          ('其他应收款', 'othrec', '七、6'), ('存货', 'inventory', '七、7'), ('其他流动资产', 'othca', '七、8'), ('流动资产合计', 'ca', ''), ('非流动资产：', None, None),
          ('长期股权投资', 'lte', '七、9'), ('固定资产', 'ppe', '七、10'), ('在建工程', 'cip', '七、11'), ('使用权资产', 'rou', '七、12'), ('无形资产', 'intang', '七、13'), ('商誉', 'goodwill', '七、14'),
          ('长期待摊费用', 'ltp', '七、15'), ('递延所得税资产', 'dta', '七、16'), ('其他非流动资产', 'othnca', '七、17'), ('非流动资产合计', 'nca', ''), ('资产总计', 'assets', ''),
          ('流动负债：', None, None), ('短期借款', 'stb', '七、18'), ('应付票据', 'np_', '七、19'), ('应付账款', 'ap', '七、20'), ('合同负债', 'cl', '七、21'), ('应付职工薪酬', 'payroll', '七、22'),
          ('应交税费', 'taxpay', '七、23'), ('其他应付款', 'othpay', '七、24'), ('一年内到期的非流动负债', 'cur_ncl', '七、25'), ('其他流动负债', 'othcl', '七、26'), ('流动负债合计', 'cl_total', ''),
          ('非流动负债：', None, None), ('长期借款', 'ltb', '七、27'), ('租赁负债', 'lease', '七、28'), ('递延收益', 'defer', '七、29'), ('递延所得税负债', 'dtl', '七、30'), ('非流动负债合计', 'ncl', ''),
          ('负债合计', 'liab', ''), ('所有者权益（或股东权益）：', None, None), ('实收资本（或股本）', 'share', '七、31'), ('资本公积', 'capres', '七、32'), ('盈余公积', 'surplus', '七、33'),
          ('未分配利润', 'undist', '七、34'), ('归属于母公司所有者权益（或股东权益）合计', 'eq_parent', ''), ('少数股东权益', 'minority', ''), ('所有者权益（或股东权益）合计', 'equity', ''),
          ('负债和所有者权益（或股东权益）总计', 'assets', '')]
    story.append(table([['项目', '附注', f'{year}年12月31日', f'{year - 1}年12月31日']] + [[label, note or '', money(d[key]) if key else '', money(p[key]) if key else ''] for label, key, note in bs],
                       [78 * mm, 18 * mm, 40 * mm, 40 * mm], align_from=2, size=8.2))
    story += [Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN), PageBreak(), Paragraph('合并利润表', H3), Paragraph(f'{year}年1—12月', SMALL), Paragraph('单位：元', SMALL)]
    inc = [('一、营业总收入', 'rev', ''), ('其中：营业收入', 'rev', '七、35'), ('二、营业总成本', 'total_cost', ''), ('其中：营业成本', 'cost', '七、35'), ('税金及附加', 'tax', '七、36'),
           ('销售费用', 'sell', '七、37'), ('管理费用', 'adm', '七、38'), ('研发费用', 'rd', '七、39'), ('财务费用', 'fin', '七、40'), ('加：其他收益', 'oth', '七、41'),
           ('投资收益（损失以“－”号填列）', 'inv', '七、42'), ('信用减值损失（损失以“－”号填列）', 'cred', '七、43'), ('资产减值损失（损失以“－”号填列）', 'asset', '七、44'),
           ('资产处置收益（损失以“－”号填列）', 'disp', '七、45'), ('三、营业利润（亏损以“－”号填列）', 'op', ''), ('加：营业外收入', 'nonin', '七、46'), ('减：营业外支出', 'nonex', '七、47'),
           ('四、利润总额（亏损总额以“－”号填列）', 'pt', ''), ('减：所得税费用', 'taxexp', '七、48'), ('五、净利润（净亏损以“－”号填列）', 'np', ''),
           ('1.归属于母公司股东的净利润（净亏损以“-”号填列）', 'npp', ''), ('2.少数股东损益（净亏损以“-”号填列）', 'minor', '')]
    rows = [['项目', '附注', f'{year}年度', f'{year - 1}年度']] + [[label, note, money(d[key]), money(p[key])] for label, key, note in inc]
    rows += [['（一）基本每股收益（元/股）', '', str(d['eps']), str(p['eps'])], ['（二）稀释每股收益（元/股）', '', str(d['eps']), str(p['eps'])]]
    story.append(table(rows, [78 * mm, 18 * mm, 40 * mm, 40 * mm], align_from=2, size=8.2))
    story += [Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN), PageBreak(), Paragraph('合并现金流量表', H3), Paragraph(f'{year}年1—12月', SMALL), Paragraph('单位：元', SMALL)]
    cf = [('一、经营活动产生的现金流量：', None), ('销售商品、提供劳务收到的现金', d['rev'] * D('1.06')), ('经营活动产生的现金流量净额', 'cfo'), ('二、投资活动产生的现金流量：', None),
          ('购建固定资产、无形资产和其他长期资产支付的现金', 'capex'), ('投资活动产生的现金流量净额', 'cfi'), ('三、筹资活动产生的现金流量：', None), ('筹资活动产生的现金流量净额', 'cff'),
          ('四、汇率变动对现金及现金等价物的影响', 'fx'), ('五、现金及现金等价物净增加额', 'netcash')]
    def val(source, item):
        return '' if item is None else money(source[item]) if isinstance(item, str) else money(item if source is d else p['rev'] * D('1.05'))
    story.append(table([['项目', f'{year}年度', f'{year - 1}年度']] + [[label, val(d, item), val(p, item)] for label, item in cf], [96 * mm, 40 * mm, 40 * mm], size=8.2))
    story += [Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN), Paragraph('财务报表附注（节选）', H3), Paragraph('现金流量表补充资料', H3), Paragraph('单位：元', SMALL)]
    story.append(table([['补充资料', '本期金额', '上期金额'], ['净利润', money(d['np']), money(p['np'])], ['固定资产折旧、油气资产折耗、生产性生物资产折旧', money(d['dep']), money(p['dep'])],
                        ['使用权资产折旧', money(d['roudep']), money(p['roudep'])], ['无形资产摊销', money(d['amort']), money(p['amort'])], ['长期待摊费用摊销', money(d['ltpam']), money(p['ltpam'])],
                        ['经营活动产生的现金流量净额', money(d['cfo']), money(p['cfo'])]], [96 * mm, 40 * mm, 40 * mm], size=8.2))
    path = ROOT / f'星河智造_{year}年年度报告.pdf'
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                      title=f'星河智造股份有限公司{year}年年度报告', author='明镜演示材料').build(story)
    return path


def research_report():
    a, b, c = Y[2023], Y[2022], Y[2021]
    yi = lambda v: f'{v / D(1e8):.2f}'
    seg23 = {n: (r, k) for n, r, k in segments(2023)}; seg22 = {n: (r, k) for n, r, k in segments(2022)}
    q = quarters(2023, 'rev')
    price, shares = D('18.60'), a['share']
    mcap = price * shares
    f = {2024: (D('1556000000'), D('181000000')), 2025: (D('1852000000'), D('224000000')), 2026: (D('2167000000'), D('268000000'))}
    cagr = ((a['rev'] / c['rev']) ** D('0.5') - 1) * 100
    seeded = []

    def bad(code, kind, wrong, right, note):
        entry = {'id': code, 'error_type': kind, 'wrong': wrong, 'right': right, 'note': note,
                 'review_status': 'developer_fixture_not_human_signed'}
        if code == 'S6':
            entry['seeded_intent'] = 'E3'
            entry['source_requirements'] = {'document': '星河智造_2023年年度报告.pdf', 'page': 2,
                                            'table': '一、主要会计数据和财务指标', 'unit_caption': '单位：元',
                                            'account': 'NP_PARENT_ADJ', 'period': '2023Y', 'scope': '合并'}
        seeded.append(entry)
        return wrong
    story = [Paragraph('工业机器人与智能仓储双轮驱动，盈利质量稳步提升', H1), Paragraph('星河智造（999001.SH）——公司深度报告', ParagraphStyle('s', parent=PLAIN, alignment=1)),
             Paragraph('2024年4月25日　　虚构证券研究所　　分析师：陈一鸣（证书编号：S0000000000001）　　投资评级：买入（维持）', ParagraphStyle('s2', parent=PLAIN, alignment=1, fontSize=9)),
             Spacer(1, 3 * mm), table([['当前股价(元)', f'{price}', '总市值(亿元)', yi(mcap), '总股本(亿股)', f'{shares / D(1e8):.2f}']], [30 * mm, 24 * mm, 30 * mm, 24 * mm, 30 * mm, 24 * mm], size=9),
             Paragraph('投资要点', H2),
             Paragraph(f'2023年公司实现营业收入{yi(a["rev"])}亿元，同比增长{bad("S1", "E5", "32.25", pct(a["rev"], b["rev"]), "同比增速算错")}%；'
                       f'实现归母净利润{bad("S2", "E1", "1.52", yi(a["npp"]), "归母净利润数值错误")}亿元，同比增长{pct(a["npp"], b["npp"])}%；'
                       f'扣非归母净利润{yi(a["adj"])}亿元。2021-2023年营业收入CAGR达{cagr:.1f}%。', BODY),
             Paragraph(f'盈利能力方面，2023年公司毛利率为{a["gp"] / a["rev"] * 100:.2f}%，同比提升{(a["gp"] / a["rev"] - b["gp"] / b["rev"]) * 100:.2f}个百分点；'
                       f'净利率为{a["np"] / a["rev"] * 100:.2f}%；加权平均净资产收益率为{a["roe"]}%；研发费用率为{a["rd"] / a["rev"] * 100:.2f}%。'
                       f'2022年公司营业收入为{bad("S3", "E2", yi(c["rev"]), yi(b["rev"]), "把2021年收入写成2022年")}亿元。', BODY),
             Paragraph(f'现金流与资产质量方面，2023年经营活动现金流量净额为{bad("S4", "E4", yi(a["cfo"]) + "万元", yi(a["cfo"]) + "亿元", "单位应为亿元")}，'
                       f'净现比为{a["cfo"] / a["np"]:.2f}倍；2023年末公司资产负债率为{a["liab"] / a["assets"] * 100:.2f}%，货币资金{yi(a["cash"])}亿元，'
                       f'应收账款{yi(a["ar"])}亿元。2023年基本每股收益为{a["eps"]}元。', BODY),
             Paragraph(f'分产品看，2023年工业机器人收入{yi(seg23["工业机器人"][0])}亿元，同比增长{pct(seg23["工业机器人"][0], seg22["工业机器人"][0])}%，'
                       f'毛利率为{(1 - seg23["工业机器人"][1] / seg23["工业机器人"][0]) * 100:.2f}%；智能仓储系统收入{yi(seg23["智能仓储系统"][0])}亿元，'
                       f'毛利率为{bad("S5", "E1", "32.80", f"{(1 - seg23['智能仓储系统'][1] / seg23['智能仓储系统'][0]) * 100:.2f}", "分部毛利率错误")}%。'
                       f'2023Q4公司实现营业收入{yi(q[3])}亿元，环比增长{pct(q[3], q[2])}%。', BODY),
             Paragraph(f'口径提示：2023年公司净利润为{bad("S6", "E3", yi(a["adj"]), yi(a["np"]), "把扣非归母净利润写成净利润")}亿元，非经常性损益为{a["nri"] / D(1e4):.0f}万元。', BODY),
             Paragraph(f'盈利预测与估值：我们预计公司2024-2026年归母净利润为{yi(f[2024][1])}/{yi(f[2025][1])}/{yi(f[2026][1])}亿元，'
                       f'对应EPS分别为{f[2024][1] / shares:.2f}/{f[2025][1] / shares:.2f}/{f[2026][1] / shares:.2f}元，'
                       f'对应当前股价的PE分别为{mcap / f[2024][1]:.1f}/{bad("S7", "E5", "12.6", f"{mcap / f[2025][1]:.1f}", "2025年PE算错")}/{mcap / f[2026][1]:.1f}倍，维持“买入”评级。', BODY),
             Paragraph('风险提示：下游资本开支不及预期、原材料价格波动、行业竞争加剧。', BODY),
             Paragraph('行业层面，2023年中国工业机器人市场规模约为620亿元，同比增长约8%，国产化率提升至45%左右。', BODY), PageBreak(),
             Paragraph('财务摘要和估值指标', H3)]
    cols = ['指标', '2022A', '2023A', '2024E', '2025E', '2026E']
    rev = [b['rev'], a['rev'], f[2024][0], f[2025][0], f[2026][0]]; npp = [b['npp'], a['npp'], f[2024][1], f[2025][1], f[2026][1]]
    prev_rev = [c['rev']] + rev[:-1]; prev_np = [c['npp']] + npp[:-1]
    cost = [b['cost'], a['cost'], f[2024][0] * D('0.652'), f[2025][0] * D('0.650'), f[2026][0] * D('0.648')]
    m = lambda v: f'{v / D(1e6):,.0f}'
    yoy_rev = [f'{(x - y) / y * 100:.1f}' for x, y in zip(rev, prev_rev)]
    yoy_rev[3] = bad('S8', 'E5', '24.0', yoy_rev[3], '预测表2025E收入增速与表内数值不一致')
    rows = [cols, ['营业收入(百万元)'] + [m(v) for v in rev], ['YOY(%)'] + yoy_rev,
            ['归母净利润(百万元)'] + [m(v) for v in npp], ['YOY(%)'] + [f'{(x - y) / y * 100:.1f}' for x, y in zip(npp, prev_np)],
            ['毛利率(%)'] + [f'{(r - k) / r * 100:.1f}' for r, k in zip(rev, cost)], ['净利率(%)'] + [f'{n / r * 100:.1f}' for n, r in zip(npp, rev)],
            ['EPS(摊薄/元)'] + [f'{n / shares:.2f}' for n in npp], ['P/E(倍)'] + [f'{mcap / n:.1f}' for n in npp]]
    story += [table(rows, [44 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm], size=9), Paragraph('数据来源：公司公告、虚构证券研究所', SMALL), Spacer(1, 5 * mm), Paragraph('附：财务预测摘要', H3)]
    eq = [b['eq_parent'], a['eq_parent']] + [a['eq_parent'] + sum(v[1] for y, v in f.items() if y <= yy) for yy in (2024, 2025, 2026)]
    assets = [b['assets'], a['assets']] + [a['assets'] * k for k in (D('1.12'), D('1.26'), D('1.40'))]
    liab = [b['liab'], a['liab']] + [assets[i] - eq[i] - a['minority'] for i in (2, 3, 4)]
    rows = [['利润表(百万元)'] + cols[1:], ['营业收入'] + [m(v) for v in rev], ['营业成本'] + [m(v) for v in cost],
            ['销售费用'] + [m(b['sell']), m(a['sell'])] + [m(r * D('0.059')) for r in rev[2:]], ['管理费用'] + [m(b['adm']), m(a['adm'])] + [m(r * D('0.049')) for r in rev[2:]],
            ['研发费用'] + [m(b['rd']), m(a['rd'])] + [m(r * D('0.069')) for r in rev[2:]], ['营业利润'] + [m(b['op']), m(a['op'])] + [m(n * D('1.22')) for n in npp[2:]],
            ['归属母公司净利润'] + [m(v) for v in npp],
            ['资产负债表(百万元)'] + cols[1:], ['资产总计'] + [m(v) for v in assets], ['负债合计'] + [m(v) for v in liab], ['归属母公司股东权益'] + [m(v) for v in eq],
            ['主要财务比率'] + cols[1:], ['资产负债率(%)'] + [f'{l / s * 100:.1f}' for l, s in zip(liab, assets)], ['ROE(%)'] + [f'{n / e * 100:.1f}' for n, e in zip(npp, eq)],
            ['每股净资产(最新摊薄)'] + [f'{e / shares:.2f}' for e in eq], ['P/B'] + [f'{mcap / e:.1f}' for e in eq]]
    story += [table(rows, [48 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm, 26 * mm], size=9), Paragraph('数据来源：公司公告、虚构证券研究所', SMALL), PageBreak(),
              Paragraph('法律声明', H2), Paragraph('本报告为明镜演示用虚构材料。分析师承诺：本报告署名分析师具有证券投资咨询执业资格。评级说明：买入指预计相对强于市场表现20%以上。', BODY)]
    path = ROOT / '虚构证券_2024-04-25_星河智造深度报告.pdf'
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title='星河智造深度报告', author='明镜演示材料').build(story)
    (ROOT / 'seeded_errors.json').write_text(json.dumps({'report': path.name, 'review_status': 'developer_fixture_not_human_signed',
                                                       'note': '预埋错误金标：wrong 为研报中的错误写法，right 为按年报应有的写法', 'errors': seeded}, ensure_ascii=False, indent=2), encoding='utf-8')
    return path



# ───────────── 2024 年一季报、半年报与第二份研报（季度与引用核查） ─────────────
Q24 = dict(rev=D('296450000.00'), cost_ratio=D('0.648'), npp=D('40560000.00'), adj=D('37980000.00'), cfo=D('31200000.00'),
           assets=D('2060000000.00'), eq=D('1227000000.00'))
H24 = dict(rev=D('643600000.00'), cost_ratio=D('0.652'), npp=D('88920000.00'), adj=D('83450000.00'), cfo=D('72300000.00'),
           cash=D('438000000.00'), assets=D('2105000000.00'), eq=D('1275000000.00'),
           seg=[('工业机器人', D('0.58'), D('0.636')), ('智能仓储系统', D('0.29'), D('0.684')), ('软件与服务', D('0.13'), D('0.633'))])
SHARES24 = D('420000000')


def prev_quarters(key):
    return quarters(2023, key)


def cover(title, date_text):
    return [Spacer(1, 60 * mm), Paragraph('星河智造股份有限公司', H1), Paragraph(title, H1), Spacer(1, 30 * mm),
            Paragraph('证券代码：999001　　证券简称：星河智造', ParagraphStyle('c', parent=PLAIN, alignment=1)),
            Paragraph(f'披露日期：{date_text}', ParagraphStyle('c2', parent=PLAIN, alignment=1)),
            Paragraph('（本报告为明镜演示用虚构材料，公司与数据均不真实）', ParagraphStyle('c3', parent=PLAIN, alignment=1, textColor=colors.grey)), PageBreak()]


def q1_report():
    a = Y[2023]
    p_rev, p_npp, p_adj, p_cfo = (prev_quarters(k)[0] for k in ('rev', 'npp', 'adj', 'cfo'))
    eps, p_eps = (Q24['npp'] / SHARES24).quantize(D('0.01')), (p_npp / SHARES24).quantize(D('0.01'))
    rows = [['项目', '本报告期', '上年同期', '本报告期比上年同期增减变动幅度(%)'],
            ['营业收入', money(Q24['rev']), money(p_rev), pct(Q24['rev'], p_rev)],
            ['归属于上市公司股东的净利润', money(Q24['npp']), money(p_npp), pct(Q24['npp'], p_npp)],
            ['归属于上市公司股东的扣除非经常性损益的净利润', money(Q24['adj']), money(p_adj), pct(Q24['adj'], p_adj)],
            ['经营活动产生的现金流量净额', money(Q24['cfo']), money(p_cfo), pct(Q24['cfo'], p_cfo)],
            ['基本每股收益（元/股）', str(eps), str(p_eps), pct(eps, p_eps)]]
    story = cover('2024年第一季度报告', '2024年4月26日') + [Paragraph('一、主要财务数据', H2), Paragraph('（一）主要会计数据和财务指标', H3), Paragraph('单位：元', SMALL),
             table(rows, [62 * mm, 34 * mm, 34 * mm, 44 * mm]), Spacer(1, 4 * mm),
             table([['', '本报告期末', '上年度末', '本报告期末比上年度末增减变动幅度(%)'],
                    ['总资产', money(Q24['assets']), money(a['assets']), pct(Q24['assets'], a['assets'])],
                    ['归属于上市公司股东的所有者权益', money(Q24['eq']), money(a['eq_parent']), pct(Q24['eq'], a['eq_parent'])]], [62 * mm, 34 * mm, 34 * mm, 44 * mm]),
             Paragraph('二、季度财务报表', H2), Paragraph('合并利润表', H3), Paragraph('2024年1—3月　　单位：元', SMALL)]
    cost, p_cost = (Q24['rev'] * Q24['cost_ratio']).quantize(D('0.01')), (p_rev * D('0.660')).quantize(D('0.01'))
    story.append(table([['项目', '2024年第一季度', '2023年第一季度'], ['一、营业总收入', money(Q24['rev']), money(p_rev)], ['其中：营业收入', money(Q24['rev']), money(p_rev)],
                        ['其中：营业成本', money(cost), money(p_cost)], ['1.归属于母公司股东的净利润（净亏损以“-”号填列）', money(Q24['npp']), money(p_npp)]], [96 * mm, 40 * mm, 40 * mm], size=8.2))
    story.append(Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN))
    path = ROOT / '星河智造_2024年第一季度报告.pdf'
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title='星河智造2024年第一季度报告', author='明镜演示材料').build(story)
    return path


def h1_values():
    p_rev = sum(prev_quarters('rev')[:2]); p_npp = sum(prev_quarters('npp')[:2]); p_adj = sum(prev_quarters('adj')[:2]); p_cfo = sum(prev_quarters('cfo')[:2])
    return p_rev, p_npp, p_adj, p_cfo


def h1_segments():
    rows, left_rev, left_cost = [], H24['rev'], (H24['rev'] * H24['cost_ratio']).quantize(D('0.01'))
    for index, (name, share, ratio) in enumerate(H24['seg']):
        last = index == len(H24['seg']) - 1
        rev = left_rev if last else (H24['rev'] * share).quantize(D('0.01'))
        cost = left_cost if last else (rev * ratio).quantize(D('0.01'))
        left_rev -= rev; left_cost -= cost
        rows.append((name, rev, cost))
    return rows


def h1_report():
    a = Y[2023]
    p_rev, p_npp, p_adj, p_cfo = h1_values()
    eps, p_eps = (H24['npp'] / SHARES24).quantize(D('0.01')), (p_npp / SHARES24).quantize(D('0.01'))
    story = cover('2024年半年度报告', '2024年8月28日') + [Paragraph('第二节 公司简介和主要财务指标', H2), Paragraph('一、主要会计数据和财务指标', H3), Paragraph('单位：元', SMALL)]
    story.append(table([['主要会计数据', '本报告期', '上年同期', '本报告期比上年同期增减(%)'],
                        ['营业收入', money(H24['rev']), money(p_rev), pct(H24['rev'], p_rev)],
                        ['归属于上市公司股东的净利润', money(H24['npp']), money(p_npp), pct(H24['npp'], p_npp)],
                        ['归属于上市公司股东的扣除非经常性损益的净利润', money(H24['adj']), money(p_adj), pct(H24['adj'], p_adj)],
                        ['经营活动产生的现金流量净额', money(H24['cfo']), money(p_cfo), pct(H24['cfo'], p_cfo)],
                        ['基本每股收益（元/股）', str(eps), str(p_eps), pct(eps, p_eps)]], [62 * mm, 34 * mm, 34 * mm, 44 * mm]))
    story += [Spacer(1, 4 * mm), table([['', '本报告期末', '上年度末', '本报告期末比上年度末增减(%)'],
                                        ['总资产', money(H24['assets']), money(a['assets']), pct(H24['assets'], a['assets'])],
                                        ['归属于上市公司股东的净资产', money(H24['eq']), money(a['eq_parent']), pct(H24['eq'], a['eq_parent'])]], [62 * mm, 34 * mm, 34 * mm, 44 * mm]),
              Paragraph('第三节 管理层讨论与分析', H2), Paragraph('主营业务分行业、分产品、分地区情况', H3), Paragraph('单位：元', SMALL)]
    rows = [['分产品', '营业收入', '营业成本', '毛利率(%)']] + [[n, money(r), money(c), f'{(r - c) / r * 100:.2f}'] for n, r, c in h1_segments()]
    story += [table(rows, [50 * mm, 40 * mm, 40 * mm, 30 * mm]), PageBreak(), Paragraph('第八节 财务报告', H2), Paragraph('合并资产负债表', H3),
              Paragraph('2024年6月30日　　单位：元', SMALL)]
    story.append(table([['项目', '附注', '2024年6月30日', '2023年12月31日'], ['货币资金', '七、1', money(H24['cash']), money(a['cash'])],
                        ['资产总计', '', money(H24['assets']), money(a['assets'])],
                        ['归属于母公司所有者权益（或股东权益）合计', '', money(H24['eq']), money(a['eq_parent'])]], [78 * mm, 18 * mm, 40 * mm, 40 * mm], align_from=2, size=8.2))
    story += [Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN), Paragraph('合并利润表', H3), Paragraph('2024年1—6月　　单位：元', SMALL)]
    cost, p_cost = (H24['rev'] * H24['cost_ratio']).quantize(D('0.01')), (p_rev * D('0.660')).quantize(D('0.01'))
    story.append(table([['项目', '附注', '2024年半年度', '2023年半年度'], ['一、营业总收入', '', money(H24['rev']), money(p_rev)], ['其中：营业收入', '七、35', money(H24['rev']), money(p_rev)],
                        ['其中：营业成本', '七、35', money(cost), money(p_cost)], ['1.归属于母公司股东的净利润（净亏损以“-”号填列）', '', money(H24['npp']), money(p_npp)]],
                       [78 * mm, 18 * mm, 40 * mm, 40 * mm], align_from=2, size=8.2))
    story.append(Paragraph('法定代表人：林晨　　主管会计工作负责人：周岚　　会计机构负责人：许拓', PLAIN))
    path = ROOT / '星河智造_2024年半年度报告.pdf'
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title='星河智造2024年半年度报告', author='明镜演示材料').build(story)
    return path


def research_report_2():
    a = Y[2023]
    p_rev, p_npp, p_adj, p_cfo = h1_values()
    q2 = H24['rev'] - Q24['rev']
    q2_prev = prev_quarters('rev')[1]
    gm = (1 - H24['cost_ratio']) * 100
    yi = lambda v: f'{v / D(1e8):.2f}'
    price = D('21.30'); mcap = price * SHARES24
    seeded = []

    def bad(code, kind, wrong, right, note):
        seeded.append({'id': code, 'error_type': kind, 'wrong': wrong, 'right': right, 'note': note})
        return wrong
    seg = {n: r for n, r, _ in h1_segments()}
    adj_growth = D(pct(H24['adj'], p_adj))
    adj_word = '下降' if adj_growth > 0 else '增长'           # T6：增减方向说反（E7 逻辑比较错误）
    adj_abs = f'{abs(adj_growth):.2f}'
    adj_right = ('增长' if adj_growth > 0 else '下降') + adj_abs + '%'
    story = [Paragraph('二季度收入环比提速，盈利能力保持稳定', H1), Paragraph('星河智造（999001.SH）——2024年中报点评', ParagraphStyle('s', parent=PLAIN, alignment=1)),
             Paragraph('2024年8月30日　　示例证券研究所　　分析师：周可（证书编号：S0000000000002）　　投资评级：增持（维持）', ParagraphStyle('s2', parent=PLAIN, alignment=1, fontSize=9)),
             Spacer(1, 3 * mm), table([['当前股价(元)', f'{price}', '总市值(亿元)', yi(mcap), '总股本(亿股)', f'{SHARES24 / D(1e8):.2f}']], [30 * mm, 24 * mm, 30 * mm, 24 * mm, 30 * mm, 24 * mm], size=9),
             Paragraph('事件', H2),
             Paragraph(f'2024年上半年公司实现营业收入{yi(H24["rev"])}亿元，同比增长{pct(H24["rev"], p_rev)}%；实现归母净利润{yi(H24["npp"])}亿元，同比增长{pct(H24["npp"], p_npp)}%；'
                       f'基本每股收益{(H24["npp"] / SHARES24).quantize(D("0.01"))}元。', BODY),
             Paragraph('点评', H2),
             Paragraph(f'分季度看，2024年第二季度公司实现营业收入{yi(q2)}亿元，同比增长{pct(q2, q2_prev)}%，环比增长{bad("T1", "E5", "13.10", pct(q2, Q24["rev"]), "二季度环比增速算错")}%，收入增长提速。'
                       f'2024年第一季度公司实现营业收入{bad("T2", "E2", yi(q2), yi(Q24["rev"]), "把二季度收入写成一季度")}亿元。', BODY),
             Paragraph(f'盈利能力方面，上半年综合毛利率为{bad("T3", "E5", "36.80", f"{gm:.2f}", "毛利率与营业收入、营业成本不符")}%；分产品看，上半年工业机器人收入{yi(seg["工业机器人"])}亿元；上半年扣非归母净利润{yi(H24["adj"])}亿元，同比{adj_word}{bad("T6", "E7", adj_abs, adj_right, "增减方向说反（实际为相反方向）")}%。', BODY),
             Paragraph(f'现金流与资产方面，据公司2023年年度报告，2024年上半年经营活动现金流量净额为{bad("T4", "E6", yi(H24["cfo"]), yi(H24["cfo"]), "数据出自2024年半年报，引用出处写错")}亿元；'
                       f'截至2024年6月末，公司货币资金{yi(H24["cash"])}亿元。', BODY),
             Paragraph('投资建议：我们维持盈利预测，维持“增持”评级。风险提示：下游需求不及预期、行业竞争加剧。', BODY), PageBreak(), Paragraph('主要财务指标', H3)]
    rev = [a['rev'], D('1556000000'), D('1852000000'), D('2167000000')]; npp = [a['npp'], D('181000000'), D('224000000'), D('268000000')]
    prev_rev = [Y[2022]['rev']] + rev[:-1]; prev_np = [Y[2022]['npp']] + npp[:-1]
    eq = [a['eq_parent'], a['eq_parent'] + npp[1], a['eq_parent'] + npp[1] + npp[2], a['eq_parent'] + npp[1] + npp[2] + npp[3]]
    growth = [f'{(x - y) / y * 100:.1f}%' for x, y in zip(rev, prev_rev)]
    growth[2] = bad('T5', 'E5', '21.5%', growth[2], '主要财务指标表2025E收入增速与表内数值不一致')
    m = lambda v: f'{v / D(1e6):,.0f}'
    rows = [['会计年度', '2023A', '2024E', '2025E', '2026E'], ['营业总收入(百万元)'] + [m(v) for v in rev], ['同比增长率'] + growth,
            ['归母净利润(百万元)'] + [m(v) for v in npp], ['同比增长率'] + [f'{(x - y) / y * 100:.1f}%' for x, y in zip(npp, prev_np)],
            ['每股收益(元)'] + [f'{n / SHARES24:.2f}' for n in npp], ['ROE'] + [f'{n / e * 100:.1f}%' for n, e in zip(npp, eq)],
            ['P/E(倍)'] + [f'{mcap / n:.1f}' for n in npp], ['P/B(倍)'] + [f'{mcap / e:.1f}' for e in eq]]
    story += [table(rows, [44 * mm, 30 * mm, 30 * mm, 30 * mm, 30 * mm], size=9), Paragraph('数据来源：公司公告、示例证券研究所', SMALL), PageBreak(),
              Paragraph('法律声明', H2), Paragraph('本报告为明镜演示用虚构材料。分析师承诺与评级说明从略。', BODY)]
    path = ROOT / '示例证券_2024-08-30_星河智造2024年中报点评.pdf'
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm, title='星河智造2024年中报点评', author='明镜演示材料').build(story)
    return path, seeded


README = """# 直传模式虚构样例（一键演示与回归测试）

全部公司、代码与数字均为虚构，仅用于演示与测试。

| 公司 | 文件 | 说明 |
| --- | --- | --- |
| 星河智造（999001） | 2022、2023 年年报，2024 年一季报、半年报 | 由 `scripts/make_smart_samples.py`（ReportLab）生成 |
| 星河智造 | 虚构证券 2024-04-25 深度报告 | 8 处预埋错误；研报日期早于一季报与半年报，核查时这两份按研报日期截断不得使用 |
| 星河智造 | 示例证券 2024-08-30 中报点评 | 6 处预埋错误（含单季推导、引用出处错误、增减方向说反） |
| 镜湖示范科技（999901） | 2023、2024 年年报，镜湖证券 2025-04-28 首次覆盖 | 9 处预埋错误；由 `scripts/make_jinghu_samples.py`（Playwright）生成 |

预埋错误金标见 `seeded_errors.json`（共 23 处，覆盖 E1–E7）。错误代码与计划书表5-6一致：E5 计算错误含表内计算不平，
E6 引用错误含“据某报告”出处不符和正文与研报自身预测表不一致，E7 为增减方向或比较关系说反。
"""


if __name__ == '__main__':
    ROOT.mkdir(parents=True, exist_ok=True)
    for year in (2022, 2023):
        print(annual_report(year))
    print(research_report())
    first = json.loads((ROOT / 'seeded_errors.json').read_text(encoding='utf-8'))
    print(q1_report())
    print(h1_report())
    second_path, second = research_report_2()
    print(second_path)
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from make_jinghu_samples import GOLD as JINGHU   # 镜湖科技三份样例（Playwright 生成，PDF 已随包提供）
    reports = [{'report': first['report'], 'errors': first['errors']}, {'report': second_path.name, 'errors': second}, JINGHU]
    (ROOT / 'README.md').write_text(README, encoding='utf-8')
    (ROOT / 'seeded_errors.json').write_text(json.dumps({'review_status': 'developer_fixture_not_human_signed',
                                                          'note': '预埋错误金标：wrong 为研报中的错误写法，right 为应有写法；错误代码按计划书表5-6（E5 计算错误含表内计算不平，E6 引用错误含正文与自身预测表不一致，E7 增减方向或比较关系说反）；第一份研报日期早于季报与半年报，核查时这两份不得使用；relation 用于区分同一句中的同值数字',
                                                          'reports': reports, 'errors': [e for r in reports for e in r['errors']]}, ensure_ascii=False, indent=2), encoding='utf-8')
