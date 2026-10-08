"""Build-time JavaScript syntax and report consistency checks."""
import json
import re
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
targets = [root/'web/index.html', root/'runs/new-demo-artifacts-final/report.html']
for path in targets:
    html = path.read_text(encoding='utf-8')
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    for script in scripts:
        with tempfile.TemporaryDirectory(prefix='mingjing-js-check-') as folder:
            file = Path(folder)/'script.js'
            file.write_text(script, encoding='utf-8')
            result = subprocess.run(['node','--check',str(file)],capture_output=True,text=True)
            if result.returncode:
                print(result.stderr)
                raise SystemExit(result.returncode)
    print(json.dumps({'asset':path.relative_to(root).as_posix(),'syntax':'passed'}))
