"""Harness UI settings and conversation acceptance in a real browser.

Settings exercise the real host and encrypted local SecretStore. Controlled
financial/session replies exercise UI states only, never model accuracy.
The real offline financial demo is covered in test_harness_web_ui.py.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from test_harness_web_ui import browser_page, ui_host  # shared real-host/browser fixtures


def save_settings(page, title):
    with page.expect_response(lambda response: response.url.endswith('/api/config') and response.request.method == 'PATCH') as saved:
        page.get_by_role('button', name=title, exact=True).click()
    assert saved.value.status == 200, saved.value.json()
    from playwright.sync_api import expect
    expect(page.get_by_role('button', name=title, exact=True)).to_be_enabled()


def test_browser_launcher_settings_export_and_shutdown(tmp_path, browser_page):
    home = tmp_path / 'profile'
    env = os.environ.copy()
    env.update(MINGJING_HOME=str(home), MINGJING_NO_BROWSER='1', PYTHONIOENCODING='utf-8')
    child = subprocess.Popen([sys.executable, '-m', 'mingjing.launcher', '--home', str(home), '--no-browser'],
                             cwd=Path(__file__).resolve().parents[1], env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='replace')
    try:
        record = home / 'workspace' / 'server.json'
        deadline = time.monotonic() + 10
        while not record.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert record.exists(), child.communicate(timeout=1)[0]
        base = json.loads(record.read_text(encoding='utf-8'))['url']
        page, errors, external = browser_page
        page.goto(base.rstrip('/') + '/classic')
        page.get_by_role('button', name='新建金融任务', exact=True).wait_for()
        page.get_by_role('button', name='设置', exact=True).click()
        page.get_by_role('button', name='服务与安全', exact=True).click()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='导出配置', exact=True).click()
        assert downloaded.value.suggested_filename == 'MingJing-config.json'
        exported = json.loads(Path(downloaded.value.path()).read_text(encoding='utf-8'))
        assert exported['config']['version'] == 2
        assert exported['secrets'] == 'omitted'
        assert not errors, errors
        assert not external, external
        req = urllib.request.Request(base.rstrip('/') + '/api/shutdown', method='POST', data=b'{}',
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=3) as response:
            assert response.status == 202
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def test_browser_provider_routes_partial_ledger_and_followup_controls(ui_host, browser_page):
    state, base = ui_host
    page, errors, external = browser_page
    page.goto(base.rstrip('/') + '/classic')
    page.get_by_role('button', name='设置', exact=True).click()
    page.get_by_label('供应商预设').select_option('kimi')
    page.get_by_label('模型 ID', exact=True).fill('kimi-fixture')
    page.get_by_label('API Token', exact=True).fill('ui-test-credential')
    page.get_by_label('单次输出 Token 上限').fill('3072')
    page.get_by_label('思考模式', exact=True).select_option('off')
    save_settings(page, '保存供应商与密钥')
    page.get_by_role('status').filter(has_text='设置已保存').wait_for()
    cfg = state.config.get()
    assert cfg['agent']['routing']['default'] == {'provider': 'kimi', 'model': 'kimi-fixture'}
    assert cfg['providers']['kimi']['protocol'] == 'openai-completions'
    assert cfg['providers']['kimi']['models'][0]['max_tokens'] == 3072
    assert cfg['providers']['kimi']['models'][0]['reasoning'] == 'off'
    assert 'ui-test-credential' not in json.dumps(state.config.public())
    assert page.get_by_label('API Token', exact=True).input_value() == ''

    # Discovery is a UI fixture, not a live Kimi API acceptance claim.
    page.route('**/api/providers/kimi/discover', lambda route: route.fulfill(json={
        'ok': True, 'models': [{'id': 'kimi-discovered', 'capability_status': 'declared_only'}],
        'agent_capability': 'not_tested'}))
    page.get_by_role('button', name='发现模型', exact=True).click()
    page.get_by_label('发现的模型', exact=True).wait_for()
    page.get_by_role('button', name='使用所选模型', exact=True).click()
    assert page.get_by_label('模型 ID', exact=True).input_value() == 'kimi-discovered'
    save_settings(page, '保存供应商与密钥')
    from playwright.sync_api import expect
    expect(page.get_by_role('status')).to_contain_text('设置已保存')
    assert [model['id'] for model in state.config.get()['providers']['kimi']['models']] == ['kimi-fixture', 'kimi-discovered']

    page.get_by_role('button', name='角色与预算', exact=True).click()
    route = json.dumps({'provider': 'kimi', 'model': 'kimi-fixture'}, separators=(',', ':'))
    page.get_by_label('verifier：独立绑定 B').select_option(route)
    page.get_by_label('任务模型请求上限', exact=True).fill('99')
    page.get_by_label('追问模型请求上限', exact=True).fill('8')
    save_settings(page, '保存角色与预算')
    cfg = state.config.get()
    assert cfg['agent']['routing']['verifier'] == {'provider': 'kimi', 'model': 'kimi-fixture'}
    assert cfg['agent']['budget']['max_model_calls'] == 99
    assert cfg['agent']['followup_budget']['max_model_calls'] == 8

    page.get_by_role('button', name='运行时', exact=True).click()
    assert page.get_by_label('Harness 运行时').input_value() == 'embedded'
    page.get_by_label('Harness 运行时').select_option('external')
    page.get_by_label('外部原生运行时路径').fill('C:/advanced-native/dsh.exe')
    save_settings(page, '保存运行时')
    assert state.config.get()['harness']['enabled'] is True
    assert state.config.get()['harness']['runtime_mode'] == 'external'
    assert state.config.get()['harness']['profile'] == 'mingjing'
    assert all(state.config.get()['harness'][key] for key in ('disable_shell', 'disable_code_mode', 'disable_telemetry', 'disable_network'))
    page.get_by_label('Harness 运行时').select_option('embedded')
    save_settings(page, '保存运行时')
    assert state.config.get()['harness']['runtime_mode'] == 'embedded'

    # Enable authentication through the UI; the next export must automatically
    # carry the newly configured in-memory token without locking this user out.
    page.get_by_role('button', name='服务与安全', exact=True).click()
    page.get_by_label('本地访问 Token', exact=True).fill('ui-local-access-token')
    page.get_by_label('要求本地访问 Token', exact=True).check()
    save_settings(page, '保存服务、安全与日志')
    assert state.config.get()['server']['bearer_token_required'] is True
    with page.expect_download() as downloaded:
        page.get_by_role('button', name='导出配置', exact=True).click()
    raw = Path(downloaded.value.path()).read_text(encoding='utf-8')
    assert 'ui-test-credential' not in raw and 'ui-local-access-token' not in raw
    assert page.evaluate('JSON.stringify([localStorage,sessionStorage])') == '[{},{}]'
    assert page.get_by_label('本地访问 Token', exact=True).input_value() == ''
    page.get_by_role('button', name='关闭', exact=True).last.click()

    run_id = 'ui-partial-fixture'
    source = 'fixture financial amount'
    fixture = {'run_id': run_id, 'mode': 'harness', 'draft_text': source,
               'claims': [{'claim_id':'m1','text':source,'start':0,'end':len(source),'account':'UNKNOWN','period':''}],
               'facts': [], 'results': [{'claim_id':'m1','status':'abstained','abstention_code':'X3','error_type':'E8','claim_text':source,'source_span':{'start':0,'end':len(source)},'evidence':[],'basis':[]}],
               'summary': {'abstained':1}, 'partial': True,
               'model': {'status': 'partial', 'error': 'fixture token budget exhausted'},
               'coverage': {'partial': True, 'counts': {'pending': 1}, 'financial_candidates': 2,
                            'processed': 1, 'coverage': .5,
                            'mentions': [{'id': 'm1', 'text': source, 'start': 0, 'end': len(source),
                                          'claim_id':'m1', 'status': 'pending', 'reason': 'needs evidence', 'page_id': 'p1'}],
                            'pages': [{'id': 'p1', 'page': 1, 'start': 0, 'end': len(source)}]},
               'analysis': {'metrics': [{'period': '2023Y', 'metric': 'REVENUE', 'status': 'verified',
                                        'value': '100', 'formula': 'source', 'evidence': [], 'operands': []}], 'signals': []}}
    run = {'id': run_id, 'status': 'partial', 'out': 'C:/fixture/frozen', 'request': {'company': 'UI fixture'}}
    page.route('**/api/runs', lambda route: route.fulfill(json={'runs': [run]}))
    page.route(f'**/api/runs/{run_id}', lambda route: route.fulfill(json=run))
    page.route(f'**/api/runs/{run_id}/events', lambda route: route.fulfill(json={'events': []}))
    page.route(f'**/api/runs/{run_id}/results', lambda route: route.fulfill(json=fixture))
    page.route(f'**/api/runs/{run_id}/reparse-packets', lambda route: route.fulfill(json={'packets': []}))
    page.get_by_role('button', name='刷新历史', exact=True).click()
    page.locator('.history-row').filter(has_text='UI fixture').click()
    page.get_by_text('本任务部分完成。', exact=False).wait_for()
    assert page.locator('.summary > div').nth(2).inner_text() == '0\n弃权'
    page.get_by_label('判定状态').select_option('pending')
    page.locator('.verdict-row').first.click()
    assert '当前没有可交付的内核结论' in page.locator('.inspector-body').inner_text()
    assert page.locator('.inspector-body').get_by_role('heading', name='数字与计算').count() == 0
    page.get_by_role('button', name='关闭', exact=True).last.click()
    page.get_by_role('button', name='覆盖账本', exact=True).click()
    assert 'needs evidence' in page.locator('.workspace-body').inner_text()
    assert '不能当作金融提及召回率' in page.locator('.workspace-body').inner_text()
    page.get_by_role('button', name='财报分析', exact=True).click()
    assert page.locator('.metric-card').count() == 1
    assert '营业收入' in page.locator('.metric-card').inner_text()

    # All session responses below are controlled state fixtures. Assert actual
    # HTTP controls instead of calling obsolete global renderResult functions.
    sessions, commands = [], []
    active = {'status': 'ready', 'resumed': False}
    sid = 'session-ui-fixture'
    def session_value(status, session_id=sid, resource_run=run_id):
        return {'session_id': session_id, 'run_id': resource_run, 'status': status,
                'messages': [{'role': 'assistant', 'text': '已依据冻结证据回答。', 'time': 0,
                              'message_id': 'message-0001', 'source_references': [{
                                  'evidence_id': 'source-quote-fixture', 'page': 3,
                                  'title': '会计政策变更披露原文', 'reference_status': 'source_quote'}]}] if status == 'ready' else [],
                'activities': [{'tool': 'mj_get_verdicts', 'role': 'question', 'status': 'success'}]}
    def create_session(route):
        if route.request.method == 'POST':
            request = route.request.post_data_json
            sessions.append(request)
            next_sid = sid if request['run_id'] == run_id else 'new-session-ui-fixture'
            route.fulfill(json=session_value('ready', next_sid, request['run_id']))
        else:
            route.fulfill(json={'sessions': []})
    def messages(route):
        commands.append(('messages', route.request.post_data_json));active['status'] = 'running'
        route.fulfill(json=session_value('running'))
    def cancel(route):
        commands.append(('cancel', route.request.method));active['status'] = 'cancelled'
        route.fulfill(json=session_value('cancelled'))
    def resume(route):
        commands.append(('resume', route.request.method));active.update(status='ready', resumed=True)
        route.fulfill(json=session_value('running'))
    page.route('**/api/sessions', create_session)
    page.route(f'**/api/sessions/{sid}', lambda route: route.fulfill(json=session_value(active['status'])))
    page.route(f'**/api/sessions/{sid}/messages', messages)
    page.route(f'**/api/sessions/{sid}/cancel', cancel)
    page.route(f'**/api/sessions/{sid}/resume', resume)
    page.get_by_role('button', name='材料追问', exact=True).click()
    assert page.get_by_label('金融问题').get_attribute('maxlength') == '3000'
    page.get_by_label('金融问题').fill('请解释冻结结果。')
    page.get_by_role('button', name='发送问题 ↑', exact=True).click()
    page.get_by_role('button', name='停止', exact=True).click()
    page.get_by_role('button', name='恢复会话', exact=True).click()
    page.get_by_text('已依据冻结证据回答。', exact=True).wait_for()
    assert sessions == [{'run_id': run_id}]
    assert commands == [('messages', {'question': '请解释冻结结果。'}), ('cancel', 'POST'), ('resume', 'POST')]
    # These responses test the UI context forwarding only. The guarded PDF
    # registry and tamper rejection are covered by real backend HTTP tests.
    from urllib.parse import parse_qs, urlparse
    import base64
    evidence_requests = []
    def quote_evidence(route):
        query = parse_qs(urlparse(route.request.url).query)
        evidence_requests.append(query)
        if query.get('format') == ['png']:
            route.fulfill(content_type='image/png', body=base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cCU0AAAAASUVORK5CYII='))
        else:
            route.fulfill(json={'page': 3, 'reference_status': 'source_quote'})
    page.route('**/api/evidence/source-quote-fixture?*', quote_evidence)
    page.get_by_role('button', name='查看原文证据 · 第 3 页').click()
    assert '未作数字核查' in page.locator('.inspector-body').inner_text()
    assert page.locator('.inspector-body').get_by_role('heading', name='数字与计算').count() == 0
    page.locator('.evidence-link').click()
    from playwright.sync_api import expect
    expect(page.get_by_alt_text('财报原始页面与证据定位')).to_have_js_property('complete', True)
    assert len(evidence_requests) == 2
    assert all(query.get('session') == [sid] and query.get('message') == ['message-0001'] and
               query.get('run') == [run_id] for query in evidence_requests)
    page.get_by_role('button', name='关闭', exact=True).last.click()
    page.get_by_text('本条消息的工具与执行活动', exact=False).click()
    assert 'mj_get_verdicts' in page.locator('.conversation-activities').inner_text()

    # Adding material creates a new run and the next question gets a new
    # session; paths only are forwarded, never candidate financial values.
    new_id = 'ui-new-version'
    new_run = {**run, 'id': new_id, 'status': 'completed', 'request': {'company': 'UI new version'}}
    materials = []
    def supplement(route):
        materials.append(route.request.post_data_json)
        route.fulfill(json={'run_id': new_id, 'parent_run_id': run_id, 'new_version': True})
    page.route(f'**/api/sessions/{sid}/materials', supplement)
    page.route(f'**/api/runs/{new_id}', lambda route: route.fulfill(json=new_run))
    page.route(f'**/api/runs/{new_id}/events', lambda route: route.fulfill(json={'events': []}))
    page.route(f'**/api/runs/{new_id}/results', lambda route: route.fulfill(json={**fixture, 'run_id': new_id}))
    page.route(f'**/api/runs/{new_id}/reparse-packets', lambda route: route.fulfill(json={'packets': []}))
    page.route('**/api/sessions/new-session-ui-fixture/messages', lambda route: route.fulfill(json=session_value('running', 'new-session-ui-fixture', new_id)))
    page.route('**/api/sessions/new-session-ui-fixture', lambda route: route.fulfill(json=session_value('running', 'new-session-ui-fixture', new_id)))
    page.get_by_role('button', name='补充材料', exact=True).click()
    page.get_by_label('追加财报路径', exact=True).fill('C:/new-materials/annual.pdf')
    page.get_by_role('button', name='补证后重查', exact=True).click()
    page.get_by_role('button', name='材料追问', exact=True).wait_for()
    page.get_by_role('button', name='材料追问', exact=True).click()
    page.get_by_label('金融问题').fill('说明新材料。')
    page.get_by_role('button', name='发送问题 ↑', exact=True).click()
    page.get_by_role('button', name='停止', exact=True).wait_for()
    assert materials == [{'corpus': 'C:/new-materials/annual.pdf'}]
    assert sessions[-1] == {'run_id': new_id}
    assert 'ui-test-credential' not in page.content() and 'ui-local-access-token' not in page.content()
    assert not errors, errors
    assert not external, external
