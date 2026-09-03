"""The canonical encoding is the floor under every digest this repository records.

A run identity, a ledger chain, and a content-addressed object are all a SHA-256 over
bytes. If two writers of the same value can produce different bytes, those digests
identify the accident of how a value was written rather than the value, and a later
check cannot tell a reordered record from a tampered one. These tests pin the bytes.
"""
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.wf import canonical  # noqa: E402


class TheAdmittedDomain(unittest.TestCase):
    """Six kinds of value are admitted. Everything else is refused by name, not coerced."""

    def test_the_six_admitted_kinds_encode(self):
        for value in (None, True, False, 0, -1, 2**53 - 1, "", "text", [], [1, "a"], {}, {"k": 1}):
            with self.subTest(value=value):
                self.assertIsInstance(canonical.canonical_bytes(value), bytes)

    def test_a_float_is_refused_even_when_it_is_integral(self):
        for value in (1.0, -0.0, 0.5, 1e308):
            with self.subTest(value=value):
                with self.assertRaises(canonical.CanonicalJsonError) as caught:
                    canonical.canonical_bytes(value)
                self.assertIn("float", str(caught.exception))

    def test_nan_and_infinity_are_refused(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                self.assertRaises(canonical.CanonicalJsonError, canonical.canonical_bytes, value)

    def test_an_unsupported_type_is_refused_by_type_name_not_by_value(self):
        with self.assertRaises(canonical.CanonicalJsonError) as caught:
            canonical.canonical_bytes({"k": b"secret-bytes"})
        self.assertIn("bytes", str(caught.exception))
        self.assertNotIn("secret-bytes", str(caught.exception))

    def test_a_non_string_key_is_refused_rather_than_dropped(self):
        with self.assertRaises(canonical.CanonicalJsonError):
            canonical.canonical_bytes({1: "a"})

    def test_a_tuple_is_refused_rather_than_silently_becoming_a_list(self):
        with self.assertRaises(canonical.CanonicalJsonError):
            canonical.canonical_bytes({"k": (1, 2)})

    def test_a_bool_encodes_as_a_bool_not_as_an_integer(self):
        self.assertEqual(canonical.canonical_bytes({"k": True}), b'{"k":true}')

    def test_an_integer_beyond_the_safe_range_is_refused(self):
        """A digest nobody else can reproduce is not evidence."""
        for value in (2**53, -(2**53), 2**64):
            with self.subTest(value=value):
                self.assertRaises(canonical.CanonicalJsonError, canonical.canonical_bytes, value)
        self.assertEqual(canonical.canonical_bytes(2**53 - 1), b"9007199254740991")

    def test_nesting_beyond_the_depth_limit_is_refused_rather_than_recursing(self):
        deep = value = []
        for _ in range(canonical.MAX_DEPTH + 2):
            inner = []
            value.append(inner)
            value = inner
        self.assertRaises(canonical.CanonicalJsonError, canonical.canonical_bytes, deep)


class TheBytes(unittest.TestCase):
    """Exactly one byte string per admitted value."""

    def test_separators_are_compact(self):
        self.assertEqual(canonical.canonical_bytes({"a": 1, "b": [1, 2]}), b'{"a":1,"b":[1,2]}')

    def test_non_ascii_is_literal_utf8_not_escaped(self):
        self.assertEqual(canonical.canonical_bytes("é"), "\"é\"".encode("utf-8"))

    def test_control_characters_use_the_short_forms_and_lowercase_hex(self):
        self.assertEqual(canonical.canonical_bytes("\n\t\x01"), b'"\\n\\t\\u0001"')

    def test_the_forward_slash_is_not_escaped(self):
        self.assertEqual(canonical.canonical_bytes("a/b"), b'"a/b"')

    def test_no_byte_below_space_appears_in_the_output(self):
        out = canonical.canonical_bytes({"a": "line\nbreak\ttab", "b": ["\r"]})
        self.assertFalse([byte for byte in out if byte < 0x20])

    def test_the_encoding_never_ends_with_a_newline(self):
        self.assertFalse(canonical.canonical_bytes({"a": 1}).endswith(b"\n"))

    def test_the_line_helper_appends_exactly_one_newline(self):
        line = canonical.canonical_line({"a": 1})
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1)

    def test_array_order_is_the_callers_order_and_is_never_sorted(self):
        self.assertEqual(canonical.canonical_bytes([3, 1, 2]), b"[3,1,2]")

    def test_keys_sort_by_utf16_code_unit_not_by_code_point(self):
        """The two orderings disagree above the basic plane, and RFC 8785 fixes UTF-16.

        json.dumps(sort_keys=True) sorts by code point and gets this backwards, which is
        why this module never uses it.
        """
        encoded = canonical.canonical_bytes({"＀": 1, "\U0001f600": 2}).decode("utf-8")
        self.assertLess(encoded.index("\U0001f600"), encoded.index("＀"))

    def test_a_lone_surrogate_is_refused_with_this_modules_error(self):
        with self.assertRaises(canonical.CanonicalJsonError):
            canonical.canonical_bytes("\ud800")

    def test_equal_values_written_differently_produce_identical_bytes(self):
        self.assertEqual(
            canonical.canonical_bytes({"b": 2, "a": 1}),
            canonical.canonical_bytes({"a": 1, "b": 2}),
        )

    def test_encoding_is_a_fixed_point(self):
        """Parse canonical bytes, re-encode, and the bytes must not move."""
        once = canonical.canonical_bytes({"z": [1, {"y": "é"}], "a": None})
        self.assertEqual(canonical.canonical_bytes(canonical.parse_canonical(once)), once)

    def test_composed_and_decomposed_text_are_different_documents(self):
        """No normalisation is performed, and that is a choice rather than an oversight."""
        self.assertNotEqual(canonical.canonical_bytes("é"), canonical.canonical_bytes("é"))


class TheStrictReader(unittest.TestCase):
    """A reader that accepts what the writer refuses would defeat the whole exercise."""

    def test_a_duplicate_key_is_refused_rather_than_keeping_the_last(self):
        with self.assertRaises(canonical.CanonicalJsonError):
            canonical.parse_canonical(b'{"a":1,"a":2}')
        self.assertEqual(json.loads('{"a":1,"a":2}'), {"a": 2})  # what the standard reader does

    def test_a_float_literal_is_refused_on_read(self):
        self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, b'{"a":1.5}')

    def test_the_json_constants_are_refused_on_read(self):
        for text in (b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}'):
            with self.subTest(text=text):
                self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, text)

    def test_an_oversized_integer_literal_is_refused_on_read(self):
        self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, b'{"a":9007199254740992}')

    def test_a_byte_order_mark_is_a_failure_not_a_stripped_prefix(self):
        self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, b"\xef\xbb\xbf{}")

    def test_invalid_utf8_is_refused(self):
        self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, b'{"a":"\xff"}')

    def test_non_canonical_but_valid_json_still_parses(self):
        """The reader admits a value; it does not require the input to already be canonical."""
        self.assertEqual(canonical.parse_canonical(b'{ "b" : 2, "a" : 1 }'), {"a": 1, "b": 2})


class TheDigest(unittest.TestCase):
    def test_the_digest_is_lowercase_hex_of_the_canonical_bytes(self):
        import hashlib

        value = {"b": 2, "a": [1, None, True]}
        expected = hashlib.sha256(canonical.canonical_bytes(value)).hexdigest()
        self.assertEqual(canonical.json_digest(value), expected)
        self.assertRegex(canonical.json_digest(value), r"^[0-9a-f]{64}$")

    def test_equal_values_written_differently_have_the_same_digest(self):
        self.assertEqual(canonical.json_digest({"a": 1, "b": 2}), canonical.json_digest({"b": 2, "a": 1}))

    def test_is_digest_accepts_only_lowercase_64_hex(self):
        self.assertTrue(canonical.is_digest("a" * 64))
        for bad in ("A" * 64, "a" * 63, "a" * 65, "g" * 64, "", None, 1):
            with self.subTest(bad=bad):
                self.assertFalse(canonical.is_digest(bad))


class TheLedgerOnMainWouldNowBeCheckable(unittest.TestCase):
    """tools/egress/record.py canonicalises by hand and tools/ci/checks.py reads it loosely.

    This is not a hypothetical: a duplicate key in a committed ledger line passes the
    validator on main today. The reader here refuses it.
    """

    def test_a_duplicate_key_in_a_ledger_line_is_caught_here_but_not_by_json_loads(self):
        line = b'{"provider":"anthropic","provider":"someone-else"}'
        self.assertEqual(json.loads(line)["provider"], "someone-else")
        self.assertRaises(canonical.CanonicalJsonError, canonical.parse_canonical, line)


if __name__ == "__main__":
    unittest.main()
