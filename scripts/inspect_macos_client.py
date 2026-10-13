"""Read installed macOS client metadata; never launch, attach or alter apps."""
import hashlib
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import shutil
import sys


def inspect(snapshot=False):
    if sys.platform != 'darwin':
        return {'state':'not_macos','clients':[],'native_send_verified':False}
    clients = []
    for name in ('WeChat.app','Weixin.app'):
        app = Path('/Applications') / name
        info = app / 'Contents/Info.plist'
        if not info.is_file():
            continue
        with info.open('rb') as source:
            metadata = plistlib.load(source)
        executable = (app / 'Contents/MacOS' / metadata['CFBundleExecutable']).resolve()
        if not executable.is_relative_to(app.resolve()):
            raise ValueError('Client executable escapes application bundle')
        with executable.open('rb') as source:
            digest = hashlib.file_digest(source,'sha256').hexdigest()
        architectures = subprocess.check_output(['/usr/bin/lipo','-archs',str(executable)],text=True).strip().split()
        entry = {'application':name,'version':metadata.get('CFBundleShortVersionString'),
                 'build':metadata.get('CFBundleVersion'),'architectures':architectures,'sha256':digest}
        module = (app / 'Contents/Resources/wechat.dylib').resolve()
        if not module.is_relative_to(app.resolve()):
            raise ValueError('Client module escapes application bundle')
        if module.is_file():
            with module.open('rb') as source:
                module_digest = hashlib.file_digest(source,'sha256').hexdigest()
            entry['native_module'] = {'path':'Contents/Resources/wechat.dylib',
                'sha256':module_digest,'bytes':module.stat().st_size,
                'architectures':subprocess.check_output(
                    ['/usr/bin/lipo','-archs',str(module)],text=True).strip().split()}
        if snapshot:
            target = Path('.build/mac-client-binary') / name / executable.name
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(executable,target)
            with target.open('rb') as source:
                if hashlib.file_digest(source,'sha256').hexdigest() != digest:
                    raise ValueError('Client changed during snapshot')
            entry['snapshot'] = str(target)
            entry['bytes'] = target.stat().st_size
            if module.is_file():
                module_target = target.parent / 'wechat.dylib'
                shutil.copyfile(module,module_target)
                with module_target.open('rb') as source:
                    if hashlib.file_digest(source,'sha256').hexdigest() != module_digest:
                        raise ValueError('Client module changed during snapshot')
                entry['native_module']['snapshot'] = str(module_target)
        clients.append(entry)
    return {'state':'found' if clients else 'not_installed','clients':clients,'native_send_verified':False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',action='store_true')
    result = inspect(parser.parse_args().snapshot)
    output = Path('.build/mac-client-environment.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
