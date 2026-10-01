"""Strict dict <-> dataclass conversion for job.json and result.json (stdlib only).

Unknown keys, missing keys and wrong types are errors, reported with their dotted path.
Supported field types: str, int, float, bool, Any, Optional[X], Tuple[X, ...], fixed Tuples,
List[X], Dict[str, X], and nested dataclasses. A dataclass may define validate() -> list of errors.
"""

import dataclasses
import json
import typing
from typing import Any, Dict, List, Type, TypeVar

T = TypeVar("T")


class SchemaError(ValueError):
    def __init__(self, errors: List[str]):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def from_dict(cls: Type[T], data: Any, path: str = "") -> T:
    errors: List[str] = []
    obj = _from_dict(cls, data, path, errors)
    if errors:
        raise SchemaError(errors)
    return obj


def _from_dict(cls, data, path, errors):
    where = path or cls.__name__
    if not isinstance(data, dict):
        errors.append(f"{where}: expected an object")
        return None
    hints = typing.get_type_hints(cls)
    fields = {f.name: f for f in dataclasses.fields(cls)}
    for key in data:
        if key not in fields:
            errors.append(f"{path + '.' if path else ''}{key}: unknown key")
    kwargs = {}
    before = len(errors)
    for name, f in fields.items():
        sub = f"{path}.{name}" if path else name
        if name not in data:
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                errors.append(f"{sub}: missing")
            continue
        kwargs[name] = _convert(hints[name], data[name], sub, errors)
    if len(errors) > before:
        return None
    obj = cls(**kwargs)
    check = getattr(obj, "validate", None)
    if check is not None:
        errors.extend(f"{path + ': ' if path else ''}{e}" for e in check())
    return obj


def _convert(tp, value, path, errors):
    if tp is Any:
        return value
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin is typing.Union:
        if value is None and type(None) in args:
            return None
        rest = [a for a in args if a is not type(None)]
        if len(rest) == 1:
            return _convert(rest[0], value, path, errors)
        errors.append(f"{path}: unsupported union")
        return None
    if dataclasses.is_dataclass(tp):
        return _from_dict(tp, value, path, errors)
    if origin in (tuple, list):
        if not isinstance(value, (list, tuple)):
            errors.append(f"{path}: expected a list")
            return None
        if origin is tuple and not (len(args) == 2 and args[1] is Ellipsis):
            if len(value) != len(args):
                errors.append(f"{path}: expected {len(args)} items, got {len(value)}")
                return None
            return tuple(_convert(a, v, f"{path}[{i}]", errors) for i, (a, v) in enumerate(zip(args, value)))
        items = [_convert(args[0], v, f"{path}[{i}]", errors) for i, v in enumerate(value)]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
            errors.append(f"{path}: expected an object")
            return None
        return {k: _convert(args[1], v, f"{path}.{k}", errors) for k, v in value.items()}
    if tp is bool:
        if not isinstance(value, bool):
            errors.append(f"{path}: expected true/false, got {value!r}")
        return value
    if tp is int:
        if isinstance(value, bool) or not isinstance(value, int):
            errors.append(f"{path}: expected an integer, got {value!r}")
        return value
    if tp is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"{path}: expected a number, got {value!r}")
            return value
        return float(value)
    if tp is str:
        if not isinstance(value, str):
            errors.append(f"{path}: expected a string, got {value!r}")
        return value
    errors.append(f"{path}: unsupported field type {tp!r}")
    return value


def to_dict(obj: Any) -> Any:
    """Dataclasses -> plain dicts/lists, ready for json.dumps."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def dumps(obj: Any) -> str:
    return json.dumps(to_dict(obj), indent=2, sort_keys=False) + "\n"
