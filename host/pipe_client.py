"""Named pipe client for CDCS agent communication.

The agent owns the named pipe server (``\\\\.\\pipe\\cdcs-{session}``).
This module provides a lightweight client that connects, sends one JSON
command, reads one JSON response, then disconnects.  The agent loops
back to ``ConnectNamedPipe`` for the next caller.

Protocol
--------
Newline-delimited JSON over a **byte-mode** named pipe:

* Client writes: ``json.dumps(command) + "\\n"``
* Client reads until ``"\\n"``: ``json.loads(line)``

Each CLI invocation opens a fresh connection, so the agent never has to
multiplex clients.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pywintypes
import win32file
import win32pipe


class PipeClient:
    """One-shot client that connects to a CDCS agent pipe.

    Typical usage::

        client = PipeClient(r"\\\\.\\pipe\\cdcs-session-0")
        client.connect(timeout=10.0)
        response = client.send({"cmd": "ping"})
        client.close()

    Or as a context manager::

        with PipeClient(pipe_name) as c:
            c.connect()
            resp = c.send({"cmd": "windows"})
    """

    BUFFER_SIZE = 65_536

    def __init__(self, pipe_name: str) -> None:
        """Store the pipe name.  Connection is deferred to :meth:`connect`.

        Args:
            pipe_name: Full pipe path, e.g. ``r'\\\\.\\pipe\\cdcs-session-0'``.
        """
        self.pipe_name: str = pipe_name
        self._handle: Any | None = None

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self, timeout: float = 10.0) -> bool:
        """Connect to the named pipe, retrying until *timeout* seconds.

        The pipe may not exist yet (agent still starting) or may be busy
        (another client just disconnected and the agent hasn't called
        ``ConnectNamedPipe`` yet), so we retry in a tight loop.

        Returns:
            ``True`` on success.

        Raises:
            TimeoutError: If the pipe is still unavailable after *timeout*.
            pywintypes.error: For unexpected Win32 errors.
        """
        deadline = time.monotonic() + timeout
        last_error: pywintypes.error | None = None

        while time.monotonic() < deadline:
            try:
                handle = win32file.CreateFile(
                    self.pipe_name,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,      # no sharing
                    None,   # default security
                    win32file.OPEN_EXISTING,
                    0,      # default attributes
                    None,   # no template
                )
                # The agent creates its pipe in byte mode; match that.
                win32pipe.SetNamedPipeHandleState(
                    handle,
                    win32pipe.PIPE_READMODE_BYTE,
                    None,
                    None,
                )
                self._handle = handle
                return True

            except pywintypes.error as exc:
                last_error = exc
                # ERROR_FILE_NOT_FOUND (2)  -- pipe doesn't exist yet
                # ERROR_PIPE_BUSY     (231) -- another client connected
                if exc.winerror == 2:
                    time.sleep(0.2)
                    continue
                if exc.winerror == 231:
                    # Wait for the pipe to become available (up to 1 s).
                    try:
                        win32pipe.WaitNamedPipe(self.pipe_name, 1000)
                    except pywintypes.error:
                        time.sleep(0.1)
                    continue
                raise

        raise TimeoutError(
            f"Could not connect to pipe {self.pipe_name!r} within "
            f"{timeout}s (last win32 error: {last_error})"
        )

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def send(self, command: dict) -> dict:
        """Send *command* as JSON and return the parsed JSON response.

        The client writes a single JSON line, then reads until a newline
        is found.  The 30-second hard timeout prevents deadlocks.

        Args:
            command: Dictionary to serialise and send.

        Returns:
            Parsed JSON response dictionary from the agent.

        Raises:
            RuntimeError: If not connected, the pipe breaks, or the
                response is not valid JSON.
        """
        if self._handle is None:
            raise RuntimeError("Not connected -- call connect() first")

        payload = json.dumps(command, separators=(",", ":")) + "\n"
        win32file.WriteFile(self._handle, payload.encode("utf-8"))

        # Read until we see a newline.
        buf = b""
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                _hr, chunk = win32file.ReadFile(self._handle, self.BUFFER_SIZE)
            except pywintypes.error as exc:
                if exc.winerror == 109:  # ERROR_BROKEN_PIPE
                    break
                raise
            buf += chunk
            if b"\n" in buf:
                break
        else:
            raise RuntimeError("Timed out waiting for agent response (30 s)")

        line = buf.split(b"\n", 1)[0]
        if not line:
            raise RuntimeError("Agent closed pipe without sending a response")

        try:
            return json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Bad JSON from agent: {exc}") from exc

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the pipe handle if open."""
        if self._handle is not None:
            try:
                win32file.CloseHandle(self._handle)
            except pywintypes.error:
                pass
            self._handle = None

    @property
    def connected(self) -> bool:
        """``True`` if the pipe handle is open."""
        return self._handle is not None

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "PipeClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
