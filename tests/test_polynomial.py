"""Term / MultilinearPoly / F_p 数据结构与错误语义测试。"""

from __future__ import annotations

import math
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import MAX_TERMS, MAX_VARS, MultilinearPoly, P, Term
from sumcheck.errors import BudgetExhausted, PolynomialError
from tests.reference import ref_boolean_sum, ref_eval  # noqa: E402


def spec_to_poly(spec):
    """独立 spec -> 核心对象（仅做结构转换，期望值仍来自 reference）。"""
    n, terms = spec
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


class FieldValidationTests(unittest.TestCase):
    def test_reject_nan_infinity_float(self):
        for bad, label in ((float("nan"), "NaN"), (float("inf"), "Infinity"),
                          (float("-inf"), "Infinity")):
            with self.subTest(value=label):
                with self.assertRaises(ValueError) as ctx:
                    Term(bad, ())
                self.assertIn(label, str(ctx.exception))

    def test_reject_plain_float(self):
        with self.assertRaises(TypeError):
            Term(1.5, (0,))
        with self.assertRaises(TypeError):
            MultilinearPoly(1, [Term(1, ())]).evaluate([0.0])

    def test_reject_bool_everywhere(self):
        with self.assertRaises(TypeError):
            Term(True, ())
        with self.assertRaises(PolynomialError):
            MultilinearPoly(True, [])
        with self.assertRaises(PolynomialError):
            Term(1, (False,))
        with self.assertRaises(TypeError):
            MultilinearPoly(1, [Term(1, (0,))]).boolean_sum(eval_budget=True)

    def test_out_of_range_elements(self):
        with self.assertRaises(ValueError):
            Term(101, ())
        with self.assertRaises(ValueError):
            Term(-1, ())
        poly = MultilinearPoly(1, [Term(1, (0,))])
        with self.assertRaises(ValueError):
            poly.evaluate([101])

    def test_nan_message_is_locatable(self):
        try:
            Term(float("nan"), (2,))
        except ValueError as exc:
            self.assertIn("Term 系数", str(exc))
        else:
            self.fail("NaN 必须被拒绝")
        self.assertTrue(math.isnan(float("nan")))


class PolynomialStructureTests(unittest.TestCase):
    def test_limits(self):
        self.assertEqual((MAX_VARS, MAX_TERMS), (8, 100))
        with self.assertRaises(PolynomialError):
            MultilinearPoly(9, [])
        with self.assertRaises(PolynomialError):
            MultilinearPoly(8, [Term(1, (8,))])
        with self.assertRaises(PolynomialError):
            MultilinearPoly(1, [Term(1, ()) for _ in range(101)])

    def test_duplicate_terms_rejected_not_silently_merged(self):
        with self.assertRaises(PolynomialError) as ctx:
            MultilinearPoly(2, [Term(1, (0,)), Term(2, (0,))])
        self.assertIn("重复项", str(ctx.exception))

    def test_bad_term_type_and_index(self):
        with self.assertRaises(PolynomialError):
            MultilinearPoly(2, [(1, 2)])  # type: ignore[list-item]
        with self.assertRaises(PolynomialError):
            MultilinearPoly(2, [Term(1, (5,))])
        with self.assertRaises(TypeError):
            Term(1, 7)  # type: ignore[arg-type]

    def test_point_length_mismatch(self):
        poly = MultilinearPoly(2, [Term(1, (0, 1))])
        with self.assertRaises(PolynomialError):
            poly.evaluate([1])


class EvaluationVsReferenceTests(unittest.TestCase):
    """与独立参考对拍：点值、布尔立方体求和、次数、限制。"""

    def setUp(self):
        self.rng = random.Random(20260922)

    def _random_spec(self, n=None, max_terms=12):
        n = n if n is not None else self.rng.randint(1, MAX_VARS)
        subsets = [frozenset() ]
        for mask in range(1, 1 << n):
            subsets.append(frozenset(j for j in range(n) if (mask >> j) & 1))
        self.rng.shuffle(subsets)
        k = self.rng.randint(1, min(max_terms, len(subsets)))
        chosen = subsets[:k]
        terms = [(self.rng.randrange(P), tuple(sorted(s))) for s in chosen]
        return n, terms

    def test_evaluate_and_sum_random(self):
        for trial in range(60):
            spec = self._random_spec()
            n, _ = spec
            poly = spec_to_poly(spec)
            for _ in range(8):
                point = tuple(self.rng.randrange(P) for _ in range(n))
                self.assertEqual(poly.evaluate(point), ref_eval(spec, point))
            self.assertEqual(poly.boolean_sum(), ref_boolean_sum(spec))

    def test_boundary_sizes(self):
        spec = self._random_spec(n=MAX_VARS, max_terms=MAX_TERMS)
        poly = spec_to_poly(spec)
        self.assertEqual(poly.boolean_sum(), ref_boolean_sum(spec))

    def test_degree_reflects_presence(self):
        poly = MultilinearPoly(3, [Term(2, (0, 2)), Term(1, (2,))])
        self.assertEqual(poly.degree_of(0), 1)
        self.assertEqual(poly.degree_of(1), 0)
        self.assertEqual(poly.degree_of(2), 1)
        with self.assertRaises(PolynomialError):
            poly.degree_of(3)
        with self.assertRaises(PolynomialError):
            poly.degree_of(False)

    def test_zero_coefficient_terms_do_not_inflate_degree(self):
        poly = MultilinearPoly(2, [Term(0, (0, 1)), Term(3, (1,))])
        self.assertEqual(poly.degree_of(0), 0)
        self.assertEqual(poly.evaluate([5, 7]), 3 * 7 % P)

    def test_restricted_matches_reference(self):
        for trial in range(30):
            spec = self._random_spec(n=4)
            n, _ = spec
            poly = spec_to_poly(spec)
            fixes = {0: self.rng.randrange(1, P), 2: self.rng.randrange(P)}
            sub = poly.restricted(fixes)
            self.assertEqual(sub.num_vars, 2)
            # 在剩余变量（旧下标 1,3）的随机点上比较
            for _ in range(6):
                tail = [self.rng.randrange(P) for _ in range(2)]
                full = [0] * n
                full[0], full[2] = fixes[0], fixes[2]
                full[1], full[3] = tail
                self.assertEqual(sub.evaluate(tail), ref_eval(spec, tuple(full)))

    def test_budget_exhausted_distinct_from_false_claim(self):
        poly = MultilinearPoly(3, [Term(1, (0, 1, 2))])
        with self.assertRaises(BudgetExhausted) as ctx:
            poly.boolean_sum(eval_budget=7)
        self.assertIn("超过预算", str(ctx.exception))
        # 预算充足时正常：资源问题与命题真假无关
        self.assertEqual(poly.boolean_sum(eval_budget=8), 1)


if __name__ == "__main__":
    unittest.main()
