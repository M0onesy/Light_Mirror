"""Browser acceptance of the vendored Harness UI against the real local host.

These are product flow checks, not mocked assertions about JSX. They never
contact a model provider and use the built-in redistributable financial corpus.
"""
import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

import pytest

from mingjing import server


@pytest.fixture
def ui_host(tmp_path, monkeypatch):
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path / 'profile'))
    if not (server.STATIC_ROOT / 'harness' / 'index.html').is_file():
        pytest.skip('Build the Harness frontend before browser acceptance')
    state = server.AppState()
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield state, f'http://127.0.0.1:{httpd.server_port}'
    for task in state.tasks.values():
        task.cancelled.set()
        if task.thread:
            task.thread.join(5)
    httpd.shutdown()
    httpd.server_close()
    thread.join(2)


@pytest.fixture
def browser_page():
    module = pytest.importorskip('playwright.sync_api')
    with module.sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except module.Error as exc:
            pytest.skip(f'Chromium unavailable: {exc}')
        context = browser.new_context(viewport={'width': 1440, 'height': 960}, accept_downloads=True)
        page = context.new_page()
        errors = []
        external = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda message: errors.append(message.text) if message.type == 'error' else None)
        page.on('request', lambda request: external.append(request.url) if urlparse(request.url).scheme in {'http', 'https'} and urlparse(request.url).hostname != '127.0.0.1' else None)
        yield page, errors, external
        context.close()
        browser.close()


def screenshot(page, name):
    root = os.environ.get('MINGJING_UI_RECEIPTS')
    if root:
        target = Path(root)
        target.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(target / f'{name}.png'), full_page=True)


def test_harness_ui_followup_budget_defaults_and_saved_user_limits(ui_host, browser_page):
    state, base = ui_host
    page, errors, external = browser_page
    page.goto(base.rstrip('/') + '/classic')
    page.get_by_role('button', name='设置', exact=True).click()
    page.get_by_role('button', name='角色与预算', exact=True).click()
    token_input = page.get_by_label('追问Token 总预算', exact=True)
    assert token_input.input_value() == '150000'
    assert '当前每条追问最多 150,000 Token' in page.locator('.settings-content').inner_text()
    assert page.get_by_label('追问模型请求上限', exact=True).input_value() == '12'
    assert page.get_by_label('追问工具调用上限', exact=True).input_value() == '80'
    assert page.get_by_label('追问任务时限（秒）', exact=True).input_value() == '180'
    before = state.config.get()['agent']['followup_budget']
    frozen = server.RunTask('budget-snapshot', {}, state.runs_root, state.config).config_snapshot
    token_input.fill('128000')
    page.get_by_role('button', name='保存角色与预算', exact=True).click()
    page.get_by_role('status').filter(has_text='设置已保存').wait_for()
    assert state.config.get()['agent']['followup_budget'] == {**before, 'max_total_tokens': 128000}
    assert frozen['agent']['followup_budget']['max_total_tokens'] == 150000
    page.reload()
    page.get_by_role('button', name='设置', exact=True).click()
    page.get_by_role('button', name='角色与预算', exact=True).click()
    assert page.get_by_label('追问Token 总预算', exact=True).input_value() == '128000'
    assert '当前每条追问最多 128,000 Token' in page.locator('.settings-content').inner_text()
    # A prior explicitly saved default is a user value, never silently upgraded.
    state.config.update({'agent': {'followup_budget': {'max_total_tokens': 24000}}})
    page.reload()
    page.get_by_role('button', name='设置', exact=True).click()
    page.get_by_role('button', name='角色与预算', exact=True).click()
    assert page.get_by_label('追问Token 总预算', exact=True).input_value() == '24000'
    assert not errors, errors
    assert not external, external


def test_harness_ui_demo_evidence_preferences_analysis(ui_host, browser_page):
    state, base = ui_host
    page, errors, external = browser_page
    page.goto(base.rstrip('/') + '/classic')
    page.get_by_role('button', name='运行内置离线演示', exact=True).click()
    page.locator('.verdict-row').first.wait_for(timeout=20000)
    assert page.locator('.summary').inner_text().count('0\n') < 3
    assert page.locator('.source-text').count() == 0
    page.get_by_role('button', name='原文', exact=True).click()
    assert page.locator('.source-text mark.verified').count() > 0
    assert page.locator('.source-text mark.violated').count() > 0
    page.get_by_role('button', name='任务', exact=True).click()
    page.get_by_label('判定状态').select_option('violated')
    page.locator('.verdict-row').first.click()
    page.get_by_role('heading', name='规则依据', exact=True).wait_for()
    assert page.locator('.inspector-body').inner_text().count('替换文本') > 0
    page.locator('.evidence-link').first.click()
    image = page.get_by_alt_text('财报原始页面与证据定位')
    image.wait_for()
    from playwright.sync_api import expect
    expect(image).to_have_js_property('complete', True)
    assert image.evaluate('(img)=>img.naturalWidth') > 0
    screenshot(page, 'evidence')
    with page.expect_download() as downloading:
        page.get_by_role('button', name='导出报告', exact=True).click()
    assert downloading.value.suggested_filename.endswith('.zip')
    page.get_by_role('button', name='内核重放', exact=True).click()
    page.get_by_role('status').filter(has_text='重放记录').wait_for()
    assert 'passed' in page.get_by_role('status').inner_text()

    # PATCH returns a plain config; ensure the UI adopts its new revision.
    page.get_by_role('button', name='设置', exact=True).click()
    page.get_by_role('button', name='数据与路径', exact=True).click()
    path = state.config.get()['workspace']['corpus_root']
    page.get_by_role('button', name='保存路径', exact=True).click()
    page.get_by_role('status').filter(has_text='设置已保存').wait_for()
    page.get_by_role('button', name='保存路径', exact=True).click()
    assert state.config.get()['revision'] >= 3
    page.get_by_role('button', name='关闭', exact=True).last.click()
    page.get_by_role('button', name='新建金融任务', exact=True).click()
    page.get_by_role('button', name='财报分析', exact=True).click()
    page.get_by_label('研报草稿', exact=True).fill('')
    page.get_by_label('财报材料', exact=True).fill(path)
    page.get_by_role('button', name='开始任务', exact=True).click()
    page.locator('.metric-card').first.wait_for(timeout=20000)
    assert '可用指标' in page.locator('.analysis-summary').inner_text()
    assert page.get_by_label('分析期间').input_value() == 'latest'
    latest = page.get_by_label('分析期间').locator('option[value="latest"]').inner_text()
    assert '2024Y' in latest
    assert '2024Y' in page.locator('.metric-card').first.inner_text()
    page.get_by_label('分析期间').select_option('all')
    assert '2023Y' in page.locator('.metric-card').first.inner_text()
    assert '%' in page.locator('.metric-card').filter(has_text='营业收入同比').filter(has_text='已核实').first.inner_text()
    page.get_by_label('分析期间').select_option('latest')
    screenshot(page, 'analysis')
    assert not errors, errors
    assert not external, external


def test_harness_ui_material_clarification_without_first_sending_a_question(ui_host, browser_page):
    state, base = ui_host
    page, errors, external = browser_page
    page.goto(base.rstrip('/') + '/classic')
    page.get_by_role('button', name='运行内置离线演示', exact=True).click()
    page.locator('.verdict-row').first.wait_for(timeout=20000)
    original = next(iter(state.tasks.values()))
    frozen = (original.out/'results.json').read_bytes()
    page.get_by_role('button', name='材料追问', exact=True).click()
    page.get_by_role('button', name='补充材料', exact=True).click()
    page.get_by_label('补充口径声明').select_option('合并')
    page.get_by_role('button', name='补证后重查', exact=True).click()
    page.locator('.notice').filter(has_text='本任务使用用户补充声明的合并口径').wait_for(timeout=20000)
    assert len(state.tasks) == 2
    new = next(task for task in state.tasks.values() if task.id != original.id)
    assert new.request['scope_declaration'] == '合并'
    assert new.request['corpus'] == original.request['corpus']
    assert (original.out/'results.json').read_bytes() == frozen
    assert not errors, errors
    assert not external, external


def test_harness_ui_bearer_login_evidence_download_and_mobile(ui_host, browser_page):
    state, base = ui_host
    page, errors, external = browser_page
    secret = 'test-local-service-token-not-a-provider-key'
    state.config.set_secret('server_bearer', secret)
    state.config.update({'server': {'bearer_token_required': True, 'bearer_token_ref': 'server_bearer'}}, 0)
    response = page.goto(base.rstrip('/') + '/classic')
    assert response.status == 200
    page.get_by_label('本地服务访问令牌').fill(secret)
    page.get_by_role('button', name='连接本地服务').click()
    page.get_by_role('button', name='运行内置离线演示', exact=True).click()
    page.locator('.verdict-row').first.wait_for(timeout=20000)
    page.locator('.verdict-row').first.click()
    page.locator('.evidence-link').first.click()
    from playwright.sync_api import expect
    image = page.get_by_alt_text('财报原始页面与证据定位')
    expect(image).to_have_js_property('complete', True)
    assert image.evaluate('(img)=>img.naturalWidth') > 0
    with page.expect_download() as downloading:
        page.get_by_role('button', name='导出报告', exact=True).click()
    assert secret not in Path(downloading.value.path()).read_bytes().decode('latin1')
    # Secrets stay in component memory, not persistent browser storage/URL.
    assert secret not in page.url
    assert page.evaluate('JSON.stringify([localStorage,sessionStorage])') == '[{},{}]'
    page.set_viewport_size({'width': 390, 'height': 844})
    page.get_by_role('button', name='关闭', exact=True).last.click()
    screenshot(page, 'mobile')
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
    # Expected initial API 401 errors are filtered; no browser exceptions.
    assert not [error for error in errors if '401' not in error], errors
    assert not external, external
