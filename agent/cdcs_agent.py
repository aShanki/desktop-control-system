"""CDCS Agent -- runs on the hidden desktop as a named-pipe server.

This process is launched by the host bridge (``desktop_sandbox.py``) on
a hidden Win32 desktop.  It:

1. Creates a named pipe server at the path received in ``sys.argv[1]``.
2. Loops: waits for a client to connect, reads **one** JSON command,
   dispatches it, writes **one** JSON response, disconnects the client,
   and loops back to step 2.
3. Exits when it receives the ``"exit"`` command or when the process is
   terminated externally.

Usage::

    python -m agent.cdcs_agent \\\\.\\pipe\\cdcs-session-0 [desktop-name]

Protocol
--------
Newline-delimited JSON over a **byte-mode** named pipe.  Each client
connection is a single request/response pair:

* Client writes: ``json.dumps(command) + "\\n"``
* Server reads line, processes, writes: ``json.dumps(response) + "\\n"``
* Server disconnects the client and re-listens.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import sys
import tempfile
import time

import pywintypes
import win32api
import win32con
import win32file
import win32gui
import win32pipe
import win32process

try:
    from . import keyboard, mouse, screenshot
except ImportError:
    import keyboard, mouse, screenshot  # type: ignore[no-redef]

log = logging.getLogger("cdcs.agent")

PIPE_BUFFER_SIZE = 65_536

# =====================================================================
# Desktop name detection
# =====================================================================

def _get_desktop_name() -> str:
    """Return the name of the desktop this process is running on."""
    try:
        si = win32process.GetStartupInfo()
        if si.lpDesktop:
            desktop = si.lpDesktop
            if "\\" in desktop:
                desktop = desktop.split("\\", 1)[1]
            return desktop
    except Exception:
        pass
    return "unknown"


# =====================================================================
# Named-pipe server
# =====================================================================

class PipeServer:
    """Server-side named pipe that accepts one client at a time.

    The lifecycle per iteration is:

    1. ``create_pipe()``   -- create the Win32 named pipe instance.
    2. ``wait_for_client()`` -- block until a client connects.
    3. ``read_line()`` / ``write_line()`` -- exchange data.
    4. ``disconnect()``    -- flush, disconnect, close handle.
    5. Go to 1.
    """

    def __init__(self, pipe_name: str) -> None:
        self.pipe_name = pipe_name
        self._handle = None
        self._buffer = b""

    def create_pipe(self) -> None:
        """Create a new named pipe instance (server side)."""
        self._handle = win32pipe.CreateNamedPipe(
            self.pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1,                  # max instances -- one client at a time
            PIPE_BUFFER_SIZE,
            PIPE_BUFFER_SIZE,
            0,                  # default timeout
            None,               # default security (current user only)
        )
        self._buffer = b""

    def wait_for_client(self, timeout_seconds: float = 0) -> bool:
        """Block until a client connects to the pipe.

        Uses synchronous ConnectNamedPipe which blocks until a client
        connects.  This is fine since the agent is a dedicated process.

        Args:
            timeout_seconds: Ignored (blocks indefinitely).

        Returns:
            ``True`` if a client connected.
        """
        try:
            win32pipe.ConnectNamedPipe(self._handle, None)
            return True
        except pywintypes.error as exc:
            if exc.winerror == 535:  # ERROR_PIPE_CONNECTED
                return True
            raise

    def read_line(self) -> str | None:
        """Read one newline-terminated line from the connected client.

        Returns ``None`` on disconnect / broken pipe.
        """
        while b"\n" not in self._buffer:
            try:
                _hr, data = win32file.ReadFile(self._handle, 4096)
            except pywintypes.error:
                return None
            if not data:
                return None
            self._buffer += data

        line, self._buffer = self._buffer.split(b"\n", 1)
        return line.decode("utf-8", errors="replace")

    def write_line(self, text: str) -> None:
        """Write a newline-terminated line to the connected client."""
        try:
            win32file.WriteFile(self._handle, (text + "\n").encode("utf-8"))
            win32file.FlushFileBuffers(self._handle)
        except pywintypes.error:
            log.warning("Failed to write to pipe.")

    def disconnect(self) -> None:
        """Disconnect the current client and close the pipe instance."""
        if self._handle is not None:
            try:
                win32file.FlushFileBuffers(self._handle)
            except pywintypes.error:
                pass
            try:
                win32pipe.DisconnectNamedPipe(self._handle)
            except pywintypes.error:
                pass
            try:
                win32api.CloseHandle(self._handle)
            except pywintypes.error:
                pass
            self._handle = None
        self._buffer = b""


# =====================================================================
# Keyboard target helpers
# =====================================================================

def _find_edit_control(hwnd: int) -> int | None:
    """Walk child windows of *hwnd* looking for an edit/text control.

    Many modern apps (e.g. Win11 Notepad) have a child control that
    actually receives keyboard messages.  Common class names include
    ``Edit``, ``RichEditD2DPT``, ``RichEdit20W``, ``Scintilla``, etc.

    Returns the child HWND if found, otherwise ``None``.
    """
    _EDIT_CLASSES = {
        "Edit", "RichEditD2DPT", "RichEdit20W", "RichEdit20A",
        "RichEdit50W", "Scintilla", "RICHEDIT60W",
        "_WwG",  # Word
        "Internet Explorer_Server",  # some WebView2 hosts
    }
    result: list[int] = []

    def _callback(child_hwnd, _):
        try:
            cls = win32gui.GetClassName(child_hwnd)
            if cls in _EDIT_CLASSES:
                result.append(child_hwnd)
                return False  # stop enumeration
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _callback, None)
    except Exception:
        pass

    return result[0] if result else None


def _get_keyboard_target(hwnd: int | None, desktop: str) -> int | None:
    """Get the best HWND for keyboard input (edit control if possible).

    If *hwnd* is explicitly provided in the command message, use it.
    Otherwise, find the topmost window on the desktop and look for an
    edit child control within it.
    """
    if hwnd is None:
        # Find the topmost titled window on this desktop.
        best_hwnd, _title = screenshot.find_best_window(desktop)
        if best_hwnd:
            hwnd = best_hwnd
    if hwnd:
        # Try to find a child edit control.
        edit = _find_edit_control(hwnd)
        if edit:
            log.debug("Keyboard target: edit control %#x (child of %#x)", edit, hwnd)
            return edit
        log.debug("Keyboard target: top-level window %#x (no edit child found)", hwnd)
    return hwnd


# =====================================================================
# Command handlers
# =====================================================================

def _handle_ping(msg: dict, desktop: str) -> dict:
    return {"ok": True, "pong": True, "desktop": desktop, "pid": os.getpid()}


def _handle_screenshot(msg: dict, desktop: str) -> dict:
    path = msg.get("path")
    if not path:
        # Generate a temp path if none provided.
        fd, path = tempfile.mkstemp(suffix=".png", prefix="cdcs-screenshot-")
        os.close(fd)

    hwnd = msg.get("hwnd")
    if not hwnd:
        hwnd, title = screenshot.find_best_window(desktop)
        if not hwnd:
            return {"ok": False, "error": "No suitable window found on this desktop"}
        result = screenshot.capture_window(hwnd, path)
        if result.get("ok"):
            result["hwnd"] = hwnd
            result["title"] = title
        return result
    else:
        return screenshot.capture_window(int(hwnd), path)


def _handle_type(msg: dict, desktop: str) -> dict:
    text = msg.get("text", "")
    hwnd = msg.get("hwnd")
    if hwnd:
        hwnd = int(hwnd)
    target = _get_keyboard_target(hwnd, desktop)
    result = keyboard.type_text(text, hwnd=target)
    if target:
        result["hwnd"] = target
    return result


def _handle_key(msg: dict, desktop: str) -> dict:
    combo = msg.get("combo", "")
    if not combo:
        return {"ok": False, "error": "Missing 'combo' in key command"}
    hwnd = msg.get("hwnd")
    if hwnd:
        hwnd = int(hwnd)
    target = _get_keyboard_target(hwnd, desktop)
    result = keyboard.send_key_combo(combo, hwnd=target)
    if target:
        result["hwnd"] = target
    return result


def _handle_click(msg: dict, desktop: str) -> dict:
    hwnd = msg.get("hwnd")
    if not hwnd:
        hwnd, _title = screenshot.find_best_window(desktop)
        if not hwnd:
            return {"ok": False, "error": "No window found for click target"}

    x = msg.get("x", 0)
    y = msg.get("y", 0)
    button = msg.get("button", "left")
    double = msg.get("double", False)
    return mouse.click(int(hwnd), int(x), int(y), button=button, double=double)


def _handle_scroll(msg: dict, desktop: str) -> dict:
    hwnd = msg.get("hwnd")
    if not hwnd:
        hwnd, _title = screenshot.find_best_window(desktop)
        if not hwnd:
            return {"ok": False, "error": "No window found for scroll target"}

    x = msg.get("x", 0)
    y = msg.get("y", 0)
    delta = msg.get("delta", -3)
    return mouse.scroll(int(hwnd), int(x), int(y), int(delta))


def _handle_launch(msg: dict, desktop: str) -> dict:
    exe = msg.get("exe")
    if not exe:
        return {"ok": False, "error": "Missing 'exe' in launch command"}

    args_str = msg.get("args", "")
    cmdline = f'"{exe}" {args_str}' if args_str else f'"{exe}"'

    try:
        # Snapshot existing windows BEFORE launching so we can detect new ones.
        existing_hwnds = _get_all_visible_hwnds(desktop)

        si = win32process.STARTUPINFO()
        si.lpDesktop = f"WinSta0\\{desktop}" if desktop != "unknown" else None

        proc_info = win32process.CreateProcess(
            None, cmdline, None, None, False, 0, None, None, si,
        )
        h_process, h_thread, pid, _tid = proc_info
        win32api.CloseHandle(h_thread)

        log.info("Launched PID %d: %s", pid, cmdline)

        # Wait up to 10 s for a new visible window to appear.
        # Win11 Notepad (and other apps) may spawn child processes, so the
        # window PID won't match the PID from CreateProcess.  We detect
        # any NEW window that wasn't present before launch.
        hwnd = 0
        title = ""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            time.sleep(0.5)

            # Strategy 1: look for a window owned by the launched PID.
            found = _find_window_by_pid(pid, desktop)
            if found:
                hwnd, title = found
                break

            # Strategy 2: detect any new window that wasn't there before.
            found_new = _find_new_window(existing_hwnds, desktop)
            if found_new:
                hwnd, title = found_new
                log.info(
                    "Found new window (child process): hwnd=%d title=%r",
                    hwnd, title,
                )
                break

        win32api.CloseHandle(h_process)
        return {"ok": True, "pid": pid, "hwnd": hwnd, "title": title}

    except Exception as exc:
        log.exception("launch failed: %s", cmdline)
        return {"ok": False, "error": str(exc)}


def _get_all_visible_hwnds(desktop: str) -> set[int]:
    """Return the set of all visible, titled window handles."""
    hwnds: set[int] = set()

    def _callback(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                hwnds.add(hwnd)
        return True

    if desktop and desktop != "unknown":
        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
            )

            @WNDENUMPROC
            def _enum_cb(hwnd, lparam):
                _callback(hwnd, None)
                return True

            hdesk = ctypes.windll.user32.OpenDesktopW(
                desktop, 0, False, 0x0040,  # DESKTOP_ENUMERATE
            )
            if hdesk:
                ctypes.windll.user32.EnumDesktopWindows(hdesk, _enum_cb, 0)
                ctypes.windll.user32.CloseDesktop(hdesk)
            else:
                win32gui.EnumWindows(_callback, None)
        except Exception:
            win32gui.EnumWindows(_callback, None)
    else:
        win32gui.EnumWindows(_callback, None)

    return hwnds


def _find_new_window(
    old_hwnds: set[int], desktop: str,
) -> tuple[int, str] | None:
    """Return the first visible, titled window that is NOT in *old_hwnds*."""
    current = _get_all_visible_hwnds(desktop)
    new_hwnds = current - old_hwnds
    for hwnd in new_hwnds:
        try:
            title = win32gui.GetWindowText(hwnd)
            if title:
                return (hwnd, title)
        except Exception:
            pass
    return None


def _find_window_by_pid(
    target_pid: int, desktop: str | None = None,
) -> tuple[int, str] | None:
    """Find the first visible, titled window owned by *target_pid*."""
    result: list[tuple[int, str]] = []

    def _callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == target_pid:
                result.append((hwnd, title))
                return False
        except Exception:
            pass
        return True

    if desktop and desktop != "unknown":
        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
            )

            @WNDENUMPROC
            def _enum_cb(hwnd, lparam):
                return _callback(hwnd, None)

            hdesk = ctypes.windll.user32.OpenDesktopW(
                desktop, 0, False, 0x0040,
            )
            if hdesk:
                ctypes.windll.user32.EnumDesktopWindows(hdesk, _enum_cb, 0)
                ctypes.windll.user32.CloseDesktop(hdesk)
            else:
                win32gui.EnumWindows(_callback, None)
        except Exception:
            win32gui.EnumWindows(_callback, None)
    else:
        win32gui.EnumWindows(_callback, None)

    return result[0] if result else None


def _handle_windows(msg: dict, desktop: str) -> dict:
    windows: list[dict] = []

    def _callback(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        rect = win32gui.GetWindowRect(hwnd)
        cls = win32gui.GetClassName(hwnd)
        windows.append({
            "hwnd":  hwnd,
            "title": title,
            "rect":  list(rect),
            "class": cls,
        })
        return True

    if desktop and desktop != "unknown":
        try:
            WNDENUMPROC = ctypes.WINFUNCTYPE(
                wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
            )

            @WNDENUMPROC
            def _enum_cb(hwnd, lparam):
                _callback(hwnd, None)
                return True

            hdesk = ctypes.windll.user32.OpenDesktopW(
                desktop, 0, False, 0x0040,  # DESKTOP_ENUMERATE
            )
            if hdesk:
                ctypes.windll.user32.EnumDesktopWindows(hdesk, _enum_cb, 0)
                ctypes.windll.user32.CloseDesktop(hdesk)
            else:
                win32gui.EnumWindows(_callback, None)
        except Exception:
            win32gui.EnumWindows(_callback, None)
    else:
        win32gui.EnumWindows(_callback, None)

    return {"ok": True, "windows": windows}


def _handle_focus(msg: dict, desktop: str) -> dict:
    hwnd = msg.get("hwnd")
    if not hwnd:
        return {"ok": False, "error": "Missing 'hwnd' in focus command"}

    hwnd = int(hwnd)
    if not win32gui.IsWindow(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    try:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
    try:
        win32gui.SetFocus(hwnd)
    except Exception:
        pass

    return {"ok": True}


# =====================================================================
# Command dispatch table
# =====================================================================

_HANDLERS: dict[str, callable] = {
    "ping":       _handle_ping,
    "screenshot": _handle_screenshot,
    "type":       _handle_type,
    "key":        _handle_key,
    "click":      _handle_click,
    "scroll":     _handle_scroll,
    "launch":     _handle_launch,
    "windows":    _handle_windows,
    "focus":      _handle_focus,
}


# =====================================================================
# Main loop -- pipe SERVER
# =====================================================================

def main() -> None:
    """Entry point.  Creates a named-pipe server and loops forever.

    The agent is the **pipe server**.  Each iteration:

    1. Create a new pipe instance.
    2. Wait for a client (the CLI) to connect.
    3. Read one JSON command, dispatch it, write one JSON response.
    4. Disconnect the client and close the pipe instance.
    5. Loop back to 1 (unless the command was ``"exit"``).
    """
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    if len(sys.argv) < 2:
        log.error("Usage: cdcs_agent.py <pipe_name> [desktop_name]")
        sys.exit(1)

    pipe_name = sys.argv[1]
    desktop = sys.argv[2] if len(sys.argv) >= 3 else _get_desktop_name()

    log.info(
        "CDCS Agent starting.  PID=%d  Desktop=%s  Pipe=%s",
        os.getpid(), desktop, pipe_name,
    )

    server = PipeServer(pipe_name)
    running = True

    while running:
        # 1. Create a fresh pipe instance for this connection.
        try:
            server.create_pipe()
        except Exception as exc:
            log.error("Failed to create pipe: %s", exc)
            time.sleep(1)
            continue

        # 2. Wait for a client to connect (blocks indefinitely).
        log.debug("Waiting for client connection on %s ...", pipe_name)
        try:
            connected = server.wait_for_client(timeout_seconds=0)
        except Exception as exc:
            log.error("wait_for_client failed: %s", exc)
            server.disconnect()
            time.sleep(0.5)
            continue

        if not connected:
            server.disconnect()
            continue

        log.debug("Client connected.")

        # 3. Read commands in a loop until client disconnects.
        try:
            while running:
                line = server.read_line()
                if line is None:
                    log.debug("Client disconnected.")
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    msg = json.loads(line)
                except json.JSONDecodeError as exc:
                    server.write_line(json.dumps({
                        "ok": False, "error": f"JSON parse error: {exc}",
                    }))
                    continue

                cmd = msg.get("cmd", "")
                log.debug("Received command: %s", cmd)

                # Handle "exit" specially.
                if cmd == "exit":
                    server.write_line(json.dumps({"ok": True}))
                    log.info("Exit command received, shutting down.")
                    running = False
                    break

                # Dispatch to handler.
                handler = _HANDLERS.get(cmd)
                if handler is None:
                    result = {"ok": False, "error": f"Unknown command: {cmd!r}"}
                else:
                    try:
                        result = handler(msg, desktop)
                    except Exception as exc:
                        log.exception("Handler for %r raised", cmd)
                        result = {"ok": False, "error": str(exc)}

                server.write_line(json.dumps(result))

        except Exception as exc:
            log.exception("Unexpected error in command loop")

        # 4. Disconnect and loop back for next client.
        server.disconnect()

    log.info("Agent shut down.")


if __name__ == "__main__":
    main()
