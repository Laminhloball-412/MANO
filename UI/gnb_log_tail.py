import os


def read_appended_lines(path, offset):
    """Return complete lines appended after offset and the next read offset."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset

    start = 0 if size < offset else offset
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(start)
        lines = handle.read().splitlines()
    return lines, size
