"""Frozen Python source addresses must select the same text in both readers."""
from mingjing.report import html_report
from test_harness_web_ui import browser_page, ui_host


def source_payload():
    return {'run_id': 'unicode-source-run', 'mode': 'kernel',
        'draft_text': '😀𠮷公司：收入10亿元。尾部🚀', 'claims': [], 'facts': [],
        'summary': {'verified': 1, 'violated': 0, 'abstained': 0},
        'results': [{'claim_id': 'income', 'claim_text': '收入10亿元', 'status': 'verified',
            'source_span': {'start': 5, 'end': 11}, 'evidence': [], 'basis': []}]}


def test_harness_source_reader_preserves_supplementary_characters(ui_host, browser_page):
    _, base = ui_host
    page, errors, external = browser_page
    payload = source_payload()
    run = {'id': payload['run_id'], 'status': 'completed', 'out': 'C:/fixture/unicode', 'request': {}}
    page.route('**/api/runs', lambda route: route.fulfill(json={'runs': [run]}))
    page.route(f'**/api/runs/{run["id"]}', lambda route: route.fulfill(json=run))
    page.route(f'**/api/runs/{run["id"]}/results', lambda route: route.fulfill(json=payload))
    page.route(f'**/api/runs/{run["id"]}/events', lambda route: route.fulfill(json={'events': []}))
    page.route(f'**/api/runs/{run["id"]}/reparse-packets', lambda route: route.fulfill(json={'packets': []}))
    page.route(f'**/api/runs/{run["id"]}/reparse-proposals', lambda route: route.fulfill(json={'proposals': []}))
    page.route('**/api/sessions', lambda route: route.fulfill(json={'sessions': []}))
    page.goto(base)
    page.locator('.history-row').first.click()
    page.get_by_role('button', name='原文', exact=True).click()
    mark = page.locator('.source-text mark')
    assert mark.inner_text() == '收入10亿元'
    assert page.locator('.source-text').inner_text() == payload['draft_text']
    mark.press('Enter')
    assert '收入10亿元' in page.locator('.inspector-body').inner_text()
    assert not errors and not external


def test_exported_html_source_reader_uses_the_same_codepoint_addresses(browser_page):
    page, errors, external = browser_page
    payload = source_payload()
    page.set_content(html_report(payload))
    assert page.locator('#source mark').inner_text() == '收入10亿元'
    assert page.locator('#source').inner_text() == payload['draft_text']
    page.locator('#source mark').click()
    assert 'income · 核实' in page.locator('#detail').inner_text()
    assert not errors and not external
