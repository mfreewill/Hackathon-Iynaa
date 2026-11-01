"""Deterministic logical-byte cost of a retained object graph.

``logical_payload_bytes`` measures the supplied value as stored, not
any visible projection of it: it never calls ``snapshot()`` or other
domain methods, and it is not response JSON size, ``repr``, pickle
size, or operating-system heap size. Frozen request bindings, raw
proposals, and decision history therefore count in full.

Metric (fixed engineering choices, not calibrated heap claims):

- Every visited value adds a fixed framing of 16 bytes.
- ``str`` adds its strict UTF-8 payload length (text is preserved
  exactly; nothing is normalized). ``bytes`` adds its actual length.
- ``bool`` adds 1 byte, finite ``float`` adds a fixed 8 bytes, and
  ``None`` adds 0 payload bytes (framing only).
- ``int`` adds its sign-inclusive byte width ``(bit_length + 8) // 8``
  (at least 1), so arbitrary-size integers need no decimal string.
- Containers add framing plus the cost of every stored child:
  mapping keys and values, sequence/set members, every stored
  dataclass field (plus the UTF-8 length of each field name), and
  every instance attribute of ``TeacherWorkspace`` /
  ``source_contract.Workspace`` (plus each attribute name length).
  Stored fields are discovered from the value itself
  (``dataclasses.fields`` / instance ``__dict__``), never from a
  duplicated field list.

Counting is per reference: a shared child reached twice is counted
twice (a conservative overcount, never a silent skip). Active
recursion (a cycle) is refused. Traversal is iterative and bounded:
depth beyond 128 edges from the root, or more than 1,000,000 visited
values, is refused with no partial result.

Supported values are exactly: ``None``, ``bool``, ``int``, finite
``float``, ``str``, ``bytes``, ``dict`` and other ``Mapping`` values,
``list``, ``tuple``, ``set``, ``frozenset``, dataclass instances, and
instances of the two domain workspace classes. Builtin scalars and
containers must be the exact builtin types. Classes, functions, and
any other opaque object are unsupported. Unsupported values,
non-finite floats, and strings with unpaired surrogates raise
``ValueError`` with a fixed message that echoes no caller content.
Only trusted, well-typed domain state is in scope.
"""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping

__all__ = ["logical_payload_bytes"]

_FRAMING_BYTES = 16
_MAX_DEPTH = 128
_MAX_NODES = 1_000_000
_DOMAIN_TYPES = frozenset({
    ("teacher_demo.adapter", "TeacherWorkspace"),
    ("source_contract.core", "Workspace"),
})

_MSG_UNSUPPORTED = "unsupported value"
_MSG_CYCLE = "cyclic value"
_MSG_LIMIT = "cost limit exceeded"
_MSG_FLOAT = "non-finite float value"
_MSG_STRING = "string value is not UTF-8 encodable"


def _utf8_len(text):
    try:
        return len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError(_MSG_STRING) from None


def logical_payload_bytes(value) -> int:
    """Return the logical-byte cost of the retained graph at ``value``."""
    total = 0
    nodes = 0
    active = set()
    stack = []

    def push(node, depth):
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES or depth > _MAX_DEPTH:
            raise ValueError(_MSG_LIMIT)
        stack.append((node, depth, False))

    push(value, 0)
    while stack:
        node, depth, exiting = stack.pop()
        if exiting:
            active.discard(id(node))
            continue
        kind = type(node)
        if node is None:
            total += _FRAMING_BYTES
            continue
        if kind is bool:
            total += _FRAMING_BYTES + 1
            continue
        if kind is int:
            total += _FRAMING_BYTES + (node.bit_length() + 8) // 8
            continue
        if kind is float:
            if not math.isfinite(node):
                raise ValueError(_MSG_FLOAT)
            total += _FRAMING_BYTES + 8
            continue
        if kind is str:
            total += _FRAMING_BYTES + _utf8_len(node)
            continue
        if kind is bytes:
            total += _FRAMING_BYTES + len(node)
            continue
        marker = id(node)
        if marker in active:
            raise ValueError(_MSG_CYCLE)
        if kind is list or kind is tuple:
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for child in node:
                push(child, depth + 1)
            continue
        if kind is set or kind is frozenset:
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for child in node:
                push(child, depth + 1)
            continue
        if kind is dict:
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for key, item in dict.items(node):
                push(key, depth + 1)
                push(item, depth + 1)
            continue
        if (kind.__module__, kind.__qualname__) in _DOMAIN_TYPES:
            try:
                state = object.__getattribute__(node, "__dict__")
            except AttributeError:
                raise ValueError(_MSG_UNSUPPORTED) from None
            if type(state) is not dict:
                raise ValueError(_MSG_UNSUPPORTED)
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for name, child in dict.items(state):
                if type(name) is not str:
                    raise ValueError(_MSG_UNSUPPORTED)
                total += _utf8_len(name)
                push(child, depth + 1)
            continue
        if dataclasses.is_dataclass(node) and not isinstance(node, type):
            try:
                fields = dataclasses.fields(node)
            except TypeError:
                raise ValueError(_MSG_UNSUPPORTED) from None
            try:
                state = object.__getattribute__(node, "__dict__")
            except AttributeError:
                state = None
            if type(state) is not dict:
                state = None
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for entry in fields:
                total += _utf8_len(entry.name)
                if state is not None and entry.name in state:
                    child = state[entry.name]
                else:
                    try:
                        child = object.__getattribute__(node, entry.name)
                    except AttributeError:
                        raise ValueError(_MSG_UNSUPPORTED) from None
                push(child, depth + 1)
            continue
        if isinstance(node, Mapping):
            active.add(marker)
            total += _FRAMING_BYTES
            stack.append((node, depth, True))
            for key, item in node.items():
                push(key, depth + 1)
                push(item, depth + 1)
            continue
        raise ValueError(_MSG_UNSUPPORTED)
    return total
