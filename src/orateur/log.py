"""Application-wide logging configuration."""

import logging
import os
import sys


def setup_logging(
    level: str | int | None = None,
    format_string: str = "%(levelname)s - %(name)s - %(message)s",
) -> None:
    """Configure logging for the application.

    Logs to stderr. Level can be set via ORATEUR_LOG_LEVEL env var
    (DEBUG, INFO, WARNING, ERROR).
    """
    if level is None:
        level = os.environ.get("ORATEUR_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger("orateur")
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(format_string))
    root.addHandler(handler)

    # Prevent propagation to root logger (avoids duplicate logs)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given module name."""
    if name.startswith("orateur"):
        return logging.getLogger(name)
    return logging.getLogger(f"orateur.{name}")
