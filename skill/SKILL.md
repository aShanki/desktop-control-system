# Claude Desktop Control System (CDCS)

## Overview

CDCS gives you isolated desktop control on Windows. You can launch apps, take screenshots, click, type, and interact with GUI applications -- all without disturbing the user's desktop. Each session runs on its own hidden Win32 desktop, fully isolated from the user's visible desktop and from other sessions.

## Setup

The CLI tool is `sandbox_ctl.py` in the project root. Run commands from the project directory:
```
python sandbox_ctl.py <command>
```

All commands return JSON to stdout. Always parse the JSON and check the `"ok"` field before proceeding.

## Agent Loop

The core workflow for controlling a desktop application. Follow this loop precisely:

1. **Create a session** -- allocates a hidden desktop and starts an agent process:
   ```
   python sandbox_ctl.py create session-0
   ```
   Response: `{"ok": true, "session": "session-0", "desktop": "cdcs-session-0", "pipe": "\\\\.\\pipe\\cdcs-session-0"}`

2. **Launch the target application**:
   ```
   python sandbox_ctl.py launch session-0 "C:\Program Files\App\app.exe"
   ```
   Response: `{"ok": true, "pid": 12345, "hwnd": 65538, "title": "App Window", "rect": [0, 0, 1280, 720]}`

3. **Take a screenshot** to see the current state:
   ```
   python sandbox_ctl.py screenshot session-0 --output screen.png
   ```
   Response: `{"ok": true, "path": "screen.png", "width": 1280, "height": 720}`

4. **Analyze the screenshot** using your vision capabilities. Identify UI elements, text, buttons, input fields, and their coordinates.

5. **Decide and execute an action** based on what you see:
   ```
   python sandbox_ctl.py click session-0 340 200
   python sandbox_ctl.py type session-0 "search query"
   python sandbox_ctl.py key session-0 "enter"
   ```

6. **Wait briefly** (500ms-1s) for the UI to settle. Use `sleep 0.5` or `sleep 1`.

7. **Screenshot again** to verify the action succeeded. Always confirm visually.

8. **Repeat steps 4-7** until the task is complete.

9. **Destroy the session** when done:
   ```
   python sandbox_ctl.py destroy session-0
   ```

IMPORTANT: Never skip the screenshot-after-action step. GUI automation is unreliable without visual confirmation. If an action did not produce the expected result, try a different approach (different coordinates, different key combo, etc.).

## Command Reference

### Session Management

| Command | Description |
|---------|-------------|
| `create <session>` | Create an isolated hidden desktop and start the agent process. The session name becomes part of the desktop and pipe names. |
| `destroy <session>` | Tear down the session: kill all processes on the hidden desktop, close the agent, destroy the desktop. |
| `list` | Show all active sessions with their desktop names, pipe paths, and status. |

### Application Control

| Command | Description |
|---------|-------------|
| `launch <session> <exe> [args...]` | Launch an application on the session's hidden desktop. Returns the PID, main window HWND, title, and bounding rect. |
| `windows <session>` | List all windows currently on the session's hidden desktop. Each entry includes HWND, title, class name, rect, and visibility. |
| `focus <session> <hwnd>` | Bring a specific window to the foreground within the session. Use this when multiple windows exist and you need to interact with a specific one. |

### Display

| Command | Description |
|---------|-------------|
| `screenshot <session> [--output path] [--hwnd H]` | Capture the session's desktop or a specific window as a PNG image. If `--hwnd` is omitted, captures the largest visible window. If `--output` is omitted, writes to `screenshot-<session>.png`. |

### Input

| Command | Description |
|---------|-------------|
| `click <session> <x> <y> [--button left\|right] [--double] [--hwnd H]` | Click at pixel coordinates within the target window. Defaults to left single-click. |
| `type <session> <text>` | Type arbitrary Unicode text character by character. Works for all BMP characters including accented letters and CJK. |
| `key <session> <combo>` | Send a key combination. Uses `+` as separator for chords. Modifiers are held while the final key is pressed. |
| `scroll <session> <x> <y> <delta> [--hwnd H]` | Scroll at the given coordinates. Positive delta scrolls up, negative scrolls down. |

## Coordinate System

- All coordinates are in **pixels relative to the target window's client area**.
- `(0, 0)` is the top-left corner of the window content area (excludes title bar and borders).
- Get window dimensions from the `rect` field returned by `launch` or `windows`. The rect is `[left, top, right, bottom]` in screen coordinates. Window width = `right - left`, height = `bottom - top`.
- When clicking, estimate coordinates from the screenshot. A screenshot is a pixel-perfect capture of the window, so pixel positions in the image map directly to click coordinates.

## Key Combo Syntax

Key combos use `+` as a separator. Modifiers come first, followed by the main key.

Examples:
```
enter           -- press Enter
tab             -- press Tab
escape          -- press Escape
ctrl+c          -- copy
ctrl+v          -- paste
ctrl+a          -- select all
ctrl+s          -- save
ctrl+shift+s    -- save as
alt+f4          -- close window
ctrl+z          -- undo
ctrl+y          -- redo
f5              -- refresh / run
```

Available key names:
- **Modifiers**: `ctrl`, `alt`, `shift`, `win`
- **Navigation**: `enter`, `tab`, `escape`, `backspace`, `delete`, `space`
- **Arrows**: `up`, `down`, `left`, `right`
- **Pages**: `home`, `end`, `pageup`, `pagedown`
- **Function keys**: `f1` through `f12`
- **Single characters**: any single character like `a`, `b`, `1`, `=`, etc.

## Parallel Sessions

Each session is fully isolated:
- Separate Win32 desktop (invisible to user and other sessions)
- Separate agent process with its own named pipe
- Actions in one session cannot affect another session

Use unique session names:
```
python sandbox_ctl.py create session-0
python sandbox_ctl.py create session-1
python sandbox_ctl.py create session-2
```

Use unique output paths for screenshots:
```
python sandbox_ctl.py screenshot session-0 --output shot-0.png
python sandbox_ctl.py screenshot session-1 --output shot-1.png
```

You can run multiple sessions simultaneously for parallel workflows. Always destroy all sessions when finished.

## Tips and Best Practices

1. **Always screenshot after every action.** This is non-negotiable. GUI state is unpredictable.

2. **Wait after launch.** Applications need 1-3 seconds after launch before their UI is ready for input. Screenshot to confirm the window has rendered.

3. **Use `windows` to find the right HWND.** If `launch` does not return the expected window, or if the app spawns multiple windows (e.g., a splash screen and a main window), use the `windows` command to enumerate all windows and find the correct one.

4. **Retry with different coordinates.** If a click does not produce the expected effect, the coordinate may be slightly off. Try clicking a few pixels in each direction, or click on a different part of the element.

5. **Check the `ok` field.** Every response includes `"ok": true` or `"ok": false`. On failure, the `"error"` field explains what went wrong.

6. **Use `key` for keyboard shortcuts, `type` for text.** Do not use `type` to send Enter or Tab -- use `key enter` or `key tab` instead.

7. **Destroy sessions in a finally block.** If your task might fail partway through, always clean up sessions. Leaked sessions waste resources.

## Supported Applications

CDCS works with standard windowing toolkits:
- **Win32** (classic Windows applications like Notepad, Paint, Calculator)
- **WPF** (.NET Windows Presentation Foundation apps)
- **WinForms** (.NET Windows Forms apps)
- **Electron** (VS Code, Slack, Discord, etc.)
- **Qt** (many cross-platform desktop apps)
- **GTK** (GIMP, Inkscape, etc.)

## Limitations

- **DirectX/OpenGL full-screen** apps return black screenshots. Windowed/borderless-windowed mode works.
- **UWP apps** may have limited support for PostMessage-based input. Use SendInput-based methods when available.
- **Some custom controls** may not respond to PostMessage mouse clicks. If a click returns `"method": "postmessage"` and the UI did not change, try a different approach.
- **DPI scaling** may affect coordinate mapping. Screenshots always show the actual pixels rendered.

## Error Handling

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Screenshot is black/blank | App does not support PrintWindow | Try `--hwnd` targeting a child window, or the app may not be compatible |
| Click has no effect | Wrong coordinates or wrong HWND | Re-screenshot and re-estimate coordinates; try `windows` to find the correct HWND |
| `ok: false` on create | Desktop creation failed | Check that no session with the same name already exists; destroy stale sessions first |
| Type produces garbled text | Focus is on the wrong window | Use `focus` to set the correct window before typing |
| Pipe timeout | Agent process crashed or never started | Destroy the session and create a new one |
