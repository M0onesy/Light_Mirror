"""可选的模型语义绑定：只改写枚举字段，不提交任何数值、公式或判定。

流程：挑出低置信或未绑定的正文陈述 → 把目标数字用【】标出、其他数字保留 → 让模型从给定枚举
（指标代码、候选期间、关系、主体类型）中选择 → 宿主逐项校验（枚举合法、主体名须为原句子串）→
回写到陈述并降级为“模型绑定”置信度 → 确定性内核重新核查。

请求与应答按内容哈希缓存在知识库（命中即复用，不再消耗 Token），每次调用写入审计日志。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from urllib.request import Request

from ..http_transport import redact_value, safe_error_message, urlopen
from .claims import Claim, RATIO_METRICS, _category
from .common import find_periods, norm
from .taxonomy import REPORT_ALIASES, account_name
from .errors import ReplayMismatch, TaskCancelled

RELATIONS = ('value', 'yoy', 'qoq', 'cagr', 'pp', 'share')
ENTITIES = ('company', 'segment', 'external', 'other')
SYSTEM = (
    '你是金融研究报告的语义标注器。对每个条目，句子中用【】标出的数字是待标注目标。'
    '只允许从给定枚举中选择：metric（指标代码，无法确定时为空字符串）、period（必须取自 candidates.periods）、'
    'relation（value 绝对值/yoy 同比/qoq 环比/cagr 复合增速/pp 百分点差/share 占比）、'
    'entity（company 公司整体/segment 分业务分产品分客户/external 行业或第三方数据/other 非财务数量）、'
    'segment（entity=segment 时填写，必须是原句中连续出现的文字）、forecast（是否为预测值）。'
    '不得输出任何数值、计算结果、公式或真伪判断。只返回 JSON 数组，每项形如'
    '{"id":"c0001","metric":"REVENUE","period":"2023Y","relation":"value","entity":"company","segment":"","forecast":false}。')


def metric_catalog() -> list[dict]:
    seen, rows = set(), []
    for alias, code in REPORT_ALIASES:
        if code in seen or code in ('TARGET_PRICE',):
            continue
        seen.add(code)
        rows.append({'code': code, 'name': account_name(code)})
    return rows


def _candidates(claim: Claim, base_year: int | None) -> list[str]:
    periods = []
    for mention in find_periods(norm(claim.text)):
        periods.extend(mention.periods)
    if base_year:
        periods += [f'{base_year + d}Y' for d in (-2, -1, 0, 1, 2, 3)] + [f'{base_year}H1', f'{base_year}Q4']
    if claim.period:
        periods.append(claim.period)
    return list(dict.fromkeys(p for p in periods if p))


def _mark(claim: Claim) -> str:
    text = norm(claim.text)
    start, end = claim.span
    if 0 <= start < end <= len(text):
        return text[:start] + '【' + text[start:end] + '】' + text[end:]
    return text


def select(claims: list[Claim], limit: int = 60) -> list[Claim]:
    picked = [c for c in claims if c.kind == 'prose' and not c.forecast and (c.confidence < 0.8 or c.category in ('other_fact',) or not c.metric)]
    return picked[:limit]


def _complete(cfg: dict, payload: dict) -> tuple[str, dict]:
    base = cfg['base_url'].rstrip('/')
    protocol = cfg.get('protocol', 'openai-completions')
    path = cfg.get('chat_path') or {'openai-responses': '/responses', 'anthropic-messages': '/messages'}.get(protocol, '/chat/completions')
    endpoint = base + (path if path.startswith('/') else '/' + path)
    wire = dict(payload)
    if protocol == 'openai-responses':
        wire = {'model': payload['model'], 'input': payload['messages'], 'max_output_tokens': payload['max_tokens']}
        if payload.get('temperature') is not None:
            wire['temperature'] = payload['temperature']
    elif protocol == 'anthropic-messages':
        wire = {'model': payload['model'], 'max_tokens': payload['max_tokens'],
                'system': '\n'.join(m['content'] for m in payload['messages'] if m['role'] == 'system'),
                'messages': [m for m in payload['messages'] if m['role'] != 'system']}
    elif protocol == 'deepseek' and cfg.get('reasoning') == 'off':
        wire['thinking'] = {'type': 'disabled'}
    raw = json.dumps(wire, ensure_ascii=False).encode('utf-8')
    request = Request(endpoint, method='POST')
    request.add_header('Content-Type', 'application/json')
    if protocol == 'anthropic-messages':
        request.add_header('x-api-key', cfg['api_key'])
        request.add_header('anthropic-version', '2023-06-01')
    else:
        request.add_header('Authorization', 'Bearer ' + cfg['api_key'])
    started = time.monotonic()
    with urlopen(request, data=raw, timeout=float(cfg.get('timeout_s') or 60), verify_tls=cfg.get('verify_tls', True)) as response:
        body = response.read()
    data = json.loads(body.decode('utf-8'))
    if protocol == 'openai-responses':
        if data.get('status') == 'incomplete':
            raise ValueError('Responses 输出不完整')
        content = ''.join(part.get('text', '') for row in data.get('output', []) if row.get('type') == 'message'
                          for part in row.get('content', []) if part.get('type') == 'output_text')
    elif protocol == 'anthropic-messages':
        if data.get('stop_reason') == 'max_tokens':
            raise ValueError('Messages 输出被截断')
        content = ''.join(part.get('text', '') for part in data.get('content', []) if part.get('type') == 'text')
    else:
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise ValueError('模型输出被截断（finish_reason=length）')
        content = (choice.get('message') or {}).get('content') or ''
    if not isinstance(content, str) or not content.strip():
        raise ValueError('模型未返回非空正文，不能作为成功的语义应答')
    meta = {'latency_ms': round((time.monotonic() - started) * 1000), 'model': data.get('model'), 'usage': data.get('usage') or {},
            'request_sha256': hashlib.sha256(raw).hexdigest(), 'response_sha256': hashlib.sha256(body).hexdigest()}
    return content, meta


def _post(cfg: dict, payload: dict) -> tuple[list, dict]:
    content, meta = _complete(cfg, payload)
    fenced = re.search(r'```(?:json)?\s*(.*?)```', content, re.S)
    text = fenced.group(1) if fenced else content
    start, end = text.find('['), text.rfind(']')
    if start < 0 or end <= start:
        raise ValueError('模型正文不是可解析的语义数组')
    items = json.loads(text[start:end + 1])
    if not isinstance(items, list):
        raise ValueError('语义结果必须为 JSON 数组')
    return items, meta


def request_config(cfg: dict) -> dict:
    return {key: cfg.get(key) for key in ('base_url', 'chat_path', 'protocol', 'model', 'temperature', 'max_tokens', 'reasoning', 'verify_tls')}


def cache_key(cfg: dict, payload: dict) -> str:
    return json.dumps({'route': request_config(cfg), 'request': payload}, ensure_ascii=False, sort_keys=True)


def refine_claims(model, document, kb, cfg: dict, audit=None, *, batch: int = 12) -> dict:
    """cfg: base_url / api_key / model / chat_path / timeout_s / temperature。返回绑定摘要。"""
    if not (cfg.get('base_url') and cfg.get('api_key') and cfg.get('model')):
        return {'mode': 'rule', 'status': 'not_configured'}
    base_year = model.meta.base_year
    targets = select(model.claims, int(cfg.get('max_claims') or 60))
    catalog = metric_catalog()
    allowed_metrics = {row['code'] for row in catalog}
    summary = {'mode': 'llm', 'status': 'success', 'model': cfg['model'], 'candidates': len(targets), 'refined': 0, 'requests': 0,
               'cache_hits': 0, 'rejected': 0, 'request_config': request_config(cfg), 'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}}
    by_id = {claim.cid: claim for claim in targets}
    for offset in range(0, len(targets), batch):
        chunk = targets[offset:offset + batch]
        items = [{'id': c.cid, 'sentence': _mark(c), 'candidates': {'periods': _candidates(c, base_year)},
                  'current': {'metric': c.metric, 'period': c.period, 'relation': c.relation, 'entity': c.entity, 'segment': c.segment}}
                 for c in chunk]
        user = json.dumps({'company': model.meta.company, 'report_date': model.meta.report_date, 'base_year': base_year,
                           'metrics': catalog, 'items': items}, ensure_ascii=False)
        payload = {'model': cfg['model'], 'temperature': float(cfg.get('temperature') or 0), 'max_tokens': int(cfg.get('max_tokens') or 2000),
                   'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': user}]}
        key = cache_key(cfg, payload)
        cached = kb.cache_get('llm-bind', key) if kb is not None else None
        try:
            if cached is not None:
                answers, meta = cached['answers'], {**cached['meta'], 'cache': 'hit'}
                summary['cache_hits'] += 1
            elif cfg.get('replay_only'):
                raise ReplayMismatch('重放所需的模型应答不在缓存中（重放不联网）')
            else:
                answers, meta = _post(cfg, payload)
                summary['requests'] += 1
                if kb is not None:
                    kb.cache_put('llm-bind', key, {'answers': answers, 'meta': meta})
                for name in summary['usage']:
                    summary['usage'][name] += int((meta.get('usage') or {}).get(name) or 0)
        except Exception as exc:
            if cfg.get('replay_only') or isinstance(exc, TaskCancelled):
                raise
            message = safe_error_message(exc, [cfg.get('api_key', '')])
            summary.update(status='partial' if summary['refined'] else 'fallback', error=message)
            if audit is not None:
                audit.emit('model_call', status='failed', error=message, model=cfg['model'], items=len(chunk))
            break
        if audit is not None:
            audit.emit('model_call', status='success', model=cfg['model'], items=len(chunk), cache=meta.get('cache', 'miss'),
                       input_hash=hashlib.sha256(key.encode('utf-8')).hexdigest(), output_hash=hashlib.sha256(json.dumps(answers, ensure_ascii=False).encode('utf-8')).hexdigest(),
                       usage=redact_value(meta.get('usage') or {}, [cfg.get('api_key', '')]))
        for answer in answers if isinstance(answers, list) else []:
            claim = by_id.get(str(answer.get('id', ''))) if isinstance(answer, dict) else None
            if claim is None:
                continue
            metric = str(answer.get('metric') or '')
            period = str(answer.get('period') or '')
            relation = str(answer.get('relation') or 'value')
            entity = str(answer.get('entity') or 'company')
            segment = str(answer.get('segment') or '')
            allowed_periods = set(_candidates(claim, base_year))
            valid = (not (set(answer) - {'id', 'metric', 'period', 'relation', 'entity', 'segment', 'forecast'})
                     and type(answer.get('forecast', False)) is bool
                     and (not metric or metric in allowed_metrics) and (not period or period in allowed_periods) and relation in RELATIONS
                     and entity in ENTITIES and (not segment or segment in norm(claim.text)) and not re.search(r'\d{3,}', segment))
            if not valid:
                summary['rejected'] += 1
                continue
            if relation == 'pp' and metric and metric not in RATIO_METRICS:
                relation = 'value'
            changed = (metric, period, relation, entity, segment) != (claim.metric, claim.period, claim.relation, claim.entity, claim.segment)
            claim.metric, claim.period, claim.relation = metric or claim.metric, period or claim.period, relation
            claim.entity = 'external' if entity == 'external' else ('segment' if entity == 'segment' and segment else 'company')
            claim.segment = segment if claim.entity == 'segment' else ''
            if entity == 'other':
                claim.metric = ''
            claim.forecast = bool(answer.get('forecast')) or claim.forecast
            claim.binder = 'llm'
            claim.confidence = max(claim.confidence, 0.8)
            claim.notes = [n for n in claim.notes if not n.startswith(('期间由上下文推定', '指标承接', '区间期间取末年'))] + ['模型语义绑定（宿主已校验枚举）']
            claim.category = _category(claim)
            summary['refined'] += int(changed)
    return summary


SUMMARY_SYSTEM = ('你是研究报告质控负责人，为核查结果写一段3至5句的中文摘要，供审核人员快速了解结论。'
                  '所有数字必须用给定槽位表示，写成{{槽位名}}，不得出现任何阿拉伯数字或中文数字，不得新增结论；'
                  '可提及错误类型代码（如E5）。只返回摘要正文。')
_SLOT = re.compile(r'\{\{([a-z0-9_]+)\}\}')


def write_summary(payload: dict, cfg: dict, kb=None, audit=None) -> dict:
    """模型撰写叙述摘要：只允许引用槽位；宿主校验后填数，校验不通过回退到模板叙述。"""
    from .render import slots, template_narrative
    fallback = {'text': template_narrative(payload), 'source': 'template'}
    if not (cfg.get('enabled') and cfg.get('base_url') and cfg.get('api_key') and cfg.get('model')):
        return fallback
    values = slots(payload)
    facts = {'task': payload['task'], 'company': payload['report'].get('company', ''), 'slots': list(values),
             'error_types': list((payload.get('summary') or {}).get('error_types', {}).keys()), 'advice': (payload.get('advice') or [])[:2],
             'signals': [s['title'] for s in (payload.get('analysis') or {}).get('signals', [])][:6]}
    request = {'model': cfg['model'], 'temperature': 0, 'max_tokens': 600,
               'messages': [{'role': 'system', 'content': SUMMARY_SYSTEM}, {'role': 'user', 'content': json.dumps(facts, ensure_ascii=False)}]}
    key = cache_key(cfg, request)
    try:
        cached = kb.cache_get('llm-summary', key) if kb is not None else None
        if cached is None and cfg.get('replay_only'):
            raise ReplayMismatch('重放未命中摘要缓存')
        if cached is None:
            text, meta = _complete(cfg, request)
            cached = {'text': text, 'usage': meta.get('usage') or {}}
            if kb is not None:
                kb.cache_put('llm-summary', key, cached)
        text = cached['text']
        names = set(_SLOT.findall(text))
        stripped = re.sub(r'(?<![A-Za-z0-9])[EXA]\d(?![0-9])', '', _SLOT.sub('', text))
        if not text or names - set(values) or re.search(r'[0-9０-９]|[零一二三四五六七八九十百千万亿]+(?:条|处|份|项|个|%)', stripped):
            if audit is not None:
                audit.emit('model_call', purpose='summary', status='rejected', reason='摘要含槽位以外的数字或未知槽位')
            return {**fallback, 'rejected': True}
        filled = _SLOT.sub(lambda m: str(values[m[1]]), text)
        if audit is not None:
            audit.emit('model_call', purpose='summary', status='success', model=cfg['model'], slots=sorted(names),
                       output_hash=hashlib.sha256(text.encode('utf-8')).hexdigest())
        return {'text': filled, 'source': 'llm', 'model': cfg['model'], 'template': text}
    except Exception as exc:
        if cfg.get('replay_only') or isinstance(exc, TaskCancelled):
            raise
        if audit is not None:
            audit.emit('model_call', purpose='summary', status='failed', error=safe_error_message(exc, [cfg.get('api_key', '')]))
        return {**fallback, 'error': safe_error_message(exc, [cfg.get('api_key', '')])}


def config_from_app(config: dict, secret) -> dict:
    """从明镜配置推导绑定所用模型：优先 agent.routing.default，其次旧版 llm 配置。"""
    routing = (config.get('agent') or {}).get('routing') or {}
    route = routing.get('binder') or routing.get('default') or {}
    if route:
        from ..providers import resolve_route, ProviderError
        try:
            resolved = resolve_route(config, 'default')
        except ProviderError:
            return {'enabled': False, 'source': 'agent.routing', 'reason': 'selected_route_unavailable_or_material_access_denied'}
        key = secret(resolved['credential_ref']) if resolved['credential_ref'] else ''
        if key:
            return {**resolved, 'enabled': True, 'api_key': key, 'source': 'agent.routing'}
        return {'enabled': False, 'source': 'agent.routing', 'reason': 'credential_missing'}
    legacy = config.get('llm') or {}
    if legacy.get('enabled') and legacy.get('base_url') and legacy.get('model'):
        key = secret(legacy.get('api_key_ref') or 'llm_api_key')
        if key:
            return {'enabled': True, 'base_url': legacy['base_url'], 'chat_path': legacy.get('chat_path') or '/chat/completions',
                    'model': legacy['model'], 'api_key': key, 'timeout_s': legacy.get('timeout_s') or 60,
                    'temperature': legacy.get('temperature') or 0, 'max_tokens': legacy.get('max_tokens') or 2000, 'source': 'llm'}
    return {'enabled': False}
