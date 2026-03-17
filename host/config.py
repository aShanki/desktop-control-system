"""CDCS persistent configuration.

Settings are stored in ``~/.cdcs/config.json`` and survive across CLI
invocations and conversations.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

CONFIG_PATH = pathlib.Path.home() / ".cdcs" / "config.json"

DEFAULTS: dict[str, Any] = {
    "preview_enabled": False,
    "preview_refresh_ms": 500,
}


def load() -> dict[str, Any]:
    """Load config from disk, merged with defaults."""
    config = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            stored = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    return config


def save(config: dict[str, Any]) -> None:
    """Write config to disk."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def get(key: str) -> Any:
    """Get a single config value."""
    return load().get(key, DEFAULTS.get(key))


def set_value(key: str, value: Any) -> dict[str, Any]:
    """Set a single config value and persist."""
    config = load()
    config[key] = value
    save(config)
    return config
