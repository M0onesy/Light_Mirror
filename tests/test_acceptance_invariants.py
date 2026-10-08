import ast
import json
from argparse import Namespace
from pathlib import Path
from mingjing.cli import command_check
from mingjing.rules import validate_rulepack

ROOT = Path(__file__).resolve().parents[1]

def test_kernel_import_graph_has_no_transport_or_model_dependencies():
    blocked = {'socket','urllib','http','requests','httpx','openai','mcp','streamlit','fastapi','server','llm','subprocess'}
    for name in ('models','compute','facts','judge','mentions','analysis'):
        tree = ast.parse((ROOT/'mingjing'/f'{name}.py').read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            imports = [x.name for x in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
            assert not any(x.split('.')[0] in blocked for x in imports), name

def test_rule_cards_are_complete_and_tampering_is_detected(tmp_path):
    assert validate_rulepack()['status'] == 'ok'
    cards = json.loads((ROOT/'rulepack/cards.json').read_text(encoding='utf-8'))
    assert {f'E{i}' for i in range(1,10)} <= {c['error_type'] for c in cards}
    cards[0]['formula'] = 'fabricated'
    (tmp_path/'cards.json').write_text(json.dumps(cards),encoding='utf-8')
    assert validate_rulepack(tmp_path)['status'] == 'failed'

def test_model_invented_fact_and_verdict_cannot_change_numeric_outputs(tmp_path, monkeypatch, capsys):
    from mingjing import llm
    source = (ROOT/'samples/demo_draft_defined.md').read_text(encoding='utf-8')
    monkeypatch.setattr(llm,'decompose_with_metadata',lambda text: ([{'source':source,'candidate_value':'999999999','verdict':'verified','formula':'evil'}],{'model':'fake-for-isolation-test','chunks':1}))
    def run(mode):
        out = tmp_path/mode
        args = Namespace(draft=str(ROOT/'samples/demo_draft_defined.md'),corpus=str(ROOT/'samples/corpus'),out=str(out),report_date='2025-03-31',company='明镜示例公司',period='2024Y',facts=None,mode=mode,run_id='test-'+mode)
        assert command_check(args) == 0
        return json.loads((out/'results.json').read_text(encoding='utf-8'))
    kernel, agent = run('kernel'),run('agent')
    assert agent['model']['status'] == 'success'
    assert kernel['facts'] == agent['facts']
    assert kernel['results'] == agent['results']
    assert agent['summary'] == {'verified':4,'violated':5,'abstained':1}
    for payload in (kernel,agent):
        html = (tmp_path/payload['mode']/'report.html').read_text(encoding='utf-8')
        for item in payload['results']:
            assert item['claim_id'] in html
        assert all(r['evidence'] for r in payload['results'] if r['status'] != 'abstained')
