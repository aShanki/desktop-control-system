"""Parallel session isolation test for CDCS.

Tests that multiple sessions can run simultaneously without crosstalk:
1. Create N sessions (default: 4)
2. Launch notepad in each
3. Type unique text in each
4. Screenshot all sessions
5. Verify each has only its own text (screenshots are non-blank and pairwise different)
6. Destroy all sessions

Also includes a stress test for rapid creation/destruction cycles.

Run with:
    python -m pytest tests/test_parallel.py -v
Or directly:
    python tests/test_parallel.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any

from PIL import Image

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_CTL = PROJECT_ROOT / "sandbox_ctl.py"
PYTHON_EXE = os.environ.get("CDCS_PYTHON_EXE", sys.executable)
OUTPUT_DIR = PROJECT_ROOT / "test_output"

# Timeout for individual sandbox_ctl calls.
CMD_TIMEOUT = 60

# Number of parallel sessions for the main isolation test.
NUM_SESSIONS = 4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_ctl(*args: str, timeout: float = CMD_TIMEOUT) -> dict[str, Any]:
    """Invoke sandbox_ctl.py with *args* and return the parsed JSON response."""
    cmd = [PYTHON_EXE, str(SANDBOX_CTL)] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stdout = result.stdout.strip()
    if result.returncode != 0 and not stdout:
        raise AssertionError(
            f"sandbox_ctl exited {result.returncode}.\n"
            f"  args: {args}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"Could not parse JSON from sandbox_ctl.\n"
            f"  args: {args}\n"
            f"  stdout: {stdout!r}\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  json error: {exc}"
        ) from exc


def _screenshot_has_content(path: str | Path) -> bool:
    """Return True if the PNG at *path* is not a solid single colour."""
    img = Image.open(path)
    pixels = list(img.getdata())
    if not pixels:
        return False
    sample = pixels[:5000]
    return len(set(sample)) > 3


def _screenshots_differ(path_a: str | Path, path_b: str | Path,
                         threshold: int = 50) -> bool:
    """Return True if two screenshots differ by at least *threshold* pixels."""
    img_a = Image.open(path_a)
    img_b = Image.open(path_b)
    data_a = list(img_a.getdata())
    data_b = list(img_b.getdata())
    if len(data_a) != len(data_b):
        return True
    limit = min(10_000, len(data_a))
    diffs = sum(1 for i in range(limit) if data_a[i] != data_b[i])
    return diffs >= threshold


def _safe_destroy(name: str) -> None:
    """Best-effort destroy; swallow all errors."""
    try:
        _run_ctl("destroy", name, timeout=15)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Per-session worker for the parallel test
# ---------------------------------------------------------------------------

class _SessionWorker:
    """Encapsulates the work done on a single session in a thread."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.name = f"par-{index}"
        self.text = f"SESSION_{index}_UNIQUE_TEXT"
        self.shot_path = str(OUTPUT_DIR / f"parallel_{index}.png")
        self.error: str | None = None
        self.created = False

    def run(self) -> None:
        """Execute the full lifecycle for this session.

        Any failure is stored in ``self.error`` rather than raised, so
        the calling thread can report it cleanly.
        """
        try:
            # Create session.
            resp = _run_ctl("create", self.name)
            if not resp.get("ok"):
                self.error = f"[{self.name}] create failed: {resp}"
                return
            self.created = True

            # Launch Notepad.
            resp = _run_ctl("launch", self.name, "notepad.exe")
            if not resp.get("ok"):
                self.error = f"[{self.name}] launch failed: {resp}"
                return
            time.sleep(2)

            # Type unique text.
            resp = _run_ctl("type", self.name, self.text)
            if not resp.get("ok"):
                self.error = f"[{self.name}] type failed: {resp}"
                return
            time.sleep(1)

            # Screenshot.
            resp = _run_ctl("screenshot", self.name, "--output", self.shot_path)
            if not resp.get("ok"):
                self.error = f"[{self.name}] screenshot failed: {resp}"
                return

        except Exception as exc:
            self.error = f"[{self.name}] exception: {exc}"

    def destroy(self) -> None:
        """Tear down the session if it was created."""
        if self.created:
            _safe_destroy(self.name)


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestParallelIsolation(unittest.TestCase):
    """Tests that multiple CDCS sessions are fully isolated from each other."""

    @classmethod
    def setUpClass(cls) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        if not SANDBOX_CTL.exists():
            raise unittest.SkipTest(
                f"sandbox_ctl.py not found at {SANDBOX_CTL}. "
                "Build the CLI first."
            )

    # ------------------------------------------------------------------
    # Main parallel isolation test
    # ------------------------------------------------------------------

    def test_parallel_sessions_are_isolated(self) -> None:
        """Create N sessions, type unique text in each concurrently, then
        verify that each screenshot is non-blank and pairwise different."""

        workers = [_SessionWorker(i) for i in range(NUM_SESSIONS)]
        threads: list[threading.Thread] = []

        try:
            # Launch all workers in parallel threads.
            for w in workers:
                t = threading.Thread(target=w.run, name=f"worker-{w.index}")
                threads.append(t)
                t.start()

            # Wait for all threads to complete (generous timeout).
            for t in threads:
                t.join(timeout=CMD_TIMEOUT * 3)

            # Check that no thread is still running.
            for t in threads:
                self.assertFalse(
                    t.is_alive(),
                    f"Thread {t.name} did not finish within the timeout.",
                )

            # Collect errors from workers.
            errors = [w.error for w in workers if w.error is not None]
            if errors:
                self.fail(
                    f"{len(errors)} session(s) failed:\n"
                    + "\n".join(errors)
                )

            # Verify every screenshot exists and has content.
            for w in workers:
                self.assertTrue(
                    Path(w.shot_path).exists(),
                    f"Screenshot not found for {w.name}: {w.shot_path}",
                )
                self.assertTrue(
                    _screenshot_has_content(w.shot_path),
                    f"Screenshot for {w.name} is blank (single colour).",
                )

            # Verify pairwise that screenshots are different.
            for i in range(NUM_SESSIONS):
                for j in range(i + 1, NUM_SESSIONS):
                    self.assertTrue(
                        _screenshots_differ(
                            workers[i].shot_path,
                            workers[j].shot_path,
                        ),
                        f"Screenshots for {workers[i].name} and {workers[j].name} "
                        f"are identical -- sessions may not be isolated.",
                    )

        finally:
            # Always clean up all sessions.
            cleanup_threads: list[threading.Thread] = []
            for w in workers:
                t = threading.Thread(target=w.destroy, name=f"cleanup-{w.index}")
                cleanup_threads.append(t)
                t.start()
            for t in cleanup_threads:
                t.join(timeout=15)

    # ------------------------------------------------------------------
    # Stress test: rapid create/destroy cycles
    # ------------------------------------------------------------------

    def test_rapid_create_destroy(self) -> None:
        """Rapidly create and destroy sessions to verify no resource leaks
        or name collisions."""

        num_cycles = 8
        errors: list[str] = []

        def _cycle(index: int) -> None:
            name = f"stress-{index}"
            try:
                resp = _run_ctl("create", name, timeout=30)
                if not resp.get("ok"):
                    errors.append(f"[{name}] create failed: {resp}")
                    return
                # Immediately destroy -- no launch, no screenshot.
                resp = _run_ctl("destroy", name, timeout=15)
                if not resp.get("ok"):
                    errors.append(f"[{name}] destroy failed: {resp}")
            except Exception as exc:
                errors.append(f"[{name}] exception: {exc}")
                # Best-effort cleanup.
                _safe_destroy(name)

        threads: list[threading.Thread] = []
        for i in range(num_cycles):
            t = threading.Thread(target=_cycle, args=(i,), name=f"stress-{i}")
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=CMD_TIMEOUT)

        if errors:
            self.fail(
                f"{len(errors)} cycle(s) failed:\n"
                + "\n".join(errors)
            )

    # ------------------------------------------------------------------
    # Sequential reuse: same session name can be reused after destroy
    # ------------------------------------------------------------------

    def test_session_name_reuse(self) -> None:
        """Destroy a session then re-create it with the same name."""
        name = "par-reuse"
        try:
            # First lifecycle.
            resp = _run_ctl("create", name)
            self.assertTrue(resp.get("ok"), f"first create failed: {resp}")
            resp = _run_ctl("destroy", name, timeout=15)
            self.assertTrue(resp.get("ok"), f"first destroy failed: {resp}")

            # Second lifecycle with the same name.
            resp = _run_ctl("create", name)
            self.assertTrue(resp.get("ok"), f"second create failed: {resp}")
        finally:
            _safe_destroy(name)

    # ------------------------------------------------------------------
    # Concurrent screenshots do not interfere
    # ------------------------------------------------------------------

    def test_concurrent_screenshots(self) -> None:
        """Two sessions screenshot at the same time without file or state conflicts."""
        names = ["par-shot-0", "par-shot-1"]
        try:
            for n in names:
                resp = _run_ctl("create", n)
                self.assertTrue(resp.get("ok"), f"create {n} failed: {resp}")
                resp = _run_ctl("launch", n, "notepad.exe")
                self.assertTrue(resp.get("ok"), f"launch {n} failed: {resp}")

            time.sleep(2)

            # Type different text in each.
            _run_ctl("type", names[0], "CONCURRENT_A")
            _run_ctl("type", names[1], "CONCURRENT_B")
            time.sleep(1)

            # Screenshot both in parallel threads.
            paths = [
                str(OUTPUT_DIR / "par_concurrent_0.png"),
                str(OUTPUT_DIR / "par_concurrent_1.png"),
            ]
            errors: list[str] = []

            def _shot(idx: int) -> None:
                try:
                    resp = _run_ctl(
                        "screenshot", names[idx], "--output", paths[idx]
                    )
                    if not resp.get("ok"):
                        errors.append(f"screenshot {names[idx]} failed: {resp}")
                except Exception as exc:
                    errors.append(f"screenshot {names[idx]} exception: {exc}")

            t0 = threading.Thread(target=_shot, args=(0,))
            t1 = threading.Thread(target=_shot, args=(1,))
            t0.start()
            t1.start()
            t0.join(timeout=CMD_TIMEOUT)
            t1.join(timeout=CMD_TIMEOUT)

            if errors:
                self.fail("\n".join(errors))

            # Both screenshots should exist and differ.
            for p in paths:
                self.assertTrue(Path(p).exists(), f"Missing screenshot: {p}")
                self.assertTrue(
                    _screenshot_has_content(p),
                    f"Screenshot is blank: {p}",
                )
            self.assertTrue(
                _screenshots_differ(paths[0], paths[1]),
                "Concurrent screenshots are identical -- possible crosstalk.",
            )

        finally:
            for n in names:
                _safe_destroy(n)


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main(verbosity=2)
