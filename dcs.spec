# PyInstaller spec for DCS
# Builds two executables: sandbox_ctl (host CLI) and cdcs_agent (agent process)

import os

block_cipher = None

# --- Host CLI (sandbox_ctl) ---
host_a = Analysis(
    ['sandbox_ctl.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['host', 'host.desktop_sandbox', 'host.pipe_client'],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
host_pyz = PYZ(host_a.pure, host_a.zipped_data, cipher=block_cipher)
host_exe = EXE(
    host_pyz,
    host_a.scripts,
    host_a.binaries,
    host_a.datas,
    [],
    name='dcs',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)

# --- Agent process (cdcs_agent) ---
agent_a = Analysis(
    ['agent/__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=[
        'agent', 'agent.cdcs_agent', 'agent.screenshot',
        'agent.mouse', 'agent.keyboard',
        'win32api', 'win32gui', 'win32ui', 'win32con',
        'win32process', 'win32event', 'win32security',
        'pywintypes', 'PIL',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
    noarchive=False,
)
agent_pyz = PYZ(agent_a.pure, agent_a.zipped_data, cipher=block_cipher)
agent_exe = EXE(
    agent_pyz,
    agent_a.scripts,
    agent_a.binaries,
    agent_a.datas,
    [],
    name='dcs-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=None,
)
