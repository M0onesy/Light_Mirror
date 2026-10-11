"""Real browser view continuity; controlled replies make no API-quality claim."""
from test_harness_web_ui import browser_page, ui_host


def _host_routes(page):
    runs = [dict(id=f'continuity-run-{name}', status='completed', out=f'C:/fixture/{name}',
                 request={'company': f'continuity-company-{name}'}) for name in ('a', 'b')]
    sessions, requests, created = {}, [], []
    page.route('**/api/runs', lambda route: route.fulfill(json={'runs': runs}))
    for run in runs:
        page.route(f'**/api/runs/{run["id"]}', lambda route, _request=None, item=run: route.fulfill(json=item))
        page.route(f'**/api/runs/{run["id"]}/events', lambda route: route.fulfill(json={'events': []}))
        page.route(f'**/api/runs/{run["id"]}/reparse-packets', lambda route: route.fulfill(json={'packets': []}))
        page.route(f'**/api/runs/{run["id"]}/results', lambda route, _request=None, item=run: route.fulfill(json={
            'run_id': item['id'], 'mode': 'kernel', 'claims': [], 'facts': [], 'results': [],
            'summary': {'verified': 0, 'violated': 0, 'abstained': 0}}))

    def session_route(route):
        if route.request.method == 'GET':
            route.fulfill(json={'sessions': list(reversed(list(sessions.values())))})
            return
        run_id = route.request.post_data_json['run_id']
        sid = f'session-continuity-{len(created):04}'
        state = dict(session_id=sid, run_id=run_id, status='ready', messages=[])
        sessions[sid] = state
        created.append(sid)
        requests.append(('create', run_id, sid))
        route.fulfill(json=state)

    def session_detail(route):
        sid = route.request.url.rsplit('/', 1)[-1]
        route.fulfill(json=sessions[sid])

    def message_route(route):
        sid = route.request.url.split('/')[-2]
        question = route.request.post_data_json['question']
        state = sessions[sid]
        requests.append(('message', state['run_id'], sid, question))
        state['messages'].extend([dict(role='user', text=question, time=1),
            dict(role='assistant', text=f'Frozen reply: {question}', time=1,
                 message_id=f'message-{len(state["messages"]):04}')])
        route.fulfill(json=state)

    page.route('**/api/sessions', session_route)
    page.route('**/api/sessions/*', session_detail)
    page.route('**/api/sessions/*/messages', message_route)
    return sessions, requests, created


def _open_run(page, name):
    page.locator('.history-row').filter(has_text=f'continuity-company-{name}').click()
    page.get_by_role('button', name='材料追问', exact=True).click()
    page.get_by_role('heading', name='同一材料内追问', exact=True).wait_for()


def _ask(page, question):
    page.get_by_label('金融问题', exact=True).fill(question)
    page.get_by_role('button', name='发送问题 ↑', exact=True).click()
    page.get_by_text(f'Frozen reply: {question}', exact=True).wait_for()


def test_result_chat_and_task_switches_keep_registered_session_history(ui_host, browser_page):
    _, base = ui_host
    page, errors, external = browser_page
    sessions, requests, created = _host_routes(page)
    page.goto(base.rstrip('/') + '/classic')
    _open_run(page, 'a')
    _ask(page, 'first-a')
    first = created[0]
    # The new session becomes history immediately, before any manual refresh.
    assert page.locator('.history-row').filter(has_text='材料追问').count() == 1
    page.get_by_role('button', name='核查结果', exact=True).click()
    page.get_by_role('button', name='材料追问', exact=True).click()
    page.get_by_text('Frozen reply: first-a', exact=True).wait_for()
    _ask(page, 'followup-a')
    assert created == [first]
    assert all(row[2] == first for row in requests if row[0] == 'message')
    _open_run(page, 'b')
    _ask(page, 'first-b')
    second = created[1]
    assert sessions[second]['run_id'] == 'continuity-run-b'
    _open_run(page, 'a')
    page.get_by_text('Frozen reply: first-a', exact=True).wait_for()
    page.get_by_text('Frozen reply: followup-a', exact=True).wait_for()
    assert page.get_by_text('Frozen reply: first-b', exact=True).count() == 0
    _ask(page, 'third-a')
    assert len(created) == 2
    assert requests[-1][1:3] == ('continuity-run-a', first)
    # A browser reload reconstructs the per-task choice from persisted history.
    page.reload()
    _open_run(page, 'a')
    page.get_by_text('Frozen reply: third-a', exact=True).wait_for()
    assert len(created) == 2
    assert not errors, errors
    assert not external, external


def test_authoritative_history_refresh_removes_deleted_session_selection(ui_host, browser_page):
    _, base = ui_host
    page, errors, external = browser_page
    sessions, requests, created = _host_routes(page)
    page.goto(base.rstrip('/') + '/classic')
    _open_run(page, 'a')
    _ask(page, 'before-removal')
    first = created[0]
    del sessions[first]
    page.get_by_role('button', name='刷新历史', exact=True).click()
    from playwright.sync_api import expect
    expect(page.locator('.history-row').filter(has_text='材料追问')).to_have_count(0)
    expect(page.get_by_text('Frozen reply: before-removal', exact=True)).to_have_count(0)
    _ask(page, 'after-removal')
    assert len(created) == 2
    assert requests[-1][2] == created[1]
    assert not errors, errors
    assert not external, external
