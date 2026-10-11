"""Harness page supplement UI against real PDF parsing and task versions."""
import json

import pytest

from test_harness_web_ui import browser_page, ui_host
from test_reparse_packets import source
from test_reparse_web import frozen_parent


@pytest.mark.parametrize('status,title', [
    ('ready', '补证包已就绪'),
    ('conflicts', '发现解析冲突（X4）'),
    ('no_new_information', '未发现新增信息'),
])
def test_harness_page_supplement_preserves_parent_and_creates_only_ready_versions(ui_host, browser_page, source, status, title):
    from playwright.sync_api import expect
    state, base = ui_host
    page, errors, external = browser_page
    parent = frozen_parent(state, source, status=status,
        draft_text='甲公司2024年合并净利润20元。甲公司2024年合并营业收入100元。')
    before = {path.relative_to(parent.out): path.read_bytes() for path in parent.out.rglob('*') if path.is_file()}
    frozen = json.loads((parent.out / 'results.json').read_text(encoding='utf-8'))
    claim = next(row for row in frozen['claims'] if row['account'] == 'NP')
    page.goto(base.rstrip('/') + '/classic')
    page.locator('.history-row').filter(has_text=parent.id[-8:]).click()
    expect(page.locator('.verdict-row')).to_have_count(2)
    page.locator('.verdict-row').filter(has_text='净利润20元').click()
    page.locator('.inspector-body .evidence-link').first.click()
    expect(page.get_by_alt_text('财报原始页面与证据定位')).to_have_js_property('complete', True)
    expect(page.get_by_role('heading', name='页面补证', exact=True)).to_be_visible()
    with page.expect_response(lambda response: response.url.endswith(f'/api/runs/{parent.id}/reparse')
            and response.request.method == 'POST') as parsing:
        page.get_by_role('button', name='重新解析该页', exact=True).click()
    response = parsing.value
    assert response.status == 201
    assert response.request.post_data_json == {
        'report_id': source['block']['report_id'], 'page': 3, 'binding_id': claim['claim_id']}
    packet = response.json()
    assert packet['status'] == status
    expect(page.locator('.reparse-packet')).to_contain_text(title)
    expect(page.locator('.reparse-packet')).to_contain_text(packet['packet_id'])
    assert all((parent.out / path).read_bytes() == raw for path, raw in before.items())
    if status != 'ready':
        assert page.get_by_role('button', name='创建补证版本', exact=True).count() == 0
        assert len(state.tasks) == 1
    else:
        page.get_by_role('button', name='预览新增事实', exact=True).click()
        preview = page.get_by_role('region', name='新增补证事实预览', exact=True)
        expect(preview).to_contain_text('营业收入')
        expect(preview).to_contain_text('100 元')
        expect(preview).to_contain_text('待应用的补证事实')
        assert preview.locator('.reparse-preview-fact').count() == packet['new_facts_count']
        with page.expect_response(lambda response: f'/reparse-packets/{packet["packet_id"]}/evidence/' in response.url
                and response.url.endswith('?format=png')) as source_image:
            preview.locator('.reparse-preview-evidence').first.click()
        assert source_image.value.status == 200
        assert source_image.value.body().startswith(b'\x89PNG\r\n\x1a\n')
        expect(page.get_by_alt_text('补证新增事实的原始 PDF 页面与定位')).to_have_js_property('complete', True)
        with page.expect_response(lambda response: response.url.endswith(f'/api/runs/{parent.id}/rerun')
                and response.request.method == 'POST') as applying:
            page.get_by_role('button', name='创建补证版本', exact=True).click()
        response = applying.value
        assert response.status == 202
        assert response.request.post_data_json == {'reparse_packet_ids': [packet['packet_id']]}
        child = state.get_task(response.json()['id'])
        expect(page.locator('.history-row.active')).to_contain_text(child.id[-8:])
        expect(page.locator('.summary > div').first).to_contain_text('2')
        expect(page.locator('.verdict-row')).to_have_count(2)
        expect(page.locator('.inspector')).to_have_count(0)
        assert child.status == 'completed', child.error
        assert child.request['parent_run_id'] == parent.id
        expect(page.locator('.verdict-row').filter(has_text='营业收入100元')).to_contain_text('已核实')
        page.get_by_role('button', name='内核重放', exact=True).click()
        expect(page.get_by_role('status')).to_contain_text('passed')
        page.get_by_role('button', name='补证材料', exact=True).click()
        added = json.loads((child.out / 'results.json').read_text(encoding='utf-8'))['reparse_supplements'][0]['added_fact_ids']
        expect(page.locator('.verdict-row')).to_have_count(len(added))
        page.locator('.verdict-row').filter(has_text='2023Y').filter(has_text='营业收入').click()
        expect(page.locator('.inspector-body')).to_contain_text('原页补证事实')
        expect(page.locator('.inspector-body')).to_contain_text('80 元')
        expect(page.get_by_role('heading', name='原页事实', exact=True)).to_be_visible()
        assert page.get_by_role('heading', name='内核正确值', exact=True).count() == 0
        assert page.locator('.inspector-body').get_by_text('已核实', exact=True).count() == 0
        page.locator('.inspector-body .evidence-link').first.click()
        expect(page.get_by_alt_text('财报原始页面与证据定位')).to_have_js_property('complete', True)
    assert all((parent.out / path).read_bytes() == raw for path, raw in before.items())
    assert not errors, errors
    assert not external, external


def test_task_supplement_list_is_accessible_when_missing_claim_has_no_evidence(ui_host, browser_page, source):
    from playwright.sync_api import expect
    state, base = ui_host
    page, errors, external = browser_page
    parent = frozen_parent(state, source)
    before = {path.relative_to(parent.out): path.read_bytes() for path in parent.out.rglob('*') if path.is_file()}
    data = json.loads((parent.out / 'results.json').read_text(encoding='utf-8'))
    assert data['results'][0]['status'] == 'abstained' and data['results'][0]['evidence'] == []
    packet = state.reparse(parent.id, {'report_id': source['block']['report_id'], 'page': 3})
    assert packet['status'] == 'ready'
    page.goto(base.rstrip('/') + '/classic')
    page.locator('.history-row').filter(has_text=parent.id[-8:]).click()
    expect(page.locator('.verdict-row')).to_have_count(1)
    packets = page.get_by_role('region', name='任务补证包', exact=True)
    expect(packets).to_contain_text('补证包已就绪')
    expect(packets).to_contain_text('第 2、3、4 页')
    assert page.locator('.inspector').count() == 0
    packets.get_by_role('button', name='预览新增事实', exact=True).click()
    expect(packets.get_by_role('region', name='新增补证事实预览')).to_contain_text('100 元')
    packets.locator('.reparse-preview-evidence').first.click()
    expect(packets.get_by_alt_text('补证新增事实的原始 PDF 页面与定位')).to_have_js_property('complete', True)
    with page.expect_response(lambda response: response.url.endswith(f'/api/runs/{parent.id}/rerun')
            and response.request.method == 'POST') as applying:
        packets.get_by_role('button', name='创建补证版本', exact=True).click()
    assert applying.value.status == 202
    child = state.get_task(applying.value.json()['id'])
    expect(page.locator('.history-row.active')).to_contain_text(child.id[-8:])
    expect(page.locator('.summary > div').first).to_contain_text('1')
    expect(page.locator('.verdict-row')).to_contain_text('已核实')
    assert child.status == 'completed', child.error
    assert all((parent.out / path).read_bytes() == raw for path, raw in before.items())
    assert not errors, errors
    assert not external, external
