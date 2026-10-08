"""Local acceptance runner. Reads the existing encrypted profile in memory.

No key, request authorization header or provider response content is exported.
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.cli import main
from mingjing.config import ConfigManager

def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='runs/real-deepseek-source')
    options = parser.parse_args()
    project = Path(__file__).resolve().parents[2]
    draft = next(project.rglob('*AP202404221630960822.pdf'))
    corpus = next(p for p in project.rglob('2024-04-15_2023\u5e74\u5e74\u5ea6\u62a5\u544a.pdf') if any(part.startswith('920493_') for part in p.parts))
    manager = ConfigManager()
    config = manager.get()
    if not config.llm.enabled:
        raise SystemExit('The saved Web profile has LLM disabled.')
    secret = manager.get_secret(config.llm.api_key_ref)
    if not secret:
        raise SystemExit('The saved Web profile has no readable API secret.')
    env = {
        'MINGJING_LLM_BASE_URL': config.llm.base_url,
        'MINGJING_LLM_MODEL': config.llm.model,
        'MINGJING_LLM_API_KEY': secret,
        'MINGJING_LLM_CHAT_PATH': config.llm.chat_path,
        'MINGJING_LLM_TIMEOUT_S': str(config.llm.timeout_s),
        'MINGJING_LLM_MAX_TOKENS': str(config.llm.max_tokens),
        'MINGJING_LLM_TEMPERATURE': str(config.llm.temperature),
        'MINGJING_LLM_THINKING': str(config.llm.thinking),
    }
    before = {key: os.environ.get(key) for key in env}
    try:
        os.environ.update(env)
        return main(['check', '--draft', str(draft), '--corpus', str(corpus), '--out', options.out,
                     '--company', '\u5e76\u884c\u79d1\u6280', '--period', '2023Y',
                     '--report-date', '2024-04-22', '--mode', 'agent'])
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

if __name__ == '__main__':
    raise SystemExit(run())
