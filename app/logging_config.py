"""Central logging configuration utilities.

Provides a helper to obtain a logger with either text or JSON formatting
based on ODIN_REQUEST_LOG_JSON (shared switch for simplicity).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

_JSON_ENV_VALUES = {"1", "true", "yes", "on"}


class _JsonFormatter(logging.Formatter):  # pragma: no cover - pure formatting
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        return json.dumps(base, separators=(",", ":"))


def get_logger(name: str, level_env: str | None = None, default_level: str = "INFO") -> logging.Logger:
    """Return a logger configured once.

    Parameters:
      name: logger name
      level_env: optional env var name containing a logging level
      default_level: fallback level if env invalid
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        if os.getenv("ODIN_REQUEST_LOG_JSON", "false").lower() in _JSON_ENV_VALUES:
            handler.setFormatter(_JsonFormatter())
        else:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        logger.addHandler(handler)
    # level resolution
    if level_env:
        lvl_str = os.getenv(level_env, default_level).upper()
    else:
        lvl_str = default_level.upper()
    logger.setLevel(getattr(logging, lvl_str, logging.INFO))
    return logger
