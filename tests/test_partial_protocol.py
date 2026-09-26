"""部分求和协议测试：独立枚举对拍、非连续顺序、分区绑定与公开点篡改。"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    MultilinearPoly,
    P,
    ProtocolError,
    Term,
    TranscriptFormatError,
    prove_partial,
    verify_partial,
)
from tests.reference import (  # noqa: E402
    ref_eval,
    ref_partial_point,
    ref_partial_round,
    ref_partial_sum,
)


def make_poly(n, terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


class PartialProofVsReferenceTests(unittest.TestCase):
    def test_non_contiguous_explicit_order_matches_independent_sum(self):
        spec = (
            5,
            [
                (5, ()),
                (2, (0,)),
                (3, (1,)),
                (4, (0, 3)),
                (6, (3, 4)),
                (7, (1, 4)),
            ],
        )
        poly = make_poly(*spec)
        sum_variables = (3, 0)
        retained_point = {1: 17, 2: 42, 4: 9}
        challenges = (60, 25)

        transcript = prove_partial(
            poly, sum_variables, retained_point, challenges
        )
        self.assertEqual(
            transcript.initial_claim,
            ref_partial_sum(spec, sum_variables, retained_point),
        )
        self.assertEqual(transcript.sum_variables, sum_variables)
        self.assertEqual(transcript.retained_variables, (1, 2, 4))
        self.assertEqual(transcript.retained_point, (17, 42, 9))
        self.assertEqual([msg.variable for msg in transcript.rounds], [3, 0])

        previous = transcript.initial_claim
        for i, msg in enumerate(transcript.rounds):
            a0, a1 = ref_partial_round(
                spec, sum_variables, retained_point, challenges[:i]
            )
            self.assertEqual(msg.coeffs[0], a0)
            if len(msg.coeffs) == 2:
                self.assertEqual(msg.coeffs[1], a1)
            else:
                self.assertEqual(a1, 0)
            self.assertEqual(msg.input_claim, previous)
            previous = (a0 + a1 * challenges[i]) % P
        self.assertEqual(transcript.final_claim, previous)

        expected_point = ref_partial_point(
            spec, sum_variables, retained_point, challenges
        )
        result = verify_partial(
            poly,
            transcript,
            sum_variables=sum_variables,
            retained_point=retained_point,
        )
        self.assertTrue(result.accepted, result.reason)
        self.assertEqual(result.direct_evaluation, ref_eval(spec, expected_point))
        self.assertEqual(result.rounds_checked, 2)

    def test_absent_summed_variable_has_degree_zero_round(self):
        poly = make_poly(4, [(2, (0,)), (3, (1, 3)), (5, ())])
        transcript = prove_partial(
            poly, (2, 3), {0: 8, 1: 9}, (11, 12)
        )
        self.assertEqual(transcript.rounds[0].variable, 2)
        self.assertEqual(len(transcript.rounds[0].coeffs), 1)
        self.assertTrue(verify_partial(poly, transcript).accepted)

    def test_degree_bound_derived_after_public_point_fixes_variable(self):
        # 原电路中 X0 次数为 1，但公开点 X1=0 固定后，关于 X0 的轮多项式退化为常量。
        poly = make_poly(2, [(1, (0, 1)), (2, (1,))])
        transcript = prove_partial(poly, (0,), {1: 0}, (7,))
        self.assertEqual(len(transcript.rounds[0].coeffs), 1)
        self.assertTrue(verify_partial(poly, transcript).accepted)
        bad = replace(
            transcript,
            rounds=(
                replace(
                    transcript.rounds[0],
                    coeffs=(transcript.rounds[0].coeffs[0], 1),
                ),
            ),
        )
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify_partial(poly, bad)
        self.assertIn("次数", str(ctx.exception))

    def test_empty_sum_set_is_direct_public_point_check(self):
        spec = (3, [(4, ()), (2, (0,)), (3, (1, 2))])
        poly = make_poly(*spec)
        retained_point = {0: 5, 1: 6, 2: 7}
        transcript = prove_partial(poly, (), retained_point, ())
        expected = ref_eval(spec, (5, 6, 7))
        self.assertEqual(transcript.rounds, ())
        self.assertEqual(transcript.challenges, ())
        self.assertEqual(transcript.initial_claim, expected)
        self.assertEqual(transcript.final_claim, expected)
        result = verify_partial(poly, transcript)
        self.assertTrue(result.accepted)
        self.assertEqual(result.rounds_checked, 0)
        self.assertEqual(result.direct_evaluation, expected)

        bad = replace(transcript, final_claim=(expected + 1) % P)
        rejected = verify_partial(poly, bad)
        self.assertFalse(rejected.accepted)
        self.assertEqual(rejected.failed_round, 0)


class PartialStatementBindingTests(unittest.TestCase):
    def setUp(self):
        self.spec = (
            4,
            [(1, ()), (2, (0,)), (3, (1,)), (4, (0, 2)), (5, (2, 3))],
        )
        self.poly = make_poly(*self.spec)
        self.sum_variables = (2, 0)
        self.retained_point = {1: 8, 3: 9}
        self.transcript = prove_partial(
            self.poly, self.sum_variables, self.retained_point, (10, 11)
        )

    def test_wrong_partition_cannot_reuse_same_proof(self):
        with self.assertRaises(ProtocolError) as ctx:
            verify_partial(
                self.poly,
                self.transcript,
                sum_variables=(0, 2),
                retained_point=self.retained_point,
            )
        self.assertIn("不能复用", str(ctx.exception))

        moved_point = {0: 8, 3: 9}
        with self.assertRaises(ProtocolError):
            verify_partial(
                self.poly,
                self.transcript,
                sum_variables=(1, 2),
                retained_point=moved_point,
            )

    def test_tampered_public_point_is_rejected(self):
        bad = replace(
            self.transcript,
            retained_point=(
                (self.transcript.retained_point[0] + 1) % P,
                self.transcript.retained_point[1],
            ),
        )
        result = verify_partial(self.poly, bad)
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, len(bad.rounds))
        self.assertNotEqual(result.final_claim, result.direct_evaluation)

    def test_tampered_statement_metadata_is_structure_error(self):
        bad_order = replace(self.transcript, sum_variables=(0, 2))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify_partial(self.poly, bad_order)
        self.assertIn("variable", str(ctx.exception))

        bad_retained = replace(self.transcript, retained_variables=(3, 1))
        with self.assertRaises(TranscriptFormatError):
            verify_partial(self.poly, bad_retained)

    def test_incomplete_or_overlapping_partition_rejected_at_prove(self):
        with self.assertRaises(ProtocolError):
            prove_partial(self.poly, (0,), {1: 2}, (3,))
        with self.assertRaises(ProtocolError):
            prove_partial(self.poly, (0, 1), {1: 2, 2: 3, 3: 4}, (5, 6))
        with self.assertRaises(ProtocolError):
            prove_partial(self.poly, (0, 0), {1: 2, 2: 3, 3: 4}, (5, 6))

    def test_unordered_sum_collection_rejected(self):
        with self.assertRaises(ProtocolError):
            prove_partial(
                self.poly,
                {0, 2},
                {1: 2, 3: 4},
                (5, 6),
            )

    def test_false_partial_claim_rejected_at_prove(self):
        true_sum = ref_partial_sum(
            self.spec, self.sum_variables, self.retained_point
        )
        with self.assertRaises(ProtocolError):
            prove_partial(
                self.poly,
                self.sum_variables,
                self.retained_point,
                (10, 11),
                claimed_sum=(true_sum + 1) % P,
            )


if __name__ == "__main__":
    unittest.main()
