"""Screenshot capture for CDCS agent.

Uses PrintWindow with PW_RENDERFULLCONTENT to capture windows that are on
hidden (non-interactive) desktops where BitBlt from the screen DC would
return a black rectangle.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
from typing import Tuple

import win32con
import win32gui
import win32ui
from PIL import Image

log = logging.getLogger(__name__)

# PrintWindow flag that tells the window to render its full content
# (including Direct Composition visuals) into the provided DC.
PW_RENDERFULLCONTENT = 0x00000002


# ── GDI helpers ──────────────────────────────────────────────────────

def _get_client_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """Return (left, top, right, bottom) of the window rect."""
    return win32gui.GetWindowRect(hwnd)


def _bitmap_is_blank(bmp_info, bmp_bits: bytes, width: int, height: int) -> bool:
    """Return True if every pixel in *bmp_bits* has the same colour.

    We only sample a handful of pixels to keep this fast.
    """
    if len(bmp_bits) < 4:
        return True
    bytes_per_pixel = 4  # BGRX
    stride = ((width * bytes_per_pixel) + 3) & ~3
    first = bmp_bits[:bytes_per_pixel]
    # Sample corners and centre.
    samples = [
        (0, 0),
        (width - 1, 0),
        (0, height - 1),
        (width - 1, height - 1),
        (width // 2, height // 2),
        (width // 4, height // 4),
        (3 * width // 4, 3 * height // 4),
    ]
    for sx, sy in samples:
        if sx < 0 or sy < 0 or sx >= width or sy >= height:
            continue
        offset = sy * stride + sx * bytes_per_pixel
        if offset + bytes_per_pixel > len(bmp_bits):
            continue
        if bmp_bits[offset:offset + bytes_per_pixel] != first:
            return False
    return True


# ── Public API ───────────────────────────────────────────────────────

def capture_window(hwnd: int, path: str) -> dict:
    """Capture *hwnd* using PrintWindow and save to *path* as PNG.

    Returns a result dict with keys: ok, path, width, height, method.
    On failure returns ok=False with an error key.
    """
    # Validate the window handle.
    if not win32gui.IsWindow(hwnd):
        return {"ok": False, "error": f"Invalid window handle: {hwnd}"}

    left, top, right, bottom = _get_client_rect(hwnd)
    width = right - left
    height = bottom - top
    if width <= 0 or height <= 0:
        return {"ok": False, "error": f"Window has zero size: {width}x{height}"}

    # GDI objects we must clean up.
    hwnd_dc = None
    mem_dc = None
    bmp = None

    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mem_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mem_dc.CreateCompatibleDC()

        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mem_dc, width, height)
        save_dc.SelectObject(bmp)

        # PrintWindow renders the window into our off-screen DC even on a
        # hidden desktop where the DWM has no on-screen composition.
        result = ctypes.windll.user32.PrintWindow(
            hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT
        )
        if result == 0:
            log.warning("PrintWindow returned 0 for hwnd %s", hwnd)

        # Extract the bitmap bits.
        bmp_info = bmp.GetInfo()
        bmp_bits = bmp.GetBitmapBits(True)

        if _bitmap_is_blank(bmp_info, bmp_bits, width, height):
            return {
                "ok": False,
                "error": "Captured image is blank (single colour)",
            }

        # Convert to Pillow Image and save.
        img = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_bits,
            "raw",
            "BGRX",
            0,
            1,
        )
        img.save(path, "PNG")
        log.info("Saved screenshot %dx%d -> %s", width, height, path)

        return {
            "ok": True,
            "path": path,
            "width": width,
            "height": height,
            "method": "printwindow",
        }

    except Exception as exc:
        log.exception("capture_window failed for hwnd %s", hwnd)
        return {"ok": False, "error": str(exc)}

    finally:
        # Careful cleanup -- order matters.
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:  # noqa: F821 -- may not be bound on early failure
                save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def capture_topmost(desktop_hwnd_list: list, output_path: str) -> dict:
    """Capture the topmost visible window from a list of HWNDs.

    Picks the first visible, non-zero-size window from *desktop_hwnd_list*
    and captures it.  Returns the same dict as :func:`capture_window` plus
    ``"hwnd"`` and ``"title"`` fields.
    """
    for hwnd in desktop_hwnd_list:
        try:
            if not win32gui.IsWindowVisible(hwnd):
                continue
            title = win32gui.GetWindowText(hwnd)
            if not title:
                continue
            rect = win32gui.GetWindowRect(hwnd)
            w = rect[2] - rect[0]
            h = rect[3] - rect[1]
            if w <= 0 or h <= 0:
                continue

            result = capture_window(hwnd, output_path)
            if result.get("ok"):
                result["hwnd"] = hwnd
                result["title"] = title
            return result
        except Exception:
            continue

    return {"ok": False, "error": "No visible, non-zero-size window found in list"}


def find_best_window(desktop_name: str | None = None) -> Tuple[int, str]:
    """Return *(hwnd, title)* of the largest visible window.

    If *desktop_name* is provided we open that desktop and use
    ``EnumDesktopWindows``; otherwise we fall back to ``EnumWindows``.
    Returns ``(0, "")`` when no suitable window is found.
    """
    candidates: list[Tuple[int, str, int]] = []  # (hwnd, title, area)

    def _callback(hwnd: int, _extra) -> bool:
        """Collect visible, sizeable windows."""
        if not win32gui.IsWindowVisible(hwnd):
            return True
        # Skip windows with empty titles (toolbars, tooltips, etc.)
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        rect = win32gui.GetWindowRect(hwnd)
        w = rect[2] - rect[0]
        h = rect[3] - rect[1]
        if w <= 0 or h <= 0:
            return True
        area = w * h
        candidates.append((hwnd, title, area))
        return True

    if desktop_name:
        try:
            hdesk = ctypes.windll.user32.OpenDesktopW(desktop_name, 0, False, 0x0040)  # DESKTOP_ENUMERATE
            if hdesk:
                # EnumDesktopWindows via pywin32 is not directly available;
                # fall back to ctypes.
                WNDENUMPROC = ctypes.WINFUNCTYPE(
                    wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
                )

                @WNDENUMPROC
                def _enum_cb(hwnd, lparam):
                    _callback(hwnd, None)
                    return True

                ctypes.windll.user32.EnumDesktopWindows(hdesk, _enum_cb, 0)
                ctypes.windll.user32.CloseDesktop(hdesk)
            else:
                log.warning(
                    "Could not open desktop %r, falling back to EnumWindows",
                    desktop_name,
                )
                win32gui.EnumWindows(_callback, None)
        except Exception:
            log.exception("EnumDesktopWindows failed, falling back to EnumWindows")
            win32gui.EnumWindows(_callback, None)
    else:
        win32gui.EnumWindows(_callback, None)

    if not candidates:
        return 0, ""

    # Largest by area.
    candidates.sort(key=lambda c: c[2], reverse=True)
    best_hwnd, best_title, _area = candidates[0]
    return best_hwnd, best_title
