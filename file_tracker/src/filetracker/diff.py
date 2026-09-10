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


def decode_text(data: bytes) -> str | None:
    """Decode supported text encodings without mistaking UTF-16 for binary.

    UTF-16 text commonly contains NUL bytes, so it must be decoded before the
    generic binary heuristic is applied.  A BOM is required for non-UTF-8
    encodings to avoid classifying arbitrary binary data as source text.
    """
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        try:
            return data.decode("utf-32")
        except UnicodeDecodeError:
            return None
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    if is_binary(data):
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


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
