"""Copy the locked Harness UI source modules, preserving exact upstream hashes.

This is a development source sync, not an application/runtime/package build.
Only the reviewed browser modules are copied; no Host plugins or credentials.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/harness"
DESTINATION = ROOT / 'frontend' / 'vendor' / 'harness'


def adapt_frame(original: str) -> str:
    """Replace Cordis seats while preserving the upstream rendering and geometry."""
    adapted = re.sub(r"import type \{\s*PropsLocale, PropsRenderSlots, PropsRuntime, PropsStore,\s*\} from '@deepseek-ai/dsh-client-ui-slots'\n", '', original)
    adapted = adapted.replace("import { DocumentTitle } from './DocumentTitle.tsx'\n", '')
    adapted = adapted.replace("import type { createLayoutStore } from './stores.ts'\n", '')
    adapted = adapted.replace("from './columns.ts'", "from '../vendor/harness/packages/client/ui-layout/src/client/columns.ts'")
    adapted = adapted.replace("from './AppFrame.module.css'", "from '../vendor/harness/packages/client/ui-layout/src/client/AppFrame.module.css'")
    start, end = adapted.index('/** Full composed props:'), adapted.index('/** Center column')
    adapted = adapted[:start] + '''export interface FrameLayout {
  sidebar: number; viewportWidth: number; narrowExpanded: boolean; rightbar: number | null;
  rightbarShown: boolean; rightbarTrack: boolean; rightbarFullscreen: boolean; rightbarInstant: boolean;
}
type Slot = 'sidebar' | 'main' | 'rightbar' | 'shell.overlay';
export interface AppFrameProps {
  useStore: <T>(selector: (state: {layoutInfo: FrameLayout}) => T) => T;
  usePanelInfo: <T>(selector: (info: {activePanelId: string | null}) => T) => T;
  useSessions?: unknown;
  actions: {setViewportWidth: (width:number)=>void; setSidebar: (width:number)=>void; setRightbar: (width:number)=>void};
  renderSlot: (slot:Slot, data:Record<string, unknown>, options?:{entryKey:string})=>ReactNode;
  t: (key:string)=>string;
}

''' + adapted[end:]
    adapted = adapted.replace("Pick<PropsRuntime<'root'>, 'usePanelInfo'> & PropsRenderSlots<'main'>", "Pick<AppFrameProps, 'usePanelInfo' | 'renderSlot'>")
    adapted = re.sub(r'<DocumentTitle\s+productTitle=\{productTitle\}\s+useSessions=\{useSessions\}\s+usePanelInfo=\{usePanelInfo\}\s*/>', '{/* Document title is owned by the financial task adapter. */}', adapted)
    return '// Adapted from Harness 0.1.6-alpha.2 AppFrame (MIT).\n// Original source SHA is recorded in vendor/harness/manifest.json.\n' + adapted


def main():
    global UPSTREAM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, default=UPSTREAM)
    args = parser.parse_args()
    UPSTREAM = args.upstream.resolve(strict=True)
    from scripts.upstream_source import verify_upstream
    verify_upstream(UPSTREAM)
    version = json.loads((UPSTREAM/'apps/web/package.json').read_text(encoding='utf-8'))['version']
    if version != '0.1.6-alpha.2':
        raise ValueError('Harness UI snapshot differs from the approved runtime snapshot')
    snapshot_path = ROOT/'runtime/harness/source_snapshot.json'
    snapshot_raw = snapshot_path.read_bytes()
    snapshot = json.loads(snapshot_raw)
    if version != snapshot['upstream_version']:
        raise ValueError('Harness UI and the locked runtime archive have different versions')
    locked_files = {entry['path']:entry for entry in snapshot['files']}
    files = []
    for name in ('ui-primitives', 'ui-dockkit'):
        base = UPSTREAM/'packages/client'/name/'src'
        files.extend(path for path in base.rglob('*') if path.is_file())
    files.extend((UPSTREAM/'packages/client/ui-theme/src/styles').glob('*.css'))
    files.extend(UPSTREAM/path for path in (
        'packages/client/ui-layout/src/client/AppFrame.tsx',
        'packages/client/ui-layout/src/client/AppFrame.module.css',
        'packages/client/ui-layout/src/client/columns.ts',
        'packages/client/web/src/base.css',
        'packages/util/brand/src/index.ts',
        'LICENSE',
    ))
    manifest = []
    # Verify the complete selected source list before copying any file. A
    # matching version alone cannot identify the approved source archive.
    for path in files:
        relative = path.relative_to(UPSTREAM).as_posix()
        expected = locked_files.get(relative)
        if not expected or path.stat().st_size != expected['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError(f'Harness UI source differs from the locked runtime archive: {relative}')
    for path in sorted(files):
        relative = path.relative_to(UPSTREAM)
        target = DESTINATION/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest.append({'path':relative.as_posix(),'bytes':path.stat().st_size,
                         'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    # Reuse the upstream frame and geometry unchanged in the rendering body;
    # replace only Cordis hook seats with the local financial application's
    # explicit React data/actions. The original remains pinned above.
    original = (DESTINATION/'packages/client/ui-layout/src/client/AppFrame.tsx').read_text(encoding='utf-8')
    target = ROOT/'frontend/src/HarnessFrame.tsx'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(adapt_frame(original), encoding='utf-8')
    (DESTINATION/'manifest.json').write_text(json.dumps({
        'version':version,'upstream_commit':None,'source_kind':'local_archive_snapshot',
        'source_archive_sha256':snapshot['source_sha256'],
        'source_snapshot_sha256':hashlib.sha256(snapshot_raw).hexdigest(),
        'sync_script_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'license':'MIT','files':manifest,
        'adapter':{'path':target.relative_to(ROOT).as_posix(), 'bytes':target.stat().st_size,
                   'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                   'source':'packages/client/ui-layout/src/client/AppFrame.tsx',
                   'method':'adapt_frame: Cordis prop seats and DocumentTitle replaced; rendering and geometry preserved'},
        'modifications':'Original files are unchanged. Application adapters live outside vendor/harness.'},
        ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'version':version,'files':len(files),'bytes':sum(f['bytes'] for f in manifest)}))


if __name__ == '__main__':
    main()
