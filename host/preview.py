"""Live preview window for a CDCS hidden-desktop session.

Runs as a standalone process::

    python host/preview.py <session> [--refresh-ms 500]

Periodically sends ``screenshot`` commands to the agent over the named
pipe and renders the captured PNG in a resizable tkinter window.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import tempfile
import tkinter as tk
from tkinter import ttk

# Ensure project root is importable when run as a script.
_HERE = pathlib.Path(__file__).resolve().parent
_PROJECT = _HERE.parent
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

from PIL import Image, ImageTk  # noqa: E402

from host.pipe_client import PipeClient  # noqa: E402


def _pipe_name(session: str) -> str:
    return f"\\\\.\\pipe\\cdcs-{session}"


class PreviewWindow:
    """Tkinter live-preview of a hidden desktop session."""

    def __init__(self, session: str, refresh_ms: int = 500) -> None:
        self.session = session
        self.refresh_ms = max(100, refresh_ms)
        self._tmp_path = os.path.join(
            tempfile.gettempdir(), f"cdcs_preview_{session}.png",
        )
        self._running = True
        self._photo: ImageTk.PhotoImage | None = None
        self._last_image: Image.Image | None = None

        # ── Build UI ────────────────────────────────────────────────
        self.root = tk.Tk()
        self.root.title(f"CDCS Preview \u2014 {session}")
        self.root.geometry("820x620")
        self.root.minsize(320, 240)
        self.root.configure(bg="#1e1e1e")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Status bar (bottom)
        self._status_var = tk.StringVar(value="Connecting\u2026")
        status = ttk.Label(
            self.root, textvariable=self._status_var,
            relief="sunken", anchor="w", padding=(6, 3),
        )
        status.pack(side="bottom", fill="x")

        # Canvas
        self._canvas = tk.Canvas(
            self.root, bg="#1e1e1e", highlightthickness=0,
        )
        self._canvas.pack(fill="both", expand=True)
        self._img_id = self._canvas.create_image(0, 0, anchor="nw")
        self._canvas.bind("<Configure>", self._on_resize)

    # ── lifecycle ────────────────────────────────────────────────────

    def run(self) -> None:
        """Enter the tkinter mainloop (blocking)."""
        self._schedule_refresh()
        self.root.mainloop()
        # Cleanup temp file on exit.
        try:
            os.unlink(self._tmp_path)
        except OSError:
            pass

    def _on_close(self) -> None:
        self._running = False
        self.root.destroy()

    # ── refresh loop ─────────────────────────────────────────────────

    def _schedule_refresh(self) -> None:
        if self._running:
            self.root.after(0, self._refresh)

    def _refresh(self) -> None:
        if not self._running:
            return
        try:
            self._capture_and_display()
        except Exception as exc:
            self._status_var.set(f"Error: {exc}")
        finally:
            if self._running:
                self.root.after(self.refresh_ms, self._refresh)

    # ── capture via agent pipe ───────────────────────────────────────

    def _capture_and_display(self) -> None:
        client = PipeClient(_pipe_name(self.session))
        try:
            client.connect(timeout=3.0)
            resp = client.send({"cmd": "screenshot", "path": self._tmp_path})
        except TimeoutError:
            self._status_var.set("Agent not responding\u2026")
            return
        except Exception as exc:
            self._status_var.set(f"Pipe: {exc}")
            return
        finally:
            client.close()

        if not resp.get("ok"):
            self._status_var.set(
                f"Screenshot: {resp.get('error', 'unknown error')}"
            )
            return

        try:
            img = Image.open(self._tmp_path)
            self._last_image = img.copy()
            self._display_image(img)
            w, h = img.size
            self._status_var.set(
                f"{self.session}  \u2014  {w}\u00d7{h}  \u2014  "
                f"every {self.refresh_ms} ms"
            )
        except Exception as exc:
            self._status_var.set(f"Image: {exc}")

    # ── render helpers ───────────────────────────────────────────────

    def _on_resize(self, _event: tk.Event) -> None:
        if self._last_image is not None:
            self._display_image(self._last_image)

    def _display_image(self, img: Image.Image) -> None:
        """Scale *img* to fit the canvas (aspect-preserving) and display."""
        cw = self._canvas.winfo_width()
        ch = self._canvas.winfo_height()
        if cw < 2 or ch < 2:
            return

        iw, ih = img.size
        scale = min(cw / iw, ch / ih)
        new_w = max(1, int(iw * scale))
        new_h = max(1, int(ih * scale))

        resized = img.resize((new_w, new_h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)

        x = (cw - new_w) // 2
        y = (ch - new_h) // 2
        self._canvas.coords(self._img_id, x, y)
        self._canvas.itemconfig(self._img_id, image=self._photo)


# ── entry point ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="CDCS live preview")
    parser.add_argument("session", help="Session name")
    parser.add_argument(
        "--refresh-ms", type=int, default=500,
        help="Refresh interval in ms (default: 500)",
    )
    args = parser.parse_args()
    PreviewWindow(args.session, args.refresh_ms).run()


if __name__ == "__main__":
    main()
