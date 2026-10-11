'use strict';
/* 明镜直传模式前端：无构建步骤的原生 JS。所有数据来自本机 /api/kb 与 /api/smart 接口。 */
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => Array.from(root.querySelectorAll(s));
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const STATUS = { verified: ['已核实', 'ok'], violated: ['有误', 'bad'], unverifiable: ['未能核实', 'unk'], skipped: ['不判定', 'skip'] };
const BASIS = { disclosed: '披露值比对', derived: '公式重算', internal: '研报内部勾稽', identity: '表内勾稽', text: '原文佐证', '': '' };
const REL = { value: '', yoy: '同比', qoq: '环比', cagr: 'CAGR', pp: '百分点变化', share: '占比' };
const CAT = { core: '历史财务（法定/封闭定义）', open_metric: '历史财务（开放定义）', forecast: '预测与估值', template: '模板聚合项', market: '行情数据', external: '行业/第三方', other_fact: '其他事实' };
const MODALITY = { fact: '事实', inference: '推论（预测与估算）', opinion: '观点（目标价等）' };
const TYPE = { research_report: '研究报告', annual_report: '年度报告', semiannual_report: '半年度报告', quarterly_report: '季度报告', prospectus: '招股说明书', facts_json: '结构化事实', other: '其他材料', unknown: '未识别' };
const state = { queue: [], run: null, poll: null, batch: null, item: null, results: null, kb: [], session: null, sessionPoll: null, provider: null };
let token = sessionStorage.getItem('mingjing-token') || '';

function toast(msg, ms = 2600) { const t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), ms); }
async function api(path, opt = {}) {
  const headers = Object.assign({}, opt.headers || {}, token ? { Authorization: 'Bearer ' + token } : {});
  const res = await fetch(path, Object.assign({ cache: 'no-store' }, opt, { headers }));
  if (res.status === 401) {
    const value = prompt('本机服务启用了访问令牌，请输入令牌：');
    if (value) { token = value; sessionStorage.setItem('mingjing-token', value); return api(path, opt); }
  }
  const type = res.headers.get('Content-Type') || '';
  const data = type.includes('json') ? await res.json() : await res.text();
  if (!res.ok) throw new Error((data && data.error) || `请求失败（${res.status}）`);
  return data;
}
async function download(path, name) {
  const res = await fetch(path, { headers: token ? { Authorization: 'Bearer ' + token } : {} });
  if (!res.ok) { toast('下载失败'); return; }
  const url = URL.createObjectURL(await res.blob()); const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 2000);
}
function fmtTime(t) { if (!t) return ''; const d = new Date(t * 1000); return d.toLocaleString('zh-CN', { hour12: false }); }
function pct(v) { return v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%'; }
function periodLabel(p) { if (!p) return ''; const k = { Y: '年', H1: '年上半年', H2: '年下半年', Q1: '年一季度', Q2: '年二季度', Q3: '年三季度', Q4: '年四季度', '9M': '年前三季度' }; return p.slice(0, 4) + (k[p.slice(4)] || p.slice(4)); }
const UNIT_SCALE = { '元': 1, '千元': 1e3, '万元': 1e4, '百万元': 1e6, '亿元': 1e8, '%': 0.01, '股': 1, '万股': 1e4, '亿股': 1e8, '倍': 1, '元/股': 1 };
function operandValue(o) {
  return MingJingDecimal.format(o.value, o.unit || '', o.decimals ?? 2);
}
function intervalText(v) {
  if (!v || v.lower === undefined || v.upper === undefined) return '未提供';
  return `${v.lower_closed ? '[' : '('}${v.lower}, ${v.upper}${v.upper_closed ? ']' : ')'}`;
}
function tag(status) { const [label, cls] = STATUS[status] || [status, 'skip']; return `<span class="tag ${cls}">${label}</span>`; }

/* —— 视图切换 —— */
function show(view) {
  $$('#nav button').forEach((b) => b.classList.toggle('active', b.dataset.view === view));
  $$('.view').forEach((v) => v.classList.toggle('active', v.id === 'view-' + view));
  if (view === 'kb') loadKb();
  if (view === 'history') loadHistory();
  if (view === 'settings') loadSettings();
  history.replaceState(null, '', '#' + view);
}
$$('#nav button').forEach((b) => b.addEventListener('click', () => show(b.dataset.view)));
document.addEventListener('click', (e) => { const a = e.target.closest('[data-goto]'); if (a) { e.preventDefault(); show(a.dataset.goto); } });

/* —— 上传与队列 —— */
const drop = $('#drop'), fileInput = $('#file');
drop.addEventListener('click', () => fileInput.click());
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') fileInput.click(); });
['dragenter', 'dragover'].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => uploadFiles(e.dataTransfer.files));
fileInput.addEventListener('change', () => { uploadFiles(fileInput.files); fileInput.value = ''; });

async function uploadFiles(files) {
  for (const file of Array.from(files)) {
    const row = { name: file.name, status: 'uploading', record: null };
    state.queue.push(row); renderQueue();
    try {
      row.record = await api('/api/kb/upload?name=' + encodeURIComponent(file.name), { method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' } });
      row.status = row.record.duplicate ? 'duplicate' : (row.record.status || 'ready');
    } catch (err) { row.status = 'error'; row.error = err.message; }
    renderQueue();
  }
}
function addRecords(records) {
  for (const record of records) {
    if (state.queue.some((q) => q.record && q.record.doc_id === record.doc_id)) continue;
    state.queue.push({ name: record.name, status: 'kb', record });
  }
  renderQueue();
}
function renderQueue() {
  $('#queuePanel').hidden = state.queue.length === 0;
  const body = $('#queue tbody');
  body.innerHTML = state.queue.map((q, i) => {
    const r = q.record || {};
    const st = { uploading: '<span class="tag info">上传解析中…</span>', ready: '<span class="tag ok">已入库</span>', duplicate: '<span class="tag info">已在库中（去重）</span>',
      kb: '<span class="tag info">来自知识库</span>', error: `<span class="tag bad">失败</span> <span class="small muted">${esc(q.error)}</span>`,
      no_text: '<span class="tag unk">无文本层</span>', failed: '<span class="tag bad">解析失败</span>' }[q.status] || esc(q.status);
    return `<tr><td>${esc(q.name)}</td><td>${r.type_label ? `<span class="tag ${r.doc_type === 'research_report' ? 'info' : 'ok'}">${esc(r.type_label)}</span>` : ''}</td>
      <td>${esc(r.company || '')}${r.stock_code ? ` <span class="muted small">${esc(r.stock_code)}</span>` : ''}</td>
      <td>${esc(r.period ? periodLabel(r.period) : '')} <span class="muted small">${esc(r.report_date || '')}</span></td><td>${st}</td>
      <td><button class="link" data-remove="${i}">移除</button>${r.doc_id ? ` <button class="link" data-edit="${r.doc_id}">修正</button>` : ''}</td></tr>`;
  }).join('');
  const ready = state.queue.filter((q) => q.record && ['ready', 'duplicate', 'kb'].includes(q.status));
  $('#start').disabled = ready.length === 0;
  const reports = ready.filter((q) => q.record.doc_type === 'research_report').length;
  const filings = ready.length - reports;
  $('#startHint').textContent = ready.length ? `将处理 ${ready.length} 份材料：研报 ${reports} 份、财报/其他 ${filings} 份。${reports ? '每份研报单独出具核查结果。' : '同一公司的财报合并分析。'}` : '请先放入文件';
}
$('#queue').addEventListener('click', (e) => {
  const rm = e.target.closest('[data-remove]'); if (rm) { state.queue.splice(+rm.dataset.remove, 1); renderQueue(); return; }
  const ed = e.target.closest('[data-edit]'); if (ed) editDoc(ed.dataset.edit);
});
$('#clearQueue').addEventListener('click', () => { state.queue = []; renderQueue(); });
$('#addFromKb').addEventListener('click', async () => {
  const data = await api('/api/kb');
  const rows = data.docs.map((d) => `<label class="check" style="flex-direction:row;align-items:center;gap:8px"><input type="checkbox" value="${d.doc_id}"> ${esc(d.name)} <span class="muted small">${esc(d.type_label || '')} ${esc(d.company || '')} ${esc(d.period || '')}</span></label>`).join('') || '<p class="muted">知识库为空</p>';
  openModal(`<h3>从知识库选择</h3><div style="max-height:50vh;overflow:auto">${rows}</div><div class="actions"><button class="primary" id="mOk">添加</button><button class="link" id="mCancel">取消</button></div>`);
  $('#mOk').onclick = () => { const ids = $$('#modalCard input:checked').map((i) => i.value); addRecords(data.docs.filter((d) => ids.includes(d.doc_id))); closeModal(); };
  $('#mCancel').onclick = closeModal;
});

$('#runDemo').addEventListener('click', async () => {
  try {
    const run = await api('/api/smart/demo', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } });
    state.run = run.run_id; $('#progressPanel').hidden = false; toast('已载入随包虚构样例，开始核查'); watch(run.run_id);
  } catch (err) { toast(err.message, 4000); }
});
function openModal(html) { $('#modalCard').innerHTML = html; $('#modal').hidden = false; }
function closeModal() { $('#modal').hidden = true; }
$('#modal').addEventListener('click', (e) => { if (e.target.id === 'modal') closeModal(); });
async function editDoc(id) {
  const r = await api('/api/kb/' + id);
  openModal(`<h3>修正识别结果</h3><p class="muted small">${esc(r.name)}</p>
    <label>文档类型<select id="eType">${Object.entries(TYPE).map(([k, v]) => `<option value="${k}" ${k === r.doc_type ? 'selected' : ''}>${v}</option>`).join('')}</select></label>
    <label>公司简称<input id="eCompany" value="${esc(r.company || '')}"></label><label>证券代码<input id="eCode" value="${esc(r.stock_code || '')}"></label>
    <label>报告期（如 2023Y / 2024H1）<input id="ePeriod" value="${esc(r.period || '')}"></label><label>日期<input id="eDate" type="date" value="${esc(r.report_date || '')}"></label>
    <div class="actions"><button class="primary" id="mSave">保存</button><button class="link" id="mCancel">取消</button></div>`);
  $('#mCancel').onclick = closeModal;
  $('#mSave').onclick = async () => {
    const patch = { doc_type: $('#eType').value, company: $('#eCompany').value.trim(), stock_code: $('#eCode').value.trim(), period: $('#ePeriod').value.trim(), report_date: $('#eDate').value };
    const updated = await api('/api/kb/' + id, { method: 'PATCH', body: JSON.stringify(patch), headers: { 'Content-Type': 'application/json' } });
    state.queue.forEach((q) => { if (q.record && q.record.doc_id === id) q.record = updated; });
    renderQueue(); closeModal(); toast('已保存，相关事实缓存将按新信息重建'); if ($('#view-kb').classList.contains('active')) loadKb();
  };
}

/* —— 启动与进度 —— */
$('#start').addEventListener('click', async () => {
  const docIds = state.queue.filter((q) => q.record && ['ready', 'duplicate', 'kb'].includes(q.status)).map((q) => q.record.doc_id);
  const mode = $('input[name=mode]:checked').value;
  const options = { kb_lookup: $('#optKb').checked, strict: $('#optStrict').checked, use_llm: $('#optLlm').checked, text_evidence: $('#optText').checked };
  if ($('#optCompany').value.trim()) options.company = $('#optCompany').value.trim();
  if ($('#optDate').value) options.report_date = $('#optDate').value;
  if ($('#optScope').value) options.scope = $('#optScope').value;
  try {
    const run = await api('/api/smart/run', { method: 'POST', body: JSON.stringify({ doc_ids: docIds, mode, options }), headers: { 'Content-Type': 'application/json' } });
    state.run = run.run_id; $('#progressPanel').hidden = false; $('#start').disabled = true; watch(run.run_id);
  } catch (err) { toast(err.message, 4000); }
});
$('#cancelRun').addEventListener('click', async () => { if (state.run) { await api(`/api/smart/runs/${state.run}/cancel`, { method: 'POST', body: '{}' }); toast('已请求取消'); } });
function watch(runId) {
  clearInterval(state.poll);
  const tick = async () => {
    let run;
    try { run = await api('/api/smart/runs/' + runId); } catch (err) { return; }
    $('#progressPhase').textContent = run.phase || run.status; $('#progressPct').textContent = (run.progress || 0) + '%';
    $('#progressBar').style.width = (run.progress || 0) + '%'; $('#progressRun').textContent = `任务 ${runId} · ${run.names.length} 份材料`;
    if (['completed', 'partial', 'failed', 'cancelled', 'interrupted'].includes(run.status)) {
      clearInterval(state.poll); renderQueue();
      if (run.status === 'completed' || run.status === 'partial') { toast(run.status === 'partial' ? '部分完成，请检查待处理项和预算记录' : '完成'); openBatch(run); } else toast(run.status === 'cancelled' ? '已取消' : '任务未完成：' + (run.error || ''), 5000);
    }
  };
  tick(); state.poll = setInterval(tick, 700);
}

/* —— 结果 —— */
async function openBatch(run) {
  state.batch = run; const items = (run.batch && run.batch.items) || [];
  if (!items.length) { toast('该任务没有可展示的结果'); return; }
  $('#rItem').innerHTML = items.map((it, i) => `<option value="${i}">${esc(it.company || it.name)} · ${esc(it.report_date || '')} · ${it.task === 'check' ? '研报核查' : '财报分析'}</option>`).join('');
  $('#rItem').hidden = items.length < 2;
  await openItem(0); show('result');
}
$('#rItem').addEventListener('change', () => openItem(+$('#rItem').value));
async function openItem(index) {
  const run = state.batch, item = run.batch.items[index]; state.item = item;
  const base = `/api/smart/runs/${run.run_id}/items/${encodeURIComponent(item.item_id)}`;
  const data = await api(base + '/results.json'); state.results = data;
  $('#resultEmpty').hidden = true; $('#resultBody').hidden = false;
  $('#rHtml').href = base + '/report.html'; $('#rHtml').onclick = null;
  $('#rCsv').onclick = (e) => { e.preventDefault(); download(base + '/corrections.csv?download=1', `明镜修改清单-${item.company || 'report'}.csv`); };
  $('#rZip').onclick = (e) => { e.preventDefault(); download(`/api/smart/runs/${run.run_id}/export`, `MingJing-${run.run_id}.zip`); };
  $('#rCsv').hidden = data.task !== 'check';
  $('#rReplay').onclick = async () => {
    const btn = $('#rReplay'); btn.disabled = true; btn.textContent = '重放中…';
    try {
      const r = await api(base + '/replay', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } });
      if (r.status === 'identical') toast(`离线重放一致：逐条比较 ${r.compared} 项，差异 0`, 4000);
      else if (r.status === 'missing_inputs') toast('知识库缺少原始输入：' + (r.missing || []).join('、'), 5000);
      else toast(`重放未通过：${r.reason || r.status} ${r.differences ?? ''} ${r.issues ? JSON.stringify(r.issues).slice(0, 240) : ''}`, 5000);
    } catch (err) { toast(err.message, 4000); } finally { btn.disabled = false; btn.textContent = '重放验证'; }
  };
  const rep = data.report || {};
  $('#rTitle').textContent = `${rep.company || '未识别公司'} · ${data.task === 'check' ? '研报核查' : '财报分析'}`;
  $('#rMeta').textContent = data.task === 'check'
    ? `《${rep.name || ''}》 · ${rep.broker || ''} · 研报日期 ${rep.report_date || '未识别'} · 参考材料 ${data.materials.filter((m) => !m.excluded).length} 份${data.materials.some((m) => m.excluded) ? `（另 ${data.materials.filter((m) => m.excluded).length} 份晚于研报日期未用）` : ''} · 事实 ${data.facts_used} 条 · ${data.mode === 'strict' ? '审慎模式' : '标准模式'} · 用时 ${data.seconds}s`
    : `参考材料 ${data.materials.length} 份 · 事实 ${data.facts_used} 条 · 用时 ${data.seconds}s`;
  const narrative = data.narrative || {};
  $('#rNarrative').innerHTML = narrative.text ? esc(narrative.text) + (narrative.source === 'llm' ? ` <small>（模型 ${esc(narrative.model || '')} 撰写，数字由宿主按槽位填入）</small>` : ' <small>（按结构化结果生成）</small>') : '';
  $('#rNarrative').hidden = !narrative.text;
  $('#checkView').hidden = data.task !== 'check'; $('#analysisView').hidden = data.task !== 'analysis';
  if (data.task === 'check') renderCheck(data); else renderAnalysis(data);
  openMaterialSession(data).catch((err) => { $('#sessionState').textContent = err.message; });
}
function subject(it) {
  const name = it.metric ? (it.metric_name || it.metric_text || it.metric) : (it.metric_text || '未绑定指标');
  return `${periodLabel(it.period)}${it.segment ? '·' + it.segment : ''} ${name}${REL[it.relation] ? '·' + REL[it.relation] : ''}`;
}
function highlight(it) {
  const t = it.text || ''; const s = it.span || [0, 0];
  if (it.kind === 'prose' && s[1] > s[0] && s[1] <= t.length) return esc(t.slice(Math.max(0, s[0] - 90), s[0])) + '<mark>' + esc(t.slice(s[0], s[1])) + '</mark>' + esc(t.slice(s[1], s[1] + 70));
  const TABLES = { SUMMARY: '财务摘要', IS: '利润表', BS: '资产负债表', CF: '现金流量表', RATIO: '主要财务比率' };
  const cells = it.row_label && t.startsWith(it.row_label) ? t.slice(it.row_label.length).trim() : t;
  return esc((it.row_label ? `【${TABLES[it.table] || '表格'}】${it.row_label}：` : '') + cells.slice(0, 160));
}
function renderCheck(data) {
  const s = data.summary, t = s.totals;
  $('#kOk').textContent = t.verified; $('#kBad').textContent = t.violated; $('#kUnk').textContent = t.unverifiable; $('#kSkip').textContent = t.skipped;
  const total = Math.max(1, t.verified + t.violated + t.unverifiable + t.skipped);
  $('#kStack').innerHTML = [['verified', 'var(--ok)'], ['violated', 'var(--bad)'], ['unverifiable', 'var(--unk)'], ['skipped', '#b8c2cf']].map(([k, c]) => `<i style="width:${(t[k] / total) * 100}%;background:${c}" title="${STATUS[k][0]} ${t[k]}"></i>`).join('');
  const execution = data.execution_statistics || {};
  $('#kRates').innerHTML = `<span>处理完成率 <b>${pct(execution.processed_fraction)}</b></span><span>历史财务有效判定 <b>${pct(s.historical.coverage)}</b></span><span>历史财务弃权率 <b>${pct(s.historical.abstention_rate)}</b></span><span>待处理 <b>${execution.pending_ledger_mentions ?? '未知'}</b></span><span class="muted">逐处陈述 ${t.total} · 去重断言 ${s.assertions?.total ?? t.total}</span>`;
  const groups = Object.values(s.groups || {});
  $('#kTiers').innerHTML = '<div class="panel-head"><h3>分级构成</h3><span class="muted small">每级陈述中已核实 / 有误 / 未能核实 / 不判定的占比</span></div>' + groups.map((g) => {
    const n = Math.max(1, g.total);
    return `<div class="tier-row"><div>${esc(g.label)}</div><div class="small muted">${g.total} 条</div><div class="bar2" title="已核实 ${g.verified} · 有误 ${g.violated} · 未能核实 ${g.unverifiable} · 不判定 ${g.skipped}">`
      + [['verified', 'var(--ok)'], ['violated', 'var(--bad)'], ['unverifiable', 'var(--unk)'], ['skipped', '#b8c2cf']].map(([k, c]) => `<i style="width:${(g[k] / n) * 100}%;background:${c}"></i>`).join('')
      + `</div><div class="small">${g.verified}/${g.violated}/${g.unverifiable}/${g.skipped}</div></div>`;
  }).join('');
  const advice = data.advice || [];
  $('#advice').hidden = !advice.length; $('#advice').innerHTML = '<b>补充材料建议</b><ul>' + advice.map((a) => `<li>${esc(a)}</li>`).join('') + '</ul>';
  const items = data.items;
  const wrong = items.filter((i) => i.status === 'violated');
  const gaps = items.filter((i) => i.status === 'unverifiable');
  $('#cWrong').textContent = wrong.length; $('#cAll').textContent = items.length; $('#cGap').textContent = gaps.length;
  $('#tab-wrong').innerHTML = wrong.length ? wrong.map((it) => `<div class="card" data-cid="${it.cid}"><div class="row1"><span class="tag bad">${esc(it.error_type)} ${esc(it.error_label)}</span><b>${esc(subject(it))}</b><span class="muted small">第${it.page}页 · ${esc(it.cid)}</span></div>
      <div class="quote">${highlight(it)}</div><div>原文 <b>${esc(it.num_text)}</b> → 应为 <b>${esc(it.expected_display)}</b> <span class="muted">（${esc(it.definition_label)}）</span> ${esc((it.diagnosis || {}).detail || '')}</div>
      ${it.correction && it.correction.replacement_text ? `<div class="fix">建议替换：${esc(it.correction.replacement_text.slice(0, 220))}</div>` : ''}</div>`).join('')
    : '<div class="empty">没有发现与材料不相容的数字。</div>';
  const cats = [...new Set(items.map((i) => i.category))];
  $('#fCat').innerHTML = '<option value="">全部分级</option>' + cats.map((c) => `<option value="${c}">${esc(CAT[c] || c)}</option>`).join('');
  renderAll();
  const byReason = {};
  gaps.forEach((g) => { const k = g.abstain_code + ' ' + (g.abstain_label || ''); (byReason[k] = byReason[k] || []).push(g); });
  const gapGroups = data.gaps || [];
  const KIND = { missing_report: 'unk', missing_detail: 'unk', definition: 'info', external: 'skip', mention: 'skip', premise: 'skip', conflict: 'bad' };
  const grouped = gapGroups.length ? `<div class="panel"><div class="panel-head"><h3>补材建议（按缺口分组）</h3><span class="muted small">先补“缺整份报告”，影响条数最多的优先</span></div>`
    + `<table class="table"><thead><tr><th>类型</th><th>需要什么</th><th>可替代材料</th><th>影响</th><th>涉及</th></tr></thead><tbody>${gapGroups.map((g) => `<tr><td><span class="tag ${KIND[g.kind] || 'info'}">${esc(g.kind_label)}</span></td><td>${esc(g.need)}</td><td class="small">${esc(g.alternative)}</td><td>${g.count} 条</td><td class="small muted">${esc(g.items.slice(0, 5).join('、'))}</td></tr>`).join('')}</tbody></table></div>` : '';
  $('#tab-gap').innerHTML = grouped + (s.gaps && s.gaps.length ? `<div class="panel"><div class="panel-head"><h3>缺口清单</h3><span class="muted small">按缺失的期间与科目汇总，补充对应报告后重跑即可</span></div><table class="table"><thead><tr><th>期间</th><th>科目/维度</th><th>涉及陈述</th></tr></thead><tbody>${s.gaps.map((g) => `<tr><td>${esc(periodLabel(g.period))}</td><td>${esc(g.item)}${g.dimension ? ' · ' + esc(g.dimension) : ''}</td><td>${g.claims}</td></tr>`).join('')}</tbody></table></div>` : '')
    + Object.entries(byReason).map(([k, list]) => `<h4>${esc(k)}（${list.length}）</h4>` + list.slice(0, 120).map((it) => `<div class="card unk" data-cid="${it.cid}"><div class="row1"><span class="tag unk">${esc(it.abstain_code)}</span><b>${esc(subject(it))} ${esc(it.num_text)}</b><span class="muted small">第${it.page}页 · ${esc(CAT[it.category] || it.category)}</span></div><div class="muted">${esc(it.reason)}${it.internal_check ? '（' + esc(it.internal_check) + '）' : ''}</div></div>`).join('')).join('');
  const assumptions = s.assumptions || [];
  $('#cAssume').textContent = s.presumed_results || '';
  $('#tab-assume').innerHTML = `<div class="panel"><div class="panel-head"><h3>推定与假设</h3><span class="muted small">${s.presumed_results || 0} 条结论用到推定；点开任一条可看它用了哪几项</span></div>`
    + (assumptions.length ? `<table class="table"><thead><tr><th>推定</th><th>涉及结论</th></tr></thead><tbody>${assumptions.map((a) => `<tr><td>${esc(a.label)}</td><td>${a.count}</td></tr>`).join('')}</tbody></table>` : '<div class="empty">本次结论没有用到推定。</div>')
    + `<p class="muted small">${data.mode === 'strict' ? '审慎模式：不做口径推定，仅备选定义相容的陈述记为“未能核实”。' : '标准模式：按通行惯例推定并逐条披露；勾选“审慎模式”可关闭口径推定。'}`
    + `${data.market_check && data.market_check.status ? ' 首页行情校验：' + esc(data.market_check.detail || data.market_check.status) + '。' : ''}</p></div>`;
  $('#tab-groups').innerHTML = `<table class="table"><thead><tr><th>分级</th><th>断言</th><th>已核实</th><th>有误</th><th>未能核实</th><th>不判定</th><th>原文出现</th></tr></thead><tbody>${Object.values(s.groups).map((g) => `<tr><td>${esc(g.label)}</td><td>${g.total}</td><td>${g.verified}</td><td>${g.violated}</td><td>${g.unverifiable}</td><td>${g.skipped}</td><td>${g.occurrences}</td></tr>`).join('')}</tbody></table>
    ${s.modality ? `<h4>事实、推论与观点</h4><table class="table"><thead><tr><th>性质</th><th>断言</th><th>已核实</th><th>有误</th><th>未能核实</th><th>不判定</th></tr></thead><tbody>${Object.entries(s.modality).map(([k, v]) => `<tr><td>${esc(MODALITY[k] || k)}</td><td>${v.total}</td><td>${v.verified}</td><td>${v.violated}</td><td>${v.unverifiable}</td><td>${v.skipped}</td></tr>`).join('')}</tbody></table>` : ''}
    <p class="muted small">核实依据：${Object.entries(s.basis || {}).map(([k, v]) => `${BASIS[k] || k} ${v}`).join('，')}。错误类型：${Object.entries(s.error_types || {}).map(([k, v]) => `${k} ${v}`).join('，') || '无'}。弃权代码：${Object.entries(s.abstain_codes || {}).map(([k, v]) => `${k} ${v}`).join('，') || '无'}。</p>`;
  $('#tab-materials').innerHTML = materialsTable(data.materials) + (data.binder && data.binder.mode === 'llm' ? `<p class="muted small">模型语义绑定：${esc(data.binder.model || '')}，候选 ${data.binder.candidates}，改写 ${data.binder.refined}，请求 ${data.binder.requests}，缓存命中 ${data.binder.cache_hits}。</p>` : `<p class="muted small">语义绑定：确定性规则${data.binder && data.binder.error ? '（模型不可用，已回退：' + esc(data.binder.error) + '）' : ''}。</p>`)
    + `<p class="muted small">知识库本次：解析缓存命中 ${data.kb.parse_hits}、事实缓存命中 ${data.kb.fact_hits}、去重命中 ${data.kb.dedupe_hits}。</p>`;
  $$('#rTabs button').forEach((b) => b.classList.toggle('active', b.dataset.tab === 'wrong')); $$('.tab').forEach((t) => (t.hidden = t.id !== 'tab-wrong'));
}
function materialsTable(materials) {
  if (!materials.length) return '<div class="empty">本次没有可用的财报材料。上传该公司的年报/半年报后重新运行。</div>';
  return `<table class="table"><thead><tr><th>文件</th><th>类型</th><th>报告期</th><th>披露日期</th><th>事实数</th><th>来源</th></tr></thead><tbody>${materials.map((m) => `<tr><td>${esc(m.name)}</td><td>${esc(m.type_label)}</td><td>${esc(periodLabel(m.period))}</td><td>${esc(m.report_date)}</td><td>${m.facts}</td><td><span class="tag info">${esc(m.source)}</span>${m.excluded ? ` <span class="tag skip" title="${esc(m.excluded)}">研报日期后公告，未使用</span>` : ''}</td></tr>`).join('')}</tbody></table>`;
}
$('#rTabs').addEventListener('click', (e) => { const b = e.target.closest('button'); if (!b) return; $$('#rTabs button').forEach((x) => x.classList.toggle('active', x === b)); $$('.tab').forEach((t) => (t.hidden = t.id !== 'tab-' + b.dataset.tab)); });
['fText', 'fStatus', 'fCat', 'fBasis'].forEach((id) => $('#' + id).addEventListener('input', renderAll));
function renderAll() {
  if (!state.results || state.results.task !== 'check') return;
  const q = $('#fText').value.trim(), st = $('#fStatus').value, cat = $('#fCat').value, basis = $('#fBasis').value;
  const rows = state.results.items.filter((it) => (!st || it.status === st) && (!cat || it.category === cat) && (!basis || it.basis === basis)
    && (!q || [it.text, it.metric, it.metric_text, it.num_text, it.segment, it.row_label].join(' ').includes(q)));
  $('#allRows').innerHTML = rows.slice(0, 400).map((it) => `<tr class="click" data-cid="${it.cid}"><td class="small muted">${esc(it.cid)}</td><td>${it.page}</td>
    <td><div class="stmt">${esc(subject(it))}</div><div class="sub">${esc((it.row_label ? `【${({ SUMMARY: '财务摘要', IS: '利润表', BS: '资产负债表', CF: '现金流量表', RATIO: '主要财务比率' })[it.table] || '表格'}】${it.row_label}` : (it.text || '')).slice(0, 80))}</div></td>
    <td>${esc(it.num_text)}</td><td>${esc(it.expected_display || '')}</td><td>${tag(it.status)}${it.advisories && it.advisories.length ? ` <span class="tag info">${esc(it.advisories.join('/'))}</span>` : ''}</td><td class="small">${esc(BASIS[it.basis] || '')}</td></tr>`).join('');
  $('#allMore').textContent = rows.length > 400 ? `共 ${rows.length} 条，仅显示前 400 条，请用筛选缩小范围。` : `共 ${rows.length} 条`;
}
document.addEventListener('click', (e) => { const el = e.target.closest('[data-cid]'); if (el && state.results) openDrawer(el.dataset.cid); });
function canRenderEvidenceImage(identifier) {
  // 与本地证据页接口的格式一致；表格来源标识仍保留为文本。
  const match = /^[0-9a-f]{12}:p\d{1,4}:(?:(?:-?\d+,){3}-?\d+:[0-9a-f]{8}|text)$/.exec(identifier);
  return !!match && match[0] === identifier;
}
function openDrawer(cid) {
  const it = state.results.items.find((x) => x.cid === cid); if (!it) return;
  $('#dTitle').innerHTML = `${tag(it.status)} ${esc(subject(it))}`;
  const ops = (it.operands || []).map((o) => `<tr><td>${esc(o.name)}${o.dimension ? '·' + esc(o.dimension) : ''}${o.relation === 'yoy' ? '（同比）' : ''}</td><td>${esc(o.period)}</td><td>${esc(operandValue(o))}</td><td class="small">${esc(o.table || '')}${o.source_type === 'report_table' ? '（研报表格）' : ''}</td></tr>`).join('');
  const evidence = [...new Set(it.evidence || [])].filter((e) => /^[0-9a-f]{12}:p\d+:/.test(e)).slice(0, 3);
  $('#dBody').innerHTML = `<div class="quote">${highlight(it)}</div>
    <h4>核查</h4><div class="kv"><div>原文数值</div><div><b>${esc(it.num_text)}</b>${it.hedge ? ' <span class="tag info">模糊限定</span>' : ''}</div>
      <div>核查值</div><div>${esc(it.expected_display || '—')}</div><div>口径/定义</div><div>${esc(it.definition_label || '—')}</div>
      <div>依据</div><div>${esc(BASIS[it.basis] || '')} ${it.error_type ? `<span class="tag bad">${esc(it.error_type)} ${esc(it.error_label)}</span>` : ''}${it.abstain_code ? `<span class="tag unk">${esc(it.abstain_code)} ${esc(it.abstain_label)}</span>` : ''}</div>
      <div>分级</div><div>${esc(CAT[it.category] || it.category)} · ${esc(MODALITY[it.modality] || '')} · 绑定置信度 ${it.confidence}${it.binder === 'llm' ? ' · 模型绑定' : ''}</div>
      ${it.cite_text ? `<div>句中出处</div><div>${esc(it.cite_text)}</div>` : ''}</div>
    ${it.reason ? `<h4>说明</h4><div>${esc(it.reason)}</div>` : ''}${(it.diagnosis || {}).detail ? `<h4>归因</h4><div>${esc(it.diagnosis.detail)}</div>` : ''}
    ${it.formula ? `<h4>计算式</h4><div class="small">${esc(it.formula)}</div>` : ''}
    ${it.candidate_interval || it.expected_interval ? `<h4>精度与比较区间</h4><div class="kv"><div>陈述区间</div><div>${esc(intervalText(it.candidate_interval))}</div><div>来源／推导区间</div><div>${esc(intervalText(it.expected_interval))}</div></div><p class="small muted">${esc(it.precision_note || '结论依据来源披露精度与陈述精度的区间关系。')}</p>` : ''}
    ${(it.rule_ids || []).length ? `<h4>登记规则</h4><div class="small">${it.rule_ids.map(esc).join(' · ')}</div>` : ''}
    ${(it.blockers || []).length ? `<h4>完整阻断链</h4><ul class="small">${it.blockers.map(b => `<li>${esc(b.reason || b.message || b.code || JSON.stringify(b))}</li>`).join('')}</ul>` : ''}
    ${ops ? `<h4>操作数（来自原始披露）</h4><table class="table small"><thead><tr><th>科目</th><th>期间</th><th>数值</th><th>来源表</th></tr></thead><tbody>${ops}</tbody></table>` : ''}
    ${(it.candidates || []).length > 1 ? `<h4>候选定义</h4><ul class="small">${it.candidates.map((c) => `<li>${c.matched ? '✓' : '✗'} ${esc(c.label)}：${esc(c.expected)}</li>`).join('')}</ul>` : ''}
    ${it.correction && it.correction.replacement_text ? `<h4>修改建议</h4><div class="fix">${esc(it.correction.replacement_text)}</div><button class="btn" id="copyFix">复制替换文本</button>` : ''}
    ${(it.assumptions || []).length ? `<h4>推定与假设</h4><ul class="small assume">${it.assumptions.map((a) => `<li><span class="tag info">${esc(a.label)}</span> ${esc(a.text)}</li>`).join('')}</ul>` : ''}
    ${(it.notes || []).length ? `<h4>提示</h4><ul class="small">${it.notes.map((n) => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}
    ${(it.missing || []).length ? `<h4>缺少的材料</h4><ul class="small">${it.missing.map((m) => `<li>${esc(periodLabel(m.period))} ${esc(m.name || m.account)} ${esc(m.dimension || '')}</li>`).join('')}</ul>` : ''}
    ${reportPage(it)}
    ${evidence.length ? `<h4>原文证据（财报）</h4><div class="evidence">${evidence.map((e) => `<div class="small muted">${esc(e)}</div>${canRenderEvidenceImage(e) ? `<img loading="lazy" alt="证据页" src="/api/kb/evidence/${encodeURIComponent(e)}.png">` : ''}`).join('')}</div>` : ''}`;
  $('#drawer').hidden = false;
  const copy = $('#copyFix'); if (copy) copy.onclick = () => navigator.clipboard.writeText(it.correction.replacement_text).then(() => toast('已复制'));
  $$('#dBody img').forEach((img) => img.addEventListener('error', () => { img.replaceWith(Object.assign(document.createElement('div'), { className: 'small muted', textContent: '当前环境无法渲染该页（需要 PyMuPDF 或 poppler）' })); }));
}
$('#dClose').addEventListener('click', () => ($('#drawer').hidden = true));
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') { $('#drawer').hidden = true; closeModal(); } });

function reportPage(it) {
  const rep = (state.results && state.results.report) || {};
  if (!rep.doc_id || !/\.pdf$/i.test(rep.name || '') || !it.page) return '';
  const box = it.bbox && it.bbox.length === 4 ? '?bbox=' + it.bbox.map((v) => Math.round(v)).join(',') : '';
  return `<h4>研报原文位置</h4><div class="small muted">${esc(rep.name)} 第 ${it.page} 页${box ? '（已框出所在行）' : '（当前读取方式没有坐标，显示整页）'}</div>`
    + `<div class="page-view"><img loading="lazy" alt="研报原页" src="/api/kb/${rep.doc_id}/page/${it.page}.png${box}"></div>`;
}

function renderAnalysis(data) {
  const a = data.analysis;
  state.analysis = a;
  const hl = a.highlights || []; $('#aHighlights').hidden = !hl.length; $('#aHighlightList').innerHTML = hl.map((x) => `<li>${esc(x)}</li>`).join('');
  const cs = a.check_summary || { total: 0, passed: 0, failed: 0, undetermined: 0 };
  $('#aCheckSummary').textContent = `共 ${cs.total} 项：一致 ${cs.passed}、不一致 ${cs.failed}、无法判定 ${cs.undetermined}`;
  state.checks = a.checks || []; renderChecks();
  $('#signals').innerHTML = a.signals.length ? a.signals.map((s) => `<div class="signal ${s.severity}"><div class="row1"><b>${esc(s.title)}</b> <span class="tag ${s.severity === 'high' ? 'bad' : s.severity === 'low' ? 'info' : 'unk'}">${{ high: '高', medium: '中', low: '提示' }[s.severity] || s.severity}</span> <span class="muted small">${esc(periodLabel(s.period))} · ${esc(s.nature)}</span></div><div>${esc(s.detail)}</div></div>`).join('') : '<div class="empty">未触发登记的异常信号规则。</div>';
  $('#aNote').textContent = a.note;
  const periods = a.periods;
  $('#metricTable').innerHTML = `<thead><tr><th>指标</th>${periods.map((p) => `<th>${esc(periodLabel(p))}</th>`).join('')}</tr></thead><tbody>${a.metrics.map((r, index) => `<tr><td>${esc(r.name)}</td>${periods.map((p) => { const c = r.cells[p]; return `<td>${c ? `<button class="link" data-analysis-row="${index}" data-analysis-period="${esc(p)}">${esc(c.display)}</button>` + (c.yoy_display ? `<div class="small muted">同比 ${esc(c.yoy_display)}</div>` : c.pp_display ? `<div class="small muted">${esc(c.pp_display)}</div>` : '') : ''}</td>`; }).join('')}</tr>`).join('')}</tbody>`;
  const q = a.quarterly || []; $('#quarterPanel').hidden = !q.length;
  if (q.length) { const qp = [...new Set(q.flatMap((r) => Object.keys(r.cells)))].sort(); $('#quarterTable').innerHTML = `<thead><tr><th>指标</th>${qp.map((p) => `<th>${esc(p)}</th>`).join('')}</tr></thead><tbody>${q.map((r) => `<tr><td>${esc(r.name)}</td>${qp.map((p) => { const c = r.cells[p]; return `<td${c && c.derived ? ` title="${esc(c.derived)}"` : ''}>${c ? esc(c.display) + (c.qoq_display ? `<div class="small muted">环比 ${esc(c.qoq_display)}</div>` : '') + (c.yoy_display ? `<div class="small muted">同比 ${esc(c.yoy_display)}</div>` : '') : ''}</td>`; }).join('')}</tr>`).join('')}</tbody>`; }
  $('#aMaterials').innerHTML = materialsTable(data.materials);
}
document.addEventListener('click', (event) => {
  const button = event.target.closest('[data-analysis-row]');
  if (!button || !state.analysis) return;
  const row = state.analysis.metrics[+button.dataset.analysisRow];
  const cell = row && row.cells[button.dataset.analysisPeriod];
  if (!cell) return;
  $('#dTitle').textContent = `${row.name} · ${periodLabel(button.dataset.analysisPeriod)}`;
  const evidence = [...new Set(cell.evidence || [])].filter(id => /^[0-9a-f]{12}:p\d+:/.test(id));
  $('#dBody').innerHTML = `<h4>来源与定义</h4><div class="kv"><div>披露中心值</div><div>${esc(cell.display)}</div><div>口径</div><div>${esc(cell.scope || '未明确')}</div><div>定义</div><div>${esc(cell.definition || '')}</div><div>有效区间</div><div>${esc(intervalText(cell.interval))}</div></div>`
    + `<h4>计算与登记规则</h4><p>${esc(cell.formula || '')}</p><p class="small">${(cell.rule_ids || []).map(esc).join(' · ')}</p>`
    + `<table class="table small"><thead><tr><th>操作数</th><th>期间</th><th>数值</th><th>口径</th></tr></thead><tbody>${(cell.operands || []).map(o => `<tr><td>${esc(o.name || o.account)}</td><td>${esc(periodLabel(o.period))}</td><td>${esc(operandValue(o))}</td><td>${esc(o.scope || '')}</td></tr>`).join('')}</tbody></table>`
    + (cell.yoy_calculation ? `<h4>同比重算</h4><p>${esc(cell.yoy_display || '')} · ${esc(cell.yoy_calculation.formula)}</p><p class="small">${esc(intervalText(cell.yoy_calculation.interval))}</p>` : '')
    + `<h4>推定条件</h4><ul>${(cell.assumptions || []).map(a => `<li>${esc(a.text || a.label || a.code)}</li>`).join('') || '<li>未使用额外推定</li>'}</ul>`
    + evidence.map(id => `<p class="small muted">${esc(id)}</p><img loading="lazy" style="max-width:100%" alt="指标来源页" src="/api/kb/evidence/${encodeURIComponent(id)}.png">`).join('');
  $('#drawer').hidden = false;
});

const CHECK_CAT = { identity: '报表恒等式', yoy: '披露同比与重算', quarter: '季度合计', cross: '跨表一致', segment: '分部合计', ratio: '披露比率与重算' };
function renderChecks() {
  const f = $('#checkFilter').value;
  const rows = (state.checks || []).filter((c) => !f || (f === 'fail' ? c.ok === false : c.ok === null));
  $('#checkTable').innerHTML = `<thead><tr><th>结论</th><th>类别</th><th>检查</th><th>期间</th><th>口径</th><th>说明</th></tr></thead><tbody>${rows.map((c) => `<tr><td>${c.ok === true ? '<span class="tag ok">一致</span>' : c.ok === false ? '<span class="tag bad">不一致</span>' : '<span class="tag unk">无法判定</span>'}</td>`
    + `<td class="small">${esc(CHECK_CAT[c.category] || c.category)}</td><td>${esc(c.name)}</td><td>${esc(periodLabel(c.period))}</td><td>${esc(c.scope)}</td><td class="small">${esc(c.detail)}</td></tr>`).join('') || '<tr><td colspan="6" class="muted">没有符合条件的检查</td></tr>'}</tbody>`;
}
$('#checkFilter').addEventListener('change', renderChecks);

/* —— 设置 —— */
async function loadSettings() {
  try {
    const [s, st] = await Promise.all([api('/api/smart/settings'), api('/api/smart/status')]);
    $('#sEnabled').checked = !!s.llm.enabled; $('#sBase').value = s.llm.base_url || ''; $('#sModel').value = s.llm.model || ''; $('#sPath').value = s.llm.chat_path || '/chat/completions'; $('#sKey').value = ''; $('#sProtocol').value = s.llm.protocol || 'openai-completions';
    state.provider = s.llm.provider_id || s.effective.provider || null;
    $('#sKeyLabel').textContent = s.llm.has_key ? 'API Key（已保存，留空则不修改）' : 'API Key';
    $('#sPersist').textContent = s.persistent ? '保存到本机配置，密钥进入系统安全存储' : '轻量模式：设置只在本次运行期间有效，不写盘';
    $('#sEffective').textContent = s.effective.configured ? `当前生效：${s.effective.model}（来源：${s.effective.source === 'agent.routing' ? '高级界面的供应商路由' : '本页设置'}）` : '当前未启用模型：以纯确定性内核运行';
    $('#sSave').disabled = !s.writable;
    $('#sStatus').innerHTML = `<div>管线版本</div><div>${esc(st.pipeline)}</div><div>PDF 读取</div><div>${esc((st.pdf_backends || []).join('、') || '不可用')}</div>`
      + `<div>原页渲染</div><div>${esc(st.page_renderer || '不可用')}</div><div>知识库</div><div>${st.kb.documents} 份文件 · <span class="small muted">${esc(st.kb.root)}</span></div><div>任务</div><div>${st.runs} 个</div>`;
  } catch (err) { toast(err.message, 4000); }
}
$('#sSave').addEventListener('click', async () => {
  const payload = { llm: { enabled: $('#sEnabled').checked, base_url: $('#sBase').value.trim(), model: $('#sModel').value.trim(), chat_path: $('#sPath').value.trim() || '/chat/completions', protocol: $('#sProtocol').value } };
  if ($('#sKey').value.trim()) payload.api_key = $('#sKey').value.trim();
  try { await api('/api/smart/settings', { method: 'POST', body: JSON.stringify(payload), headers: { 'Content-Type': 'application/json' } }); toast('已保存设置'); loadSettings(); refreshLlm(); }
  catch (err) { toast(err.message, 5000); }
});
async function refreshLlm() {
  try {
    const l = await api('/api/smart/llm');
    $('#llmState').textContent = l.configured ? `（${l.model}，已默认启用）` : '（未配置模型：以纯内核运行，可在“设置”页配置）';
    $('#optLlm').disabled = !l.configured; $('#optLlm').checked = !!l.configured;
  } catch (e) { /* 可选 */ }
}

/* —— 知识库 —— */
async function loadKb() {
  const data = await api('/api/kb'); state.kb = data.docs; const s = data.stats;
  $('#kbStats').innerHTML = [['文档', s.documents], ['公司', s.companies.length], ['去重节省上传', s.duplicate_uploads_saved], ['任务引用次数', s.reuse_hits], ['缓存条目', s.cache_entries]].map(([k, v]) => `<div class="kpi"><b>${v}</b><span>${k}</span></div>`).join('');
  const types = [...new Set(data.docs.map((d) => d.type_label).filter(Boolean))];
  const current = $('#kbType').value; $('#kbType').innerHTML = '<option value="">全部类型</option>' + types.map((t) => `<option ${t === current ? 'selected' : ''}>${esc(t)}</option>`).join('');
  renderKb(); verifyJournal(); const j = await api('/api/kb/journal?limit=80');
  const LABEL = { added: '入库', dedupe_hit: '去重命中', parsed: '解析', facts_extracted: '抽取事实', used: '任务引用', meta_updated: '修正元数据', removed: '移除', version_sibling: '同报告多版本' };
  $('#journal').innerHTML = j.events.reverse().map((e) => `<li><time>${esc(e.time)}</time><span class="tag info">${esc(LABEL[e.event] || e.event)}</span><span>${esc(e.name || (e.docs || []).join(', ') || (e.doc_id || '').slice(0, 12))}${e.run_id ? ' · ' + esc(e.run_id) : ''}${e.facts !== undefined ? ' · 事实 ' + e.facts : ''}</span></li>`).join('') || '<li class="muted">暂无记录</li>';
}
function renderKb() {
  const q = $('#kbSearch').value.trim(), type = $('#kbType').value;
  const rows = state.kb.filter((d) => (!type || d.type_label === type) && (!q || [d.name, d.company, d.stock_code].join(' ').includes(q)));
  $('#kbTable tbody').innerHTML = rows.map((d) => `<tr><td>${esc(d.name)}${d.status && d.status !== 'ready' ? ` <span class="tag unk">${esc(d.error || d.status)}</span>` : ''}${d.same_report_versions ? ' <span class="tag info">多版本</span>' : ''}</td>
    <td><span class="tag ${d.doc_type === 'research_report' ? 'info' : 'ok'}">${esc(d.type_label || '')}</span></td><td>${esc(d.company || '')} <span class="muted small">${esc(d.stock_code || '')}</span></td>
    <td>${esc(periodLabel(d.period || ''))}</td><td>${esc(d.report_date || '')}</td><td>${d.doc_type === 'research_report' ? '<span class="muted small">—</span>' : (d.facts ?? '<span class="muted small">首次使用时抽取</span>')}</td><td>${d.uploads || 1} / ${d.hits || 0}</td>
    <td><button class="link" data-use="${d.doc_id}">用于任务</button><button class="link" data-edit="${d.doc_id}">修正</button><button class="link" data-dl="${d.doc_id}">下载</button><button class="link danger" data-del="${d.doc_id}">删除</button></td></tr>`).join('') || '<tr><td colspan="8" class="muted">知识库为空：在工作台上传的文件会自动入库，也可以点击“上传到知识库”。</td></tr>';
}
async function verifyJournal() {
  try { const v = await api('/api/kb/journal/verify'); $('#journalState').innerHTML = v.ok ? `<span class="tag ok">哈希链完整</span> ${v.entries} 条` : `<span class="tag bad">第 ${v.broken_at} 条断链</span> ${esc(v.detail)}`; }
  catch (err) { $('#journalState').textContent = err.message; }
}
$('#verifyJournal').addEventListener('click', verifyJournal);
$('#kbSearch').addEventListener('input', renderKb); $('#kbType').addEventListener('change', renderKb);
$('#kbUpload').addEventListener('click', () => { const input = Object.assign(document.createElement('input'), { type: 'file', multiple: true, accept: '.pdf,.docx,.md,.txt,.json' }); input.onchange = async () => { for (const f of input.files) { try { await api('/api/kb/upload?name=' + encodeURIComponent(f.name), { method: 'POST', body: f, headers: { 'Content-Type': 'application/octet-stream' } }); } catch (err) { toast(err.message); } } loadKb(); }; input.click(); });
$('#kbTable').addEventListener('click', async (e) => {
  const use = e.target.closest('[data-use]'), edit = e.target.closest('[data-edit]'), dl = e.target.closest('[data-dl]'), del = e.target.closest('[data-del]');
  if (use) { addRecords(state.kb.filter((d) => d.doc_id === use.dataset.use)); show('work'); toast('已加入本次材料'); }
  if (edit) editDoc(edit.dataset.edit);
  if (dl) { const d = state.kb.find((x) => x.doc_id === dl.dataset.dl); download(`/api/kb/${d.doc_id}/file`, d.name); }
  if (del && confirm('从知识库删除该文件及其解析缓存？')) { await api('/api/kb/' + del.dataset.del, { method: 'DELETE' }); loadKb(); }
});

/* —— 历史 —— */
async function loadHistory() {
  const data = await api('/api/smart/runs');
  $('#historyTable tbody').innerHTML = data.runs.map((r) => {
    const items = (r.batch && r.batch.items) || [];
    const summary = items.map((it) => it.task === 'check' ? `${esc(it.company || '')}：核实 ${it.totals.verified} · 有误 ${it.totals.violated} · 未核实 ${it.totals.unverifiable}` : `${esc(it.company || '')}：信号 ${it.signals} · 指标 ${it.metrics}`).join('<br>');
    return `<tr><td class="small">${fmtTime(r.started_at)}</td><td class="small">${r.names.map(esc).join('<br>')}</td><td>${{ auto: '自动', check: '研报核查', analysis: '财报分析' }[r.mode] || r.mode}</td>
      <td>${{ completed: '<span class="tag ok">完成</span>', running: '<span class="tag info">运行中</span>', queued: '<span class="tag info">排队</span>', failed: '<span class="tag bad">失败</span>', cancelled: '<span class="tag skip">已取消</span>', interrupted: '<span class="tag skip">中断</span>' }[r.status] || esc(r.status)}</td>
      <td class="small">${summary || esc(r.error || '')}</td><td>${items.length ? `<button class="link" data-open="${r.run_id}">查看</button>` : ''}${['cancelled', 'interrupted', 'partial'].includes(r.status) ? `<button class="link" data-resume-batch="${r.run_id}">恢复</button>` : ''}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">暂无任务</td></tr>';
  $$('[data-open]').forEach((b) => b.addEventListener('click', async () => openBatch(await api('/api/smart/runs/' + b.dataset.open))));
  $$('[data-resume-batch]').forEach((b) => b.addEventListener('click', async () => {
    try { await api(`/api/smart/runs/${b.dataset.resumeBatch}/resume`, {method: 'POST', body: '{}'}); state.run = b.dataset.resumeBatch; show('work'); $('#progressPanel').hidden = false; pollRun(); }
    catch (err) { toast(err.message, 5000); }
  }));
}
$('#refreshHistory').addEventListener('click', loadHistory);

/* 同一冻结材料的原生会话。结论与引用来自宿主工具，不在浏览器计算。 */
async function openMaterialSession(data) {
  clearTimeout(state.sessionPoll); state.session = null;
  $('#sessionPanel').hidden = !data.native_run_id;
  if (!data.native_run_id) return;
  const model = data.model || {};
  $('#nativeState').textContent = `原生任务 ${data.native_run_id} · 模型执行 ${model.status || '未记录'}${model.error ? '（' + model.error + '）' : ''} · ${data.mode === 'strict' ? '审慎策略' : '标准策略'} · 追问使用任务冻结配置；补证创建新版本。`;
  const session = data.session_id ? await api('/api/sessions/' + data.session_id)
    : await api('/api/sessions', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({run_id: data.native_run_id})});
  state.session = session.session_id; renderSession(session);
}
function renderSession(s) {
  const labels = {ready:'可追问',running:'原生工具执行中',cancelled:'已取消',failed:'未完成',interrupted:'待恢复'};
  $('#sessionState').textContent = `${labels[s.status] || s.status} · ${s.session_id}`;
  $('#sessionSend').disabled = s.status === 'running';
  $('#sessionResume').hidden = !['interrupted','cancelled','failed'].includes(s.status);
  $('#sessionMessages').innerHTML = (s.messages || []).map((m) => `<div class="message ${esc(m.role)}"><b>${{user:'问题',assistant:'答复',system:'状态'}[m.role] || m.role}</b><p>${esc(m.text)}</p>${m.message_id ? `<button class="link" data-message-replay="${esc(m.message_id)}">离线重放此答复</button>` : ''}${(m.source_references || []).map((r) => `<div class="small muted">${esc(r.document_name || r.source || '')} · 页 ${esc(r.page || '')} · ${esc(r.evidence_id || '')}</div>`).join('')}</div>`).join('') || '<p class="muted">可继续询问依据、指标定义或缺少的材料。</p>';
  const activities = s.activities || s.events || [];
  $('#sessionActivities').textContent = activities.slice(-6).map((e) => `${e.role || ''} ${e.tool || e.event || e.type || ''} ${e.status || ''}`).join(' → ');
  if (s.status === 'running') state.sessionPoll = setTimeout(refreshSession, 1800);
}
async function refreshSession() {
  clearTimeout(state.sessionPoll);
  if (!state.session) return;
  try { const s = await api('/api/sessions/' + state.session); if (s.session_id === state.session) renderSession(s); }
  catch (err) { $('#sessionState').textContent = err.message; }
}
$('#sessionRefresh').addEventListener('click', refreshSession);
$('#sessionSend').addEventListener('click', async () => {
  if (!state.session) return; const question = $('#sessionQuestion').value.trim(); if (!question) return;
  $('#sessionSend').disabled = true;
  try { const s = await api(`/api/sessions/${state.session}/messages`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question})}); $('#sessionQuestion').value=''; renderSession(s); }
  catch (err) { toast(err.message,5000); $('#sessionSend').disabled=false; }
});
for (const action of ['cancel','resume']) $('#session' + (action === 'cancel' ? 'Cancel' : 'Resume')).addEventListener('click', async () => {
  if (!state.session) return;
  try { renderSession(await api(`/api/sessions/${state.session}/${action}`, {method:'POST',body:'{}'})); }
  catch (err) { toast(err.message,5000); }
});
$('#sessionMessages').addEventListener('click', async (e) => {
  const b = e.target.closest('[data-message-replay]'); if (!b || !state.session) return; b.disabled=true;
  try { const r=await api(`/api/sessions/${state.session}/messages/${b.dataset.messageReplay}/replay`,{method:'POST',body:'{}'}); toast(r.status === 'passed' ? '消息离线重放通过' : `消息重放：${r.status} ${r.reason || ''}`,5000); }
  catch (err) { toast(err.message,5000); } finally { b.disabled=false; }
});
$('#sessionSupplement').addEventListener('click', async () => {
  if (!state.item || !state.batch) return;
  try {
    const k=await api('/api/kb');
    $('#modalCard').innerHTML='<h3>选择补证材料</h3><p class="muted">先在知识库上传缺少的财报或附注。补证将创建新任务，不修改已冻结结果。</p><div class="supplement-list">'+k.docs.filter(d=>d.doc_type!=='research_report').map(d=>`<label class="check"><input type="checkbox" data-supplement-id="${esc(d.doc_id)}">${esc(d.name)} · ${esc(d.company || '')} · ${esc(d.report_date || '日期未知')}</label>`).join('')+'</div><div class="actions"><button class="primary" id="confirmSupplement">补证并重查</button><button class="btn" id="closeSupplement">关闭</button></div>';
    $('#modal').hidden=false; $('#closeSupplement').onclick=()=>$('#modal').hidden=true;
    $('#confirmSupplement').onclick=async()=>{
      const doc_ids=$$('[data-supplement-id]:checked').map(e=>e.dataset.supplementId); if(!doc_ids.length){toast('请选择材料');return;}
      try { const r=await api(`/api/smart/runs/${state.batch.run_id}/items/${encodeURIComponent(state.item.item_id)}/materials`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({doc_ids})}); $('#modal').hidden=true;
        if(r.batch_id){ state.run=r.batch_id; show('work'); $('#progressPanel').hidden=false; pollRun(); }
        else toast('补证版本已创建：'+r.run_id+'，可在高级界面的任务历史中查看',6000);
      }catch(err){toast(err.message,5000);}
    };
  }catch(err){toast(err.message,5000);}
});
async function providerDiagnostic(action) {
  const button={test:$('#sTest'),'test-agent':$('#sTestAgent'),discover:$('#sDiscover')}[action]; button.disabled=true;
  $('#sTestResult').hidden=false; $('#sTestResult').textContent='正在执行 '+(action==='test-agent'?'真实原生工具往返（虚构数据）':'供应商检查')+'…';
  try {
    if(!state.provider){ const cfg=await api('/api/config'); state.provider=cfg.config.agent.routing.default.provider; }
    if(!state.provider) throw new Error('请先保存供应商设置');
    const r=await api(`/api/providers/${encodeURIComponent(state.provider)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model:$('#sModel').value.trim()})});
    $('#sTestResult').textContent=JSON.stringify(r,null,2);
  }catch(err){$('#sTestResult').textContent=err.message;}finally{button.disabled=false;}
}
$('#sTest').addEventListener('click',()=>providerDiagnostic('test'));
$('#sTestAgent').addEventListener('click',()=>providerDiagnostic('test-agent'));
$('#sDiscover').addEventListener('click',()=>providerDiagnostic('discover'));

/* —— 启动 —— */
(async function init() {
  try { const h = await api('/api/health'); $('#health').textContent = '本机服务 ' + (h.version || ''); $('#health').className = 'pill ok'; } catch (e) { $('#health').textContent = '服务未连接'; }
  await refreshLlm();
  const view = (location.hash || '#work').slice(1); if (['work', 'result', 'kb', 'history', 'settings', 'about'].includes(view)) show(view);
})();
