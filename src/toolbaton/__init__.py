"""tool-baton — pass your context between AI coding agents.

Reads one agent's chat history, rules and derived project knowledge, and writes
them into another's. The canonical model in `ir.py` is the seam: an adapter only
has to reach the IR, never another agent's format.
"""

from __future__ import annotations

try:  # installed
    from importlib.metadata import version

    __version__ = version("tool-baton")
except Exception:  # running from a source checkout
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
