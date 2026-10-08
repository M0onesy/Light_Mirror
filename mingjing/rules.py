"""Validate frozen rule metadata without external I/O or model calls."""
import hashlib
import json
import sys
import ast
from decimal import Decimal, InvalidOperation, localcontext
from datetime import date
from pathlib import Path
from .decimal_math import DecimalInterval

def rulepack_root():
    """Resolve the bundled resource location used by PyInstaller onefile."""
    if getattr(sys, 'frozen', False) and getattr(sys, '_MEIPASS', None):
        return Path(sys._MEIPASS) / 'rulepack'
    return Path(__file__).resolve().parents[1] / 'rulepack'

class RuleError(ValueError):
    pass


_REGISTERED_CONSTANTS = {"ZERO": "0", "ONE": "1", "HALF": "0.5", "HUNDRED": "100",
                         "TEN_THOUSAND": "10000", "HUNDRED_MILLION": "100000000",
                         "CASH_THRESHOLD": "0.5", "NRI_THRESHOLD": "0.30"}


def _dimension(text):
    dimensions = {"currency": (1, 0), "shares": (0, 1), "currency_per_share": (1, -1),
                  "ratio": (0, 0), "boolean": (0, 0), "identity": (0, 0)}
    if text not in dimensions:
        raise RuleError(f"未声明量纲: {text}")
    return dimensions[text]


def _compile(card):
    try:
        tree = ast.parse(card["formula"], mode="eval")
    except (SyntaxError, KeyError) as exc:
        raise RuleError("公式不是受限表达式") from exc
    for key, value in card.get("constants", {}).items():
        try:
            valid = key in _REGISTERED_CONSTANTS and Decimal(str(value.get('value'))) == Decimal(_REGISTERED_CONSTANTS[key]) and value.get('dimension') == 'ratio'
        except InvalidOperation:
            valid = False
        if not valid:
            raise RuleError("未登记常数或常数值被修改")
    if set(card.get('positive_inputs', [])) - set(card.get('inputs', {})):
        raise RuleError("公式前提引用了未声明输入")
    dimensions = {key: _dimension(value) for key, value in card.get("inputs", {}).items()}
    dimensions.update({key: _dimension(value["dimension"]) for key, value in card.get("constants", {}).items()})
    def dimension(node):
        if isinstance(node, ast.Name):
            if node.id not in dimensions:
                raise RuleError(f"未声明科目或常数: {node.id}")
            return dimensions[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return dimension(node.operand)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, right = dimension(node.left), dimension(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if left != right:
                    raise RuleError("加减操作数的量纲不同")
                return left
            sign = 1 if isinstance(node.op, ast.Mult) else -1
            return tuple(a + sign*b for a, b in zip(left, right))
        if isinstance(node, ast.Compare) and len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.Gt, ast.GtE, ast.Lt, ast.LtE)):
            if dimension(node.left) != dimension(node.comparators[0]):
                raise RuleError("比较操作数的量纲不同")
            return (0, 0)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            if not all(isinstance(value, (ast.Compare, ast.BoolOp)) and dimension(value) == (0, 0)
                       for value in node.values):
                raise RuleError("逻辑运算只允许已授权的比较表达式")
            return (0, 0)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "abs" and len(node.args) == 1 and not node.keywords:
            return dimension(node.args[0])
        if (card.get('id')=='FIN-cagr' and isinstance(node,ast.Call) and isinstance(node.func,ast.Name)
                and node.func.id=='cagr' and not node.keywords and len(node.args)==3
                and all(isinstance(x,ast.Name) for x in node.args)
                and [x.id for x in node.args]==['current','base','years']):
            if dimension(node.args[0])!=dimension(node.args[1]) or dimension(node.args[2])!=(0,0):
                raise RuleError('CAGR首尾量纲或整数期数量纲错误')
            return (0,0)
        raise RuleError("禁止自由常数、属性、下标、函数、幂或代码执行")
    if dimension(tree.body) != _dimension(card["dimension"]):
        raise RuleError("公式结果量纲与声明不一致")
    return tree.body


def evaluate_rule(card: dict, operands: dict) -> Decimal | bool | None:
    """Execute a frozen rule; callers cannot supply an expression."""
    node = _compile(card)
    if set(operands) - set(card["inputs"]):
        raise RuleError("操作数包含未授权科目")
    if set(card["inputs"]) - set(operands) or any(value is None for value in operands.values()):
        return None
    try:
        values = {key: Decimal(str(value)) for key, value in operands.items()}
        values.update({key: Decimal(str(item["value"])) for key, item in card.get("constants", {}).items()})
    except (InvalidOperation, ValueError) as exc:
        raise RuleError("操作数不是Decimal") from exc
    if any(not value.is_finite() for value in values.values()):
        raise RuleError("操作数必须为有限值")
    if any(values[key] not in {Decimal(0), Decimal(1)} for key, kind in card['inputs'].items()
           if kind == 'boolean'):
        raise RuleError("布尔输入必须为零或一")
    if any(values[key] <= 0 for key in card.get("positive_inputs", [])):
        return None
    def execute(expr):
        if isinstance(expr, ast.Name):
            return values[expr.id]
        if isinstance(expr, ast.UnaryOp):
            result = execute(expr.operand)
            if result is None:
                return None
            return -result if isinstance(expr.op, ast.USub) else result
        if isinstance(expr, ast.Call):
            if expr.func.id=='cagr':
                from .root_math import cagr_interval
                points=[DecimalInterval(execute(x),execute(x),True,True) for x in expr.args]
                interval=cagr_interval(*points)
                return None if interval is None else (interval.lower+interval.upper)/2
            result = execute(expr.args[0])
            return None if result is None else abs(result)
        if isinstance(expr, ast.BoolOp):
            results = [execute(value) for value in expr.values]
            if isinstance(expr.op, ast.And):
                return False if False in results else None if None in results else True
            return True if True in results else None if None in results else False
        left = execute(expr.left)
        if isinstance(expr, ast.Compare):
            right = execute(expr.comparators[0])
            if left is None or right is None:
                return None
            op = expr.ops[0]
            return {ast.Eq: lambda: left == right, ast.NotEq: lambda: left != right, ast.Gt: lambda: left > right,
                    ast.GtE: lambda: left >= right, ast.Lt: lambda: left < right, ast.LtE: lambda: left <= right}[type(op)]()
        right = execute(expr.right)
        if isinstance(expr.op, ast.Div) and right == 0:
            return None
        if right is None or left is None:
            return None
        return {ast.Add: lambda: left+right, ast.Sub: lambda: left-right,
                ast.Mult: lambda: left*right, ast.Div: lambda: left/right}[type(expr.op)]()
    # Formula execution must not change global precision/flags. Enough local
    # precision is retained for large financial amounts and derived ratios.
    with localcontext() as context:
        exponent_span=max(value.adjusted() for value in values.values())-min(value.as_tuple().exponent for value in values.values()) if values else 0
        context.prec = max(64, exponent_span+12, sum(len(value.as_tuple().digits) for value in values.values()) + 12)
        return execute(node)


def evaluate_rule_interval(card: dict, operands: dict) -> DecimalInterval | None:
    """Propagate disclosure intervals through the same frozen arithmetic AST."""
    node = _compile(card)
    if set(operands) - set(card['inputs']):
        raise RuleError('区间操作数包含未授权科目')
    if set(card['inputs']) - set(operands) or any(value is None for value in operands.values()):
        return None
    if any(not isinstance(value, DecimalInterval) for value in operands.values()):
        raise RuleError('区间操作数必须为DecimalInterval')
    if any(operands[key].lower <= 0 for key in card.get('positive_inputs', [])):
        return None
    values = dict(operands)
    values.update({key: DecimalInterval(Decimal(str(item['value'])), Decimal(str(item['value'])), True, True)
                   for key, item in card.get('constants', {}).items()})
    def execute(expr):
        if isinstance(expr, ast.Name):
            return values[expr.id]
        if isinstance(expr, ast.UnaryOp):
            value = execute(expr.operand)
            return value.scaled(Decimal(-1)) if isinstance(expr.op, ast.USub) else value
        if isinstance(expr, ast.Call):
            if expr.func.id=='cagr':
                from .root_math import cagr_interval
                interval=cagr_interval(*[execute(x) for x in expr.args])
                if interval is None:raise ValueError('CAGR前提不满足')
                return interval
            return execute(expr.args[0]).absolute()
        if not isinstance(expr, ast.BinOp):
            raise RuleError('仅算术规则允许生成数值披露区间')
        left, right = execute(expr.left), execute(expr.right)
        return {ast.Add: left.add, ast.Sub: left.subtract,
                ast.Mult: left.multiply, ast.Div: left.divide_positive}[type(expr.op)](right)
    try:
        return execute(node)
    except ValueError:
        return None


def load_rule_cards(root=None) -> list[dict]:
    return json.loads(((Path(root) if root else rulepack_root()) / "cards.json").read_text(encoding="utf-8"))


def load_caliber_dictionary(root=None) -> list[dict]:
    return json.loads(((Path(root) if root else rulepack_root()) / "caliber.json").read_text(encoding="utf-8"))


def execute_rule(rule_id: str, operands: dict, root=None):
    card = next((item for item in load_rule_cards(root) if item["id"] == rule_id), None)
    if card is None:
        raise RuleError("规则编号不在冻结规则包中")
    return evaluate_rule(card, operands)


def execute_rule_interval(rule_id: str, operands: dict, root=None):
    card = next((item for item in load_rule_cards(root) if item['id'] == rule_id), None)
    if card is None:
        raise RuleError('规则编号不在冻结规则包中')
    return evaluate_rule_interval(card, operands)


def intervals_compatible(observed: DecimalInterval, expected: DecimalInterval) -> bool:
    operands = {}
    for prefix, interval in (('observed', observed), ('expected', expected)):
        operands.update({prefix+'_lower': interval.lower, prefix+'_upper': interval.upper,
                         prefix+'_lower_closed': int(interval.lower_closed),
                         prefix+'_upper_closed': int(interval.upper_closed)})
    return execute_rule('E1-value', operands) is True


def compare_disclosure_intervals(left: DecimalInterval, right: DecimalInterval, relation: str) -> bool | None:
    """Order only when every possible value obeys the stated relationship."""
    def greater(a, b, inclusive=False):
        return execute_rule('E7-comparison', {'current': a.lower, 'base': b.upper,
            'permit_touch': int(inclusive or not (a.lower_closed and b.upper_closed))}) is True
    if relation == 'eq':
        if left.lower == left.upper == right.lower == right.upper:
            return True
        return None if left.intersects(right) else False
    if relation in {'lt', 'le'}:
        return compare_disclosure_intervals(right, left, 'gt' if relation == 'lt' else 'ge')
    if relation not in {'gt', 'ge'}:
        raise RuleError('比较关系未登记')
    if greater(left, right, relation == 'ge'):
        return True
    if greater(right, left, relation == 'gt'):
        return False
    return None


def rule_applicability(rule_id: str, report_date: str, root=None) -> dict:
    """Separate mathematical executability from regulatory validity/signoff."""
    base = Path(root) if root else rulepack_root()
    as_of = date.fromisoformat(report_date)
    card = next((item for item in load_rule_cards(base) if item['id'] == rule_id), None)
    if not card:
        raise RuleError('未知规则编号')
    sources = {item['id']: item for item in json.loads((base/'sources.json').read_text(encoding='utf-8'))['sources']}
    reasons = []
    for ref in card['basis_refs']:
        source = sources[ref['source_id']]
        if source.get('kind') != 'regulatory':
            continue
        if not source.get('effective_at') or not source.get('source_url'):
            reasons.append(ref['source_id']+':source_metadata_incomplete')
            continue
        if as_of < date.fromisoformat(source['effective_at']) or (source.get('valid_until') and as_of > date.fromisoformat(source['valid_until'])):
            reasons.append(ref['source_id']+':outside_effective_period')
    if not card.get('approved_by'):
        reasons.append('human_signoff_pending')
    return {'rule_id':rule_id,'report_date':report_date,'mathematically_executable':True,
            'professional_applicability':'approved' if not reasons else 'pending_or_inapplicable','reasons':reasons}


def validate_rulepack(root=None):
    root = Path(root) if root else rulepack_root()
    try:
        cards = load_rule_cards(root)
        sources = json.loads((root/'sources.json').read_text(encoding='utf-8'))
        clauses = json.loads((root/'source_clauses.json').read_text(encoding='utf-8'))
        dictionary = load_caliber_dictionary(root)
    except (OSError, ValueError) as exc:
        return {'status':'failed', 'cards':0, 'errors':[str(exc)], 'professional_signoff_status':'pending'}
    errors = []
    fields = {'id','basis','applicable_caliber','formula','dimension','examples','version','version_hash'}
    ids, signatures = set(), {}
    source_by_id = {s['id']: s for s in sources['sources']}
    clause_by_id = {s['id']: s for s in clauses}
    pending = []
    metadata = {key:value for key,value in sources.items() if key != 'version_hash'}
    if hashlib.sha256(json.dumps(metadata,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest() != sources.get('version_hash'):
        errors.append('来源登记版本哈希不匹配')
    if len(cards) < 30 or len(dictionary) < 60:
        errors.append('规则卡或口径字典未达到30/60首版范围')
    for source in sources['sources']:
        snapshot = root / source.get('snapshot', '')
        if not snapshot.is_file() or hashlib.sha256(snapshot.read_bytes()).hexdigest() != source.get('sha256'):
            errors.append(source['id'] + ': 来源快照哈希不匹配')
    for card in cards:
        identifier = card.get('id','unknown')
        if identifier in ids or not fields <= card.keys():
            errors.append(f'{identifier}: missing fields or duplicate ID')
        ids.add(identifier)
        value = {k:v for k,v in card.items() if k != 'version_hash'}
        checksum = hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')).hexdigest()
        if checksum != card.get('version_hash'):
            errors.append(f'{identifier}: version hash mismatch')
        if not {'positive','negative','boundary'} <= card.get('examples',{}).keys():
            errors.append(f'{identifier}: missing example')
        try:
            _compile(card)
            for category, example in card['examples'].items():
                result = evaluate_rule(card, example['operands'])
                expected = example.get('expected')
                expected = expected if isinstance(expected, (bool, type(None))) else Decimal(str(expected))
                equal = result == expected
                if equal != example.get('expect_match', True):
                    raise RuleError(f'{category}例执行结果不同')
            key = card.get('conflict_key', identifier)
            if key in signatures and signatures[key] != card['formula']:
                raise RuleError('同适用条件下规则公式冲突')
            signatures[key] = card['formula']
            for basis in card['basis_refs']:
                source = source_by_id[basis['source_id']]
                clause = clause_by_id[basis['clause_id']]
                if clause['source_id'] != source['id'] or basis['quote'] != clause['quote']:
                    raise RuleError('依据逐字引文不匹配')
                if clause['quote'] not in (root/source['snapshot']).read_text(encoding='utf-8'):
                    raise RuleError('引文不在冻结来源原文中')
                checksum = hashlib.sha256(clause['quote'].encode('utf-8')).hexdigest()
                if checksum != basis['quote_sha256'] or checksum != clause['quote_sha256']:
                    raise RuleError('引文哈希不匹配')
                if source.get('kind') == 'regulatory':
                    if not source.get('source_url') or not source.get('effective_at'):
                        pending.append(source['id'])
                    else:
                        date.fromisoformat(source['effective_at'])
                    if source.get('valid_until'):
                        date.fromisoformat(source['valid_until'])
            if not card.get('approved_by'):
                pending.append(identifier + ':human_signoff')
        except (RuleError, ValueError, KeyError, TypeError, OSError) as exc:
            errors.append(f'{identifier}: {exc}')
    labels = set()
    for item in dictionary:
        if item.get('label') in labels or item.get('scope') not in {'合并','母公司'} or item.get('nr') not in {'未扣非','扣非'} or not item.get('account'):
            errors.append('口径字典重复或口径不合法')
        labels.add(item.get('label'))
    return {'status':'ok' if not errors else 'failed','cards':len(cards),'dictionary_entries':len(dictionary),
            'version':sources['version'],'errors':errors, 'professional_signoff_status':'pending' if pending else 'approved',
            'pending_review':sorted(set(pending)), 'checks':['schema','quote','formula_dimension','account','executable_examples','conflict','free_constants']}
