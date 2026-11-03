from __future__ import annotations


MAX_JSON_NESTING = 128


class JSONNestingError(ValueError):
    """JSON exceeds the scanner's supported nesting depth."""


def check_json_nesting(text: str) -> None:
    """Reject excessively nested JSON before runtime-specific decoders can fail."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_NESTING:
                raise JSONNestingError
        elif char in "]}":
            depth = max(0, depth - 1)
