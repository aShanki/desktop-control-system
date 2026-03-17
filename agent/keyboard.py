"""Keyboard input for CDCS agent.

Two delivery methods are supported:

1. **PostMessage** (primary) -- sends ``WM_CHAR`` / ``WM_KEYDOWN`` /
   ``WM_KEYUP`` messages directly to a target HWND.  This works reliably
   from a process running on a hidden Win32 desktop (Win11 modern Notepad,
   etc.) where ``SendInput`` with ``KEYEVENTF_UNICODE`` is silently dropped.

2. **SendInput** (fallback) -- the legacy path used when no HWND is given.
   Works for apps that happen to accept broadcast input events.

Public functions:

* ``type_text(text, hwnd=None)`` -- types arbitrary Unicode text.
* ``send_key_combo(combo, hwnd=None)`` -- sends a key combination like
  ``"ctrl+shift+e"``.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import time

log = logging.getLogger(__name__)

user32 = ctypes.windll.user32

# ── PostMessage constants ───────────────────────────────────────────

WM_KEYDOWN    = 0x0100
WM_KEYUP      = 0x0101
WM_CHAR       = 0x0102
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP   = 0x0105

# ── ctypes structures for SendInput ──────────────────────────────────

INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
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
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


_SendInput = user32.SendInput
_SendInput.argtypes = [
    ctypes.c_uint,
    ctypes.c_void_p,
    ctypes.c_int,
]
_SendInput.restype = ctypes.c_uint


# ── Virtual-key code table ───────────────────────────────────────────

# Modifiers
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12       # Alt
VK_LWIN = 0x5B

# Navigation / editing
VK_RETURN = 0x0D
VK_TAB = 0x09
VK_ESCAPE = 0x1B
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21      # Page Up
VK_NEXT = 0x22       # Page Down
VK_SPACE = 0x20
VK_INSERT = 0x2D
VK_SNAPSHOT = 0x2C   # Print Screen
VK_PAUSE = 0x13
VK_CAPITAL = 0x14    # Caps Lock
VK_NUMLOCK = 0x90
VK_SCROLL = 0x91     # Scroll Lock

# Arrow keys
VK_UP = 0x26
VK_DOWN = 0x28
VK_LEFT = 0x25
VK_RIGHT = 0x27

# Function keys
VK_F1 = 0x70
VK_F2 = 0x71
VK_F3 = 0x72
VK_F4 = 0x73
VK_F5 = 0x74
VK_F6 = 0x75
VK_F7 = 0x76
VK_F8 = 0x77
VK_F9 = 0x78
VK_F10 = 0x79
VK_F11 = 0x7A
VK_F12 = 0x7B

# Friendly-name -> virtual-key code mapping.
_KEY_MAP: dict[str, int] = {
    "ctrl": VK_CONTROL,
    "control": VK_CONTROL,
    "alt": VK_MENU,
    "shift": VK_SHIFT,
    "win": VK_LWIN,
    "super": VK_LWIN,
    "enter": VK_RETURN,
    "return": VK_RETURN,
    "tab": VK_TAB,
    "escape": VK_ESCAPE,
    "esc": VK_ESCAPE,
    "backspace": VK_BACK,
    "delete": VK_DELETE,
    "del": VK_DELETE,
    "home": VK_HOME,
    "end": VK_END,
    "pageup": VK_PRIOR,
    "pagedown": VK_NEXT,
    "space": VK_SPACE,
    "insert": VK_INSERT,
    "ins": VK_INSERT,
    "printscreen": VK_SNAPSHOT,
    "pause": VK_PAUSE,
    "capslock": VK_CAPITAL,
    "numlock": VK_NUMLOCK,
    "scrolllock": VK_SCROLL,
    "up": VK_UP,
    "down": VK_DOWN,
    "left": VK_LEFT,
    "right": VK_RIGHT,
    "menu": VK_MENU,
    "lwin": VK_LWIN,
    "back": VK_BACK,
    "pgup": VK_PRIOR,
    "pgdn": VK_NEXT,
    "prtsc": VK_SNAPSHOT,
    "f1": VK_F1,
    "f2": VK_F2,
    "f3": VK_F3,
    "f4": VK_F4,
    "f5": VK_F5,
    "f6": VK_F6,
    "f7": VK_F7,
    "f8": VK_F8,
    "f9": VK_F9,
    "f10": VK_F10,
    "f11": VK_F11,
    "f12": VK_F12,
    **{f"f{i}": 0x70 + i - 1 for i in range(13, 25)},
}

# Keys that need the EXTENDEDKEY flag.
_EXTENDED_KEYS = {
    VK_DELETE, VK_HOME, VK_END, VK_PRIOR, VK_NEXT, VK_INSERT,
    VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_LWIN, VK_SNAPSHOT,
}

# Modifier VK codes (we need to know which keys to "hold").
_MODIFIER_VKS = {VK_CONTROL, VK_MENU, VK_SHIFT, VK_LWIN}


# ── PostMessage helpers ─────────────────────────────────────────────

def _make_lparam(scan_code: int, extended: bool = False, is_up: bool = False) -> int:
    """Build the lparam for WM_KEYDOWN / WM_KEYUP messages.

    Bit layout:
      0-15  : repeat count (1)
      16-23 : scan code
      24    : extended key flag
      29    : context code (0 for normal keys)
      30    : previous key state (0 for down, 1 for up)
      31    : transition state (0 for down, 1 for up)
    """
    lparam = 1  # repeat count
    lparam |= (scan_code & 0xFF) << 16
    if extended:
        lparam |= 1 << 24
    if is_up:
        lparam |= 1 << 30  # previous key state
        lparam |= 1 << 31  # transition state
    return lparam


def _make_syslparam(scan_code: int, extended: bool = False, is_up: bool = False) -> int:
    """Build lparam for WM_SYSKEYDOWN / WM_SYSKEYUP (Alt combos).

    Same as _make_lparam but also sets bit 29 (context code = 1 for Alt).
    """
    lparam = _make_lparam(scan_code, extended, is_up)
    lparam |= 1 << 29  # context code: Alt is held
    return lparam


def _post_key(hwnd: int, vk: int, extended: bool = False) -> None:
    """Send a single key press+release via PostMessage to *hwnd*."""
    scan = user32.MapVirtualKeyW(vk, 0)  # MAPVK_VK_TO_VSC
    lp_down = _make_lparam(scan, extended)
    lp_up = _make_lparam(scan, extended, is_up=True)
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lp_down)
    time.sleep(0.02)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lp_up)


def _post_char(hwnd: int, char: str) -> None:
    """Send a single character via PostMessage WM_CHAR to *hwnd*."""
    user32.PostMessageW(hwnd, WM_CHAR, ord(char), 0)


# ── Edit control messages for reliable modifier combos ──────────────

# Standard edit control messages that bypass GetKeyState() entirely.
EM_SETSEL   = 0x00B1   # wParam=start, lParam=end  (-1 = select all)
EM_UNDO     = 0x00C7
WM_COPY     = 0x0301
WM_PASTE    = 0x0302
WM_CUT      = 0x0300
WM_UNDO     = 0x0304
WM_CLEAR    = 0x0303

# Map of (frozenset-of-modifier-vks, main-vk) → function(hwnd) for
# well-known edit operations.  These send dedicated Win32 messages that
# edit controls handle regardless of keyboard state.
def _edit_select_all(hwnd: int) -> None:
    user32.PostMessageW(hwnd, EM_SETSEL, 0, -1)

def _edit_copy(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_COPY, 0, 0)

def _edit_paste(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_PASTE, 0, 0)

def _edit_cut(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CUT, 0, 0)

def _edit_undo(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_UNDO, 0, 0)

def _edit_clear(hwnd: int) -> None:
    user32.PostMessageW(hwnd, WM_CLEAR, 0, 0)

# ctrl+key → dedicated edit message
_CTRL_EDIT_OPS: dict[int, callable] = {
    0x41: _edit_select_all,  # Ctrl+A  (VK_A = 0x41)
    0x43: _edit_copy,        # Ctrl+C  (VK_C = 0x43)
    0x56: _edit_paste,       # Ctrl+V  (VK_V = 0x56)
    0x58: _edit_cut,         # Ctrl+X  (VK_X = 0x58)
    0x5A: _edit_undo,        # Ctrl+Z  (VK_Z = 0x5A)
}


# ── SendInput helpers (legacy / fallback) ───────────────────────────

def _make_key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    """Build a single keyboard INPUT structure."""
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp._input.ki.wVk = vk
    inp._input.ki.wScan = scan
    inp._input.ki.dwFlags = flags
    inp._input.ki.time = 0
    inp._input.ki.dwExtraInfo = None
    return inp


def _send(inputs: list[INPUT]) -> int:
    """Call SendInput with an array of INPUT structures."""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = _SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        log.warning("SendInput sent %d/%d events", sent, n)
    return sent


def _resolve_key(name: str) -> int:
    """Map a human-readable key name to a virtual-key code.

    Single printable characters are mapped via ``VkKeyScanW`` which returns
    the VK code (low byte) plus modifier state (high byte).  We only use
    the low byte here; the caller is responsible for handling shift state
    when using ``send_key_combo``.
    """
    lower = name.lower().strip()
    if lower in _KEY_MAP:
        return _KEY_MAP[lower]
    if len(name) == 1:
        # Use VkKeyScanW to translate the character.
        vk_scan = user32.VkKeyScanW(ord(name))
        if vk_scan == -1:
            raise ValueError(f"Cannot map character {name!r} to a virtual key")
        return vk_scan & 0xFF
    raise ValueError(f"Unknown key name: {name!r}")


# ── Public API ───────────────────────────────────────────────────────

def send_key(vk_code: int, scan_code: int = 0, extended: bool = False) -> None:
    """Low-level single key press and release via SendInput.

    Sends a key-down followed by a key-up event for the given virtual-key
    code.  If *extended* is True the ``KEYEVENTF_EXTENDEDKEY`` flag is set.
    """
    flags_down = 0
    flags_up = KEYEVENTF_KEYUP
    if extended:
        flags_down |= KEYEVENTF_EXTENDEDKEY
        flags_up |= KEYEVENTF_EXTENDEDKEY
    _send([
        _make_key_input(vk=vk_code, scan=scan_code, flags=flags_down),
        _make_key_input(vk=vk_code, scan=scan_code, flags=flags_up),
    ])


def type_text(text: str, hwnd: int = None, method: str = "auto") -> dict:
    """Type *text* character by character.

    *method* can be ``"auto"`` (default), ``"sendinput"``, or ``"postmessage"``.
    When ``"sendinput"`` is specified, always uses SendInput regardless of hwnd.

    If *hwnd* is provided and method is auto, uses ``PostMessage(WM_CHAR)``
    targeted at that window handle -- this is the reliable path for
    hidden-desktop scenarios.

    If *hwnd* is ``None``, falls back to ``SendInput`` with
    ``KEYEVENTF_UNICODE`` (legacy path, may be silently dropped on Win11
    modern apps from a hidden desktop).

    Returns ``{"ok": True, "chars": N, "method": "postmessage"|"sendinput"}``.
    """
    if not text:
        return {"ok": True, "chars": 0, "method": "none"}

    if method == "sendinput":
        log.debug("type_text via SendInput (forced), %d chars", len(text))
        inputs: list[INPUT] = []
        for ch in text:
            code = ord(ch)
            inputs.append(
                _make_key_input(vk=0, scan=code, flags=KEYEVENTF_UNICODE)
            )
            inputs.append(
                _make_key_input(
                    vk=0, scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                )
            )
        _send(inputs)
        return {"ok": True, "chars": len(text), "method": "sendinput"}

    if hwnd is not None:
        # ── Primary path: PostMessage WM_CHAR ──
        log.debug("type_text via PostMessage WM_CHAR to hwnd=%#x, %d chars", hwnd, len(text))
        for ch in text:
            _post_char(hwnd, ch)
            time.sleep(0.005)  # small inter-char delay for reliability
        return {"ok": True, "chars": len(text), "method": "postmessage"}

    # ── Fallback: SendInput KEYEVENTF_UNICODE ──
    log.debug("type_text via SendInput (no hwnd), %d chars", len(text))
    inputs: list[INPUT] = []
    for ch in text:
        code = ord(ch)
        inputs.append(
            _make_key_input(vk=0, scan=code, flags=KEYEVENTF_UNICODE)
        )
        inputs.append(
            _make_key_input(
                vk=0, scan=code, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
            )
        )

    _send(inputs)
    return {"ok": True, "chars": len(text), "method": "sendinput"}


def send_key_combo(combo: str, hwnd: int = None, method: str = "auto") -> dict:  # noqa: C901
    """Send a key combination.

    *combo* is a ``+``-delimited string, e.g. ``"ctrl+shift+e"``,
    ``"alt+f4"``, ``"enter"``.  Modifiers are held while the final key is
    pressed, then all keys are released in reverse order.

    *method* can be ``"auto"`` (default), ``"sendinput"``, or ``"postmessage"``.
    When ``"sendinput"`` is specified, always uses SendInput regardless of hwnd.

    Returns ``{"ok": True, "combo": combo, "method": "postmessage"|"sendinput"}``.
    """
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        return {"ok": False, "error": "Empty combo string"}

    try:
        vks = [_resolve_key(p) for p in parts]
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    if method == "sendinput":
        hwnd = None  # force SendInput path

    if hwnd is not None:
        # ── Primary path: PostMessage ──
        log.debug("send_key_combo via PostMessage to hwnd=%#x: %s", hwnd, combo)

        # Separate modifiers from the main key.
        modifiers = [vk for vk in vks if vk in _MODIFIER_VKS]
        main_keys = [vk for vk in vks if vk not in _MODIFIER_VKS]
        modifier_set = set(modifiers)

        # --- Strategy 1: Ctrl+letter → dedicated edit control message ---
        if modifier_set == {VK_CONTROL} and len(main_keys) == 1:
            main_vk = main_keys[0]
            edit_op = _CTRL_EDIT_OPS.get(main_vk)
            if edit_op is not None:
                edit_op(hwnd)
                time.sleep(0.02)
                return {"ok": True, "combo": combo, "method": "postmessage_edit"}

        # --- Strategy 2: Ctrl+letter → WM_CHAR with control character ---
        # When you press Ctrl+A physically, the edit control receives
        # WM_CHAR with wParam=0x01.  Ctrl+letter = letter_code - 0x40.
        if modifier_set == {VK_CONTROL} and len(main_keys) == 1:
            main_vk = main_keys[0]
            # VK for A-Z is 0x41-0x5A
            if 0x41 <= main_vk <= 0x5A:
                ctrl_char = main_vk - 0x40  # Ctrl+A=0x01, Ctrl+Z=0x1A
                user32.PostMessageW(hwnd, WM_CHAR, ctrl_char, 0)
                time.sleep(0.02)
                return {"ok": True, "combo": combo, "method": "postmessage_ctrlchar"}

        # --- Strategy 3: Single key (no modifiers) → WM_KEYDOWN/UP ---
        if not modifiers and len(main_keys) == 1:
            _post_key(hwnd, main_keys[0], main_keys[0] in _EXTENDED_KEYS)
            return {"ok": True, "combo": combo, "method": "postmessage"}

        # --- Strategy 4: General combo → WM_KEYDOWN/UP sequence ---
        # This may not work for all apps since GetKeyState() won't
        # reflect the modifier state, but it's the best we can do.
        has_alt = VK_MENU in modifier_set

        for vk in vks:
            extended = vk in _EXTENDED_KEYS
            scan = user32.MapVirtualKeyW(vk, 0)
            is_alt_combo = has_alt and vk != VK_MENU

            if is_alt_combo:
                lp = _make_syslparam(scan, extended)
                user32.PostMessageW(hwnd, WM_SYSKEYDOWN, vk, lp)
            else:
                lp = _make_lparam(scan, extended)
                user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lp)
            time.sleep(0.02)

        for vk in reversed(vks):
            extended = vk in _EXTENDED_KEYS
            scan = user32.MapVirtualKeyW(vk, 0)
            is_alt_combo = has_alt and vk != VK_MENU

            if is_alt_combo:
                lp = _make_syslparam(scan, extended, is_up=True)
                user32.PostMessageW(hwnd, WM_SYSKEYUP, vk, lp)
            else:
                lp = _make_lparam(scan, extended, is_up=True)
                user32.PostMessageW(hwnd, WM_KEYUP, vk, lp)
            time.sleep(0.02)

        return {"ok": True, "combo": combo, "method": "postmessage"}

    # ── Fallback: SendInput ──
    log.debug("send_key_combo via SendInput (no hwnd): %s", combo)
    inputs: list[INPUT] = []

    # Press all keys in order (modifiers first, then the main key).
    for vk in vks:
        flags = 0
        if vk in _EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        inputs.append(_make_key_input(vk=vk, flags=flags))

    # Release in reverse order.
    for vk in reversed(vks):
        flags = KEYEVENTF_KEYUP
        if vk in _EXTENDED_KEYS:
            flags |= KEYEVENTF_EXTENDEDKEY
        inputs.append(_make_key_input(vk=vk, flags=flags))

    _send(inputs)

    # Tiny sleep so the target app processes the events before we return.
    time.sleep(0.02)

    return {"ok": True, "combo": combo, "method": "sendinput"}


# ── Convenience wrappers (used by cdcs_agent.py) ────────────────────

def type_text_to_window(hwnd: int, text: str) -> dict:
    """Type *text* into *hwnd* via PostMessage WM_CHAR."""
    return type_text(text, hwnd=hwnd)


def send_key_combo_to_window(hwnd: int, combo: str) -> dict:
    """Send a key combo to *hwnd* via PostMessage WM_KEYDOWN/UP."""
    return send_key_combo(combo, hwnd=hwnd)
