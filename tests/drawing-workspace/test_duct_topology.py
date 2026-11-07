"""Independent fixtures for the explicit path-pair topology helper.

Every expectation below is written by hand from the assignment contract: the
canonical form, the row states and the counts are stated literally in each
test rather than recomputed with the module's own logic. The negative cases
check that an omitted pair never becomes a negative claim and that an
indeterminate correspondence never produces a score.

These fixtures establish no representative-project accuracy, no HVAC tolerance
and no acceptance threshold. They exercise the named structural cases only.
"""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

PATH = Path(__file__).resolve().parents[2] / "scripts/duct_topology.py"
spec = importlib.util.spec_from_file_location("duct_topology_test", PATH)
topology = importlib.util.module_from_spec(spec)
spec.loader.exec_module(topology)

TRUTH_OBJECTS = ["t1", "t2", "t3", "t4"]
PREDICTION_OBJECTS = ["p1", "p2", "p3", "p4"]
IDENTITY = [["t1", "p1"], ["t2", "p2"], ["t3", "p3"], ["t4", "p4"]]


class Fancy(str):
    """A str subclass is not an exact string for this boundary."""


def assertion(identifier, first, second, relation, evidence=("e1",)):
    return {
        "id": identifier,
        "members": [first, second],
        "relation": relation,
        "evidence_ids": list(evidence),
    }


def distinct_pairs(count, objects):
    """The first ``count`` distinct undirected pairs of ``objects`` in order."""
    pairs = []
    for left in range(len(objects)):
        for right in range(left + 1, len(objects)):
            pairs.append((objects[left], objects[right]))
            if len(pairs) == count:
                return pairs
    raise AssertionError("the fixture asked for more pairs than the objects allow")


def compare(truth, prediction, assignment=IDENTITY, state="definite", ambiguous=False):
    return topology.compare_assertions(truth, prediction, copy.deepcopy(assignment), state, ambiguous)


def row(truth_id, members, expected, prediction_id, prediction_members, observed, state):
    return {
        "truth_id": truth_id,
        "truth_members": members,
        "expected": expected,
        "prediction_id": prediction_id,
        "prediction_members": prediction_members,
        "observed": observed,
        "state": state,
    }


class NormalizeTest(unittest.TestCase):
    def normalize(self, assertions, objects=None, evidence=None, allow_uncertain=True):
        return topology.normalize_assertions(
            assertions,
            list(TRUTH_OBJECTS) if objects is None else objects,
            ["e1", "e2", "e3"] if evidence is None else evidence,
            allow_uncertain,
        )

    def rejects(self, assertions, code="topology_invalid_assertions", **kwargs):
        with self.assertRaises(topology.TopologyError) as caught:
            self.normalize(assertions, **kwargs)
        self.assertEqual(caught.exception.code, code)
        self.assertTrue(caught.exception.message)
        return caught.exception

    def test_canonical_order_and_shape(self):
        given = [
            assertion("b", "t4", "t3", "not_connected", ["e3", "e1"]),
            assertion("a", "t2", "t1", "connected", ["e2"]),
        ]
        self.assertEqual(self.normalize(given), [
            {"id": "a", "members": ["t1", "t2"], "relation": "connected", "evidence_ids": ["e2"]},
            {"id": "b", "members": ["t3", "t4"], "relation": "not_connected", "evidence_ids": ["e1", "e3"]},
        ])

    def test_permutation_invariance(self):
        first = self.normalize([
            assertion("a", "t1", "t2", "connected", ["e1", "e2"]),
            assertion("b", "t3", "t4", "uncertain", ["e2"]),
        ])
        second = self.normalize([
            assertion("b", "t4", "t3", "uncertain", ["e2"]),
            assertion("a", "t2", "t1", "connected", ["e2", "e1"]),
        ])
        self.assertEqual(first, second)

    def test_inputs_are_not_mutated_and_result_is_independent(self):
        given = [assertion("a", "t2", "t1", "connected", ["e2", "e1"])]
        objects = list(TRUTH_OBJECTS)
        evidence = ["e1", "e2", "e3"]
        untouched = copy.deepcopy(given)
        result = topology.normalize_assertions(given, objects, evidence)
        self.assertEqual(given, untouched)
        self.assertEqual(objects, TRUTH_OBJECTS)
        self.assertEqual(evidence, ["e1", "e2", "e3"])
        result[0]["members"].append("t3")
        result[0]["relation"] = "uncertain"
        self.assertEqual(given, untouched)
        self.assertEqual(topology.normalize_assertions(given, objects, evidence),
                         [{"id": "a", "members": ["t1", "t2"], "relation": "connected",
                           "evidence_ids": ["e1", "e2"]}])

    def test_uncertain_allowed_and_forbidden(self):
        given = [assertion("a", "t1", "t2", "uncertain")]
        self.assertEqual(self.normalize(given)[0]["relation"], "uncertain")
        self.rejects(given, allow_uncertain=False)

    def test_allow_uncertain_must_be_a_bool(self):
        with self.assertRaises(topology.TopologyError) as caught:
            self.normalize([], allow_uncertain=1)
        self.assertEqual(caught.exception.code, "topology_invalid_options")

    def test_unicode_identifiers(self):
        objects = ["Kanal-Ø", "パス-1", "kanál"]
        given = [
            assertion("bøjning", "パス-1", "Kanal-Ø", "connected", ["bevis-Ω"]),
            assertion("aftræk", "kanál", "パス-1", "not_connected", ["bevis-Ω"]),
        ]
        result = self.normalize(given, objects=objects, evidence=["bevis-Ω"])
        self.assertEqual([entry["id"] for entry in result], sorted(["bøjning", "aftræk"]))
        self.assertEqual(result[0]["members"], sorted(["kanál", "パス-1"]))
        self.assertEqual(result[1]["members"], sorted(["Kanal-Ø", "パス-1"]))

    def test_identifier_rejections(self):
        for bad in ["", " ", "t1 ", " t1", "t1\n", "\tt1", "pa\ud800th", "x" * 161, 1, True, None,
                    Fancy("t1"), ["t1"]]:
            self.rejects([assertion("a", bad, "t2", "connected")])
            self.rejects([{"id": bad, "members": ["t1", "t2"], "relation": "connected",
                           "evidence_ids": ["e1"]}])
            self.rejects([assertion("a", "t1", "t2", "connected", [bad])])

    def test_identifier_length_bounds(self):
        longest = "u" * topology.MAX_ID_LENGTH
        result = self.normalize([assertion(longest, "t1", "t2", "connected", [longest])],
                                evidence=[longest])
        self.assertEqual(result[0]["id"], longest)
        self.rejects([assertion("u" * (topology.MAX_ID_LENGTH + 1), "t1", "t2", "connected")])

    def test_self_pair_rejected(self):
        self.rejects([assertion("a", "t1", "t1", "connected")])

    def test_member_count_rejected(self):
        for members in [[], ["t1"], ["t1", "t2", "t3"], ("t1", "t2"), "t1t2"]:
            self.rejects([{"id": "a", "members": members, "relation": "connected",
                           "evidence_ids": ["e1"]}])

    def test_duplicate_assertion_identifier_rejected(self):
        self.rejects([assertion("a", "t1", "t2", "connected"),
                      assertion("a", "t3", "t4", "connected")])

    def test_duplicate_and_conflicting_pairs_rejected(self):
        self.rejects([assertion("a", "t1", "t2", "connected"),
                      assertion("b", "t2", "t1", "connected")])
        self.rejects([assertion("a", "t1", "t2", "connected"),
                      assertion("b", "t2", "t1", "not_connected")])
        self.rejects([assertion("a", "t1", "t2", "connected"),
                      assertion("b", "t2", "t1", "uncertain")])

    def test_dangling_references_rejected(self):
        self.rejects([assertion("a", "t1", "t9", "connected")])
        self.rejects([assertion("a", "t9", "t1", "connected")])
        self.rejects([assertion("a", "t1", "t2", "connected", ["e9"])])
        self.rejects([assertion("a", "t1", "t2", "connected", ["e1", "e9"])])

    def test_evidence_rejections(self):
        self.rejects([assertion("a", "t1", "t2", "connected", [])])
        self.rejects([assertion("a", "t1", "t2", "connected", ["e1", "e1"])])
        self.rejects([{"id": "a", "members": ["t1", "t2"], "relation": "connected",
                       "evidence_ids": ("e1",)}])
        self.rejects([{"id": "a", "members": ["t1", "t2"], "relation": "connected",
                       "evidence_ids": "e1"}])

    def test_relation_rejections(self):
        for relation in ["Connected", "linked", "", True, None, 1, Fancy("connected")]:
            self.rejects([{"id": "a", "members": ["t1", "t2"], "relation": relation,
                           "evidence_ids": ["e1"]}])

    def test_field_and_container_rejections(self):
        exact = assertion("a", "t1", "t2", "connected")
        extra = dict(exact, note="visual")
        self.rejects([extra])
        for field in sorted(topology.ASSERTION_FIELDS):
            missing = dict(exact)
            del missing[field]
            self.rejects([missing])
        for item in ["a", ("a",), None, 3, True, [exact]]:
            self.rejects([item])
        for container in [(), "abc", None, {"a": exact}, True]:
            self.rejects(container)

    def test_known_identifier_list_rejections(self):
        self.rejects([], code="topology_invalid_objects", objects=("t1", "t2"))
        self.rejects([], code="topology_invalid_objects", objects=["t1", "t1"])
        self.rejects([], code="topology_invalid_objects", objects=["t1", ""])
        self.rejects([], code="topology_invalid_evidence", evidence=("e1",))
        self.rejects([], code="topology_invalid_evidence", evidence=["e1", "e1"])
        self.rejects([], code="topology_invalid_evidence", evidence=[2])

    def test_upper_bounds_and_one_over(self):
        objects = ["o%03d" % index for index in range(topology.MAX_OBJECT_IDS)]
        evidence = ["e%04d" % index for index in range(topology.MAX_EVIDENCE_IDS)]
        pairs = distinct_pairs(topology.MAX_ASSERTIONS, objects)
        full = [assertion("a%04d" % index, left, right, "connected", ["e0000"])
                for index, (left, right) in enumerate(pairs)]
        result = topology.normalize_assertions(full, objects, evidence)
        self.assertEqual(len(result), topology.MAX_ASSERTIONS)

        over_pairs = distinct_pairs(topology.MAX_ASSERTIONS + 1, objects)
        over = [assertion("a%04d" % index, left, right, "connected", ["e0000"])
                for index, (left, right) in enumerate(over_pairs)]
        self.rejects(over, objects=objects, evidence=evidence)
        self.rejects([], code="topology_invalid_objects",
                     objects=objects + ["o250"], evidence=evidence)
        self.rejects([], code="topology_invalid_evidence",
                     objects=objects, evidence=evidence + ["e2000"])

    def test_evidence_citation_bounds(self):
        evidence = ["e%04d" % index for index in range(topology.MAX_ASSERTION_EVIDENCE + 1)]
        cited = evidence[:topology.MAX_ASSERTION_EVIDENCE]
        result = self.normalize([assertion("a", "t1", "t2", "connected", cited)], evidence=evidence)
        self.assertEqual(result[0]["evidence_ids"], sorted(cited))
        self.rejects([assertion("a", "t1", "t2", "connected", evidence)], evidence=evidence)


class CompareTest(unittest.TestCase):
    def test_correct_and_incorrect_relations(self):
        truth = [assertion("a", "t1", "t2", "connected"),
                 assertion("b", "t3", "t4", "not_connected")]
        prediction = [assertion("q1", "p1", "p2", "connected"),
                      assertion("q2", "p3", "p4", "connected")]
        result = compare(truth, prediction)
        self.assertEqual(result["state"], "evaluated")
        self.assertEqual(result["scope"], "explicit_same_sheet_path_pairs")
        self.assertEqual(result["truth_count"], 2)
        self.assertEqual((result["correct"], result["incorrect"]), (1, 1))
        self.assertEqual((result["unresolved"], result["unmatched_geometry"]), (0, 0))
        self.assertEqual(result["known_pair_accuracy"], 0.5)
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["unscored_prediction_ids"], [])
        self.assertEqual(result["rows"], [
            row("a", ["t1", "t2"], "connected", "q1", ["p1", "p2"], "connected", "correct"),
            row("b", ["t3", "t4"], "not_connected", "q2", ["p3", "p4"], "connected", "incorrect"),
        ])

    def test_negative_truth_confirmed(self):
        result = compare([assertion("a", "t1", "t2", "not_connected")],
                         [assertion("q1", "p1", "p2", "not_connected")])
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["rows"][0]["state"], "correct")
        self.assertEqual(result["rows"][0]["observed"], "not_connected")

    def test_omission_is_not_an_explicit_negative(self):
        """An absent prediction stays unknown for both truth relations."""
        for expected in ("connected", "not_connected"):
            result = compare([assertion("a", "t1", "t2", expected)],
                             [assertion("q2", "p3", "p4", "connected")])
            self.assertEqual((result["correct"], result["incorrect"]), (0, 0))
            self.assertEqual(result["unresolved"], 1)
            self.assertEqual(result["known_pair_accuracy"], 0.0)
            self.assertEqual(result["coverage"], 0.0)
            self.assertEqual(result["rows"], [
                row("a", ["t1", "t2"], expected, None, ["p1", "p2"], None, "unresolved")])
            self.assertEqual(result["unscored_prediction_ids"], ["q2"])

    def test_uncertain_prediction_is_unresolved_but_retained(self):
        result = compare([assertion("a", "t1", "t2", "connected")],
                         [assertion("q1", "p2", "p1", "uncertain")])
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(result["rows"], [
            row("a", ["t1", "t2"], "connected", "q1", ["p1", "p2"], "uncertain", "unresolved")])
        self.assertEqual(result["unscored_prediction_ids"], [])

    def test_partial_truth_leaves_other_predictions_unscored(self):
        truth = [assertion("a", "t1", "t2", "connected")]
        prediction = [assertion("q3", "p3", "p4", "connected"),
                      assertion("q1", "p1", "p2", "connected"),
                      assertion("q2", "p2", "p3", "not_connected")]
        result = compare(truth, prediction)
        self.assertEqual(result["truth_count"], 1)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["known_pair_accuracy"], 1.0)
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["unscored_prediction_ids"], ["q2", "q3"])

    def test_no_truth_leaves_ratios_undefined(self):
        prediction = [assertion("q1", "p1", "p2", "connected")]
        result = compare([], prediction)
        self.assertEqual(result["state"], "evaluated")
        self.assertEqual(result["truth_count"], 0)
        self.assertEqual(result["rows"], [])
        self.assertIsNone(result["known_pair_accuracy"])
        self.assertIsNone(result["coverage"])
        self.assertEqual(result["unscored_prediction_ids"], ["q1"])

    def test_no_predictions_at_all(self):
        result = compare([assertion("a", "t1", "t2", "connected")], [])
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(result["unscored_prediction_ids"], [])
        self.assertIsNone(result["rows"][0]["prediction_id"])

    def test_unmatched_geometry(self):
        truth = [assertion("a", "t1", "t2", "connected"),
                 assertion("b", "t2", "t3", "connected")]
        prediction = [assertion("q1", "p1", "p2", "connected")]
        result = compare(truth, prediction, assignment=[["t1", "p1"], ["t2", "p2"]])
        self.assertEqual(result["unmatched_geometry"], 1)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["known_pair_accuracy"], 0.5)
        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["rows"][1],
                         row("b", ["t2", "t3"], "connected", None, None, None, "unmatched_geometry"))

    def test_empty_assignment_matches_nothing(self):
        result = compare([assertion("a", "t1", "t2", "connected")],
                         [assertion("q1", "p1", "p2", "connected")], assignment=[])
        self.assertEqual(result["state"], "evaluated")
        self.assertEqual(result["unmatched_geometry"], 1)
        self.assertEqual(result["known_pair_accuracy"], 0.0)
        self.assertEqual(result["unscored_prediction_ids"], ["q1"])

    def test_indeterminate_and_ambiguous_correspondence(self):
        truth = [assertion("a", "t1", "t2", "connected"),
                 assertion("b", "t3", "t4", "not_connected")]
        prediction = [assertion("q1", "p1", "p2", "connected")]
        for state, ambiguous in (("indeterminate", False), ("definite", True),
                                 ("indeterminate", True)):
            result = compare(truth, prediction, state=state, ambiguous=ambiguous)
            self.assertEqual(result["state"], "indeterminate")
            self.assertEqual(result["truth_count"], 2)
            self.assertEqual([result[name] for name in topology.COUNTED_STATES], [0, 0, 0, 0])
            self.assertIsNone(result["known_pair_accuracy"])
            self.assertIsNone(result["coverage"])
            self.assertEqual(result["unscored_prediction_ids"], ["q1"])
            self.assertEqual(result["rows"], [
                row("a", ["t1", "t2"], "connected", None, None, None, "indeterminate"),
                row("b", ["t3", "t4"], "not_connected", None, None, None, "indeterminate"),
            ])

    def test_permutation_invariance_and_immutability(self):
        truth = [assertion("b", "t3", "t4", "not_connected"),
                 assertion("a", "t1", "t2", "connected")]
        prediction = [assertion("q2", "p4", "p3", "not_connected"),
                      assertion("q1", "p2", "p1", "connected")]
        assignment = [["t4", "p4"], ["t1", "p1"], ["t3", "p3"], ["t2", "p2"]]
        untouched = copy.deepcopy((truth, prediction, assignment))
        first = topology.compare_assertions(truth, prediction, assignment, "definite", False)
        self.assertEqual((truth, prediction, assignment), untouched)
        second = compare(list(reversed(truth)), list(reversed(prediction)), IDENTITY)
        self.assertEqual(first, second)
        self.assertEqual(first["correct"], 2)
        self.assertEqual([entry["truth_id"] for entry in first["rows"]], ["a", "b"])

    def test_result_is_json_compatible(self):
        result = compare([assertion("a", "t1", "t2", "connected")],
                         [assertion("q1", "p1", "p2", "uncertain")])
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_truth_forbids_uncertain(self):
        with self.assertRaises(topology.TopologyError) as caught:
            compare([assertion("a", "t1", "t2", "uncertain")], [])
        self.assertEqual(caught.exception.code, "topology_invalid_assertions")
        self.assertIn("truth[0]", caught.exception.message)

    def test_structure_is_revalidated_without_external_identifiers(self):
        """Both lists are checked here, using only the identifiers they contain."""
        cases = [
            ([assertion("a", "t1", "t1", "connected")], []),
            ([assertion("a", "t1", "t2", "connected"), assertion("a", "t3", "t4", "connected")], []),
            ([assertion("a", "t1", "t2", "connected"), assertion("b", "t2", "t1", "not_connected")], []),
            ([assertion("a", "t1", "t2", "connected", [])], []),
            ([{"id": "a", "members": ["t1", "t2"], "relation": "linked", "evidence_ids": ["e1"]}], []),
            ([dict(assertion("a", "t1", "t2", "connected"), note="x")], []),
            ([], [assertion("q1", "p1", "p1", "connected")]),
            ([], [assertion("q1", "p1", "p2", "connected"), assertion("q1", "p3", "p4", "connected")]),
            ([], [assertion("q1", "p1", "p2", "connected"), assertion("q2", "p2", "p1", "uncertain")]),
            ([], "not a list"),
            ("not a list", []),
        ]
        for truth, prediction in cases:
            with self.assertRaises(topology.TopologyError) as caught:
                compare(truth, prediction)
            self.assertEqual(caught.exception.code, "topology_invalid_assertions")

    def test_derived_object_bound(self):
        objects = ["o%03d" % index for index in range(topology.MAX_OBJECT_IDS + 2)]
        allowed = [assertion("q%03d" % index, objects[2 * index], objects[2 * index + 1], "connected")
                   for index in range(topology.MAX_OBJECT_IDS // 2)]
        result = compare([], allowed, assignment=[])
        self.assertEqual(len(result["unscored_prediction_ids"]), topology.MAX_OBJECT_IDS // 2)
        over = allowed + [assertion("q999", objects[-2], objects[-1], "connected")]
        with self.assertRaises(topology.TopologyError) as caught:
            compare([], over, assignment=[])
        self.assertEqual(caught.exception.code, "topology_invalid_assertions")

    def test_assignment_rejections(self):
        truth = [assertion("a", "t1", "t2", "connected")]
        prediction = [assertion("q1", "p1", "p2", "connected")]
        cases = [
            [["t1", "p1"], ["t1", "p2"]],
            [["t1", "p1"], ["t2", "p1"]],
            [["t1", "p1", "extra"]],
            [["t1"]],
            [[]],
            [("t1", "p1")],
            ["t1p1"],
            [["t1", 2]],
            [[True, "p1"]],
            [["t1", ""]],
            [[" t1", "p1"]],
            [["t1", "x" * 161]],
            [None],
            (),
            "assignment",
        ]
        for assignment in cases:
            with self.assertRaises(topology.TopologyError) as caught:
                topology.compare_assertions(truth, prediction, assignment, "definite", False)
            self.assertEqual(caught.exception.code, "topology_invalid_assignment")

    def test_assignment_bounds(self):
        full = [["t%03d" % index, "p%03d" % index]
                for index in range(topology.MAX_ASSIGNMENT_PAIRS)]
        result = compare([], [], assignment=full)
        self.assertEqual(result["state"], "evaluated")
        over = full + [["t250", "p250"]]
        with self.assertRaises(topology.TopologyError) as caught:
            compare([], [], assignment=over)
        self.assertEqual(caught.exception.code, "topology_invalid_assignment")

    def test_option_rejections(self):
        truth = [assertion("a", "t1", "t2", "connected")]
        for state, ambiguous in (("Definite", False), ("", False), (None, False), (True, False),
                                 (Fancy("definite"), False), ("definite", 0), ("definite", None),
                                 ("definite", "no")):
            with self.assertRaises(topology.TopologyError) as caught:
                topology.compare_assertions(truth, [], IDENTITY, state, ambiguous)
            self.assertEqual(caught.exception.code, "topology_invalid_options")

    def test_unicode_and_normalized_input_round_trip(self):
        objects = ["Kanál-1", "kanal-2"]
        truth = topology.normalize_assertions(
            [assertion("påstand", "kanal-2", "Kanál-1", "connected", ["bevis"])],
            objects, ["bevis"], allow_uncertain=False)
        prediction = topology.normalize_assertions(
            [assertion("forslag", "Ø2", "Ø1", "connected", ["bild"])],
            ["Ø1", "Ø2"], ["bild"])
        result = compare(truth, prediction,
                         assignment=[["Kanál-1", "Ø1"], ["kanal-2", "Ø2"]])
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["rows"][0]["truth_members"], sorted(objects))
        self.assertEqual(result["rows"][0]["prediction_members"], ["Ø1", "Ø2"])


if __name__ == "__main__":
    unittest.main()
