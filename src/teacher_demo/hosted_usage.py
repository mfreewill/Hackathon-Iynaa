"""Durable hosted-usage accounting around one provider call.

Import is inert: no file, network, credential, or environment access
happens at import or construction time. A store is created only by
``initialize_usage`` for a new path; ``UsageLedger`` opens an existing
store per operation (``mode=rw``), validates its schema and fixed
policy inside one ``BEGIN IMMEDIATE`` transaction, and commits an
incremented attempt count before permission is returned. The store
holds only the format version, the fixed policy, the spent count,
the last start time, and a latched closed flag -- never a credential
or an application payload. ``_now()`` is the only wall-clock seam.

Configuration errors raise one fixed ``ValueError``. Runtime
accounting failures raise a value-free ``UsageDenied`` whose ``code``
is one of a closed set, with no retained cause or context, and never
fall through to the delegate provider.
"""

import math
import os
import sqlite3
import time
from pathlib import Path

from teacher_demo.meta_provider import ProviderCallContext

__all__ = [
    "BudgetedProvider",
    "UsageDenied",
    "UsageLedger",
    "UsagePolicy",
    "initialize_usage",
]

_INVALID = "hosted usage configuration is invalid"
_IMMUTABLE = "usage policy is immutable"
_EXHAUSTED = "usage_exhausted"
_WINDOW_CLOSED = "usage_window_closed"
_RATE_LIMITED = "usage_rate_limited"
_UNAVAILABLE = "usage_accounting_unavailable"
_DENIED_CODES = frozenset(
    (_EXHAUSTED, _WINDOW_CLOSED, _RATE_LIMITED, _UNAVAILABLE))

_EXPECTED_COLUMNS = (
    ("id", "INTEGER", 1),
    ("version", "INTEGER", 0),
    ("max_attempts", "INTEGER", 0),
    ("max_output_tokens", "INTEGER", 0),
    ("window_end", "REAL", 0),
    ("minimum_spacing_seconds", "REAL", 0),
    ("spent", "INTEGER", 0),
    ("last_start", "REAL", 0),
    ("closed", "INTEGER", 0),
)

_CREATE_TABLE_SQL = (
    "CREATE TABLE usage_state ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "version INTEGER NOT NULL CHECK (version = 1), "
    "max_attempts INTEGER NOT NULL CHECK (max_attempts > 0), "
    "max_output_tokens INTEGER NOT NULL "
    "CHECK (max_output_tokens >= 16), "
    "window_end REAL NOT NULL CHECK (window_end > 0), "
    "minimum_spacing_seconds REAL NOT NULL "
    "CHECK (minimum_spacing_seconds > 0), "
    "spent INTEGER NOT NULL CHECK (spent >= 0), "
    "last_start REAL CHECK (last_start IS NULL OR last_start > 0), "
    "closed INTEGER NOT NULL CHECK (closed IN (0, 1))"
    ")"
)

_SELECT_ROW_SQL = (
    "SELECT id, version, max_attempts, max_output_tokens, window_end, "
    "minimum_spacing_seconds, spent, last_start, closed "
    "FROM usage_state"
)


def _now():
    return time.time()


def _finite_positive_float(value):
    if type(value) is int:
        if value <= 0:
            return None
        try:
            converted = float(value)
        except (OverflowError, ValueError):
            return None
        if math.isfinite(converted) and converted > 0:
            return converted
        return None
    if type(value) is float:
        if math.isfinite(value) and value > 0:
            return value
        return None
    return None


def _valid_max_attempts(value):
    return type(value) is int and value > 0


def _valid_max_output_tokens(value):
    return type(value) is int and value >= 16


class UsagePolicy:
    """Immutable fixed usage limits for one hosted window."""

    __slots__ = (
        "max_attempts",
        "max_output_tokens",
        "window_end",
        "minimum_spacing_seconds",
    )

    def __init__(self, *, max_attempts, max_output_tokens, window_end,
                 minimum_spacing_seconds):
        window = _finite_positive_float(window_end)
        spacing = _finite_positive_float(minimum_spacing_seconds)
        if not _valid_max_attempts(max_attempts):
            raise ValueError(_INVALID)
        if not _valid_max_output_tokens(max_output_tokens):
            raise ValueError(_INVALID)
        if window is None or spacing is None:
            raise ValueError(_INVALID)
        object.__setattr__(self, "max_attempts", max_attempts)
        object.__setattr__(self, "max_output_tokens", max_output_tokens)
        object.__setattr__(self, "window_end", window)
        object.__setattr__(self, "minimum_spacing_seconds", spacing)

    def __setattr__(self, name, value):
        raise AttributeError(_IMMUTABLE)

    def __delattr__(self, name):
        raise AttributeError(_IMMUTABLE)

    def __eq__(self, other):
        if type(other) is not UsagePolicy:
            return NotImplemented
        return (
            self.max_attempts == other.max_attempts
            and self.max_output_tokens == other.max_output_tokens
            and self.window_end == other.window_end
            and self.minimum_spacing_seconds
            == other.minimum_spacing_seconds
        )

    def __hash__(self):
        return hash((self.max_attempts, self.max_output_tokens,
                     self.window_end, self.minimum_spacing_seconds))

    def __repr__(self):
        return (
            f"UsagePolicy(max_attempts={self.max_attempts!r}, "
            f"max_output_tokens={self.max_output_tokens!r}, "
            f"window_end={self.window_end!r}, "
            f"minimum_spacing_seconds={self.minimum_spacing_seconds!r})"
        )


class UsageDenied(Exception):
    """Value-free hosted-usage refusal with one closed code."""

    def __init__(self, code):
        if not isinstance(code, str) or code not in _DENIED_CODES:
            raise ValueError(_INVALID)
        super().__init__(code)
        self.code = code


def _policy_values(policy):
    if not isinstance(policy, UsagePolicy):
        return None
    try:
        max_attempts = policy.max_attempts
        max_output_tokens = policy.max_output_tokens
        window_end = policy.window_end
        minimum_spacing_seconds = policy.minimum_spacing_seconds
    except Exception:
        return None
    window = _finite_positive_float(window_end)
    spacing = _finite_positive_float(minimum_spacing_seconds)
    if not _valid_max_attempts(max_attempts):
        return None
    if not _valid_max_output_tokens(max_output_tokens):
        return None
    if window is None or spacing is None:
        return None
    return (max_attempts, max_output_tokens, window, spacing)


def _path_string(path):
    if isinstance(path, str):
        value = path
    elif isinstance(path, os.PathLike):
        try:
            value = os.fspath(path)
        except Exception:
            return None
    else:
        return None
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    return value


def _sqlite_uri(path_string):
    try:
        return Path(path_string).absolute().as_uri() + "?mode=rw"
    except Exception:
        return None


def _close_quietly(connection):
    if connection is None:
        return
    try:
        connection.close()
    except Exception:
        pass


def _execute(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.rowcount
    finally:
        cursor.close()


def _fetchone(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.fetchone()
    finally:
        cursor.close()


def _fetchall(connection, sql, parameters=()):
    cursor = connection.execute(sql, parameters)
    try:
        return cursor.fetchall()
    finally:
        cursor.close()


def _rollback_quietly(connection):
    try:
        _execute(connection, "ROLLBACK")
    except Exception:
        pass


def _open_connection(uri, synchronous_full):
    connection = sqlite3.connect(
        uri, timeout=5.0, isolation_level=None, uri=True)
    try:
        _execute(connection, "PRAGMA busy_timeout = 5000")
        if synchronous_full:
            _execute(connection, "PRAGMA synchronous = FULL")
    except Exception:
        _close_quietly(connection)
        raise
    return connection


def _read_validated_row(connection, expected):
    version_row = _fetchone(connection, "PRAGMA user_version")
    if version_row is None or len(version_row) != 1:
        return None
    if type(version_row[0]) is not int or version_row[0] != 1:
        return None
    mode_row = _fetchone(connection, "PRAGMA journal_mode")
    if mode_row is None or len(mode_row) != 1 or mode_row[0] != "delete":
        return None
    objects = _fetchall(
        connection,
        "SELECT type, name FROM sqlite_master "
        "WHERE substr(name, 1, 7) <> 'sqlite_'")
    if objects != [("table", "usage_state")]:
        return None
    columns = _fetchall(connection, "PRAGMA table_info(usage_state)")
    if len(columns) != len(_EXPECTED_COLUMNS):
        return None
    for index, column in enumerate(columns):
        expected_name, expected_type, expected_pk = _EXPECTED_COLUMNS[index]
        if len(column) != 6:
            return None
        cid, name, declared_type, _notnull, _default, pk = column
        if cid != index or name != expected_name:
            return None
        if not isinstance(declared_type, str):
            return None
        if declared_type.upper() != expected_type or pk != expected_pk:
            return None
    rows = _fetchall(connection, _SELECT_ROW_SQL)
    if len(rows) != 1 or len(rows[0]) != 9:
        return None
    (row_id, version, max_attempts, max_output_tokens, window_end,
     minimum_spacing_seconds, spent, last_start, closed) = rows[0]
    if type(row_id) is not int or row_id != 1:
        return None
    if type(version) is not int or version != 1:
        return None
    if not _valid_max_attempts(max_attempts):
        return None
    if not _valid_max_output_tokens(max_output_tokens):
        return None
    window = _finite_positive_float(window_end)
    spacing = _finite_positive_float(minimum_spacing_seconds)
    if window is None or spacing is None:
        return None
    if type(spent) is not int or spent < 0 or spent > max_attempts:
        return None
    if type(closed) is not int or closed not in (0, 1):
        return None
    if spent == 0:
        if last_start is not None:
            return None
        last = None
    else:
        last = _finite_positive_float(last_start)
        if last is None or not last < window:
            return None
    if (max_attempts, max_output_tokens, window, spacing) != expected:
        return None
    return (max_attempts, max_output_tokens, window, spacing,
            spent, last, closed == 1)


def _sample_now():
    try:
        raw = _now()
    except Exception:
        return None
    return _finite_positive_float(raw)


def _reserve_attempt(uri, expected):
    connection = None
    in_transaction = False
    code = _UNAVAILABLE
    try:
        connection = _open_connection(uri, True)
        _execute(connection, "BEGIN IMMEDIATE")
        in_transaction = True
        row = _read_validated_row(connection, expected)
        now = _sample_now() if row is not None else None
        if row is not None and now is not None:
            max_attempts = row[0]
            window = row[2]
            spacing = row[3]
            spent = row[4]
            last = row[5]
            closed = row[6]
            if closed:
                code = _WINDOW_CLOSED
            elif now >= window:
                changed = _execute(
                    connection,
                    "UPDATE usage_state SET closed = 1 WHERE id = 1")
                if changed == 1:
                    _execute(connection, "COMMIT")
                    in_transaction = False
                    code = _WINDOW_CLOSED
            elif spent >= max_attempts:
                code = _EXHAUSTED
            elif last is not None and now < last + spacing:
                code = _RATE_LIMITED
            else:
                changed = _execute(
                    connection,
                    "UPDATE usage_state SET spent = ?, last_start = ? "
                    "WHERE id = 1",
                    (spent + 1, now))
                if changed == 1:
                    _execute(connection, "COMMIT")
                    in_transaction = False
                    code = None
        if in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
    except Exception:
        if connection is not None and in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
        code = _UNAVAILABLE
    finally:
        _close_quietly(connection)
    return code


def _snapshot_attempt(uri, expected):
    connection = None
    in_transaction = False
    snapshot = None
    code = _UNAVAILABLE
    try:
        connection = _open_connection(uri, False)
        _execute(connection, "BEGIN")
        in_transaction = True
        row = _read_validated_row(connection, expected)
        if row is not None:
            snapshot = {
                "version": 1,
                "policy": {
                    "max_attempts": row[0],
                    "max_output_tokens": row[1],
                    "window_end": row[2],
                    "minimum_spacing_seconds": row[3],
                },
                "spent": row[4],
                "last_start": row[5],
                "closed": row[6],
            }
            code = None
        if in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
    except Exception:
        if connection is not None and in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
        snapshot = None
        code = _UNAVAILABLE
    finally:
        _close_quietly(connection)
    return snapshot, code


def _unlink_quietly(path_string):
    try:
        os.unlink(path_string)
    except Exception:
        pass


def _initialize_attempt(path_string, uri, expected):
    created = False
    connection = None
    in_transaction = False
    success = False
    try:
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(path_string, flags, 0o600)
        created = True
        os.close(descriptor)
        connection = _open_connection(uri, True)
        mode_row = _fetchone(connection, "PRAGMA journal_mode = DELETE")
        if (mode_row is not None and len(mode_row) == 1
                and mode_row[0] == "delete"):
            _execute(connection, "BEGIN IMMEDIATE")
            in_transaction = True
            _execute(connection, _CREATE_TABLE_SQL)
            inserted = _execute(
                connection,
                "INSERT INTO usage_state (id, version, max_attempts, "
                "max_output_tokens, window_end, "
                "minimum_spacing_seconds, spent, last_start, closed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (1, 1, expected[0], expected[1], expected[2],
                 expected[3], 0, None, 0))
            _execute(connection, "PRAGMA user_version = 1")
            row = _read_validated_row(connection, expected)
            if (inserted == 1 and row is not None and row[4] == 0
                    and row[5] is None and row[6] is False):
                _execute(connection, "COMMIT")
                in_transaction = False
                success = True
        if in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
    except Exception:
        if connection is not None and in_transaction:
            _rollback_quietly(connection)
            in_transaction = False
        success = False
    finally:
        _close_quietly(connection)
        if not success and created:
            _unlink_quietly(path_string)
            _unlink_quietly(path_string + "-journal")
            _unlink_quietly(path_string + "-wal")
            _unlink_quietly(path_string + "-shm")
    return success


def initialize_usage(path, policy):
    """Create a new usage store at ``path`` for ``policy``.

    Creation is exclusive: an existing path is left untouched and the
    call is refused. Parent directories are never created.
    """
    path_string = _path_string(path)
    expected = _policy_values(policy)
    uri = None
    if path_string is not None:
        uri = _sqlite_uri(path_string)
    if path_string is None or expected is None or uri is None:
        raise ValueError(_INVALID)
    if not _initialize_attempt(path_string, uri, expected):
        raise UsageDenied(_UNAVAILABLE)


class UsageLedger:
    """Existing-store-only access to one durable usage account."""

    __slots__ = ("_expected", "_uri")

    def __init__(self, path, policy):
        path_string = _path_string(path)
        expected = _policy_values(policy)
        uri = None
        if path_string is not None:
            uri = _sqlite_uri(path_string)
        if path_string is None or expected is None or uri is None:
            raise ValueError(_INVALID)
        self._expected = expected
        self._uri = uri

    def reserve(self):
        code = _reserve_attempt(self._uri, self._expected)
        if code is not None:
            raise UsageDenied(code)

    def snapshot(self):
        snapshot, code = _snapshot_attempt(self._uri, self._expected)
        if code is not None:
            raise UsageDenied(code)
        return snapshot


class BudgetedProvider:
    """Reserve one durable attempt, then delegate at most once."""

    __slots__ = ("_delegate", "_ledger")

    def __init__(self, delegate, ledger):
        try:
            reserve = getattr(ledger, "reserve", None)
        except Exception:
            reserve = None
        if not callable(delegate) or not callable(reserve):
            raise ValueError(_INVALID)
        self._delegate = delegate
        self._ledger = ledger

    def __call__(self, request, *, context):
        if not isinstance(context, ProviderCallContext):
            raise ValueError(_INVALID)
        context.remaining()
        self._ledger.reserve()
        context.remaining()
        return self._delegate(request, context=context)
