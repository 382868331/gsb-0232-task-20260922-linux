"""流式验证状态机测试：顺序、重复、次数、拒绝终态、暂停恢复、与批量对拍。"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    ACCEPTED,
    AWAITING_CHALLENGE,
    AWAITING_POLY,
    REJECTED,
    MultilinearPoly,
    StreamStateError,
    StreamingVerifier,
    Term,
    TranscriptFormatError,
    VerificationFailed,
    prove,
    verify,
)
from sumcheck.errors import ProtocolError  # noqa: E402


def make_poly(n, terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


class ManualTwoVariableTests(unittest.TestCase):
    """手工两变量例子 f(x0,x1) = 1 + x0*x1，挑战 (2,3)，逐轮核对。

    手工推导（mod 101）：
      立方体点值 1,1,1,2，初始主张 C = 5
      轮 0: g0(t) = (1) + (1+t) = 2 + t，g0(0)+g0(1) = 2+3 = 5 = C
      挑战 r0 = 2，主张更新为 g0(2) = 4
      轮 1: f(2,t) = 1 + 2t，即 g1(t) = 1 + 2t，g1(0)+g1(1) = 1+3 = 4
      挑战 r1 = 3，最终主张 g1(3) = 7
      直接点值 f(2,3) = 1 + 2*3 = 7，接受。
    """

    def setUp(self):
        self.poly = make_poly(2, [(1, ()), (1, (0, 1))])

    def test_round_by_round_matches_manual_derivation(self):
        v = StreamingVerifier(self.poly, 5)
        self.assertEqual(v.state, AWAITING_POLY)
        self.assertEqual(v.next_round, 0)
        self.assertEqual(v.current_claim, 5)

        v.submit_round((2, 1))  # g0(t) = 2 + t
        self.assertEqual(v.state, AWAITING_CHALLENGE)
        v.submit_challenge(2)
        self.assertEqual(v.state, AWAITING_POLY)
        self.assertEqual(v.next_round, 1)
        self.assertEqual(v.current_claim, 4)

        v.submit_round((1, 2))  # g1(t) = 1 + 2t
        v.submit_challenge(3)
        self.assertEqual(v.state, ACCEPTED)
        self.assertEqual(v.current_claim, 7)

    def test_streaming_agrees_with_batch_verify(self):
        transcript = prove(self.poly, (2, 3))
        batch = verify(self.poly, transcript)
        self.assertTrue(batch.accepted)

        v = StreamingVerifier(self.poly, transcript.initial_claim)
        for i, msg in enumerate(transcript.rounds):
            v.submit_round(msg.coeffs)
            v.submit_challenge(transcript.challenges[i])
        self.assertEqual(v.state, ACCEPTED)
        self.assertEqual(v.current_claim, batch.final_claim)
        self.assertEqual(v.current_claim, batch.direct_evaluation)

    def test_honest_rounds_with_false_initial_claim_rejected_round0(self):
        v = StreamingVerifier(self.poly, 6)  # 正确主张是 5
        with self.assertRaises(VerificationFailed) as ctx:
            v.submit_round((2, 1))
        self.assertIn("轮 0", str(ctx.exception))
        self.assertEqual(v.state, REJECTED)


class OrderingAndDuplicationTests(unittest.TestCase):
    def setUp(self):
        self.poly = make_poly(2, [(1, ()), (1, (0, 1))])
        self.tr = prove(self.poly, (2, 3))

    def test_challenge_before_polynomial_rejected(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        with self.assertRaises(StreamStateError):
            v.submit_challenge(2)  # 顺序颠倒：尚未提交本轮多项式
        self.assertEqual(v.state, AWAITING_POLY)  # 状态机未被破坏
        # 纠正顺序后仍可正常完成
        v.submit_round(self.tr.rounds[0].coeffs)
        v.submit_challenge(2)
        v.submit_round(self.tr.rounds[1].coeffs)
        v.submit_challenge(3)
        self.assertEqual(v.state, ACCEPTED)

    def test_duplicate_round_rejected(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        v.submit_round(self.tr.rounds[0].coeffs)
        with self.assertRaises(StreamStateError):
            v.submit_round(self.tr.rounds[0].coeffs)  # 重复轮次：未提交挑战
        self.assertEqual(v.state, AWAITING_CHALLENGE)

    def test_duplicate_challenge_rejected(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        v.submit_round(self.tr.rounds[0].coeffs)
        v.submit_challenge(2)
        with self.assertRaises(StreamStateError):
            v.submit_challenge(2)  # 挑战已提交，应等下一轮多项式
        self.assertEqual(v.state, AWAITING_POLY)

    def test_submit_after_accepted_rejected(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        for i, msg in enumerate(self.tr.rounds):
            v.submit_round(msg.coeffs)
            v.submit_challenge(self.tr.challenges[i])
        self.assertEqual(v.state, ACCEPTED)
        with self.assertRaises(StreamStateError):
            v.submit_round((0, 0))
        with self.assertRaises(StreamStateError):
            v.submit_challenge(0)


class StructuralViolationTests(unittest.TestCase):
    def setUp(self):
        self.poly = make_poly(2, [(1, ()), (1, (0, 1))])
        self.tr = prove(self.poly, (2, 3))

    def test_degree_overflow_enters_terminal_reject(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        with self.assertRaises(TranscriptFormatError) as ctx:
            v.submit_round((1, 2, 3))  # 二次：超过多线性次数 1
        self.assertIn("次数", str(ctx.exception))
        self.assertEqual(v.state, REJECTED)

    def test_degree_bound_after_killing_challenge(self):
        # f = x0*x1，r0 = 0 杀死全部项：第 1 轮实际次数为 0
        poly = make_poly(2, [(1, (0, 1))])
        tr = prove(poly, (0, 7))
        v = StreamingVerifier(poly, tr.initial_claim)
        v.submit_round(tr.rounds[0].coeffs)
        v.submit_challenge(0)
        with self.assertRaises(TranscriptFormatError):
            v.submit_round((tr.rounds[1].coeffs[0], 5))  # 声明次数 1 超界
        self.assertEqual(v.state, REJECTED)

    def test_out_of_field_coefficient_not_masked_by_mod(self):
        # 101 与 0 在模 101 下相同，但输入必须拒绝而不是取模掩盖
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        with self.assertRaises(TranscriptFormatError) as ctx:
            v.submit_round((101, 1))
        self.assertIn("越界", str(ctx.exception))
        self.assertEqual(v.state, REJECTED)

        v2 = StreamingVerifier(self.poly, self.tr.initial_claim)
        with self.assertRaises(TranscriptFormatError):
            v2.submit_round((2, -1))
        self.assertEqual(v2.state, REJECTED)

    def test_bool_and_float_coefficients_rejected(self):
        for bad in (True, 1.5, float("nan")):
            v = StreamingVerifier(self.poly, self.tr.initial_claim)
            with self.assertRaises(TranscriptFormatError):
                v.submit_round((bad, 1))
            self.assertEqual(v.state, REJECTED)

    def test_invalid_challenge_leaves_state_unchanged(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        v.submit_round(self.tr.rounds[0].coeffs)
        with self.assertRaises(TranscriptFormatError):
            v.submit_challenge(101)  # 域外挑战
        self.assertEqual(v.state, AWAITING_CHALLENGE)  # 可修正重试
        v.submit_challenge(2)
        self.assertEqual(v.state, AWAITING_POLY)


class TerminalRejectTests(unittest.TestCase):
    def setUp(self):
        self.poly = make_poly(2, [(1, ()), (1, (0, 1))])
        self.tr = prove(self.poly, (2, 3))

    def test_sum_relation_failure_is_unrecoverable(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        with self.assertRaises(VerificationFailed):
            v.submit_round((3, 1))  # 3 + 4 = 7，不等于主张 5
        self.assertEqual(v.state, REJECTED)
        self.assertIn("轮 0", v.rejected_reason)
        # 已拒绝后继续送入：多项式与挑战都抛 StreamStateError
        with self.assertRaises(StreamStateError):
            v.submit_round(self.tr.rounds[0].coeffs)
        with self.assertRaises(StreamStateError):
            v.submit_challenge(2)
        self.assertEqual(v.state, REJECTED)

    def test_final_direct_evaluation_failure_is_terminal(self):
        # 各轮求和关系对 self.poly 的转录成立，但 verifier 持有另一个
        # 多项式：必须在最终直接点值检查失败，不能靠轮间关系宣布成功。
        other = make_poly(2, [(2, ()), (1, (0, 1))])  # f = 2 + x0*x1
        v = StreamingVerifier(other, self.tr.initial_claim)
        v.submit_round(self.tr.rounds[0].coeffs)
        v.submit_challenge(2)
        v.submit_round(self.tr.rounds[1].coeffs)
        with self.assertRaises(VerificationFailed) as ctx:
            v.submit_challenge(3)
        self.assertIn("直接点值", str(ctx.exception))
        self.assertEqual(v.state, REJECTED)
        with self.assertRaises(StreamStateError):
            v.submit_round((0,))


class PauseResumeTests(unittest.TestCase):
    def setUp(self):
        self.poly = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))])
        self.challenges = (17, 42, 3)
        self.tr = prove(self.poly, self.challenges)

    def _drive(self, v, upto_round):
        for i in range(upto_round):
            v.submit_round(self.tr.rounds[i].coeffs)
            v.submit_challenge(self.tr.challenges[i])

    def test_pause_and_resume_completes_like_uninterrupted(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        self._drive(v, 1)  # 完成第 0 轮后暂停
        state = v.export_state()
        self.assertEqual(state.next_round, 1)
        self.assertEqual(state.phase, AWAITING_POLY)

        resumed = StreamingVerifier.restore(self.poly, state)
        self.assertEqual(resumed.state, AWAITING_POLY)
        self.assertEqual(resumed.next_round, 1)
        self.assertEqual(resumed.current_claim, v.current_claim)
        for i in range(1, 3):
            resumed.submit_round(self.tr.rounds[i].coeffs)
            resumed.submit_challenge(self.tr.challenges[i])
        self.assertEqual(resumed.state, ACCEPTED)
        self.assertEqual(resumed.current_claim, self.tr.final_claim)

    def test_pause_resume_via_plain_dict(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        self._drive(v, 2)
        state_dict = v.export_state().to_dict()  # 可 JSON 序列化的纯 dict

        resumed = StreamingVerifier.restore(self.poly, state_dict)
        self.assertEqual(resumed.state, AWAITING_POLY)
        resumed.submit_round(self.tr.rounds[2].coeffs)
        resumed.submit_challenge(self.tr.challenges[2])
        self.assertEqual(resumed.state, ACCEPTED)

    def test_restore_rechecks_from_scratch(self):
        # 导出状态只含公开转录；篡改已提交系数，恢复时重放检查当场失败。
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        self._drive(v, 1)
        data = v.export_state().to_dict()
        data["rounds"][0] = [data["rounds"][0][0] + 1, data["rounds"][0][1]]
        with self.assertRaises((VerificationFailed, TranscriptFormatError)):
            StreamingVerifier.restore(self.poly, data)

    def test_restore_rejects_inconsistent_position(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        self._drive(v, 1)
        data = v.export_state().to_dict()
        data["next_round"] = 2  # 与 rounds 长度不符
        with self.assertRaises(ProtocolError):
            StreamingVerifier.restore(self.poly, data)

    def test_restore_completed_state_rechecks_final_point_value(self):
        v = StreamingVerifier(self.poly, self.tr.initial_claim)
        self._drive(v, 3)
        self.assertEqual(v.state, ACCEPTED)
        resumed = StreamingVerifier.restore(self.poly, v.export_state())
        self.assertEqual(resumed.state, ACCEPTED)
        # 换用另一个多项式恢复同一状态：最终点值检查必须重新失败
        other = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (2, (0, 2))])
        with self.assertRaises(VerificationFailed):
            StreamingVerifier.restore(other, v.export_state())


if __name__ == "__main__":
    unittest.main()
