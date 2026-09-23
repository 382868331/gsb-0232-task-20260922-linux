"""sum-check 协议测试。

期望值来自 tests/reference.py 的独立实现（不导入被测核心的求和/轮次逻辑），
被测库只负责产生证明与核验。
"""

import random
import unittest

from sumcheck import (
    FIELD_P,
    ChallengeError,
    FieldValueError,
    PolynomialError,
    Proof,
    ProofFormatError,
    MultilinearPolynomial,
    Term,
    prove,
    verify,
)

from reference import cube_sum, eval_poly, round_values


def lib_poly_from_ref(ref_terms, n):
    return MultilinearPolynomial(
        [Term(t["coeff"], t["vars"]) for t in ref_terms], num_vars=n
    )


# 确定性固定多项式集合：(变量数, 参考表示)。
FIXED_CASES = [
    (1, [{"coeff": 1, "vars": (0,)}]),               # f=x0, 立方和=1
    (2, [{"coeff": 3, "vars": ()},
         {"coeff": 2, "vars": (0,)},
         {"coeff": 5, "vars": (1,)},
         {"coeff": 7, "vars": (0, 1)}]),
    (3, [{"coeff": 11, "vars": (0, 2)},
         {"coeff": 13, "vars": (1,)},
         {"coeff": 17, "vars": (0, 1, 2)}]),
    (4, [{"coeff": 1, "vars": tuple(range(4))},
         {"coeff": 99, "vars": (3,)},
         {"coeff": 50, "vars": ()}]),
]

FIXED_CHALLENGES = {
    1: (7,),
    2: (3, 5),
    3: (2, 9, 4),
    4: (10, 20, 30, 40),
}


class HonestProofTests(unittest.TestCase):
    def test_claimed_sum_matches_independent_cube_enumeration(self):
        for n, ref_terms in FIXED_CASES:
            with self.subTest(n=n):
                poly = lib_poly_from_ref(ref_terms, n)
                output = prove(poly, FIXED_CHALLENGES[n])
                self.assertEqual(output.claimed_sum, cube_sum(ref_terms, n))

    def test_honest_proof_accepted_and_final_value_matches_reference(self):
        for n, ref_terms in FIXED_CASES:
            with self.subTest(n=n):
                poly = lib_poly_from_ref(ref_terms, n)
                rs = FIXED_CHALLENGES[n]
                output = prove(poly, rs)
                result = verify(poly, output.claimed_sum, rs, output.proof)
                self.assertTrue(result.accepted, msg=result.message)
                self.assertIsNone(result.failed_round)
                self.assertEqual(len(result.transcript), n)
                self.assertEqual(result.final_value, eval_poly(ref_terms, rs))

    def test_each_round_coefficients_match_independent_suffix_sums(self):
        n, ref_terms = 3, FIXED_CASES[2][1]
        poly = lib_poly_from_ref(ref_terms, n)
        rs = FIXED_CHALLENGES[3]
        output = prove(poly, rs)
        prefix = ()
        running_claim = cube_sum(ref_terms, n)
        for j, rec_args in enumerate(output.proof.rounds):
            c0, c1 = rec_args
            g0, g1 = round_values(ref_terms, n, prefix, j)
            self.assertEqual(c0, g0)                       # g(0)
            self.assertEqual((c0 + c1) % FIELD_P, g1)      # g(1)
            self.assertEqual((g0 + g1) % FIELD_P, running_claim)
            running_claim = (c0 + c1 * rs[j]) % FIELD_P
            prefix = prefix + (rs[j],)
        self.assertEqual(running_claim, eval_poly(ref_terms, rs))

    def test_transcript_round_records_chain(self):
        n, ref_terms = 2, FIXED_CASES[1][1]
        poly = lib_poly_from_ref(ref_terms, n)
        rs = FIXED_CHALLENGES[2]
        output = prove(poly, rs)
        result = verify(poly, output.claimed_sum, rs, output.proof)
        claim = output.claimed_sum
        for rec in result.transcript:
            self.assertEqual(rec.expected_claim, claim)
            self.assertEqual((rec.g_at_zero + rec.g_at_one) % FIELD_P, claim)
            self.assertEqual(
                rec.next_claim,
                (rec.coeffs[0] + rec.coeffs[1] * rec.challenge) % FIELD_P,
            )
            claim = rec.next_claim


class TamperTests(unittest.TestCase):
    def setUp(self):
        self.n = 3
        self.ref_terms = FIXED_CASES[2][1]
        self.poly = lib_poly_from_ref(self.ref_terms, self.n)
        self.rs = FIXED_CHALLENGES[3]
        self.output = prove(self.poly, self.rs)

    def _rounds(self):
        return [list(r) for r in self.output.proof.rounds]

    def test_middle_round_coefficient_tamper_fails_at_that_round(self):
        rounds = self._rounds()
        rounds[1][1] = (rounds[1][1] + 1) % FIELD_P  # 篡改第 2 轮一次项系数
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, 2)
        self.assertEqual(result.reason_code, "claim")
        self.assertEqual(len(result.transcript), 1)  # 转录停在失败轮之前

    def test_first_round_tamper(self):
        rounds = self._rounds()
        rounds[0][0] = (rounds[0][0] + 1) % FIELD_P
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertFalse(result.accepted)
        self.assertEqual((result.failed_round, result.reason_code), (1, "claim"))
        self.assertEqual(result.transcript, ())

    def test_wrong_claimed_sum_rejected_round_one(self):
        wrong = (self.output.claimed_sum + 1) % FIELD_P
        result = verify(self.poly, wrong, self.rs, self.output.proof)
        self.assertFalse(result.accepted)
        self.assertEqual((result.failed_round, result.reason_code), (1, "claim"))

    def test_illegal_degree_rejected(self):
        rounds = self._rounds()
        rounds[2] = [rounds[2][0], rounds[2][1], 0]  # 二次多项式
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_round, 3)
        self.assertEqual(result.reason_code, "degree")
        self.assertEqual(len(result.transcript), 2)

    def test_out_of_field_coefficient_rejected(self):
        rounds = self._rounds()
        rounds[0][1] = FIELD_P  # 101 在域外
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertFalse(result.accepted)
        self.assertEqual((result.failed_round, result.reason_code), (1, "field"))
        rounds2 = self._rounds()
        rounds2[1][0] = -3
        result2 = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds2))
        self.assertEqual((result2.failed_round, result2.reason_code), (2, "field"))

    def test_non_integer_coefficient_rejected_as_format(self):
        rounds = self._rounds()
        rounds[0][0] = 1.5
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason_code, "format")
        rounds2 = self._rounds()
        rounds2[0] = "01"  # 字符串不是系数序列
        result2 = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds2))
        self.assertEqual(result2.reason_code, "format")

    def test_first_failure_reported_when_multiple_rounds_bad(self):
        # 第 1 轮与第 3 轮都被篡改：必须报告首个失败轮 1。
        rounds = self._rounds()
        rounds[0][1] = (rounds[0][1] + 10) % FIELD_P
        rounds[2] = [1, 1, 1]
        result = verify(self.poly, self.output.claimed_sum, self.rs, Proof(rounds))
        self.assertEqual(result.failed_round, 1)

    def test_rounds_pass_but_final_check_fails(self):
        # 作弊证明：每轮发 [0, claim]，折叠恒等于上一主张，次数与值域合法，
        # 但终末 f(r_1) 与链上最后主张不同。
        n = 1
        ref_terms = [{"coeff": 1, "vars": ()}]  # f(x0)=1，立方和=2
        poly = lib_poly_from_ref(ref_terms, n)
        rs = (3,)
        claim = cube_sum(ref_terms, n)
        cheating = Proof([[0, claim]])  # g(0)+g(1)=0+claim=claim
        result = verify(poly, claim, rs, cheating)
        self.assertFalse(result.accepted)
        self.assertIsNone(result.failed_round)
        self.assertEqual(result.reason_code, "final")
        self.assertEqual(result.final_value, eval_poly(ref_terms, rs))
        self.assertEqual(len(result.transcript), 1)


class ZeroVariableTests(unittest.TestCase):
    def test_empty_variables_honest(self):
        poly = MultilinearPolynomial([Term(40, ())], num_vars=0)
        output = prove(poly, ())
        self.assertEqual(output.claimed_sum, 40)  # 立方体只有一个空点
        self.assertEqual(output.proof.rounds, ())
        result = verify(poly, 40, (), output.proof)
        self.assertTrue(result.accepted, msg=result.message)
        self.assertEqual(result.final_value, 40)

    def test_empty_variables_wrong_claim_rejected(self):
        poly = MultilinearPolynomial([Term(40, ())], num_vars=0)
        result = verify(poly, 41, (), Proof(()))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason_code, "final")
        self.assertIsNone(result.failed_round)
        self.assertEqual(result.final_value, 40)

    def test_empty_variables_challenge_length_must_be_zero(self):
        poly = MultilinearPolynomial([Term(40, ())], num_vars=0)
        with self.assertRaises(ChallengeError):
            prove(poly, (1,))


class ApiErrorTests(unittest.TestCase):
    def setUp(self):
        self.poly = MultilinearPolynomial([Term(1, (0,))])

    def test_challenge_length_mismatch(self):
        with self.assertRaises(ChallengeError):
            prove(self.poly, (1, 2))
        with self.assertRaises(ChallengeError):
            prove(self.poly, ())

    def test_challenge_value_contract(self):
        with self.assertRaises(FieldValueError):
            prove(self.poly, (101,))
        with self.assertRaises(FieldValueError):
            prove(self.poly, (-1,))
        with self.assertRaises(FieldValueError):
            prove(self.poly, (True,))
        with self.assertRaises(FieldValueError):
            prove(self.poly, (float("nan"),))

    def test_claimed_sum_contract(self):
        output = prove(self.poly, (2,))
        with self.assertRaises(FieldValueError):
            verify(self.poly, 101, (2,), output.proof)
        with self.assertRaises(FieldValueError):
            verify(self.poly, True, (2,), output.proof)

    def test_proof_round_count_mismatch_raises(self):
        output = prove(self.poly, (2,))
        with self.assertRaises(ProofFormatError):
            verify(self.poly, output.claimed_sum, (2,), Proof(()))
        with self.assertRaises(ProofFormatError):
            verify(self.poly, output.claimed_sum, (2,),
                   Proof([output.proof.rounds[0], (1, 0)]))

    def test_poly_type_checked(self):
        with self.assertRaises(PolynomialError):
            prove([Term(1, (0,))], (2,))


class SeededFuzzTests(unittest.TestCase):
    """固定随机种子的补充性质测试：诚实往返必须成功，主张等于独立枚举。"""

    def test_seeded_random_honest_runs(self):
        rng = random.Random(20260922)
        for _ in range(30):
            n = rng.randint(1, 5)
            supports = [tuple(k for k in range(n) if (mask >> k) & 1)
                        for mask in range(1, 1 << n)]
            rng.shuffle(supports)
            supports = supports[: rng.randint(1, min(20, len(supports)))]
            ref_terms = [{"coeff": rng.randint(1, 100), "vars": s} for s in supports]
            rs = tuple(rng.randint(0, 100) for _ in range(n))
            poly = lib_poly_from_ref(ref_terms, n)
            output = prove(poly, rs)
            self.assertEqual(output.claimed_sum, cube_sum(ref_terms, n))
            result = verify(poly, output.claimed_sum, rs, output.proof)
            self.assertTrue(result.accepted, msg=result.message)
            self.assertEqual(result.final_value, eval_poly(ref_terms, rs))


if __name__ == "__main__":
    unittest.main()
