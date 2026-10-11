"""从文件内容自动识别：文档类型、公司、证券代码、报告期、日期、券商。

识别结果只用于检索与默认值，全部带置信度与依据片段；用户填写的可选项优先。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from .common import compact, find_periods, norm
from .docio import Document

REPORT_MARKS = ('投资评级', '分析师', '证书编号', '执业证书', '目标价', '盈利预测', '风险提示', '首次覆盖', '买入', '增持',
                '证券研究报告', '研究所', '维持评级', '估值', '投资建议', '投资要点', '强烈推荐', '跟踪报告', '点评报告', '深度报告')
FILING_MARKS = ('重要提示', '公司简介', '主要会计数据', '会计数据和财务指标', '合并资产负债表', '合并利润表', '审计报告',
                '法定代表人', '主管会计工作负责人', '会计机构负责人', '董事会', '监事会', '财务报表附注', '股份变动', '公司治理')
_COMPANY_SUFFIX = r'(?:股份有限公司|有限责任公司|集团有限公司|有限公司)'
_FILING_TITLE = re.compile(r'(20\d{2})年?(年度报告|半年度报告|年半年度报告|中期报告|第?[一三]季度报告|第?[一三]季度季度报告)(摘要)?')
_KIND_OF = {'年度报告': ('annual_report', 'Y'), '半年度报告': ('semiannual_report', 'H1'), '年半年度报告': ('semiannual_report', 'H1'),
            '中期报告': ('semiannual_report', 'H1')}


@dataclass
class DocMeta:
    doc_type: str = 'unknown'          # research_report / annual_report / semiannual_report / quarterly_report / prospectus / facts_json / other
    company: str = ''                  # 简称
    company_full: str = ''
    stock_code: str = ''
    period: str = ''                   # 财报报告期，如 2023Y / 2024H1 / 2024Q1 / 20249M
    base_year: int | None = None       # 研报最近一个实际（A）财年
    report_date: str = ''              # 研报日期或财报披露日期（ISO）
    broker: str = ''
    title: str = ''
    confidence: float = 0.0
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_filing(self) -> bool:
        return self.doc_type in {'annual_report', 'semiannual_report', 'quarterly_report', 'prospectus', 'facts_json'}


def _iso(year, month, day) -> str:
    try:
        from datetime import date
        return date(int(year), int(month), int(day)).isoformat()
    except Exception:
        return ''


def _dates(text: str) -> list[str]:
    found = [_iso(*m.groups()) for m in re.finditer(r'(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日', text)]
    found += [_iso(*m.groups()) for m in re.finditer(r'(?<![\d.])(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?![\d.])', text)]
    return [value for value in found if value]


def _pdf_date(doc: Document, period: str) -> str:
    """Read a plausible PDF creation date for diagnostics, never disclosure proof."""
    if doc.kind != 'pdf':
        return ''
    raw = ''
    try:
        import fitz  # type: ignore
        with fitz.open(doc.path) as handle:
            raw = (handle.metadata or {}).get('creationDate', '')
    except Exception:
        try:
            from pypdf import PdfReader
            raw = str((PdfReader(doc.path).metadata or {}).get('/CreationDate', ''))
        except Exception:
            raw = ''
    match = re.search(r'(20\d{2})(\d{2})(\d{2})', raw or '')
    if not match:
        return ''
    value = _iso(*match.groups())
    end = {'Y': '12-31', 'H1': '06-30', 'Q1': '03-31', '9M': '09-30'}.get(period[4:], '12-31')
    start = f'{period[:4]}-{end}'
    limit = f'{int(period[:4]) + 2}-06-30'
    return value if start < value <= limit else ''


def short_name(full: str) -> str:
    name = re.sub(_COMPANY_SUFFIX + r'$', '', full)
    name = re.sub(r'^(?:北京|上海|天津|重庆|深圳|广州|杭州|南京|苏州|成都|武汉|西安|厦门|青岛|宁波|合肥|长沙|郑州|济南|福州|无锡|'
                  r'河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|'
                  r'内蒙古|广西|西藏|宁夏|新疆|中国)(?:省|市)?', '', name)
    return name or full


def code_aliases(code: str) -> set[str]:
    """证券代码别名：北交所存量公司2024年起由8、4开头切换为920开头、后三位不变（如839493→920493）。"""
    code = (code or '').strip()
    if not re.fullmatch(r'\d{6}', code):
        return {code} if code else set()
    aliases = {code}
    if code[0] in '48' and not code.startswith('920'):
        aliases.add('920' + code[-3:])
    return aliases


def same_company(a: 'DocMeta | dict', b: 'DocMeta | dict') -> bool:
    get = (lambda o, k: o.get(k, '') if isinstance(o, dict) else getattr(o, k, ''))
    code_a, code_b = get(a, 'stock_code'), get(b, 'stock_code')
    if code_a and code_b:
        # Two original non-920 codes remain distinct even when their final
        # digits would produce the same speculative 920 search alias.
        if code_a != code_b and not (code_a.startswith('920') or code_b.startswith('920')):
            return False
        if code_aliases(code_a) & code_aliases(code_b):
            return True
        if code_a.startswith('920') or code_b.startswith('920'):
            pass                     # 新旧代码后三位不同，再按名称判断
        else:
            return False
    names_a = {n for n in (get(a, 'company'), get(a, 'company_full'), short_name(get(a, 'company_full') or '')) if n and len(n) >= 2}
    names_b = {n for n in (get(b, 'company'), get(b, 'company_full'), short_name(get(b, 'company_full') or '')) if n and len(n) >= 2}
    if names_a & names_b:
        return True
    return any(x in y or y in x for x in names_a for y in names_b if min(len(x), len(y)) >= 3)


def detect(doc: Document, *, hints: dict | None = None) -> DocMeta:
    hints = hints or {}
    meta = DocMeta()
    if doc.kind == 'json':
        meta.doc_type, meta.confidence = 'facts_json', 0.9
        meta.signals.append('结构化事实文件')
    head_pages = doc.pages[:4]
    head = '\n'.join(page.flow for page in head_pages)
    first = doc.pages[0].flow if doc.pages else ''
    whole_sample = '\n'.join(page.flow for page in doc.pages[:12])
    c_head, c_first = compact(head), compact(first)
    name = norm(doc.name)
    report_score = sum(mark in compact(whole_sample) for mark in REPORT_MARKS)
    filing_score = sum(mark in compact(whole_sample) for mark in FILING_MARKS)
    forecast_cols = len(re.findall(r'20\d{2}E', compact(whole_sample)))
    title = _FILING_TITLE.search(c_first) or _FILING_TITLE.search(compact(name)) or _FILING_TITLE.search(c_head)
    if doc.kind != 'json':
        if '招股说明书' in c_first[:400] or '招股说明书' in name:
            meta.doc_type = 'prospectus'
        elif title and filing_score >= max(2, report_score - 2) and forecast_cols < 3:
            kind = title[2]
            if kind in _KIND_OF:
                meta.doc_type, suffix = _KIND_OF[kind]
            else:
                meta.doc_type, suffix = 'quarterly_report', ('Q1' if '一' in kind else '9M')
            meta.period = title[1] + suffix
            meta.title = title[0]
            meta.signals.append('标题：' + title[0])
        elif report_score >= 3 or forecast_cols >= 3:
            meta.doc_type = 'research_report'
        elif filing_score >= 4:
            meta.doc_type = 'annual_report' if '年度报告' in c_head else 'other'
        else:
            meta.doc_type = 'research_report' if re.search(r'研报|研究报告|点评|深度|覆盖', name) else 'other'
        meta.confidence = min(0.99, 0.5 + 0.08 * abs(report_score - filing_score) + (0.2 if title else 0))
        meta.signals.append(f'研报特征{report_score}项/财报特征{filing_score}项/预测列{forecast_cols}')
    # —— 公司与代码 ——
    code = re.search(r'(?:证券代码|股票代码|公司代码|代码)[:：]?(\d{6})', compact('\n'.join(page.flow for page in doc.pages[:10])))
    if not code and doc.pages:
        cover = re.search(r'^\s*([\u4e00-\u9fffA-Z*]{2,8})\s*\n\s*(\d{6})\s*$', norm(doc.pages[0].flow), re.M)
        code = re.match(r'(.*)', cover[2]) if cover else None
    paired = Counter()
    loose = norm('\n'.join(page.flow for page in doc.pages[:3]) + '\n' + '\n'.join(row.text for page in doc.pages[:2] for row in page.rows))
    for m in re.finditer(r'(?:^|[\s:：|,，。、“"(（])([\u4e00-\u9fffA-Z*]{2,8})\s*[（(]\s*(\d{6})(?:\.(?:SH|SZ|BJ|HK|sh|sz|bj))?\s*[)）]', loose, re.M):
        if not re.search(r'报告|研究|证券|评级|代码|简称|公司$', m[1]):
            paired[(m[1], m[2])] += 1
    # Keep physical text boundaries: compacting all cover lines together
    # turns “年度报告\n北京…股份有限公司” into a false legal issuer name.
    company_first = '\n'.join(compact(line) for line in first.splitlines())
    company_head = '\n'.join(compact(line) for line in head.splitlines())
    full = re.search(r'([\u4e00-\u9fff（）()]{2,30}?' + _COMPANY_SUFFIX + ')', company_first) or re.search(
        r'([\u4e00-\u9fff（）()]{2,30}?' + _COMPANY_SUFFIX + ')', company_head)
    abbr = re.search(r'(?:证券简称|股票简称|公司简称)[ \t]*[:：]?[ \t]*([\u4e00-\u9fffA-Z*]{2,8})(?![\u4e00-\u9fffA-Z*])',
                     norm('\n'.join(page.flow for page in doc.pages[:10])))
    if meta.doc_type == 'research_report' and paired:
        (company, stock), _ = paired.most_common(1)[0]
        company = re.sub(r'^(?:公司|报告|研究|覆盖|评级|所)+', '', company)
        meta.company, meta.stock_code = company, stock
        meta.signals.append(f'公司：{company}（{stock}）')
    else:
        if code:
            meta.stock_code = code[1]
        elif paired:
            meta.stock_code = paired.most_common(1)[0][0][1]
        if full:
            meta.company_full = full[1]
        if abbr and abbr[1] not in ('无', '不适用'):
            meta.company = abbr[1]
        elif paired:
            meta.company = paired.most_common(1)[0][0][0]
        elif meta.company_full:
            meta.company = short_name(meta.company_full)
    folder = re.search(r'(\d{6})_([\u4e00-\u9fffA-Z*]{2,10})', str(doc.path).replace('\\', '/'))
    if folder:
        meta.stock_code = meta.stock_code or folder[1]
        meta.company = meta.company or folder[2]
    if not meta.company and meta.doc_type == 'research_report':
        guess = Counter(re.findall(r'([\u4e00-\u9fff]{2,6})(?:是|为)(?:国内|全球|行业)?领先', c_head))
        if guess:
            meta.company = guess.most_common(1)[0][0]
    # —— 日期 ——
    name_date = re.search(r'(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})', name)
    if meta.doc_type == 'research_report':
        candidates = _dates(first)
        meta.report_date = max(candidates) if candidates else (_iso(*name_date.groups()) if name_date else '')
        broker = Counter(re.findall(r'([\u4e00-\u9fff]{2,6}证券)(?:研究所|股份|有限|证券研究)?', compact(whole_sample)))
        broker.update(re.findall(r'^([\u4e00-\u9fff]{2,6}证券)', name))
        if broker:
            meta.broker = broker.most_common(1)[0][0]
        actual = [int(y) for y in re.findall(r'(?<![\dA-Za-z])(20\d{2})\s?A(?![A-Za-z])', norm('\n'.join(r.text for p in doc.pages for r in p.rows)))]
        if actual:
            meta.base_year = max(actual)
        else:
            years = Counter(int(p.periods[0][:4]) for p in find_periods(norm(whole_sample)) if not p.forecast and not p.is_range)
            limit = int(meta.report_date[:4]) if meta.report_date else 2100
            plausible = {y: n for y, n in years.items() if y <= limit}
            meta.base_year = max(plausible, key=lambda y: (plausible[y], y)) if plausible else None
        title_row = next((row.text for row in doc.pages[0].rows if 8 <= len(compact(row.text)) <= 60 and re.search(r'[\u4e00-\u9fff]{6,}', row.text)
                          and not re.search(r'分析师|证书|评级|请务必|@', row.text)), '') if doc.pages else ''
        meta.title = meta.title or title_row.strip()
    else:
        if name_date:
            meta.report_date = _iso(*name_date.groups())
        else:
            marked = re.search(r'(?:披露日期|公告日期|报出日期|批准报出日)[:：为]?(20\d{2})年(\d{1,2})月(\d{1,2})日', compact(whole_sample))
            if marked:
                meta.report_date = _iso(*marked.groups())
            elif meta.period:
                tail = '\n'.join(page.flow for page in doc.pages[-3:]) + head
                later = [d for d in _dates(tail) if d[:4] >= meta.period[:4] and d > f'{meta.period[:4]}-06-30']
                if meta.period.endswith('Y'):
                    later = [d for d in later if d[:4] == str(int(meta.period[:4]) + 1)]
                if later:
                    meta.report_date = max(later)
        if not meta.period and meta.doc_type != 'facts_json':
            title = _FILING_TITLE.search(compact(name))
            if title:
                meta.period = title[1] + _KIND_OF.get(title[2], ('', 'Q1' if '一' in title[2] else '9M'))[1]
    if doc.kind == 'pdf' and meta.period and not meta.report_date:
        creation_date = _pdf_date(doc, meta.period)
        if creation_date:
            meta.signals.append('PDF创建日期仅供诊断，不证明公开披露日：' + creation_date)
    # —— 用户可选项覆盖 ——
    for key in ('company', 'stock_code', 'period', 'report_date', 'doc_type'):
        if hints.get(key):
            setattr(meta, key, hints[key])
            meta.signals.append(f'用户指定{key}')
    if not meta.company and meta.company_full:
        meta.company = short_name(meta.company_full)
    return meta
