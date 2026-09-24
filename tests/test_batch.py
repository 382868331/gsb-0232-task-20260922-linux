"""多实例批处理测试。

期望值来源：
- 布尔立方体求和、点值：tests/reference.py 独立参考（不 import 被测核心）；
- n=1 作弊转录等关键数值：手算并在注释中给出推导；
prove_batch/verify_batch 的往返一致仅作补充。
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    MAX_BATCH_INSTANCES,
    BatchError,
    BatchInstance,
    MultilinearPoly,
    P,
    RoundMessage,
    Term,
    Transcript,
    TranscriptFormatError,
    prove,
    prove_batch,
    verify,
    verify_batch,
)
from sumcheck.errors import BudgetExhausted  # noqa: E402
from tests.reference import ref_boolean_sum, ref_eval  # noqa: E402


def make_poly(n, terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


def spec_of(poly):
    return (poly.num_vars, [(t.coeff, tuple(sorted(t.variables))) for t in poly.terms])


def make_instance(iid, n, raw_terms, challenges, claimed_sum=None):
    return BatchInstance(iid, make_poly(n, raw_terms), tuple(challenges), claimed_sum)


class HonestBatchTests(unittest.TestCase):
    def setUp(self):
        self.rng = random.Random(20260924)

    def _random_spec(self, n):
        subsets = [frozenset(j for j in range(n) if (mask >> j) & 1)
                   for mask in range(1 << n)]
        self.rng.shuffle(subsets)
        k = self.rng.randint(1, min(12, len(subsets)))
        terms = [(self.rng.randrange(P), tuple(sorted(s))) for s in subsets[:k]]
        return n, terms

    def test_mixed_size_batch_matches_independent_reference(self):
        """一批不同变量数的实例，各自保留 n 与次数上界；主张/点值对拍独立参考。"""
        instances = []
        specs = {}
        for idx, n in enumerate([1, 2, 3, 4, 5, 7, 8]):
            n, terms = self._random_spec(n)
            ch = tuple(self.rng.randrange(P) for _ in range(n))
            iid = f"inst-n{n}-{idx}"
            instances.append(BatchInstance(iid, make_poly(n, terms), ch))
            specs[iid] = (n, terms, ch)

        report = prove_batch(instances)
        self.assertTrue(report.accepted, report.pretty())
        self.assertEqual(report.count, 7)
        self.assertEqual(report.accepted_count, 7)
        self.assertEqual(set(report.by_id()), set(specs))

        for r in report.results:
            n, terms, ch = specs[r.instance_id]
            spec = (n, terms)
            self.assertEqual(r.num_vars, n)
            # 初始主张必须等于独立参考的布尔立方体全枚举
            self.assertEqual(r.claimed_sum, ref_boolean_sum(spec))
            # 最终点值必须等于独立参考在挑战点的直接求值
            self.assertEqual(r.direct_evaluation, ref_eval(spec, tuple(ch)))
            # 批处理转录必须携带所属实例 ID
            self.assertEqual(r.transcript.instance_id, r.instance_id)

    def test_each_instance_keeps_own_round_count_and_degree_bound(self):
        """同批中 n 不同 ⇒ 轮数不同；缺变量的实例该轮次数为 0（互不串扰）。"""
        only_x0 = make_instance("a", 1, [(1, (0,))], (5,))
        x0_and_x2 = make_instance("b", 3, [(2, (0,)), (3, (2,))], (17, 42, 3))
        report = prove_batch([only_x0, x0_and_x2])
        tr_a, tr_b = report.results
        self.assertEqual(len(tr_a.transcript.rounds), 1)
        self.assertEqual(len(tr_b.transcript.rounds), 3)
        # 实例 b 中 X_1 不出现：第 1 轮必须是 0 次（紧规范 (a0,)）
        self.assertEqual(len(tr_b.transcript.rounds[1].coeffs), 1)
        self.assertEqual(len(tr_b.transcript.rounds[0].coeffs), 2)

    def test_handworked_two_instance_batch(self):
        """手算例子：f=1+x0*x1（立方体和 5）与 f=x0（和 1）。"""
        inst1 = make_instance("prod", 2, [(1, ()), (1, (0, 1))], (2, 3))
        inst2 = make_instance("lin", 1, [(1, (0,))], (5,))
        report = prove_batch([inst1, inst2])
        r1, r2 = report.by_id()["prod"], report.by_id()["lin"]
        self.assertTrue(report.accepted)
        # 1+x0*x1 在 (1,1),(1,0),(0,1),(0,0) 取值 2,1,1,1 ⇒ 和 5
        self.assertEqual(r1.claimed_sum, 5)
        self.assertEqual(r1.direct_evaluation, 7)   # 1+2*3
        self.assertEqual(r1.transcript.rounds[0].coeffs, (2, 1))
        # x0 在 0,1 取值 0,1 ⇒ 和 1；f(5)=5
        self.assertEqual(r2.claimed_sum, 1)
        self.assertEqual(r2.direct_evaluation, 5)

    def test_verify_round_trip_on_separate_call(self):
        """prove_batch 的转录可交给独立的 verify_batch 重新核验（模拟分开发送）。"""
        instances = [
            make_instance("one", 2, [(1, ()), (1, (0, 1))], (2, 3)),
            make_instance("two", 3, [(3, ()), (2, (0,)), (1, (0, 2))], (17, 42, 3)),
        ]
        produced = prove_batch(instances)
        mapping = produced.transcript_by_id()
        again = verify_batch(instances, mapping)
        self.assertTrue(again.accepted)
        self.assertEqual(again.count, 2)


class BatchFailureTests(unittest.TestCase):
    def setUp(self):
        self.inst1 = make_instance("alpha", 2, [(1, ()), (1, (0, 1))], (2, 3))
        self.inst2 = make_instance(
            "beta", 3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))], (17, 42, 3)
        )
        self.good = prove_batch([self.inst1, self.inst2])

    def test_one_tampered_instance_fails_whole_batch_but_others_reported(self):
        """篡改 beta 第 1 轮系数：整批失败，alpha 仍单独接受，beta 定位到轮 1。"""
        mapping = self.good.transcript_by_id()
        tr_beta = mapping["beta"]
        msg = tr_beta.rounds[1]
        tampered = replace(
            tr_beta,
            rounds=tuple(
                [replace(msg, coeffs=(msg.coeffs[0], (msg.coeffs[1] + 1) % P))
                 if i == 1 else m for i, m in enumerate(tr_beta.rounds)]
            ),
        )
        mapping["beta"] = tampered
        report = verify_batch([self.inst1, self.inst2], mapping)
        self.assertFalse(report.accepted)
        self.assertEqual(report.accepted_count, 1)
        self.assertTrue(report.by_id()["alpha"].accepted)
        beta = report.by_id()["beta"]
        self.assertFalse(beta.accepted)
        self.assertEqual(beta.failed_round, 1)

    def test_cross_instance_swap_is_rejected(self):
        """把 A 的转录交给 B（ID 已绑定）必须抛 BatchError，不能张冠李戴。"""
        mapping = self.good.transcript_by_id()
        swapped = {"alpha": mapping["beta"], "beta": mapping["alpha"]}
        with self.assertRaises(BatchError) as ctx:
            verify_batch([self.inst1, self.inst2], swapped)
        self.assertIn("实例 ID 绑定不符", str(ctx.exception))

    def test_unbound_single_instance_transcript_rejected(self):
        """单实例 prove 产出的转录（instance_id=None）不能混进批次。"""
        raw = prove(self.inst1.poly, self.inst1.challenges)
        self.assertIsNone(raw.instance_id)
        with self.assertRaises(BatchError) as ctx:
            verify_batch([self.inst1], {"alpha": raw})
        self.assertIn("未绑定实例 ID", str(ctx.exception))

    def test_missing_and_extra_transcript_ids(self):
        mapping = self.good.transcript_by_id()
        with self.assertRaises(BatchError) as ctx:
            verify_batch([self.inst1, self.inst2], {"alpha": mapping["alpha"]})
        self.assertIn("缺少", str(ctx.exception))
        extra = dict(mapping)
        extra["gamma"] = mapping["alpha"]
        # 多出的键还绑着 alpha 的 ID ⇒ 先在集合比对阶段被拦下
        with self.assertRaises(BatchError) as ctx:
            verify_batch([self.inst1, self.inst2], extra)
        self.assertIn("多出", str(ctx.exception))

    def test_direct_boolean_sum_catches_fixed_challenge_cheat(self):
        """手写作弊转录：f=x0, r0=5, g0(t)≡5, 初始主张 10。

        闭合检查：g(0)+g(1)=5+5=10=初始主张；最终点值：g(5)=5=f(5)。
        固定挑战下旧单实例 verifier 会被这种“围绕挑战点重选和”的转录骗过，
        但批处理新增的小域直接求和核验知道真实和为 1（f(0)+f(1)=0+1），
        必须拒绝。
        """
        poly = make_poly(1, [(1, (0,))])
        cheat = Transcript(
            1, 10,
            (RoundMessage(0, (5,), 10, None, None),),
            (5,), 5,
            instance_id="cheat",
        )
        # 独立参考确认真实求和
        self.assertEqual(ref_boolean_sum(spec_of(poly)), 1)
        # 旧单实例入口：固定挑战下确实接受（这是本次批处理要补的核验口径）
        self.assertTrue(verify(poly, cheat).accepted)
        # 批处理入口：直接枚举求和 1 ≠ 转录主张 10 ⇒ 拒绝，整批失败
        inst = BatchInstance("cheat", poly, (5,))
        report = verify_batch([inst], {"cheat": cheat})
        self.assertFalse(report.accepted)
        r = report.by_id()["cheat"]
        self.assertEqual(r.failed_round, 0)
        self.assertIn("直接求和核验", r.reason)

    def test_false_initial_claim_tamper_rejected_by_direct_sum(self):
        """轮间闭合成立但初始主张被改成错误值：直接求和核验在轮 0 拦下。"""
        mapping = self.good.transcript_by_id()
        tr = mapping["alpha"]
        # f=1+x0*x1 真实和为 5；改成 6 且保持转录自洽不可能（闭合会断），
        # 这里直接改 initial_claim，验证独立求和口径优先于转录。
        tampered = replace(tr, initial_claim=6)
        report = verify_batch([self.inst1], {"alpha": tampered})
        self.assertFalse(report.accepted)
        self.assertEqual(report.by_id()["alpha"].failed_round, 0)


class BatchInputValidationTests(unittest.TestCase):
    def test_empty_batch(self):
        with self.assertRaises(BatchError):
            prove_batch([])
        with self.assertRaises(BatchError):
            verify_batch([], {})

    def test_duplicate_ids(self):
        inst = make_instance("x", 1, [(1, (0,))], (1,))
        with self.assertRaises(BatchError) as ctx:
            prove_batch([inst, inst])
        self.assertIn("重复", str(ctx.exception))

    def test_bad_id_type_and_empty_id(self):
        poly = make_poly(1, [(1, (0,))])
        for bad in (123, None, True, "", b"x"):
            with self.subTest(bad=bad):
                with self.assertRaises(BatchError):
                    BatchInstance(bad, poly, (1,))

    def test_bad_instance_element_type(self):
        with self.assertRaises(BatchError):
            prove_batch(["not-an-instance"])

    def test_instance_challenge_validation(self):
        poly = make_poly(2, [(1, (0,))])
        with self.assertRaises(BatchError):  # 长度不符
            BatchInstance("x", poly, (1,))
        with self.assertRaises(BatchError):  # 域外
            BatchInstance("y", poly, (1, 101))
        with self.assertRaises(BatchError):  # bool
            BatchInstance("z", poly, (True, 0))

    def test_instance_zero_vars_rejected(self):
        poly0 = MultilinearPoly(0, [Term(9, ())])
        with self.assertRaises(BatchError):
            BatchInstance("const", poly0, ())

    def test_false_claimed_sum_rejected_before_any_transcript(self):
        inst = make_instance("cs", 1, [(1, (0,))], (5,), claimed_sum=9)
        with self.assertRaises(BatchError) as ctx:
            prove_batch([inst])
        self.assertIn("claimed_sum", str(ctx.exception))

    def test_no_partial_state_when_later_instance_bad(self):
        """第 2 个实例主张为假：整体抛异常，不返回任何半成品报告，可原样重试。"""
        good = make_instance("ok", 1, [(1, (0,))], (5,))
        bad = make_instance("bad", 1, [(1, (0,))], (5,), claimed_sum=9)
        with self.assertRaises(BatchError):
            prove_batch([good, bad])
        # 输入对象未被修改；去掉坏实例后整批成功
        report = prove_batch([good])
        self.assertTrue(report.accepted)

    def test_budget_exhausted_in_batch(self):
        big = make_instance("big", 4, [(1, (0, 1, 2, 3))], (1, 2, 3, 4))
        with self.assertRaises(BudgetExhausted):
            prove_batch([big], eval_budget=15)
        with self.assertRaises(BudgetExhausted):
            tr = prove_batch([big]).transcript_by_id()
            verify_batch([big], tr, eval_budget=15)
        # 放宽预算即成功（预算耗尽 ≠ 无解）
        self.assertTrue(prove_batch([big], eval_budget=16).accepted)

    def test_batch_size_limit(self):
        insts = [
            make_instance(f"i{k:03d}", 1, [(1, (0,))], (k % P,))
            for k in range(MAX_BATCH_INSTANCES + 1)
        ]
        with self.assertRaises(BatchError):
            prove_batch(insts)

    def test_batch_size_limit_boundary_accepted(self):
        # 上限边界：256 个 n=1 实例应当全部通过
        insts = [
            make_instance(f"i{k:03d}", 1, [(1, (0,))], (k % P,))
            for k in range(MAX_BATCH_INSTANCES)
        ]
        report = prove_batch(insts)
        self.assertEqual(report.count, MAX_BATCH_INSTANCES)
        self.assertTrue(report.accepted)

    def test_transcripts_must_be_mapping(self):
        inst = make_instance("x", 1, [(1, (0,))], (1,))
        good = prove_batch([inst]).transcript_by_id()
        with self.assertRaises(BatchError):
            verify_batch([inst], list(good.values()))
        with self.assertRaises(BatchError):
            verify_batch([inst], [("x", good["x"])])


class SingleInstanceApiUnchangedTests(unittest.TestCase):
    """旧单实例入口与旧 5 字段位置构造必须继续可用。"""

    def test_legacy_positional_transcript_and_verify(self):
        poly = make_poly(2, [(1, ()), (1, (0, 1))])
        tr = prove(poly, (2, 3))
        self.assertIsNone(tr.instance_id)
        # 旧测试风格的 5 位置参数构造
        legacy = Transcript(
            tr.num_vars, tr.initial_claim, tr.rounds, tr.challenges, tr.final_claim
        )
        self.assertTrue(verify(poly, legacy).accepted)

    def test_bound_transcript_also_verifies_single_instance(self):
        poly = make_poly(1, [(1, (0,))])
        tr = replace(prove(poly, (5,)), instance_id="x")
        # 单实例 verify 不关心 ID（绑定是批处理层的职责）
        self.assertTrue(verify(poly, tr).accepted)

    def test_structure_error_in_batch_wrapped_with_instance_id(self):
        inst = make_instance("s", 2, [(1, (0,))], (1, 2))
        tr = prove_batch([inst]).transcript_by_id()["s"]
        bad = replace(tr, rounds=tuple(list(tr.rounds[:1])))  # 少一轮
        with self.assertRaises(BatchError) as ctx:
            verify_batch([inst], {"s": bad})
        self.assertIn("s", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
