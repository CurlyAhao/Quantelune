"""Stage the existing media runtime for distribution; never alter its inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path


def copy_tree(source: Path, destination: Path):
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--python-home', required=True)
    parser.add_argument('--media-environment', required=True)
    parser.add_argument('--models', required=True)
    parser.add_argument('--ffmpeg', required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Choose an empty staging directory')
    output.mkdir(parents=True, exist_ok=True)
    runtime = output / 'runtime'
    python = runtime / 'python'
    python.mkdir(parents=True, exist_ok=True)
    base = Path(args.python_home).resolve()
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python312.dll', 'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(base / name, python / name)
    for name in ('Lib', 'DLLs'):
        copy_tree(base / name, python / name)
    copy_tree(Path(args.media_environment) / 'Lib/site-packages', python / 'Lib/site-packages')
    # Explicit relative paths avoid the original venv's absolute pyvenv.cfg.
    (python / 'python312._pth').write_text('Lib\nDLLs\nLib/site-packages\n../../_internal/app/services\nimport site\n', encoding='utf-8')
    copy_tree(Path(args.models), runtime / 'models')
    ffmpeg = runtime / 'ffmpeg'
    ffmpeg.mkdir(parents=True, exist_ok=True)
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        shutil.copy2(Path(args.ffmpeg) / 'bin' / name, ffmpeg / name)
    for name in ('LICENSE', 'README.txt'):
        shutil.copy2(Path(args.ffmpeg) / name, ffmpeg / name)
    config = {
        'portable': True,
        'python': 'runtime/python/python.exe',
        'model_root': 'runtime/models',
        'options': {
            'ffmpeg_path': 'runtime/ffmpeg/ffmpeg.exe',
            'ffprobe_path': 'runtime/ffmpeg/ffprobe.exe',
            'resnet_weights': 'runtime/models/resnet18-f37072fd.pth',
            'minilm_path': 'runtime/models/all-MiniLM-L6-v2',
            'minilm_revision': '1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
            'whisper_path': 'runtime/models/whisper-tiny',
            'whisper_revision': '169d4a4341b33bc18d8881c4b69c2e104e1cc0af',
        },
    }
    (output / 'media-runtime.json').write_text(json.dumps(config, indent=2), encoding='utf-8')
    entries = []
    for file in sorted(runtime.rglob('*')):
        if file.is_file():
            with file.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            entries.append({'path': file.relative_to(output).as_posix(), 'bytes': file.stat().st_size, 'sha256': digest})
    (output / 'runtime-manifest.json').write_text(json.dumps({'files': entries}, indent=2), encoding='utf-8')
    print(json.dumps({'files': len(entries), 'bytes': sum(row['bytes'] for row in entries)}))


if __name__ == '__main__':
    main()
