"""End-to-end integration test for CDCS.

Tests the complete workflow:
1. Create a session
2. Launch notepad
3. Type text
4. Take screenshot and verify
5. Click (to position cursor)
6. Send key combo (Ctrl+A to select all)
7. Take screenshot and verify
8. Destroy session

Run with:
    python -m pytest tests/test_integration.py -v
Or directly:
    python tests/test_integration.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
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

# Per-test timeout in seconds.
TEST_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_ctl(*args: str, timeout: float = TEST_TIMEOUT) -> dict[str, Any]:
    """Invoke sandbox_ctl.py with *args* and return the parsed JSON response.

    Raises ``AssertionError`` on non-zero exit or unparseable output.
    """
    cmd = [PYTHON_EXE, str(SANDBOX_CTL)] + list(args)
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    # Prefer stdout for the JSON payload; fall back to stderr for diagnostics.
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
    """Return True if the PNG at *path* contains more than a single solid colour."""
    img = Image.open(path)
    pixels = list(img.getdata())
    if not pixels:
        return False
    # Sample up to 5000 pixels -- if we find more than 3 distinct colours,
    # the image has real content.
    sample = pixels[:5000]
    return len(set(sample)) > 3


def _screenshots_differ(path_a: str | Path, path_b: str | Path,
                         threshold: int = 50) -> bool:
    """Return True if two PNG screenshots differ by at least *threshold* pixels.

    Compares the first 10 000 pixels by exact RGB match.
    """
    img_a = Image.open(path_a)
    img_b = Image.open(path_b)
    data_a = list(img_a.getdata())
    data_b = list(img_b.getdata())
    if len(data_a) != len(data_b):
        return True  # different dimensions => definitely different
    limit = min(10_000, len(data_a))
    diffs = sum(1 for i in range(limit) if data_a[i] != data_b[i])
    return diffs >= threshold


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestCDCSIntegration(unittest.TestCase):
    """End-to-end integration tests for the CDCS sandbox_ctl CLI."""

    # Class-level tracking of sessions that need cleanup.
    _sessions_to_destroy: list[str] = []

    @classmethod
    def setUpClass(cls) -> None:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        # Verify the CLI exists before running any tests.
        if not SANDBOX_CTL.exists():
            raise unittest.SkipTest(
                f"sandbox_ctl.py not found at {SANDBOX_CTL}. "
                "Build the CLI first."
            )

    @classmethod
    def tearDownClass(cls) -> None:
        # Best-effort cleanup of any sessions leaked by failing tests.
        for name in cls._sessions_to_destroy:
            try:
                _run_ctl("destroy", name, timeout=15)
            except Exception:
                pass

    def _create_session(self, name: str) -> dict[str, Any]:
        """Create a session and register it for automatic teardown."""
        resp = _run_ctl("create", name)
        self.assertTrue(resp.get("ok"), f"create failed: {resp}")
        self.__class__._sessions_to_destroy.append(name)
        return resp

    def _destroy_session(self, name: str) -> None:
        """Destroy a session and remove it from the teardown list."""
        try:
            resp = _run_ctl("destroy", name, timeout=15)
            # Even if destroy reports an error we still deregister.
            self.assertTrue(resp.get("ok"), f"destroy failed: {resp}")
        finally:
            try:
                self.__class__._sessions_to_destroy.remove(name)
            except ValueError:
                pass

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_create_destroy(self) -> None:
        """Create a session and immediately destroy it."""
        name = "integ-create-destroy"
        self._create_session(name)
        self._destroy_session(name)

    def test_launch_and_screenshot(self) -> None:
        """Launch Notepad, take a screenshot, verify it has content."""
        name = "integ-launch-shot"
        self._create_session(name)
        try:
            # Launch Notepad.
            launch_resp = _run_ctl("launch", name, "notepad.exe")
            self.assertTrue(launch_resp.get("ok"), f"launch failed: {launch_resp}")

            # Give the window time to render.
            time.sleep(2)

            # Screenshot.
            shot_path = str(OUTPUT_DIR / "integ_launch_shot.png")
            shot_resp = _run_ctl("screenshot", name, "--output", shot_path)
            self.assertTrue(shot_resp.get("ok"), f"screenshot failed: {shot_resp}")
            self.assertTrue(
                Path(shot_path).exists(),
                f"Screenshot file not created at {shot_path}",
            )
            self.assertTrue(
                _screenshot_has_content(shot_path),
                "Screenshot is blank (single colour).",
            )
        finally:
            self._destroy_session(name)

    def test_type_and_verify(self) -> None:
        """Launch Notepad, type text, screenshot, and verify the image changed."""
        name = "integ-type"
        self._create_session(name)
        try:
            _run_ctl("launch", name, "notepad.exe")
            time.sleep(2)

            # Screenshot before typing.
            before = str(OUTPUT_DIR / "integ_type_before.png")
            _run_ctl("screenshot", name, "--output", before)

            # Type some text.
            type_resp = _run_ctl("type", name, "Hello CDCS Integration Test!")
            self.assertTrue(type_resp.get("ok"), f"type failed: {type_resp}")
            time.sleep(1)

            # Screenshot after typing.
            after = str(OUTPUT_DIR / "integ_type_after.png")
            shot_resp = _run_ctl("screenshot", name, "--output", after)
            self.assertTrue(shot_resp.get("ok"), f"screenshot failed: {shot_resp}")

            # The two screenshots must differ (text was typed).
            self.assertTrue(
                _screenshots_differ(before, after),
                "Screenshots before and after typing are identical -- typing may have failed.",
            )
        finally:
            self._destroy_session(name)

    def test_key_combo(self) -> None:
        """Type text then Ctrl+A to select all; verify the screenshot changes."""
        name = "integ-keycombo"
        self._create_session(name)
        try:
            _run_ctl("launch", name, "notepad.exe")
            time.sleep(2)

            # Type text.
            _run_ctl("type", name, "Select All Test Text")
            time.sleep(1)

            # Screenshot before Ctrl+A.
            before = str(OUTPUT_DIR / "integ_keycombo_before.png")
            _run_ctl("screenshot", name, "--output", before)

            # Ctrl+A to select all text -- this visually highlights the text.
            key_resp = _run_ctl("key", name, "ctrl+a")
            self.assertTrue(key_resp.get("ok"), f"key failed: {key_resp}")
            time.sleep(0.5)

            # Screenshot after Ctrl+A.
            after = str(OUTPUT_DIR / "integ_keycombo_after.png")
            _run_ctl("screenshot", name, "--output", after)

            # Selection highlighting should make the screenshots differ.
            self.assertTrue(
                _screenshots_differ(before, after),
                "Screenshots before and after Ctrl+A are identical -- key combo may have failed.",
            )
        finally:
            self._destroy_session(name)

    def test_click(self) -> None:
        """Click at a coordinate within Notepad and verify the command succeeds."""
        name = "integ-click"
        self._create_session(name)
        try:
            launch_resp = _run_ctl("launch", name, "notepad.exe")
            self.assertTrue(launch_resp.get("ok"), f"launch failed: {launch_resp}")
            time.sleep(2)

            # Click near the top-left of the editing area.
            click_resp = _run_ctl("click", name, "50", "50")
            self.assertTrue(click_resp.get("ok"), f"click failed: {click_resp}")

            # After clicking, type a character to confirm the click placed focus.
            _run_ctl("type", name, "X")
            time.sleep(0.5)

            shot_path = str(OUTPUT_DIR / "integ_click.png")
            shot_resp = _run_ctl("screenshot", name, "--output", shot_path)
            self.assertTrue(shot_resp.get("ok"), f"screenshot failed: {shot_resp}")
            self.assertTrue(
                _screenshot_has_content(shot_path),
                "Screenshot after click+type is blank.",
            )
        finally:
            self._destroy_session(name)

    def test_full_workflow(self) -> None:
        """Complete end-to-end workflow exercising every major command."""
        name = "integ-full"
        self._create_session(name)
        try:
            # 1. Launch Notepad.
            launch_resp = _run_ctl("launch", name, "notepad.exe")
            self.assertTrue(launch_resp.get("ok"), f"launch failed: {launch_resp}")
            time.sleep(2)

            # 2. Screenshot after launch.
            shot1 = str(OUTPUT_DIR / "integ_full_1_launch.png")
            _run_ctl("screenshot", name, "--output", shot1)
            self.assertTrue(_screenshot_has_content(shot1))

            # 3. Click to place cursor.
            _run_ctl("click", name, "100", "100")
            time.sleep(0.3)

            # 4. Type text.
            _run_ctl("type", name, "Full workflow test: CDCS is working!")
            time.sleep(1)

            # 5. Screenshot after typing.
            shot2 = str(OUTPUT_DIR / "integ_full_2_typed.png")
            _run_ctl("screenshot", name, "--output", shot2)
            self.assertTrue(_screenshots_differ(shot1, shot2))

            # 6. Select all with Ctrl+A.
            _run_ctl("key", name, "ctrl+a")
            time.sleep(0.5)

            # 7. Screenshot after selection.
            shot3 = str(OUTPUT_DIR / "integ_full_3_selected.png")
            _run_ctl("screenshot", name, "--output", shot3)
            self.assertTrue(_screenshots_differ(shot2, shot3))

            # 8. List windows to verify we can enumerate the session.
            win_resp = _run_ctl("windows", name)
            self.assertTrue(win_resp.get("ok"), f"windows failed: {win_resp}")

            # 9. List all sessions.
            list_resp = _run_ctl("list")
            self.assertTrue(list_resp.get("ok"), f"list failed: {list_resp}")

        finally:
            self._destroy_session(name)


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Support running as a standalone script with readable output.
    unittest.main(verbosity=2)
