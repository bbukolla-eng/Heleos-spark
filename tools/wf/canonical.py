"""Deterministic JSON bytes, so a digest identifies a value rather than how it was written.

Every digest this repository records is a SHA-256 over bytes: a run identity, a ledger
chain, a content-addressed object. That is only evidence if two writers of the same value
cannot produce different bytes, and if a reader cannot admit a document the writer would
refuse. This module fixes both halves.

The encoding follows RFC 8785, the JSON Canonicalization Scheme, over a deliberately
narrow value domain: None, bool, int, str, list, and dict with string keys. Floats are
refused everywhere, because float formatting is the classic source of digest drift and a
quantity that must survive a hash belongs in a string or an integer.

Two divergences from RFC 8785, both deliberate and both stricter:

- Integers outside the range a IEEE 754 double represents exactly are refused. A digest an
  independent implementation cannot reproduce is not evidence, and most JSON tooling
  silently corrupts integers beyond 2**53.
- Lone surrogates are refused by name here rather than raising from the encoder, so the
  error names the problem instead of surfacing as a UnicodeEncodeError from a lower layer.

No Unicode normalisation is performed. Composed and decomposed text are different strings,
different keys, and different documents. Callers that need normalisation normalise first.

Standard library only.
"""
import hashlib
import json

MAX_DEPTH = 64
MAX_SAFE_INT = 2**53 - 1
MIN_SAFE_INT = -(2**53 - 1)
SEPARATORS = (",", ":")
DIGEST_ALGORITHM = "sha256"
DIGEST_PATTERN_LENGTH = 64
_HEX = frozenset("0123456789abcdef")


class CanonicalJsonError(Exception):
    """A value or document falls outside the domain this module will hash.

    Deliberately not a ValueError: a caller catching ValueError around json parsing should
    not silently swallow an admissibility refusal, which is a policy failure rather than a
    syntax one.
    """


def _refuse(what):
    raise CanonicalJsonError(what)


def _check_int(value):
    if value > MAX_SAFE_INT or value < MIN_SAFE_INT:
        _refuse(
            f"integer outside the exactly representable range "
            f"{MIN_SAFE_INT} to {MAX_SAFE_INT}: {value}"
        )
    return value


def _check_str(value):
    for character in value:
        if 0xD800 <= ord(character) <= 0xDFFF:
            _refuse("string contains a lone surrogate, which has no UTF-8 encoding")
    return value


def _utf16_key(key):
    """RFC 8785 orders object keys by UTF-16 code unit, which is not Python code point order.

    The two disagree above the basic multilingual plane, so json.dumps(sort_keys=True) is
    wrong for any document with an astral-plane key and is never used here.
    """
    return key.encode("utf-16-be", "surrogatepass")


def prepare(value, _depth=0):
    """Return an equal value with every mapping rebuilt in canonical key order.

    Refuses anything outside the admitted domain, naming the offending type rather than the
    offending value, so an error message can never leak the content it refused.
    """
    if _depth > MAX_DEPTH:
        _refuse(f"nesting deeper than {MAX_DEPTH}")
    if value is None or isinstance(value, bool):
        return value  # bool first: bool is an int subclass
    if isinstance(value, int):
        return _check_int(value)
    if isinstance(value, float):
        _refuse("float is never admitted, use an integer or a string")
    if isinstance(value, str):
        return _check_str(value)
    if isinstance(value, list):
        return [prepare(item, _depth + 1) for item in value]
    if isinstance(value, dict):
        for key in value:
            if not isinstance(key, str):
                _refuse(f"object key is {type(key).__name__}, only str is admitted")
            _check_str(key)
        return {key: prepare(value[key], _depth + 1) for key in sorted(value, key=_utf16_key)}
    _refuse(f"{type(value).__name__} is not an admitted JSON type")


def canonical_bytes(value):
    """The one UTF-8 encoding of an admitted value. No trailing newline, no byte below 0x20.

    Every argument below is load-bearing. separators must be explicit, because the default
    when indent is None is (", ", ": "). ensure_ascii must be False so text is literal UTF-8.
    sort_keys must be False because prepare has already applied the correct ordering. No
    default hook may ever be passed: it would silently coerce an unadmitted object and
    destroy the domain guarantee that makes the digest meaningful.
    """
    text = json.dumps(
        prepare(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=SEPARATORS,
        sort_keys=False,
        indent=None,
        skipkeys=False,
    )
    return text.encode("utf-8", "strict")


def canonical_line(value):
    """The canonical bytes plus exactly one newline, the only sanctioned JSON Lines framing.

    Safe because the encoding itself contains no byte below 0x20, so the newline is
    unambiguously a record separator.
    """
    return canonical_bytes(value) + b"\n"


def _reject_constant(name):
    _refuse(f"{name} is not admitted")


def _reject_float(literal):
    _refuse(f"float literal is not admitted: {literal}")


def _pairs_to_dict(pairs):
    """json keeps the last of a duplicate key. A ledger a reader silently rewrites is not one."""
    seen = {}
    for key, item in pairs:
        if key in seen:
            _refuse(f"duplicate object key: {key!r}")
        seen[key] = item
    return seen


def _parse_int(literal):
    return _check_int(int(literal))


def parse_canonical(data):
    """Parse bytes under the same rules the writer enforces.

    A reader that admits what the writer refuses would defeat the exercise: a duplicate key
    in a committed ledger line would pass validation while changing the value the digest was
    taken over. Input must be UTF-8 with no byte order mark. The input need not already be
    canonical; this admits a value, it does not assert the bytes were minimal.
    """
    if isinstance(data, str):
        _refuse("parse bytes, not str, so the encoding is checked rather than assumed")
    if data.startswith(b"\xef\xbb\xbf"):
        _refuse("byte order mark is not admitted")
    try:
        text = data.decode("utf-8", "strict")
    except UnicodeDecodeError as error:
        raise CanonicalJsonError(f"input is not valid UTF-8: {error.reason}") from error
    try:
        value = json.loads(
            text,
            object_pairs_hook=_pairs_to_dict,
            parse_float=_reject_float,
            parse_int=_parse_int,
            parse_constant=_reject_constant,
        )
    except CanonicalJsonError:
        raise
    except ValueError as error:
        raise CanonicalJsonError(f"input is not valid JSON: {error}") from error
    return prepare(value)


def sha256_hex(data):
    """Lowercase SHA-256 hex of exact bytes."""
    return hashlib.sha256(data).hexdigest()


def json_digest(value):
    """Lowercase SHA-256 hex over the canonical encoding of an admitted value."""
    return sha256_hex(canonical_bytes(value))


def is_digest(value):
    """True only for a 64-character lowercase hex string, the one shape a digest field takes."""
    return (
        isinstance(value, str)
        and len(value) == DIGEST_PATTERN_LENGTH
        and not set(value) - _HEX
    )
