"""Scoped offline verification after relocation, without rebuilding the EXE."""
from __future__ import annotations
import argparse,hashlib,json,os,shutil,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,default=ROOT/'evidence/relocation/offline-smoke');a=p.parse_args()
 out=a.out.resolve();out.mkdir(parents=True,exist_ok=False)
 from mingjing.config import AppConfig,ensure_workspace,save_config,app_root
 from scripts.dev3_delivery_smoke import start,homepage_kernel
 from scripts.delivery_smoke import clean_environment,shutdown,force_cleanup
 from mingjing.harness_client import HarnessRuntime
 from scripts.build_frontend import verify,verify_smart
 profile=app_root();before={str(q):sha(q) for q in [profile/'config.json',profile/'secrets.json',profile/'secrets.key'] if q.is_file()}
 receipt={'schema':'mingjing.workspace-relocation-smoke.v1','started_utc':datetime.now(timezone.utc).isoformat(),
          'real_model_requests':0,'exe_rebuilt':False,'checks':[]}
 def passed(name,**fields):receipt['checks'].append({'name':name,'status':'passed',**fields})
 try:
  import mingjing,mingjing.server,fitz
  assert Path(mingjing.__file__).resolve().is_relative_to(ROOT)
  assert Path(mingjing.server.__file__).resolve().is_relative_to(ROOT)
  verify(ROOT);verify_smart(ROOT)
  passed('module_ui_pdf_sources',root=str(ROOT),pdf=True)
  runtime=HarnessRuntime(out/'runtime-probe',lambda *_:{})
  try:hello=runtime.start();passed('native_runtime_offline',node_version=hello.get('node_version'))
  finally:runtime.close()
  for source in (False,True):
   name='source' if source else 'exe';case=out/name;case.mkdir()
   home=Path(tempfile.mkdtemp(prefix='mingjing-relocation-offline-'));proc=stream=record=None
   try:
    cfg=AppConfig();cfg.workspace.data_root=str(home/'workspace');cfg.workspace.output_root=str(home/'workspace/runs')
    cfg.workspace.temp_root=str(home/'tmp');cfg.workspace.knowledge_root=str(home/'workspace/knowledge')
    cfg.logging.directory=str(home/'logs');cfg.server.host='127.0.0.1';cfg.server.port=0
    cfg.server.auto_open_browser=False;cfg.server.bearer_token_required=False
    cfg.security.allowed_roots=[str(home),str(ROOT/'samples')];cfg.security.allow_external_paths=True
    cfg.harness.enabled=False;ensure_workspace(cfg)
    # A configured knowledge directory must exist before task validation.
    Path(cfg.workspace.knowledge_root).mkdir(parents=True,exist_ok=True)
    save_config(cfg,home/'config.json')
    env=clean_environment(home,home)
    executable=ROOT/'artifacts/program/MingJing.exe'
    build=json.loads((executable.parent/'MingJing-build.json').read_text('utf-8'))
    assert sha(executable)==build['sha256']
    proc,stream,api,record=start(SimpleNamespace(source=source),executable,home,env,case/'launcher.log')
    assert api.json('/api/health')['version']=='1.0.0.dev3'
    result,_=homepage_kernel(api,case,180)
    passed(name+'_offline_upload_report_replay',**result,exe_sha256=build['sha256'] if not source else None)
    api.json('/api/shutdown','POST',{})
    proc.wait(timeout=30);stream.close();proc=stream=None
   finally:
    if proc is not None:force_cleanup(proc,stream,record)
    assert home.parent==Path(tempfile.gettempdir()).resolve() and home.name.startswith('mingjing-relocation-offline-')
    assert not home.is_symlink() and not home.is_junction()
    shutil.rmtree(Path(chr(92)*2+'?'+chr(92)+str(home)))
    assert not home.exists()
  assert all(Path(q).is_file() and sha(Path(q))==h for q,h in before.items())
  receipt.update(status='passed_scoped_offline_relocation',profile_unchanged=True)
 except Exception as e:
  receipt.update(status='failed',error=type(e).__name__+': '+str(e));raise
 finally:
  receipt['finished_utc']=datetime.now(timezone.utc).isoformat()
  (out/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n','utf-8')
 print(json.dumps(receipt,ensure_ascii=False))
 return 0

if __name__=='__main__':raise SystemExit(main())
