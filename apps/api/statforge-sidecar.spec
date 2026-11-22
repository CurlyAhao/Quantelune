# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for StatForge Python sidecar.
Builds a standalone executable that Tauri can bundle as an external binary.

Usage:
    cd apps/api
    pyinstaller statforge-sidecar.spec
"""

import os, sys
from pathlib import Path

# CPython 3.12.0 breaks CodeType.replace() for inlined comprehensions. The
# frozen SciPy import can then raise NameError before the sidecar binds HTTP.
if sys.version_info[:2] != (3, 12) or sys.version_info[:3] == (3, 12, 0):
    raise RuntimeError(
        "StatForge sidecar builds require Python 3.12.1 or newer in the 3.12 series "
        "(3.12.0 has a CodeType.replace bug affecting frozen SciPy)."
    )

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

# SPECPATH is provided by PyInstaller; fall back to cwd.
_root = Path(getattr(sys.modules.get('PyInstaller.building.build_main'), 'SPECPATH',
                    os.getcwd())).resolve().parent
if not (_root / 'app').exists():
    _root = Path(os.getcwd())

a = Analysis(
    [str(_root / 'app' / 'sidecar_entry.py')],
    pathex=[str(_root)],
    binaries=[],
    datas=[
        (str(_root / 'alembic.ini'), '.'),
        (str(_root / 'alembic'), 'alembic'),
        (str(_root / 'app'), 'app'),
    ] + copy_metadata('deepagents', recursive=True)
      + [entry for name in ('matplotlib', 'seaborn', 'numpy', 'pandas', 'polars', 'pyarrow', 'pillow')
         for entry in copy_metadata(name)],
    hiddenimports=[
        'aiosqlite', 'sqlalchemy', 'sqlalchemy.ext.asyncio', 'sqlalchemy.pool',
        'fastapi', 'uvicorn', 'uvicorn.loops', 'uvicorn.protocols',
        'pydantic', 'pydantic_settings',
        'scipy', 'statsmodels', 'sklearn', 'sklearn.ensemble', 'sklearn.linear_model',
        'sklearn.tree', 'sklearn.neighbors', 'sklearn.svm', 'sklearn.calibration',
        'sklearn.metrics', 'sklearn.model_selection', 'sklearn.preprocessing',
        'sklearn.compose', 'sklearn.pipeline', 'sklearn.impute', 'sklearn.inspection',
        'polars', 'numpy', 'pandas', 'pyarrow', 'joblib', 'httpx', 'argon2', 'jwt',
        'matplotlib', 'seaborn', 'matplotlib.backends.backend_agg',
        'matplotlib.backends.backend_svg', 'matplotlib.backends.backend_pdf',
    ] + collect_submodules('app') + collect_submodules('fastapi')
      + collect_submodules('starlette') + collect_submodules('uvicorn'),
    hookspath=[],
    hooksconfig={'matplotlib': {'backends': ['Agg', 'svg', 'pdf']}},
    runtime_hooks=[],
    excludes=['tkinter', 'jedi', 'IPython'],
)

pyz = PYZ(a.pure)

_onedir = os.environ.get('STATFORGE_SIDECAR_LAYOUT') == 'onedir'
exe = EXE(
    pyz,
    a.scripts,
    [] if _onedir else a.binaries,
    [] if _onedir else a.datas,
    [],
    name='statforge-sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
    exclude_binaries=_onedir,
)

if _onedir:
    collect = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='statforge-sidecar')
