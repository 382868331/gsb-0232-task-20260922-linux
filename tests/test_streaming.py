"""流式 sum-check 验证状态机测试。"""

from __future__ import annotations

import os
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    MultilinearPoly,
    StreamRound,
    StreamingVerifier,
    StreamingVerifierState,
    Term,
    prove,
    verify,
)


def make_poly(num_vars, spec):
    return MultilinearPoly(num_vars, [Term(coeff, variables) for coeff, variables in spec])


class StreamingVerifierTests(unittest.TestCase):
    def test_handworked_two_variables_round_by_round_and_batch_match(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)

        status = verifier.submit_polynomial(0, (2, 1))
        self.assertEqual(status.phase, "awaiting_challenge")
        self.assertIsNone(status.output_claim)

        status = verifier.submit_challenge(0, 2)
        self.assertEqual(status.phase, "awaiting_polynomial")
        self.assertEqual(status.output_claim, 4)
        self.assertEqual(status.next_round, 1)

        status = verifier.submit_polynomial(1, (1, 2))
        self.assertEqual(status.phase, "awaiting_challenge")
        status = verifier.submit_challenge(1, 3)
        self.assertTrue(status.accepted)
        self.assertEqual(status.output_claim, 7)
        self.assertEqual(status.direct_evaluation, 7)

        transcript = verifier.to_transcript()
        batch = verify(poly, transcript)
        self.assertTrue(batch.accepted)
        self.assertEqual(batch.final_claim, 7)
        self.assertEqual(batch.direct_evaluation, 7)

    def test_challenge_cannot_be_submitted_before_polynomial(self):
        poly = make_poly(1, [(1, (0,))])
        verifier = StreamingVerifier(poly, 1)
        status = verifier.submit_challenge(0, 4)
        self.assertTrue(status.rejected)
        self.assertIn("多项式", status.reason)

    def test_next_round_cannot_be_accepted_before_challenge(self):
        poly = make_poly(2, [(1, ())])
        verifier = StreamingVerifier(poly, 4)
        verifier.submit_polynomial(0, (2,))
        status = verifier.submit_polynomial(1, (1,))
        self.assertTrue(status.rejected)
        self.assertIn("挑战未提交", status.reason)

    def test_reversed_and_repeated_rounds_are_rejected(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)
        status = verifier.submit_polynomial(1, (1, 2))
        self.assertTrue(status.rejected)
        self.assertIn("轮次顺序错误", status.reason)

        status = verifier.submit_polynomial(0, (2, 1))
        self.assertTrue(status.rejected)
        self.assertEqual(status.phase, "rejected")

    def test_bad_polynomial_sum_enters_terminal_rejection(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)
        status = verifier.submit_polynomial(0, (3, 1))
        self.assertTrue(status.rejected)
        self.assertIn("主张不闭合", status.reason)

        status = verifier.submit_challenge(0, 2)
        self.assertTrue(status.rejected)
        self.assertIn("主张不闭合", status.reason)

    def test_degree_over_bound_and_noncanonical_coefficients_rejected(self):
        poly = make_poly(2, [(1, (0,))])
        verifier = StreamingVerifier(poly, 2)
        self.assertFalse(verifier.submit_polynomial(0, (0, 2)).accepted)
        verifier.submit_challenge(0, 2)
        status = verifier.submit_polynomial(1, (2, 0))
        self.assertTrue(status.rejected)
        self.assertIn("实际次数 0", status.reason)

        verifier = StreamingVerifier(poly, 2)
        status = verifier.submit_polynomial(0, (101, 1))
        self.assertTrue(status.rejected)
        self.assertIn("coeffs[0]", status.reason)

        verifier = StreamingVerifier(poly, 2)
        status = verifier.submit_polynomial(0, (-1, 1))
        self.assertTrue(status.rejected)
        self.assertIn("越界", status.reason)

        verifier = StreamingVerifier(poly, 2)
        status = verifier.submit_polynomial(0, (1, 2, 3))
        self.assertTrue(status.rejected)
        self.assertIn("次数", status.reason)

    def test_final_direct_evaluation_is_required(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)
        verifier.submit_polynomial(0, (2, 1))
        verifier.submit_challenge(0, 2)
        verifier.submit_polynomial(1, (2, 0))
        status = verifier.submit_challenge(1, 3)
        self.assertTrue(status.rejected)
        self.assertEqual(status.phase, "rejected")
        self.assertIn("最终直接点值检查失败", status.reason)

    def test_pause_and_resume_rechecks_submitted_rounds(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)
        verifier.submit_polynomial(0, (2, 1))

        paused_at_polynomial = verifier.export_state()
        restored = StreamingVerifier.restore(poly, paused_at_polynomial)
        self.assertEqual(restored.phase, "awaiting_challenge")
        self.assertEqual(restored.next_round, 0)
        restored.submit_challenge(0, 2)

        paused_after_challenge = restored.export_state()
        restored = StreamingVerifier.restore(poly, paused_after_challenge)
        self.assertEqual(restored.phase, "awaiting_polynomial")
        self.assertEqual(restored.next_round, 1)
        restored.submit_polynomial(1, (1, 2))
        status = restored.submit_challenge(1, 3)
        self.assertTrue(status.accepted)

    def test_resume_rejects_tampered_public_transcript(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier(poly, 5)
        verifier.submit_polynomial(0, (2, 1))
        verifier.submit_challenge(0, 2)
        state = verifier.export_state()
        tampered = replace(
            state,
            rounds=(replace(state.rounds[0], coeffs=(3, 1)),),
        )
        restored = StreamingVerifier.restore(poly, tampered)
        self.assertTrue(restored.is_rejected)
        self.assertIn("主张不闭合", restored.reason)

    def test_resume_rejects_missing_challenge_before_next_round(self):
        state = StreamingVerifierState(
            num_vars=2,
            initial_claim=5,
            rounds=(
                StreamRound(0, (2, 1), None),
                StreamRound(1, (1, 2), None),
            ),
            next_round=1,
        )
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        verifier = StreamingVerifier.restore(poly, state)
        self.assertTrue(verifier.is_rejected)
        self.assertIn("挑战未提交", verifier.reason)

    def test_accepted_round_trip_with_prover_transcript(self):
        poly = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))])
        challenges = (17, 42, 3)
        expected = prove(poly, challenges)
        verifier = StreamingVerifier(poly, expected.initial_claim)
        for i, message in enumerate(expected.rounds):
            verifier.submit_polynomial(i, message.coeffs)
            status = verifier.submit_challenge(i, challenges[i])
        self.assertTrue(status.accepted)
        self.assertEqual(verifier.to_transcript(), expected)


if __name__ == "__main__":
    unittest.main()
