"""Unified diff generation and binary detection helpers."""

from __future__ import annotations

import difflib

# A file is considered binary if it contains a NUL byte, or if a very long
# "line" (no newline for a long stretch) is found, which is typical of binary blobs.
_MAX_BINARY_LINE = 1024 * 1024  # 1 MiB


def is_binary(data: bytes) -> bool:
    """Return True when *data* looks like binary content."""
    if b"\x00" in data:
        return True
    # Search for an extremely long run without a newline.
    last = -1
    idx = 0
    limit = len(data)
    while True:
        idx = data.find(b"\n", idx)
        if idx == -1:
            segment = limit - (last + 1)
            return segment > _MAX_BINARY_LINE
        segment = idx - (last + 1)
        if segment > _MAX_BINARY_LINE:
            return True
        last = idx
        idx += 1


def unified_diff(
    old_text: str,
    new_text: str,
    from_path: str = "",
    to_path: str = "",
) -> str:
    """Convenience wrapper producing a path-annotated unified diff."""
    from_label = f"a/{from_path}" if from_path else "a"
    to_label = f"b/{to_path}" if to_path else "b"
    return "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=from_label,
            tofile=to_label,
        )
    )
