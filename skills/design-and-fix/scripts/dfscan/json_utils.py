from __future__ import annotations


MAX_JSON_NESTING = 128
MAX_JSON_NUMBER_DIGITS = 4300


class JSONNestingError(ValueError):
    """JSON exceeds the scanner's supported nesting depth."""


class JSONNumberError(ValueError):
    """A JSON number exceeds the scanner's supported digit count."""


def check_json_safety(text: str) -> None:
    """Reject pathological JSON before runtime-specific decoders can fail or consume excess work."""
    depth = 0
    number_digits = 0
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
            number_digits = 0
        elif char in "[{":
            depth += 1
            number_digits = 0
            if depth > MAX_JSON_NESTING:
                raise JSONNestingError
        elif char in "]}":
            depth = max(0, depth - 1)
            number_digits = 0
        elif char in "0123456789eE+-.":
            if char.isdigit():
                number_digits += 1
                if number_digits > MAX_JSON_NUMBER_DIGITS:
                    raise JSONNumberError
        else:
            number_digits = 0
