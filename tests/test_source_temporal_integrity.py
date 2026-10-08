"""Publication cutoff is enforced for every original-text access path."""
import pytest

from mingjing.financial_tools import FinancialDispatcher, ToolDenied


def source_task(publish_date='2025-03-01', count=1):
    resource = {'id': 'report-alpha', 'sha256': 'a'*64, 'path': 'registered.pdf', 'publish_date': publish_date}
    reports = [{'report_id': resource['id'], 'companies': ['甲公司'], 'sha256': resource['sha256']}]
    blocks = [{'id': f'block-{i}', 'report_id': resource['id'], 'page': i+1,
               'bbox': [10, 10, 500, 100], 'text': '会计政策变更：采用新的确认方法。'} for i in range(count)]
    dispatcher = FinancialDispatcher('run', 'lock', [], [], blocks, reports, '2025-03-31',
                                     task_company='甲公司', source_resources=[resource])
    dispatcher.register_session('question', 'question')
    return dispatcher


def call(dispatcher, tool, params):
    return dispatcher.dispatch({'session_id': 'question', 'role': 'question', 'run_id': 'run',
                                'resource_lock': 'lock', 'call_id': str(len(dispatcher.trace)),
                                'tool_name': tool, 'params': params})


@pytest.mark.parametrize('publish_date', ['', '2025-04-01', 'not-a-date'])
def test_unknown_or_future_publication_cannot_be_read_or_quoted(publish_date):
    d = source_task(publish_date)
    searched = call(d, 'mj_search_blocks', {'query': '会计政策变更'})
    assert not searched[0].get('source_references')
    direct = call(d, 'mj_read_context', {'mention_id': 'block-0'})
    assert not direct.get('source_references')
    assert '采用新的确认方法' not in str(direct)
    assert not d.source_references or all(row.get('reference_status') != 'unverified_source_quotation'
                                          for row in d.source_references.values())


def test_source_publication_rechecked_when_submitting_existing_slots():
    d = source_task()
    revealed = call(d, 'mj_read_context', {'mention_id': 'block-0'})
    assert revealed['source_references']
    d.source_resources['report-alpha']['publish_date'] = '2025-04-01'
    with pytest.raises(ToolDenied):
        call(d, 'mj_submit_report', {'template': revealed['slot_template'], 'slots': revealed['slot_ids']})
    assert not d.templates


def test_quotation_limit_rejects_at_submit_and_allows_corrected_retry():
    d = source_task(count=33)
    slots = []
    for index in range(33):
        slots.extend(call(d, 'mj_read_context', {'mention_id': f'block-{index}'})['slot_ids'])
    with pytest.raises(ToolDenied):
        call(d, 'mj_submit_report', {'template': '# 原文\n'+'\n'.join('{{'+slot+'}}' for slot in slots), 'slots': slots})
    assert not d.templates
    slots = slots[:32]
    accepted = call(d, 'mj_submit_report', {'template': '# 原文\n'+'\n'.join('{{'+slot+'}}' for slot in slots), 'slots': slots})
    assert accepted['accepted'] and len(d.submitted_slots['question']) == 32
