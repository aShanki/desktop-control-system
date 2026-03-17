"""Desktop sandbox lifecycle management for CDCS.

Creates hidden Windows desktops, launches the ``cdcs-agent`` process on
them, and proxies commands over named pipes.

Session state is persisted to ``~/.cdcs/sessions/{name}.json`` so that
:class:`DesktopSandbox` -- and the CLI -- can reconnect across process
invocations without keeping a long-running daemon.

Architecture
------------
* The **agent** is the named-pipe *server*.  It creates the pipe, loops
  on ``ConnectNamedPipe``, handles one command per client connection,
  then loops back for the next client.
* The **host / CLI** is the pipe *client*.  Each CLI invocation opens a
  fresh connection, sends one command, reads the response, and exits.
* Session metadata (desktop name, agent PID, pipe path) is stored in a
  tiny JSON file so subsequent CLI calls can find the agent.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import datetime as _dt
import json
import os
import pathlib
import subprocess
import sys
import time
from typing import Any

from host import config as _config
from host.pipe_client import PipeClient

# =====================================================================
# Win32 constants
# =====================================================================

DESKTOP_READOBJECTS     = 0x0001
DESKTOP_CREATEWINDOW    = 0x0002
DESKTOP_CREATEMENU      = 0x0004
DESKTOP_HOOKCONTROL     = 0x0008
DESKTOP_JOURNALRECORD   = 0x0010
DESKTOP_JOURNALPLAYBACK = 0x0020
DESKTOP_ENUMERATE       = 0x0040
DESKTOP_WRITEOBJECTS    = 0x0080
DESKTOP_SWITCHDESKTOP   = 0x0100
DESKTOP_ALL             = 0x01FF
GENERIC_ALL             = 0x1000_0000

CREATE_NEW_CONSOLE        = 0x0000_0010
CREATE_NEW_PROCESS_GROUP  = 0x0000_0200

PROCESS_TERMINATE         = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400

STILL_ACTIVE = 259

# =====================================================================
# Win32 bindings via ctypes
# =====================================================================

user32   = ctypes.WinDLL("user32",   use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# -- CreateDesktopW ---------------------------------------------------

CreateDesktopW = user32.CreateDesktopW
CreateDesktopW.restype  = wintypes.HANDLE
CreateDesktopW.argtypes = [
    wintypes.LPCWSTR,   # lpszDesktop
    wintypes.LPCWSTR,   # lpszDevice  (NULL)
    ctypes.c_void_p,    # pDevmode    (NULL)
    wintypes.DWORD,     # dwFlags
    wintypes.DWORD,     # dwDesiredAccess
    ctypes.c_void_p,    # lpsa        (NULL)
]

CloseDesktop = user32.CloseDesktop
CloseDesktop.restype  = wintypes.BOOL
CloseDesktop.argtypes = [wintypes.HANDLE]

# -- CreateProcessW ---------------------------------------------------

class STARTUPINFOW(ctypes.Structure):
    """Mirrors the Win32 ``STARTUPINFOW`` structure."""
    _fields_ = [
        ("cb",              wintypes.DWORD),
        ("lpReserved",      wintypes.LPWSTR),
        ("lpDesktop",       wintypes.LPWSTR),
        ("lpTitle",         wintypes.LPWSTR),
        ("dwX",             wintypes.DWORD),
        ("dwY",             wintypes.DWORD),
        ("dwXSize",         wintypes.DWORD),
        ("dwYSize",         wintypes.DWORD),
        ("dwXCountChars",   wintypes.DWORD),
        ("dwYCountChars",   wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags",         wintypes.DWORD),
        ("wShowWindow",     wintypes.WORD),
        ("cbReserved2",     wintypes.WORD),
        ("lpReserved2",     ctypes.c_void_p),
        ("hStdInput",       wintypes.HANDLE),
        ("hStdOutput",      wintypes.HANDLE),
        ("hStdError",       wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    """Mirrors the Win32 ``PROCESS_INFORMATION`` structure."""
    _fields_ = [
        ("hProcess",    wintypes.HANDLE),
        ("hThread",     wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId",  wintypes.DWORD),
    ]


CreateProcessW = kernel32.CreateProcessW
CreateProcessW.restype  = wintypes.BOOL
CreateProcessW.argtypes = [
    wintypes.LPCWSTR,                       # lpApplicationName
    wintypes.LPWSTR,                        # lpCommandLine
    ctypes.c_void_p,                        # lpProcessAttributes
    ctypes.c_void_p,                        # lpThreadAttributes
    wintypes.BOOL,                          # bInheritHandles
    wintypes.DWORD,                         # dwCreationFlags
    ctypes.c_void_p,                        # lpEnvironment
    wintypes.LPCWSTR,                       # lpCurrentDirectory
    ctypes.POINTER(STARTUPINFOW),           # lpStartupInfo
    ctypes.POINTER(PROCESS_INFORMATION),    # lpProcessInformation
]

# -- Process helpers --------------------------------------------------

OpenProcess = kernel32.OpenProcess
OpenProcess.restype  = wintypes.HANDLE
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

TerminateProcess = kernel32.TerminateProcess
TerminateProcess.restype  = wintypes.BOOL
TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]

GetExitCodeProcess = kernel32.GetExitCodeProcess
GetExitCodeProcess.restype  = wintypes.BOOL
GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]

WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.restype  = wintypes.DWORD
WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

CloseHandle = kernel32.CloseHandle
CloseHandle.restype  = wintypes.BOOL
CloseHandle.argtypes = [wintypes.HANDLE]

# =====================================================================
# Paths and configuration
# =====================================================================

_HERE          = pathlib.Path(__file__).resolve().parent       # host/
_PROJECT       = _HERE.parent                                  # desktop-control-system/

PYTHON_EXE = os.environ.get("CDCS_PYTHON_EXE", sys.executable)

STATE_DIR = pathlib.Path.home() / ".cdcs" / "sessions"


def _pipe_name(session: str) -> str:
    r"""Return the canonical named-pipe path for *session*.

    Named pipes require the UNC path ``\\.\pipe\<name>``.
    """
    return "\\\\.\\pipe\\cdcs-" + session


# =====================================================================
# Session state persistence
# =====================================================================

def _state_path(session: str) -> pathlib.Path:
    return STATE_DIR / f"{session}.json"


def _save_state(session: str, data: dict) -> None:
    """Write session metadata to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(session).write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )


def _load_state(session: str) -> dict | None:
    """Load session metadata, or ``None`` if the file is missing/corrupt."""
    p = _state_path(session)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _delete_state(session: str) -> None:
    """Remove the session metadata file."""
    p = _state_path(session)
    if p.exists():
        p.unlink(missing_ok=True)


def _list_states() -> list[dict]:
    """Return all saved session state dicts."""
    if not STATE_DIR.exists():
        return []
    results: list[dict] = []
    for f in STATE_DIR.glob("*.json"):
        try:
            results.append(json.loads(f.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return results


# =====================================================================
# Process helpers
# =====================================================================

def _is_process_alive(pid: int) -> bool:
    """Return ``True`` if *pid* is still running."""
    handle = OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if GetExitCodeProcess(handle, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        CloseHandle(handle)


def _kill_process(pid: int) -> bool:
    """Force-kill *pid*.  Returns ``True`` on success."""
    handle = OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(TerminateProcess(handle, 1))
    finally:
        CloseHandle(handle)


def _kill_desktop_processes(desktop_name: str) -> None:
    """Enumerate all windows on *desktop_name* and kill their owning processes.

    Uses ctypes EnumDesktopWindows + GetWindowThreadProcessId to find PIDs,
    then TerminateProcess on each.  This ensures launched apps (which may
    have spawned child processes with different PIDs) are cleaned up.
    """
    pids: set[int] = set()

    WNDENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
    )

    # Use ctypes GetWindowThreadProcessId for cross-desktop safety.
    _GetWindowThreadProcessId = user32.GetWindowThreadProcessId
    _GetWindowThreadProcessId.restype = wintypes.DWORD
    _GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
    ]

    @WNDENUMPROC
    def _enum_cb(hwnd, lparam):
        pid = wintypes.DWORD()
        _GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value:
            pids.add(pid.value)
        return True

    hdesk = user32.OpenDesktopW(
        desktop_name, 0, False, DESKTOP_ENUMERATE,
    )
    if hdesk:
        user32.EnumDesktopWindows(hdesk, _enum_cb, 0)
        user32.CloseDesktop(hdesk)

    for pid in pids:
        try:
            _kill_process(pid)
        except Exception:
            pass

    if pids:
        time.sleep(0.3)


def _send_one_command(session: str, command: dict,
                      timeout: float = 10.0) -> dict:
    """Open a throwaway pipe connection, send *command*, return response.

    This is the primary communication pattern: each CLI invocation
    calls this once, then exits.
    """
    client = PipeClient(_pipe_name(session))
    try:
        client.connect(timeout=timeout)
        return client.send(command)
    finally:
        client.close()


# =====================================================================
# DesktopSandbox
# =====================================================================

class DesktopSandbox:
    """Manages hidden-desktop creation, agent launching, and teardown.

    Every public method is **stateless** across CLI invocations: it reads
    or writes the persisted session JSON and opens a fresh pipe
    connection when it needs to talk to the agent.

    The only in-process state is ``_desktops``, which caches desktop
    handles opened by *this* Python process (useful when ``create`` and
    ``destroy`` happen in the same process).
    """

    # HDESK handles opened by *this* process.
    _desktops: dict[str, int] = {}

    # ==================================================================
    # create
    # ==================================================================

    def create(self, name: str) -> dict:
        """Create a hidden desktop, launch the agent, verify it is alive.

        Steps:
          1. ``CreateDesktopW`` to make the hidden desktop.
          2. ``CreateProcessW`` to start ``cdcs_agent.py`` **on that
             desktop**, passing the pipe name as an argument.  The agent
             itself creates the named pipe server and loops on
             ``ConnectNamedPipe``.
          3. Wait for the pipe to appear, connect, send ``ping``,
             confirm the agent is responsive.
          4. Persist session metadata to ``~/.cdcs/sessions/{name}.json``.

        Returns:
            ``{"ok": True, "session": ..., "pipe": ..., "agent_pid": ...}``
            on success, or ``{"ok": False, "error": ...}`` on failure.
        """
        # Guard: don't create twice.
        existing = _load_state(name)
        if existing and _is_process_alive(existing["agent_pid"]):
            return {
                "ok": False,
                "error": f"Session '{name}' already exists and agent is alive",
            }

        # 1. Create the hidden desktop.
        hdesk = CreateDesktopW(
            name, None, None, 0, DESKTOP_ALL | GENERIC_ALL, None
        )
        if not hdesk:
            err = ctypes.get_last_error()
            return {
                "ok": False,
                "error": f"CreateDesktopW failed (win32 error {err})",
            }
        self._desktops[name] = hdesk

        pipe = _pipe_name(name)

        # 2. Launch the agent on the hidden desktop.
        si = STARTUPINFOW()
        si.cb = ctypes.sizeof(STARTUPINFOW)
        si.lpDesktop = f"WinSta0\\{name}"  # routes the new process to our desktop

        pi = PROCESS_INFORMATION()

        # Launch as a module so that relative imports in the agent package work.
        # The cwd is set to _PROJECT so ``python -m agent`` resolves correctly.
        cmd_line = f'"{PYTHON_EXE}" -m agent "{pipe}" "{name}"'

        ok = CreateProcessW(
            None,                                # lpApplicationName
            cmd_line,                            # lpCommandLine
            None,                                # lpProcessAttributes
            None,                                # lpThreadAttributes
            False,                               # bInheritHandles
            CREATE_NEW_CONSOLE,                  # dwCreationFlags
            None,                                # lpEnvironment
            str(_PROJECT),                       # lpCurrentDirectory
            ctypes.byref(si),
            ctypes.byref(pi),
        )
        if not ok:
            err = ctypes.get_last_error()
            CloseDesktop(hdesk)
            return {
                "ok": False,
                "error": f"CreateProcessW failed (win32 error {err})",
            }

        agent_pid = pi.dwProcessId
        CloseHandle(pi.hThread)
        CloseHandle(pi.hProcess)

        # 3. Persist session state.
        _save_state(name, {
            "session":    name,
            "pipe":       pipe,
            "desktop":    name,
            "agent_pid":  agent_pid,
            "created_at": _dt.datetime.now().isoformat(),
        })

        # 4. Wait for the agent's pipe server to come up and ping it.
        try:
            resp = _send_one_command(name, {"cmd": "ping"}, timeout=10.0)
        except Exception as exc:
            return {
                "ok": False,
                "error": f"Agent ping failed: {exc}",
                "session": name,
                "agent_pid": agent_pid,
            }

        if not resp.get("ok"):
            return {
                "ok": False,
                "error": f"Agent ping returned: {resp}",
                "session": name,
                "agent_pid": agent_pid,
            }

        result = {
            "ok":        True,
            "session":   name,
            "pipe":      pipe,
            "desktop":   name,
            "agent_pid": agent_pid,
        }

        # Auto-launch preview if configured.
        if _config.get("preview_enabled"):
            preview_pid = self._spawn_preview(name)
            if preview_pid:
                result["preview_pid"] = preview_pid
                # Persist the preview PID in session state.
                state = _load_state(name)
                if state:
                    state["preview_pid"] = preview_pid
                    _save_state(name, state)

        return result

    # ==================================================================
    # destroy
    # ==================================================================

    def destroy(self, name: str) -> dict:
        """Tear down a session: ask agent to exit, kill leftovers, clean up.

        Returns ``{"ok": True, "session": name, "status": "destroyed"}``.
        """
        state = _load_state(name)
        agent_pid: int | None = state["agent_pid"] if state else None

        # Kill preview window first (if tracked).
        preview_pid = state.get("preview_pid") if state else None
        if preview_pid and _is_process_alive(preview_pid):
            _kill_process(preview_pid)

        # 1. Ask the agent to exit gracefully.
        if agent_pid and _is_process_alive(agent_pid):
            try:
                _send_one_command(name, {"cmd": "exit"}, timeout=3.0)
            except Exception:
                pass  # best effort

            # 2. Wait briefly for clean shutdown.
            handle = OpenProcess(PROCESS_QUERY_INFORMATION, False, agent_pid)
            if handle:
                WaitForSingleObject(handle, 2000)
                CloseHandle(handle)

        # 3. Kill ALL processes that have windows on this desktop.
        _kill_desktop_processes(name)

        # 4. Force-kill agent if still alive.
        if agent_pid and _is_process_alive(agent_pid):
            _kill_process(agent_pid)
            time.sleep(0.3)

        # 5. Close the desktop handle if we have it in-process.
        hdesk = self._desktops.pop(name, None)
        if hdesk:
            CloseDesktop(hdesk)

        # 6. Remove persisted state.
        _delete_state(name)

        return {"ok": True, "session": name, "status": "destroyed"}

    # ==================================================================
    # launch (app on hidden desktop)
    # ==================================================================

    def launch(self, name: str, exe: str, args: str = "") -> dict:
        """Launch *exe* on the session's hidden desktop via the agent.

        The agent calls ``CreateProcessW`` with ``lpDesktop`` set to
        the hidden desktop, so the launched app's windows live there.
        The agent waits up to 10 s for the app's window to appear, so
        we use a longer timeout here.
        """
        try:
            return _send_one_command(name, {
                "cmd": "launch", "exe": exe, "args": args,
            }, timeout=15.0)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ==================================================================
    # send_command (generic proxy)
    # ==================================================================

    def send_command(self, name: str, command: dict) -> dict:
        """Send an arbitrary command dict to the session's agent.

        Opens a fresh pipe connection, sends the command, returns the
        agent's JSON response.  On failure wraps the exception in an
        ``{"ok": False, "error": ...}`` dict.
        """
        try:
            return _send_one_command(name, command)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ==================================================================
    # preview
    # ==================================================================

    @staticmethod
    def _spawn_preview(session: str) -> int | None:
        """Launch the preview window as a detached process.

        Returns the preview PID, or ``None`` on failure.
        """
        refresh = _config.get("preview_refresh_ms") or 500
        script = str(_PROJECT / "host" / "preview.py")
        try:
            proc = subprocess.Popen(
                [PYTHON_EXE, script, session,
                 "--refresh-ms", str(refresh)],
                cwd=str(_PROJECT),
                creationflags=(
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.DETACHED_PROCESS
                ),
                close_fds=True,
            )
            return proc.pid
        except Exception:
            return None

    def open_preview(self, name: str) -> dict:
        """Manually open a preview window for *name*.

        Saves the preview PID in the session state so ``destroy`` can
        clean it up.
        """
        state = _load_state(name)
        if not state:
            return {"ok": False, "error": f"Session '{name}' not found"}
        if not _is_process_alive(state.get("agent_pid", 0)):
            return {"ok": False, "error": f"Agent for '{name}' is not alive"}

        # Kill any existing preview first.
        old_pid = state.get("preview_pid")
        if old_pid and _is_process_alive(old_pid):
            _kill_process(old_pid)

        pid = self._spawn_preview(name)
        if not pid:
            return {"ok": False, "error": "Failed to launch preview process"}

        state["preview_pid"] = pid
        _save_state(name, state)
        return {"ok": True, "session": name, "preview_pid": pid}

    # ==================================================================
    # list_sessions
    # ==================================================================

    def list_sessions(self) -> list[dict]:
        """Return a list of known sessions with liveness information.

        Each entry::

            {
                "session":     "my-session",
                "agent_pid":   12345,
                "agent_alive": True,
                "windows":     3,
            }
        """
        results: list[dict] = []
        for st in _list_states():
            name = st["session"]
            pid  = st.get("agent_pid", 0)
            alive = _is_process_alive(pid)

            entry: dict[str, Any] = {
                "session":     name,
                "agent_pid":   pid,
                "agent_alive": alive,
            }

            if alive:
                try:
                    resp = _send_one_command(name, {"cmd": "windows"}, timeout=3.0)
                    if resp.get("ok"):
                        entry["windows"] = len(resp.get("windows", []))
                    else:
                        entry["windows"] = -1
                except Exception:
                    entry["windows"] = -1
            else:
                entry["windows"] = 0

            results.append(entry)
        return results
