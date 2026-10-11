"""结果导出：results.json（结构化）、report.md、report.html（自包含）、corrections.csv。"""
from __future__ import annotations

import csv
import html
import io
import json
from pathlib import Path

STATUS = {'verified': '已核实', 'violated': '有误', 'unverifiable': '未能核实', 'skipped': '不判定'}
RELATION = {'value': '', 'yoy': '同比', 'qoq': '环比', 'cagr': 'CAGR', 'pp': '百分点变化', 'share': '占比'}
BASIS = {'disclosed': '披露值直接比对', 'derived': '按登记公式重算', 'internal': '研报内部勾稽', 'identity': '表内勾稽', 'text': '原文佐证', '': ''}


def _pct(value) -> str:
    return '—' if value is None else f'{value * 100:.1f}%'


def _subject(item: dict) -> str:
    from .common import period_label
    from .taxonomy import account_name
    name = account_name(item['metric']) if item.get('metric') else (item.get('metric_text') or '未绑定指标')
    return f"{period_label(item.get('period', ''))}{item.get('segment', '')}{name}{RELATION.get(item.get('relation', ''), '')}"


ERROR_NAMES = {'E1': '数值错误', 'E2': '期间错误', 'E3': '口径错误', 'E4': '单位错误', 'E5': '计算错误', 'E6': '引用错误', 'E7': '逻辑比较错误', 'E8': '材料不可验证', 'E9': '披露版本错配'}
GAP_KINDS = {'missing_report': '缺整份报告', 'missing_detail': '报告中无该明细', 'definition': '定义缺口', 'external': '需外部来源',
             'mention': '原文信息不足', 'premise': '公式前提不成立', 'conflict': '来源冲突'}


def slots(payload: dict) -> dict:
    """叙述可用的数值槽位：模型只能引用这些槽位，数字由宿主填入。"""
    if payload['task'] == 'analysis':
        analysis = payload['analysis']
        return {'materials': len(payload['materials']), 'facts': payload['facts_used'], 'metrics': len(analysis['metrics']),
                'signals': len(analysis['signals']), 'high': sum(s['severity'] == 'high' for s in analysis['signals'])}
    summary = payload['summary']
    totals = summary['totals']
    errors = sorted(summary['error_types'].items(), key=lambda kv: -kv[1])
    values = {'total': totals['total'], 'occurrences': totals['occurrences'], 'assertions': summary.get('assertions', {}).get('total', totals['total']),
              'verified': totals['verified'], 'violated': totals['violated'],
              'unverifiable': totals['unverifiable'], 'skipped': totals['skipped'], 'materials': len(payload['materials']),
              'core_rate': _pct(summary['core']['abstention_rate']), 'core_total': summary['core']['total'],
              'core_unverifiable': summary['core']['unverifiable'], 'financial_coverage': _pct(summary['financial']['coverage'])}
    for index, (code, count) in enumerate(errors[:3], 1):
        values[f'error{index}_count'] = count
    return values


def template_narrative(payload: dict) -> str:
    """确定性叙述：所有数字取自结构化结果。"""
    if payload['task'] == 'analysis':
        analysis = payload['analysis']
        titles = '、'.join(dict.fromkeys(s['title'] for s in analysis['signals'][:5])) or '无'
        checks = analysis.get('check_summary') or {'total': 0, 'failed': 0}
        return (f"基于{len(payload['materials'])}份材料的{payload['facts_used']}条事实，计算{len(analysis['metrics'])}项指标，"
                f"执行{checks['total']}项报告内部勾稽检查（{checks['failed']}项不一致），"
                f"触发{len(analysis['signals'])}条复核信号（{titles}）。信号为规则提示，需结合原文判断，不构成投资或舞弊结论。")
    summary, totals = payload['summary'], payload['summary']['totals']
    errors = sorted(summary['error_types'].items(), key=lambda kv: -kv[1])
    error_text = '、'.join(f'{ERROR_NAMES.get(code, code)}{count}处' for code, count in errors[:5])
    used = [m for m in payload['materials'] if not m.get('excluded')]
    late = len(payload['materials']) - len(used)
    parts = [f"本次核查共识别{totals['occurrences']}处数字陈述（{summary.get('assertions', {}).get('total', totals['total'])}条去重断言），参考材料{len(used)}份"
             + (f"（另有{late}份晚于研报日期公告，按研报日期截断未使用）" if late else '') + '。',
             f"已核实{totals['verified']}处；发现{totals['violated']}处与原始披露或研报自身表格不相容" + (f"，其中{error_text}" if error_text else '') + '。',
             f"{totals['unverifiable']}处未能核实，{totals['skipped']}处为观点或行情输入不作判定；公司历史财务陈述的弃权率为{_pct(summary['core']['abstention_rate'])}"
             f"（{summary['core']['unverifiable']}/{summary['core']['total']}）。"]
    gaps = payload.get('gaps') or []
    reports = [g for g in gaps if g['kind'] == 'missing_report']
    details = [g for g in gaps if g['kind'] == 'missing_detail']
    if reports or details:
        text = '补材建议：' + (f"补充{'、'.join(g['need'] for g in reports[:4])}{'等' if len(reports) > 4 else ''}可解除{sum(g['count'] for g in reports)}条缺口" if reports else '')
        text += ('；' if reports and details else '') + (f"另有{sum(g['count'] for g in details)}条涉及分业务、分客户等明细，通常见于招股说明书" if details else '')
        parts.append(text + '（详见“未能核实与补材”）。')
    elif payload.get('advice'):
        parts.append('补材建议：' + payload['advice'][0])
    return ''.join(parts)


def render_markdown(payload: dict) -> str:
    lines = []
    report = payload['report']
    if payload['task'] == 'analysis':
        analysis = payload['analysis']
        lines += [f"# 明镜财报分析 · {report.get('company', '')}", '', f"运行 `{payload['run_id']}` · 材料 {len(payload['materials'])} 份 · 事实 {payload['facts_used']} 条", '',
                  '> ' + ((payload.get('narrative') or {}).get('text') or template_narrative(payload)), '',
                  '> ' + analysis['note'], ''] + (['## 要点', ''] + [f'- {line}' for line in analysis.get('highlights', [])] + [''] if analysis.get('highlights') else []) + ['## 异常信号', '']
        if not analysis['signals']:
            lines.append('未触发登记的异常信号规则。')
        for signal in analysis['signals']:
            lines.append(f"- **[{signal['severity']}] {signal['title']}**（{signal['nature']}）：{signal['detail']}")
        summary = analysis.get('check_summary') or {}
        lines += ['', f"## 勾稽检查（共{summary.get('total', 0)}项：一致{summary.get('passed', 0)}、不一致{summary.get('failed', 0)}、无法判定{summary.get('undetermined', 0)}）", '',
                  '| 结论 | 检查 | 期间 | 口径 | 说明 |', '|---|---|---|---|---|']
        for check in analysis.get('checks', []):
            verdict = '一致' if check['ok'] is True else '不一致' if check['ok'] is False else '无法判定'
            lines.append(f"| {verdict} | {check['name']} | {check['period']} | {check['scope']} | {check['detail']} |")
        lines += ['', '## 主要指标', '', '| 指标 | ' + ' | '.join(analysis['periods']) + ' |', '|---|' + '---|' * len(analysis['periods'])]
        for row in analysis['metrics']:
            cells = []
            for period in analysis['periods']:
                cell = row['cells'].get(period)
                cells.append('' if not cell else cell['display'] + (f"（{cell['yoy_display']}）" if cell.get('yoy_display') else ''))
            lines.append(f"| {row['name']} | " + ' | '.join(cells) + ' |')
        if analysis.get('quarterly'):
            quarters = sorted({p for row in analysis['quarterly'] for p in row['cells']})
            lines += ['', '## 分季度（带*为由累计数推算的单季数）', '', '| 指标 | ' + ' | '.join(quarters) + ' |', '|---|' + '---|' * len(quarters)]
            for row in analysis['quarterly']:
                lines.append(f"| {row['name']} | " + ' | '.join((row['cells'][p]['display'] + (f"（环比{row['cells'][p]['qoq_display']}）" if row['cells'][p].get('qoq_display') else '')) if p in row['cells'] else '' for p in quarters) + ' |')
        return '\n'.join(lines) + '\n'
    summary = payload['summary']
    totals = summary['totals']
    narrative = payload.get('narrative') or {}
    lines += [f"# 明镜研报核查 · {report.get('company', '')}", '',
              f"> {narrative.get('text') or template_narrative(payload)}" + ('（叙述由模型撰写，数字由宿主按槽位填入）' if narrative.get('source') == 'llm' else ''), '',
              f"运行 `{payload['run_id']}` · 研报《{report.get('name', '')}》· 研报日期 {report.get('report_date') or '未识别'} · 模式 {payload['mode']}", '',
              f"**已核实 {totals['verified']} · 有误 {totals['violated']} · 未能核实 {totals['unverifiable']} · 不判定 {totals['skipped']}**（按原文 {totals['occurrences']} 处逐处计数；去重断言 {summary.get('assertions', {}).get('total', totals['total'])} 条）", '',
              f"- 公司历史财务（法定/封闭定义）：判定覆盖率 {_pct(summary['core']['coverage'])}，弃权率 {_pct(summary['core']['abstention_rate'])}",
              f"- 全部历史财务（含开放定义指标）：判定覆盖率 {_pct(summary['historical']['coverage'])}，弃权率 {_pct(summary['historical']['abstention_rate'])}",
              f"- 含预测/估值内部勾稽：判定覆盖率 {_pct(summary['financial']['coverage'])}", '', '## 参考材料', '']
    for material in payload['materials'] or [{'name': '（无）', 'type_label': '', 'period': '', 'facts': 0, 'source': ''}]:
        lines.append(f"- {material['name']}｜{material.get('type_label', '')}｜{material.get('period', '')}｜事实 {material.get('facts', 0)} 条｜{material.get('source', '')}"
                     + (f"｜{material['excluded']}" if material.get('excluded') else ''))
    for tip in payload.get('advice', []):
        lines.append(f'> {tip}')
    if payload.get('gaps'):
        lines += ['', '## 补材建议（按缺口类型分组）', '', '| 类型 | 需要什么 | 可替代材料 | 影响条数 | 涉及 |', '|---|---|---|---|---|']
        for gap in payload['gaps']:
            lines.append(f"| {gap['kind_label']} | {gap['need']} | {gap['alternative']} | {gap['count']} | {'、'.join(gap['items'][:5])} |")
    if summary.get('assumptions'):
        lines += ['', f"## 推定与假设（{summary.get('presumed_results', 0)}条结论用到推定，逐条结果中已写明）", '']
        for row in summary['assumptions']:
            lines.append(f"- {row['label']}：{row['count']} 条")
    lines += ['', '## 分级统计', '', '| 分级 | 出现位置 | 已核实 | 有误 | 未能核实 | 不判定 |', '|---|---|---|---|---|---|']
    for group in summary['groups'].values():
        lines.append(f"| {group['label']} | {group['total']} | {group['verified']} | {group['violated']} | {group['unverifiable']} | {group['skipped']} |")
    names = {'fact': '事实', 'inference': '推论（预测与估算）', 'opinion': '观点（目标价等）'}
    lines += ['', '## 事实、推论与观点', '', '| 性质 | 出现位置 | 已核实 | 有误 | 未能核实 | 不判定 |', '|---|---|---|---|---|---|']
    for key, row in summary.get('modality', {}).items():
        lines.append(f"| {names.get(key, key)} | {row['total']} | {row['verified']} | {row['violated']} | {row['unverifiable']} | {row['skipped']} |")
    wrong = [item for item in payload['items'] if item['status'] == 'violated']
    lines += ['', f'## 有误陈述与修改建议（{len(wrong)}）', '']
    for item in wrong:
        lines += [f"### {item['cid']} · {item['error_type']} {item['error_label']} · {_subject(item)}", '', f"- 原文（第{item['page']}页）：{item['text'][:160]}",
                  f"- 原文数值：{item['num_text']}；应为：**{item['expected_display']}**（{item['definition_label']}）",
                  f"- 归因：{item['diagnosis'].get('detail', '')}", f"- 计算：{item['formula']}",
                  f"- 建议替换：{item['correction'].get('replacement_text', '')[:200]}", f"- 证据：{'、'.join(item['evidence'][:4])}", '']
    pending = [item for item in payload['items'] if item['status'] == 'unverifiable' and item['category'] in ('core', 'open_metric')]
    lines += [f'## 未能核实的历史财务陈述（{len(pending)}）', '']
    for item in pending[:80]:
        lines.append(f"- {item['cid']}｜第{item['page']}页｜{_subject(item)} {item['num_text']}｜{item['abstain_code']} {item['abstain_label']}：{item['reason']}" + (f"（{item['internal_check']}）" if item.get('internal_check') else ''))
    notable = [item for item in payload['items'] if item['status'] == 'verified' and item['advisories']]
    lines += ['', f'## 已核实但需提示（{len(notable)}）', '']
    for item in notable[:60]:
        lines.append(f"- {item['cid']}｜{_subject(item)} {item['num_text']}｜{'/'.join(item['advisories'])}：{'；'.join(item['notes'])[:160]}")
    return '\n'.join(lines) + '\n'


_CSS = """
body{font:14px/1.6 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#1c2430}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.muted{color:#6b7686}.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}.card{background:#fff;border-radius:10px;padding:14px 16px;border:1px solid #e3e7ee}
.card b{display:block;font-size:26px}.ok b{color:#0f8a5f}.bad b{color:#c7372f}.unk b{color:#b7791f}.skip b{color:#6b7686}
table{width:100%;border-collapse:collapse;background:#fff;border:1px solid #e3e7ee;border-radius:10px;overflow:hidden}th,td{padding:8px 10px;border-bottom:1px solid #eef1f5;text-align:left;vertical-align:top}
th{background:#f0f3f7;font-weight:600}.item{background:#fff;border:1px solid #e3e7ee;border-left:4px solid #c7372f;border-radius:8px;padding:12px 14px;margin:10px 0}
.item.unk{border-left-color:#b7791f}.item.ok{border-left-color:#0f8a5f}.tag{display:inline-block;border-radius:10px;padding:1px 8px;font-size:12px;background:#eef1f5;margin-right:6px}
.fix{background:#f1f8f4;border-radius:6px;padding:8px 10px;margin-top:6px}code{background:#f0f3f7;border-radius:4px;padding:1px 4px}
"""


def render_html(payload: dict) -> str:
    esc = html.escape
    report = payload['report']
    parts = [f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>明镜报告 · {esc(report.get("company", ""))}</title><style>{_CSS}</style><div class="wrap">']
    if payload['task'] == 'analysis':
        analysis = payload['analysis']
        parts.append(f'<h1>明镜财报分析 · {esc(report.get("company", ""))}</h1><div class="muted">运行 {esc(payload["run_id"])} · 材料 {len(payload["materials"])} 份 · 事实 {payload["facts_used"]} 条</div><p class="muted">{esc(analysis["note"])}</p>')
        if analysis.get('highlights'):
            parts.append('<h2>要点</h2><ul>' + ''.join(f'<li>{esc(line)}</li>' for line in analysis['highlights']) + '</ul>')
        summary = analysis.get('check_summary') or {}
        parts.append(f"<h2>勾稽检查（共{summary.get('total', 0)}项：一致{summary.get('passed', 0)}、不一致{summary.get('failed', 0)}、无法判定{summary.get('undetermined', 0)}）</h2>"
                     '<table><tr><th>结论</th><th>检查</th><th>期间</th><th>口径</th><th>说明</th></tr>'
                     + ''.join(f"<tr><td>{'一致' if c['ok'] is True else '<b style=\"color:#c7372f\">不一致</b>' if c['ok'] is False else '无法判定'}</td><td>{esc(c['name'])}</td><td>{esc(c['period'])}</td><td>{esc(c['scope'])}</td><td>{esc(c['detail'])}</td></tr>"
                               for c in analysis.get('checks', [])) + '</table><h2>异常信号</h2>')
        for signal in analysis['signals'] or []:
            parts.append(f'<div class="item {"" if signal["severity"] == "high" else "unk"}"><b>{esc(signal["title"])}</b> <span class="tag">{esc(signal["severity"])}</span><span class="tag">{esc(signal["nature"])}</span><div>{esc(signal["detail"])}</div></div>')
        if not analysis['signals']:
            parts.append('<p>未触发登记的异常信号规则。</p>')
        parts.append('<h2>主要指标</h2><table><tr><th>指标</th>' + ''.join(f'<th>{esc(p)}</th>' for p in analysis['periods']) + '</tr>')
        for row in analysis['metrics']:
            parts.append(f'<tr><td>{esc(row["name"])}</td>' + ''.join('<td>' + (esc(row['cells'][p]['display']) + (f' <span class="muted">{esc(row["cells"][p].get("yoy_display") or row["cells"][p].get("pp_display") or "")}</span>') if p in row['cells'] else '') + '</td>' for p in analysis['periods']) + '</tr>')
        parts.append('</table></div></html>')
        return ''.join(parts)
    summary, totals = payload['summary'], payload['summary']['totals']
    parts.append(f'<h1>明镜研报核查 · {esc(report.get("company", ""))}</h1><div class="muted">《{esc(report.get("name", ""))}》 · 研报日期 {esc(report.get("report_date") or "未识别")} · 运行 {esc(payload["run_id"])} · {esc(payload["mode"])} 模式</div>')
    parts.append(f'<div class="cards"><div class="card ok"><b>{totals["verified"]}</b>已核实</div><div class="card bad"><b>{totals["violated"]}</b>有误</div><div class="card unk"><b>{totals["unverifiable"]}</b>未能核实</div><div class="card skip"><b>{totals["skipped"]}</b>不判定</div></div>')
    narrative = payload.get('narrative') or {}
    parts.append(f'<p class="muted">{esc(narrative.get("text") or template_narrative(payload))}</p>')
    parts.append(f'<p>公司历史财务弃权率 <b>{_pct(summary["core"]["abstention_rate"])}</b>（法定/封闭定义）· <b>{_pct(summary["historical"]["abstention_rate"])}</b>（含开放定义指标）；含预测/估值内部勾稽的判定覆盖率 <b>{_pct(summary["financial"]["coverage"])}</b>。</p>')
    parts.append('<h2>参考材料</h2><table><tr><th>文件</th><th>类型</th><th>期间</th><th>事实</th><th>来源</th></tr>' + ''.join(f'<tr><td>{esc(m["name"])}</td><td>{esc(m.get("type_label", ""))}</td><td>{esc(m.get("period", ""))}</td><td>{m.get("facts", 0)}</td><td>{esc(m.get("source", ""))}</td></tr>' for m in payload['materials']) + '</table>')
    if payload.get('gaps'):
        parts.append('<h2>补材建议（按缺口类型分组）</h2><table><tr><th>类型</th><th>需要什么</th><th>可替代材料</th><th>影响条数</th><th>涉及</th></tr>'
                     + ''.join(f"<tr><td>{esc(g['kind_label'])}</td><td>{esc(g['need'])}</td><td>{esc(g['alternative'])}</td><td>{g['count']}</td><td>{esc('、'.join(g['items'][:5]))}</td></tr>" for g in payload['gaps']) + '</table>')
    if summary.get('assumptions'):
        parts.append(f"<h2>推定与假设</h2><p class=\"muted\">{summary.get('presumed_results', 0)} 条结论用到推定，逐条结果中已写明所用推定。</p><ul>"
                     + ''.join(f"<li>{esc(row['label'])}：{row['count']} 条</li>" for row in summary['assumptions']) + '</ul>')
    parts.append('<h2>分级统计</h2><table><tr><th>分级</th><th>出现位置</th><th>已核实</th><th>有误</th><th>未能核实</th><th>不判定</th></tr>' + ''.join(f'<tr><td>{esc(g["label"])}</td><td>{g["total"]}</td><td>{g["verified"]}</td><td>{g["violated"]}</td><td>{g["unverifiable"]}</td><td>{g["skipped"]}</td></tr>' for g in summary['groups'].values()) + '</table>')
    wrong = [item for item in payload['items'] if item['status'] == 'violated']
    parts.append(f'<h2>有误陈述与修改建议（{len(wrong)}）</h2>')
    for item in wrong:
        parts.append(f'<div class="item"><span class="tag">{esc(item["error_type"])} {esc(item["error_label"])}</span><b>{esc(_subject(item))}</b> <span class="muted">第{item["page"]}页 · {esc(item["cid"])}</span><div>{esc(item["text"][:200])}</div><div>原文 <code>{esc(item["num_text"])}</code> → 应为 <b>{esc(item["expected_display"])}</b>（{esc(item["definition_label"])}）。{esc(item["diagnosis"].get("detail", ""))}</div><div class="muted">{esc(item["formula"])}</div><div class="fix">建议替换：{esc(item["correction"].get("replacement_text", "")[:260])}</div></div>')
    pending = [item for item in payload['items'] if item['status'] == 'unverifiable' and item['category'] in ('core', 'open_metric')]
    parts.append(f'<h2>未能核实的历史财务陈述（{len(pending)}）</h2>')
    for item in pending[:120]:
        parts.append(f'<div class="item unk"><span class="tag">{esc(item["abstain_code"])} {esc(item["abstain_label"])}</span><b>{esc(_subject(item))} {esc(item["num_text"])}</b> <span class="muted">第{item["page"]}页</span><div>{esc(item["reason"])}</div></div>')
    parts.append('</div></html>')
    return ''.join(parts)


def corrections_csv(payload: dict) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['编号', '页码', '状态', '错误类型', '指标', '期间', '关系', '原文数值', '应为', '定义/口径', '归因', '建议替换文本', '证据', '原文'])
    for item in payload.get('items', []):
        if item['status'] in ('violated',) or item['advisories']:
            writer.writerow([item['cid'], item['page'], STATUS[item['status']], f"{item['error_type']} {item['error_label']}".strip(), item.get('metric', ''), item.get('period', ''),
                             RELATION.get(item.get('relation', ''), ''), item['num_text'], item['expected_display'], item['definition_label'],
                             item['diagnosis'].get('detail', '') or '；'.join(item['notes']), item['correction'].get('replacement_text', ''), ' '.join(item['evidence'][:4]), item['text'][:200]])
    return buffer.getvalue()


def write_outputs(payload: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / 'results.json').write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    (out_dir / 'report.md').write_text(render_markdown(payload), encoding='utf-8')
    (out_dir / 'report.html').write_text(render_html(payload), encoding='utf-8')
    if payload['task'] == 'check':
        (out_dir / 'corrections.csv').write_text('\ufeff' + corrections_csv(payload), encoding='utf-8')
