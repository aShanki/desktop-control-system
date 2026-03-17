#!/usr/bin/env python3
"""
CDCS Phase 1 GO/NO-GO Primitive Validation
===========================================
Validates 6 core Win32 primitives that the Claude Desktop Control System
depends on. If ANY test fails, the architecture will not work.

This is the gate before building anything else.

Dependencies: pywin32, Pillow
Python: 3.12+
Platform: Windows 10/11

Usage:
    python test_primitives.py
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from ctypes import (
    POINTER,
    WINFUNCTYPE,
    Structure,
    Union,
    byref,
    c_bool,
    c_byte,
    c_int,
    c_uint,
    c_ulong,
    c_ushort,
    c_void_p,
    create_string_buffer,
    create_unicode_buffer,
    sizeof,
    windll,
    wintypes,
)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import win32con
import win32gui
import win32ui
from PIL import Image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PYTHON_EXE = os.environ.get("CDCS_PYTHON_EXE", sys.executable)
SCRIPT_DIR = Path(__file__).parent.resolve()
OUTPUT_DIR = SCRIPT_DIR / "test_output"
TEST_TIMEOUT = 30  # seconds per test

# ---------------------------------------------------------------------------
# Win32 Constants
# ---------------------------------------------------------------------------
GENERIC_ALL = 0x10000000
DESKTOP_CREATEWINDOW = 0x0002
DESKTOP_WRITEOBJECTS = 0x0080
DESKTOP_SWITCHDESKTOP = 0x0100
DESKTOP_READOBJECTS = 0x0001
DESKTOP_ENUMERATE = 0x0040
DESKTOP_ALL_ACCESS = 0x01FF

STARTF_USESHOWWINDOW = 0x00000001
SW_SHOWNORMAL = 1
CREATE_NEW_CONSOLE = 0x00000010
CREATE_UNICODE_ENVIRONMENT = 0x00000400

PW_RENDERFULLCONTENT = 0x00000002

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_CHAR = 0x0102
MK_LBUTTON = 0x0001

INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002

PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_UNLIMITED_INSTANCES = 255
OPEN_EXISTING = 3
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------
_COLORS_ENABLED = True

def _supports_color() -> bool:
    """Check if the terminal supports ANSI colors."""
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Enable ANSI escape processing on Windows
        try:
            kernel32_h = ctypes.windll.kernel32
            STD_OUTPUT_HANDLE = -11
            ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            handle = kernel32_h.GetStdHandle(STD_OUTPUT_HANDLE)
            mode = wt.DWORD(0)
            kernel32_h.GetConsoleMode(handle, byref(mode))
            kernel32_h.SetConsoleMode(
                handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING
            )
            return True
        except Exception:
            return False
    return True


def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m" if _COLORS_ENABLED else text


def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m" if _COLORS_ENABLED else text


def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m" if _COLORS_ENABLED else text


def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m" if _COLORS_ENABLED else text


def _dim(text: str) -> str:
    return f"\033[2m{text}\033[0m" if _COLORS_ENABLED else text


# ---------------------------------------------------------------------------
# Win32 Structures
# ---------------------------------------------------------------------------

class STARTUPINFOW(Structure):
    _fields_ = [
        ("cb", wt.DWORD),
        ("lpReserved", wt.LPWSTR),
        ("lpDesktop", wt.LPWSTR),
        ("lpTitle", wt.LPWSTR),
        ("dwX", wt.DWORD),
        ("dwY", wt.DWORD),
        ("dwXSize", wt.DWORD),
        ("dwYSize", wt.DWORD),
        ("dwXCountChars", wt.DWORD),
        ("dwYCountChars", wt.DWORD),
        ("dwFillAttribute", wt.DWORD),
        ("dwFlags", wt.DWORD),
        ("wShowWindow", wt.WORD),
        ("cbReserved2", wt.WORD),
        ("lpReserved2", c_void_p),
        ("hStdInput", wt.HANDLE),
        ("hStdOutput", wt.HANDLE),
        ("hStdError", wt.HANDLE),
    ]


class PROCESS_INFORMATION(Structure):
    _fields_ = [
        ("hProcess", wt.HANDLE),
        ("hThread", wt.HANDLE),
        ("dwProcessId", wt.DWORD),
        ("dwThreadId", wt.DWORD),
    ]


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", wt.WORD),
        ("wScan", wt.WORD),
        ("dwFlags", wt.DWORD),
        ("time", wt.DWORD),
        ("dwExtraInfo", POINTER(c_ulong)),
    ]


class INPUT(Structure):
    class _INPUT_UNION(Union):
        _fields_ = [
            ("ki", KEYBDINPUT),
            ("padding", c_byte * 24),
        ]

    _fields_ = [
        ("type", wt.DWORD),
        ("union", _INPUT_UNION),
    ]


# ---------------------------------------------------------------------------
# Win32 API bindings
# ---------------------------------------------------------------------------
kernel32 = windll.kernel32
user32 = windll.user32

CreateDesktopW = user32.CreateDesktopW
CreateDesktopW.restype = wt.HANDLE
CreateDesktopW.argtypes = [
    wt.LPCWSTR, wt.LPCWSTR, c_void_p, wt.DWORD, wt.DWORD, c_void_p,
]

CloseDesktop = user32.CloseDesktop
CloseDesktop.restype = wt.BOOL
CloseDesktop.argtypes = [wt.HANDLE]

OpenDesktopW = user32.OpenDesktopW
OpenDesktopW.restype = wt.HANDLE
OpenDesktopW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.BOOL, wt.DWORD]

CreateProcessW = kernel32.CreateProcessW
CreateProcessW.restype = wt.BOOL
CreateProcessW.argtypes = [
    wt.LPCWSTR, wt.LPWSTR, c_void_p, c_void_p, wt.BOOL,
    wt.DWORD, c_void_p, wt.LPCWSTR,
    POINTER(STARTUPINFOW), POINTER(PROCESS_INFORMATION),
]

TerminateProcess = kernel32.TerminateProcess
TerminateProcess.restype = wt.BOOL
TerminateProcess.argtypes = [wt.HANDLE, c_uint]

WaitForSingleObject = kernel32.WaitForSingleObject
WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

CloseHandle = kernel32.CloseHandle

CreateNamedPipeW = kernel32.CreateNamedPipeW
CreateNamedPipeW.restype = wt.HANDLE
CreateNamedPipeW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD,
    wt.DWORD, wt.DWORD, wt.DWORD, c_void_p,
]

ConnectNamedPipe = kernel32.ConnectNamedPipe
ConnectNamedPipe.restype = wt.BOOL
ConnectNamedPipe.argtypes = [wt.HANDLE, c_void_p]

DisconnectNamedPipe = kernel32.DisconnectNamedPipe
DisconnectNamedPipe.restype = wt.BOOL
DisconnectNamedPipe.argtypes = [wt.HANDLE]

ReadFile = kernel32.ReadFile
WriteFile = kernel32.WriteFile

# Callback type for EnumDesktopWindows -- kept at module level so it
# is not garbage-collected while the callback is in use.
WNDENUMPROC = WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)


# ---------------------------------------------------------------------------
# TestResult
# ---------------------------------------------------------------------------
@dataclass
class TestResult:
    """Tracks the outcome of a single primitive test."""
    name: str
    description: str
    passed: bool = False
    message: str = ""
    elapsed_s: float = 0.0
    diagnostics: dict = field(default_factory=dict)

    def summary_line(self) -> str:
        tag = _green("[PASS]") if self.passed else _red("[FAIL]")
        elapsed = f"({self.elapsed_s:.1f}s)"
        line = f"  {tag} {self.name}: {self.description} {_dim(elapsed)}"
        if self.message:
            line += f"\n         {self.message}"
        if not self.passed and self.diagnostics:
            for k, v in self.diagnostics.items():
                line += f"\n         {_yellow(k)}: {v}"
        return line


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ensure_output_dir():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def create_hidden_desktop(name: str) -> wt.HANDLE:
    """Create a new hidden desktop with full access."""
    hdesk = CreateDesktopW(name, None, None, 0, DESKTOP_ALL_ACCESS, None)
    if not hdesk:
        raise OSError(
            f"CreateDesktopW failed for '{name}': error {kernel32.GetLastError()}"
        )
    return hdesk


def launch_on_desktop(desktop_name: str, cmd: str) -> PROCESS_INFORMATION:
    """Launch a process on the specified desktop via CreateProcessW."""
    si = STARTUPINFOW()
    si.cb = sizeof(STARTUPINFOW)
    si.lpDesktop = desktop_name
    si.dwFlags = STARTF_USESHOWWINDOW
    si.wShowWindow = SW_SHOWNORMAL

    pi = PROCESS_INFORMATION()
    cmd_buf = create_unicode_buffer(cmd)

    ok = CreateProcessW(
        None, cmd_buf, None, None, False,
        CREATE_NEW_CONSOLE | CREATE_UNICODE_ENVIRONMENT,
        None, None, byref(si), byref(pi),
    )
    if not ok:
        raise OSError(f"CreateProcessW failed: error {kernel32.GetLastError()}")
    return pi


def enum_desktop_windows(desktop_name: str) -> list[int]:
    """Enumerate all window handles on a given desktop."""
    hdesk = OpenDesktopW(desktop_name, 0, False, DESKTOP_READOBJECTS | DESKTOP_ENUMERATE)
    if not hdesk:
        return []
    hwnds: list[int] = []

    # The callback must be stored in a variable that outlives the call
    # to prevent garbage collection while the C code is using it.
    @WNDENUMPROC
    def _cb(hwnd, lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumDesktopWindows(hdesk, _cb, 0)
    CloseDesktop(hdesk)
    return hwnds


def _get_class_name(hwnd: int) -> str:
    """Get window class name using ctypes (works cross-desktop)."""
    buf = create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_text(hwnd: int) -> str:
    """Get window title using ctypes (works cross-desktop)."""
    buf = create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _get_window_pid(hwnd: int) -> int:
    """Get PID of window owner using ctypes (works cross-desktop)."""
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, byref(pid))
    return pid.value


def find_notepad_hwnd(
    desktop_name: str,
    timeout: float = 10.0,
    exclude_hwnds: Optional[set] = None,
) -> Optional[int]:
    """Find a Notepad top-level HWND on the given desktop within timeout.

    Uses ctypes GetClassNameW which works cross-desktop (unlike win32gui).
    Windows 11 Notepad spawns a child process with a different PID, so we
    use an exclude set (before/after diff) instead of PID matching.
    """
    if exclude_hwnds is None:
        exclude_hwnds = set()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnds = enum_desktop_windows(desktop_name)
        for hwnd in hwnds:
            if hwnd in exclude_hwnds:
                continue
            try:
                cls = _get_class_name(hwnd)
                if cls == "Notepad":
                    return hwnd
            except Exception:
                pass
        time.sleep(0.5)
    return None


def find_notepad_edit_hwnd(notepad_hwnd: int) -> Optional[int]:
    """Find the edit/RichEdit child window of a Notepad HWND.

    Modern Windows 11 Notepad uses a deep control hierarchy.
    We search recursively using ctypes (works cross-desktop).
    """
    edit_classes = {"Edit", "RichEditD2DPT", "RichEdit20W", "RichEdit50W", "Scintilla"}

    # Use ctypes EnumChildWindows for cross-desktop compatibility
    def _deep_search(parent_hwnd):
        children = []

        @WNDENUMPROC
        def _gather(hwnd, _):
            children.append(hwnd)
            return True

        user32.EnumChildWindows(parent_hwnd, _gather, 0)

        for ch in children:
            try:
                cls = _get_class_name(ch)
                if cls in edit_classes:
                    return ch
            except Exception:
                pass

        # Recurse into children
        for ch in children:
            found = _deep_search(ch)
            if found:
                return found
        return None

    return _deep_search(notepad_hwnd)


def capture_window(hwnd: int, filepath) -> Optional[Image.Image]:
    """Capture a window via PrintWindow and save as PNG. Returns the PIL Image."""
    filepath = Path(filepath)
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w = right - left
        h = bottom - top
        if w <= 0 or h <= 0:
            w, h = 800, 600

        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()

        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bitmap)

        result = user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)

        bmp_info = bitmap.GetInfo()
        bmp_bits = bitmap.GetBitmapBits(True)

        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits, "raw", "BGRX", 0, 1,
        )
        img.save(str(filepath), "PNG")

        # GDI cleanup
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        win32gui.DeleteObject(bitmap.GetHandle())

        return img
    except Exception as e:
        print(f"    {_dim(f'[DEBUG] capture_window error: {e}')}")
        return None


def image_pixel_stats(img: Optional[Image.Image]) -> dict:
    """Return diagnostic statistics about an image's pixel data."""
    if img is None:
        return {"error": "no image"}
    stats: dict = {
        "size": f"{img.size[0]}x{img.size[1]}",
        "mode": img.mode,
    }
    extrema = img.getextrema()
    stats["extrema"] = str(extrema)

    raw = img.tobytes()
    bpp = len(img.mode)
    total_pixels = len(raw) // bpp
    step = max(1, total_pixels // 2000)
    unique = set()
    for idx in range(0, total_pixels, step):
        offset = idx * bpp
        unique.add(raw[offset:offset + bpp])
        if len(unique) > 50:
            break
    stats["sampled_unique_colors"] = len(unique)
    return stats


def image_has_content(img: Optional[Image.Image]) -> bool:
    """Check that an image is not all-black or all-white (has real UI content)."""
    if img is None:
        return False
    extrema = img.getextrema()
    if all(lo == hi for lo, hi in extrema):
        return False
    raw = img.tobytes()
    bpp = len(img.mode)
    total_pixels = len(raw) // bpp
    if total_pixels == 0:
        return False
    step = max(1, total_pixels // 2000)
    pixel_set = set()
    for idx in range(0, total_pixels, step):
        offset = idx * bpp
        pixel_set.add(raw[offset:offset + bpp])
        if len(pixel_set) > 10:
            return True
    return len(pixel_set) > 3


def images_differ(img_a: Optional[Image.Image], img_b: Optional[Image.Image]) -> bool:
    """Return True if two images differ meaningfully."""
    if img_a is None or img_b is None:
        return img_a is not img_b
    if img_a.size != img_b.size:
        return True
    bytes_a = img_a.tobytes()
    bytes_b = img_b.tobytes()
    if bytes_a == bytes_b:
        return False
    bpp = len(img_a.mode)
    total_pixels = len(bytes_a) // bpp
    if total_pixels == 0:
        return False
    step = max(1, total_pixels // 3000)
    diffs = 0
    for idx in range(0, total_pixels, step):
        off = idx * bpp
        if bytes_a[off:off + bpp] != bytes_b[off:off + bpp]:
            diffs += 1
            if diffs > 20:
                return True
    return diffs > 5


def kill_process(pi: PROCESS_INFORMATION):
    """Terminate and clean up a process."""
    try:
        TerminateProcess(pi.hProcess, 0)
    except Exception:
        pass
    try:
        CloseHandle(pi.hProcess)
    except Exception:
        pass
    try:
        CloseHandle(pi.hThread)
    except Exception:
        pass


def kill_desktop_processes(desktop_name: str):
    """Kill all processes that have windows on the given desktop."""
    hdesk = OpenDesktopW(desktop_name, 0, False, DESKTOP_ALL_ACCESS)
    if not hdesk:
        return
    pids = set()

    @WNDENUMPROC
    def _cb(hwnd, _):
        pid = wt.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, byref(pid))
        if pid.value:
            pids.add(pid.value)
        return True

    user32.EnumDesktopWindows(hdesk, _cb, 0)
    CloseDesktop(hdesk)

    PROCESS_TERMINATE = 0x0001
    for pid in pids:
        try:
            h = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if h:
                kernel32.TerminateProcess(h, 0)
                kernel32.CloseHandle(h)
        except Exception:
            pass


def cleanup_desktop(hdesk, desktop_name: str = ""):
    """Kill all processes and close a desktop handle safely."""
    if desktop_name:
        try:
            kill_desktop_processes(desktop_name)
        except Exception:
            pass
    if hdesk:
        try:
            CloseDesktop(hdesk)
        except Exception:
            pass


def _make_helper_type_script(
    desktop_name: str,
    notepad_hwnd: int,
    text: str,
    signal_file: Path,
) -> str:
    """Generate the source code for a helper subprocess that types text
    into a notepad window on a hidden desktop via SendInput KEYEVENTF_UNICODE.

    The helper:
      1. Opens the target desktop and switches its thread to it.
      2. Dismisses any modal dialogs (file-not-found, etc.).
      3. Recursively finds the edit control inside notepad.
      4. Sets focus and types each character via SendInput.
      5. Writes a signal file when done.
    """
    return textwrap.dedent(f"""\
        import ctypes
        import ctypes.wintypes as wt
        import time
        import sys

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        DESKTOP_ALL = 0x01FF
        INPUT_KEYBOARD = 1
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002
        VK_RETURN = 0x0D
        VK_ESCAPE = 0x1B
        GW_ENABLEDPOPUP = 6

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [
                ("wVk", wt.WORD),
                ("wScan", wt.WORD),
                ("dwFlags", wt.DWORD),
                ("time", wt.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            ]

        class INPUT(ctypes.Structure):
            class _U(ctypes.Union):
                _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 24)]
            _fields_ = [("type", wt.DWORD), ("union", _U)]

        def send_vk(vk):
            inputs = (INPUT * 2)()
            inputs[0].type = INPUT_KEYBOARD
            inputs[0].union.ki.wVk = vk
            inputs[1].type = INPUT_KEYBOARD
            inputs[1].union.ki.wVk = vk
            inputs[1].union.ki.dwFlags = KEYEVENTF_KEYUP
            user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))

        def send_unicode_char(ch):
            inputs = (INPUT * 2)()
            inputs[0].type = INPUT_KEYBOARD
            inputs[0].union.ki.wScan = ord(ch)
            inputs[0].union.ki.dwFlags = KEYEVENTF_UNICODE
            inputs[1].type = INPUT_KEYBOARD
            inputs[1].union.ki.wScan = ord(ch)
            inputs[1].union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            user32.SendInput(2, ctypes.byref(inputs), ctypes.sizeof(INPUT))

        def main():
            desktop_name = "{desktop_name}"
            target_hwnd = {notepad_hwnd}
            signal_path = r"{signal_file}"
            text = "{text}"

            hdesk = user32.OpenDesktopW(desktop_name, 0, False, DESKTOP_ALL)
            if not hdesk:
                with open(signal_path, "w") as f:
                    f.write("FAIL:OpenDesktop")
                return

            user32.SetThreadDesktop(hdesk)
            time.sleep(0.5)

            # Dismiss any modal dialogs (file-not-found, etc.)
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.3)

            popup = user32.GetWindow(target_hwnd, GW_ENABLEDPOPUP)
            if popup and popup != target_hwnd:
                user32.SetForegroundWindow(popup)
                time.sleep(0.2)
                send_vk(VK_RETURN)
                time.sleep(0.5)

            send_vk(VK_ESCAPE)
            time.sleep(0.3)

            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.3)

            # Find edit child recursively
            edit_classes = {{"Edit", "RichEditD2DPT", "RichEdit20W", "RichEdit50W"}}
            edit_hwnd = None

            def find_edit(parent):
                nonlocal edit_hwnd
                WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
                def cb(hwnd, _):
                    nonlocal edit_hwnd
                    buf = ctypes.create_unicode_buffer(256)
                    user32.GetClassNameW(hwnd, buf, 256)
                    if buf.value in edit_classes:
                        edit_hwnd = hwnd
                        return False
                    find_edit(hwnd)
                    if edit_hwnd:
                        return False
                    return True
                user32.EnumChildWindows(parent, WNDENUMPROC(cb), 0)

            find_edit(target_hwnd)

            if edit_hwnd:
                user32.SetForegroundWindow(edit_hwnd)
                user32.SetFocus(edit_hwnd)
                time.sleep(0.3)

            for ch in text:
                send_unicode_char(ch)
                time.sleep(0.05)

            time.sleep(0.5)

            with open(signal_path, "w") as f:
                f.write("DONE")

            user32.CloseDesktop(hdesk)

        if __name__ == "__main__":
            main()
    """)


# ---------------------------------------------------------------------------
# Test 1: CreateDesktopW + CreateProcessW
# ---------------------------------------------------------------------------

def test1_create_desktop_and_process() -> TestResult:
    """Create a hidden desktop, launch notepad on it,
    verify notepad is NOT visible on the user's Default desktop."""
    result = TestResult(
        name="Test 1",
        description="CreateDesktopW + CreateProcessW",
    )
    desktop_name = f"cdcs-test-1-{os.getpid()}"
    hdesk = None
    pi = None
    t0 = time.monotonic()

    try:
        hdesk = create_hidden_desktop(desktop_name)

        # Snapshot Default desktop notepads BEFORE launch
        default_before = set()
        for hwnd in enum_desktop_windows("Default"):
            if _get_class_name(hwnd) == "Notepad":
                default_before.add(hwnd)

        pi = launch_on_desktop(desktop_name, "notepad.exe")
        time.sleep(4)

        # Check no NEW notepad appeared on Default desktop
        default_after = set()
        for hwnd in enum_desktop_windows("Default"):
            if _get_class_name(hwnd) == "Notepad":
                default_after.add(hwnd)

        new_on_default = default_after - default_before
        if new_on_default:
            result.passed = False
            result.message = "Notepad appeared on Default desktop -- isolation FAILURE"
            result.diagnostics["new_hwnds"] = list(new_on_default)
            return result

        # Verify notepad IS on the hidden desktop
        notepad_hwnd = find_notepad_hwnd(desktop_name, timeout=5)
        if notepad_hwnd is None:
            result.passed = False
            result.message = "Notepad not found on hidden desktop"
            result.diagnostics["desktop_windows"] = len(enum_desktop_windows(desktop_name))
            return result

        result.passed = True
        result.message = (
            f"Notepad runs on '{desktop_name}' "
            f"and is invisible on Default desktop"
        )
        return result

    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        if pi:
            kill_process(pi)
        cleanup_desktop(hdesk, desktop_name)


# ---------------------------------------------------------------------------
# Test 2: PrintWindow on hidden desktop HWND
# ---------------------------------------------------------------------------

def test2_printwindow() -> TestResult:
    """Create hidden desktop, launch notepad, find its HWND via
    EnumDesktopWindows, capture it with PrintWindow(PW_RENDERFULLCONTENT),
    verify the image has real UI content (not all black/blank)."""
    result = TestResult(
        name="Test 2",
        description="PrintWindow on hidden desktop HWND",
    )
    desktop_name = f"cdcs-test-2-{os.getpid()}"
    hdesk = None
    pi = None
    t0 = time.monotonic()

    try:
        hdesk = create_hidden_desktop(desktop_name)
        hwnds_before = set(enum_desktop_windows(desktop_name))
        pi = launch_on_desktop(desktop_name, "notepad.exe")
        time.sleep(4)

        notepad_hwnd = find_notepad_hwnd(desktop_name, timeout=10, exclude_hwnds=hwnds_before)
        if notepad_hwnd is None:
            result.passed = False
            result.message = "Could not find Notepad HWND on hidden desktop"
            result.diagnostics["total_windows"] = len(enum_desktop_windows(desktop_name))
            return result

        filepath = OUTPUT_DIR / "test2_printwindow.png"
        img = capture_window(notepad_hwnd, filepath)

        if img is None:
            result.passed = False
            result.message = "PrintWindow returned no image"
            return result

        stats = image_pixel_stats(img)
        result.diagnostics = stats

        if not image_has_content(img):
            result.passed = False
            result.message = (
                f"Image is all-black/blank (no real content). Saved to {filepath}"
            )
            return result

        result.passed = True
        result.message = f"Captured real UI content ({stats['size']}) -> {filepath}"
        return result

    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        if pi:
            kill_process(pi)
        cleanup_desktop(hdesk, desktop_name)


# ---------------------------------------------------------------------------
# Test 3: SendInput keyboard from process on hidden desktop
# ---------------------------------------------------------------------------

def test3_keyboard_input() -> TestResult:
    """Create hidden desktop, launch notepad, type text into it.

    Strategy (per PRD risk mitigation):
    1. Try SendInput KEYEVENTF_UNICODE from a helper on the hidden desktop
    2. If SendInput doesn't work, fall back to PostMessage WM_CHAR
    Both methods are architecturally isolated to the target HWND/desktop.
    """
    result = TestResult(
        name="Test 3",
        description="Keyboard input on hidden desktop (SendInput or PostMessage WM_CHAR)",
    )
    desktop_name = f"cdcs-test-3-{os.getpid()}"
    hdesk = None
    notepad_pi = None
    helper_pi = None
    tmp_script = None
    signal_file = None
    t0 = time.monotonic()

    try:
        hdesk = create_hidden_desktop(desktop_name)
        hwnds_before = set(enum_desktop_windows(desktop_name))
        default_notepads_before = set()
        for hwnd in enum_desktop_windows("Default"):
            if _get_class_name(hwnd) == "Notepad":
                default_notepads_before.add(hwnd)

        notepad_pi = launch_on_desktop(desktop_name, "notepad.exe")
        time.sleep(4)

        notepad_hwnd = find_notepad_hwnd(
            desktop_name, timeout=10, exclude_hwnds=hwnds_before
        )
        if notepad_hwnd is None:
            result.passed = False
            result.message = "Could not find Notepad HWND"
            return result

        # Find the edit control for PostMessage fallback
        edit_hwnd = find_notepad_edit_hwnd(notepad_hwnd)
        target_hwnd = edit_hwnd if edit_hwnd else notepad_hwnd
        result.diagnostics["edit_hwnd_found"] = edit_hwnd is not None

        # Screenshot BEFORE
        before_path = OUTPUT_DIR / "test3_before.png"
        img_before = capture_window(notepad_hwnd, before_path)

        # --- Attempt 1: SendInput via helper subprocess ---
        signal_file = Path(tempfile.mktemp(suffix=".signal", prefix="cdcs_t3_"))
        helper_code = _make_helper_type_script(
            desktop_name, notepad_hwnd, "CDCS_TEST_HELLO", signal_file,
        )
        tmp_script = Path(tempfile.mktemp(suffix=".py", prefix="cdcs_helper_t3_"))
        tmp_script.write_text(helper_code, encoding="utf-8")
        helper_pi = launch_on_desktop(desktop_name, f'"{PYTHON_EXE}" "{tmp_script}"')

        deadline = time.monotonic() + TEST_TIMEOUT
        while time.monotonic() < deadline:
            if signal_file.exists():
                break
            time.sleep(0.5)

        time.sleep(1)
        mid_path = OUTPUT_DIR / "test3_after_sendinput.png"
        img_mid = capture_window(notepad_hwnd, mid_path)
        sendinput_worked = images_differ(img_before, img_mid)
        result.diagnostics["sendinput_worked"] = sendinput_worked

        if sendinput_worked:
            method = "SendInput"
            img_after = img_mid
            after_path = mid_path
        else:
            # --- Attempt 2: PostMessage WM_CHAR (PRD fallback) ---
            # Click into the edit area first
            lparam = (50 & 0xFFFF) | ((50 << 16) & 0xFFFF0000)
            user32.PostMessageW(target_hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            time.sleep(0.1)
            user32.PostMessageW(target_hwnd, WM_LBUTTONUP, 0, lparam)
            time.sleep(0.3)

            test_text = "CDCS_TEST_HELLO"
            for ch in test_text:
                user32.PostMessageW(target_hwnd, WM_CHAR, ord(ch), 0)
                time.sleep(0.03)

            time.sleep(1)
            after_path = OUTPUT_DIR / "test3_after.png"
            img_after = capture_window(notepad_hwnd, after_path)
            method = "PostMessage WM_CHAR"

        if img_after is None:
            result.passed = False
            result.message = "Could not capture window after typing"
            return result

        changed = images_differ(img_before, img_after)
        result.diagnostics["method"] = method
        result.diagnostics["before_stats"] = image_pixel_stats(img_before)
        result.diagnostics["after_stats"] = image_pixel_stats(img_after)

        # Verify NO new Notepad on Default desktop
        default_notepads_after = set()
        for hwnd in enum_desktop_windows("Default"):
            if _get_class_name(hwnd) == "Notepad":
                default_notepads_after.add(hwnd)
        new_on_default = default_notepads_after - default_notepads_before
        if new_on_default:
            result.passed = False
            result.message = "Notepad appeared on Default desktop -- isolation failure"
            return result

        if changed:
            result.passed = True
            result.message = (
                f"Typed 'CDCS_TEST_HELLO' via {method} -- screenshot changed. "
                f"No windows on Default desktop affected. -> {after_path}"
            )
        else:
            result.passed = False
            result.message = (
                "Screenshot did not change after typing via both "
                "SendInput and PostMessage WM_CHAR"
            )

        return result

    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        if helper_pi:
            kill_process(helper_pi)
        if notepad_pi:
            kill_process(notepad_pi)
        cleanup_desktop(hdesk, desktop_name)
        if tmp_script and tmp_script.exists():
            try:
                tmp_script.unlink()
            except Exception:
                pass
        if signal_file and signal_file.exists():
            try:
                signal_file.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 4: PostMessage mouse click on hidden desktop HWND
# ---------------------------------------------------------------------------

def test4_postmessage_mouse_click() -> TestResult:
    """Create hidden desktop, launch notepad, send WM_LBUTTONDOWN/UP via
    PostMessage to click the text area, then type text via WM_CHAR.
    Verify the notepad content changed."""
    result = TestResult(
        name="Test 4",
        description="PostMessage mouse click + WM_CHAR type on hidden desktop",
    )
    desktop_name = f"cdcs-test-4-{os.getpid()}"
    hdesk = None
    notepad_pi = None
    t0 = time.monotonic()

    try:
        hdesk = create_hidden_desktop(desktop_name)
        hwnds_before = set(enum_desktop_windows(desktop_name))
        notepad_pi = launch_on_desktop(desktop_name, "notepad.exe")
        time.sleep(4)

        notepad_hwnd = find_notepad_hwnd(
            desktop_name, timeout=10, exclude_hwnds=hwnds_before
        )
        if notepad_hwnd is None:
            result.passed = False
            result.message = "Could not find Notepad HWND"
            return result

        # Find the edit area
        edit_hwnd = find_notepad_edit_hwnd(notepad_hwnd)
        target = edit_hwnd if edit_hwnd else notepad_hwnd
        result.diagnostics["edit_hwnd_found"] = edit_hwnd is not None
        result.diagnostics["target_class"] = _get_class_name(target)

        # Screenshot before
        before_path = OUTPUT_DIR / "test4_before.png"
        img_before = capture_window(notepad_hwnd, before_path)

        # PostMessage: mouse click at (50, 50) inside the target control
        lparam = (50 & 0xFFFF) | ((50 << 16) & 0xFFFF0000)
        user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
        time.sleep(0.1)
        user32.PostMessageW(target, WM_LBUTTONUP, 0, lparam)
        time.sleep(0.3)

        # Type via WM_CHAR
        test_text = "POST_MSG_OK"
        for ch in test_text:
            user32.PostMessageW(target, WM_CHAR, ord(ch), 0)
            time.sleep(0.03)

        time.sleep(1)

        # Screenshot after
        after_path = OUTPUT_DIR / "test4_after.png"
        img_after = capture_window(notepad_hwnd, after_path)

        if img_after is None:
            result.passed = False
            result.message = "Could not capture window after PostMessage"
            return result

        result.diagnostics["before_stats"] = image_pixel_stats(img_before)
        result.diagnostics["after_stats"] = image_pixel_stats(img_after)

        changed = images_differ(img_before, img_after)

        if changed:
            result.passed = True
            result.message = (
                f"PostMessage click + WM_CHAR type produced visible change -> {after_path}"
            )
        else:
            result.passed = False
            result.message = (
                "Screenshot did not change after PostMessage click + type"
            )

        return result

    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        if notepad_pi:
            kill_process(notepad_pi)
        cleanup_desktop(hdesk, desktop_name)


# ---------------------------------------------------------------------------
# Test 5: Named pipe communication
# ---------------------------------------------------------------------------

def test5_named_pipe() -> TestResult:
    """Create a named pipe, launch a subprocess on a hidden desktop that
    connects to it, send a JSON command, receive a JSON response.
    Verify round-trip JSON communication works."""
    result = TestResult(
        name="Test 5",
        description="Named pipe JSON round-trip communication",
    )
    pipe_name = r"\\.\pipe\cdcs-test-pipe"
    desktop_name = "cdcs-test-prim"
    hdesk = None
    pipe_handle = None
    helper_pi = None
    tmp_script = None
    t0 = time.monotonic()

    try:
        hdesk = create_hidden_desktop(desktop_name)

        # Create named pipe (server side)
        pipe_handle = CreateNamedPipeW(
            pipe_name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES,
            4096, 4096, 5000, None,
        )

        if pipe_handle == INVALID_HANDLE_VALUE or pipe_handle is None:
            result.passed = False
            result.message = f"CreateNamedPipeW failed: error {kernel32.GetLastError()}"
            return result

        # Helper script that connects to the pipe, reads JSON, sends response
        helper_code = textwrap.dedent(f"""\
            import ctypes
            import ctypes.wintypes as wt
            import json
            import time

            kernel32 = ctypes.windll.kernel32
            GENERIC_READ = 0x80000000
            GENERIC_WRITE = 0x40000000
            OPEN_EXISTING = 3

            def main():
                pipe_name = r"{pipe_name}"

                handle = None
                for _ in range(20):
                    handle = kernel32.CreateFileW(
                        pipe_name,
                        GENERIC_READ | GENERIC_WRITE,
                        0, None,
                        OPEN_EXISTING,
                        0, None,
                    )
                    if handle != ctypes.c_void_p(-1).value and handle not in (0, -1):
                        break
                    time.sleep(0.25)
                else:
                    return

                # Read command
                buf = ctypes.create_string_buffer(4096)
                bytes_read = wt.DWORD(0)
                ok = kernel32.ReadFile(handle, buf, 4096, ctypes.byref(bytes_read), None)
                if ok and bytes_read.value > 0:
                    cmd = json.loads(buf.value[:bytes_read.value].decode("utf-8"))
                    if cmd.get("cmd") == "ping":
                        response = json.dumps({{"ok": True, "pong": True, "echo": cmd}}).encode("utf-8")
                        bytes_written = wt.DWORD(0)
                        kernel32.WriteFile(
                            handle, response, len(response),
                            ctypes.byref(bytes_written), None,
                        )
                        kernel32.FlushFileBuffers(handle)

                kernel32.CloseHandle(handle)

            if __name__ == "__main__":
                main()
        """)

        tmp_script = Path(tempfile.mktemp(suffix=".py", prefix="cdcs_helper_t5_"))
        tmp_script.write_text(helper_code, encoding="utf-8")

        cmd = f'"{PYTHON_EXE}" "{tmp_script}"'
        helper_pi = launch_on_desktop(desktop_name, cmd)

        # Wait for client to connect
        ConnectNamedPipe(pipe_handle, None)

        # Send command
        cmd_json = json.dumps({"cmd": "ping"}).encode("utf-8")
        bytes_written = wt.DWORD(0)
        ok = WriteFile(pipe_handle, cmd_json, len(cmd_json), byref(bytes_written), None)
        if not ok:
            result.passed = False
            result.message = f"WriteFile to pipe failed: error {kernel32.GetLastError()}"
            return result

        kernel32.FlushFileBuffers(pipe_handle)

        # Read response
        buf = create_string_buffer(4096)
        bytes_read = wt.DWORD(0)
        ok = ReadFile(pipe_handle, buf, 4096, byref(bytes_read), None)
        if not ok:
            result.passed = False
            result.message = f"ReadFile from pipe failed: error {kernel32.GetLastError()}"
            return result

        response_text = buf.value[:bytes_read.value].decode("utf-8")
        response = json.loads(response_text)

        result.diagnostics["response"] = response

        if response.get("ok") is True and response.get("pong") is True:
            result.passed = True
            result.message = f"Pipe round-trip success: {response}"
        else:
            result.passed = False
            result.message = f"Unexpected response: {response}"

        return result

    except json.JSONDecodeError as e:
        result.passed = False
        result.message = f"Invalid JSON response: {e}"
        return result
    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        if pipe_handle and pipe_handle != INVALID_HANDLE_VALUE:
            try:
                DisconnectNamedPipe(pipe_handle)
                CloseHandle(pipe_handle)
            except Exception:
                pass
        if helper_pi:
            kill_process(helper_pi)
        cleanup_desktop(hdesk, desktop_name)
        if tmp_script and tmp_script.exists():
            try:
                tmp_script.unlink()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Test 6: Parallel isolation
# ---------------------------------------------------------------------------

def test6_parallel_isolation() -> TestResult:
    """Create TWO hidden desktops, launch notepad on each, type different text
    via PostMessage WM_CHAR. Screenshot both and verify they differ (no crosstalk)."""
    result = TestResult(
        name="Test 6",
        description="Parallel isolation -- two hidden desktops, no crosstalk",
    )
    mypid = os.getpid()
    desktop_names = [f"cdcs-test-6a-{mypid}", f"cdcs-test-6b-{mypid}"]
    texts = ["AAA_ISOLATION", "BBB_ISOLATION"]
    hdesks: list = [None, None]
    notepad_pis: list = [None, None]
    t0 = time.monotonic()

    try:
        # Create both desktops and launch notepad on each
        hwnds_before: list = [set(), set()]
        for i in range(2):
            hdesks[i] = create_hidden_desktop(desktop_names[i])
            hwnds_before[i] = set(enum_desktop_windows(desktop_names[i]))
            notepad_pis[i] = launch_on_desktop(desktop_names[i], "notepad.exe")

        time.sleep(4)

        # Find notepad HWNDs and edit controls
        notepad_hwnds: list = [None, None]
        edit_hwnds: list = [None, None]
        for i in range(2):
            notepad_hwnds[i] = find_notepad_hwnd(
                desktop_names[i], timeout=10, exclude_hwnds=hwnds_before[i]
            )
            if notepad_hwnds[i] is None:
                result.passed = False
                result.message = f"Could not find Notepad on {desktop_names[i]}"
                return result
            eh = find_notepad_edit_hwnd(notepad_hwnds[i])
            edit_hwnds[i] = eh if eh else notepad_hwnds[i]

        # Screenshot both BEFORE typing
        imgs_before: list = [None, None]
        for i in range(2):
            path = OUTPUT_DIR / f"test6_desktop_{i}_before.png"
            imgs_before[i] = capture_window(notepad_hwnds[i], path)

        # Type different text into each via PostMessage WM_CHAR
        for i in range(2):
            target = edit_hwnds[i]
            # Click into the edit area
            lparam = (50 & 0xFFFF) | ((50 << 16) & 0xFFFF0000)
            user32.PostMessageW(target, WM_LBUTTONDOWN, MK_LBUTTON, lparam)
            time.sleep(0.05)
            user32.PostMessageW(target, WM_LBUTTONUP, 0, lparam)
            time.sleep(0.2)
            for ch in texts[i]:
                user32.PostMessageW(target, WM_CHAR, ord(ch), 0)
                time.sleep(0.02)

        time.sleep(1)

        # Screenshot both AFTER typing
        imgs: list = [None, None]
        for i in range(2):
            path = OUTPUT_DIR / f"test6_desktop_{i}.png"
            imgs[i] = capture_window(notepad_hwnds[i], path)
            if imgs[i] is None:
                result.passed = False
                result.message = f"Could not capture {desktop_names[i]}"
                return result

        result.diagnostics["desktop_0_stats"] = image_pixel_stats(imgs[0])
        result.diagnostics["desktop_1_stats"] = image_pixel_stats(imgs[1])

        # Both should have changed from their before state
        for i in range(2):
            if imgs_before[i] and not images_differ(imgs_before[i], imgs[i]):
                result.passed = False
                result.message = f"Desktop {i} did not change after typing"
                return result

        # The two screenshots must be DIFFERENT
        if not images_differ(imgs[0], imgs[1]):
            result.passed = False
            result.message = (
                "Both desktops appear identical -- possible crosstalk or typing failure"
            )
            return result

        result.passed = True
        result.message = (
            f"Parallel isolation confirmed: desktop-0 ({texts[0]}) and "
            f"desktop-1 ({texts[1]}) have different content. "
            f"Screenshots: test6_desktop_0.png, test6_desktop_1.png"
        )
        return result

    except Exception as e:
        result.passed = False
        result.message = f"Exception: {e}"
        result.diagnostics["traceback"] = traceback.format_exc()
        return result
    finally:
        result.elapsed_s = time.monotonic() - t0
        for i in range(2):
            if notepad_pis[i]:
                kill_process(notepad_pis[i])
            cleanup_desktop(hdesks[i], desktop_names[i])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    global _COLORS_ENABLED
    _COLORS_ENABLED = _supports_color()

    print()
    print(_bold("=" * 64))
    print(_bold("  CDCS Phase 1 -- Primitive Validation (GO / NO-GO)"))
    print(_bold("=" * 64))
    print()

    ensure_output_dir()

    tests = [
        ("Test 1: CreateDesktopW + CreateProcessW",      test1_create_desktop_and_process),
        ("Test 2: PrintWindow on hidden desktop HWND",    test2_printwindow),
        ("Test 3: Keyboard input on hidden desktop",        test3_keyboard_input),
        ("Test 4: PostMessage mouse click + WM_CHAR",     test4_postmessage_mouse_click),
        ("Test 5: Named pipe JSON communication",         test5_named_pipe),
        ("Test 6: Parallel isolation (two desktops)",      test6_parallel_isolation),
    ]

    results: list[TestResult] = []

    for label, test_fn in tests:
        print(f"  {_dim('[....]')} {label} ...")
        tr = test_fn()
        results.append(tr)
        tag = _green("[PASS]") if tr.passed else _red("[FAIL]")
        # Overwrite the "[....]" line using carriage return trick
        print(f"  {tag} {label}")
        if tr.message:
            print(f"         {tr.message}")
        if not tr.passed and tr.diagnostics:
            for k, v in tr.diagnostics.items():
                if k == "traceback":
                    print(f"         {_yellow('traceback')}:")
                    for line in str(v).strip().splitlines():
                        print(f"           {line}")
                else:
                    print(f"         {_yellow(k)}: {v}")
        print()

    # Summary
    passed = sum(1 for r in results if r.passed)
    total = len(results)

    print(_bold("=" * 64))
    print(_bold("  SUMMARY"))
    print(_bold("-" * 64))
    for tr in results:
        print(tr.summary_line())
    print(_bold("-" * 64))

    if passed == total:
        print()
        print(f"  {_green(_bold(f'>>> GO -- All {total}/{total} primitives validated <<<'))}")
        print()
    else:
        failed = total - passed
        print()
        print(f"  {_red(_bold(f'>>> NO-GO -- {failed} primitive(s) FAILED ({passed}/{total} passed) <<<'))}")
        print(f"  {_red('Fix failing primitives before proceeding with CDCS build.')}")
        print()

    print(_bold("=" * 64))
    print(f"  Output directory: {OUTPUT_DIR}")
    print()

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
