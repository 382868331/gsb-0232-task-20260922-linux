"""sum-check 协议测试：诚实证明对拍独立参考、篡改/非法次数/域外数/空变量。"""

from __future__ import annotations

import os
import random
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    MAX_VARS,
    MultilinearPoly,
    P,
    Term,
    Transcript,
    TranscriptFormatError,
    prove,
    verify,
)
from sumcheck.errors import BudgetExhausted, PolynomialError, ProtocolError  # noqa: E402
from tests.reference import ref_expected_transcript_values  # noqa: E402

FIXED_CHALLENGES = (17, 42, 3, 60, 91, 25, 8, 50)


def make_poly(n, terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


def tamper(transcript, **round_changes):
    """返回替换某轮字段后的新转录（不可变对象的纯函数式篡改，无部分变更）。"""
    rounds = list(transcript.rounds)
    idx = round_changes.pop("index")
    rounds[idx] = replace(rounds[idx], **round_changes)
    return replace(transcript, rounds=tuple(rounds))


class HonestProofVsReferenceTests(unittest.TestCase):
    """prover 输出必须逐项等于独立参考预言机的期望值。"""

    def setUp(self):
        self.rng = random.Random(777)

    def _random_spec(self, n):
        subsets = [frozenset(j for j in range(n) if (mask >> j) & 1)
                   for mask in range(1 << n)]
        self.rng.shuffle(subsets)
        k = self.rng.randint(1, min(14, len(subsets)))
        terms = [(self.rng.randrange(P), tuple(sorted(s))) for s in subsets[:k]]
        return n, terms

    def test_transcript_matches_reference_all_sizes(self):
        for n in range(1, MAX_VARS + 1):
            for _ in range(5):
                spec = self._random_spec(n)
                poly = make_poly(n, spec[1])
                challenges = tuple(self.rng.randrange(P) for _ in range(n))
                expected = ref_expected_transcript_values(spec, challenges)
                tr = prove(poly, challenges)
                self.assertEqual(tr.initial_claim, expected["initial_claim"])
                self.assertEqual(tr.final_claim, expected["final_claim"])
                self.assertEqual(len(tr.rounds), n)
                for i, msg in enumerate(tr.rounds):
                    exp = expected["rounds"][i]
                    # 参考给出完整 (a0,a1)；prover 在实际次数 0 时可只发 (a0,)
                    self.assertEqual(msg.coeffs[0], exp["coeffs"][0])
                    if len(msg.coeffs) == 2:
                        self.assertEqual(msg.coeffs[1], exp["coeffs"][1])
                    else:
                        self.assertEqual(exp["coeffs"][1], 0)
                    self.assertEqual(msg.input_claim, exp["input_claim"])
                    self.assertEqual((msg.eval_at(0) + msg.eval_at(1)) % P,
                                     exp["g0_plus_g1"])
                    if i < n - 1:
                        self.assertEqual(msg.output_claim, exp["output_claim"])
                result = verify(poly, tr)
                self.assertTrue(result.accepted, result.reason)
                self.assertEqual(result.direct_evaluation,
                                 expected["direct_evaluation"])

    def test_degree_zero_round_uses_single_coefficient(self):
        # 变量 X_1 不出现在任何项中：第 1 轮（index=1）次数必须为 0
        poly = make_poly(3, [(2, (0,)), (3, (2,))])
        tr = prove(poly, (17, 42, 3))
        self.assertEqual(len(tr.rounds[1].coeffs), 1)
        self.assertEqual(len(tr.rounds[0].coeffs), 2)
        self.assertTrue(verify(poly, tr).accepted)

    def test_constant_only_poly(self):
        # 多项式 f=7（与变量无关）：每轮 g_i(t)=7*2^(剩余位数)，全为常量
        poly = make_poly(2, [(7, ())])
        tr = prove(poly, (11, 12))
        for msg in tr.rounds:
            self.assertEqual(len(msg.coeffs), 1)
        self.assertTrue(verify(poly, tr).accepted)
        self.assertEqual(tr.initial_claim, 7 * 4 % P)


class TamperAndRejectTests(unittest.TestCase):
    def setUp(self):
        # f = 3 + 2*x0 + x1 + x0*x2，3 变量
        self.poly = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))])
        self.ch = FIXED_CHALLENGES[:3]
        self.tr = prove(self.poly, self.ch)

    def test_tampered_intermediate_coefficient_is_rejected(self):
        """中间系数篡改：首个失败轮必须精确指向被篡改的轮。"""
        bad = tamper(self.tr, index=1,
                     coeffs=(self.tr.rounds[1].coeffs[0],
                             (self.tr.rounds[1].coeffs[1] + 1) % P))
        result = verify(self.poly, bad)
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, 1)
        self.assertIn("轮 1", result.reason)

    def test_tamper_round0_detected_at_round0(self):
        bad = tamper(self.tr, index=0,
                     coeffs=((self.tr.rounds[0].coeffs[0] + 5) % P,
                             self.tr.rounds[0].coeffs[1]))
        result = verify(self.poly, bad)
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, 0)

    def test_illegal_degree_rejected_structure_error(self):
        """非法次数（二次，3 个系数）必须作为结构错误抛出并定位轮次。"""
        bad = tamper(self.tr, index=1, coeffs=(1, 2, 3))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify(self.poly, bad)
        self.assertIn("rounds[1]", str(ctx.exception))
        self.assertIn("次数", str(ctx.exception))

    def test_degree_exceeds_that_variable_bound_absent_var(self):
        """变量 X_1 不出现在多项式中：该轮发来 1 次多项式必须被结构拒绝。"""
        poly = make_poly(3, [(2, (0,)), (3, (2,))])
        tr = prove(poly, (17, 42, 3))
        self.assertEqual(len(tr.rounds[1].coeffs), 1)
        a0 = tr.rounds[1].coeffs[0]
        # 即便塞零系数（表示上“声明次数 1”）也拒绝：转录必须采用紧规范形式
        bad = tamper(tr, index=1, coeffs=(a0, 0))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify(poly, bad)
        self.assertIn("rounds[1]", str(ctx.exception))
        self.assertIn("次数", str(ctx.exception))

    def test_degree_bound_after_killing_challenge(self):
        """f=x0*x1 且 r0=0：第二轮的真实 g1 为常量，发来一次式必须拒绝。"""
        poly = make_poly(2, [(1, (0, 1))])
        tr = prove(poly, (0, 7))
        self.assertEqual(len(tr.rounds[1].coeffs), 1)
        self.assertTrue(verify(poly, tr).accepted)
        bad = tamper(tr, index=1, coeffs=(tr.rounds[1].coeffs[0], 5))
        with self.assertRaises(TranscriptFormatError):
            verify(poly, bad)
        # r0 非零时该轮次数恢复为 1：同样长度 2 合法
        tr2 = prove(poly, (4, 7))
        self.assertEqual(len(tr2.rounds[1].coeffs), 2)
        self.assertTrue(verify(poly, tr2).accepted)

    def test_out_of_field_number_rejected(self):
        """域外数（101、-1）必须被拒绝并定位。"""
        bad = tamper(self.tr, index=0, coeffs=(101, self.tr.rounds[0].coeffs[1]))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify(self.poly, bad)
        self.assertIn("rounds[0].coeffs[0]", str(ctx.exception))

        bad2 = tamper(self.tr, index=2,
                      coeffs=(self.tr.rounds[2].coeffs[0], -1))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify(self.poly, bad2)
        self.assertIn("rounds[2].coeffs[1]", str(ctx.exception))

    def test_nan_and_bool_in_transcript_rejected(self):
        bad = tamper(self.tr, index=0, coeffs=(float("nan"),
                                               self.tr.rounds[0].coeffs[1]))
        with self.assertRaises(TranscriptFormatError):
            verify(self.poly, bad)
        bad_bool = replace(self.tr, initial_claim=True)
        with self.assertRaises(TranscriptFormatError):
            verify(self.poly, bad_bool)

    def test_empty_variables_rejected(self):
        """空变量：构造/prove/verify 三处都必须明确报错。"""
        poly0 = MultilinearPoly(0, [Term(9, ())])
        self.assertEqual(poly0.evaluate(()), 9)  # 0 变量常量多项式自身可求值
        with self.assertRaises(ProtocolError) as ctx:
            prove(poly0, ())
        self.assertIn("空变量", str(ctx.exception))
        dummy = Transcript(0, 9, (), (), 9)
        with self.assertRaises(ProtocolError):
            verify(poly0, dummy)
        with self.assertRaises(PolynomialError):
            MultilinearPoly(-1, [])

    def test_wrong_round_count_structure_error(self):
        bad = replace(self.tr, rounds=self.tr.rounds[:2])
        with self.assertRaises(TranscriptFormatError):
            verify(self.poly, bad)

    def test_challenge_length_mismatch(self):
        with self.assertRaises(ProtocolError):
            prove(self.poly, (17, 42))  # 少一个挑战
        with self.assertRaises(ProtocolError):
            prove(self.poly, (17, 42, 3, 99))

    def test_wrong_polynomial_fails_final_direct_check(self):
        """转录对一个多项式有效，对另一个多项式必须在最终直接点值检查失败。"""
        other = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (2, (0, 2))])
        result = verify(other, self.tr)
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, 3)  # num_vars 表示最终检查
        self.assertNotEqual(result.final_claim, result.direct_evaluation)

    def test_first_failure_reported_not_later_ones(self):
        # 同时破坏轮 0 与轮 2：必须报告轮 0
        bad = tamper(self.tr, index=2,
                     coeffs=(self.tr.rounds[2].coeffs[0], 99))
        bad = tamper(bad, index=0,
                     coeffs=(self.tr.rounds[0].coeffs[0],
                             (self.tr.rounds[0].coeffs[1] + 4) % P))
        result = verify(self.poly, bad)
        self.assertEqual(result.failed_round, 0)


class ClaimAndBudgetTests(unittest.TestCase):
    def test_false_claimed_sum_rejected_at_prove(self):
        poly = make_poly(2, [(1, (0,)), (1, (1,))])
        honest = prove(poly, (5, 6))
        with self.assertRaises(ProtocolError) as ctx:
            prove(poly, (5, 6), claimed_sum=(honest.initial_claim + 1) % P)
        self.assertIn("claimed_sum", str(ctx.exception))
        # 显式给出正确主张时成功
        self.assertTrue(verify(poly, prove(poly, (5, 6),
                                           claimed_sum=honest.initial_claim)).accepted)

    def test_budget_exhausted_separate_from_no_proof(self):
        poly = make_poly(4, [(1, (0, 1, 2, 3))])
        with self.assertRaises(BudgetExhausted):
            prove(poly, (1, 2, 3, 4), eval_budget=15)
        # 同一多项式、预算充足即可证明（预算耗尽≠无解）
        self.assertTrue(verify(poly, prove(poly, (1, 2, 3, 4),
                                           eval_budget=16)).accepted)

    def test_no_partial_state_on_failure(self):
        """prove 失败时不返回半成品转录；调用者状态保持不变。"""
        poly = make_poly(2, [(1, (0,))])
        tr_before = prove(poly, (5, 6))
        with self.assertRaises(Exception):
            prove(poly, (5, 6), eval_budget=1)
        after = prove(poly, (5, 6))
        self.assertEqual(tr_before, after)


class SupplementaryRoundTripTests(unittest.TestCase):
    """往返一致仅作补充：若干固定手工样例端到端接受。"""

    def test_handworked_example(self):
        # f(x0,x1) = 1 + x0*x1。立方体点：1,1,1,2 -> 和 5
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        tr = prove(poly, (2, 3))
        self.assertEqual(tr.initial_claim, 5)
        # g0(t) = Σ_b1 (1 + t*b1) = (1)+(1+t) = 2+t
        self.assertEqual(tr.rounds[0].coeffs, (2, 1))
        # 挑战 2 -> 4；g1(t)=1+2t，挑战 3 -> 7；直接求值 1+6=7
        self.assertEqual(tr.final_claim, 7)
        result = verify(poly, tr)
        self.assertTrue(result.accepted)
        self.assertEqual(result.direct_evaluation, 7)
        self.assertEqual(result.rounds_checked, 2)

    def test_transcript_is_immutable(self):
        poly = make_poly(1, [(1, (0,))])
        tr = prove(poly, (4,))
        with self.assertRaises(Exception):
            tr.initial_claim = 0  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
