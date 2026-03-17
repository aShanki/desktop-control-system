#!/usr/bin/env python
"""sandbox_ctl -- CLI bridge between Claude Code and the CDCS agent.

Every sub-command prints a single JSON object to **stdout** and exits 0
on success or non-zero on failure.  Errors are printed as JSON to
**stderr**.

Usage
-----
::

    sandbox_ctl.py create     <session>
    sandbox_ctl.py destroy    <session>
    sandbox_ctl.py launch     <session> <exe> [args ...]
    sandbox_ctl.py screenshot <session> [--output PATH] [--hwnd H]
    sandbox_ctl.py click      <session> <x> <y> [--button left|right] [--double] [--hwnd H]
    sandbox_ctl.py type       <session> <text>
    sandbox_ctl.py key        <session> <combo>
    sandbox_ctl.py scroll     <session> <x> <y> <delta> [--hwnd H]
    sandbox_ctl.py windows    <session>
    sandbox_ctl.py focus      <session> <hwnd>
    sandbox_ctl.py list

Each invocation is **stateless**: it reads session metadata from
``~/.cdcs/sessions/<name>.json``, opens a fresh named-pipe connection
to the agent, sends one command, receives one response, and exits.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from host.desktop_sandbox import DesktopSandbox

# =====================================================================
# Output helpers
# =====================================================================

def _ok(data: dict) -> None:
    """Print *data* as JSON to stdout and exit 0."""
    json.dump(data, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.exit(0)


def _fail(message: str, code: int = 1) -> None:
    """Print an error JSON object to stderr and exit with *code*."""
    json.dump({"ok": False, "error": message}, sys.stderr, indent=2)
    sys.stderr.write("\n")
    sys.exit(code)


def _result(data: dict) -> None:
    """Emit *data* to the appropriate stream and exit.

    If ``data["ok"]`` is truthy the output goes to stdout and we exit 0;
    otherwise it goes to stderr and we exit 1.
    """
    stream = sys.stdout if data.get("ok") else sys.stderr
    json.dump(data, stream, indent=2)
    stream.write("\n")
    sys.exit(0 if data.get("ok") else 1)


# =====================================================================
# Sub-command handlers
# =====================================================================

def cmd_create(args: argparse.Namespace) -> None:
    """Create a new session (hidden desktop + agent)."""
    sb = DesktopSandbox()
    _result(sb.create(args.session))


def cmd_destroy(args: argparse.Namespace) -> None:
    """Destroy an existing session."""
    sb = DesktopSandbox()
    _result(sb.destroy(args.session))


def cmd_launch(args: argparse.Namespace) -> None:
    """Launch an application on the session's hidden desktop."""
    sb = DesktopSandbox()
    extra = " ".join(args.args) if args.args else ""
    _result(sb.launch(args.session, args.exe, extra))


def cmd_screenshot(args: argparse.Namespace) -> None:
    """Capture a screenshot of a window on the hidden desktop."""
    sb = DesktopSandbox()
    command: dict[str, Any] = {"cmd": "screenshot"}
    if args.output:
        command["path"] = args.output
    if args.hwnd:
        command["hwnd"] = int(args.hwnd)
    _result(sb.send_command(args.session, command))


def cmd_click(args: argparse.Namespace) -> None:
    """Send a mouse click to a position on the hidden desktop."""
    sb = DesktopSandbox()
    command: dict[str, Any] = {
        "cmd":    "click",
        "x":      int(args.x),
        "y":      int(args.y),
    }
    if args.button:
        command["button"] = args.button
    if args.double:
        command["double"] = True
    if args.hwnd:
        command["hwnd"] = int(args.hwnd)
    if args.sendinput:
        command["method"] = "sendinput"
    _result(sb.send_command(args.session, command))


def cmd_type(args: argparse.Namespace) -> None:
    """Type text into the focused window."""
    sb = DesktopSandbox()
    command: dict[str, Any] = {"cmd": "type", "text": args.text}
    if args.hwnd:
        command["hwnd"] = int(args.hwnd)
    if args.sendinput:
        command["method"] = "sendinput"
    _result(sb.send_command(args.session, command))


def cmd_key(args: argparse.Namespace) -> None:
    """Send a key combination (e.g. ``ctrl+s``, ``alt+f4``)."""
    sb = DesktopSandbox()
    command: dict[str, Any] = {"cmd": "key", "combo": args.combo}
    if args.hwnd:
        command["hwnd"] = int(args.hwnd)
    if args.sendinput:
        command["method"] = "sendinput"
    _result(sb.send_command(args.session, command))


def cmd_scroll(args: argparse.Namespace) -> None:
    """Scroll at a position on the hidden desktop."""
    sb = DesktopSandbox()
    command: dict[str, Any] = {
        "cmd":   "scroll",
        "x":     int(args.x),
        "y":     int(args.y),
        "delta": int(args.delta),
    }
    if args.hwnd:
        command["hwnd"] = int(args.hwnd)
    _result(sb.send_command(args.session, command))


def cmd_windows(args: argparse.Namespace) -> None:
    """List visible windows on the session's hidden desktop."""
    sb = DesktopSandbox()
    _result(sb.send_command(args.session, {"cmd": "windows"}))


def cmd_focus(args: argparse.Namespace) -> None:
    """Focus a window by its handle."""
    sb = DesktopSandbox()
    _result(sb.send_command(args.session, {
        "cmd": "focus", "hwnd": int(args.hwnd),
    }))


def cmd_list(args: argparse.Namespace) -> None:
    """List all known sessions and their status."""
    sb = DesktopSandbox()
    sessions = sb.list_sessions()
    _ok({"ok": True, "sessions": sessions})


# =====================================================================
# Argument parser
# =====================================================================

def build_parser() -> argparse.ArgumentParser:
    """Build the top-level ``argparse`` parser with all sub-commands."""
    parser = argparse.ArgumentParser(
        prog="sandbox_ctl",
        description=(
            "CLI bridge for the Claude Desktop Control System (CDCS).  "
            "All output is JSON.  Exit 0 on success, non-zero on error."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- create -------------------------------------------------------
    p = sub.add_parser(
        "create",
        help="Create a session with a hidden desktop and agent",
    )
    p.add_argument("session", help="Unique session name")
    p.set_defaults(func=cmd_create)

    # -- destroy ------------------------------------------------------
    p = sub.add_parser("destroy", help="Destroy a session")
    p.add_argument("session")
    p.set_defaults(func=cmd_destroy)

    # -- launch -------------------------------------------------------
    p = sub.add_parser(
        "launch",
        help="Launch an app on the session's hidden desktop",
    )
    p.add_argument("session")
    p.add_argument("exe", help="Path to the executable")
    p.add_argument(
        "args", nargs="*", default=[],
        help="Additional arguments for the executable",
    )
    p.set_defaults(func=cmd_launch)

    # -- screenshot ---------------------------------------------------
    p = sub.add_parser("screenshot", help="Capture a screenshot")
    p.add_argument("session")
    p.add_argument(
        "--output", default=None,
        help="File path to save the PNG (agent picks a temp path if omitted)",
    )
    p.add_argument("--hwnd", default=None, help="Target window handle")
    p.set_defaults(func=cmd_screenshot)

    # -- click --------------------------------------------------------
    p = sub.add_parser("click", help="Send a mouse click")
    p.add_argument("session")
    p.add_argument("x", help="X coordinate")
    p.add_argument("y", help="Y coordinate")
    p.add_argument(
        "--button", choices=["left", "right"], default=None,
        help="Mouse button (default: left)",
    )
    p.add_argument("--double", action="store_true", help="Double-click")
    p.add_argument("--hwnd", default=None, help="Target window handle")
    p.add_argument("--sendinput", action="store_true",
                    help="Force SendInput instead of PostMessage (for Qt/Electron apps)")
    p.set_defaults(func=cmd_click)

    # -- type ---------------------------------------------------------
    p = sub.add_parser("type", help="Type text into the focused window")
    p.add_argument("session")
    p.add_argument("text", help="Text to type")
    p.add_argument("--hwnd", default=None, help="Target window handle")
    p.add_argument("--sendinput", action="store_true",
                    help="Force SendInput instead of PostMessage (for Qt/Electron apps)")
    p.set_defaults(func=cmd_type)

    # -- key ----------------------------------------------------------
    p = sub.add_parser(
        "key",
        help="Send a key combination (e.g. ctrl+s, alt+f4)",
    )
    p.add_argument("session")
    p.add_argument("combo", help="Key combination string")
    p.add_argument("--hwnd", default=None, help="Target window handle")
    p.add_argument("--sendinput", action="store_true",
                    help="Force SendInput instead of PostMessage (for Qt/Electron apps)")
    p.set_defaults(func=cmd_key)

    # -- scroll -------------------------------------------------------
    p = sub.add_parser("scroll", help="Scroll at a position")
    p.add_argument("session")
    p.add_argument("x", help="X coordinate")
    p.add_argument("y", help="Y coordinate")
    p.add_argument("delta", help="Scroll amount (positive=up, negative=down)")
    p.add_argument("--hwnd", default=None, help="Target window handle")
    p.set_defaults(func=cmd_scroll)

    # -- windows ------------------------------------------------------
    p = sub.add_parser(
        "windows",
        help="List windows on the session's hidden desktop",
    )
    p.add_argument("session")
    p.set_defaults(func=cmd_windows)

    # -- focus --------------------------------------------------------
    p = sub.add_parser("focus", help="Focus a window by handle")
    p.add_argument("session")
    p.add_argument("hwnd", help="Window handle to focus")
    p.set_defaults(func=cmd_focus)

    # -- list ---------------------------------------------------------
    p = sub.add_parser("list", help="List all active sessions")
    p.set_defaults(func=cmd_list)

    return parser


# =====================================================================
# Main
# =====================================================================

def main() -> None:
    """Parse arguments, dispatch to the sub-command handler."""
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        _fail("Interrupted", 130)
    except Exception as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
