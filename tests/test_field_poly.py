"""素域与多项式构造的输入契约测试。"""

import math
import unittest

from sumcheck import (
    FIELD_P,
    FieldValueError,
    PolynomialError,
    Term,
    MultilinearPolynomial,
    check_field_int,
    field_add,
    field_mul,
    field_sub,
)


class FieldContractTests(unittest.TestCase):
    def test_boundary_values_accepted(self):
        self.assertEqual(check_field_int(0), 0)
        self.assertEqual(check_field_int(FIELD_P - 1), FIELD_P - 1)

    def test_out_of_range_rejected(self):
        for bad in (-1, FIELD_P, 10_000):
            with self.subTest(bad=bad):
                with self.assertRaises(FieldValueError):
                    check_field_int(bad, what="x")

    def test_bool_rejected_even_though_int_subclass(self):
        with self.assertRaises(FieldValueError):
            check_field_int(True, what="flag")
        with self.assertRaises(FieldValueError):
            check_field_int(False, what="flag")

    def test_float_nan_infinity_rejected_with_named_message(self):
        for bad, name in ((float("nan"), "NaN"), (float("inf"), "Infinity"),
                          (float("-inf"), "Infinity"), (1.5, "GF")):
            with self.subTest(bad=bad):
                try:
                    check_field_int(bad, what="v")
                except FieldValueError as exc:
                    self.assertIn(name, str(exc))
                else:
                    self.fail("float must be rejected")

    def test_arithmetic_is_modular(self):
        self.assertEqual(field_add(60, 50), 9)
        self.assertEqual(field_sub(0, 1), 100)
        self.assertEqual(field_mul(20, 5), 100)


class TermTests(unittest.TestCase):
    def test_constant_term(self):
        t = Term(7, ())
        self.assertEqual(t.coeff, 7)
        self.assertEqual(t.variables, ())

    def test_variables_sorted_and_validated(self):
        self.assertEqual(Term(1, (2, 0, 1)).variables, (0, 1, 2))
        with self.assertRaises(PolynomialError):
            Term(1, (0, 0))
        with self.assertRaises(PolynomialError):
            Term(1, (-1,))
        with self.assertRaises(PolynomialError):
            Term(1, (True,))

    def test_coeff_contract(self):
        with self.assertRaises(FieldValueError):
            Term(101, (0,))
        with self.assertRaises(FieldValueError):
            Term(True, (0,))
        with self.assertRaises(FieldValueError):
            Term(float("nan"), (0,))


class PolynomialTests(unittest.TestCase):
    def test_basic_evaluation(self):
        poly = MultilinearPolynomial([Term(3, ()), Term(2, (0,)), Term(5, (1,)),
                                      Term(7, (0, 1))])
        # f(4,6) = 3 + 2*4 + 5*6 + 7*4*6 = 209 == 7 (mod 101)
        self.assertEqual(poly.evaluate((4, 6)), 209 % FIELD_P)
        self.assertEqual(poly.evaluate((0, 0)), 3)

    def test_num_vars_explicit_and_inferred(self):
        self.assertEqual(MultilinearPolynomial([Term(1, (2,))]).num_vars, 3)
        self.assertEqual(MultilinearPolynomial([Term(9, ())], num_vars=0).num_vars, 0)
        poly = MultilinearPolynomial([Term(2, (0,))], num_vars=3)
        self.assertEqual(poly.num_vars, 3)
        self.assertEqual(poly.degree_in(1), 0)
        self.assertEqual(poly.degree_in(0), 1)

    def test_reject_bad_structure_with_location(self):
        with self.assertRaises(PolynomialError):  # 重复支撑集
            MultilinearPolynomial([Term(1, (0, 1)), Term(2, (1, 0))])
        with self.assertRaises(PolynomialError):  # 零系数项
            MultilinearPolynomial([Term(0, (0,))])
        with self.assertRaises(PolynomialError):  # 下标越界
            MultilinearPolynomial([Term(1, (3,))], num_vars=2)
        with self.assertRaises(PolynomialError):  # 非 Term
            MultilinearPolynomial([(1, (0,))])
        with self.assertRaises(PolynomialError):  # 空列表无法推断 n
            MultilinearPolynomial([])

    def test_limits_enforced(self):
        with self.assertRaises(PolynomialError):
            MultilinearPolynomial([Term(1, (8,))])  # 第 9 个变量，超过 8
        with self.assertRaises(PolynomialError):
            MultilinearPolynomial([Term(1, (0,))], num_vars=9)
        terms = [Term(1, ())] + [Term(j, (0,)) for j in range(1, 101)]
        with self.assertRaises(PolynomialError):
            MultilinearPolynomial(terms)  # 101 项，超过 100

    def test_limit_boundary_accepted(self):
        terms = [Term(j + 1, (j,)) for j in range(8)]
        poly = MultilinearPolynomial(terms)
        self.assertEqual(poly.num_vars, 8)

        # 100 个互不相同的支撑（常数项 + 99 个非空布尔支撑）。
        supports = [tuple(k for k in range(8) if (mask >> k) & 1)
                    for mask in range(1, 256)][:99]
        terms100 = [Term(1, ())] + [Term(1, s) for s in supports]
        poly = MultilinearPolynomial(terms100)
        self.assertEqual(len(poly.terms), 100)

    def test_evaluate_rejects_bad_point(self):
        poly = MultilinearPolynomial([Term(1, (0,))])
        with self.assertRaises(PolynomialError):
            poly.evaluate((1, 2))  # 长度不符
        with self.assertRaises(FieldValueError):
            poly.evaluate((101,))
        with self.assertRaises(FieldValueError):
            poly.evaluate((math.inf,))
        with self.assertRaises(FieldValueError):
            poly.evaluate((False,))


if __name__ == "__main__":
    unittest.main()
