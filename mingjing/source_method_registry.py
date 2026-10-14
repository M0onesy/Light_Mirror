"""原文取证的券商自定义指标口径映射 registry（A+C 改造的 C 部分）。

设计原则（与"宁弃权不造假"护城河一致）：

1. **数据与逻辑分离**：本文件只放"数据驱动的可外部扩充映射表"加其校验框架；
   券商简表方法的强身份识别逻辑仍在 ``source_method.py``，两者互不污染。
2. **出处即证据**：每条映射必须携带原文出处四元组
   （``report_date`` + ``source_page`` + ``source_quote`` + ``source_span``）
   与原文片段 SHA-256；缺一即拒绝。
3. **绝不读金额/候选/事实/模型输出**：``validated_mapping`` 只看 claim 的来源
   未定口径标识与 registry 自身，绝不反推公式或按相近金额对齐口径。
4. **空表起步**：在拿到原 44 页研报 PDF（或等价 triage JSON / 历史字段字典）
   之前不得写入任何一条映射；凭指标名称反推口径触碰护城河红线。

映射 schema：``source-method-mapping.v1``，结构见 ``registry`` 内注释示例。
"""
from __future__ import annotations

import hashlib

SCHEMA = 'source-method-mapping.v1'

# 出处枚举：只允许"原文等价"来源；用户补充/模型推断一律不得写入。
PROVENANCE_ALLOWED = ('report_original_footnote', 'report_original_table',
                      'report_original_disclosure')

# 抗凑数阈值：同一原文片段（text_sha256）最多可支撑多少个**互不相同**的
# 目标指标（canonical_account）。超过即判定为"一源多标"式的格式凑数，
# 整组拒绝放行。理由：真实的口径依据是"一个指标锚定一条最相关原文"，
# 而非"几十个不相关指标共用一条通用规定"（如所有指标都挂到"公式运算符白名单"）。
# 这一常量与 A+C 护城河一致：宁可维持弃权，也不接受出处凑数的映射。
MAX_ACCOUNTS_PER_SOURCE = 2

# 每条映射必须携带原文出处四元组 + 内容哈希；缺一不可。
# 初始为空：见模块 docstring 第 4 点。真实条目必须逐个从原 PDF 取证后填入。
registry: list[dict] = [
    # ---- 示例（仅示范结构，非真实数据；真实条目必须逐个从原 PDF 取证后填入）----
    # {
    #   'schema_version': 'source-method-mapping.v1',
    #   'source_account': 'ROE_UNSPECIFIED',           # 原文未定义标签
    #   'canonical_account': 'ROE',                    # 映射到的登记指标
    #   'definition_id': 'metric:roe-end-equity',      # METRIC_SPECS 已登记 definition_id
    #   'rule_id': 'FIN-roe',                          # 可选：登记规则
    #   'source_page': 42,                             # 原文页码（int）
    #   'source_quote': '净资产收益率=净利润/期末所有者权益',  # 原文片段
    #   'source_span': {'start': 12345, 'end': 12378, 'text': '净资产收益率=净利润/期末所有者权益'},
    #   'report_date': '2024-04-22',                   # 研报日期（回溯截止日）
    #   'text_sha256': '<sha256 of source_quote utf-8>',  # 与 _text_hash 同构
    #   'provenance': 'report_original_footnote',      # 只允许原文出处枚举
    # },
]


def _sha256_text(value: str) -> str:
    """与 ``source_semantics._text_hash`` 同构的原文片段哈希（utf-8）。"""
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()


def _shared_source_accounts(text_sha256: str) -> set[str]:
    """返回 registry 中与给定片段哈希关联的全部互异 canonical_account。

    只做"来源共享度"统计，不读取任何金额、候选事实或模型输出。
    用于识别"一源多标"式出处凑数（详见 MAX_ACCOUNTS_PER_SOURCE）。
    """
    accounts: set[str] = set()
    for row in registry:
        if not isinstance(row, dict):
            continue
        if row.get('text_sha256') != text_sha256:
            continue
        account = row.get('canonical_account')
        if isinstance(account, str) and account:
            accounts.add(account)
    return accounts


def _mapping_evidence_ok(row: dict) -> bool:
    """校验单条映射的出处是否完整、内部自洽且可核验。

    只读取映射自身字段；不读取任何金额、候选事实或模型输出。
    任一条件不满足即返回 False（维持弃权语义）。
    """
    if not isinstance(row, dict):
        return False
    # 1) 必填字段
    for key in ('schema_version', 'source_account', 'canonical_account', 'definition_id',
                'source_page', 'source_quote', 'source_span', 'report_date',
                'text_sha256', 'provenance'):
        if key not in row:
            return False
    # 2) schema 版本必须匹配
    if row['schema_version'] != SCHEMA:
        return False
    # 3) 出处必须是允许的原文枚举
    if row['provenance'] not in PROVENANCE_ALLOWED:
        return False
    # 4) 页码为正整数
    if type(row['source_page']) is not int or row['source_page'] < 1:
        return False
    # 5) 研报日期非空字符串
    if not isinstance(row['report_date'], str) or not row['report_date'].strip():
        return False
    # 6) 原文片段必须是非空字符串
    if not isinstance(row['source_quote'], str) or not row['source_quote']:
        return False
    # 7) 原文片段哈希一致（与 source_semantics._text_hash 同构）
    if _sha256_text(row['source_quote']) != row['text_sha256']:
        return False
    # 8) 字符区间自洽，且区间文本与 source_quote 逐字一致
    span = row['source_span']
    from .source_method import _ref_ok
    if not _ref_ok(span, text=None):
        return False
    if span['text'] != row['source_quote']:
        return False
    # 9) canonical_account 必须映射到真实登记的 definition_id
    from .metrics import METRIC_SPECS
    spec = METRIC_SPECS.get(row['canonical_account'])
    if spec is None or spec.definition_id != row.get('definition_id'):
        return False
    # 10) 抗凑数：同一原文片段不得被过多互异指标共用（一源多标 → 整组拒绝）。
    #     真实的口径依据是一标一条最相关原文；几十条不相关指标共用同一条
    #     通用规定（如"公式运算符白名单"）属格式凑数，一律不放行。
    if len(_shared_source_accounts(row['text_sha256'])) > MAX_ACCOUNTS_PER_SOURCE:
        return False
    return True


def validated_mapping(claim) -> dict | None:
    """仅当出处可核验的 registry 命中时返回映射，否则返回 None。

    绝不读金额、候选值、事实或模型输出。缺出处一律返回 None（维持弃权）。
    命中不代表 verdict 转变——放行由调用方（C1）决定，本函数只做取证校验。
    """
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    account = source.get('source_account_unresolved') or getattr(claim, 'account', '')
    for row in registry:
        if not isinstance(row, dict):
            continue
        if row.get('schema_version') != SCHEMA or row.get('source_account') != account:
            continue
        # (a) 出处必须完整且内部一致
        if not _mapping_evidence_ok(row):
            continue
        return dict(row)
    return None
