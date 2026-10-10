"""Builds the voice engine packages (worker.exe + runtime + motor.json) as release zips.

    py -3 build_engines.py            all engines
    py -3 build_engines.py ema        one engine (folder name under engines/)

Output: dist-engines/cevirgec-ses-<id>-win64.zip with one folder <id>/ inside, to be installed in
Çevirgeç with Ayarlar → Sesli okuma → "Dosyadan kur". Packages are built locally and not published:
EMA is packaged with whatever PyTorch is installed (CUDA PyTorch gives a GPU-capable, multi-GB package).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / 'build-engines'
DIST = ROOT / 'dist-engines'
# Extra PyInstaller options per engine folder: data files and native libraries the analysis misses.
OPTIONS = {
    'ema': ['--collect-all', 'ema_lightning', '--collect-all', 'normalizer_tr', '--collect-data', 'huggingface_hub'],
    # voxcpm pulls in gradio/funasr/modelscope/datasets for its demo and training; reading needs none of them.
    'voxcpm': ['--collect-all', 'voxcpm', '--collect-all', 'normalizer_tr', '--collect-data', 'librosa',
               '--collect-data', 'huggingface_hub', '--collect-data', 'transformers', '--copy-metadata', 'transformers',
               '--copy-metadata', 'tokenizers', '--copy-metadata', 'huggingface_hub', '--copy-metadata', 'safetensors',
               '--copy-metadata', 'torch', '--copy-metadata', 'numpy', '--copy-metadata', 'tqdm', '--copy-metadata', 'regex',
               '--copy-metadata', 'packaging', '--copy-metadata', 'filelock', '--copy-metadata', 'pyyaml',
               '--exclude-module', 'gradio', '--exclude-module', 'funasr', '--exclude-module', 'modelscope',
               '--exclude-module', 'datasets', '--exclude-module', 'spaces', '--exclude-module', 'uvicorn',
               '--exclude-module', 'fastapi', '--exclude-module', 'voxcpm.training', '--exclude-module', 'tensorboardX'],
}
EXCLUDE = ['PySide6', 'webview', 'tkinter', 'matplotlib', 'IPython', 'pytest']


def bundle_licenses(output):
    """Moves the deeply nested *.dist-info/licenses trees into one LISANSLAR.zip.

    They are not needed at run time but some paths (torch's third-party licenses) exceed Windows'
    260-character limit once installed under a normal program folder. Metadata files stay in place.
    """
    internal = output / '_internal'
    folders = sorted(internal.glob('*.dist-info/licenses'))
    if not folders:
        return
    with zipfile.ZipFile(internal / 'LISANSLAR.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for folder in folders:
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(internal).as_posix())
            shutil.rmtree(folder)


def build(folder):
    manifest = json.loads((folder / 'motor.json').read_text(encoding='utf-8'))
    engine_id = manifest['id']
    work = BUILD / folder.name
    shutil.rmtree(work, ignore_errors=True)
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--console', '--noupx',
               '--name', 'worker', '--distpath', str(work / 'dist'), '--workpath', str(work / 'build'),
               '--specpath', str(work), '--paths', str(ROOT), '--hidden-import', 'engines.protocol']
    for module in EXCLUDE:
        command += ['--exclude-module', module]
    command += OPTIONS.get(folder.name, [])
    command.append(str(folder / 'worker.py'))
    print('==>', engine_id, flush=True)
    subprocess.run(command, check=True, cwd=ROOT)
    output = work / 'dist' / 'worker'
    shutil.copy(folder / 'motor.json', output / 'motor.json')
    bundle_licenses(output)
    DIST.mkdir(exist_ok=True)
    archive_path = DIST / f'cevirgec-ses-{engine_id}-win64.zip'
    with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(output.rglob('*')):
            if path.is_file():
                archive.write(path, f'{engine_id}/{path.relative_to(output).as_posix()}')
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    size = archive_path.stat().st_size / 1e6
    print(f'    {archive_path.name}: {size:.0f} MB, sha256 {digest}', flush=True)
    return archive_path


def cuda_torch():
    import importlib.util
    if importlib.util.find_spec('torch') is None:
        return False
    import torch
    return bool(torch.version.cuda)


def main():
    # Turkish messages must not crash a cp1252 console or a redirected log.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    wanted = set(sys.argv[1:])
    if (not wanted or 'ema' in wanted) and not cuda_torch():
        print('UYARI: Bu Python ortamındaki PyTorch CUDA desteklemiyor; EMA paketi yalnızca CPU ile çalışır. '
              "GPU için önce CUDA'lı PyTorch kurun: py -3 -m pip install torch --index-url "
              'https://download.pytorch.org/whl/cu128', flush=True)
    folders = [path.parent for path in sorted((ROOT / 'engines').glob('*/motor.json'))
               if not wanted or path.parent.name in wanted]
    if not folders:
        sys.exit('Derlenecek motor bulunamadı.')
    for folder in folders:
        build(folder)


if __name__ == '__main__':
    main()
