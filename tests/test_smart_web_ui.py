"""直传工作台浏览器端到端测试（Python 版，Playwright + Chromium）。

覆盖：一键演示 → 结果页（分级构成、有误与修改建议、推定与假设、补材分组）→ 证据抽屉（推定、研报原页）
→ 重放验证 → 财报分析（要点、勾稽检查）→ 设置页保存模型配置 → 知识库留痕哈希链校验。
没有 Playwright 或 Chromium 时自动跳过；`python -m unittest tests.test_smart_web_ui` 或 pytest 均可运行。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SAMPLES = ROOT / 'samples' / 'smart'


def _browser_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


@unittest.skipUnless((SAMPLES / 'seeded_errors.json').is_file() and _browser_ready(), '需要 Playwright、Chromium 与 samples/smart 样例')
class SmartWebUiTests(unittest.TestCase):
    def test_demo_check_analysis_settings_and_knowledge_base(self):
        from playwright.sync_api import sync_playwright
        from mingjing.smart.web import make_server
        tmp = Path(tempfile.mkdtemp(prefix='mj-ui-'))
        server = make_server('127.0.0.1', 0, str(tmp))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/'
        errors: list[str] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                # 页面自身的 CSP 禁止字符串求值；测试脚本的等待条件需要求值，因此只在测试上下文中绕过 CSP
                context = browser.new_context(viewport={'width': 1360, 'height': 900}, bypass_csp=True)
                page = context.new_page()
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.on('console', lambda msg: errors.append(f'{msg.text} ({msg.location.get("url", "")})') if msg.type == 'error' else None)
                page.goto(url)
                page.click('#runDemo')
                page.wait_for_selector('#resultBody:not([hidden])', timeout=180000)
                self.assertEqual(page.locator('#rItem option').count(), 3)                    # 三份虚构研报
                self.assertGreaterEqual(page.locator('#kTiers .tier-row').count(), 3)        # 分级构成条
                wrong = int(page.inner_text('#kBad'))
                self.assertIn(wrong, (6, 8, 9))
                page.click('#rTabs button[data-tab="assume"]')
                self.assertIn('默认合并口径', page.inner_text('#tab-assume'))
                page.click('#rTabs button[data-tab="wrong"]')
                page.locator('#tab-wrong .card').first.click()
                page.wait_for_selector('#drawer:not([hidden])')
                drawer = page.inner_text('#drawer')
                self.assertIn('推定与假设', drawer)
                self.assertIn('研报原文位置', drawer)
                page.click('#dClose')
                page.click('#rReplay')
                page.wait_for_function("document.querySelector('#toast') && document.querySelector('#toast').textContent.includes('离线重放一致')", timeout=120000)
                ids = page.evaluate("async () => (await api('/api/kb')).docs.filter((d) => d.doc_type === 'annual_report' && d.company === '星河智造').map((d) => d.doc_id)")
                self.assertEqual(len(ids), 2)
                page.evaluate("async (ids) => { const r = await api('/api/smart/run', { method: 'POST', body: JSON.stringify({ doc_ids: ids, mode: 'analysis', options: { kb_lookup: false } }), headers: { 'Content-Type': 'application/json' } }); watch(r.run_id); }", ids)
                page.wait_for_selector('#analysisView:not([hidden]) #checkTable tbody tr', timeout=120000)
                self.assertIn('不一致 0', page.inner_text('#aCheckSummary'))
                self.assertFalse(page.locator('#aHighlights').is_hidden())
                page.click('#nav button[data-view="settings"]')
                page.wait_for_function("document.querySelector('#sStatus').textContent.includes('管线版本')")
                page.check('#sEnabled')
                page.fill('#sBase', 'http://127.0.0.1:9/v1')
                page.fill('#sModel', 'mock-model')
                page.fill('#sKey', 'sk-ui-test')
                page.click('#sSave')
                page.wait_for_function("document.querySelector('#sEffective').textContent.includes('mock-model')")
                self.assertEqual(page.input_value('#sKey'), '')                               # 密钥保存后不回显
                page.click('#nav button[data-view="kb"]')
                page.wait_for_function("document.querySelector('#journalState').textContent.includes('哈希链完整')")
                page.set_viewport_size({'width': 390, 'height': 844})
                page.click('#nav button[data-view="work"]')
                overflow = page.evaluate('document.documentElement.scrollWidth - document.documentElement.clientWidth')
                self.assertLessEqual(overflow, 2)                                                # 窄屏无横向溢出
                browser.close()
            self.assertEqual(errors, [])
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_evidence_drawer_preserves_source_ids_and_requests_only_supported_images(self):
        from playwright.sync_api import sync_playwright
        from mingjing.smart.web import make_server
        tmp = Path(tempfile.mkdtemp(prefix='mj-ui-evidence-'))
        server = make_server('127.0.0.1', 0, str(tmp))
        record = server.smart.kb.add_file(SAMPLES / '星河智造_2023年年度报告.pdf', origin='ui-test')
        prefix = record['doc_id'][:12]
        valid = [f'{prefix}:p1:text', f'{prefix}:p1:0,0,0,0:deadbeef']
        invalid = [f'{prefix}:p1:table', f'{prefix}:p1:text:extra',
                   f'{prefix}:p1:0,0,0,0:deadbee', f'{prefix}:p10000:text',
                   f'{prefix}:p1:text\n']
        threading.Thread(target=server.serve_forever, daemon=True).start()
        errors: list[str] = []
        requested: list[str] = []
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                context = browser.new_context(bypass_csp=True)
                page = context.new_page()
                page.on('pageerror', lambda exc: errors.append(str(exc)))
                page.on('console', lambda msg: errors.append(msg.text) if msg.type == 'error' else None)
                page.on('response', lambda response: errors.append(f'{response.status} {response.url}') if response.status >= 400 else None)
                page.on('request', lambda request: requested.append(unquote(urlparse(request.url).path))
                        if '/api/kb/evidence/' in request.url else None)
                page.goto(f'http://127.0.0.1:{server.server_port}/')
                for source_id in invalid:
                    page.evaluate("""(evidence) => {
                        state.results = {items: [{cid: 'evidence-format-boundary', status: 'verified',
                            text: '来源证据', evidence}]};
                        openDrawer('evidence-format-boundary');
                    }""", [source_id, *valid])
                    self.assertIn(source_id.strip(), page.inner_text('#dBody .evidence'))
                    self.assertEqual(page.locator('#dBody .evidence img').evaluate_all(
                        '(images) => images.map((image) => image.getAttribute("src"))'),
                        [f'/api/kb/evidence/{quote(identifier, safe="")}.png' for identifier in valid])
                    page.wait_for_function("""() => {
                        const images = [...document.querySelectorAll('#dBody .evidence img')];
                        return images.length === 2 && images.every((image) => image.complete && image.naturalWidth > 0);
                    }""")
                self.assertEqual(set(requested), {f'/api/kb/evidence/{identifier}.png' for identifier in valid})
                browser.close()
            self.assertEqual(errors, [])
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
