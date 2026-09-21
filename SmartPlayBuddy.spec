# -*- mode: python ; coding: utf-8 -*-
"""
SmartPlayBuddy PyInstaller 打包配置。
构建命令: pyinstaller SmartPlayBuddy.spec
"""
import os
import sys as _sys
import json
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
PROJECT_ROOT = os.path.abspath(os.path.dirname(SPEC))
SRC_DIR = os.path.join(PROJECT_ROOT, 'src')
ICON_PATH = os.path.join(SRC_DIR, 'smartplaybuddy', 'ui', 'resources', 'icons', 'logo.ico')

with open(os.path.join(SRC_DIR, 'smartplaybuddy', 'utils', 'i18n', 'locales', 'en_US.json'), 'r', encoding='utf-8') as _f:
    _app = json.load(_f)['app']
APP_NAME = _app['name']
APP_EXE = _app['exe_name'].replace('.exe', '')

# ── 数据文件 ──
datas = []

# i18n 翻译文件
datas += [(os.path.join(SRC_DIR, 'smartplaybuddy', 'utils', 'i18n', 'locales'),
           os.path.join('smartplaybuddy', 'utils', 'i18n', 'locales'))]

# UI 图标资源
datas += [(os.path.join(SRC_DIR, 'smartplaybuddy', 'ui', 'resources'),
           os.path.join('smartplaybuddy', 'ui', 'resources'))]

# PyQt6 数据文件 (Qt 平台插件、翻译等)
datas += collect_data_files('PyQt6')

# PyQt6-WebEngine 需要额外收集的资源
try:
    datas += collect_data_files('PyQt6.QtWebEngineCore')
except Exception:
    pass

# ── 隐式导入 ──
hiddenimports = [
    # keyring 后端 (Windows 凭据管理器)
    'keyring.backends',
    'keyring.backends.Windows',
    'keyring.backends.chainer',
    'keyring.backends.fail',
    # websockets
    'websockets',
    'websockets.legacy',
    'websockets.legacy.server',
    'websockets.legacy.client',
    # PyQt6 WebEngine
    'PyQt6',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebChannel',
    'PyQt6.QtNetwork',
    # qasync
    'qasync',
    # 项目内部模块 (动态导入需显式声明)
    'smartplaybuddy',
    'smartplaybuddy.client',
    'smartplaybuddy.config',
    'smartplaybuddy.mod',
    'smartplaybuddy.ws',
    'smartplaybuddy.ws.connector',
    'smartplaybuddy.ws.bridge',
    'smartplaybuddy.ws.permit',
    'smartplaybuddy.ws.stream',
    'smartplaybuddy.ws.message',
    'smartplaybuddy.ws.message.message',
    'smartplaybuddy.ws.message.error',
    'smartplaybuddy.ws.message.session',
    'smartplaybuddy.ws.message.system',
    'smartplaybuddy.ws.logic',
    'smartplaybuddy.ws.logic.systemlogic',
    'smartplaybuddy.drivers',
    'smartplaybuddy.drivers.base',
    'smartplaybuddy.drivers.host',
    'smartplaybuddy.drivers.registry',
    'smartplaybuddy.user',
    'smartplaybuddy.user.login',
    'smartplaybuddy.utils',
    'smartplaybuddy.utils.logger',
    'smartplaybuddy.utils.i18n',
    'smartplaybuddy.utils.i18n.translator',
    'smartplaybuddy.ui',
    'smartplaybuddy.ui.main',
    'smartplaybuddy.ui.capsule',
    'smartplaybuddy.ui.config',
    'smartplaybuddy.ui.login',
    'smartplaybuddy.ui.request',
]

a = Analysis(
    [os.path.join(SRC_DIR, 'launcher.py')],
    pathex=[SRC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'unittest', 'xml', 'xmlrpc',
        'pydoc', 'doctest', 'test', 'tests',
        'numpy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_EXE,
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
    icon=ICON_PATH,
    version_info=os.path.join(PROJECT_ROOT, 'build', 'version_info.txt'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False, upx=True, upx_exclude=[],
    name=APP_NAME,
)
