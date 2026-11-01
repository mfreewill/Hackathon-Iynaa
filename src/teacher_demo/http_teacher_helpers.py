"""Pure helpers for the teacher HTTP boundary (no I/O, no sockets).

Snapshot projection, recursive freezing of provider-bound request
data, and bounded detached copies of provider-returned JSON-like
values. Nothing here calls a provider, the decoder, or HTTP.
"""
from __future__ import annotations

import dataclasses
import json
import math
from typing import Mapping

_MAX_DEPTH = 32
_MAX_NODES = 20_000


class FrozenMap(Mapping):
    """Immutable mapping whose deepcopy is itself.

    ``mappingproxy`` cannot be deep-copied, while tracer/provider
    code legitimately does ``copy.deepcopy(dict(request))``; an
    immutable value needs no copy, so deepcopy returns ``self``.
    """

    __slots__ = ("_data",)

    def __init__(self, data):
        object.__setattr__(self, "_data", dict(data))

    def __getitem__(self, key):
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self):
        return len(self._data)

    def __repr__(self):
        return f"FrozenMap({self._data!r})"

    def __eq__(self, other):
        if isinstance(other, Mapping):
            return dict(self) == dict(other)
        return NotImplemented

    __hash__ = None

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self


def jsonable(value):
    """Faithfully project dataclasses/tuples to JSON-shaped data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: jsonable(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def snapshot_payload(snapshot):
    return jsonable(snapshot)


def deep_freeze(value):
    """Recursively freeze containers; deepcopy(dict(...)) stays safe.

    Mappings become ``MappingProxyType`` and sequences become
    tuples at every depth, so freezing is never only the outer
    mapping. Bytes/str/numbers/None are already immutable.
    """
    if isinstance(value, Mapping):
        return FrozenMap(
            {key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


def detached_json_value(value, byte_limit):
    """Return a detached plain JSON-like copy within ``byte_limit``.

    Rejects (``ValueError``, value-free) unsupported types, cycles,
    non-string keys, non-finite floats, strings without a strict
    UTF-8 representation, and values whose canonical UTF-8 JSON
    encoding exceeds the limit. The result shares no mutable
    container with the provider's object.
    """
    budget = {"nodes": 0}

    def walk(item, depth, active):
        budget["nodes"] += 1
        if depth > _MAX_DEPTH or budget["nodes"] > _MAX_NODES:
            raise ValueError("provider result is malformed")
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeEncodeError:
                raise ValueError(
                    "provider result is malformed") from None
            return item
        if isinstance(item, int):
            return item
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("provider result is malformed")
            return item
        if isinstance(item, Mapping):
            marker = id(item)
            if marker in active:
                raise ValueError("provider result is malformed")
            active.add(marker)
            try:
                copied = {}
                for key, child in item.items():
                    if not isinstance(key, str):
                        raise ValueError("provider result is malformed")
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        raise ValueError(
                            "provider result is malformed") from None
                    copied[key] = walk(child, depth + 1, active)
                return copied
            finally:
                active.discard(marker)
        if isinstance(item, (list, tuple)):
            marker = id(item)
            if marker in active:
                raise ValueError("provider result is malformed")
            active.add(marker)
            try:
                return [walk(child, depth + 1, active) for child in item]
            finally:
                active.discard(marker)
        raise ValueError("provider result is malformed")

    copied = walk(value, 0, set())
    if canonical_size(copied) > byte_limit:
        raise ValueError("provider result is malformed")
    return copied


def canonical_size(value):
    """UTF-8 byte size of canonical JSON for an admitted value."""
    return len(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False).encode("utf-8"))
