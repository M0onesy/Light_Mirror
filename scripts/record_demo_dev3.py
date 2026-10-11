"""Record real browser operations on fictional samples; never invent results."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    args = parser.parse_args()
    uri = urlparse(args.url)
    if uri.scheme != 'http' or uri.hostname not in {'127.0.0.1', 'localhost'} or uri.username:
        raise ValueError('Record only the local application')
    args.out.mkdir(parents=True, exist_ok=False)
    from playwright.sync_api import sync_playwright, expect
    errors, steps = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={'width': 1440, 'height': 900},
                                      record_video_dir=str(args.out / 'recording'),
                                      record_video_size={'width': 1440, 'height': 900}, locale='zh-CN')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle')

        def mark(name, duration=3):
            page.evaluate("""title => {
              let label = document.getElementById('demo-recording-caption');
              if (!label) {
                label = document.createElement('div');
                label.id = 'demo-recording-caption';
                label.style.cssText = 'position:fixed;bottom:18px;left:50%;transform:translateX(-50%);'
                  + 'z-index:100000;padding:12px 24px;border-radius:12px;background:#102338;'
                  + 'color:white;font:18px sans-serif;box-shadow:0 2px 16px #0005;pointer-events:none';
                document.body.appendChild(label);
              }
              label.textContent = '明镜 · ' + title + '　｜　虚构样例，实际运行';
            }""", name)
            steps.append({'name': name, 'time': time.time()})
            page.screenshot(path=str(args.out / (f'{len(steps):02d}-' + name + '.png')))
            page.wait_for_timeout(duration * 1000)

        mark('上传即用首页')
        page.locator('#file').set_input_files([
            str(ROOT / 'samples/smart/星河智造_2023年年度报告.pdf'),
            str(ROOT / 'samples/smart/虚构证券_2024-04-25_星河智造深度报告.pdf')])
        expect(page.locator('#queue tbody')).to_contain_text('已入库', timeout=60000)
        expect(page.locator('#start')).to_be_enabled(timeout=60000)
        mark('虚构材料自动识别')
        page.locator('details.options summary').click()
        page.locator('#optLlm').uncheck()
        mark('可选择标准或审慎策略')
        page.locator('#start').click()
        page.wait_for_selector('#resultBody:not([hidden])', timeout=180000)
        mark('真实内核执行结果', 6)
        page.locator('#tab-wrong [data-cid]').first.click()
        page.wait_for_selector('#drawer:not([hidden])')
        mark('公式操作数与原文证据', 6)
        page.locator('#dClose').click()
        page.locator('#rReplay').click()
        expect(page.locator('#rReplay')).to_be_enabled(timeout=60000)
        mark('离线数字重放')
        page.locator('#rTabs [data-tab="all"]').click()
        mark('逐处核查账本')
        page.locator('#nav [data-view="kb"]').click()
        page.wait_for_timeout(1000)
        mark('知识库与来源材料')
        page.locator('#nav [data-view="history"]').click()
        page.wait_for_timeout(1000)
        mark('冻结任务历史')
        page.locator('#nav [data-view="settings"]').click()
        page.wait_for_timeout(1000)
        mark('多供应商设置与能力测试')
        video = page.video
        page.close()
        context.close()
        webm = Path(video.path())
        browser.close()
    target = args.out / '明镜新版_实际操作演示.mp4'
    subprocess.run([str(args.ffmpeg.resolve(strict=True)), '-hide_banner', '-loglevel', 'error', '-y',
                    '-i', str(webm), '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', str(target)], check=True, timeout=120)
    if target.stat().st_size > 500_000_000:
        raise ValueError('Competition video exceeds 500 MB')
    probe = subprocess.run([str(args.ffmpeg), '-hide_banner', '-i', str(target),
                            '-f', 'null', '-'], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', check=True, timeout=120)
    matched = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', probe.stderr)
    if not matched:
        raise ValueError('MP4 duration could not be verified')
    duration_s = int(matched[1]) * 3600 + int(matched[2]) * 60 + float(matched[3])
    if duration_s > 300:
        raise ValueError('Competition video exceeds five minutes')
    receipt = {'status': 'passed' if not errors else 'failed', 'source': 'real_browser_operations',
               'data': 'fictional_distributable_samples_not_real_financial_quality_evidence',
               'mode': 'kernel_demo', 'steps': steps, 'page_errors': errors, 'file': target.name,
               'bytes': target.stat().st_size, 'duration_s': duration_s,
               'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
               'limitations': ['Silent operation video; real API acceptance is in separate receipts']}
    (args.out / 'video-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(receipt, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == '__main__':
    raise SystemExit(main())
