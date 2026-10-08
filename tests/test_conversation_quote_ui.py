"""Browser presentation only; controlled messages do not claim API accuracy."""
import base64
from urllib.parse import parse_qs, unquote, urlparse

from test_harness_web_ui import browser_page, ui_host


def test_long_source_reader_preserves_all_quotes_and_numeric_sections(ui_host, browser_page):
    _, base = ui_host
    page, errors, external = browser_page
    run_id, sid = 'quote-display-run', 'session-quote-display'
    references = [dict(evidence_id=f'quote-fragment-{index:02}',
        page=47 if index < 8 else 46 if index < 15 else 2,
        name='A年报.pdf' if index < 15 else 'B补充.pdf',
        doc_sha256=('a' if index < 15 else 'b') * 64,
        reference_status='unverified_source_quotation') for index in range(16)]
    source_text = '# 原文\n' + '\n\n'.join(
        '## 原文\n原文引用，未作数字核查。\n\n'
        f'> 政策原文片段{index:02}：本公司执行规定，原文金额为{100 + index}元。\n'
        '> \\# 财报分析（这是材料中的原文标题）。\n\n'
        f'来源：A年报.pdf；证据 ID：{reference["evidence_id"]}'
        for index, reference in enumerate(references))
    mixed_text = source_text + '\n\n# 财报分析\n## 营业收入同比\n数值：12.50%；公式：(本期－基期)／基期。'
    plain_text = '# 核查结果\n' + '普通数值回答仍需完整显示。' * 220 + '\n数值：88.00%；普通回答全文结尾。'
    unknown_text = '# 未识别的材料标题\n' + source_text + '\n无法可靠分区时保持全文显示。'
    nested_numeric = source_text + '\n\n## 营业收入同比\n数值：99.50%；数值区段没有独立顶层标题也不能被隐藏。'
    messages = [dict(role='assistant', text=text, time=0, message_id=message_id,
        source_references=refs) for text, message_id, refs in [
        (source_text, 'message-source', references),
        (mixed_text, 'message-mixed', references),
        (plain_text, 'message-numeric', []),
        (unknown_text, 'message-unknown', references),
        (nested_numeric, 'message-nested-numeric', references),
    ]]
    session = dict(session_id=sid, run_id=run_id, status='ready', messages=messages)
    run = dict(id=run_id, status='completed', out='C:/fixture/display', request={'company': '阅读体验示例'})
    result = dict(run_id=run_id, mode='kernel', results=[], claims=[], facts=[],
        summary={'verified': 0, 'violated': 0, 'abstained': 0})
    page.route('**/api/runs', lambda route: route.fulfill(json={'runs': [run]}))
    page.route(f'**/api/runs/{run_id}', lambda route: route.fulfill(json=run))
    page.route(f'**/api/runs/{run_id}/results', lambda route: route.fulfill(json=result))
    page.route(f'**/api/runs/{run_id}/events', lambda route: route.fulfill(json={'events': []}))
    page.route('**/api/sessions', lambda route: route.fulfill(json={'sessions': [session]}))
    page.route(f'**/api/sessions/{sid}', lambda route: route.fulfill(json=session))
    requests = []

    def evidence(route):
        query = parse_qs(urlparse(route.request.url).query)
        identifier = unquote(urlparse(route.request.url).path.rsplit('/', 1)[-1])
        requests.append((identifier, query))
        if query.get('format') == ['png']:
            route.fulfill(content_type='image/png', body=base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cCU0AAAAASUVORK5CYII='))
        else:
            route.fulfill(json={'page': 2, 'reference_status': 'unverified_source_quotation'})

    page.route('**/api/evidence/*?*', evidence)
    page.goto(base)
    page.locator('.history-row').filter(has_text='材料追问').click()
    page.get_by_role('heading', name='同一材料内追问', exact=True).wait_for()
    from playwright.sync_api import expect
    expect(page.locator('.message.assistant')).to_have_count(5)
    source, mixed, numeric, unknown = [page.locator('.message.assistant').nth(index) for index in range(4)]
    assert source.locator('.source-quote-reader').count() == 0
    assert '未作数字核查' in source.inner_text()
    assert '政策原文片段00' not in source.inner_text()
    assert source.locator('.source-reference-link').count() == 3
    assert mixed.locator('.source-quote-reader').count() == 0
    assert '12.50%' in mixed.inner_text() and '(本期－基期)／基期' in mixed.inner_text()
    assert numeric.locator('.source-quote-disclosure').count() == 0
    assert '88.00%' in numeric.inner_text() and '普通回答全文结尾' in numeric.inner_text()
    assert unknown.locator('.source-quote-disclosure').count() == 0
    assert '无法可靠分区时保持全文显示' in unknown.inner_text()
    nested = page.locator('.message.assistant').nth(4)
    assert nested.locator('.source-quote-disclosure').count() == 0
    assert '99.50%' in nested.inner_text()

    disclosure = source.get_by_role('button', name='展开完整原文', exact=True)
    assert disclosure.get_attribute('aria-expanded') == 'false'
    disclosure.press('Enter')
    reader = source.get_by_role('region', name='完整原文引用', exact=True)
    reader.wait_for()
    complete_text = reader.inner_text()
    assert all(f'政策原文片段{index:02}' in complete_text and f'{100 + index}元' in complete_text for index in range(16))
    assert '# 财报分析（这是材料中的原文标题）' in complete_text
    assert reader.evaluate('(node)=>node.scrollHeight > node.clientHeight')
    source.get_by_role('button', name='收起完整原文', exact=True).press('Space')
    assert source.locator('.source-quote-reader').count() == 0
    source.get_by_role('button', name='展开全部 16 条证据', exact=True).click()
    assert source.locator('.source-reference-link').count() == 16
    assert set(source.locator('.source-reference-link').evaluate_all('(nodes)=>nodes.map(node=>node.dataset.evidenceId)')) == {
        reference['evidence_id'] for reference in references}
    assert source.locator('.source-reference-group h4').all_text_contents() == [
        'A年报.pdf · 第 46 页', 'A年报.pdf · 第 47 页', 'B补充.pdf · 第 2 页']
    # Every retained ID remains individually selectable in the real Inspector.
    for reference in references:
        source.locator(f'button[data-evidence-id="{reference["evidence_id"]}"]').click()
        assert page.locator('.inspector-body .evidence-link').inner_text() == reference['evidence_id']
        if reference is references[-1]:
            page.locator('.inspector-body .evidence-link').click()
            expect(page.get_by_alt_text('财报原始页面与证据定位')).to_have_js_property('complete', True)
        page.get_by_role('button', name='关闭', exact=True).last.click()
    assert len(requests) == 2
    assert all(identifier == references[-1]['evidence_id'] and query.get('session') == [sid]
        and query.get('message') == ['message-source'] and query.get('run') == [run_id]
        for identifier, query in requests)
    source.get_by_role('button', name='收起证据', exact=True).click()
    assert source.locator('.source-reference-link').count() == 3
    mixed.get_by_role('button', name='展开完整原文', exact=True).click()
    assert all(f'政策原文片段{index:02}' in mixed.get_by_role('region', name='完整原文引用').inner_text() for index in range(16))
    assert '12.50%' in mixed.inner_text()
    assert not errors, errors
    assert not external, external


def test_logical_knowledge_evidence_displays_original_without_requesting_pdf(ui_host, browser_page):
    _, base = ui_host
    page, errors, external = browser_page
    run_id, sid, eid = 'knowledge-display-run', 'session-knowledge-display', 'kb:text:original'
    reference = {'evidence_id': eid, 'page': None, 'name': '会计准则.md',
                 'material_kind': 'accounting_knowledge', 'reference_status': 'unverified_source_quotation'}
    message = {'role': 'assistant', 'text': '# 原文\n外部会计材料原文，来源与有效性尚未独立核验。',
               'time': 0, 'message_id': 'message-0001', 'source_references': [reference]}
    session = {'session_id': sid, 'run_id': run_id, 'status': 'ready', 'messages': [message]}
    run = {'id': run_id, 'status': 'completed', 'out': 'C:/fixture/knowledge', 'request': {}}
    result = {'run_id': run_id, 'mode': 'kernel', 'results': [], 'claims': [], 'facts': [],
              'summary': {'verified': 0, 'violated': 0, 'abstained': 0}}
    page.route('**/api/runs', lambda route: route.fulfill(json={'runs': [run]}))
    page.route(f'**/api/runs/{run_id}', lambda route: route.fulfill(json=run))
    page.route(f'**/api/runs/{run_id}/results', lambda route: route.fulfill(json=result))
    page.route(f'**/api/runs/{run_id}/events', lambda route: route.fulfill(json={'events': []}))
    page.route('**/api/sessions', lambda route: route.fulfill(json={'sessions': [session]}))
    page.route(f'**/api/sessions/{sid}', lambda route: route.fulfill(json=session))
    requests = []
    original = '追溯调整法：<script>材料中的文字保持原文，不执行。</script>'

    def evidence(route):
        requests.append(parse_qs(urlparse(route.request.url).query))
        route.fulfill(json={'evidence_id': eid, 'page': None, 'image_available': False,
            'material_kind': 'accounting_knowledge', 'quote_text': original,
            'source_path': 'C:/fixture/准则.md', 'knowledge_lock': 'a'*64,
            'metadata': {'authority_status': 'declared_unverified', 'publish_date': '2006-02-15'},
            'effective_status': 'declared_dates_only'})

    page.route('**/api/evidence/*?*', evidence)
    page.goto(base)
    page.locator('.history-row').filter(has_text='材料追问').click()
    page.get_by_role('button', name='查看原文证据 · 文本片段', exact=True).click()
    page.locator('.inspector-body .evidence-link').click()
    page.get_by_role('heading', name='原文片段', exact=True).wait_for()
    from playwright.sync_api import expect
    expect(page.locator('.inspector-body')).to_contain_text(original)
    assert '第 null 页' not in page.locator('.inspector-body').inner_text()
    assert page.locator('.evidence-image').count() == 0
    assert len(requests) == 1 and 'format' not in requests[0]
    assert requests[0]['session'] == [sid] and requests[0]['message'] == ['message-0001']
    assert not errors, errors
    assert not external, external
