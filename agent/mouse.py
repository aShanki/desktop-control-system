"""Mouse input for CDCS agent.

Provides a **tiered click strategy** designed to work on hidden desktops
where the global cursor position is irrelevant:

    Tier 1 -- UI Automation ``InvokePattern`` (safest, no cursor movement)
    Tier 2 -- ``PostMessage`` WM_LBUTTON / WM_RBUTTON (reliable for most apps)
    Tier 3 -- ``SendInput`` (last resort; moves the *global* cursor)

Also provides scroll via ``PostMessage WM_MOUSEWHEEL``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time

import win32api
import win32con
import win32gui

log = logging.getLogger(__name__)

# ── Win32 constants ──────────────────────────────────────────────────

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_RBUTTONDBLCLK = 0x0206
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010

# SendInput mouse
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

WHEEL_DELTA = 120


# ── ctypes structures for SendInput ──────────────────────────────────

class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


_SendInput = ctypes.windll.user32.SendInput
_SendInput.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_int]
_SendInput.restype = ctypes.c_uint


# ── Tier 1: UI Automation ───────────────────────────────────────────

def _try_uia_click(hwnd: int, x: int, y: int) -> bool:
    """Attempt to click via UI Automation InvokePattern.

    Converts (x, y) client coords to screen coords, asks UIA for the
    element at that point, then tries ``Invoke()`` on it.

    Returns True on success, False if UIA could not handle it.
    """
    try:
        import comtypes  # noqa: F401
        import comtypes.client

        # Late-import the UIAutomation COM object.
        clsid_uia = comtypes.GUID("{FF48DBA4-60EF-4201-AA87-54103EEF594E}")
        iid_uia = comtypes.GUID("{30CBE57D-D9D0-452A-AB13-7AC5AC4825EE}")
        uia = comtypes.CoCreateInstance(clsid_uia, interface=None, clsctx=comtypes.CLSCTX_INPROC_SERVER)
        uia = uia.QueryInterface(comtypes.gen.UIAutomationClient.IUIAutomation)

        # Convert client → screen coordinates.
        pt = win32gui.ClientToScreen(hwnd, (x, y))

        # Find the element at that screen point.
        from comtypes.gen.UIAutomationClient import (
            IUIAutomationInvokePattern,
            UIA_InvokePatternId,
        )

        element = uia.ElementFromPoint(ctypes.wintypes.POINT(pt[0], pt[1]))
        if element is None:
            return False

        # Try to get InvokePattern.
        pattern = element.GetCurrentPattern(UIA_InvokePatternId)
        if pattern is None:
            return False

        invoke = pattern.QueryInterface(IUIAutomationInvokePattern)
        invoke.Invoke()
        log.debug("UIA Invoke succeeded at (%d, %d)", x, y)
        return True

    except ImportError:
        log.debug("comtypes not available, skipping UIA tier")
        return False
    except Exception as exc:
        log.debug("UIA click failed: %s", exc)
        return False


# ── Tier 2: PostMessage ──────────────────────────────────────────────

def _make_lparam(x: int, y: int) -> int:
    """Pack (x, y) into an LPARAM for mouse messages."""
    return (y << 16) | (x & 0xFFFF)


def _postmessage_click(
    hwnd: int, x: int, y: int, button: str = "left", double: bool = False
) -> bool:
    """Send a click via PostMessage.

    Returns True if both the down and up messages were posted successfully.
    """
    lparam = _make_lparam(x, y)

    if button == "right":
        msg_down = WM_RBUTTONDOWN
        msg_up = WM_RBUTTONUP
        msg_dbl = WM_RBUTTONDBLCLK
        wparam = MK_RBUTTON
    elif button == "middle":
        msg_down = WM_MBUTTONDOWN
        msg_up = WM_MBUTTONUP
        msg_dbl = 0  # No standard double-click for middle
        wparam = MK_MBUTTON
    else:  # left
        msg_down = WM_LBUTTONDOWN
        msg_up = WM_LBUTTONUP
        msg_dbl = WM_LBUTTONDBLCLK
        wparam = MK_LBUTTON

    try:
        # Send WM_MOUSEMOVE first so Qt6 (and other frameworks) update
        # their internal widget-under-cursor tracking before the click.
        win32api.PostMessage(hwnd, WM_MOUSEMOVE, 0, lparam)
        time.sleep(0.03)

        if double and msg_dbl:
            # Double-click: down, up, dblclk, up.
            win32api.PostMessage(hwnd, msg_down, wparam, lparam)
            time.sleep(0.02)
            win32api.PostMessage(hwnd, msg_up, 0, lparam)
            time.sleep(0.02)
            win32api.PostMessage(hwnd, msg_dbl, wparam, lparam)
            time.sleep(0.02)
            win32api.PostMessage(hwnd, msg_up, 0, lparam)
        else:
            win32api.PostMessage(hwnd, msg_down, wparam, lparam)
            time.sleep(0.05)
            win32api.PostMessage(hwnd, msg_up, 0, lparam)

        log.debug("PostMessage click at (%d, %d) button=%s", x, y, button)
        return True

    except Exception as exc:
        log.debug("PostMessage click failed: %s", exc)
        return False


# ── Tier 3: SendInput ────────────────────────────────────────────────

def _sendinput_click(
    hwnd: int, x: int, y: int, button: str = "left", double: bool = False
) -> bool:
    """Click using SetCursorPos + SendInput.

    Moves the global mouse cursor to the target position using SetCursorPos
    (which works correctly on hidden desktops), then sends down/up events
    without MOUSEEVENTF_ABSOLUTE to avoid coordinate mapping issues.
    """
    try:
        # Client → screen.
        sx, sy = win32gui.ClientToScreen(hwnd, (x, y))

        if button == "right":
            down_flag = MOUSEEVENTF_RIGHTDOWN
            up_flag = MOUSEEVENTF_RIGHTUP
        elif button == "middle":
            down_flag = MOUSEEVENTF_MIDDLEDOWN
            up_flag = MOUSEEVENTF_MIDDLEUP
        else:
            down_flag = MOUSEEVENTF_LEFTDOWN
            up_flag = MOUSEEVENTF_LEFTUP

        def _mi(flags, data=0):
            inp = INPUT()
            inp.type = INPUT_MOUSE
            inp._input.mi.dx = 0
            inp._input.mi.dy = 0
            inp._input.mi.mouseData = data
            inp._input.mi.dwFlags = flags
            inp._input.mi.time = 0
            inp._input.mi.dwExtraInfo = None
            return inp

        clicks = 2 if double else 1
        for _ in range(clicks):
            # Position cursor first using SetCursorPos (reliable on hidden desktops).
            ctypes.windll.user32.SetCursorPos(sx, sy)
            time.sleep(0.02)

            # Send click events at current cursor position (no ABSOLUTE flag).
            events = [
                _mi(down_flag),
                _mi(up_flag),
            ]
            arr = (INPUT * len(events))(*events)
            _SendInput(len(events), arr, ctypes.sizeof(INPUT))
            time.sleep(0.05)

        log.debug("SendInput click at screen (%d, %d)", sx, sy)
        return True

    except Exception as exc:
        log.debug("SendInput click failed: %s", exc)
        return False


# ── Public API ───────────────────────────────────────────────────────

def click(
    hwnd: int,
    x: int,
    y: int,
    button: str = "left",
    double: bool = False,
    method: str = "auto",
) -> dict:
    """Click at *(x, y)* in client coordinates of *hwnd*.

    *method* controls which input strategy to use:

    - ``"auto"`` (default) -- tries UIA → PostMessage → SendInput.
    - ``"sendinput"`` -- skip straight to SendInput (for Qt / Electron apps).
    - ``"postmessage"`` -- use PostMessage only.

    Returns ``{"ok": True, "method": "uia"|"postmessage"|"sendinput"}``.
    """
    if not win32gui.IsWindow(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    if method == "sendinput":
        if _sendinput_click(hwnd, x, y, button=button, double=double):
            return {"ok": True, "method": "sendinput", "x": x, "y": y}
        return {"ok": False, "error": "SendInput click failed"}

    # Tier 1: UIA
    if method == "auto" and button == "left" and not double:
        if _try_uia_click(hwnd, x, y):
            return {"ok": True, "method": "uia", "x": x, "y": y}

    # Tier 2: PostMessage
    if _postmessage_click(hwnd, x, y, button=button, double=double):
        return {"ok": True, "method": "postmessage", "x": x, "y": y}

    # Tier 3: SendInput
    if _sendinput_click(hwnd, x, y, button=button, double=double):
        return {"ok": True, "method": "sendinput", "x": x, "y": y}

    return {"ok": False, "error": "All click strategies failed"}


def scroll(hwnd: int, x: int, y: int, delta: int) -> dict:
    """Scroll at *(x, y)* in client coordinates of *hwnd*.

    *delta* is in "notches" -- positive scrolls up, negative scrolls down.
    Uses ``PostMessage WM_MOUSEWHEEL``.

    The wParam high word carries the wheel distance (delta * WHEEL_DELTA).
    The lParam carries **screen** coordinates (per the WM_MOUSEWHEEL spec).
    """
    if not win32gui.IsWindow(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    try:
        # WM_MOUSEWHEEL expects screen coords in lparam.
        sx, sy = win32gui.ClientToScreen(hwnd, (x, y))
        lparam = _make_lparam(sx, sy)

        wheel_amount = delta * WHEEL_DELTA
        # wParam: high word = wheel delta, low word = key state (0).
        wparam = (wheel_amount << 16) & 0xFFFFFFFF

        win32api.PostMessage(hwnd, WM_MOUSEWHEEL, wparam, lparam)
        return {"ok": True, "delta": delta}

    except Exception as exc:
        log.exception("scroll failed")
        return {"ok": False, "error": str(exc)}
