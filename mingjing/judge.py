from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation, localcontext
import re
from .compute import display_amount, display_percent
from .facts import FactStore, _date_key
from .mentions import unit_multiplier, source_currency, VALUE_RE, NUM, percentage_point_dimensions, cagr_source_dimensions
from .models import AtomicClaim, VerificationResult
from .decimal_math import DecimalInterval, fact_interval, growth_interval, ratio_interval, resolution, rounding_interval, round_display_interval
from .rules import execute_rule, execute_rule_interval, intervals_compatible, compare_disclosure_intervals
from .taxonomy import AMOUNT_ACCOUNTS as REGISTERED_AMOUNTS, ACCOUNT_NAMES as REGISTERED_NAMES, PER_SHARE_ACCOUNTS,SHARE_ACCOUNTS
from .metrics import METRIC_SPECS, RATIO_METRICS, AMOUNT_METRICS, TURNOVER_METRICS, metric_spec
from .dimensions import claim_dimensions

RATIO_ACCOUNTS = {"GROSS_MARGIN", "NET_MARGIN", "NP_PARENT_MARGIN", "DEBT_RATIO", "ROE"}
RATIO_ACCOUNTS |= RATIO_METRICS
AMOUNT_ACCOUNTS = {"REVENUE", "COST", "NP", "NP_PARENT", "NP_PARENT_ADJ", "CFO",
                   "ASSETS", "LIABILITIES", "EQUITY", "EQUITY_PARENT", "NRI_TOTAL",
                   "NRI_TAX_EFFECT", "NRI_MINORITY", "NRI_MINORITY_RAW"}
ACCOUNT_NAMES = {"REVENUE": "营业收入", "NP_PARENT": "归母净利润", "NP_PARENT_ADJ": "扣非净利润", "NP": "净利润", "COST": "营业成本", "CFO": "经营现金流", "GROSS_MARGIN": "毛利率", "NET_MARGIN": "净利率", "DEBT_RATIO": "资产负债率", "EPS": "每股收益", "ROE": "净资产收益率"}
AMOUNT_ACCOUNTS |= REGISTERED_AMOUNTS
AMOUNT_ACCOUNTS |= AMOUNT_METRICS
ACCOUNT_NAMES = {**REGISTERED_NAMES, **ACCOUNT_NAMES}
ACCOUNT_NAMES['EPS_DILUTED'] = '稀释每股收益'


def _source_numeric_position(claim: AtomicClaim) -> tuple[int | None, bool]:
    """Return a source-local position without guessing among repeated values.

    Old frozen claims remain readable: a unique raw numeric string is enough
    to locate the occurrence. Missing occurrence identity is a semantic
    ambiguity, never permission to select the first matching number.
    """
    position = claim.source_numeric_start
    if position is not None:
        valid = (type(position) is int and 0 <= position <= len(claim.text)
                 and (not claim.numeric_text or
                      claim.text[position:position+len(claim.numeric_text)] == claim.numeric_text))
        return (position, True) if valid else (None, False)
    if claim.numeric_text:
        positions = [m.start() for m in re.finditer(re.escape(claim.numeric_text), claim.text)]
        return (positions[0], True) if len(positions) == 1 else (None, False)
    # Legacy programmatic claims have no raw occurrence field. Their whole
    # source still receives conservative domain guards; new extraction always
    # records the exact local offset.
    return None, True


def _source_amount_range(claim, *, with_span=False):
    """A range endpoint/connector cannot be interpreted as a signed scalar."""
    position, valid = _source_numeric_position(claim)
    if not valid:
        return None
    units = r'百万元|亿元|万元|元|[%％]'
    ranges = re.finditer(rf'{NUM}\s*(?P<low_unit>{units})?\s*[-－–—~～至到]\s*'
                         rf'{NUM}\s*(?P<high_unit>{units})', claim.text)
    for match in ranges:
        # “增长55%至4.14亿元” states growth plus amount, not one range.
        low, high = match['low_unit'], match['high_unit']
        if low and low != high and {low, high} != {'%', '％'}:
            continue
        if position is None or match.start() <= position < match.end():
            parsed=re.fullmatch(rf'(?P<low>{NUM})\s*(?:{units})?\s*[-－–—~～至到]\s*(?P<high>{NUM})\s*(?:{units})',match.group())
            if parsed:
                convert=lambda value:Decimal(value.replace(',','').replace('，','').replace('−','-').replace('－','-'))
                left,right=convert(parsed['low']),convert(parsed['high'])
                with localcontext() as context:
                    context.prec=max(64,len(left.as_tuple().digits)+len(right.as_tuple().digits)+16)
                    low_value=left*unit_multiplier(low or high)
                    high_value=right*unit_multiplier(high)
                if low_value<=high_value:
                    interval=DecimalInterval(low_value,high_value,True,True)
                    return (interval,match.start(),match.end()) if with_span else interval
    return None

def _source_amount_range_issue(claim: AtomicClaim) -> tuple[str,str] | None:
    # Source ranges now have an explicit containment rule. Unparseable source
    # structures remain subject to X6 and never become scalar endpoints.
    if _source_amount_range(claim) and claim.relation not in {'value','ratio','share'}:
        return 'X2','原文增长或比较关系的范围断言尚无对应授权规则，不能按端点作单值判断'
    return None


def _source_semantic_issue(claim: AtomicClaim) -> tuple[str, str] | None:
    """Source qualifiers are authoritative over model account/relation labels.

    Fact currently has no business-segment dimension. A segment occurrence
    cannot consume a whole-company fact even when two model bindings agree.
    Only source before the target is considered, so a later segment value
    does not invalidate a preceding whole-company value in the same sentence.
    """
    position, valid = _source_numeric_position(claim)
    if not valid:
        return "X3", "无法唯一定位原文中的目标数字；重复数值需要保留原文局部位置"
    source = claim.table_context
    from .source_quarter_quotation import validated_threshold_reference
    if validated_threshold_reference(claim):
        return None
    if source.get('period_source') == 'source_header':
        # These dimensions come from the untouched source table. Two models
        # agreeing to relabel a cash-flow change as a loan balance, or to pick
        # another period with a closer amount, do not make the binding valid.
        for key, observed in (('account_hint', claim.account), ('period_hint', claim.period),
                              ('relation_hint', claim.relation)):
            expected = source.get(key)
            if expected and expected != 'UNKNOWN' and observed != expected:
                return 'X3', '语义绑定与原文表格的科目、期间或关系定义冲突'
        source_scope = source.get('scope_hint')
        if source_scope in {'合并', '母公司'} and source_scope not in claim.caliber_hint:
            return 'X3', '语义绑定与原文表格明确声明的报表口径冲突'
    prefix = claim.text[:position] if position is not None else claim.text
    # This operation checks an explicitly cited table's transcription, rather
    # than authorising a free-form ratio calculation or its original denominator.
    from .source_table_reference import validated_table_reference
    from .source_business_mode import DEFINITION as business_mode_definition
    reference = validated_table_reference(claim)
    if reference and reference.get('quotation_kind') in {'quarter_revenue_share_narrative', 'business_mode_quarter_share_caption'}:
        from .source_quarter_quotation import narrative_identity
        if narrative_identity(claim):
            return None
    if (reference and reference.get('quotation_kind') == 'business_mode_narrative'
            and reference.get('source_definition_id') == business_mode_definition):
        return None
    if reference and reference.get('quotation_kind') in {
            'customer_type_chart', 'customer_type_narrative', 'customer_concentration_narrative'}:
        from .source_customer_quotation import identity as customer_identity
        # This only authorizes transcription of the registered source table,
        # including its disclosed percentage. It grants no free ratio formula.
        if customer_identity(claim):
            return None
    if reference and reference.get('quotation_kind') in {'product_financial_narrative', 'product_financial_caption'}:
        from .source_product_financial import narrative_identity
        if narrative_identity(claim):
            return None
    title = str(claim.table_context.get("title", ""))
    compact = re.sub(r"\s+", "", title + "；" + prefix)
    total_subjects = list(re.finditer(
        r"(?:全公司|集团|公司|合并(?:报表|口径)?)(?:整体|总体|合计)?(?:的)?"
        r"(?:20\d{2}年(?:度)?|报告期)?(?:实现|总)?"
        r"(?:营业总收入|营业收入|营收|收入|营业成本|归母净利润|扣非净利润|净利润|"
        r"毛利率|净利率|经营现金流|现金流|资产负债率|每股收益|净资产收益率|ROE)",
        compact, re.I))
    total_end = total_subjects[-1].end() if total_subjects else -1
    cagr = list(re.finditer(r"CAGR|(?:年均|年化)?复合(?:年均)?(?:增长率|增速|增长)", compact, re.I))
    rates = list(re.finditer(r"同比|环比", compact))
    rate_end = rates[-1].end() if rates else -1
    if cagr and cagr[-1].end() > max(total_end, rate_end):
        cagr_dimensions = cagr_source_dimensions(
            claim.text, position if position is not None else len(claim.text))
        if (claim.relation != 'cagr' or not cagr_dimensions
                or not 1 <= cagr_dimensions['source_cagr_period']['years'] <= 100):
            return "X2", "原文目标数值为CAGR/复合增长率，缺少来源明确的首尾期间与整数期数，不能当金额或普通同比判定"
    if claim.unit == "百分点":
        pp = percentage_point_dimensions(claim.text, position) if position is not None else {}
        if not pp:
            return "X2", "原文百分点差缺少明确比率定义、升降方向或可用基期；不能当绝对比率或同比判定"
        if claim.relation != 'pp_difference':
            return 'X3', '原文目标为百分点差，不能改写为绝对比率或普通同比'
        if pp.get('percentage_point_direction_conflict'):
            return 'X3', '原文百分点的升降方向与明示正负号冲突；不能由模型改写方向'
    shares = list(re.finditer(r"占比|占总|比重|占(?:营业收入|营收|收入|净利润|归母净利润)", compact))
    if shares and shares[-1].end() > max(total_end, rate_end) and claim.unit in {"%", "％"}:
        if claim.account != 'REVENUE_SHARE' or metric_spec(claim.account,claim_dimensions(claim)['definition_id']) is None:
            return "X2", "原文目标数值为占比；当前无匹配分子、分母定义与授权占比公式"
    segments = list(re.finditer(
        r"超算云|行业云|AI云|共建模式|外采(?:算力)?模式|分部|分业务|分产品|"
        r"前(?:五|5)大客户|主要客户|客户(?:营收|收入|毛利率)|核心业务|产品收入结构|"
        r"(?:业务|产品)(?:收入|营收|毛利率|净利率)|主营业务收入|收入构成|收入结构|"
        # Product names, service lines and geographical reporting domains
        # are not issuer totals. Match source noun qualifiers, never amounts
        # or model-proposed segment labels. A later explicit issuer-total
        # subject still resets the domain via ``total_end`` above.
        r"(?:产品|业务线|业务板块|服务|地区|区域|市场)"
        r"[\u4e00-\u9fffA-Za-z0-9_-]{0,24}?"
        r"(?:营业总收入|营业收入|营收|收入|营业成本|成本|毛利率|净利率|净利润)", compact, re.I))
    if segments and segments[-1].end() > total_end:
        if claim_dimensions(claim)['dimension_kind']=='total':
            return "X3", "原文目标数值属于分业务、分部、产品或客户口径；缺少来源明确的匹配分部维度，不能与全公司金额或比率比较"
    return None

def _source_scope_issue(claim: AtomicClaim, *, strict=False, check_binding=True) -> tuple[str, str] | None:
    """New Agent source policy, retained in frozen claims for kernel replay."""
    if (not strict and claim.table_context.get('source_scope_required') is not True
            and not isinstance(claim.table_context.get('source_dimensions'),dict)):
        return None
    from .scope import source_scope_dimensions
    dimensions = source_scope_dimensions(claim)
    if dimensions.get('scope_declaration_conflict'):
        return 'X3', '用户口径澄清与原文明示口径冲突；请修订补充材料后新建任务'
    if dimensions.get('scope_header') not in {'合并', '母公司', '来源表'}:
        return 'X3', '原文未唯一声明合并或母公司报表口径；请补充有来源的口径声明后新建核查任务'
    # Failed or incomplete binding keeps the original parser hints. They
    # are not a committed model scope; the later consistency check reports
    # X3, while the genuine source-scope checks above remain mandatory.
    if (check_binding and claim.binding_status not in {'unknown', 'unresolved', 'disagreed', 'pending'}
            and dimensions['scope_header'] not in claim.caliber_hint):
        return 'X3', '语义绑定与原文明示或用户补充声明的报表口径冲突'
    return None


def _period_label(period):
    return period[:4] + {"Y": "年", "H1": "年上半年", "9M": "年前三季度", "Q1": "年第一季度", "Q2": "年第二季度", "Q3": "年第三季度", "Q4": "年第四季度"}.get(period[4:], period[4:])

def _hedge_kind(claim: AtomicClaim) -> str | None:
    position, valid = _source_numeric_position(claim)
    prefix = claim.text[:position] if valid and position is not None else claim.text
    before = re.sub(r"\s+", "", re.split(r"[,，。；;]", prefix)[-1])
    match = re.search(r"(大约|约|左右|将近|接近|近|逾|超过|多于|不足|不到)(?:为|达|达到|了)?$", before)
    after = claim.text[position+len(claim.numeric_text):] if position is not None else ''
    word = match[1] if match else '左右' if re.match(r'\s*左右', after) else '约' if claim.approximate else None
    if word in {'约', '大约', '左右'}:
        return 'approximately'
    if word in {'逾', '超过', '多于'}:
        return 'above'
    return 'below' if word in {'近', '将近', '接近', '不足', '不到'} else None


def _source_precision(facts):
    return [{"fact_id": fact.fact_id, "display_unit": fact.display_unit,
             "decimals": fact.decimals, "resolution": str(resolution(fact.display_unit, fact.decimals))}
            for fact in facts]


def _asserts_latest_source(claim):
    position, valid = _source_numeric_position(claim)
    prefix = claim.text[:position] if valid and position is not None else claim.text
    local = re.sub(r'\s+', '', re.split(r'[,，。；;]', prefix.rstrip('。；;，, \t\r\n'))[-1])
    return bool(re.search(r'最新(?:披露|报告|年报|数据)|(?:调整后|更正后|重述后)(?:的)?(?:金额|数据|营业收入|营收|净利润|归母净利润)?', local))

class Judge:
    def __init__(self, facts: FactStore, report_date: str | None = None, *, strict_caliber: bool = False, confidence_threshold: str = "0.95", require_dual_binding: bool = False, approximate_multiplier: str | None = None, hedge_relative: str = "0.02"):
        self.facts, self.report_date = facts, report_date
        self.strict_caliber = strict_caliber
        confidence = Decimal(str(confidence_threshold))
        if not confidence.is_finite() or not Decimal('0') <= confidence <= Decimal('1'):
            raise ValueError('事实置信度阈值须为零至一')
        self.confidence_threshold = str(confidence)
        self.require_dual_binding = require_dual_binding
        # The deprecated multiplier is accepted for caller compatibility; the
        # proposal's relative h is explicit and is never inferred from it.
        self.approximate_multiplier = Decimal(str(approximate_multiplier or '1'))
        if not self.approximate_multiplier.is_finite() or not 1 <= self.approximate_multiplier <= 3:
            raise ValueError("旧模糊区间放宽倍数须为一至三")
        self.hedge_relative = Decimal(str(hedge_relative))
        if not self.hedge_relative.is_finite() or not 0 <= self.hedge_relative <= Decimal('0.05'):
            raise ValueError("模糊区间相对放宽参数须为零至百分之五")

    def _claim_interval(self, claim, observed):
        interval = rounding_interval(observed, claim.unit, claim.display_decimals if claim.display_decimals is not None else 2)
        kind = _hedge_kind(claim)
        if kind and observed <= 0:
            raise ValueError('负数或零的模糊限定词没有签发的区间定义')
        if kind == 'approximately':
            return interval.expanded(abs(observed)*self.hedge_relative)
        if kind == 'above':
            if self.hedge_relative == 0:
                raise ValueError('单侧模糊词的零放宽参数不能建立非空授权区间')
            return DecimalInterval(observed, observed*(1+self.hedge_relative), False, True)
        if kind == 'below':
            if self.hedge_relative == 0:
                raise ValueError('单侧模糊词的零放宽参数不能建立非空授权区间')
            return DecimalInterval(observed*(1-self.hedge_relative), observed, True, False)
        return interval

    def _interval_result(self, result, claim, observed_interval, expected_interval, facts, rule_id):
        result.claim_interval = observed_interval.to_dict()
        result.expected_interval = expected_interval.to_dict()
        result.source_precision = _source_precision(facts)
        result.verification_basis = 'disclosure_interval_compatibility'
        result.rule_ids = [rule_id]
        if result.status == 'verified':
            result.precision_note = '与披露精度相容；不表示原文所有精确位数均已独立核实'
        elif result.correction and len(facts) > 1 and result.expected is not None:
            # A derived correction cannot inherit arbitrary fine draft digits.
            # Use a point only when the entire propagated source interval
            # rounds to that point. Otherwise leave a visible interval proposal.
            scale = unit_multiplier(claim.unit)
            bounds = expected_interval.divide_positive(DecimalInterval(scale, scale, True, True))
            requested = claim.display_decimals if claim.display_decimals is not None else 2
            supported = next((decimals for decimals in range(min(max(requested, 2), 12), -1, -1)
                              if round_display_interval(bounds, decimals)[0] == round_display_interval(bounds, decimals)[1]), None)
            if supported is not None:
                revised_claim = replace(claim, display_decimals=supported)
                result.correction = self._correction(revised_claim, Decimal(result.expected), facts, result.formula or '')
                result.correction.update(precision_basis='propagated_interval_stable_rounding',
                                         replacement_precision=str(supported))
                result.precision_note = '修订文本按操作数披露区间可支持的小数位展示；完整来源区间保留供复核'
            else:
                decimals = min(max(requested, 2), 12)
                _, _, lower, upper = round_display_interval(bounds, decimals)
                display = f'{format(lower, f".{decimals}f")}至{format(upper, f".{decimals}f")}{claim.unit}'
                relation_label = {'yoy':'同比变动率', 'qoq':'环比变动率', 'pp_difference':'较基期的百分点变化'}.get(claim.relation, '')
                result.correction.update(correct_value=display, precision_basis='propagated_interval_range',
                    replacement_text=f'{claim.company}{_period_label(claim.period)}{ACCOUNT_NAMES.get(claim.account, claim.account)}{relation_label}的计算结果按来源披露精度为区间{display}；请结合完整来源区间确认修订表述。',
                    replacement_status='interval_requires_review', replacement_display=display)
                result.precision_note = '来源区间在整数位仍跨越舍入边界，不能生成有来源支持的单点修订值；区间建议需复核'
        return result

    def _lookup(self, claim, scope, nr, *, source=None, strategy=None, period=None, account=None):
        dimensions=claim_dimensions(claim)
        target=account or claim.account
        if account or target not in METRIC_SPECS or not METRIC_SPECS[target].definition_required:
            dimensions['definition_id']=''
        return self.facts.query_outcome(claim.company, account or claim.account,
            claim.period if period is None else period, scope, nr, cutoff=self.report_date,
            strategy=strategy or ('pinned' if source else 'latest_as_of'),
            source_doc=source, min_confidence=self.confidence_threshold, **dimensions)

    def _available(self, fact):
        return (fact.source_relation == 'value'
                and Decimal(fact.parse_conf) >= Decimal(self.confidence_threshold)
                and (not self.report_date or _date_key(fact.publish_date)[0]
                     and fact.publish_date <= self.report_date))

    @staticmethod
    def _same_claim_dimension(claim,fact):
        dim=claim_dimensions(claim)
        return (fact.dimension_kind,fact.dimension_id,fact.currency)==(dim['dimension_kind'],dim['dimension_id'],dim['currency'])

    def verify(self, claim: AtomicClaim) -> VerificationResult:
        try:
            dimensions=claim_dimensions(claim)
        except ValueError as exc:
            return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X3',
                                      reason=str(exc),blockers=[{'code':'X3','kind':'source_dimensions','reason':str(exc)}],
                                      source_dimensions={'validation_status':'rejected','source_start':claim.start,'source_end':claim.end})
        result = self._verify(claim)
        from .source_semantics import source_dimensions_from_text
        from .scope import source_scope_dimensions
        source_proof=source_dimensions_from_text(claim)
        scope_proof=source_scope_dimensions(claim)
        result.source_dimensions={**source_proof,**dimensions,'scope_header':scope_proof.get('scope_header',''),
                                  'scope_provenance':scope_proof.get('provenance',''),
                                  'source_provenance':source_proof.get('provenance','')}
        for key in ('scope_proof','scope_definition_id','scope_definition_label','scope_definition_basis',
                    'scope_definition_source','scope_declaration_resource_id','scope_declaration_sha256','source_table_reference',
                    'conditional_verification','scope_declaration_draft_sha256','scope_declaration_target_sha256'):
            if key in scope_proof:result.source_dimensions[key]=scope_proof[key]
        if result.status=='abstained':
            result.blockers=self._all_blockers(claim,result)
        if not result.rule_ids:
            result.rule_ids = [{"E1": "E1-value", "E2": "E2-period", "E3": "E3-caliber",
                                "E4": "E4-unit", "E5": "E5-yoy", "E6": "E6-citation",
                                "E7": "E7-comparison", "E8": "E8-missing", "E9": "E9-version"}.get(result.error_type, "E1-value")]
        if _hedge_kind(claim):
            result.advisories.append("A2")
        if claim.account == "NP_PARENT" and "净利润" in claim.text and not any(term in claim.text for term in ("归母", "归属于", "母公司所有者", "挂牌公司股东", "上市公司股东")):
            if self.strict_caliber and result.status == "verified":
                result.status, result.error_type = "violated", "E3"
                result.rule_ids = ['E3-caliber']
                result.reason = "净利润与归母净利润口径用语不同；严格模式要求明确归母"
                result.correction = {"correct_value": result.display_value or result.expected or "", "source": ", ".join(result.evidence), "calculation": result.formula or "原披露值", "basis": "E3：净利润与归母净利润区分", "replacement_text": claim.text.replace("净利润", "归母净利润", 1)}
            else:
                result.advisories.append("A3")
        if result.abstention_code == "X4" and result.reason and "冲突" in result.reason:
            result.advisories.append("A4")
        from .scope import source_scope_dimensions
        scope = source_scope_dimensions(claim)
        if scope.get('provenance') == 'user_supplied_scope':
            result.advisories.append('A3')
            result.basis.append('报表口径来自用户补充澄清，非原文明示；资源：' + scope['scope_declaration_resource_id'] +
                                '；SHA-256：' + scope['scope_declaration_sha256'])
        result.advisories = list(dict.fromkeys(result.advisories))
        return result

    def _all_blockers(self,claim,result):
        """Independent source guards and operand lookups, without creating verdicts."""
        blockers=list(result.blockers)
        def add(code,kind,reason,**details):
            item={'code':code,'kind':kind,'reason':reason,**details}
            if item not in blockers: blockers.append(item)
        add(result.abstention_code or 'X1','primary',result.reason or '无法核查')
        if claim.parse_status!='parsed':add('X6','source_parse','原文结构尚未完整解析')
        if claim.binding_status in {'unknown','unresolved','disagreed','pending'}:add('X3','binding','语义绑定未知或双盲绑定不一致')
        from .source_semantics import unresolved_source_definition_issue
        source_failures=[]
        source_strict=(claim.table_context.get('source_scope_required') is True
                       or isinstance(claim.table_context.get('source_dimensions'),dict))
        for kind, issue in [('source_scope',_source_scope_issue(claim,strict=source_strict,check_binding=False)),
                            ('definition',unresolved_source_definition_issue(claim)),('source_semantics',_source_semantic_issue(claim))]:
            if issue:
                add(issue[0],kind,issue[1]);source_failures.append(issue)
        from .source_semantics import source_binding_issue
        binding_issue=source_binding_issue(claim,strict=source_strict)
        if binding_issue:
            add(binding_issue[0],'source_binding',binding_issue[1]);source_failures.append(binding_issue)
        spec=metric_spec(claim.account,claim_dimensions(claim)['definition_id'])
        if claim.account in METRIC_SPECS and spec is None:add('X2','definition','指标缺少来源明确的登记方法',account=claim.account)
        scope='母公司' if '母公司' in claim.caliber_hint else '合并'
        nr='扣非' if claim.account=='NP_PARENT_ADJ' or '扣非' in claim.caliber_hint else '未扣非'
        # A diagnostic never bypasses a failed identity, source or unit guard.
        # Those failures are reported together; dependent factual queries are
        # explicitly pending rather than executed with a guessed dimension.
        if (source_failures or claim.parse_status!='parsed' or result.abstention_code in {'X3','X6'}
                or claim.binding_status in {'unknown','unresolved','disagreed','pending'}):
            add('X3','fact_lookup','源维度或语义前置验证未通过，依赖事实查询未执行',lookup_status='not_executed')
            return blockers
        if claim.company and claim.account not in {'','UNKNOWN'} and claim.period and self.report_date:
            if spec:
                from .financial_derivations import derive_registered_metric
                outcome=self._lookup(claim,scope,nr)
                if outcome.status=='missing':
                    derived=derive_registered_metric(self.facts,claim.company,claim.account,claim.period,scope,nr,self.report_date,
                                  min_confidence=self.confidence_threshold,source_claim=claim,**claim_dimensions(claim))
                    for item in derived.blockers:
                        if item not in blockers:blockers.append(item)
                elif not outcome.fact:add(outcome.abstention_code or 'X1','fact_lookup',outcome.reason,lookup_status=outcome.status)
            else:
                outcome=self._lookup(claim,scope,nr)
                if not outcome.fact:add(outcome.abstention_code or 'X1','fact_lookup',outcome.reason,account=claim.account,period=claim.period,lookup_status=outcome.status,evidence=list(outcome.evidence))
        return blockers

    def _quoted_table_value(self, claim, reference):
        """Check the quotation, without assuming group scope or deriving data."""
        from .source_table_reference import query_quoted_table
        outcome = query_quoted_table(self.facts, claim, self.report_date, self.confidence_threshold)
        if not outcome.fact:
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8',
                abstention_code=outcome.abstention_code or 'X1', reason=outcome.reason,
                evidence=list(outcome.evidence), basis=['明确引用的同源表身份核查；不推断合并或母公司'])
        fact = outcome.fact
        percent = fact.display_unit in {'%','％'}
        if (percent and claim.unit not in {'%','％'}
                or not percent and claim.unit not in {'元','万元','亿元','百万元'}):
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8',
                abstention_code='X3', reason='引用表单元格与原文单位量纲不一致')
        try:
            observed = Decimal(claim.candidate_value)
            interval = self._claim_interval(claim, observed)
        except (InvalidOperation, ValueError, TypeError):
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8',
                abstention_code='X6', reason='引用表原文数值无法确定性解析')
        expected_interval = fact_interval(fact)
        compatible = intervals_compatible(interval, expected_interval)
        result = VerificationResult(claim.claim_id, 'verified' if compatible else 'violated',
            error_type=None if compatible else 'E1', observed=str(observed), expected=str(fact.value),
            evidence=list(dict.fromkeys([*fact.evidence, *reference['source_table_evidence']])),
            operands=[fact.to_dict()], formula='所引官方披露表单元格（按各自披露精度比较）',
            basis=['E1-value：单位换算与披露区间比较',
                   '来源由原文披露引用、发行人、期间、表身份及列类型确定，未按金额选择',
                   '本结论核实该表的转述；未证明该表为合并或母公司口径'],
            correction=None if compatible else self._correction(claim, fact.value, [fact], '所引披露表单元格'))
        result = self._interval_result(result, claim, interval, expected_interval, [fact], 'E1-value')
        result.verification_basis = 'explicit_disclosed_table_comparison'
        if reference.get('caption_semantics_conflict'):
            result.advisories.append('A4')
            result.basis.append('原表为对外采购，研报标题称共建；金额转述相容不代表标题定义正确，需修订标题')
        if claim.relation == 'yoy':
            result.basis.append('本结论核对原表已披露同比列；未宣称基期操作数齐备或同比已独立重算')
        if reference.get('quotation_kind') == 'business_mode_narrative' and claim.account == 'REVENUE_SHARE':
            result.advisories.append('A4')
            result.basis.append('原文仅称占比，未独立明示分母；本结论只核对所引表中该业务线总收入分母的已披露比例，不能称原文分母或占比公式已独立核实')
        if (reference.get('quotation_kind') in {'customer_type_chart', 'customer_type_narrative'}
                and reference.get('draft_share_denominator_status') == 'not_explicit_source_defined_quotation_only'):
            result.advisories.append('A4')
            result.basis.append('原文未独立明示该客户类型比例的分母；本结论核对所引披露表的超算云服务收入比例，不能将其解释为公司总营收占比或称占比公式已独立重算')
        if compatible:
            result.precision_note = '与明确引用表的披露精度相容；未推断其合并或母公司口径'
        return result

    def _verify(self, claim: AtomicClaim) -> VerificationResult:
        def abstain(reason: str, evidence=(), code="X1"):
            return VerificationResult(claim.claim_id, "abstained", error_type="E8", reason=reason,
                                      evidence=list(evidence), basis=["E8：给定材料优先、口径匹配"], diagnostics=[code], abstention_code=code)
        position, valid = _source_numeric_position(claim)
        currency = source_currency(claim.text, position) if valid and position is not None else source_currency(claim.text)
        if currency is None and position is None:
            # Legacy programmatic claims may lack numeric occurrence fields.
            # A source-written foreign amount still cannot consume CNY facts;
            # conflicting currency-bearing occurrences remain unknown.
            currencies = [source_currency(claim.text, match.start()) for match in VALUE_RE.finditer(claim.text)]
            currencies = [item for item in currencies if item]
            if currencies:
                codes = {item['currency'] for item in currencies}
                currency = {'currency':next(iter(codes)) if len(codes)==1 else 'UNKNOWN',
                            'source':'legacy_source_currency_scan',
                            'conflict':len(codes)>1 or any(item.get('conflict') for item in currencies)}
        currency = currency or claim.table_context.get('source_dimensions',{}).get('source_currency') or claim.table_context.get('source_currency')
        # Current facts have no currency/FX dimension. Two binders agreeing
        # cannot turn a foreign amount or foreign-currency growth rate into a
        # RMB result. This source guard also covers an unresolved bare “美元”.
        if currency and currency.get('currency') != 'CNY' and claim.unit not in {'股','万股','亿股','份','万份'}:
            if currency.get('currency') == 'UNKNOWN' or currency.get('conflict'):
                return abstain('原文币种未知或声明冲突；不能与人民币事实比较', code='X3')
            dims=claim_dimensions(claim)
            scope='母公司' if '母公司' in claim.caliber_hint else '合并'
            same_currency=self.facts._matches(claim.company,claim.account,claim.period,scope,'未扣非',**dims)
            if not same_currency and claim.account not in METRIC_SPECS:
                return abstain('缺少同币种财务事实；不使用人民币数值或猜测汇率换算',code='X1')
        range_issue = _source_amount_range_issue(claim)
        if range_issue:
            code, reason = range_issue
            return abstain(reason, code=code)
        if claim.parse_status != "parsed":
            return abstain("原文提及的科目、期间或单位维度不足，或尚未完成结构解析；不能确定可比较数值", code="X6")
        if claim.modality != "fact":
            return abstain("预测、观点或推论不属于历史数字事实判定", code="X2")
        from .source_semantics import unresolved_source_definition_issue
        semantic_definition_issue = unresolved_source_definition_issue(claim)
        if semantic_definition_issue:
            code, reason = semantic_definition_issue
            return abstain(reason, code=code)
        source_issue = _source_semantic_issue(claim)
        if source_issue:
            code, reason = source_issue
            return abstain(reason, code=code)
        from .source_semantics import source_binding_issue
        binding_issue = source_binding_issue(claim, strict=self.require_dual_binding)
        if binding_issue:
            code, reason = binding_issue
            return abstain(reason, code=code)
        scope_issue = _source_scope_issue(claim, strict=self.require_dual_binding)
        if scope_issue:
            code, reason = scope_issue
            return abstain(reason, code=code)
        if (claim.binding_status in {"unknown", "unresolved", "disagreed", "pending"}
                or '未知' in claim.caliber_hint
                or (self.require_dual_binding and claim.binding_status != "agreed")):
            return abstain("语义绑定未知或双盲绑定不一致", code="X3")
        from .source_quarter_quotation import threshold_operands
        threshold = threshold_operands(claim)
        if threshold:
            from .thresholds import evaluate_threshold_assertion
            from .rules import load_rule_cards
            source_assertion, claim_assertion, operand = threshold
            proof=claim.table_context['source_threshold_reference']
            if not self.report_date:
                return abstain('来源界限断言核查缺少回溯日期',operand['evidence'],'X5')
            if proof['cutoff']!=self.report_date or proof['publish_date']>self.report_date:
                return abstain('来源界限断言的冻结截止日与当前核查日期不一致',operand['evidence'],'X5')
            card = next((row for row in load_rule_cards() if row['id']=='SOURCE-threshold-entailment'), None)
            if card is None:
                return abstain('来源严格界限蕴含规则尚未登记',operand['evidence'],'X2')
            decision = evaluate_threshold_assertion(source_assertion,claim_assertion,rule_card=card).to_dict()
            return VerificationResult(claim.claim_id,
                'abstained' if decision['status']=='unknown' else decision['status'],
                error_type='E8' if decision['status']=='unknown' else 'E7' if decision['status']=='violated' else None,
                abstention_code='X4' if decision['status']=='unknown' else None,
                observed=claim.numeric_text,expected=operand['source_quote'],display_value=operand['source_quote'],
                formula='来源严格界限集合为原文断言集合的子集，或二者互斥；部分重叠不足以判定',
                evidence=operand['evidence'],operands=[operand],basis=[card['basis']],
                rule_ids=[card['id']],claim_interval=claim_assertion.to_dict(),expected_interval=source_assertion.to_dict(),
                verification_basis='source_threshold_assertion_entailment',
                precision_note='核实官方披露的严格下界断言，不代表比例等于下界，也不独立核实精确比例',
                reason='官方严格下界蕴含原文断言' if decision['status']=='verified' else
                       '官方严格下界与原文断言互斥' if decision['status']=='violated' else
                       '来源只披露严格下界，不能确认更强断言或精确比例')
        from .source_table_reference import validated_table_reference
        reference = validated_table_reference(claim)
        if reference and '来源表' in claim.caliber_hint:
            return self._quoted_table_value(claim, reference)
        if not claim.company or not claim.account or claim.account == "UNKNOWN" or not claim.period:
            return abstain("无法确认公司、科目、期间或分部/比例定义；需要补充匹配材料", code="X3")
        # A rate must never be compared with the underlying currency amount.
        # This check is applied after semantic binding, before any fact query
        # or correction formatting, so relabelled percentages cannot produce
        # absurd whole-company amounts rendered with a percent sign.
        percent_unit = claim.unit in {"%", "％", "百分点"}
        if claim.candidate_value is not None:
            if claim.relation == 'pp_difference' and (claim.unit != '百分点' or claim.account not in RATIO_ACCOUNTS):
                return abstain('百分点差必须使用百分点单位和已授权比率科目', code='X3')
            if claim.relation in {"yoy", "qoq",'cagr'} and not percent_unit:
                return abstain("同比/环比陈述量纲不是百分比，不能与增长率公式比较", code="X3")
            if claim.relation == "value" and ((claim.account in AMOUNT_ACCOUNTS and percent_unit)
                                               or (claim.account in RATIO_ACCOUNTS and claim.account not in TURNOVER_METRICS|{'CURRENT_RATIO','QUICK_RATIO'} and not percent_unit)
                                               or (claim.account in PER_SHARE_ACCOUNTS and claim.unit != '元')
                                               or (claim.account in SHARE_ACCOUNTS and claim.unit not in {'股','万股','亿股'})):
                return abstain("陈述单位与科目量纲不一致；金额、增长率与绝对比率必须分别定义", code="X3")
        scope = "母公司" if "母公司" in claim.caliber_hint else "合并"
        nr = "扣非" if claim.account == "NP_PARENT_ADJ" or "扣非" in claim.caliber_hint else "未扣非"
        observed = None
        if claim.candidate_value is not None:
            try:
                observed = Decimal(str(claim.candidate_value))
            except (InvalidOperation, ValueError):
                return abstain("陈述数值不是有效Decimal", code="X6")
            if not observed.is_finite():
                return abstain("陈述数值必须为有限数值", code="X6")
        versions = self.facts.versions(claim.company, claim.account, claim.period, scope, nr,**claim_dimensions(claim))
        if not self.report_date and len({(f.version, f.publish_date, f.value) for f in versions}) > 1:
            return abstain("版本敏感的回溯核查缺少研报日期", [e for f in versions for e in f.evidence], "X5")
        try:
            observed_interval = self._claim_interval(claim, observed) if observed is not None else None
        except ValueError as exc:
            return abstain(str(exc), code='X2' if _hedge_kind(claim) else 'X6')
        strategy = "pinned" if claim.declared_source else claim.query_strategy
        if claim.relation == 'pp_difference':
            return self._percentage_point_difference(claim, scope, nr, observed, observed_interval, strategy)
        outcome = self._lookup(claim, scope, nr, source=claim.declared_source, strategy=strategy)
        # Neither another disclosure nor an authorised calculation may erase
        # a same-version conflict, rejected confidence, or unknown source date.
        if outcome.abstention_code == 'X4':
            return abstain(outcome.reason, outcome.evidence, 'X4')
        current = outcome.fact
        if outcome.status=='missing' and claim.account in METRIC_SPECS and claim.relation in {'yoy','qoq','cagr'}:
            return self._metric_rate_comparison(claim,scope,nr,observed,observed_interval,strategy)
        if (current is None and outcome.status == 'missing' and claim.relation in {'value','ratio','share'}
                and (claim.account in METRIC_SPECS or claim.account == 'EPS' or (claim.account in {'REVENUE', 'COST', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO', 'CFI', 'CFF'}
                                               and claim.period[4:] in {'Q2', 'Q3', 'Q4'}))):
            from .financial_derivations import derive_metric
            derived = derive_metric(self.facts, claim.company, claim.account, claim.period, scope, nr,
                                    self.report_date, direct_outcome=outcome, strategy=strategy,
                                    source_doc=claim.declared_source, min_confidence=self.confidence_threshold,
                                    basic_eps_definition='基本每股收益' in claim.text,source_claim=claim,**claim_dimensions(claim))
            return self._derived_result(claim, observed, observed_interval, derived)
        if current is None and claim.declared_source and outcome.status == 'missing':
            fallback = self._lookup(claim, scope, nr)
            if fallback.abstention_code == 'X4':
                return abstain(fallback.reason, fallback.evidence, 'X4')
            current = fallback.fact
        if current is None and claim.account in RATIO_ACCOUNTS and claim.relation == "value" and outcome.status == 'missing':
            return self._ratio(claim, scope, observed)
        if current is None:
            # Equal amounts across periods are not proof of a period mistake.
            # Only an explicit, unique source-cell reference can establish E2.
            candidates = [f for f in self.facts.facts if f.company == claim.company and f.account == claim.account and f.scope == scope and f.nr == nr and f.period != claim.period and self._available(f) and self._same_claim_dimension(claim,f)]
            declared = (claim.declared_source or "").removeprefix("evidence:")
            matches = [f for f in candidates if declared and declared in f.evidence]
            same_reference = [f for f in self.facts.facts if f.company == claim.company and declared and declared in f.evidence and self._available(f) and self._same_claim_dimension(claim,f)]
            if len({f.period for f in same_reference}) == 1 and matches:
                fact = min(matches, key=lambda f: f.fact_id)
                return VerificationResult(claim.claim_id, "violated", error_type="E2", observed=str(observed) if observed is not None else None,
                    reason=f"所引数值/证据属于 {fact.period}，原文声明为 {claim.period}", evidence=list(fact.evidence), basis=["E2：有效期间匹配"],
                    correction={"correct_value": fact.period, "source": ", ".join(fact.evidence), "calculation": "期间与原披露列标题匹配", "basis": "E2：期间匹配", "replacement_text": f"{fact.company}公司{_period_label(fact.period)}{fact.scope}口径{ACCOUNT_NAMES.get(fact.account, fact.label or fact.account)}为{display_amount(fact.value, claim.unit or '元', claim.display_decimals or 0)}。"})
            result = abstain(outcome.reason or "缺少匹配事实", outcome.evidence, outcome.abstention_code or 'X1')
            if observed_interval:
                result.diagnostic_hypotheses = self._numeric_hypotheses(claim, observed_interval, scope, nr)
            return result
        evidence = list(current.evidence)
        if claim.declared_source and _asserts_latest_source(claim):
            latest_outcome = self._lookup(claim, scope, nr)
            if latest_outcome.abstention_code == 'X4':
                return abstain(latest_outcome.reason, latest_outcome.evidence, 'X4')
            latest = latest_outcome.fact
            if latest and (latest.version, latest.publish_date, latest.source_doc) != (
                    current.version, current.publish_date, current.source_doc):
                return VerificationResult(claim.claim_id, 'violated', error_type='E9',
                    observed=str(observed) if observed is not None else None, expected=str(latest.value),
                    evidence=list(dict.fromkeys([*current.evidence, *latest.evidence])),
                    operands=[latest.to_dict(), current.to_dict()],
                    reason='原文明示最新或调整后版本，但声明出处属于截止日前已被替代的版本',
                    basis=['E9-version：原文明示版本与登记披露来源冲突'],
                    correction=self._correction(claim, latest.value, [latest], '采用截至研报日最新的合法披露来源'))
        if claim.relation in {"gt", "lt", "ge", "le", "eq"}:
            base_outcome = self._base_outcome(claim, current, scope, nr)
            base = base_outcome.fact
            if base is None:
                return abstain(base_outcome.reason or "缺少比较基期事实", [*evidence, *base_outcome.evidence], base_outcome.abstention_code or 'X1')
            left_interval, right_interval = fact_interval(current), fact_interval(base)
            ok = compare_disclosure_intervals(left_interval, right_interval, claim.relation)
            if ok is None:
                return abstain('披露精度区间重叠，无法独立确认原文比较关系', [*evidence, *base.evidence], 'X4')
            result = VerificationResult(claim.claim_id, "verified" if ok else "violated", error_type=None if ok else "E7", observed=str(current.value), expected=str(base.value),
                formula=f"{current.fact_id} {claim.relation} {base.fact_id}", evidence=[*evidence, *base.evidence], basis=["E7：同口径比较关系"],
                operands=[current.to_dict(), base.to_dict()], reason=None if ok else "原文比较方向与冻结事实相反",
                correction=None if ok else self._correction(claim, current.value, [current, base], "比较大小并修正方向", replacement=f"{claim.company}公司{_period_label(claim.period)}{scope}口径{ACCOUNT_NAMES.get(claim.account, claim.account)}{'高于' if current.value > base.value else '低于' if current.value < base.value else '等于'}{_period_label(base.period)}。"))
            result.claim_interval, result.expected_interval = left_interval.to_dict(), right_interval.to_dict()
            result.source_precision = _source_precision([current, base])
            result.verification_basis = 'disclosure_interval_ordering'
            result.rule_ids = ['E7-comparison']
            return result
        if observed is None:
            return abstain("陈述没有可比较数值", evidence, "X6")
        if claim.declared_source:
            declared = claim.declared_source.removeprefix("evidence:")
            if declared not in current.evidence and declared not in {current.source_doc, current.source_path}:
                cited = [f for f in self.facts.facts if f.company == claim.company and declared in f.evidence and self._available(f) and self._same_claim_dimension(claim,f)]
                periods = {fact.period for fact in cited}
                other_period = next((f for f in sorted(cited, key=lambda fact: fact.fact_id)
                    if f.account == claim.account and f.scope == scope and f.nr == nr and f.period != claim.period), None)
                if other_period and len(periods) == 1:
                    return VerificationResult(claim.claim_id, "violated", error_type="E2", observed=str(observed), expected=str(current.value), evidence=[*evidence, *other_period.evidence],
                        reason=f"所引证据属于{other_period.period}，原文声明为{claim.period}", basis=["E2：引用期间与陈述期间一致"], correction=self._correction(claim, current.value, [current], "使用当期正确引用与披露值"))
                calibers = {(f.account, f.scope, f.nr) for f in cited if f.period == claim.period}
                if len(calibers) == 1:
                    actual_account, actual_scope, actual_nr = next(iter(calibers))
                    family = {claim.account} | ({'NP_PARENT', 'NP_PARENT_ADJ'} if claim.account in {'NP_PARENT', 'NP_PARENT_ADJ'} else set())
                    if actual_account in family and (actual_scope != scope or actual_nr != nr or actual_account != claim.account):
                        witness = min((f for f in cited if f.period == claim.period), key=lambda fact: fact.fact_id)
                        return VerificationResult(claim.claim_id, 'violated', error_type='E3', observed=str(observed), expected=str(current.value),
                            evidence=list(dict.fromkeys([*evidence, *witness.evidence])), operands=[current.to_dict(), witness.to_dict()],
                            reason='原文声明的单元格出处明确属于另一报表口径，而非金额巧合推断',
                            basis=['E3-caliber：声明单元格的科目与报表口径证明'],
                            correction=self._correction(claim, current.value, [current], '采用匹配声明口径的原披露单元格'))
                return VerificationResult(claim.claim_id, "violated", error_type="E6", observed=str(observed), expected=str(current.value), evidence=evidence,
                    reason="原文声明的来源不能支持本条事实", basis=["E6：引用必须对应冻结事实的证据"], correction=self._correction(claim, current.value, [current], "原始披露值与引用匹配"))
        if claim.relation == "value":
            expected = current.value
            expected_interval = fact_interval(current)
            range_interval=_source_amount_range(claim)
            if range_interval is not None:
                containment=execute_rule('FIN-range',{'lower':range_interval.lower,'upper':range_interval.upper,
                                    'actual_lower':expected_interval.lower,'actual_upper':expected_interval.upper})
                overlap=range_interval.intersects(expected_interval)
                if not containment and overlap:
                    return abstain('事实披露区间仅部分落入原文范围，不能独立确认整个范围断言',evidence,'X4')
                correction=None if containment else self._correction(claim,current.value,[current],'原报告披露值')
                if correction:
                    _,range_start,range_end=_source_amount_range(claim,with_span=True)
                    correction['replacement_text']=claim.text[:range_start]+correction['replacement_display']+claim.text[range_end:]
                result=VerificationResult(claim.claim_id,'verified' if containment else 'violated',
                        error_type=None if containment else 'E1',observed=str(range_interval.to_dict()),expected=str(current.value),
                        evidence=evidence,operands=[current.to_dict()],formula='来源披露区间包含于原文明示范围',
                        basis=['FIN-range：范围包含、分离与部分重叠三分判定'],
                        correction=correction)
                return self._interval_result(result,claim,range_interval,expected_interval,[current],'FIN-range')
            ok = intervals_compatible(observed_interval, expected_interval)
            error = None if ok else "E1"
            reason = None
            supporting = [current]
            hypotheses = []
            if not ok:
                hypotheses = self._numeric_hypotheses(claim, observed_interval, scope, nr, current)
                # A numerically compatible alternative is an explanation to
                # inspect, never proof of the claimed source/caliber/version.
                if expected and claim.unit and claim.account in AMOUNT_ACCOUNTS:
                    currency_scales = [unit_multiplier(u) for u in ('元', '万元', '百万元', '亿元')]
                    unit_ratios = sorted({a / b for a in currency_scales for b in currency_scales if a != b})
                    for multiplier in unit_ratios:
                        if observed_interval.intersects(expected_interval.scaled(multiplier)):
                            error, reason = "E4", "数值差异与元/万元/百万元/亿元倍率一致；请修正单位"
                            break
            result = VerificationResult(claim.claim_id, "verified" if ok else "violated", error_type=error, observed=str(observed), expected=str(expected),
                display_value=self._display(claim, expected), tolerance=str((expected_interval.upper-expected_interval.lower + observed_interval.upper-observed_interval.lower)/2), evidence=[e for f in supporting for e in f.evidence],
                formula=f"披露值 {current.value} / 单位倍率 {unit_multiplier(claim.unit)}", operands=[current.to_dict()], basis=[f"{error or 'E1'}：原始披露、口径与显示精度"], reason=reason,
                correction=None if ok else self._correction(claim, expected, supporting, f"{current.value} / {unit_multiplier(claim.unit)}"))
            result.diagnostic_hypotheses = hypotheses
            if claim.declared_source:
                latest = self._lookup(claim,scope,nr).fact
                if latest and (latest.version, latest.publish_date, latest.source_doc) != (current.version, current.publish_date, current.source_doc):
                    result.advisories.append("A1")
            return self._interval_result(result, claim, observed_interval, expected_interval, [current], 'E4-unit' if error == 'E4' else 'E1-value')
        if claim.relation=='cagr':
            if not (re.fullmatch(r'20\d{2}Y',claim.period or '') and re.fullmatch(r'20\d{2}Y',claim.base_period or '')):
                return abstain('CAGR缺少明确的同类年度首尾期间，禁止推算任意年化期数',evidence,'X2')
            years=int(claim.period[:4])-int(claim.base_period[:4])
            if not 1<=years<=100:
                return abstain('CAGR首尾期间整数差须在登记的一至一百年范围内',evidence,'X2')
            base_outcome=self._base_outcome(claim,current,scope,nr)
            if not base_outcome.fact:
                return abstain(base_outcome.reason,[*evidence,*base_outcome.evidence],base_outcome.abstention_code or 'X1')
            base=base_outcome.fact
            # FIN-cagr's locked source clause authorizes a same-report
            # comparison. Numerically compatible endpoints in unrelated
            # reports do not establish a comparable historical series.
            # Adjusted and current-period columns in the same disclosure
            # remain valid; ``version`` is a column label, not report identity.
            current_source=current.source_doc or current.source_path
            base_source=base.source_doc or base.source_path
            if (current_source != base_source or
                    current.version_group and base.version_group and
                    current.version_group != base.version_group):
                return abstain('FIN-cagr要求首尾值来自同一报告版本；不同披露报告或版本组不能拼接，须补充同源历史序列或另行签发跨报告方法',
                               [*evidence,*base.evidence],'X2')
            values={'current':current.value,'base':base.value,'years':Decimal(years)}
            intervals={'current':fact_interval(current),'base':fact_interval(base),'years':DecimalInterval(Decimal(years),Decimal(years),True,True)}
            expected=execute_rule('FIN-cagr',values); expected_interval=execute_rule_interval('FIN-cagr',intervals)
            if expected is None or expected_interval is None:
                return abstain('CAGR首尾披露区间非正或整数期数前提不成立',[*evidence,*base.evidence],'X2')
            ok=intervals_compatible(observed_interval,expected_interval)
            formula=f'({current.value}/{base.value})的{years}次整数根 - 1'
            result=VerificationResult(claim.claim_id,'verified' if ok else 'violated',error_type=None if ok else 'E5',observed=str(observed),expected=str(expected),display_value=self._display(claim,expected),formula=formula,
                     evidence=[*evidence,*base.evidence],operands=[current.to_dict(),base.to_dict()],basis=['FIN-cagr：整数根上下界，不使用浮点幂'],
                     correction=None if ok else self._correction(claim,expected,[current,base],formula))
            return self._interval_result(result,claim,observed_interval,expected_interval,[current,base],'FIN-cagr')
        if claim.relation not in {"yoy", "qoq"}:
            return abstain("陈述关系不在授权公式范围内", evidence, "X2")
        base_outcome = self._base_outcome(claim, current, scope, nr)
        base = base_outcome.fact
        if base is None:
            return abstain(base_outcome.reason or "缺少同比/环比同报告基期事实", [*evidence, *base_outcome.evidence], base_outcome.abstention_code or 'X1')
        if fact_interval(base).lower <= 0:
            return abstain("零基数或负基数不适用默认增长率公式", [*evidence, *base.evidence], "X2")
        rule_id = 'E5-yoy' if claim.relation == 'yoy' else 'FIN-qoq'
        expected = execute_rule(rule_id, {'current': current.value, 'base': base.value})
        expected_interval = growth_interval(fact_interval(current), fact_interval(base))
        ok = intervals_compatible(observed_interval, expected_interval)
        formula = f"({current.value} - {base.value}) / {base.value}"
        result = VerificationResult(claim.claim_id, "verified" if ok else "violated", error_type=None if ok else "E5", observed=str(observed), expected=str(expected),
            display_value=self._display(claim, expected), formula=formula, tolerance=str((expected_interval.upper-expected_interval.lower + observed_interval.upper-observed_interval.lower)/2), operands=[current.to_dict(), base.to_dict()],
            evidence=[*evidence, *base.evidence], basis=["E5：同报告同口径同比" if claim.relation == "yoy" else "E5：相邻单季环比"],
            correction=None if ok else self._correction(claim, expected, [current, base], formula))
        return self._interval_result(result, claim, observed_interval, expected_interval, [current, base], rule_id)

    def _derived_result(self, claim, observed, observed_interval, derived):
        if derived.value is None or observed is None:
            code = derived.abstention_code or 'X6'
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8', reason=derived.reason or '没有可比较陈述数值',
                evidence=derived.evidence, operands=[fact.to_dict() for fact in derived.operands],
                formula=derived.formula, abstention_code=code, diagnostics=[code],blockers=derived.blockers,
                rule_ids=derived.rule_ids,calculation_steps=derived.calculation_steps)
        range_interval=_source_amount_range(claim)
        if range_interval:
            contained=execute_rule('FIN-range',{'lower':range_interval.lower,'upper':range_interval.upper,
                         'actual_lower':derived.interval.lower,'actual_upper':derived.interval.upper})
            if not contained and range_interval.intersects(derived.interval):
                return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X4',
                        reason='授权派生披露区间仅部分落入原文范围，不能独立确认范围断言',
                        formula=derived.formula,evidence=derived.evidence,operands=[x.to_dict() for x in derived.operands],
                        claim_interval=range_interval.to_dict(),expected_interval=derived.interval.to_dict(),rule_ids=[*derived.rule_ids,'FIN-range'],
                        calculation_steps=derived.calculation_steps)
            observed_interval=range_interval
            ok=contained
        else:
            ok = intervals_compatible(observed_interval, derived.interval)
        result = VerificationResult(claim.claim_id, 'verified' if ok else 'violated', error_type=None if ok else 'E5',
            observed=str(observed), expected=str(derived.value), display_value=self._display(claim, derived.value),
            formula=derived.formula, evidence=derived.evidence, operands=[fact.to_dict() for fact in derived.operands],
            correction=None if ok else self._correction(claim, derived.value, derived.operands, derived.formula))
        result = self._interval_result(result, claim, observed_interval, derived.interval, derived.operands, derived.rule['id'])
        result.rule_ids=derived.rule_ids
        result.calculation_steps=derived.calculation_steps
        if range_interval:
            result.rule_ids.append('FIN-range')
            if result.correction and result.correction.get('precision_basis')!='propagated_interval_range':
                _,begin,end=_source_amount_range(claim,with_span=True)
                result.correction['replacement_text']=claim.text[:begin]+result.correction['replacement_display']+claim.text[end:]
        result.verification_basis = 'authorized_formula_interval_propagation'
        result.basis = [derived.rule['id']+'；版本 '+derived.rule['version']+'；SHA-256 '+derived.rule['version_hash'],
                        '量纲：'+derived.rule['dimension'], *derived.rule['preconditions']]
        for step in derived.calculation_steps:
            if step['rule_id']!=derived.rule['id']:
                result.basis.append(step['rule_id']+'；版本 '+step['rule_version']+'；SHA-256 '+step['rule_version_hash'])
        return result

    def _registered_rate(self,claim,scope,nr,period,source=None,strategy=None):
        outcome=self._lookup(claim,scope,nr,period=period,source=source,strategy=strategy)
        if outcome.fact:
            fact=outcome.fact
            return {'value':fact.value,'interval':fact_interval(fact),'facts':[fact],
                    'document':fact.source_doc or fact.source_path,'rate_formula':'原报告披露值'},None
        if outcome.status!='missing':
            return None,VerificationResult(claim.claim_id,'abstained',error_type='E8',reason=outcome.reason,
                       abstention_code=outcome.abstention_code,evidence=list(outcome.evidence))
        from .financial_derivations import derive_metric
        derived=derive_metric(self.facts,claim.company,claim.account,period,scope,nr,self.report_date,
                     strategy=strategy or 'latest_as_of',source_doc=source,min_confidence=self.confidence_threshold,
                     source_claim=claim,**claim_dimensions(claim))
        if derived.value is None:
            return None,VerificationResult(claim.claim_id,'abstained',error_type='E8',reason=derived.reason,
                     abstention_code=derived.abstention_code,evidence=derived.evidence,blockers=derived.blockers,
                     operands=[x.to_dict() for x in derived.operands],formula=derived.formula,
                     rule_ids=[derived.rule['id']] if derived.rule else [])
        return {'value':derived.value,'interval':derived.interval,'facts':derived.operands,
                'document':derived.operands[0].source_doc or derived.operands[0].source_path,
                'rate_formula':derived.rule['id']},None

    def _metric_rate_comparison(self,claim,scope,nr,observed,observed_interval,strategy):
        current,issue=self._registered_rate(claim,scope,nr,claim.period,claim.declared_source,strategy)
        if issue:return issue
        base,issue=self._registered_rate(claim,scope,nr,claim.base_period or '',current['document'],'same_report')
        if issue:
            issue.evidence=list(dict.fromkeys([*issue.evidence,*[e for x in current['facts'] for e in x.evidence]]))
            return issue
        rule='FIN-cagr' if claim.relation=='cagr' else 'E5-yoy' if claim.relation=='yoy' else 'FIN-qoq'
        values={'current':current['value'],'base':base['value']}
        intervals={'current':current['interval'],'base':base['interval']}
        if claim.relation=='cagr':
            source_facts=[*current['facts'],*base['facts']]
            documents={fact.source_doc or fact.source_path for fact in source_facts}
            version_groups={fact.version_group for fact in source_facts if fact.version_group}
            if len(documents)>1 or len(version_groups)>1:
                return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X2',
                    reason='FIN-cagr要求首尾指标及其操作数来自同一报告版本；不同披露报告或版本组不能拼接',
                    evidence=list(dict.fromkeys(e for fact in source_facts for e in fact.evidence)),
                    rule_ids=['FIN-cagr'])
            if not (re.fullmatch(r'20\d{2}Y',claim.period or '') and re.fullmatch(r'20\d{2}Y',claim.base_period or '')):
                return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X2',reason='CAGR首尾期间没有明确整数年差')
            years=Decimal(int(claim.period[:4])-int(claim.base_period[:4]))
            values['years']=years;intervals['years']=DecimalInterval(years,years,True,True)
        expected=execute_rule(rule,values);expected_interval=execute_rule_interval(rule,intervals)
        facts=list({x.fact_id:x for x in [*current['facts'],*base['facts']]}.values())
        if expected is None or expected_interval is None:
            return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X2',
                  reason='比率增长授权公式前提不成立；基数披露区间须为正',evidence=list(dict.fromkeys(e for x in facts for e in x.evidence)),
                  operands=[x.to_dict() for x in facts],rule_ids=[rule])
        if observed is None or observed_interval is None:
            return VerificationResult(claim.claim_id,'abstained',error_type='E8',abstention_code='X6',reason='比率增长没有可比较的原文数值')
        ok=intervals_compatible(observed_interval,expected_interval)
        formula=f'{rule}({current["rate_formula"]}: {current["value"]}, {base["rate_formula"]}: {base["value"]})'
        result=VerificationResult(claim.claim_id,'verified' if ok else 'violated',error_type=None if ok else 'E5',
                  observed=str(observed),expected=str(expected),display_value=self._display(claim,expected),formula=formula,
                  evidence=list(dict.fromkeys(e for x in facts for e in x.evidence)),operands=[x.to_dict() for x in facts],
                  correction=None if ok else self._correction(claim,expected,facts,formula))
        result=self._interval_result(result,claim,observed_interval,expected_interval,facts,rule)
        result.rule_ids+=list(dict.fromkeys(x for x in (current['rate_formula'],base['rate_formula']) if x!='原报告披露值'))
        return result

    def _percentage_point_difference(self, claim, scope, nr, observed, observed_interval, strategy):
        """Compare two dated same-report rates through FIN-pp-difference.

        A missing disclosed rate may use its already registered ratio formula;
        conflicts, low confidence or unknown dates always stop that fallback.
        Every operand remains an original fact, never a model-created value.
        """
        def rejected(reason, evidence=(), code='X1'):
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8', reason=reason,
                evidence=list(dict.fromkeys(evidence)), diagnostics=[code], abstention_code=code,
                basis=['FIN-pp-difference：同报告同口径比率差'])
        if observed is None or observed_interval is None:
            return rejected('陈述没有可比较的百分点数值', code='X6')
        position, valid = _source_numeric_position(claim)
        pp = percentage_point_dimensions(claim.text, position) if valid and position is not None else {}
        if not pp or any(pp[key] != (getattr(claim, key) or '') for key in ('account', 'period', 'relation', 'base_period')):
            return rejected('百分点关系及期间必须来自原文明示，不能由模型猜测或改写', code='X3')
        dependencies = {'GROSS_MARGIN': ('REVENUE', 'COST'), 'NET_MARGIN': ('NP', 'REVENUE'),
                        'NP_PARENT_MARGIN': ('NP_PARENT', 'REVENUE'),
                        'DEBT_RATIO': ('LIABILITIES', 'ASSETS')}
        ratio_rules = {'GROSS_MARGIN': 'FIN-gross-margin', 'NET_MARGIN': 'FIN-net-margin',
                       'NP_PARENT_MARGIN': 'FIN-parent-net-margin',
                       'DEBT_RATIO': 'FIN-debt-ratio'}
        def rate(period, source=None, lookup_strategy=None):
            if claim.account in METRIC_SPECS:
                return self._registered_rate(claim,scope,nr,period,source,lookup_strategy)
            outcome = self._lookup(claim, scope, nr, source=source, strategy=lookup_strategy, period=period)
            if outcome.fact is not None:
                fact = outcome.fact
                return {'value': fact.value, 'interval': fact_interval(fact), 'facts': [fact],
                        'document': fact.source_doc or fact.source_path, 'rate_formula': '原报告披露值'}, None
            if outcome.status != 'missing':
                return None, rejected(outcome.reason, outcome.evidence, outcome.abstention_code)
            keys = dependencies.get(claim.account)
            if keys is None:
                return None, rejected('缺少比率披露值；当前科目没有可用于百分点差的授权重算定义',
                                      outcome.evidence, 'X2')
            if not self.report_date and any(len({(f.version, f.publish_date, f.value)
                    for f in self.facts.versions(claim.company, key, period, scope)}) > 1 for key in keys):
                return None, rejected('版本敏感的比率重算缺少回溯日期', code='X5')
            first_outcome = self._lookup(claim, scope, '未扣非', account=keys[0], period=period,
                                         source=source, strategy=lookup_strategy)
            first = first_outcome.fact
            document = (first.source_doc or first.source_path) if first else None
            outcomes = [first_outcome] + [self._lookup(claim, scope, '未扣非', account=key, period=period,
                source=document, strategy='same_report' if document else 'latest_as_of') for key in keys[1:]]
            failures = [item for item in outcomes if item.fact is None]
            if failures:
                issue = next((item for item in failures if item.abstention_code == 'X4'), failures[0])
                return None, rejected(issue.reason, [e for item in outcomes for e in item.evidence], issue.abstention_code)
            facts = [item.fact for item in outcomes]
            operands = {key: fact.value for key, fact in zip(keys, facts)}
            intervals = {key: fact_interval(fact) for key, fact in zip(keys, facts)}
            rule_id = ratio_rules[claim.account]
            value, interval = execute_rule(rule_id, operands), execute_rule_interval(rule_id, intervals)
            if value is None or interval is None:
                return None, rejected('授权比率公式前提不成立，不能计算百分点差',
                                      [e for fact in facts for e in fact.evidence], 'X2')
            return {'value': value, 'interval': interval, 'facts': facts, 'document': document,
                    'rate_formula': rule_id}, None
        current, issue = rate(claim.period, claim.declared_source, strategy)
        if issue:
            return issue
        superseded = None
        if claim.declared_source and _asserts_latest_source(claim):
            latest, issue = rate(claim.period, None, 'latest_as_of')
            if issue:
                return issue
            original_versions = {(fact.version, fact.publish_date, fact.source_doc or fact.source_path)
                                 for fact in current['facts']}
            latest_versions = {(fact.version, fact.publish_date, fact.source_doc or fact.source_path)
                               for fact in latest['facts']}
            if original_versions != latest_versions:
                superseded, current = current, latest
        base, issue = rate(claim.base_period, current['document'] or None,
                           'same_report' if current['document'] else 'latest_as_of')
        if issue:
            issue.evidence = list(dict.fromkeys([*[e for fact in current['facts'] for e in fact.evidence], *issue.evidence]))
            return issue
        values = {'current': current['value'], 'base': base['value']}
        intervals = {'current': current['interval'], 'base': base['interval']}
        expected = execute_rule('FIN-pp-difference', values)
        expected_interval = execute_rule_interval('FIN-pp-difference', intervals)
        if expected is None or expected_interval is None:
            return rejected('登记的百分点差公式无法执行', code='X2')
        ok = intervals_compatible(observed_interval, expected_interval)
        facts = list({fact.fact_id: fact for fact in [*current['facts'], *base['facts'],
            *(superseded['facts'] if superseded else [])]}.values())
        formula = f"{current['value']} - {base['value']}"
        error = 'E9' if superseded else None if ok else 'E5'
        result = VerificationResult(claim.claim_id, 'violated' if error else 'verified',
            error_type=error, observed=str(observed), expected=str(expected),
            display_value=self._display(claim, expected), formula=formula,
            operands=[fact.to_dict() for fact in facts], evidence=list(dict.fromkeys(e for fact in facts for e in fact.evidence)),
            basis=['FIN-pp-difference：同报告同口径比率相减，百分点为比例乘以一百',
                   '当期依据：' + current['rate_formula'], '基期依据：' + base['rate_formula']],
            reason='原文明示最新或调整后版本，但百分点差声明出处属于已被替代的版本' if superseded
                else None if ok else '原文百分点差与冻结事实及授权公式不相容',
            correction=self._correction(claim, expected, facts, formula) if error else None)
        result = self._interval_result(result, claim, observed_interval, expected_interval, facts, 'FIN-pp-difference')
        result.rule_ids += list(dict.fromkeys(rule for rule in (current['rate_formula'], base['rate_formula'])
                                if rule != '原报告披露值'))
        if superseded:
            result.rule_ids.append('E9-version')
        elif claim.declared_source:
            latest_outcome = self._lookup(claim, scope, nr)
            latest = latest_outcome.fact
            if latest and (latest.version, latest.publish_date, latest.source_doc or latest.source_path) not in {
                    (fact.version, fact.publish_date, fact.source_doc or fact.source_path) for fact in current['facts']}:
                result.advisories.append('A1')
        return result

    def _numeric_hypotheses(self, claim, observed_interval, scope, nr, current=None):
        hypotheses = []
        family = {claim.account}
        if claim.account in {'NP_PARENT', 'NP_PARENT_ADJ'}:
            family |= {'NP_PARENT', 'NP_PARENT_ADJ'}
        for fact in sorted(self.facts.facts, key=lambda item: item.fact_id):
            if fact.company != claim.company or fact.account not in family or not self._available(fact) or not self._same_claim_dimension(claim,fact):
                continue
            if current and fact.fact_id == current.fact_id:
                continue
            try:
                compatible = observed_interval.intersects(fact_interval(fact))
            except ValueError:
                continue
            if not compatible:
                continue
            if fact.period != claim.period and fact.account == claim.account and fact.scope == scope and fact.nr == nr:
                type_ = 'E2'
            elif fact.period == claim.period and (fact.scope != scope or fact.nr != nr or fact.account != claim.account):
                type_ = 'E3'
            elif current and fact.period == claim.period and (fact.version, fact.publish_date, fact.source_doc) != (
                    current.version, current.publish_date, current.source_doc):
                type_ = 'E9'
            else:
                continue
            hypotheses.append({'error_type': type_, 'fact_id': fact.fact_id,
                               'evidence': list(fact.evidence),
                               'proof': 'numeric_interval_compatibility_only',
                               'reason': '金额区间相容仅为待查解释，不足以证明期间、口径或版本错配'})
        return hypotheses

    def _base_outcome(self, claim, current, scope, nr):
        # Legacy synthetic facts without a document remain usable offline;
        # production parsers always attach source_doc/source_path.
        source = current.source_doc or current.source_path
        outcome = self._lookup(claim, scope, nr, period=claim.base_period or '',
                               strategy='same_report' if source else 'latest_as_of', source=source or None)
        if claim.relation == 'cagr' and source and outcome.status == 'missing':
            return replace(outcome, reason='FIN-cagr要求首尾值来自同一报告版本；截至研报日缺少同报告基期值，禁止用另一报告补齐')
        return outcome

    def _base(self, claim, current, scope, nr):
        return self._base_outcome(claim, current, scope, nr).fact

    def _ratio(self, claim, scope, observed):
        dependencies = {"GROSS_MARGIN": ("REVENUE", "COST"), "NET_MARGIN": ("NP", "REVENUE"),
                        "NP_PARENT_MARGIN": ("NP_PARENT", "REVENUE"),
                        "DEBT_RATIO": ("LIABILITIES", "ASSETS")}
        keys = dependencies.get(claim.account)
        if not keys or observed is None:
            return VerificationResult(claim.claim_id, "abstained", error_type="E8", reason="缺少指标披露值或授权公式所需操作数；ROE需明确加权净资产口径", diagnostics=["X2"], abstention_code="X2")
        if not self.report_date and any(len({(f.version, f.publish_date, f.value) for f in self.facts.versions(claim.company,key,claim.period,scope)}) > 1 for key in keys):
            return VerificationResult(claim.claim_id, "abstained", error_type="E8", reason="版本敏感的财务指标缺少研报日期", diagnostics=["X5"], abstention_code="X5")
        first_outcome = self._lookup(claim, scope, '未扣非', account=keys[0], source=claim.declared_source)
        first = first_outcome.fact
        document = (first.source_doc or first.source_path) if first else None
        outcomes = [first_outcome] + [self._lookup(claim, scope, '未扣非', account=key,
                          strategy='same_report' if document else 'latest_as_of', source=document)
                          for key in keys[1:]]
        rejected = [outcome for outcome in outcomes if outcome.fact is None]
        if rejected:
            issue = next((outcome for outcome in rejected if outcome.abstention_code == 'X4'), rejected[0])
            return VerificationResult(claim.claim_id, "abstained", error_type="E8", reason=issue.reason,
                evidence=list(issue.evidence), diagnostics=[issue.abstention_code], abstention_code=issue.abstention_code)
        facts = [outcome.fact for outcome in outcomes]
        a, b = [f.value for f in facts]
        try:
            expected_interval = ratio_interval(claim.account, *[fact_interval(fact) for fact in facts])
            observed_interval = self._claim_interval(claim, observed)
        except ValueError as exc:
            return VerificationResult(claim.claim_id, 'abstained', error_type='E8', reason=str(exc),
                evidence=[e for fact in facts for e in fact.evidence], diagnostics=['X2'], abstention_code='X2')
        rule_id = {'GROSS_MARGIN': 'FIN-gross-margin', 'NET_MARGIN': 'FIN-net-margin',
                   'NP_PARENT_MARGIN': 'FIN-parent-net-margin',
                   'DEBT_RATIO': 'FIN-debt-ratio'}[claim.account]
        expected = execute_rule(rule_id, {key: fact.value for key, fact in zip(keys, facts)})
        formula = f"({a} - {b}) / {a}" if claim.account == "GROSS_MARGIN" else f"{a} / {b}"
        ok = intervals_compatible(observed_interval, expected_interval)
        result = VerificationResult(claim.claim_id, "verified" if ok else "violated", error_type=None if ok else "E5", observed=str(observed), expected=str(expected), display_value=self._display(claim, expected),
            formula=formula, tolerance=str((expected_interval.upper-expected_interval.lower + observed_interval.upper-observed_interval.lower)/2), operands=[f.to_dict() for f in facts], evidence=[e for f in facts for e in f.evidence], basis=[f"{rule_id}：财务指标授权公式与披露区间"],
            correction=None if ok else self._correction(claim, expected, facts, formula))
        return self._interval_result(result, claim, observed_interval, expected_interval, facts, rule_id)

    @staticmethod
    def _display(claim, value):
        precision = claim.display_decimals if claim.display_decimals is not None else 2
        if claim.unit == '百分点':
            return display_amount(value, '百分点', precision)
        return display_percent(value, precision) if claim.unit in {"%", "％", "百分点"} else display_amount(value, claim.unit or "元", precision)

    def _correction(self, claim, value, facts, formula, replacement=None):
        display = self._display(claim, value)
        replacement_display = display
        precision_basis = 'compatible_display_value'
        if claim.relation == 'value' and len(facts) == 1 and resolution(facts[0].display_unit, facts[0].decimals) > resolution(claim.unit, claim.display_decimals if claim.display_decimals is not None else 2):
            interval = fact_interval(facts[0])
            lower = self._display(claim, interval.lower)
            upper = self._display(claim, interval.upper)
            display = f'{lower}至{upper}（上界不含）'
            # The detail retains the support interval. A directly usable draft
            # quotes the actual printed source unit and precision instead of
            # inventing fine digits or two uncheckable range endpoints.
            source = facts[0]
            replacement_display = (display_percent(source.value, source.decimals)
                                   if source.display_unit in {'%', '％'} else
                                   display_amount(source.value, source.display_unit, source.decimals))
            precision_basis = 'source_disclosure_interval'
        scope = "母公司" if "母公司" in claim.caliber_hint else "合并"
        label = ACCOUNT_NAMES.get(claim.account, claim.account)
        prefix = f"{claim.company}公司{_period_label(claim.period)}{scope}口径{label}"
        relation = "同比增长" if claim.relation == "yoy" else "环比增长" if claim.relation == "qoq" else "为"
        if claim.relation == 'pp_difference':
            relation = f"较{_period_label(claim.base_period or '')}{'下降' if value < 0 else '上升'}"
            replacement_display = self._display(claim, value.copy_abs())
        position, valid = _source_numeric_position(claim)
        if claim.numeric_text and valid and position is not None:
            prefix_text = claim.text[:position]
            if claim.relation in {'yoy', 'qoq', 'pp_difference'}:
                direction = re.search(r'(增长|上升|增加|提升|提高|扩大|下降|下滑|减少|降低|下跌|回落|缩减|收窄)(\s*(?:约|近|了|达|为)?\s*)$', prefix_text)
                if direction:
                    word = '下降' if value < 0 else '上升' if claim.relation == 'pp_difference' else '增长'
                    prefix_text = prefix_text[:direction.start(1)] + word + prefix_text[direction.end(1):]
                    replacement_display = self._display(claim, value.copy_abs())
            text = prefix_text + replacement_display + claim.text[position+len(claim.numeric_text):]
        else:
            text = f"{prefix}{relation}{replacement_display}。"
        if claim.declared_source and facts:
            declared = claim.declared_source.removeprefix('evidence:')
            primary = facts[0]
            if declared not in {primary.source_doc, primary.source_path, *primary.evidence}:
                reference = primary.source_doc or next(iter(primary.evidence), '')
                if reference and declared in text:
                    text = text.replace(declared, reference, 1)
        return {"correct_value": display, "source": ", ".join(e for f in facts for e in f.evidence), "calculation": formula,
                "basis": "事实层、期间/口径/版本规则与原文显示精度", "replacement_text": replacement or text,
                "precision_basis": precision_basis, "replacement_display": replacement_display}
