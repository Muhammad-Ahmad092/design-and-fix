"""Stdlib mini JSON-schema validator: type, required, properties, items, enum,
additionalProperties (bool or schema)."""
from __future__ import annotations

from typing import Any, List

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def validate(instance: Any, schema: dict, path: str = "$") -> List[str]:
    errors: List[str] = []
    typ = schema.get("type")
    if typ is not None:
        names = typ if isinstance(typ, list) else [typ]
        if not any(_TYPES[n](instance) for n in names):
            return ["%s: expected %s, got %s" % (path, "/".join(names), type(instance).__name__)]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append("%s: %r not in %r" % (path, instance, schema["enum"]))
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append("%s: missing required key %r" % (path, key))
        extra = schema.get("additionalProperties", True)
        for key, val in instance.items():
            sub = "%s.%s" % (path, key)
            if key in props:
                errors.extend(validate(val, props[key], sub))
            elif extra is False:
                errors.append("%s: unexpected key %r" % (path, key))
            elif isinstance(extra, dict):
                errors.extend(validate(val, extra, sub))
    if isinstance(instance, list) and "items" in schema:
        for i, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], "%s[%d]" % (path, i)))
    return errors
