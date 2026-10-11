"""Presentation records projected from the frozen smart analysis, without arithmetic."""
from __future__ import annotations

from copy import deepcopy

from .analysis import AMOUNTS, RATIOS, EXTRA
from .taxonomy import account_name


def metric_catalog(metric=None):
    rows = []
    for code in [*AMOUNTS, *RATIOS, *EXTRA]:
        label = EXTRA.get(code) or account_name(code)
        rows.append({'metric': code, 'label': label, 'kind': 'smart_kernel',
                     'rule_ids': ['SMART-registered-kernel']})
        for suffix, text in (('YOY', '同比'), ('QOQ', '环比'), ('PP', '百分点变动')):
            rows.append({'metric': code + '_' + suffix, 'label': label + text,
                         'kind': 'smart_kernel_comparison', 'rule_ids': ['SMART-registered-kernel']})
    return [row for row in rows if not metric or row['metric'] == metric]


def project_analysis(analysis):
    """Keep every smart value, proof, interval and visible block in native slots."""
    output = {key: deepcopy(value) for key, value in analysis.items()
              if key not in {'metrics', 'quarterly', 'signals', 'blocks'}}
    output.update(metrics=[], signals=[], events=[], execution_entry='mingjing.smart.analysis.analyze')
    seen = set()
    for group in ('metrics', 'quarterly'):
        for row in analysis.get(group, []):
            for period, cell in row.get('cells', {}).items():
                key = (row['metric'], period)
                if key not in seen:
                    seen.add(key)
                    record = deepcopy(cell)
                    record.update(metric=row['metric'], period=period, status='verified',
                                  projection_source='mingjing.smart.analysis.analyze',
                                  expected_interval=deepcopy(cell.get('interval', {})),
                                  rule_id='；'.join(cell.get('rule_ids', [])),
                                  value_unit='元' if row['metric'] in AMOUNTS else
                                             '倍' if row['metric'] in {'CURRENT_RATIO', 'CASH_CONTENT'} else
                                             '元/股' if row['metric'] in {'EPS', 'BPS'} else '无量纲比例')
                    output['metrics'].append(record)
                for relation in ('yoy', 'qoq', 'pp'):
                    calculation = cell.get(relation + '_calculation')
                    comparison_key = (row['metric'] + '_' + relation.upper(), period)
                    if not calculation or comparison_key in seen:
                        continue
                    seen.add(comparison_key)
                    record = deepcopy(calculation)
                    record.update(metric=comparison_key[0], period=period, status='verified',
                                  projection_source='mingjing.smart.analysis.analyze',
                                  expected_interval=deepcopy(calculation.get('interval', {})),
                                  rule_id='；'.join(calculation.get('rule_ids', [])), value_unit='无量纲比例')
                    output['metrics'].append(record)
    for block in analysis.get('blocks', []):
        record = deepcopy(block)
        relation = block.get('relation', 'value')
        metric = block['metric'] + ('_' + relation.upper() if relation != 'value' else '')
        key = (metric, block['period'])
        if key in seen:
            continue
        seen.add(key)
        record.update(metric=metric, status='abstained', abstention_code=block.get('abstain_code'),
                      rule_ids=['SMART-registered-kernel'], rule_id='SMART-registered-kernel')
        record.pop('value', None)
        output['metrics'].append(record)
    signal_kinds = {'S-NRI': 'non_recurring_share', 'S-CASH-LOW': 'low_cash_content',
                    'S-DIVERGE': 'profit_cashflow_divergence', 'S-LOSS-CASH': 'profit_cashflow_divergence',
                    'S-CASH-NEG': 'profit_cashflow_divergence'}
    for signal in analysis.get('signals', []):
        record = {**deepcopy(signal), 'kind': signal_kinds.get(signal['code'], signal['code']),
                  'reason': signal.get('detail', ''), 'scope': analysis.get('scope', ''),
                  'projection_source': 'mingjing.smart.analysis.analyze'}
        if signal['code'] in {'S-POLICY', 'S-RESTATE'}:
            title = signal.get('title', '')
            record['kind'] = ('disclosure_version_change' if signal['code'] == 'S-RESTATE' else
                              'accounting_estimate_change' if '估计' in title else
                              'prior_period_error' if '差错' in title else 'accounting_policy_change')
            record['source_text'] = signal.get('detail', '')
            output['events'].append(record)
        else:
            output['signals'].append(record)
    return output
