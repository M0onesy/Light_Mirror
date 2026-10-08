from __future__ import annotations
from collections import Counter
import html
import json
from pathlib import Path
import re

def _plain(value):
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)

def _unit(fact):
    from .presentation import standard_unit
    registered = standard_unit(str(fact.get('account', '')))
    if registered != '单位未知':
        return '比率' if registered == '无量纲比例' else registered
    if str(fact.get('account', '')).startswith('EPS'):
        return '元/股'
    if str(fact.get('account', '')).endswith('_YOY') or fact.get('display_unit') in {'%', '％', '百分点'} or fact.get('account') in {'GROSS_MARGIN', 'NET_MARGIN', 'DEBT_RATIO', 'ROE', 'CASH_CONTENT', 'NON_RECURRING_SHARE'}:
        return '比率'
    if fact.get('display_unit') in {'股', '万股'}:
        return '股'
    return '元'

def _local_link(value):
    value = str(value or '')
    return value if value and not re.match(r'^(?:[a-z][a-z\d+.-]*:|[/\\])', value, re.I) and '\\' not in value else None

def _evidence_lines(identifiers, payload):
    output = []
    for identifier in dict.fromkeys(identifiers or []):
        resource = payload.get('evidence_resources', {}).get(identifier) or {}
        link = _local_link(resource.get('image'))
        output.append(f'- 证据：`{identifier}`' + (f' [页级高亮](<{link}>)' if link else ''))
    return output


def _analysis_summary_lines(payload):
    """Count frozen analysis records without changing the claim summary."""
    analysis = payload.get('analysis') or {}
    metrics = [row for row in analysis.get('metrics', []) if isinstance(row, dict)]
    counts = Counter(row.get('status') for row in metrics)
    other = sum(count for status, count in counts.items() if status not in {'verified', 'violated', 'abstained'})
    lines = [f"指标计算项 {len(metrics)}；可用 {counts['verified']}；发现差异 {counts['violated']}；弃权 {counts['abstained']}。"]
    if other:
        lines.append(f'其他状态指标 {other}；需要查看对应记录，不能视作已完成判定。')
    codes = Counter(str(row.get('abstention_code') or '未登记') for row in metrics if row.get('status') == 'abstained')
    if codes:
        lines.append('指标弃权码：' + '；'.join(f'{code} {count}' for code, count in sorted(codes.items())) + '。')
    lines.append(f"财务复核提示 {len(analysis.get('signals', []))}；披露事件 {len(analysis.get('events', []))}。")
    if not metrics:
        lines.append('本任务尚无冻结分析指标，不能据此宣称分析已完成。')
    return lines


def _has_coverage(payload):
    coverage = payload.get('coverage') or {}
    if payload.get('task_type') == 'analysis':
        return bool(coverage.get('mentions') or coverage.get('financial_candidates'))
    return bool(coverage)

def _financial_coverage_lines(payload):
    stats = payload.get('coverage_statistics')
    if not stats or not stats['core']['financial_candidates']:
        return []
    core = stats['core']
    def percent(value):
        return '未知' if value is None else f'{value:.1%}'
    lines = ['## 金融核查范围与实际覆盖', '', stats['policy'], '',
             f"主范围 {core['financial_candidates']} 处；去重断言 {core['unique_assertions']} 条；核实独立事实 {stats['verified_fact_dimensions']} 项。",
             f"处理完成率 {percent(core['processing_completion_rate'])}；有效判定覆盖率 {percent(core['judgement_coverage'])}；弃权率 {percent(core['abstention_rate'])}；待处理 {core['pending']} 处。",
             '处理完成包含弃权，去重不减少验收分母。条件核查不代表原文口径已获证实；专业准确性和全文召回率仍需独立金标。', '']
    if 'source_supported_judgement_coverage' in core:
        lines.extend([f"原文证据支持的判定覆盖率 {percent(core['source_supported_judgement_coverage'])}；条件判定 {core['conditional_judged']} 处；原文未解决 {stats['unconditional_unresolved']} 处。", ''])
    lines.extend(['| 范围 | 原文出现 | 核实 | 违背 | 弃权 | 条件判定 | 待处理 |',
                  '|---|---:|---:|---:|---:|---:|---:|'])
    for group in stats['groups'].values():
        counts = group['counts']
        lines.append(f"| {group['label']} | {group['occurrences']} | {counts['verified']} | {counts['violated']} | {counts['abstained']} | {group.get('conditional_judged', 0)} | {group['pending']} |")
    return lines + ['']


def _coverage_lines(payload):
    coverage = payload.get('coverage') or {}
    if not _has_coverage(payload):
        return []
    lines = ['## 原文覆盖账本', '',
             f"任务状态：{'partial（尚未处理完整）' if payload.get('partial', coverage.get('partial')) else '当前候选提及处理完成'}。",
             f"金融候选提及 {coverage.get('financial_candidates', '未知')}；已处理 {coverage.get('processed', '未知')}；状态计数 {_plain(coverage.get('counts', {}))}。",
             f"召回评测：{coverage.get('recall', 'unknown_requires_independent_gold')}。账本处理完成不代表全文金融陈述召回已验收。", '']
    for entry in coverage.get('mentions', []):
        lines.extend([f"- {entry.get('id', '')} · {entry.get('status', '')} · 原文区间 [{entry.get('start', '')},{entry.get('end', '')}) · 页 {entry.get('page_id') or '未知'}",
                      f"  原文：{html.escape(str(entry.get('text', '')))}；原因：{entry.get('reason', '')}" +
                      (f"；弃权码：{entry['abstention_code']}" if entry.get('abstention_code') else '')])
    return lines + ['']


def _source_gap_lines(payload):
    rows = payload.get('source_gaps') or []
    if not rows:
        return []
    lines = ['## 已取得来源中的定义缺口', '', '这些原文记录尚不足以进入事实库，不属于核实结论，也不减少主范围弃权。', '']
    for row in rows:
        lines.append(f"- {html.escape(str(row['id']))} · {html.escape(str(row.get('table_label') or row.get('account') or ''))} · {html.escape(str(row.get('period') or ''))} · {html.escape(str(row.get('dimension_id') or ''))}")
        lines.append('  需补依据：'+html.escape(str(row.get('needed_method') or row.get('reason') or '')))
        lines.append('  截止日前已披露：'+str(bool(row.get('public_at_cutoff')))+'；来源：'+str(row.get('report_id') or ''))
        lines.extend(_evidence_lines(row.get('evidence'), payload))
    return lines + ['']

def _analysis_lines(payload):
    from .presentation import display_metric_value
    analysis = payload.get('analysis') or {}
    if not analysis:
        return []
    lines = ['## 财报分析', '', f"公司：{analysis.get('company', '')}；口径：{analysis.get('scope', '')}；期间：{_plain(analysis.get('periods', []))}。", '']
    for metric in analysis.get('metrics', []):
        lines.extend([f"### {metric.get('period', '')} · {metric.get('metric', '')} · {metric.get('status', '')}", ''])
        lines.append(f"- 数值：{display_metric_value(metric)}")
        for key, label in [('formula', '公式'), ('abstention_code', '弃权码'), ('reason', '原因'),
                           ('disclosed_value', '披露值'), ('match_disclosure', '重算与披露一致'),
                           ('expected_interval', '来源传播区间'), ('source_precision', '来源精度'), ('precision_note', '披露精度提示'),
                           ('display_precision_note', '展示精度说明'),
                           ('rule_id', '授权规则'), ('rule_ids', '规则版本')]:
            if metric.get(key) is not None:
                suffix = ' ' + _unit({'account': metric.get('metric')}) if key in {'value', 'disclosed_value'} else ''
                lines.append(f'- {label}：{_plain(metric[key])}{suffix}')
        for operand in metric.get('operands', []):
            lines.append(f"- 操作数：{operand.get('fact_id', '')} / {operand.get('period', '')} / {operand.get('scope', '')} / {operand.get('value', '')} {_unit(operand)}")
        lines.extend(_evidence_lines(metric.get('evidence'), payload))
        lines.append('')
    for kind, label in [('signals', '财务复核提示'), ('events', '披露事件')]:
        for event in analysis.get(kind, []):
            lines.extend([f"### {label} · {event.get('code', '')} · {event.get('kind', '')}", '',
                          f"- 信息：{_plain({k: v for k, v in event.items() if k != 'evidence'})}"])
            lines.extend(_evidence_lines(event.get('evidence'), payload))
            lines.append('')
    if analysis.get('parameters'):
        lines.extend([f"分析参数：{_plain(analysis['parameters'])}。团队参数只触发财务复核提示。", ''])
    return lines

def markdown_report(payload: dict) -> str:
    summary = payload.get('summary', {})
    model = payload.get('model', {})
    is_analysis = payload.get('task_type') == 'analysis'
    core = (payload.get('coverage_statistics') or {}).get('core') or {}
    if not is_analysis and core.get('financial_candidates'):
        summary = core['counts']
    title = '明镜财报分析报告' if is_analysis else '明镜核查报告'
    overview = _analysis_summary_lines(payload) if is_analysis else [
        f"核实 {summary.get('verified',0)}；违背 {summary.get('violated',0)}；弃权 {summary.get('abstained',0)}。"]
    description = ('本报告展示指定公司、期间与口径下的财报指标、财务复核提示和披露事件。'
                   '弃权指标需要补充材料或满足授权公式前提。指标计数不代表全文陈述召回或质量验收通过。'
                   if is_analysis else '本报告覆盖从给定草稿中识别且定位的陈述。弃权条目需要补充材料或确认口径。')
    lines = ['# ' + title, '', f"运行 ID：`{payload.get('run_id','')}`", '', *overview,
             f"语义层：{model.get('status','not_requested')}；数字结论由确定性内核生成。", '', description, '']
    if model.get('error'):
        lines.extend([f"模型降级原因：{model['error']}", ''])
    if payload.get('scope_declaration'):
        lines.extend([f"本轮口径：{html.escape(str(payload['scope_declaration']))}。来源为用户补充声明，不能据此声称原文明示该口径。", ''])
    if payload.get('scope_clarifications'):
        lines.extend(['部分陈述按用户选定组补充口径，属于条件核查；来源表转述与其他原文位置保持独立，不能据此声称原文明示该口径。', ''])
    if payload.get('scope_declaration') or payload.get('scope_clarifications'):
        for resource in payload.get('clarifications', []):
            lines.append(f"- 澄清材料：{_plain(resource.get('resource_id', ''))}；SHA-256：{_plain(resource.get('sha256', ''))}")
        lines.append('')
    if payload.get('partial'):
        partial = ('本次任务为 partial：部分任务阶段尚未完成，请查看任务状态与已有分析结果。'
                   if is_analysis else '本次任务为 partial：仍有候选提及或表格单元格未处理，请查看覆盖账本。')
        lines.extend([partial, ''])
    lines.extend(_financial_coverage_lines(payload))
    lines.extend(_coverage_lines(payload))
    lines.extend(_source_gap_lines(payload))
    if payload.get('agent_report'):
        lines.extend(['## Agent 槽位报告', '', html.escape(str(payload['agent_report'])), ''])
    lines.extend(_analysis_lines(payload))
    for item in payload.get('results', []):
        lines.extend([f"## {item['claim_id']} · {item['status']}", '', f"原文：{html.escape(str(item.get('claim_text','')))}", ''])
        for field, label in [('error_type','错误类型'),('abstention_code','弃权码'),('diagnostics','诊断码'),('advisories','提示'),('rule_ids','规则 ID'),('reason','原因'),('observed','观测值'),('expected','期望值'),('display_value','显示值'),('tolerance','容差'),('formula','公式'),('claim_interval','陈述区间'),('expected_interval','来源区间'),('source_precision','来源精度'),('precision_note','披露精度提示'),('verification_basis','验证依据'),('diagnostic_hypotheses','待证实的错误线索')]:
            if item.get(field) is not None and item.get(field) not in ([], ''):
                lines.append(f"- {label}：{_plain(item[field])}")
        if item.get('source_dimensions'):
            lines.append('- 来源口径与方法：' + _plain(item['source_dimensions']))
        for blocker in item.get('blockers', []):
            lines.append(f"- 待补依据：{blocker.get('code', '')} {blocker.get('account', '')} {blocker.get('period', '')} · {html.escape(str(blocker.get('reason', '')))}")
        for fact in item.get('operands', []):
            lines.append(f"- 操作数：{fact.get('fact_id','')} / {fact.get('period','')} / {fact.get('scope','')} / {fact.get('value','')} {_unit(fact)}")
        lines.extend(_evidence_lines(item.get('evidence'), payload))
        if item.get('basis'):
            lines.append('- 依据：'+'；'.join(item['basis']))
        correction = item.get('correction') or {}
        if correction:
            lines.extend(['', '修改建议：', ''])
            for key, label in [('correct_value','正确值'),('source','来源与证据'),('calculation','计算式'),('basis','规则依据'),('replacement_text','可直接替换的文本')]:
                lines.append(f"- {label}：{html.escape(str(correction.get(key,'')))}")
        lines.append('')
    return '\n'.join(lines)

def html_report(payload: dict) -> str:
    from .presentation import metric_display_metadata
    # Resolve legacy records with the same Decimal presentation used in Markdown.
    # Browser code consumes these strings; it never rounds financial values.
    metric_displays = json.dumps([metric_display_metadata(record)
        for record in (payload.get('analysis') or {}).get('metrics', [])], ensure_ascii=False)
    from .metrics import METRIC_SPECS
    # The UI displays the same registered units as the Python report.
    metric_units = json.dumps({code: _unit({'account': code}) for code in METRIC_SPECS}, ensure_ascii=False)
    data = json.dumps(payload,ensure_ascii=False)
    for raw, safe in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        data = data.replace(raw, safe)
        metric_displays = metric_displays.replace(raw, safe)
    analysis_summary = json.dumps(' '.join(_analysis_summary_lines(payload)), ensure_ascii=False)
    for raw, safe in [('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        analysis_summary = analysis_summary.replace(raw, safe)
    title = '明镜财报分析报告' if payload.get('task_type') == 'analysis' else '明镜核查报告'
    return r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'''+title+r'''</title>
<style>body{margin:0;background:#f3f5f8;color:#182638;font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif}header{background:#143c5d;color:white;padding:16px 24px}main{display:grid;grid-template-columns:1.1fr 1fr 1fr;gap:12px;padding:12px;height:calc(100vh - 112px)}section{background:white;padding:16px;border:1px solid #dce3eb;border-radius:6px;overflow:auto}pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fa;padding:10px}.source{white-space:pre-wrap;word-break:break-word}.card{border-left:4px solid #ad7c19;padding:10px;background:#fafbfc;cursor:pointer;margin-bottom:9px}.verified{border-color:#168454;background:#e9f8f0}.violated{border-color:#c53832;background:#fff0ef}.abstained{border-color:#ad7c19;background:#fff8e4}mark{padding:2px;cursor:pointer;border-bottom:2px solid}img{max-width:100%}small{color:#62758c}button{padding:6px;cursor:pointer}@media(max-width:850px){main{display:block;height:auto}section{max-height:75vh;margin-bottom:10px}}</style></head><body>
<header><b>明镜 MingJing</b><div id="summary"></div><div id="model"></div><div id="coverage-summary"></div></header><main><section><h2 id="source-heading">原文与定位</h2><div id="source" class="source"></div></section><section><h2 id="results-heading">陈述核查</h2><div id="list"></div><details id="coverage-panel"><summary>原文覆盖账本</summary><div id="coverage-ledger"></div></details><details id="analysis-panel"><summary>财报分析</summary><div id="analysis"></div></details><details id="agent-panel"><summary>Agent 槽位报告</summary><pre id="agent-report"></pre></details></section><section><h2>证据、公式与修改建议</h2><div id="detail">点击陈述或原文高亮查看详情。</div></section></main><script>
const D='''+data+r''';
const analysisSummary='''+analysis_summary+r''';
const metricDisplays='''+metric_displays+r''';
const metricUnits='''+metric_units+r''';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stringify=v=>typeof v==='object'?JSON.stringify(v):String(v??'');
const statuses={verified:'核实',violated:'违背',abstained:'弃权',pending:'未处理',judged:'已判定',out_of_scope:'范围外',rule_excluded:'规则排除'};
const results=D.results||[], C=D.coverage||{};
const isAnalysis=D.task_type==='analysis', hasCoverage=isAnalysis?Boolean((C.mentions||[]).length||C.financial_candidates):Boolean(D.coverage);
document.getElementById('summary').textContent=(D.run_id||'')+' · '+(isAnalysis?analysisSummary:Object.entries(D.summary||{}).map(([k,v])=>(statuses[k]||k)+' '+v).join(' / '));
document.getElementById('source-heading').textContent=isAnalysis?'材料与任务说明':'原文与定位';
document.getElementById('results-heading').textContent=isAnalysis?'财报分析':'陈述核查';
document.getElementById('model').textContent='语义层：'+(D.model?.status||'not_requested')+(D.model?.error?' · 降级原因：'+D.model.error:'')+' · 数字结论由确定性内核生成';
if(D.scope_declaration||(D.scope_clarifications||[]).length){const note=document.createElement('div');note.textContent=D.scope_declaration?'本轮口径：'+D.scope_declaration+'；来源为用户补充声明，不能据此声称原文明示该口径。':'部分陈述按选定组补充口径，属于条件核查；不能替代原文口径证据。';document.querySelector('header').appendChild(note);for(const resource of D.clarifications||[]){const proof=document.createElement('div');proof.textContent='澄清材料：'+(resource.resource_id||'')+'；SHA-256：'+(resource.sha256||'');document.querySelector('header').appendChild(proof);}}
document.getElementById('coverage-summary').textContent=(D.partial||C.partial?'任务尚未完整完成（partial）':'当前候选提及处理完成')+' · 金融候选 '+(C.financial_candidates??'未知')+' / 已处理 '+(C.processed??'未知')+' · 全文召回需独立真值评测';
if(isAnalysis&&!hasCoverage){document.getElementById('coverage-summary').textContent=(D.partial?'部分任务阶段尚未完成（partial）':(D.analysis?.metrics||[]).length?'已展示冻结分析指标':'尚无冻结分析指标，不能据此宣称分析完成')+' · 指标计数不代表全文陈述召回或质量验收通过';}
const byId=Object.fromEntries(results.map(x=>[x.claim_id,x]));
const localLink=v=>typeof v==='string'&&v&&!/^(?:[a-z][a-z\d+.-]*:|[/\\])/i.test(v)&&!v.includes('\\')?v:null;
function evidence(ids){return [...new Set(ids||[])].map(e=>{const r=D.evidence_resources?.[e], link=localLink(r?.image);return '<p><code>'+esc(e)+'</code></p>'+(link?'<a target="_blank" rel="noopener" href="'+esc(link)+'"><img src="'+esc(link)+'" alt="PDF原页高亮"></a>':'<small>请在Web界面反查此证据。</small>')}).join('');}
function unit(f){return metricUnits[f.account]||(String(f.account||'').startsWith('EPS')?'元/股':String(f.account||'').endsWith('_YOY')||['%','％','百分点'].includes(f.display_unit)||['GROSS_MARGIN','NET_MARGIN','DEBT_RATIO','ROE','CASH_CONTENT','NON_RECURRING_SHARE'].includes(f.account)?'比率':['股','万股'].includes(f.display_unit)?'股':'元');}
function operands(rows){return (rows||[]).map(f=>'<p>'+esc(f.fact_id)+'<br><small>'+esc([f.company,f.period,f.scope,f.nr,(f.value??'')+' '+unit(f)].join(' / '))+'</small></p>').join('');}
function codes(x){return [['error_type','错误类型'],['abstention_code','弃权码'],['diagnostics','诊断码'],['advisories','提示'],['rule_ids','规则 ID']].filter(([k])=>x[k]!==undefined&&x[k]!==null&&stringify(x[k])!=='[]').map(([k,label])=>'<p><b>'+label+'：</b>'+esc(stringify(x[k]))+'</p>').join('');}
function precision(x){return [['display_precision_note','展示精度说明'],['precision_note','披露精度提示'],['claim_interval','陈述区间'],['expected_interval','来源区间'],['value_interval','来源传播区间'],['source_precision','来源精度'],['verification_basis','验证依据']].filter(([k])=>x[k]!==undefined&&x[k]!==null&&stringify(x[k])!=='[]').map(([k,label])=>'<p><b>'+label+'：</b>'+esc(stringify(x[k]))+'</p>').join('');}
function sourceSemantics(x){return (x.source_dimensions?'<h3>来源口径与方法</h3><pre>'+esc(stringify(x.source_dimensions))+'</pre>':'')+(x.blockers?.length?'<h3>全部阻断原因</h3>'+x.blockers.map(b=>'<p><b>'+esc(b.code)+' · '+esc(b.kind)+'</b><br>'+esc(b.reason)+'<br><small>'+esc(stringify(b))+'</small></p>').join(''):'');}
function show(id){const x=byId[id];if(!x)return;document.getElementById('detail').innerHTML='<b>'+esc(id)+' · '+esc(statuses[x.status]||x.status)+'</b>'+codes(x)+'<p>'+esc(x.reason||'')+'</p><p>期望值：'+esc(x.display_value??x.expected??'—')+'</p><p>观测值：'+esc(x.observed??'—')+'；容差：'+esc(x.tolerance??'—')+'</p><pre>'+esc(x.formula||'')+'</pre>'+precision(x)+sourceSemantics(x)+'<p>规则依据：'+esc((x.basis||[]).join('；'))+'</p>'+operands(x.operands)+evidence(x.evidence)+(x.correction?'<h3>修改建议</h3>'+[['correct_value','正确值'],['source','来源与证据'],['calculation','计算式'],['basis','规则依据'],['replacement_text','可替换文本']].map(([k,v])=>'<p><b>'+v+'</b><br>'+esc(x.correction[k])+'</p>').join(''):'');}
document.getElementById('list').innerHTML=results.map(x=>'<div class="card '+esc(x.status)+'" data-id="'+esc(x.claim_id)+'"><b>'+esc(x.claim_id)+' · '+esc(statuses[x.status]||x.status)+'</b><p>'+esc(x.claim_text)+'</p><small>'+esc([x.error_type,x.abstention_code,...(x.advisories||[]),x.reason].filter(Boolean).join(' · '))+'</small></div>').join('');
document.getElementById('coverage-panel').hidden=!hasCoverage;
document.getElementById('coverage-ledger').innerHTML='<p>状态计数：'+esc(stringify(C.counts||{}))+'</p><p>召回评测：'+esc(C.recall||'unknown_requires_independent_gold')+'</p><table><thead><tr><th>原文及地址</th><th>处理状态</th></tr></thead><tbody>'+(C.mentions||[]).map(m=>'<tr><td>'+esc(m.text)+'<br><small>'+esc(m.id)+' · ['+esc(m.start)+','+esc(m.end)+') · '+esc(m.page_id||'未知页')+'</small></td><td>'+esc(statuses[m.status]||m.status)+'<br>'+esc(m.reason||'')+(m.abstention_code?'<br>'+esc(m.abstention_code):'')+'</td></tr>').join('')+'</tbody></table>';
if(D.source_gaps?.length){document.getElementById('coverage-ledger').innerHTML+='<details><summary>来源表口径与方法登记</summary><p>记录保留尚需确认的会计口径或方法；明确引用且已锁定表身份的转述核查见结果，未定口径不能用于跨表计算。登记本身不计作核实。</p>'+D.source_gaps.map(g=>'<p><b>'+esc(g.id)+' · '+esc(g.table_label||g.account)+' · '+esc(g.period)+' · '+esc(g.dimension_id)+'</b><br>'+esc(g.needed_method||g.reason)+'<br>截止日前已披露：'+esc(g.public_at_cutoff)+'<br>'+esc(g.report_id)+'</p>').join('')+'</details>';}
const A={...(D.analysis||{}),metrics:(D.analysis?.metrics||[]).map((m,i)=>({...m,...metricDisplays[i]}))};
document.getElementById('analysis-panel').hidden=!D.analysis;
document.getElementById('analysis-panel').open=isAnalysis;
if(isAnalysis&&!results.length)document.getElementById('detail').textContent='展开财报指标查看公式、操作数与证据；复核提示及披露事件位于分析列表下方。';
document.getElementById('analysis').innerHTML='<p>'+esc([A.company,A.scope,stringify(A.periods||[])].filter(Boolean).join(' / '))+'</p>'+(A.metrics||[]).map(m=>'<details><summary>'+esc([m.period,m.metric,statuses[m.status]||m.status].join(' · '))+'</summary><p>数值：'+esc(m.display_value??(m.value!==null&&m.value!==undefined?m.value+' '+unit({account:m.metric}):'—'))+'</p><pre>'+esc(m.formula||'')+'</pre>'+codes(m)+precision(m)+'<p>'+esc(m.reason||'')+'</p>'+(m.disclosed_value!==undefined?'<p>披露值：'+esc(m.disclosed_value)+'；重算与披露一致：'+esc(m.match_disclosure)+'</p>':'')+operands(m.operands)+evidence(m.evidence)+'</details>').join('')+[['signals','财务复核提示'],['events','披露事件']].map(([key,label])=>(A[key]||[]).map(s=>'<details><summary>'+label+' · '+esc([s.code,s.kind].filter(Boolean).join(' · '))+'</summary><pre>'+esc(JSON.stringify(Object.fromEntries(Object.entries(s).filter(([k])=>k!=='evidence')),null,2))+'</pre>'+evidence(s.evidence)+'</details>').join('')).join('')+(A.parameters?'<p>分析参数：'+esc(stringify(A.parameters))+'。团队参数只触发财务复核提示。</p>':'');
document.getElementById('agent-panel').hidden=!D.agent_report;
document.getElementById('agent-report').textContent=D.agent_report||'';
const text=D.draft_text||results.map(x=>x.claim_text).join('\n'),points=Array.from(text);let cursor=0,source='';
results.filter(x=>x.source_span).sort((a,b)=>a.source_span.start-b.source_span.start).forEach(x=>{const {start,end}=x.source_span;if(!Number.isInteger(start)||!Number.isInteger(end)||start<cursor||end<=start||end>points.length)return;source+=esc(points.slice(cursor,start).join(''))+'<mark class="'+esc(x.status)+'" data-id="'+esc(x.claim_id)+'">'+esc(points.slice(start,end).join(''))+'</mark>';cursor=end});
document.getElementById('source').innerHTML=source+esc(points.slice(cursor).join(''));document.querySelectorAll('[data-id]').forEach(el=>el.onclick=()=>show(el.dataset.id));
const CS=D.coverage_statistics;
if(CS&&!isAnalysis&&CS.core?.financial_candidates){
const core=CS.core,pct=value=>value===null?'未知':(value*100).toFixed(1)+'%';
document.getElementById('summary').textContent=(D.run_id||'')+' · 公司历史财务 '+['verified','violated','abstained'].map(k=>statuses[k]+' '+(core.counts[k]||0)).join(' / ');
document.getElementById('coverage-summary').textContent='主范围 '+core.financial_candidates+' 处 · 去重断言 '+core.unique_assertions+' 条 · 处理完成率 '+pct(core.processing_completion_rate)+' · 有效判定覆盖率 '+pct(core.judgement_coverage)+' · 弃权率 '+pct(core.abstention_rate)+' · 待处理 '+core.pending;
if(core.source_supported_judgement_coverage!==undefined){document.getElementById('coverage-summary').textContent+=' · 原文证据支持 '+pct(core.source_supported_judgement_coverage)+' · 条件判定 '+core.conditional_judged+' · 原文未解决 '+CS.unconditional_unresolved;}
const pane=document.createElement('details');pane.innerHTML='<summary>全部核查范围与未核实内容</summary><p>'+esc(CS.policy)+'</p><p>处理完成包含弃权；去重不减少验收分母。全文保留 '+esc(CS.occurrences)+' 个出现位置；专业准确性和召回率需独立金标。</p><table><thead><tr><th>范围</th><th>出现</th><th>核实</th><th>违背</th><th>弃权</th><th>待处理</th></tr></thead><tbody>'+Object.values(CS.groups).map(g=>'<tr><td>'+esc(g.label)+'</td><td>'+esc(g.occurrences)+'</td><td>'+esc(g.counts.verified)+'</td><td>'+esc(g.counts.violated)+'</td><td>'+esc(g.counts.abstained)+'</td><td>'+esc(g.pending)+'</td></tr>').join('')+'</tbody></table>';document.getElementById('list').before(pane);
}
</script></body></html>'''

def write_reports(payload: dict, out: str | Path) -> None:
    root = Path(out)
    (root/'report.md').write_text(markdown_report(payload), encoding='utf-8')
    (root/'report.html').write_text(html_report(payload), encoding='utf-8')
