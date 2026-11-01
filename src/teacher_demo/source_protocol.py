"""Source-section proposal protocol.

Packaged with the runtime so the package is self-contained.
This module only mechanically
validates the *shape* of a source transcription proposal; it certifies
no transcription accuracy. Supplied text is never stripped, normalized,
or mutated; error messages never echo caller input.
"""
from __future__ import annotations

__all__ = ["SOURCE_SCHEMA", "validate_source_proposal"]

_NONBLANK_PATTERN = "\\S"

SOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sections", "page_continues", "review_notes"],
    "properties": {
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["section_id", "kind", "text", "review_reasons"],
                "properties": {
                    "section_id": {"type": "string", "pattern": _NONBLANK_PATTERN},
                    "kind": {
                        "type": "string",
                        "enum": ["title", "body", "vocabulary", "other"],
                    },
                    "text": {"type": "string", "pattern": _NONBLANK_PATTERN},
                    "review_reasons": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
            },
        },
        "page_continues": {"type": "boolean"},
        "review_notes": {"type": "array", "items": {"type": "string"}},
    },
}

_SOURCE_TOP_FIELDS = frozenset(("sections", "page_continues", "review_notes"))
_SECTION_FIELDS = frozenset(("section_id", "kind", "text", "review_reasons"))
_SECTION_KINDS = frozenset(("title", "body", "vocabulary", "other"))
_SOURCE_MALFORMED = "source proposal is malformed"


def _is_nonblank_str(value):
    return isinstance(value, str) and bool(value.strip())


def _is_str_list(value):
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def validate_source_proposal(proposal):
    """Return an accepted source proposal unchanged, else raise ValueError.

    Empty and malformed output are refused, never salvaged. The input
    is never mutated, stripped, or normalized.
    """
    if not isinstance(proposal, dict):
        raise ValueError(_SOURCE_MALFORMED)
    if set(proposal.keys()) != _SOURCE_TOP_FIELDS:
        raise ValueError(_SOURCE_MALFORMED)
    sections = proposal["sections"]
    if not isinstance(sections, list) or not sections:
        raise ValueError(_SOURCE_MALFORMED)
    if not isinstance(proposal["page_continues"], bool):
        raise ValueError(_SOURCE_MALFORMED)
    if not _is_str_list(proposal["review_notes"]):
        raise ValueError(_SOURCE_MALFORMED)
    seen_ids = set()
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError(_SOURCE_MALFORMED)
        if set(section.keys()) != _SECTION_FIELDS:
            raise ValueError(_SOURCE_MALFORMED)
        section_id = section["section_id"]
        if not _is_nonblank_str(section_id):
            raise ValueError(_SOURCE_MALFORMED)
        if section_id in seen_ids:
            raise ValueError(_SOURCE_MALFORMED)
        seen_ids.add(section_id)
        kind = section["kind"]
        if not isinstance(kind, str) or kind not in _SECTION_KINDS:
            raise ValueError(_SOURCE_MALFORMED)
        if not _is_nonblank_str(section["text"]):
            raise ValueError(_SOURCE_MALFORMED)
        if not _is_str_list(section["review_reasons"]):
            raise ValueError(_SOURCE_MALFORMED)
    return proposal
