"""Single guarded dispatcher shared by native Harness and standard MCP.

Trusted call identities originate in the host/runtime, never tool arguments.
This module is a transport boundary; the Decimal kernel remains independent.
"""
from __future__ import annotations

from copy import deepcopy
import re
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .audit import digest
from .coverage import mask_financial_numbers
from .facts import FactStore
from .judge import AMOUNT_ACCOUNTS, RATIO_ACCOUNTS, Judge, _source_numeric_position, _source_semantic_issue, _source_amount_range
from .taxonomy import PER_SHARE_ACCOUNTS,SHARE_ACCOUNTS
from .models import AtomicClaim
from .rules import rulepack_root
from .source_semantics import freeze_source_dimensions, source_binding_issue, source_dimensions_from_text
from .metrics import METRIC_SPECS, metric_definitions, TURNOVER_METRICS
from .dimensions import claim_dimensions

BIND_FIELDS = ("company", "account", "period", "scope", "relation", "base_period")
from .metrics import unified_definition_catalog
from .smart.taxonomy import ACCOUNT_NAMES as SMART_ACCOUNT_NAMES
BIND_ACCOUNTS = ('', 'UNKNOWN', *sorted(AMOUNT_ACCOUNTS | RATIO_ACCOUNTS | PER_SHARE_ACCOUNTS | SHARE_ACCOUNTS |
                                      set(SMART_ACCOUNT_NAMES) | {row['account'] for row in unified_definition_catalog('smart')}))
BIND_SCOPES = ('', '合并', '母公司', '来源表')
BIND_RELATIONS = ('', 'value', 'yoy', 'qoq', 'pp_difference', 'share', 'ratio', 'cagr', 'gt', 'lt', 'ge', 'le', 'eq')
PERIOD_PATTERN = r'^20\d{2}(Y|H1|H2|[1-4]Q|Q[1-4]|9M)$'
# This is a directory of implemented operations, not a source of financial
# values. Keep unsupported formula cards out of this tool's metric enum.
ANALYSIS_METRICS = {
    'REVENUE': ('营业收入', 'disclosed', ['E1-value']),
    'COST': ('营业成本', 'disclosed', ['E1-value']),
    'NP': ('净利润', 'disclosed', ['E1-value']),
    'NP_PARENT': ('归母净利润', 'disclosed', ['E1-value']),
    'NP_PARENT_ADJ': ('扣非归母净利润', 'disclosed', ['E1-value']),
    'CFO': ('经营活动现金流量净额', 'disclosed', ['E1-value']),
    'ASSETS': ('资产总额', 'disclosed', ['E1-value']),
    'LIABILITIES': ('负债总额', 'disclosed', ['E1-value']),
    'EQUITY_PARENT': ('归属于母公司所有者权益', 'disclosed', ['E1-value']),
    'EPS': ('基本每股收益披露值', 'disclosed', ['E1-value']),
    'EPS_DILUTED': ('稀释每股收益披露值', 'disclosed', ['E1-value']),
    'ROE': ('净资产收益率披露值', 'disclosed', ['FIN-ROE']),
    'REVENUE_YOY': ('营业收入同比', 'computed', ['E5-yoy', 'TIME-same-report']),
    'NP_PARENT_YOY': ('归母净利润同比', 'computed', ['E5-yoy', 'TIME-same-report']),
    'CFO_YOY': ('经营活动现金流量净额同比', 'computed', ['E5-yoy', 'TIME-same-report']),
    'GROSS_MARGIN': ('毛利率', 'computed', ['FIN-gross-margin']),
    'NET_MARGIN': ('净利率', 'computed', ['FIN-net-margin']),
    'DEBT_RATIO': ('资产负债率', 'computed', ['FIN-debt-ratio']),
    'CASH_CONTENT': ('经营现金流含金量', 'computed', ['FIN-cash-content']),
    'NP_PARENT_RECOMPUTED': ('四输入扣非归母净利润重算', 'computed', ['FIN-non-recurring']),
    'NON_RECURRING_SHARE': ('非经常性损益占比', 'computed', ['FIN-non-recurring', 'FIN-non-recurring-share']),
}
ANALYSIS_METRICS.update({item.account:(item.label,'computed',[item.rule_id])
                         for item in METRIC_SPECS.values() if item.analysis})
from .smart.native_analysis import metric_catalog as smart_analysis_metric_catalog, project_analysis
SMART_ANALYSIS_METRICS = {row['metric']: row for row in smart_analysis_metric_catalog()}
ANALYSIS_KINDS = {
    'non_recurring_share': ('signals', '非经常性损益占比提示', 'FIN-non-recurring-share'),
    'low_cash_content': ('signals', '经营现金流含金量连续期间提示', 'FIN-low-cash-content'),
    'profit_cashflow_divergence': ('signals', '利润与经营现金流变动方向背离提示', 'FIN-divergence'),
    'disclosure_version_change': ('events', '披露版本变化', 'E9-version'),
    'accounting_policy_change': ('events', '会计政策变更', 'CHANGE-policy'),
    'accounting_estimate_change': ('events', '会计估计变更', 'CHANGE-estimate'),
    'prior_period_error': ('events', '前期差错更正', 'CHANGE-error'),
}
SOURCE_QUOTATION_STATUS = '原文引用，未作数字核查'
SLOT_INSTRUCTION = ('取得所需槽位后，原样将 slot_template 和 slot_ids 作为 '
                    'mj_submit_report 的 template 和 slots 提交并结束；'
                    '不得将 slots 的内容复制成自由文字或数字，不得自行重写公式。')


def analysis_metric_catalog(metric: str | None = None) -> list[dict]:
    return [{'metric': code, 'label': label, 'kind': kind, 'rule_ids': list(rules)}
            for code, (label, kind, rules) in ANALYSIS_METRICS.items() if not metric or code == metric]


def analysis_kind_catalog(category=None, kind=None) -> list[dict]:
    return [{'category': group, 'kind': code, 'label': label, 'rule_id': rule}
            for code, (group, label, rule) in ANALYSIS_KINDS.items()
            if (not category or group == category) and (not kind or code == kind)]


def _analysis_period_key(period: str) -> tuple:
    suffix = period[4:]
    quarter = int(suffix[1:]) if re.fullmatch(r'Q[1-4]', suffix) else (
        int(suffix[:1]) if re.fullmatch(r'[1-4]Q', suffix) else
        {'H1': 2, 'H2': 4, '9M': 3, 'Y': 4}.get(suffix, 0))
    return period[:4], quarter, suffix


FORBIDDEN = {"candidate_value", "fact_value", "expected", "observed", "formula", "verdict", "status", "value", "path", "role", "session_id", "resource_lock", "run_id", "call_id"}
PERMISSIONS = {
    "planner": {"mj_list_reports", "mj_list_mentions", "mj_read_context", "mj_fact_dimensions", "mj_get_rule", "mj_search_blocks", "mj_submit_binding", "mj_submit_bindings"},
    "verifier": {"mj_list_reports", "mj_list_mentions", "mj_read_context", "mj_fact_dimensions", "mj_get_rule", "mj_search_blocks", "mj_submit_binding", "mj_submit_bindings"},
    "reporter": {"mj_get_verdicts", "mj_analyze", "mj_submit_report"},
    "question": {"mj_list_reports", "mj_search_blocks", "mj_read_context", "mj_get_rule", "mj_get_verdicts", "mj_analyze", "mj_submit_report"},
    "coordinator": {"mj_list_reports", "mj_read_context", "mj_search_blocks", "mj_request_reparse", "mj_get_fact", "mj_get_facts", "mj_verify", "mj_verify_bindings", "mj_get_rule", "mj_get_verdicts", "mj_analyze"},
    "probe": {"mj_list_reports", "mj_search_blocks", "mj_read_context", "mj_get_rule"},
}
for _kb_role in ('planner', 'verifier', 'coordinator', 'question', 'probe'):
    PERMISSIONS[_kb_role].update({'mj_kb_list', 'mj_kb_search', 'mj_kb_read'})
    PERMISSIONS[_kb_role].add('mj_list_source_gaps')


class ToolDenied(ValueError):
    pass


# Only these public, static messages may cross the model repair boundary.
# Never use exception messages, input values, unknown JSON keys or execution
# identities to construct a rejection. Authorization errors remain ToolDenied.
_BINDING_ARGUMENT_ERRORS = {
    'invalid_binding_account': ('binding.account',
        'account must use a registered code: use the values of account_labels, '
        'not its Chinese caption keys. If the source definition is unknown, submit an empty string or UNKNOWN.',
        BIND_ACCOUNTS),
    'invalid_binding_scope': ('binding.scope',
        'scope must use a registered source-supported scope; submit an empty string when unknown.', BIND_SCOPES),
    'invalid_binding_relation': ('binding.relation',
        'relation must use a registered relation; submit an empty string when unknown.', BIND_RELATIONS),
    'invalid_binding_arguments': ('binding',
        'submit mention_id and binding with all six string fields: '
        'company, account, period, scope, relation, base_period; no extra fields.', None),
    'invalid_bindings_arguments': ('bindings',
        'submit mention_id/binding objects within the registered batch cap; each binding '
        'must contain all six string fields and no extra fields.', None),
}


class FinancialArgumentError(ToolDenied):
    """A repairable binding rejection whose public content is registry-owned."""

    def __init__(self, code: str):
        if code not in _BINDING_ARGUMENT_ERRORS:
            raise ValueError('Unknown financial argument rejection code')
        self.code = code
        super().__init__(_BINDING_ARGUMENT_ERRORS[code][1])

    def rejection(self) -> dict:
        # Rebuild rather than serializing the exception or a caller-supplied
        # dict. Even tampered exception args/attributes cannot leak through it.
        code = self.code if self.code in _BINDING_ARGUMENT_ERRORS else 'invalid_binding_arguments'
        field, message, allowed = _BINDING_ARGUMENT_ERRORS[code]
        error = {'code': code, 'field': field, 'message': message}
        if allowed is not None:
            error['allowed'] = list(allowed)
        return {'status': 'rejected', 'accepted': False, 'error': error}


_VERIFICATION_REPAIR_RULES = frozenset({'E1-value','E5-yoy','FIN-qoq','FIN-EPS','FIN-single-quarter','FIN-pp-difference','FIN-cagr','FIN-range','SOURCE-threshold-entailment'} | {item.rule_id for item in METRIC_SPECS.values()})


class FinancialRequirementError(ToolDenied):
    """Registry-only prerequisites, safely repairable by the native loop."""

    def __init__(self, required_rule_ids, *, facts_required=False):
        self.required_rule_ids = tuple(sorted(set(required_rule_ids) & _VERIFICATION_REPAIR_RULES))
        self.facts_required = facts_required is True
        super().__init__('read facts and '+', '.join(self.required_rule_ids)+' in this session before verification')

    def rejection(self):
        # Neither exception text, identity nor hostile tool arguments may
        # become a public diagnostic. Re-filter even tampered attributes.
        required = self.required_rule_ids if isinstance(self.required_rule_ids, (tuple, list, set, frozenset)) else ()
        rules = [rule for rule in sorted(_VERIFICATION_REPAIR_RULES) if rule in required]
        return {'status':'rejected', 'accepted':False,
                'error':{'code':'verification_reads_required',
                         'message':'Read the fact lookup and every required rule in this same session; '
                                   'missing facts still require rules and must receive a kernel abstention. '
                                   'Repair prerequisites once, then retry verification.',
                         'required_rule_ids':rules, 'facts_required':self.facts_required is True},
                'required_rule_ids':rules, 'facts_required':self.facts_required is True}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


STRING = {"type": "string", "minLength": 1, "maxLength": 500}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100}
PAGE_FIELDS = {'cursor': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}
BIND_PROPERTIES = {key: {'type': 'string', 'maxLength': 200} for key in BIND_FIELDS}
BIND_PROPERTIES.update(
    account={'type': 'string', 'enum': list(BIND_ACCOUNTS),
             'description': '登记科目代码；使用 account_labels 映射的值（如 REVENUE），不能提交中文标签键。原文定义未知时填空或 UNKNOWN。'},
    scope={'type': 'string', 'enum': list(BIND_SCOPES),
           'description': '仅提交原文有依据的合并或母公司；来源表仅用于宿主已验证的明确引用表身份；未知填空。'},
    relation={'type': 'string', 'enum': list(BIND_RELATIONS),
              'description': '原文登记关系；value 为金额/比率，yoy 为同比，qoq 为环比，pp_difference 为明确基期的百分点差，未知填空。'},
)
TOOL_SCHEMAS = {
    "mj_list_reports": _schema({}),
    'mj_list_source_gaps':_schema({**PAGE_FIELDS,'query':STRING,'report_id':STRING}),
    "mj_list_mentions": _schema({"cursor": {"type": "integer", "minimum": 0}, "limit": LIMIT}),
    "mj_read_context": _schema({"mention_id": STRING,
        'context_blocks': {'type': 'integer', 'enum': [0, 1, 2, 4, 8, 12, 16], 'description': '显式N读取同文档同页前后各至多N块，0只读定位块；未指定时标题读取后续正文。绑定角色全部遮数，协调器可读原始列头和单位；各段引用独立且不产生事实。'}}, ["mention_id"]),
    "mj_fact_dimensions": _schema({"mention_id": STRING}, ["mention_id"]),
    "mj_get_rule": _schema({"rule_id": STRING}, ["rule_id"]),
    "mj_search_blocks": _schema({
        "query": {**STRING, 'description': '空格分词按AND匹配同一个原文块；无命中可先搜索一个标题或科目，再读取邻近上下文，不跨块拼接条件。'},
        "limit": {**LIMIT, 'description': '协调器和绑定角色默认10，最多100；追问默认3，最多5。使用next_cursor继续。'},
        'report_id': STRING,
        'cursor': {'type': 'integer', 'minimum': 0, 'description': '首次0，后续填写前次返回的next_cursor；None表示没有下一页。'},
        'context_blocks': {'type': 'integer', 'enum': [0, 1, 2, 4, 8, 12, 16],
            'description': '读取同文档同页前后各至多N个非空块；0只读命中块。协调器默认2，追问默认标题后续正文，绑定角色遮数。'}}, ["query"]),
    'mj_kb_list': _schema(PAGE_FIELDS),
    'mj_kb_search': _schema({**PAGE_FIELDS, 'query': STRING, 'document_id': STRING,
                           'intent': {'type':'string','enum':['search','definition'],
                                      'description':'定义问题使用definition并提供一个完整术语；只返回该术语作主语的定义，不以应用/修正术语代替。'}}, ['query']),
    'mj_kb_read': _schema({'block_id': STRING}, ['block_id']),
    "mj_submit_binding": _schema({"mention_id": STRING, "binding": _schema(BIND_PROPERTIES, list(BIND_FIELDS))}, ["mention_id", "binding"]),
    "mj_request_reparse": _schema({"block_id": STRING, "binding_id": STRING}, ["block_id", "binding_id"]),
    "mj_get_fact": _schema({"binding_id": STRING}, ["binding_id"]),
    "mj_verify": _schema({"binding_id": STRING}, ["binding_id"]),
    "mj_get_verdicts": _schema({**PAGE_FIELDS, 'query': STRING, 'claim_id': STRING}),
    "mj_analyze": _schema({**PAGE_FIELDS, 'period': {'type': 'string', 'pattern': PERIOD_PATTERN},
                           'metric': {'type': 'string', 'enum': list(ANALYSIS_METRICS),
                                      'description': '营业收入同比=REVENUE_YOY；归母净利润同比=NP_PARENT_YOY；经营现金流同比=CFO_YOY。'},
                           'catalog_only': {'type': 'boolean', 'description': '仅查询已实现指标目录与期间，不计算或返回金额。'},
                           'category': {'type': 'string', 'enum': ['metrics', 'signals', 'events'],
                                        'description': '指标、复核信号或披露事件；会计变更用events，现金流提示用signals。'},
                           'kind': {'type': 'string', 'enum': list(ANALYSIS_KINDS),
                                    'description': '会计政策变更=accounting_policy_change；现金流背离=profit_cashflow_divergence。'},
                           "analysis_id": {"type": "string", "enum": ["financial_summary"]}}, ["analysis_id"]),
    "mj_submit_report": _schema({"template": {"type": "string", "maxLength": 16000}, "slots": {"type": "array", "items": STRING, "maxItems": 1000}}, ["template", "slots"]),
}
TOOL_SCHEMAS['mj_submit_bindings'] = _schema({'bindings': {'type': 'array', 'items': TOOL_SCHEMAS['mj_submit_binding'], 'minItems': 1, 'maxItems': 8}}, ['bindings'])
for _batch_tool in ('mj_get_facts', 'mj_verify_bindings'):
    TOOL_SCHEMAS[_batch_tool] = _schema({'binding_ids': {'type': 'array', 'items': STRING,
                                                       'minItems': 1, 'maxItems': 8, 'uniqueItems': True}}, ['binding_ids'])
DESCRIPTIONS = {
    "mj_list_reports": "列出已锁定文档的资源ID、公司和期间，不包含金额。",
    'mj_list_source_gaps':'分页查看登记材料中尚缺报表口径、指标定义等来源条件的条目，只返回维度和原因，不返回金额或原文数字；这些条目不是Fact，也未形成verified判定。可query检索标签、period、公司或gap_kind，按report_id限定材料。补齐源方法后须新版本建库，不允许模型填写数字或定义授权。',
    "mj_list_mentions": "分页列出当前角色待绑定的原文提及。金额已屏蔽。",
    "mj_read_context": "按提及ID或检索返回块ID读允许访问的上下文。question可获得带冻结来源/页/哈希的原文引用槽位，原文数字未核查；按slot_template/slot_ids提交，不可称verified。",
    "mj_fact_dimensions": "读取可用事实的公司、科目、期间、口径维度；不返回金额。",
    "mj_get_rule": "按规则ID读取授权规则卡。",
    'mj_kb_list': '分页列出任务冻结的唯一外部会计知识库。未知/未来公开日明确标记；外部资料不属于签发规则。',
    'mj_kb_search': '在已冻结会计准则/解释/问答/案例原文中检索；空格分词按AND匹配。定义问题使用intent=definition且query只填完整术语；仅完整术语作主语的“是指/定义为”等定义可回答，修正术语和应用段落不能代替；无定义返回缺材。只返回截至任务日期可引用原文，未知日期不猜测。question使用受控原文槽位；原文不能替换mj_get_rule或产生事实/判定。',
    'mj_kb_read': '按检索返回的知识库block_id读取原文，不能提供路径、数值或公式。外部版本和有效性仅是声明，金融判断仍使用签发规则。',
    "mj_search_blocks": "在锁定文档检索原文。空格分词按AND在同一原文块匹配，不跨标题和数值块拼接；可按report_id筛选，用cursor/next_cursor分页。空结果说明本次未命中，不证明材料缺失；可分别检索标题或科目再read_context查看邻近列头和单位。question默认三个命中并提供受控引用槽位，原文数字未核查；绑定角色遮数。重复无新增信息时明确停止。",
    "mj_submit_binding": "提交六字段语义绑定；未知填空。禁止候选金额、公式和判定。",
    "mj_submit_bindings": "一次原子提交本会话至多八条六字段绑定。全部校验成功才写入，未知填空，禁止金额。",
    "mj_request_reparse": "仅一致绑定协调器，在本会话get_fact返回缺项后，可对登记PDF块前后至多三页确定性重解析。运行中实际读取原页并保存staged补证，任务冻结后才能供新版本使用；不修改当前facts/判定。禁止金额、公式和路径。每提及在本任务全局最多两次，换块、换会话及恢复均不重置；每块也最多两次。相同块未获得新信息时停止重复请求。",
    "mj_get_fact": "按已一致绑定ID查询事实；不接受候选数字。核查前必须实际读取返回required_rule_ids中的每张规则。",
    "mj_verify": "由宿主读取候选原文并调用确定性内核；模型不能提供数值。",
    "mj_get_facts": "批量按至多八个已一致绑定ID查询事实，逐项返回binding_id与事实；不接受候选数字。",
    "mj_verify_bindings": "批量核查已读取事实的至多八个绑定ID。数值来自宿主原文，返回逐项确定性判定，不接受金额、公式或判定。",
    "mj_get_verdicts": "分页只读研报草稿的冻结判定，可按query/claim_id筛选。财报分析任务没有草稿判定，请直接用mj_analyze。默认五条；取得槽位后按slot_template/slot_ids提交报告。",
    "mj_analyze": "执行登记财报分析。营收同比用REVENUE_YOY，本期传period。category=signals查现金流等复核提示，category=events查会计变更等事件，可用kind筛选。会计政策变更kind=accounting_policy_change，现金流背离kind=profit_cashflow_divergence。catalog_only查询目录。默认当前期间五项；用slot_template/slot_ids提交，不接受自由公式。",
    "mj_submit_report": "提交引用冻结槽位的文字模板，禁止自由数字和无依据结论。",
}


def tool_definitions(role: str, batch_size: int = 8, *, smart_profile: bool = False) -> list[dict]:
    return [{"name": name, "description": DESCRIPTIONS[name].replace('八', str(batch_size)) if batch_size != 8 else DESCRIPTIONS[name],
             "inputSchema": tool_schema(name, batch_size, smart_profile=smart_profile)}
            for name in sorted(PERMISSIONS[role])]


def tool_schema(name: str, batch_size: int = 8, *, smart_profile: bool = False) -> dict:
    from copy import deepcopy
    schema = deepcopy(TOOL_SCHEMAS[name])
    if name in {'mj_submit_bindings', 'mj_get_facts', 'mj_verify_bindings'}:
        field = 'bindings' if name == 'mj_submit_bindings' else 'binding_ids'
        schema['properties'][field]['maxItems'] = batch_size
    if smart_profile and name == 'mj_analyze':
        schema['properties']['metric']['enum'] = list(SMART_ANALYSIS_METRICS)
    return schema


def _validate(value: Any, schema: dict, tool_name: str | None = None) -> None:
    # The same schemas are served by MCP. JSON Schema validation happens here,
    # before any resource or state access, even for internal callers.
    import jsonschema
    try:
        jsonschema.validate(value, schema)
    except jsonschema.ValidationError as exc:
        if tool_name not in {'mj_submit_binding', 'mj_submit_bindings'}:
            # Preserve the strict exception contract without jsonschema's
            # default rendered instance/schema, which can contain credentials.
            raise jsonschema.ValidationError('Financial tool arguments do not match the registered schema') from None
        # Accept only the fixed schema paths, never echo arbitrary property
        # names or indexes supplied by a hostile model.
        path = list(exc.absolute_path)
        if tool_name == 'mj_submit_bindings' and len(path) == 4 and path[0] == 'bindings' and type(path[1]) is int:
            path = path[2:]
        if len(path) == 2 and path[0] == 'binding' and path[1] in {'account', 'scope', 'relation'}:
            code = 'invalid_binding_' + path[1]
        else:
            code = ('invalid_bindings_arguments' if tool_name == 'mj_submit_bindings'
                    else 'invalid_binding_arguments')
        raise FinancialArgumentError(code) from None


def _blind_source_metadata(value: Any, *, period_header: bool = False) -> Any:
    """Mask nested source strings while retaining typed coordinates and enums."""
    if isinstance(value, str):
        if period_header:
            from .table_mentions import _header
            tokens = [token for token in re.split(r'[\s|]+', value.strip()) if token]
            if tokens and all(_header(token) is not None for token in tokens):
                return value
        return mask_financial_numbers(value, preserve_bare_years=False)
    if isinstance(value, dict):
        return {key: _blind_source_metadata(item, period_header=period_header or key == 'headers')
                for key, item in value.items()}
    if isinstance(value, list):
        return [_blind_source_metadata(item, period_header=period_header) for item in value]
    if isinstance(value, tuple):
        return tuple(_blind_source_metadata(item, period_header=period_header) for item in value)
    return value


class FinancialDispatcher:
    def __init__(self, run_id: str, resource_lock: str, claims: list[AtomicClaim], facts: list,
                 blocks: list[dict], reports: list[dict], report_date: str, audit=None,
                 budget: dict | None = None, emit=None, *, task_company: str | None = None,
                 task_period: str | None = None, source_resources: list[dict] | None = None,
                 reparse_root=None, reparse_source_generation: dict | None = None, reparse_cancel_event=None,
                 knowledge_registry=None, secret_values: list[str] | None = None, source_gaps: list[dict] | None = None):
        self.run_id, self.resource_lock, self.audit = run_id, resource_lock, audit
        # Keep the host-owned append-only list, so keys resolved after prepare
        # are also forbidden in financial tool inputs. No credential is logged.
        self.secret_values = secret_values if secret_values is not None else []
        # Freeze host-extracted semantics before any role proposes bindings.
        # Rebound claims retain this proof on resume instead of relocking it.
        self.claims = {claim.claim_id: freeze_source_dimensions(claim) for claim in claims}
        if task_company is not None and not isinstance(task_company, str):
            raise ValueError('trusted task_company must be a string')
        if task_period and (not isinstance(task_period, str) or not re.fullmatch(PERIOD_PATTERN, task_period)):
            raise ValueError('trusted task_period must be a registered period')
        self.task_period = task_period or None
        claim_companies = {claim.company for claim in claims if claim.company}
        fact_companies = {fact.company for fact in facts if fact.company}
        self.task_company = task_company or (next(iter(claim_companies)) if len(claim_companies) == 1 else
                                            next(iter(fact_companies)) if len(fact_companies) == 1 else '')
        self.store = FactStore([fact for fact in facts if self.task_company and fact.company == self.task_company])
        selected_reports = []
        for report in reports:
            if report.get('company') and report['company'] != self.task_company:
                continue
            declared = set(report.get('companies') or [])
            if report.get('company'):
                declared.add(report['company'])
            if declared and (not self.task_company or self.task_company not in declared):
                continue
            filtered = dict(report)
            if report.get('companies'):
                filtered['companies'] = [self.task_company]
            selected_reports.append(filtered)
        excluded_reports = {report.get('report_id') for report in reports} - {report.get('report_id') for report in selected_reports}
        excluded_reports.discard(None)
        self.blocks = {block['id']: block for block in blocks if block.get('report_id') not in excluded_reports
                       and (not block.get('company') or block['company'] == self.task_company)}
        self.reports, self.report_date = selected_reports, report_date
        allowed_report_ids = {report.get('report_id') for report in selected_reports}
        self.reparse_resources = [dict(resource) for resource in source_resources or []
                                  if resource.get('id') in allowed_report_ids and resource.get('path')]
        self.reparse_root = reparse_root
        self.reparse_source_generation = reparse_source_generation
        self.reparse_cancel_event = reparse_cancel_event
        self.source_resources = {}
        source_paths = {resource.get('id'): resource.get('path') for resource in source_resources or []}
        for resource in source_resources or []:
            resource_id = resource.get('id')
            if resource_id != 'draft' and resource_id not in allowed_report_ids:
                continue
            sha = resource.get('sha256', '')
            if re.fullmatch(r'[a-fA-F0-9]{64}', sha):
                from pathlib import Path
                self.source_resources[resource_id] = {'id': resource_id, 'sha256': sha.lower(),
                    'name': resource.get('name') or Path(resource.get('path') or resource_id).name,
                    'publish_date': resource.get('publish_date') or ''}
        for report in selected_reports:
            sha = report.get('sha256', '')
            if report.get('report_id') and re.fullmatch(r'[a-fA-F0-9]{64}', sha):
                registered = self.source_resources.setdefault(report['report_id'], {'id': report['report_id'],
                    'sha256': sha.lower(), 'name': report.get('name') or report['report_id']})
                if report.get('publish_date'):
                    registered['publish_date'] = report['publish_date']
        for resource_id, resource in self.source_resources.items():
            if resource_id == 'draft':
                resource['publish_date'] = self.report_date
                resource['publish_date_basis'] = 'task_draft_as_of_date'
                continue
            if not resource.get('publish_date'):
                sha12 = resource['sha256'][:12]
                dates = {fact.publish_date for fact in self.store.facts if fact.publish_date and
                         (fact.source_doc.startswith(sha12) or any(e.startswith(sha12 + ':') for e in fact.evidence)
                          or (source_paths.get(resource_id) and fact.source_path and
                              Path(fact.source_path).resolve() == Path(source_paths[resource_id]).resolve()))}
                if len(dates) == 1:
                    resource['publish_date'] = next(iter(dates))
                    resource['publish_date_basis'] = 'locked_fact_document_metadata'
        self.source_gaps=[]
        gap_keys=('id','doc_sha256','company','period','dimension_kind','dimension_id','currency','account',
                  'scope','gap_kind','reason','table_label','evidence','report_id','publish_date','public_at_cutoff',
                  'status','needed_method')
        for gap in source_gaps or []:
            if not isinstance(gap,dict) or gap.get('company')!=self.task_company:
                continue
            if not re.fullmatch(r'sg-[a-f0-9]{24}',str(gap.get('id',''))) or not re.fullmatch(r'[a-f0-9]{64}',str(gap.get('doc_sha256',''))):
                raise ValueError('Source gap identity lacks a frozen document hash')
            matches=[key for key,resource in self.source_resources.items() if resource.get('sha256')==gap['doc_sha256'] and key!='draft']
            if gap.get('report_id'):
                matches=[key for key in matches if key==gap['report_id']]
            if len(matches)!=1:
                raise ValueError('Source gap does not identify one allowed frozen report')
            entry={key:gap[key] for key in gap_keys if key in gap}
            for key in ('reason','table_label','needed_method'):
                if key in entry:
                    if not isinstance(entry[key],str):
                        raise ValueError('Source gap descriptive metadata must be text')
                    entry[key]=mask_financial_numbers(entry[key],preserve_bare_years=False)
            entry['report_id']=matches[0]
            entry['publish_date']=self.source_resources[matches[0]].get('publish_date','')
            # Raw values and source fragments stay in host artifacts. This
            # metadata can neither create a fact nor serve as a quotation.
            entry.update(eligible_as_fact=False,reference_status='unresolved_source_metadata')
            if self._contains_secret(entry):
                raise ValueError('Source gap metadata contains a protected credential')
            self.source_gaps.append(entry)
        self.source_references: dict[str, dict] = {}
        self.diagnostic_slots: dict[str, dict] = {}
        self.submitted_slots: dict[str, list[str]] = {}
        self.sessions: dict[str, dict] = {}
        self.bindings: dict[str, dict[str, dict]] = {"planner": {}, "verifier": {}}
        self.agreements: dict[str, dict] = {}
        self.results: list[dict] = []
        self.slots: dict[str, str] = {}
        self.templates: dict[str, str] = {}
        self.analysis_snapshots: dict[str, dict] = {}
        self.batch_size = 8
        self.analysis_scope = '合并'
        self.reparse_attempts: dict[str, int] = {}
        self.reparse_claim_attempts: dict[str, int] = {}
        self.searches: set[tuple[str, str]] = set()
        self.calls: set[tuple[str, str]] = set()
        self.trace: list[dict] = []
        self.lock = threading.RLock()
        self.budget = budget or {}
        self.started = time.monotonic()
        self.emit = emit or (lambda *a, **k: None)
        self.source_text = ''
        self.knowledge_registry = knowledge_registry
        self.knowledge_rule_sources, self.knowledge_rule_cards = {}, []
        if knowledge_registry:
            import json
            self.knowledge_rule_sources = json.loads((rulepack_root()/'sources.json').read_text(encoding='utf-8'))
            self.knowledge_rule_cards = json.loads((rulepack_root()/'cards.json').read_text(encoding='utf-8'))

    def restore_reparse_budgets(self, block_attempts, claim_attempts):
        """Restore only bounded, registered counters, never below recorded work."""
        def checked(raw, registered):
            if (not isinstance(raw, dict) or any(type(key) is not str or key not in registered
                    or type(value) is not int or not 0 <= value <= 2 for key, value in raw.items())):
                raise ValueError('Invalid reparse budget checkpoint')
            return dict(raw)

        blocks = checked(block_attempts, self.blocks)
        claims = checked(claim_attempts, self.claims)
        observed_blocks, observed_claims = {}, {}
        for row in self.trace:
            output = row.get('output', {})
            if (row.get('tool') != 'mj_request_reparse' or row.get('status') != 'success'
                    or output.get('status') not in {'ready', 'staged', 'no_new_information', 'conflicts'}):
                continue
            block = row.get('input', {}).get('block_id')
            claim = row.get('input', {}).get('binding_id')
            if block not in self.blocks or claim not in self.claims:
                raise ValueError('Reparse checkpoint references an unregistered resource')
            observed_blocks[block] = observed_blocks.get(block, 0) + 1
            observed_claims[claim] = observed_claims.get(claim, 0) + 1
        if (any(blocks.get(key, 0) < value for key, value in observed_blocks.items())
                or any(claims.get(key, 0) < value for key, value in observed_claims.items())):
            raise ValueError('Reparse checkpoint would reset consumed attempts')
        self.reparse_attempts, self.reparse_claim_attempts = blocks, claims

    def _knowledge_payload(self, raw, *, blind=False, quote=False):
        from .knowledge import RAW_NOTICE, blind_knowledge_payload
        if not raw.get('available'):
            proof = {'status': 'knowledge_material_required', 'reason': raw.get('reason') or 'knowledge_not_available',
                     'resource_lock': self.resource_lock, 'verified': False, 'provenance': 'host_knowledge_registry'}
            slot = 'diagnostic.knowledge.'+digest(proof)[:20]+'.text'
            self.slots[slot] = ('## 待补材料\n未取得请求术语的完整定义；提及、应用段落或修正术语不能代替该术语定义，请补充明确来源与版本。'
                                if proof['reason']=='no_definition_match' else
                                '## 待补材料\n未取得截至任务日期可引用的会计材料。未知或未来公开日不能作为回溯依据；请补充明确来源与版本。')+RAW_NOTICE
            self.diagnostic_slots[slot] = proof
            return {'available': False, 'reason': proof['reason'], 'diagnostics': [{'slot_id': slot, **proof}],
                    'slot_ids': [slot], 'slot_template': '# 待补材料\n{{'+slot+'}}', 'source_references': [], 'notice': RAW_NOTICE}
        if not quote:
            raw = dict(raw, rule_references=self.knowledge_registry.rule_links(raw['knowledge_id'],
                       source_register=self.knowledge_rule_sources, rule_cards=self.knowledge_rule_cards))
            return blind_knowledge_payload(raw) if blind else raw
        registry = self.knowledge_registry
        document = registry.documents[raw['knowledge_id']]
        text = raw['text']
        slot = 'source.kb.'+digest([registry.resource_lock, raw['block_id'], raw['span_sha256']])[:24]+'.text'
        proof = {'slot_id': slot, 'report_id': raw['knowledge_id'], 'knowledge_id': raw['knowledge_id'],
                 'knowledge_lock': registry.resource_lock, 'material_kind': 'accounting_knowledge',
                 'doc_sha256': raw['document_sha256'], 'page': raw['page'], 'bbox': raw['bbox'],
                 'block_id': raw['block_id'], 'block_sha256': raw['span_sha256'], 'quote_sha256': raw['span_sha256'],
                 'quote_text': text, 'quote_start': 0, 'quote_end': len(text), 'evidence_id': raw['evidence_id'],
                 'company': self.task_company, 'name': document['name'], 'title': document['name'],
                 'publish_date': raw['metadata'].get('publish_date'), 'as_of_date': self.report_date,
                 'metadata': raw['metadata'], 'effective_status': raw['effective_status'],
                 'reference_status': 'unverified_source_quotation', 'reference_label': RAW_NOTICE,
                 'status': 'unverified_source_quotation', 'authorizes_judgement': False}
        from .presentation import escape_markdown
        citation = ('## 原文\n'+RAW_NOTICE+'\n\n> '+escape_markdown(text)+'\n\n来源：'+escape_markdown(document['name'])+
                    '；页码：'+(str(raw['page']) if raw['page'] else '逻辑段落，无物理页码')+'；证据 ID：'+escape_markdown(raw['evidence_id']))
        self.slots[slot], self.source_references[slot] = citation, proof
        return {key: raw[key] for key in ('knowledge_id', 'block_id', 'page', 'text', 'metadata', 'effective_status', 'notice')} | {
            'available': True, 'reference_status': 'unverified_source_quotation', 'material_kind': 'accounting_knowledge',
            'rule_references': registry.rule_links(raw['knowledge_id'], source_register=self.knowledge_rule_sources, rule_cards=self.knowledge_rule_cards),
            'source_references': [{key: proof[key] for key in ('slot_id', 'report_id', 'block_id', 'page', 'evidence_id', 'reference_status', 'material_kind')}],
            'slot_ids': [slot], 'slot_template': '# 原文\n{{'+slot+'}}', 'slot_instruction': SLOT_INSTRUCTION}

    def _quotation_is_current(self, proof):
        if proof.get('material_kind') == 'accounting_knowledge':
            registry = self.knowledge_registry
            if not registry or proof.get('knowledge_lock') != registry.resource_lock:
                return False
            try:
                raw = registry.read(proof['block_id'], as_of_date=self.report_date)
            except ValueError:
                return False
            return raw.get('available') and raw['span_sha256'] == proof['quote_sha256']
        return not self._source_temporal_issue(self.blocks[proof['block_id']])

    def binding_context(self, claim: AtomicClaim) -> str:
        text = claim.text
        local, valid = _source_numeric_position(claim)
        row_label = claim.table_context.get('row_label')
        # Deterministic table extraction keeps the exact row label followed by
        # cells in ``text``; period headers live separately in table metadata.
        # A cell equal to 20xx is still a financial value, not a guessed year.
        cells_start = (len(row_label) if isinstance(row_label, str) and row_label
                       and text.startswith(row_label) else None)
        if claim.source_numeric_start is None and self.source_text:
            origins = [m.start() for m in re.finditer(re.escape(text), self.source_text)]
            origin = next((p for p in origins if p <= claim.start < p + len(text)), None)
            if origin is not None:
                local = claim.start - origin
        def redact(match):
            if match.start() == local and claim.numeric_text:
                trailing = match[0][len(match[0].rstrip()):] if not match['unit'] else ''
                return '[待绑定数值]' + (match['unit'] or '') + trailing
            if cells_start is not None and match.start() >= cells_start:
                trailing = match[0][len(match[0].rstrip()):] if not match['unit'] else ''
                return '[数值已屏蔽]' + (match['unit'] or '') + trailing
            return match[0]
        from .mentions import VALUE_RE
        return mask_financial_numbers(VALUE_RE.sub(redact, text), preserve_bare_years=False)

    @staticmethod
    def source_labels(claim: AtomicClaim) -> dict[str, str]:
        """Small source caption dictionary, never a value-based fact lookup.

        Keep longest captions rather than their contained aliases (扣非净利润
        must not also recommend 净利润). Codes help both isolated binders use
        the same vocabulary; neither receives the other's selected binding.
        """
        from .mentions import ACCOUNT_LABELS, account_label_pattern
        compact = re.sub(r'\s+', '', str(claim.table_context.get('row_label') or claim.text))
        matches = [(m.start(), m.end(), label, code) for label, code in ACCOUNT_LABELS
                   for m in re.finditer(account_label_pattern(label), compact)]
        matches = [row for row in matches if not any(
            other[0] <= row[0] and other[1] >= row[1] and other[:2] != row[:2]
            for other in matches)]
        labels = {label: code for _, _, label, code in matches}

        # Table parsers already froze the source account before either blind
        # binder sees the mention.  A customer/business row often has no
        # account words in its row label (the label is the counterparty or
        # product name), and broker compact rows deliberately keep a
        # different canonical identity from the statutory same-name item.
        # Expose those registered identities explicitly so a model does not
        # have to guess from a masked amount or a nearby fact.
        context = claim.table_context if isinstance(claim.table_context, dict) else {}
        source_account = context.get('account_hint')
        if isinstance(source_account, str) and source_account in BIND_ACCOUNTS:
            labels.setdefault(source_account, source_account)
        definition = context.get('definition_id')
        if definition == 'metric:revenue-share':
            # REVENUE_SHARE is a ratio with a registered numerator/denominator
            # definition.  Keep it distinct from customer revenue even when
            # the visible row only contains the customer name.
            labels.setdefault('REVENUE_SHARE', 'REVENUE_SHARE')
        return labels

    @staticmethod
    def _canonical_binding_account(claim: AtomicClaim, account: str) -> tuple[str, str | None]:
        """Map a safe source alias to the frozen canonical account.

        Broker compact tables use ``BROKER_OTHER_*`` identities so a same-name
        statutory item can never be substituted.  Models trained on ordinary
        financial vocabulary frequently return ``OTHER_*`` for those rows.
        Likewise a dimension table whose header explicitly says ``占营业收入
        比例`` is a source-proven ``REVENUE_SHARE`` even if the row label is a
        customer name and a binder chooses the numerator account.  These are
        source-local aliases only; no amount/value or fact lookup is involved.
        Unknown or unrelated accounts remain unchanged and are rejected by
        the normal frozen-source guard.
        """
        source = claim.table_context if isinstance(claim.table_context, dict) else {}
        try:
            proof = source_dimensions_from_text(claim)
        except (TypeError, ValueError):
            return account, None
        target = proof.get('account')
        broker_aliases = {
            'BROKER_OTHER_CURRENT_ASSETS': 'OTHER_CURRENT_ASSETS',
            'BROKER_OTHER_CURRENT_LIABILITIES': 'OTHER_CURRENT_LIABILITIES',
            'BROKER_OTHER_NONCURRENT_ASSETS': 'OTHER_NONCURRENT_ASSETS',
            'BROKER_OTHER_NONCURRENT_LIABILITIES': 'OTHER_NONCURRENT_LIABILITIES',
        }
        if target in broker_aliases and account == broker_aliases[target]:
            return target, 'broker_compact_row_alias'
        # A ratio header is authoritative for a dimension row.  This narrow
        # mapping cannot turn a normal company REVENUE into a share because it
        # requires both a source dimension and the registered definition ID.
        if (target == 'REVENUE_SHARE' and account == 'REVENUE'
                and proof.get('dimension_kind') in {'customer', 'business'}
                and proof.get('definition_id') == 'metric:revenue-share'):
            return target, 'dimension_revenue_share_alias'
        return account, None

    @staticmethod
    def source_dimensions(claim: AtomicClaim) -> dict:
        from .scope import source_scope_dimensions
        proof = source_dimensions_from_text(claim)
        dimensions = {**proof, **source_scope_dimensions(claim), 'source_provenance': proof['provenance']}
        smart = claim.table_context.get('smart_binding')
        if isinstance(smart, dict) and claim.table_context.get('smart_cid'):
            dimensions.update(smart, scope_header=smart['scope'], company_provenance='host_locked_issuer_context',
                              source_provenance='host_smart_deterministic_parser',
                              scope_assumption=claim.table_context.get('scope_assumption', False),
                              validation_policy=claim.table_context.get('validation_policy'))
            original = claim.table_context.get('smart_claim', {})
            dimensions.update(smart_entity=original.get('entity', ''),
                              smart_segment=original.get('segment', ''),
                              smart_notes=original.get('notes', []),
                              **claim.table_context.get('smart_dimensions', {}))
            if original.get('entity') in {'company', 'segment'}:
                # Sentence continuation and segment recognition are frozen
                # host evidence, even if the native source row is shortened.
                dimensions.update(source_company_conflict=False, source_company_ambiguous=False,
                                  source_company_subjects=[], company_provenance='host_locked_issuer_context')
        return dimensions

    def mention_context(self, claim: AtomicClaim, blind=True, *, compact=False) -> dict:
        dimensions = self.source_dimensions(claim)
        # A local omitted subject may use the already frozen task issuer.
        # This is shared host authority, never either binder's selection;
        # an explicit conflicting/ambiguous source subject takes priority.
        issuer_applicable = bool(
            self.task_company
            and dimensions.get('company_provenance') == 'host_locked_issuer_context'
            and dimensions.get('company') == claim.company == self.task_company
            and not dimensions.get('source_company_conflict')
            and not dimensions.get('source_company_ambiguous'))
        issuer_context = {'company': self.task_company if issuer_applicable else '',
                          'provenance': 'host_locked_issuer_context',
                          'authority': 'frozen_task_company',
                          'source_resource_id': 'source-dimensions',
                          'applicable': issuer_applicable}
        if compact:
            from .binding_metadata import compact_binding_metadata
            dimensions = compact_binding_metadata(dimensions,
                redact=_blind_source_metadata if blind else None)
        context = {"mention_id": claim.claim_id, "text": self.binding_context(claim) if blind else claim.text,
                   "unit": claim.unit, "company_hint": claim.company,
                   "account_labels": self.source_labels(claim),
                   "source_dimensions": (_blind_source_metadata(dimensions)
                                         if blind and not compact else dimensions),
                   # Only fixed authority identifiers and the trusted issuer
                   # appear here; preserve a registered name such as 360公司.
                   "issuer_context": issuer_context}
        if compact and dimensions.get('metadata_view') == 'compact-binding.v1':
            # The public reader checks the session's mention scope and masks
            # amounts for both binders. This is an actual resource-ID tool
            # path, not a filesystem path or a second, weaker proof.
            context['metadata_reference'] = {
                'tool': 'mj_read_context', 'params': {'mention_id': claim.claim_id},
                'source_resource_id': 'source-dimensions'}
        if claim.table_context:
            # Labels and headers may repeat candidate amounts or ratios.
            # Mask a fresh metadata tree so the frozen source remains intact.
            metadata = claim.table_context
            if compact:
                metadata = {key: value for key, value in metadata.items() if key in {
                    'title', 'row_label', 'column_index', 'period_source', 'account_hint',
                    'period_hint', 'scope_hint', 'unit_hint', 'relation_hint',
                    'numeric_parse_status','semantic_review_required','source_account_unresolved'}}
                metadata['headers'] = [{'text': row.get('text', '')}
                                       for row in claim.table_context.get('headers', [])]
            context['table_context'] = _blind_source_metadata(metadata) if blind else metadata
        from .source_semantics import unresolved_source_definition_issue
        issue = _source_semantic_issue(claim) or unresolved_source_definition_issue(claim)
        if issue:
            context['source_definition'] = {'supported': False, 'reason': issue[1],
                                            'instruction': '保留原文限定，无法用登记维度完整表达时提交未知字段，不得绑定为全公司数值'}
        return context

    def validated_binding(self, identity, role, p):
        c = self._claim(identity, p['mention_id'])
        b = dict(p['binding'])
        if b['company'] and self.task_company and b['company'] != self.task_company:
            raise ToolDenied('binding outside trusted task company')
        if b['company'] and c.company and b['company'] != c.company:
            raise ToolDenied('cross-company binding rejected')
        for field, allowed in (('scope', BIND_SCOPES), ('relation', BIND_RELATIONS), ('account', BIND_ACCOUNTS)):
            if b[field] not in allowed:
                raise FinancialArgumentError('invalid_binding_' + field)
        # Reject a contradicted *known* source field while this native role
        # can still observe the tool result and repair its own submission.
        # A missing/UNSPECIFIED source dimension is not permission to invent
        # a definition; it remains unknown at the existing freeze/kernel gate.
        # No fact query or opposite-role binding is read at this boundary.
        proof = self.source_dimensions(c)
        known = {}
        if (proof.get('company') and proof.get('company') == self.task_company
                and not proof.get('source_company_conflict') and not proof.get('source_company_ambiguous')):
            known['company'] = self.task_company
        account = proof.get('account')
        if account in BIND_ACCOUNTS and account not in {'', 'UNKNOWN'}:
            known['account'] = account
        for field in ('period', 'base_period'):
            value = proof.get(field)
            if isinstance(value, str) and re.fullmatch(PERIOD_PATTERN, value):
                known[field] = value
        relation = proof.get('relation')
        if relation in BIND_RELATIONS and relation:
            known['relation'] = relation
            # A source-addressed scalar has no comparison base. Do not let a
            # model add a second period that survives into A/B agreement.
            from .source_quarter_quotation import validated_threshold_reference
            if (relation == 'value' or validated_threshold_reference(c)) and proof.get('base_period') == '':
                known['base_period'] = ''
        if proof.get('scope_header') in BIND_SCOPES[1:] and not any(proof.get(flag) for flag in (
                'source_scope_conflict', 'source_scope_ambiguous', 'scope_declaration_conflict')):
            known['scope'] = proof['scope_header']
        canonical_account, _ = self._canonical_binding_account(c, b['account'])
        compared = dict(b, account=canonical_account)
        conflicts = tuple(field for field in BIND_FIELDS
                          if field in known and compared[field] not in {'', 'UNKNOWN'}
                          and compared[field] != known[field])
        if conflicts:
            # These closed-over fields are reconstructed from trusted,
            # amount-free source vocabulary. Never echo the rejected values,
            # raw PDF excerpts, private A/B history or exception attributes.
            safe_fields = tuple((field, value) for field, value in known.items()
                                if not any(secret and secret in value for secret in self.secret_values))

            class SourceBindingConflict(FinancialArgumentError):
                def __init__(self):
                    super().__init__('invalid_binding_arguments')

                def rejection(self):
                    return {'status': 'rejected', 'accepted': False,
                            'error': {'code': 'binding_source_conflict',
                                      'field': 'binding',
                                      'message': 'Nonempty binding fields contradict the frozen source dimensions. '
                                                 'Read mj_read_context for this mention, correct only your own '
                                                 'submission, and retry. Unknown fields may remain empty.',
                                      'conflicting_fields': list(conflicts),
                                      'source_dimensions': dict(safe_fields),
                                      'next_tool': 'mj_read_context'}}

            raise SourceBindingConflict()
        old = self.bindings[role].get(c.claim_id)
        if old and old != b:
            raise ToolDenied('binding already committed')
        return c.claim_id, b

    def register_session(self, session_id: str, role: str, mention_ids: list[str] | None = None) -> None:
        if role not in PERMISSIONS or session_id in self.sessions:
            raise ToolDenied("invalid or duplicate trusted session")
        self.sessions[session_id] = {"role": role, "mentions": set(self.claims if mention_ids is None else mention_ids),
                                     "active": True, "revealed_slots": set(), "turn_revealed_slots": set()}

    def dispatch(self, envelope: dict) -> dict | list:
        with self.lock:
            session_id = envelope.get('session_id')
            identity = self.sessions.get(session_id)
            if not identity or not identity['active']:
                raise ToolDenied("unknown or closed session")
            role = identity['role']
            if envelope.get('role') != role or envelope.get('run_id') != self.run_id or envelope.get('resource_lock') != self.resource_lock:
                raise ToolDenied("trusted identity or resource lock mismatch")
            name, params, call_id = envelope.get('tool_name'), envelope.get('params', {}), envelope.get('call_id')
            if name not in PERMISSIONS[role]:
                raise ToolDenied("role cannot call this tool")
            call_key = (session_id, call_id)
            if not isinstance(call_id, str) or not call_id or call_key in self.calls:
                raise ToolDenied("duplicate or missing call_id")
            if len(self.calls) >= self.budget.get('max_tool_calls', 1000):
                raise ToolDenied("tool budget exhausted")
            if time.monotonic() - self.started > self.budget.get('max_runtime_s', 1800):
                raise ToolDenied("runtime budget exhausted")
            self.calls.add(call_key)
            try:
                if self._contains_secret(params):
                    raise ToolDenied('credential material cannot enter financial tools')
                if role == 'question' and name == 'mj_submit_report':
                    attempts = identity.get('report_submission_attempts', 0)
                    if attempts >= 2:
                        raise ToolDenied('report structure repair exhausted; one correction only, stop this turn')
                    if identity.get('answer_submitted'):
                        raise ToolDenied('guarded answer already submitted; stop this turn')
                    identity['report_submission_attempts'] = attempts + 1
                _validate(params, tool_schema(name, self.batch_size, smart_profile=hasattr(self, 'smart_analysis')), name)
                output = self._execute(role, identity, name, params)
            except Exception as exc:
                rejection = exc.rejection() if isinstance(exc, (FinancialArgumentError, FinancialRequirementError)) else {"error": type(exc).__name__}
                self._log(session_id, role, call_id, name, params, rejection, "denied")
                raise
            self._log(session_id, role, call_id, name, params, output, "success")
            return self.model_verification_output(name, output)

    @staticmethod
    def model_verification_output(name, output):
        """Project smart verdicts for the model after recording the full result.

        The host trace, final judgements and report slots retain the original
        kernel result. The model needs one copy of the chosen operands and
        precision, plus all assumptions and blocking explanations; candidate
        catalogues and the nested copy of that same result are audit data.
        Legacy tool responses keep their existing shape and values.
        """
        if name not in {'mj_verify', 'mj_verify_bindings'}:
            return output

        def project(row):
            if not isinstance(row, dict) or not isinstance(row.get('smart_result'), dict):
                return row
            smart = row['smart_result']
            compact = {key: value for key, value in row.items() if key != 'smart_result'}
            # Keep every distinct diagnostic, assumption and blocker. Native
            # IDs/status/codes and display values already carry these aliases.
            for key, value in smart.items():
                if key not in compact and key not in {'cid', 'abstain_code', 'expected_display', 'candidates'}:
                    compact[key] = value
            operand_fields = {
                'fact_id', 'claim_id', 'company', 'account', 'period', 'scope', 'nr', 'value',
                'unit', 'value_unit', 'display_unit', 'decimals', 'quantum', 'interval',
                'currency', 'definition_id', 'dimension', 'dimension_kind', 'dimension_id',
                'relation', 'source_relation', 'source_doc', 'publish_date', 'version', 'evidence',
                'subject_evidence', 'page', 'source_table_id', 'source_table_evidence',
                'source_scope_evidence', 'source_row_index', 'operand_type', 'assertion_id',
                'operator', 'bound', 'eligible_as_point_fact',
            }
            # Preserve smart units verbatim. Native compact_operand infers
            # canonical units, which would relabel a disclosed smart YOY row.
            compact['operands'] = [
                {key: value for key, value in operand.items() if key in operand_fields and value is not None}
                for operand in row.get('operands') or []
            ]
            compact['source_precision'] = [
                {key: value for key, value in precision.items() if value is not None}
                for precision in row.get('source_precision') or []
            ]
            return deepcopy(compact)

        if name == 'mj_verify_bindings' and isinstance(output, dict):
            return {**output, 'items': [project(row) for row in output.get('items', [])]}
        return project(output)

    def _log(self, session_id, role, call_id, name, params, output, status):
        input_hash = digest(params)
        if status != 'success':
            # Every rejected payload is untrusted, including extra fields and
            # attempted role/value injections. A stable hash supports replay
            # comparison without persisting its raw arguments anywhere.
            params = {'input_sha256': input_hash}
        event = {"session_id": session_id, "role": role, "call_id": call_id, "tool": name,
                 "input": params, "input_hash": input_hash, "output": output, "status": status, "resource_lock": self.resource_lock}
        self.trace.append(event)
        if self.audit:
            self.audit.emit("tool_call", **event)
        self.emit("tool", session_id=session_id, role=role, call_id=call_id, tool=name, status=status)

    def _contains_secret(self, value):
        secrets = tuple(secret for secret in self.secret_values if isinstance(secret, str) and secret)
        if isinstance(value, str):
            return any(secret in value for secret in secrets)
        if isinstance(value, dict):
            return any(self._contains_secret(key) or self._contains_secret(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(self._contains_secret(item) for item in value)
        return False

    def _claim(self, identity: dict, mention_id: str) -> AtomicClaim:
        if mention_id not in identity['mentions'] or mention_id not in self.claims:
            raise ToolDenied("mention outside session resource scope")
        return self.claims[mention_id]

    def _source_payload(self, block: dict, *, query='') -> dict:
        """Host-addressed verbatim quotations never become financial facts."""
        import hashlib
        from .evidence import make_evidence_id
        from .presentation import escape_markdown
        resource_id = block.get('source_resource_id') or block.get('report_id') or 'draft'
        temporal_issue = self._source_temporal_issue(block)
        if temporal_issue:
            return self._missing_source_payload(query, temporal_issue)
        resource = self.source_resources.get(resource_id)
        if not resource:
            return {'block_id': block['id'], 'page': block.get('page'), 'report_id': resource_id,
                    'text': block['text'], 'slots': {}, 'slot_ids': [],
                    'reason': 'source_not_in_frozen_resource_registry',
                    'reference_status': '缺少冻结来源证明，不能提交为原文引用'}
        original = str(block['text'])
        anchor = self._source_query_anchor(original, query) if query else 0
        left = max(0, anchor - 100) if anchor >= 0 else 0
        right = left + len(original[left:].encode('utf-8')[:6400].decode('utf-8', errors='ignore'))
        # An excerpt must not turn a cut-off number into a different amount.
        from .mentions import VALUE_RE
        for number in VALUE_RE.finditer(original):
            if number.start() < left < number.end():
                left = number.start()
            if number.start() < right < number.end():
                right = number.start()
        quote = original[left:right]
        if not quote.strip() or len(quote.encode('utf-8')) > 8000:
            return self._missing_source_payload(query, '原文块超过可安全引用范围，请补充较短的定位材料或查看原页')
        sha = lambda value: hashlib.sha256(value.encode('utf-8')).hexdigest()
        slot = 'source.' + digest([resource['sha256'], block['id'], left, right, sha(quote)])[:24] + '.text'
        evidence_id = make_evidence_id(resource['sha256'], block.get('page'), quote, block.get('bbox'))
        proof = {'slot_id': slot, 'report_id': resource_id, 'doc_sha256': resource['sha256'],
                 'page': block.get('page'), 'bbox': block.get('bbox'), 'block_id': block['id'],
                 'block_sha256': sha(original), 'quote_sha256': sha(quote), 'quote_text': quote,
                 'quote_start': left, 'quote_end': right, 'evidence_id': evidence_id,
                 'company': self.task_company, 'name': resource['name'], 'title': resource['name'],
                 'publish_date': resource.get('publish_date'), 'as_of_date': self.report_date,
                 'reference_status': 'unverified_source_quotation', 'reference_label': SOURCE_QUOTATION_STATUS,
                 'status': 'unverified_source_quotation'}
        citation = (f"## 原文\n{SOURCE_QUOTATION_STATUS}。引用中的日期、金额和结论仅为披露原文，"
                    "不得视为明镜的事实值或 verified 判定。\n\n> " + escape_markdown(quote) +
                    '\n\n来源：' + escape_markdown(resource['name']) +
                    '；页码：' + (str(block['page']) if block.get('page') else '未定位') +
                    '；证据 ID：' + escape_markdown(evidence_id))
        self.slots[slot] = citation
        self.source_references[slot] = proof
        # Keep complete byte-addressed proofs and rendered citations in the
        # host. Sending the same quote in proof, slots and text wasted context.
        model_reference = {key: proof[key] for key in
                           ('slot_id', 'report_id', 'block_id', 'page', 'evidence_id', 'reference_status')}
        return {'block_id': block['id'], 'page': block.get('page'), 'report_id': resource_id, 'text': quote,
                'publish_date': resource.get('publish_date'), 'as_of_date': self.report_date,
                'excerpted': left != 0 or right != len(original), 'source_references': [model_reference],
                'content_kind': self._source_block_kind(block),
                'reference_status': 'unverified_source_quotation', 'reference_label': SOURCE_QUOTATION_STATUS,
                'slot_ids': [slot],
                'slot_template': '# 原文\n{{' + slot + '}}', 'slot_instruction': SLOT_INSTRUCTION}

    def _source_temporal_issue(self, block: dict) -> str | None:
        resource_id = block.get('source_resource_id') or block.get('report_id') or 'draft'
        if resource_id == 'draft':
            return None
        resource = self.source_resources.get(resource_id) or {}
        from datetime import date
        try:
            cutoff = date.fromisoformat(self.report_date)
        except (ValueError, TypeError):
            return '缺少有效回溯日期，不能把财报原文作为截至日期已知依据'
        try:
            disclosed = date.fromisoformat(resource.get('publish_date', ''))
        except (ValueError, TypeError):
            return '来源披露日期未知，需要补充公开日期证明'
        if disclosed > cutoff:
            return '来源在回溯日期之后披露，不能作为截至日期已知依据'
        return None

    def _missing_source_payload(self, query: str, reason='未在本次锁定材料检索中找到可引用的原文') -> dict:
        from .presentation import escape_markdown
        slot = 'source.missing.' + digest([self.resource_lock, query, reason])[:24] + '.text'
        text = ('## 待补材料\n' + reason + '。\n检索词：' + escape_markdown(query) +
                '\n该结果只说明本次检索未取得可引用片段，不能证明公司不存在该事项。'
                '需要补充锁定报告中的原文、文档来源和页级位置；不能使用其他财务指标替代回答。')
        self.slots[slot] = text
        self.source_references[slot] = {'slot_id': slot, 'status': 'source_material_required',
                                       'resource_lock': self.resource_lock, 'query_hash': digest(query)}
        return {'found': False, 'reason': 'source_material_required', 'source_references': [],
                'slots': {slot: text}, 'slot_ids': [slot], 'slot_template': '# 待补材料\n{{' + slot + '}}',
                'slot_instruction': SLOT_INSTRUCTION}

    def _analysis_missing_payload(self, p, category) -> dict:
        from .presentation import escape_markdown
        label = ANALYSIS_KINDS[p['kind']][1] if p.get('kind') else (
            (SMART_ANALYSIS_METRICS[p['metric']]['label'] if hasattr(self, 'smart_analysis') and p['metric'] in SMART_ANALYSIS_METRICS
             else ANALYSIS_METRICS[p['metric']][0]) if p.get('metric') else
            {'metrics': '财报指标', 'signals': '复核信号', 'events': '披露事件'}.get(category, '财报分析'))
        proof = {'status': 'analysis_record_not_found', 'category': category, 'kind': p.get('kind'),
                 'metric': p.get('metric'), 'requested_period': p.get('period'),
                 'company': self.task_company, 'resource_lock': self.resource_lock,
                 'provenance': 'host_query_over_frozen_analysis', 'verified': False}
        slot = 'diagnostic.' + digest(proof)[:24] + '.text'
        text = ('## 提示\n当前冻结分析未检出所请求的登记记录：' + escape_markdown(label) + '。'
                '这是登记结果查询诊断，不是数字核查判定，也不是披露原文。'
                '不能据此断言公司或原文不存在该事项。请检索锁定原文，或补充可定位的材料后重新分析；'
                '不能以其他财务指标替代本问题。')
        self.slots[slot] = text
        self.diagnostic_slots[slot] = proof
        return {'slots': {slot: text}, 'slot_ids': [slot], 'slot_template': '# 提示\n{{' + slot + '}}',
                'diagnostics': [{'slot_id': slot, **proof}]}

    @staticmethod
    def _reveal_slots(identity, payload):
        identity['revealed_slots'].update(payload.get('slot_ids', []))
        identity['turn_revealed_slots'].update(payload.get('slot_ids', []))

    @staticmethod
    def _source_search_text(text: str) -> str:
        # Text layers split words between lines or insert spaces. Matching
        # alone is normalized; quotations, offsets and hashes stay verbatim.
        return re.sub(r'\s+', '', text.casefold())

    @classmethod
    def _source_query_anchor(cls, text: str, query: str) -> int:
        compact, positions = [], []
        for index, char in enumerate(text):
            if not char.isspace():
                folded = char.casefold()
                compact.append(folded)
                positions.extend([index] * len(folded))
        found = ''.join(compact).find(cls._source_search_text(query))
        return positions[found] if found >= 0 and positions else -1

    @classmethod
    def _source_block_kind(cls, block: dict) -> str:
        text = block['text'].strip()
        compact = cls._source_search_text(text)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not compact or re.fullmatch(r'\d{1,4}', compact):
            return 'page_or_blank'
        if re.search(r'[.．…·]{4,}\s*\d+\s*$', text):
            return 'table_of_contents'
        if re.fullmatch(r'[√□适用不\s]+', text):
            return 'applicability_label'
        numeric_lines = sum(bool(re.fullmatch(r'[\d\s.,，%％()+＋\-－—/年月日]+', line)) for line in lines)
        if len(lines) > 1 and numeric_lines >= max(1, len(lines) // 2):
            return 'table_values'
        # Generic column labels, independent of any report or query.
        headers = ('内容和原因', '报表项目', '影响金额', '期初余额', '期末余额', '本期金额',
                   '上期金额', '合并', '母公司', '调整前', '调整后', '项目名称')
        if sum(label in compact for label in headers) >= 2 and not re.search(r'[。；！？]', text):
            return 'table_header'
        prose = bool(re.search(r'[。，；！？]|本公司|财政部|执行|规定|确认|应当|对于|发生|实施|公布|施行', text))
        if len(compact) <= 32 and not prose:
            return 'section_heading'
        return 'body'

    def _source_following_blocks(self, block: dict, count: int | None = None) -> list[dict]:
        resource_id = block.get('source_resource_id') or block.get('report_id') or 'draft'
        neighbors = sorted([item for item in self.blocks.values() if item.get('page') == block.get('page')
            and (str(item.get('text', '')).strip() or item['id'] == block['id'])
            and (item.get('source_resource_id') or item.get('report_id') or 'draft') == resource_id],
            key=lambda item: (item.get('start') is None, item.get('start') or 0, item['id']))
        position = next(index for index, item in enumerate(neighbors) if item['id'] == block['id'])
        if count is not None:
            return [*neighbors[max(0, position - count):position],
                    *neighbors[position + 1:position + 1 + count]]
        # Native PDFs can expose one line as one block. Follow headings far
        # enough to include sentences, rather than just the next column label.
        if self._source_block_kind(block) != 'section_heading':
            return []
        selected, seen_body, sentences = [], False, 0
        for item in neighbors[position + 1:position + 21]:
            kind = self._source_block_kind(item)
            if kind in {'page_or_blank', 'applicability_label'}:
                continue
            if kind in {'table_header', 'table_values', 'table_of_contents'}:
                break
            if seen_body and kind == 'section_heading':
                break
            if not seen_body and kind == 'section_heading':
                continue
            if kind == 'body':
                seen_body = True
            selected.append(item)
            sentences += len(re.findall(r'[。！？]', item['text']))
            if len(selected) >= 16 or (len(selected) >= 3 and sentences >= 3):
                break
        return selected

    def _source_context_payload(self, block: dict, *, query='', context_blocks=None) -> dict:
        import json
        output = self._source_payload(block, query=query)
        if self._source_temporal_issue(block):
            return output
        related, omissions = [], []
        for item in self._source_following_blocks(block, context_blocks):
            payload = self._source_payload(item)
            if isinstance(payload.get('text'), str) and payload['text'].strip():
                related.append(payload)
            else:
                omissions.append({'block_id': item['id'], 'reason': payload.get('reason') or 'no_quotable_context'})
        # Omit entire trailing quotations to bound a response; never shorten
        # a number or claim inside an individual preview to fit the budget.
        truncated = False
        while related and len(json.dumps([output, *related], ensure_ascii=False).encode('utf-8')) > 32768:
            related.pop()
            truncated = True
        references = list(output.get('source_references', []))
        slots = list(output.get('slot_ids', []))
        contexts = []
        for item in related:
            references.extend(item.get('source_references', []))
            slots.extend(item.get('slot_ids', []))
            contexts.append({key: item.get(key) for key in ('block_id', 'page', 'text', 'content_kind', 'slot_ids')})
        output.update(source_references=references, slot_ids=slots, related_contexts=contexts,
                      context_omissions=omissions,
                      requested_context_blocks=context_blocks, context_truncated=truncated,
                      context_scope='same_document_and_page',
                      includes_substantive_context=any(item.get('content_kind') == 'body' for item in [output, *related]))
        if slots:
            output['slot_template'] = '# 原文\n' + '\n\n'.join('{{' + slot + '}}' for slot in slots)
        if output.get('content_kind') in {'table_header', 'table_values', 'section_heading'} and not output['includes_substantive_context']:
            output['context_instruction'] = ('当前片段仅为标题、表头或金额列，不足以回答内容和原因；'
                                             '请读取同页正文或改用更具体的关键词检索，不能把栏目名称当作披露说明。')
        return output

    def _source_search_rank(self, block: dict, query: str):
        report = next((item for item in self.reports if item.get('report_id') == block.get('report_id')), {})
        periods = report.get('periods', [])
        target = not self.task_period or self.task_period in periods
        kind = self._source_block_kind(block)
        contextual_body = kind == 'section_heading' and any(
            self._source_block_kind(item) == 'body' for item in self._source_following_blocks(block))
        quality = (0 if contextual_body else 1 if kind == 'body' else
                   2 if kind == 'section_heading' else 3 if kind == 'table_header' else
                   4 if kind == 'table_values' else 5)
        return not target, quality, block.get('page') or 0, block.get('start') or 0

    def _execute(self, role, identity, name, p):
        blind = role in {'planner', 'verifier'}
        if name in {'mj_kb_list', 'mj_kb_search', 'mj_kb_read'}:
            registry = self.knowledge_registry
            if registry is None:
                output = self._knowledge_payload({'available': False, 'reason': 'knowledge_root_not_configured'})
                if role == 'question':
                    self._reveal_slots(identity, output)
                return output
            if name == 'mj_kb_list':
                from .knowledge import _pagination, knowledge_summary, blind_knowledge_payload
                cursor, limit = p.get('cursor', 0), p.get('limit', 20)
                _pagination(cursor, limit, 20)
                registry.validate_locked()
                documents = [registry.public_document(row, self.report_date) for row in registry.documents.values()]
                output = {'documents': documents[cursor:cursor+limit], 'total_documents': len(documents),
                        'next_cursor': cursor+limit if cursor+limit < len(documents) else None,
                        'knowledge': knowledge_summary(registry, self.report_date)}
                return blind_knowledge_payload(output) if blind else output
            if name == 'mj_kb_read':
                raw = registry.read(p['block_id'], as_of_date=self.report_date)
                output = self._knowledge_payload(raw, blind=blind, quote=role == 'question')
                if role == 'question':
                    self._reveal_slots(identity, output)
                return output
            search_key = (role, 'knowledge:'+self._source_search_text(p['query'])+':'+str(p.get('document_id') or '')+':'+str(p.get('cursor', 0))+':'+p.get('intent','search'))
            if search_key in self.searches:
                return {'stopped': True, 'reason': 'no_new_information'}
            self.searches.add(search_key)
            found = registry.search(p['query'], as_of_date=self.report_date,
                    document_ids=[p['document_id']] if p.get('document_id') else None,
                    cursor=p.get('cursor', 0), limit=min(p.get('limit', 3), 5), intent=p.get('intent','search'))
            if role == 'question' and found['definition_request']:
                identity.setdefault('definition_terms', set()).update(found['definition_terms'])
            if not found['hits']:
                output = self._knowledge_payload({'available': False, 'reason': found['no_match_reason'] or 'no_quotable_knowledge_match'})
                output.update(unavailable_documents=found['unavailable_documents'], total_matches=0)
            else:
                hits = [self._knowledge_payload(raw, blind=blind, quote=role == 'question') for raw in found['hits']]
                output = {'hits': hits, 'total_matches': found['total_matches'], 'next_cursor': found['next_cursor'],
                          'slot_ids': [slot for hit in hits for slot in hit.get('slot_ids', [])],
                          'source_references': [reference for hit in hits for reference in hit.get('source_references', [])],
                          'slot_template': '# 原文\n'+'\n'.join('{{'+slot+'}}' for hit in hits for slot in hit.get('slot_ids', []))}
            output.update(definition_request=found['definition_request'], definition_terms=found['definition_terms'])
            if role == 'question':
                self._reveal_slots(identity, output)
            return output
        if name == 'mj_list_reports':
            identity['reports_read'] = True
            return self.reports
        if name=='mj_list_source_gaps':
            visible=[row for row in self.source_gaps if row.get('public_at_cutoff') is True and not self._source_temporal_issue(row)]
            if p.get('report_id'):
                if p['report_id'] not in self.source_resources or p['report_id']=='draft':
                    raise ToolDenied('source gaps report outside frozen resource scope')
                visible=[row for row in visible if row['report_id']==p['report_id']]
            if p.get('query'):
                terms=[self._source_search_text(x) for x in p['query'].split() if x.strip()]
                visible=[row for row in visible if all(term in self._source_search_text(' '.join(str(row.get(k,'')) for k in
                    ('company','account','period','dimension_id','reason','table_label','gap_kind'))) for term in terms)]
            def gaps(items):
                output=[]
                for item in items:
                    row=dict(item)
                    output.append(row)
                return {'source_gaps':output}
            return self.bounded_page(visible,p,gaps,{'eligible_as_fact':False,
                    'reference_status':'unresolved_source_metadata','as_of_date':self.report_date,
                    'unavailable_at_cutoff':len(self.source_gaps)-sum(row.get('public_at_cutoff') is True and not self._source_temporal_issue(row) for row in self.source_gaps),
                    'notice':'只列来源条件缺口；没有金额，不是事实，不代表原文错误或公司不存在相关项目。'})
        if name == 'mj_list_mentions':
            claims = [c for c in self.claims.values() if c.claim_id in identity['mentions']]
            cursor, limit = p.get('cursor', 0), p.get('limit', 30)
            return {"mentions": [self.mention_context(c, compact=True) for c in claims[cursor:cursor+limit]],
                    "binding_fields": list(BIND_FIELDS),
                    "scope_policy": '仅用源文明确口径；未知填空，不从事实库默认口径',
                    "next_cursor": cursor + limit if cursor + limit < len(claims) else None}
        if name == 'mj_read_context':
            mid = p['mention_id']
            if mid in self.blocks:
                block = self.blocks[mid]
                if self._source_temporal_issue(block):
                    output = self._missing_source_payload('', self._source_temporal_issue(block))
                    if role == 'question':
                        self._reveal_slots(identity, output)
                    return output
                if not blind:
                    output = self._source_context_payload(block, context_blocks=p.get('context_blocks'))
                    if role == 'question':
                        self._reveal_slots(identity, output)
                    return output
                return {"block_id": mid, "page": block.get('page'),
                        "text": mask_financial_numbers(block['text'], preserve_bare_years=False),
                        'related_contexts': [{'block_id': item['id'], 'page': item.get('page'),
                            'text': mask_financial_numbers(item['text'], preserve_bare_years=False)}
                            for item in self._source_following_blocks(block, p.get('context_blocks', 0))],
                        'context_scope': 'same_document_and_page',
                        'requested_context_blocks': p.get('context_blocks', 0)}
            c = self._claim(identity, mid)
            context = self.mention_context(c, blind)
            if not blind or p.get('context_blocks'):
                block = next((item for item in self.blocks.values() if
                    (item.get('source_resource_id') or item.get('report_id') or 'draft') == 'draft'
                    and item.get('start') is not None and item['start'] <= c.start < item['end']), None)
                if block and blind:
                    context['related_contexts'] = [{'block_id': item['id'], 'page': item.get('page'),
                        'text': mask_financial_numbers(item['text'], preserve_bare_years=False)}
                        for item in self._source_following_blocks(block, p.get('context_blocks', 0))]
                elif role == 'question' or p.get('context_blocks') is not None:
                    payload = self._source_context_payload(block, context_blocks=p.get('context_blocks')) if block else self._missing_source_payload(c.account, '原文提及缺少可反查的冻结文本块')
                    context.update(payload)
                    if role == 'question':
                        self._reveal_slots(identity, context)
            return context
        if name == 'mj_search_blocks':
            terms = sorted(set(self._source_search_text(part) for part in p['query'].split() if part.strip()))
            if not terms:
                raise ToolDenied('source search requires a non-whitespace query')
            if len(terms) > 16:
                raise ToolDenied('source search accepts at most sixteen AND terms')
            report_id = p.get('report_id')
            if report_id and report_id not in self.source_resources:
                raise ToolDenied('source search report outside frozen resource scope')
            session_id = next(sid for sid, value in self.sessions.items() if value is identity)
            cursor, limit = p.get('cursor', 0), min(p.get('limit', 3 if role == 'question' else 10), 5 if role == 'question' else 100)
            search_key = (session_id, 'source.request:' + digest([terms, report_id, cursor, limit, p.get('context_blocks')]))
            if search_key in self.searches:
                return [{'stopped': True, 'reason': 'no_new_information',
                         'instruction': '不要重复检索；使用已取得槽位提交，缺证则保留工具给出的弃权说明。'}]
            self.searches.add(search_key)
            hits = [b for b in self.blocks.values() if not self._source_temporal_issue(b) and all(term in self._source_search_text(b['text']) for term in terms) and (not report_id or
                (b.get('source_resource_id') or b.get('report_id') or 'draft') == report_id)]
            if not blind:
                hits.sort(key=lambda block: (*self._source_search_rank(block, p['query']), block['id']))
            if not hits or cursor >= len(hits):
                output = self._missing_source_payload(p['query'],
                    '本次同块 AND 检索未返回引用；可调整词语或读取标题上下文，不能据此判断锁定材料缺失') if role == 'question' else {'found': False,
                    'reason': 'source_search_no_match' if not hits else 'source_search_cursor_exhausted'}
                output.update(total_matches=len(hits), cursor=cursor, next_cursor=None, query_terms=terms,
                    match_policy='AND_same_original_block',
                    search_status='no_match' if not hits else 'cursor_exhausted', material_availability='not_inferred',
                    instruction='本次同块检索未返回原文，不证明材料不存在；分别检索一个标题或科目，并读取命中块前后上下文以查看单位和期间列头。')
                if role == 'question':
                    self._reveal_slots(identity, output)
                return [output]
            if not blind:
                outputs, included, following = [], set(), cursor
                for offset, block in enumerate(hits[cursor:], cursor):
                    following = offset + 1
                    if block['id'] in included:
                        continue
                    context_blocks = p.get('context_blocks', 2 if role == 'coordinator' else None)
                    output = self._source_context_payload(block, query=terms[0], context_blocks=context_blocks)
                    outputs.append(output)
                    included.update([block['id'], *[item['block_id'] for item in output.get('related_contexts', [])]])
                    if len(outputs) >= limit:
                        break
                import json
                while len(outputs) > 1 and len(json.dumps(outputs, ensure_ascii=False).encode('utf-8')) > 32768:
                    dropped = outputs.pop()
                    following = min(following, next(index for index, block in enumerate(hits) if block['id'] == dropped['block_id']))
                for output in outputs:
                    output.update(total_matches=len(hits), cursor=cursor, next_cursor=following if following < len(hits) else None,
                                  query_terms=terms, match_policy='AND_same_original_block')
                    if role == 'question':
                        self._reveal_slots(identity, output)
            else:
                following = min(cursor + limit, len(hits))
                outputs = [{"block_id": block['id'], "page": block.get('page'), "report_id": block.get('report_id'),
                    "text": mask_financial_numbers(block['text'], preserve_bare_years=False),
                    'total_matches': len(hits), 'cursor': cursor, 'next_cursor': following if following < len(hits) else None,
                    'query_terms': terms, 'match_policy': 'AND_same_original_block'} for block in hits[cursor:following]]
            novel = []
            for output in outputs:
                result_key = (session_id, 'source.result:' + digest([output.get('block_id'), output.get('text'), output.get('related_contexts')]))
                if result_key not in self.searches:
                    self.searches.add(result_key)
                    novel.append(output)
            if not novel:
                return [{'stopped': True, 'reason': 'no_new_information', 'total_matches': len(hits),
                         'next_cursor': following if following < len(hits) else None,
                         'instruction': '本次仅取得本会话已读取的相同原文；继续有效分页或使用既有结果，不重复同一检索。'}]
            return novel
        if name == 'mj_fact_dimensions':
            c = self._claim(identity, p['mention_id'])
            labels = self.source_labels(c)
            source_account = c.table_context.get('account_hint')
            relevant = set(labels.values()) | ({source_account} if source_account else set())
            return {"company_hint": c.company, "accounts": sorted(relevant),
                    "periods": sorted({f.period for f in self.store.facts}),
                    "companies": sorted({f.company for f in self.store.facts}),
                    "scopes_available": sorted({f.scope for f in self.store.facts}),
                    "scope_policy": '可用事实口径不代表原文口径；原文不明确必须填空',
                    "source_dimensions": _blind_source_metadata(self.source_dimensions(c)),
                    "metric_definitions": [x for x in metric_definitions() if x['account'] in relevant],
                    "relations": [relation for relation in BIND_RELATIONS if relation],
                    "fields": list(BIND_FIELDS),
                    "account_labels": labels}
        if name == 'mj_get_rule':
            if p['rule_id'] == 'SMART-registered-kernel' and hasattr(self, 'trusted_verifier'):
                from .source_cache import extractor_source_modules
                catalog = unified_definition_catalog('smart')
                selected = [self.claims[mid] for mid in identity['mentions'] if mid in self.claims]
                accounts = {c.account for c in selected}
                if any(c.relation != 'value' for c in selected):
                    accounts.add('PERIOD_CHANGE')
                if not selected and hasattr(self, 'smart_analysis'):
                    from .smart.analysis import AMOUNTS, RATIOS, EXTRA
                    accounts.update([*AMOUNTS, *RATIOS, *EXTRA, 'PERIOD_CHANGE'])
                identity.setdefault('rules_read', set()).add('SMART-registered-kernel')
                return {'rule_id': 'SMART-registered-kernel', 'entrypoint': 'mingjing.smart.verify.verify_model',
                        'validation_policy': self.validation_policy,
                        'definitions': [row for row in catalog if row['account'] in accounts],
                        'catalog_hash': digest(catalog), 'catalog_entries': len(catalog),
                        'catalog_scope': 'authorized_session_accounts', 'professional_review_status': 'pending',
                        'extractor_lock': extractor_source_modules(), 'source': 'host_registered_deterministic_kernel',
                        'policy': '值仅取宿主冻结原文，绑定仅六字段；文本佐证不提升为数值核实'}
            if p['rule_id'].startswith('SMART:'):
                from .rules import registered_rule_description
                rule = registered_rule_description(p['rule_id'])
                if rule:
                    identity.setdefault('rules_read', set()).add(p['rule_id'])
                    return rule
            import json
            cards = json.loads((rulepack_root() / 'cards.json').read_text(encoding='utf-8'))
            for card in cards:
                if p['rule_id'] in {card.get('rule_id'), card.get('id'), card.get('error_type')}:
                    identity.setdefault('rules_read', set()).add(card.get('rule_id') or card.get('id') or p['rule_id'])
                    return card
            return {"found": False}
        if name == 'mj_submit_binding':
            mid, binding = self.validated_binding(identity, role, p)
            self.bindings[role][mid] = binding
            return {"accepted": True, "binding_id": f"{role}:{mid}"}
        if name == 'mj_submit_bindings':
            validated = [self.validated_binding(identity, role, item) for item in p['bindings']]
            if len({mid for mid, _ in validated}) != len(validated):
                raise ToolDenied('duplicate mention in binding batch')
            self.bindings[role].update(validated)
            return {"accepted": True, "binding_ids": [mid for mid, _ in validated]}
        if name == 'mj_request_reparse':
            block_id = p['block_id']
            if block_id not in self.blocks:
                raise ToolDenied("unknown block")
            mid = p['binding_id']
            claim = self._claim(identity, mid)
            if not self.agreements.get(mid, {}).get('agreed'):
                raise ToolDenied('fact reparse requires a consistent frozen binding')
            if mid not in identity.get('facts_missing', set()):
                raise ToolDenied('read a missing fact in this session before requesting reparse')
            block = self.blocks[block_id]
            resource_id = block.get('source_resource_id') or block.get('report_id')
            if not resource_id or resource_id == 'draft' or resource_id not in {row['id'] for row in self.reparse_resources}:
                raise ToolDenied('fact reparse only accepts a registered source PDF report')
            if not self.reparse_root:
                return {'status': 'unavailable', 'reason': 'controlled_reparse_output_root_not_configured',
                        'requires_new_task_version': True, 'applied_to_current_task': False}
            attempts = self.reparse_attempts.get(block_id, 0)
            claim_attempts = self.reparse_claim_attempts.get(mid, 0)
            if attempts >= 2 or claim_attempts >= 2:
                return {"status": "stopped", "reason": "retry_budget_exhausted",
                        'attempt':claim_attempts, 'remaining_claim_attempts':0,
                        'requires_new_task_version':True, 'applied_to_current_task':False}
            if any(row['tool']=='mj_request_reparse' and row['status']=='success'
                   and row['input'].get('block_id')==block_id and row['input'].get('binding_id')==mid
                   and row['output'].get('status') in {'ready','staged','no_new_information','conflicts'}
                   for row in self.trace):
                return {'status':'stopped', 'reason':'no_new_information', 'attempt':claim_attempts,
                        'remaining_claim_attempts':max(0,2-claim_attempts),
                        'requires_new_task_version':True, 'applied_to_current_task':False}
            self.reparse_attempts[block_id] = attempts + 1
            self.reparse_claim_attempts[mid] = claim_attempts + 1
            from .reparse import create_reparse_packet
            output = create_reparse_packet(output_root=self.reparse_root, block=block, claim=claim,
                source_resources=self.reparse_resources, baseline_facts=self.store.facts,
                company=self.task_company, as_of_date=self.report_date,
                source_generation=self.reparse_source_generation, cancel_event=self.reparse_cancel_event,
                deadline_monotonic=self.started + self.budget.get('max_runtime_s', 1800),
                staged=self.reparse_source_generation is None)
            return {**output, 'attempt': attempts + 1, 'claim_attempt':claim_attempts + 1,
                    'remaining_claim_attempts':1-claim_attempts}
        if name in {'mj_get_facts', 'mj_verify_bindings'}:
            ids = p['binding_ids']
            for mid in ids:
                self._claim(identity, mid)
                if not self.agreements.get(mid, {}).get('agreed'):
                    raise ToolDenied('no consistent binding')
            if name == 'mj_verify_bindings' and (not set(ids).issubset(identity.get('facts_read', set()))
                                                 or not identity.get('rules_read')):
                required = {rule for mid in ids for rule in self._required_verification_rules(self.claims[mid])}
                raise FinancialRequirementError(required or ['E1-value'],
                    facts_required=not set(ids).issubset(identity.get('facts_read', set())))
            if name == 'mj_verify_bindings':
                # Validate every applicable formula before executing any item. An
                # unrelated E1 card does not authorize a derived formula;
                # one missing rule cannot produce partially executed batches.
                for mid in ids:
                    self._require_verification_reads(identity, self.claims[mid])
            singular = 'mj_get_fact' if name == 'mj_get_facts' else 'mj_verify'
            return {'items': [{'binding_id': mid, **self._execute(role, identity, singular, {'binding_id': mid})}
                              for mid in ids]}
        if name in {'mj_get_fact', 'mj_verify'}:
            binding_id = p['binding_id']
            self._claim(identity, binding_id)
            agreement = self.agreements.get(binding_id)
            if not agreement or not agreement.get('agreed'):
                raise ToolDenied("no consistent binding")
            claim = self.claims[binding_id]
            if name == 'mj_get_fact':
                identity.setdefault('facts_read', set()).add(binding_id)
                query_dimensions = claim_dimensions(claim)
                if claim.table_context.get('smart_cid') and hasattr(self, 'trusted_verifier'):
                    from .dimensions import validate_fact_dimensions
                    query_dimensions = {**query_dimensions, **claim.table_context.get('smart_dimensions', {})}
                    validate_fact_dimensions(query_dimensions['dimension_kind'], query_dimensions['dimension_id'],
                                             query_dimensions['currency'], query_dimensions['definition_id'])
                metadata = {'required_rule_ids': self._required_verification_rules(claim),
                            'query_dimensions': {'company': claim.company, 'account': claim.account,
                                'period': claim.period, 'scope': claim.caliber_hint[0] if claim.caliber_hint else '',
                                'relation': claim.relation, 'base_period': claim.base_period or '', **query_dimensions},
                            'metric_definitions':[x for x in unified_definition_catalog('smart' if claim.table_context.get('smart_cid') else 'canonical') if x['account']==claim.account]}
                if claim.table_context.get('smart_cid'):
                    metadata.pop('metric_definitions', None)
                    metadata['definition_reference'] = {'tool': 'mj_get_rule', 'rule_id': 'SMART-registered-kernel', 'account': claim.account}
                from .judge import _source_scope_issue
                issue = (None if claim.table_context.get('smart_cid') and hasattr(self, 'trusted_verifier') else
                         (_source_semantic_issue(claim) or source_binding_issue(claim, strict=True)
                          or _source_scope_issue(claim, strict=True)))
                if issue:
                    identity.setdefault('facts_missing', set()).discard(binding_id)
                    return {"fact": None, "source_definition_unavailable": issue[1], **metadata}
                from .source_quarter_quotation import threshold_operands
                threshold = threshold_operands(claim)
                if threshold:
                    proof=claim.table_context['source_threshold_reference']
                    if proof['cutoff']!=self.report_date or not self.report_date:
                        raise ToolDenied('threshold assertion belongs to a different cutoff snapshot')
                    identity.setdefault('facts_missing', set()).discard(binding_id)
                    return {'fact': None,'source_assertion':threshold[2],
                            'query_outcome':{'status':'source_assertion_found',
                                             'eligible_as_point_fact':False}, **metadata}
                scope = claim.caliber_hint[0] if claim.caliber_hint else None
                if scope == '来源表':
                    from .source_table_reference import query_quoted_table
                    outcome = query_quoted_table(self.store, claim, self.report_date)
                else:
                    outcome = self.store.query_outcome(claim.company, claim.account, claim.period, scope=scope or '合并',
                                        nr='扣非' if claim.account == 'NP_PARENT_ADJ' else '未扣非', cutoff=self.report_date,
                                        strategy='pinned' if claim.declared_source else claim.query_strategy,
                                        source_doc=claim.declared_source, **query_dimensions)
                missing = identity.setdefault('facts_missing', set())
                # A raw lookup failure may still need a future-task parse
                # proposal. It never authorizes a current formula or clears X4.
                missing.add(binding_id) if outcome.fact is None else missing.discard(binding_id)
                if claim.table_context.get('smart_cid'):
                    outcome_meta = {key: value for key, value in outcome.to_dict().items()
                                    if key in {'status', 'reason', 'cutoff', 'strategy', 'blockers', 'source_doc', 'source_selection'}}
                    return {'fact': self.compact_operand(outcome.fact.to_dict()) if outcome.fact else None,
                            'query_outcome': outcome_meta, **metadata}
                return {"fact": outcome.fact.to_dict() if outcome.fact else None, "query_outcome": outcome.to_dict(), **metadata}
            self._require_verification_reads(identity, claim)
            if hasattr(self, 'trusted_verifier'):
                result = self.trusted_verifier(claim)
                if result is not None:
                    return result
            return Judge(self.store, self.report_date).verify(claim).to_dict()
        if name == 'mj_get_verdicts':
            rows = [r for r in self.results if r['claim_id'] in identity['mentions']]
            if p.get('claim_id'):
                self._claim(identity, p['claim_id'])
                rows = [r for r in rows if r['claim_id'] == p['claim_id']]
            if p.get('query'):
                query = p['query'].casefold()
                rows = [r for r in rows if query in (self.claims[r['claim_id']].text + ' ' + self.claims[r['claim_id']].account + ' ' + self.claims[r['claim_id']].period).casefold()]
            def render(items):
                verdicts = [{key: row.get(key) for key in ('claim_id', 'status', 'error_type', 'abstention_code')} for row in items]
                prefixes = {r['claim_id'] for r in items}
                slots = {key: value for key, value in self.slots.items() if key.split('.')[0] in prefixes}
                template = '# 明镜核查报告\n' + '\n\n'.join('{{'+slot+'}}' for slot in slots)
                return dict(verdicts=verdicts, slots=slots, slot_template=template, slot_ids=list(slots))
            metadata = {'slot_instruction': SLOT_INSTRUCTION}
            if not rows:
                metadata.update(reason='no_matching_frozen_verdicts' if self.results else 'no_draft_verdicts',
                    notice='没有匹配的研报草稿判定；这不代表财报事实不存在。不要重复请求相同筛选。',
                    next_action={'tool': 'mj_analyze', 'arguments': {'analysis_id': 'financial_summary', 'catalog_only': True}},
                    registered_metrics=analysis_metric_catalog())
            output = self.bounded_page(rows, p, render, metadata)
            identity['revealed_slots'].update(output['slot_ids'])
            identity['turn_revealed_slots'].update(output['slot_ids'])
            return output
        if name == 'mj_analyze':
            from .analysis import analyze_financials
            metric_catalog = smart_analysis_metric_catalog if hasattr(self, 'smart_analysis') else analysis_metric_catalog
            if not self.task_company:
                raise ToolDenied('financial analysis requires one trusted task company; select the issuer in a new task')
            requested_category = p.get('category') or (ANALYSIS_KINDS[p['kind']][0] if p.get('kind') else None)
            if p.get('kind') and requested_category != ANALYSIS_KINDS[p['kind']][0]:
                raise ToolDenied('analysis kind does not belong to requested category')
            if p.get('metric') and (requested_category not in {None, 'metrics'} or p.get('kind')):
                raise ToolDenied('metric filter cannot be combined with signal/event kind')
            if p.get('catalog_only'):
                periods = {period for item in self.analysis_snapshots.values() for period in item.get('periods', [])}
                if role != 'reporter':
                    periods.update(f.period for f in self.store.facts)
                return self.bounded_page([], p, lambda _: dict(analysis=[], slots={}, slot_template='', slot_ids=[]),
                    dict(catalog_only=True, company=self.task_company, scope=self.analysis_scope,
                         report_date=self.report_date, registered_metrics=metric_catalog(p.get('metric'))
                             if requested_category in {None, 'metrics'} else [],
                         registered_kinds=analysis_kind_catalog(requested_category, p.get('kind')),
                         selection=self.analysis_selection(periods, p), slot_instruction=SLOT_INSTRUCTION,
                         next_action={'tool': 'mj_analyze', 'arguments': {'analysis_id': 'financial_summary'},
                                      'instruction': '从目录选择问题对应的metric并设置本期period；同比槽位自带本期、基期、公式和证据。'}))
            if role == 'coordinator' and not identity.get('frozen_analysis_reader') and (not identity.get('reports_read') or not any(
                str(rule).startswith(('FIN-', 'NON_RECURRING_', 'SMART-registered-kernel')) for rule in identity.get('rules_read', set()))):
                raise ToolDenied('read locked reports and an authorized financial rule before analysis')
            if role == 'reporter' or identity.get('frozen_analysis_reader') or (role == 'question' and self.analysis_snapshots):
                if not self.analysis_snapshots:
                    raise ToolDenied('reporter can only read already frozen financial analysis')
                analysis = list(self.analysis_snapshots.values())
            else:
                analysis = [project_analysis(self.smart_analysis())] if hasattr(self, 'smart_analysis') else [analyze_financials(self.store, self.task_company,
                    sorted({f.period for f in self.store.facts}), self.report_date, self.analysis_scope,
                    disclosures=getattr(self, 'accounting_disclosures', []))]
                for item in analysis:
                    self.freeze_analysis(item)
            available_periods = {period for item in analysis for period in item.get('periods', [])}
            selection = self.analysis_selection(available_periods, p)
            selected_period = selection['selected_period']
            rows = []
            for item in analysis:
                prefix = self.analysis_prefix(item)
                for category in ('metrics', 'signals', 'events'):
                    if requested_category and category != requested_category:
                        continue
                    for index, record in enumerate(item.get(category, [])):
                        if selected_period and record.get('period') != selected_period and selected_period not in record.get('periods', []) and not (
                            category == 'events' and not p.get('period') and not record.get('period') and not record.get('periods')):
                            continue
                        if p.get('metric') and record.get('metric') != p['metric']:
                            continue
                        if p.get('kind') and record.get('kind') != p['kind']:
                            continue
                        raw_slot_id = f'{prefix}.{category}.{index}'
                        slot_id = raw_slot_id + '.text'
                        import json
                        rows.append({'category': category, 'record': json.loads(self.slots[raw_slot_id]),
                                     'slot_id': slot_id, 'raw_slot_id': raw_slot_id, 'company': item['company']})
            metadata = dict(company=self.task_company, scope=self.analysis_scope, report_date=self.report_date,
                            selection=selection, slot_instruction=SLOT_INSTRUCTION,
                            registered_metrics=metric_catalog(p.get('metric'))
                                if requested_category in {None, 'metrics'} and (p.get('metric') or not rows) else [],
                            registered_kinds=analysis_kind_catalog(requested_category, p.get('kind')) if requested_category or p.get('kind') or not rows else [])
            selection.update(category=requested_category, kind=p.get('kind'))
            if not rows:
                source_query = ANALYSIS_KINDS[p['kind']][1] if p.get('kind') else '会计变更' if requested_category == 'events' else '经营现金流' if requested_category == 'signals' else None
                metadata.update(reason='no_registered_analysis_matches',
                    next_action={'tool': 'mj_analyze', 'arguments': {'analysis_id': 'financial_summary', 'catalog_only': True},
                                 'instruction': '检查已登记指标和可用期间。目录不支持的指标需要补充登记，不能从PDF自行编造公式；不要重复相同空查询。'})
                if role == 'question' and source_query:
                    metadata['next_action'] = {'tool': 'mj_search_blocks', 'arguments': {'query': source_query, 'limit': 3},
                        'instruction': '冻结分析没有匹配记录，不等于不存在该事项。需要原文时检索并引用受控原文槽位；不要用营收等其他指标替代。'}
                if role == 'question':
                    metadata.update(self._analysis_missing_payload(p, requested_category))
            output = self.bounded_page(rows, p, lambda items: dict(analysis=items, slots={r['slot_id']: self.slots[r['slot_id']] for r in items},
                                                                  slot_template='# 财报分析\n'+'\n\n'.join('{{'+r['slot_id']+'}}' for r in items),
                                                                  slot_ids=[r['slot_id'] for r in items]), metadata)
            output['selection']['page_periods'] = sorted({period for item in output['analysis'] for period in
                ([item['record']['period']] if item['record'].get('period') else item['record'].get('periods', []))}, key=_analysis_period_key)
            identity['revealed_slots'].update(output['slot_ids'])
            identity['turn_revealed_slots'].update(output['slot_ids'])
            return output
        if name == 'mj_submit_report':
            slots = p['slots']
            template = p['template']
            referenced = re.findall(r"\{\{([^{}]+)\}\}", template)
            if role == 'question' and (not referenced or not set(referenced).issubset(identity['turn_revealed_slots'])):
                raise ToolDenied('question answer must cite at least one frozen slot read in this turn')
            if set(slots) != set(referenced) or any(slot not in self.slots for slot in referenced):
                raise ToolDenied("unknown or undeclared report slot")
            source_slots = {slot for slot in referenced if slot in self.source_references and
                            self.source_references[slot].get('reference_status') == 'unverified_source_quotation'}
            if len(source_slots) > 32:
                raise ToolDenied('Each message permits at most 32 distinct source quotations; select fewer slots')
            if any(not self._quotation_is_current(self.source_references[slot]) for slot in source_slots):
                raise ToolDenied('Source quotation is outside the task disclosure cutoff')
            if role == 'question' and identity.get('definition_terms'):
                from .knowledge import definition_subject_matches
                quotes = [self.source_references[slot].get('quote_text','') for slot in source_slots
                          if self.source_references[slot].get('material_kind') == 'accounting_knowledge']
                definitions_present = all(any(definition_subject_matches(text, term) for text in quotes)
                                          for term in identity['definition_terms'])
                if not definitions_present and any(slot not in self.diagnostic_slots for slot in referenced):
                    raise ToolDenied('Definition answer requires the exact subject definition; incidental or modified terms cannot substitute; use missing-material slots if unavailable')
            if any(slot not in identity['revealed_slots'] or
                   (not slot.startswith('analysis.') and not
                    (role == 'question' and slot.startswith('source.') and slot in self.source_references)
                    and not (role == 'question' and slot.startswith('diagnostic.') and slot in self.diagnostic_slots)
                    and slot.split('.')[0] not in identity['mentions'])
                   for slot in referenced):
                raise ToolDenied("report slot outside session scope or not read by this session")
            plain = re.sub(r"\{\{[^{}]+\}\}", "", template)
            allowed_headings = {'明镜核查报告', '核查报告', '财报分析', '核查结果', '核查结果如下', '原文', '证据',
                                '来源与证据', '规则依据', '公式', '修改建议', '正确值', '替换文本', '待补材料', '提示',
                                '营业收入', '归母净利润', '净利润', '扣非净利润', '经营现金流', '毛利率', '净利率', '资产负债率'}
            words = re.findall(r'[\w\u4e00-\u9fff]+', plain)
            if any(word not in allowed_headings for word in words):
                raise ToolDenied("Report text must use registered headings and frozen slots only")
            rendered = re.sub(r"\{\{([^{}]+)\}\}", lambda m: self.slots[m[1]], template)
            self.templates[role] = rendered
            self.submitted_slots[role] = list(dict.fromkeys(referenced))
            if role == 'question':
                identity['answer_submitted'] = True
            return {"accepted": True, "report_hash": digest(rendered)}
        raise ToolDenied("unknown tool")

    def _required_verification_rules(self, claim: AtomicClaim) -> list[str]:
        if claim.table_context.get('smart_cid') and hasattr(self, 'trusted_verifier'):
            return ['SMART-registered-kernel']
        from .source_quarter_quotation import validated_threshold_reference
        if validated_threshold_reference(claim):
            return ['SOURCE-threshold-entailment']
        from .source_table_reference import validated_table_reference
        if '来源表' in claim.caliber_hint and validated_table_reference(claim):
            return ['E1-value']
        range_rules=['FIN-range'] if _source_amount_range(claim) else []
        if claim.relation == 'pp_difference':
            return ['FIN-pp-difference']
        if claim.relation=='cagr':return ['FIN-cagr']
        spec=METRIC_SPECS.get(claim.account)
        if spec:
            scope='母公司' if '母公司' in claim.caliber_hint else '合并'
            outcome=self.store.query_outcome(claim.company,claim.account,claim.period,scope,
                         '扣非' if claim.account=='NP_PARENT_ADJ' or '扣非' in claim.caliber_hint else '未扣非',
                         cutoff=self.report_date,**claim_dimensions(claim))
            if outcome.status=='missing':
                from .financial_derivations import derive_registered_metric
                source=claim.declared_source.removeprefix('evidence:') if claim.declared_source else None
                computation=derive_registered_metric(self.store,claim.company,claim.account,claim.period,scope,
                    '扣非' if claim.account=='NP_PARENT_ADJ' or '扣非' in claim.caliber_hint else '未扣非',self.report_date,
                    strategy='pinned' if source else claim.query_strategy,source_doc=source,source_claim=claim,**claim_dimensions(claim))
                result=computation.rule_ids or [spec.rule_id]
                if claim.relation in {'yoy','qoq','cagr'}:
                    result.append({'yoy':'E5-yoy','qoq':'FIN-qoq','cagr':'FIN-cagr'}[claim.relation])
                return [*result,*range_rules]
            return range_rules or ['E1-value']
        from .financial_derivations import FLOW_ACCOUNTS
        eligible = (claim.account == 'EPS' and '基本每股收益' in claim.text
                    or claim.account in FLOW_ACCOUNTS and claim.period[4:] in {'Q2', 'Q3', 'Q4'})
        if claim.relation != 'value' or not eligible:
            # The three-state numeric baseline also applies when growth or
            # comparison operands are missing. Never advertise an empty
            # prerequisite list while the host requires a real rule read.
            return range_rules or ['E1-value']
        scope = '母公司' if '母公司' in claim.caliber_hint else '合并'
        nr = '扣非' if claim.account == 'NP_PARENT_ADJ' or '扣非' in claim.caliber_hint else '未扣非'
        outcome = self.store.query_outcome(claim.company, claim.account, claim.period, scope, nr,
                    cutoff=self.report_date, strategy='pinned' if claim.declared_source else claim.query_strategy,
                    source_doc=claim.declared_source,**claim_dimensions(claim))
        # Conflict/unknown-date/low-confidence direct disclosures remain X4;
        # only a genuine missing disclosure can activate these derivations.
        return ([*['FIN-EPS' if claim.account == 'EPS' else 'FIN-single-quarter'],*range_rules]
                if outcome.status == 'missing' else ['E1-value'])

    def _require_verification_reads(self, identity: dict, claim: AtomicClaim) -> None:
        required = self._required_verification_rules(claim)
        if required and (claim.claim_id not in identity.get('facts_read', set())
                         or not set(required).issubset(identity.get('rules_read', set()))):
            raise FinancialRequirementError(required,
                facts_required=claim.claim_id not in identity.get('facts_read', set()))

    def freeze_bindings(self) -> list[AtomicClaim]:
        bound = []
        for mid, claim in self.claims.items():
            a, b = self.bindings['planner'].get(mid), self.bindings['verifier'].get(mid)
            # Compare binder output in the vocabulary frozen by the source
            # parser. This narrow alias layer handles broker compact residuals
            # and explicitly labelled revenue shares; all other substitutions
            # remain subject to the frozen-source guard below.
            a_cmp, b_cmp = (dict(a) if a else None), (dict(b) if b else None)
            aliases = []
            for role_name, item in (('planner', a_cmp), ('verifier', b_cmp)):
                if item is None:
                    continue
                canonical, alias = self._canonical_binding_account(claim, item.get('account', ''))
                if alias and canonical != item.get('account'):
                    aliases.append({'role': role_name, 'from': item.get('account'),
                                    'to': canonical, 'reason': alias})
                    item['account'] = canonical
            essential = ('company', 'account', 'period', 'scope', 'relation')
            agreed = bool(a_cmp and b_cmp and a_cmp == b_cmp
                          and all(a_cmp.get(key) for key in essential)
                          and a_cmp['account'] != 'UNKNOWN')
            source_scope = self.source_dimensions(claim).get('scope_header')
            # validated_binding has already restricted scope to the registered
            # enum. Requiring equality with the frozen header prevents an
            # arbitrary scope string from becoming source evidence.
            scope_proven = source_scope in {'合并', '母公司', '来源表'} and bool(a_cmp) and a_cmp.get('scope') == source_scope
            if not scope_proven:
                agreed = False
            from .source_quarter_quotation import validated_threshold_reference
            smart = claim.table_context.get('smart_binding')
            smart_yoy = bool(agreed and smart and hasattr(self, 'trusted_verifier') and smart.get('relation') == 'yoy'
                             and all(a_cmp.get(key) == smart.get(key) for key in BIND_FIELDS))
            if agreed and a_cmp['relation'] != 'value' and not a_cmp['base_period'] and not smart_yoy and not validated_threshold_reference(claim):
                agreed = False
            self.agreements[mid] = {"claim_id": mid, "agreed": agreed,
                                    "differences": [key for key in BIND_FIELDS
                                                    if not a_cmp or not b_cmp or a_cmp[key] != b_cmp[key]],
                                    "scope_source_proven": scope_proven}
            # Keep the historical agreement schema byte-for-byte when no
            # alias was used; record the extra provenance only for an actual
            # registered source-account alias.
            if aliases:
                self.agreements[mid]["account_aliases"] = aliases
            if agreed:
                nr = '\u6263\u975e' if a_cmp['account'] == 'NP_PARENT_ADJ' else '\u672a\u6263\u975e'
                proposed = replace(claim, company=a_cmp['company'], account=a_cmp['account'], period=a_cmp['period'],
                                   caliber_hint=(a_cmp['scope'], nr), relation=a_cmp['relation'],
                                   base_period=a_cmp['base_period'] or None, binding_status='agreed')
                smart = claim.table_context.get('smart_binding')
                if smart and hasattr(self, 'trusted_verifier'):
                    source_issue = (None if all(a_cmp[k] == smart[k] for k in BIND_FIELDS) else
                                    ('X3', '绑定与宿主冻结新版解析语义冲突'))
                else:
                    source_issue = source_binding_issue(proposed, strict=True)
                agreed = source_issue is None
                self.agreements[mid].update(agreed=agreed, source_dimensions_proven=agreed,
                                            source_binding_issue=source_issue[1] if source_issue else None)
                if agreed:
                    claim = proposed
            if not agreed:
                claim = replace(claim, binding_status='disagreed')
            self.claims[mid] = claim
            bound.append(claim)
        return bound

    def freeze_results(self, results: list[dict]) -> None:
        self.results = results
        for item in results:
            mid = item['claim_id']
            self.slots[f'{mid}.result'] = f"{item['status']}：{item.get('reason') or '确定性内核已核查'}"
            self.slots[f'{mid}.evidence'] = '；'.join(item.get('evidence', [])) or '缺少可用证据'
            correction = item.get('correction') or {}
            self.slots[f'{mid}.correction'] = correction.get('replacement_text', '无需替换或需要补充材料')
            import json
            claim = self.claims.get(mid)
            detail = {key: item.get(key) for key in ('status','error_type','abstention_code','observed','expected','display_value','formula','tolerance','reason','evidence','basis',
                      'blockers','source_dimensions','claim_interval','expected_interval','verification_basis','precision_note','source_precision','rule_ids','advisories','diagnostic_hypotheses','calculation_steps')}
            detail['operands'] = [self.compact_operand(row) for row in item.get('operands', [])]
            if claim:
                detail.update(company=claim.company, account=claim.account, period=claim.period, source_text=claim.text, source_unit=claim.unit)
            self.slots[f'{mid}.detail'] = json.dumps(detail, ensure_ascii=False)

    @staticmethod
    def compact_operand(row):
        account = row.get('account')
        value_unit = ('无量纲比例' if row.get('source_relation') == 'yoy' else
                      '倍' if account in TURNOVER_METRICS|{'CURRENT_RATIO','QUICK_RATIO'} else
                      '元' if account in AMOUNT_ACCOUNTS else '无量纲比例' if account in RATIO_ACCOUNTS
                      else '元/股' if account in PER_SHARE_ACCOUNTS else '股' if account in SHARE_ACCOUNTS
                      else row.get('value_unit') or row.get('unit') or 'unknown')
        compact = {key: row.get(key) for key in ('fact_id','company','account','period','scope','nr','value','evidence','source_doc','publish_date','version','display_unit','source_relation',
                     'decimals','column_label','table_label','version_group','dimension_kind','dimension_id','currency','definition_id',
                     'source_table_id','source_table_evidence','source_scope_evidence','source_row_index',
                     'operand_type','assertion_id','operator','bound','source_quote','eligible_as_point_fact')}
        return dict(compact, unit=value_unit, value_unit=value_unit)

    @staticmethod
    def analysis_prefix(analysis):
        return 'analysis.' + digest([analysis.get('company'), analysis.get('periods')])[:12]

    def analysis_selection(self, periods, p):
        available = sorted(set(periods), key=_analysis_period_key)
        selected = p.get('period') or self.task_period or (available[-1] if available else None)
        return dict(mode='explicit' if p.get('period') else 'task_period' if self.task_period else 'latest_frozen',
                    task_period=self.task_period, requested_period=p.get('period'), selected_period=selected,
                    available_periods=available[-24:], period_count=len(available), periods_truncated=len(available) > 24)

    def bounded_page(self, rows, p, render, metadata=None):
        import json
        cursor, size = p.get('cursor', 0), p.get('limit', 5)
        items = rows[cursor:cursor+size]
        while True:
            output = render(items)
            following = cursor+len(items)
            output.update(total_matched=len(rows), next_cursor=following if following < len(rows) else None,
                          returned=len(items), complete=following >= len(rows), notice='本页仅覆盖返回项，完整结果请查看冻结报告或继续分页')
            output.update(metadata or {})
            if len(json.dumps(output, ensure_ascii=False).encode('utf-8')) <= 16384:
                return output
            if len(items) <= 1:
                raise ToolDenied('单条证据结果超过响应上限，请查看本地冻结报告；不截断数字或证据')
            items = items[:-1]

    def freeze_analysis(self, analysis: dict) -> None:
        import json
        from .presentation import analysis_record_text
        if not self.task_company or analysis.get('company') != self.task_company:
            raise ToolDenied('analysis snapshot outside trusted task company')
        for category in ('metrics', 'signals', 'events'):
            for record in analysis.get(category, []):
                identities = [record.get('company')] + [row.get('company') for row in record.get('operands', [])]
                if any(company and company != self.task_company for company in identities):
                    raise ToolDenied('analysis record or operand outside trusted task company')
        prefix = self.analysis_prefix(analysis)
        self.analysis_snapshots[prefix] = json.loads(json.dumps(analysis, ensure_ascii=False))
        for category in ('metrics', 'signals', 'events'):
            for index, record in enumerate(analysis.get(category, [])):
                # Full deterministic records retain operands, units, formula,
                # dated source evidence, abstention and registered thresholds.
                frozen = {'company': analysis.get('company'), 'scope': analysis.get('scope'), **record}
                if 'operands' in frozen:
                    frozen['operands'] = [self.compact_operand(row) for row in frozen['operands']]
                slot_id = f'{prefix}.{category}.{index}'
                self.slots[slot_id] = json.dumps(frozen, ensure_ascii=False)
                self.slots[slot_id + '.text'] = analysis_record_text(category, frozen, analysis.get('parameters'))
