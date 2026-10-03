# -*- mode: python ; coding: utf-8 -*-
"""
LOKI Agent — Optimized PyInstaller Specification File (loki.spec)

Optimizes standalone executable footprint, compilation duration, and startup speed:
1. Prunes `litellm.proxy` (~50MB of Next.js frontend chunks, Prisma schemas, FastAPI proxy routes).
2. Filters out developer & testing dependencies (pytest, unittest, doctest).
3. Excludes unused GUI frameworks and dev tools (tkinter, tcl, pydoc, idlelib, pdb).
4. Excludes runtime build/packaging tools (pip, setuptools, wheel).
5. Collects Playwright driver binaries and LiteLLM core completion submodules without dead weight.
"""

import os
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

block_cipher = None

repo_root = Path(os.path.abspath(SPECPATH))

# --- 1. Targeted Dependency Harvesting ---

# Playwright: requires browser driver bindings and runtime assets
playwright_datas, playwright_binaries, playwright_hidden = collect_all("playwright")

# Rich & Typer: terminal formatting and CLI infrastructure
rich_datas, rich_binaries, rich_hidden = collect_all("rich")
typer_datas, typer_binaries, typer_hidden = collect_all("typer")

# LiteLLM: Surgical harvest of core completions & providers
# Exclude the 50MB `litellm.proxy` Next.js frontend and enterprise proxy daemon
litellm_submodules = [
    mod for mod in collect_submodules("litellm")
    if not mod.startswith("litellm.proxy")
    and "test" not in mod
    and "benchmark" not in mod
]

# Harvest data files (model pricing, provider configs) while strictly omitting 860+ web UI assets
litellm_datas = [
    (src, dst) for src, dst in collect_data_files("litellm")
    if "proxy" not in src
    and "_experimental" not in src
    and "out" not in src
]

# Combined datas, binaries, and hidden imports
datas = playwright_datas + rich_datas + typer_datas + litellm_datas
binaries = playwright_binaries + rich_binaries + typer_binaries

hiddenimports = (
    playwright_hidden
    + rich_hidden
    + typer_hidden
    + litellm_submodules
    + [
        "src.loki.cli",
        "src.loki.config",
        "src.loki.safety",
        "src.loki.ai.brain",
        "src.loki.ai.chat",
        "src.loki.engine.api_chaos",
        "src.loki.engine.ci",
        "src.loki.engine.healer",
        "src.loki.engine.html_reporter",
        "src.loki.engine.infra_chaos",
        "src.loki.engine.recorder",
        "src.loki.engine.replayer",
        "src.loki.engine.reporter",
        "src.loki.engine.sandbox",
        "src.loki.engine.scanner",
        "src.loki.engine.scrubber",
        "src.loki.engine.updater",
        "src.loki.personas.adversary",
        "src.loki.personas.base",
        "src.loki.personas.network_tormentor",
        "src.loki.personas.novice_chaotic",
        "src.loki.personas.rage_clicker",
        "src.loki.personas.swarm",
        "PIL",
        "yaml",
        "psutil",
        "prompt_toolkit",
        "tenacity",
    ]
)

# --- 2. Comprehensive Dead-Weight Exclusions ---

excludes = [
    # LiteLLM proxy and web UI dependencies
    "litellm.proxy",
    "litellm.proxy._experimental",
    "litellm.proxy.hooks",
    "litellm.proxy.management_endpoints",
    "litellm.proxy.ui_crud_endpoints",
    "litellm.proxy.proxy_server",
    "fastapi",
    "starlette",
    "uvicorn",
    # Testing suites & runners
    "pytest",
    "_pytest",
    "unittest",
    "doctest",
    "test",
    # Packaging & build-time tools
    "pip",
    "setuptools",
    "distutils",
    "wheel",
    "pkg_resources.tests",
    # GUI and debug utilities
    "tkinter",
    "Tkinter",
    "_tkinter",
    "tcl",
    "turtle",
    "idlelib",
    "pydoc",
    "pdb",
    # Heavy scientific / interactive suites
    "matplotlib",
    "scipy",
    "IPython",
    "notebook",
    "jupyter",
    "PIL.ImageQt",
    "PIL.ImageTk",
]

# --- 3. Analysis & Build Configuration ---

a = Analysis(
    ["loki_entry.py"],
    pathex=[str(repo_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="loki",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
