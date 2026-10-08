import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.cli import _read_draft
from mingjing.mentions import extract_mentions

root = Path(__file__).resolve().parents[2]
draft = next(root.rglob('*AP202404221630960822.pdf'))
text = _read_draft(draft)
target = Path(__file__).resolve().parents[1] / 'runs' / 'acceptance-work'
target.mkdir(parents=True, exist_ok=True)
(target / 'real_draft.txt').write_text(text, encoding='utf-8')
print(json.dumps({'chars': len(text), 'path': str(target)}, ensure_ascii=True))
claims = extract_mentions(text, '\u5e76\u884c\u79d1\u6280', '2023Y')
(target / 'extracted.json').write_text(json.dumps([c.to_dict() for c in claims], ensure_ascii=False, indent=2), encoding='utf-8')
for c in claims:
    print(json.dumps({'id': c.claim_id, 'account': c.account, 'period': c.period, 'relation': c.relation, 'value': c.candidate_value, 'text': c.text}, ensure_ascii=True))
