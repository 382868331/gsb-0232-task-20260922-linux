"""多实例批处理测试：正常/边界/失败/跨实例绑定。

期望值来源（不调用被测核心生成期望值）：

- ``tests/reference.py``：与被测库完全独立的 F_101 枚举参考；
- 手算常量例子（在测试内显式写出数字与逐点求和）；
- 第三条解析公式：多线性项 ``c*Π_{i∈S} X_i`` 在布尔立方体上的总贡献为
  ``c * 2^(n-|S|)``（对每个自由布尔位求和），与枚举实现相互独立。
单实例 prove/verify 往返一致只作补充。
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sumcheck import (  # noqa: E402
    MAX_INSTANCES,
    MAX_VARS,
    BatchError,
    BatchInstance,
    BatchResult,
    BatchTranscript,
    BoundTranscript,
    MultilinearPoly,
    P,
    RoundMessage,
    Term,
    Transcript,
    TranscriptFormatError,
    independent_boolean_sum,
    prove,
    prove_batch,
    verify,
    verify_batch,
)
from sumcheck.errors import BudgetExhausted, ProtocolError  # noqa: E402
from tests.reference import ref_boolean_sum  # noqa: E402


def make_poly(n, terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in terms])


def analytic_boolean_sum(poly: MultilinearPoly) -> int:
    """测试专用第三条独立路径：按项的解析贡献 c*2^(n-|S|) 求和。"""
    n = poly.num_vars
    total = 0
    for term in poly.terms:
        total = (total + term.coeff * pow(2, n - len(term.variables), P)) % P
    return total


def hand_boolean_sum(poly: MultilinearPoly) -> int:
    """测试专用：最朴素的逐点 0/1 代入（直接算乘积，不用库求值）。"""
    n = poly.num_vars
    total = 0
    for mask in range(1 << n):
        point = [(mask >> j) & 1 for j in range(n)]
        value = 0
        for term in poly.terms:
            product = 1
            for i in term.variables:
                product *= point[i]
            value += term.coeff * product
        total = (total + value) % P
    return total


# 手算样例（README 同款）：
#   A: f=3+2*x0+x1+x0*x2，n=3，立方体逐点 3,5,4,6,3,6,4,7 -> 和 38
#   B: f=1+x0*x1，n=2，逐点 1,1,1,2 -> 和 5
#   C: f=5+7*x0，n=1，逐点 5,12 -> 和 17
POLY_A = make_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))])
POLY_B = make_poly(2, [(1, ()), (1, (0, 1))])
POLY_C = make_poly(1, [(5, ()), (7, (0,))])
HAND_SUMS = {"A": 38, "B": 5, "C": 17}


def hand_batch(challenges=None):
    ch = challenges or {"A": (17, 42, 3), "B": (2, 3), "C": (4,)}
    return [
        BatchInstance("A", POLY_A, ch["A"]),
        BatchInstance("B", POLY_B, ch["B"]),
        BatchInstance("C", POLY_C, ch["C"]),
    ]


class IndependentSumTests(unittest.TestCase):
    """independent_boolean_sum 必须与三条独立路径全部一致。"""

    def test_handworked_constants(self):
        for poly, expect in ((POLY_A, 38), (POLY_B, 5), (POLY_C, 17)):
            with self.subTest(poly=poly):
                self.assertEqual(independent_boolean_sum(poly), expect)
                self.assertEqual(hand_boolean_sum(poly), expect)
                self.assertEqual(analytic_boolean_sum(poly), expect)

    def test_matches_reference_and_analytic_random(self):
        rng = random.Random(20260924)
        for _ in range(80):
            n = rng.randint(1, MAX_VARS)
            subsets = [frozenset(j for j in range(n) if (mask >> j) & 1)
                       for mask in range(1 << n)]
            rng.shuffle(subsets)
            k = rng.randint(1, min(14, len(subsets)))
            raw = [(rng.randrange(1, P), tuple(sorted(s))) for s in subsets[:k]]
            poly = make_poly(n, raw)
            spec = (n, raw)
            self.assertEqual(independent_boolean_sum(poly), ref_boolean_sum(spec))
            self.assertEqual(independent_boolean_sum(poly), analytic_boolean_sum(poly))
            self.assertEqual(independent_boolean_sum(poly), hand_boolean_sum(poly))

    def test_zero_coefficient_terms_contribute_nothing(self):
        poly = make_poly(3, [(0, (0, 1, 2)), (4, ()), (0, (2,))])
        # 只有常量 4：8 个点各 4 -> 32
        self.assertEqual(independent_boolean_sum(poly), 32 % P)


class HappyPathBatchTests(unittest.TestCase):
    def test_mixed_variable_counts_all_accepted(self):
        """一个批次中各实例保留自己的变量数（1/2/3 混合）。"""
        instances = hand_batch()
        bt = prove_batch(instances)
        result = verify_batch(instances, bt)
        self.assertIsInstance(result, BatchResult)
        self.assertTrue(result.accepted)
        self.assertEqual(result.accepted_count, 3)
        self.assertEqual(result.failed_count, 0)
        for r, inst in zip(result.results, instances):
            self.assertEqual(r.instance_id, inst.instance_id)
            self.assertIsNone(r.failed_round)
            self.assertEqual(r.direct_sum, HAND_SUMS[inst.instance_id])
            self.assertTrue(r.verify_result.accepted)
        # 封套身份快照
        self.assertEqual(bt.instance_ids, ("A", "B", "C"))
        self.assertEqual([item.num_vars for item in bt.items], [3, 2, 1])
        self.assertEqual([item.position for item in bt.items], [0, 1, 2])

    def test_single_instance_batch(self):
        instances = [BatchInstance("only", POLY_B, (2, 3))]
        result = verify_batch(instances, prove_batch(instances))
        self.assertTrue(result.accepted)
        self.assertEqual(result.results[0].direct_sum, 5)

    def test_generator_input_consumed_once_by_caller(self):
        # 生成器是单次迭代容器：批次内部必须先物化为 tuple，不能迭代两遍
        def gen():
            yield BatchInstance("A", POLY_A, (17, 42, 3))
            yield BatchInstance("B", POLY_B, (2, 3))

        bt = prove_batch(gen())
        result = verify_batch(iter([BatchInstance("A", POLY_A, (17, 42, 3)),
                                    BatchInstance("B", POLY_B, (2, 3))]), bt)
        self.assertTrue(result.accepted)

    def test_constant_only_polys_various_sizes(self):
        instances = [
            BatchInstance(f"const{n}", make_poly(n, [(7, ())]), tuple(range(1, n + 1)))
            for n in range(1, MAX_VARS + 1)
        ]
        result = verify_batch(instances, prove_batch(instances))
        self.assertTrue(result.accepted, [r.reason for r in result.results if not r.accepted])
        for n, r in enumerate(result.results, start=1):
            self.assertEqual(r.direct_sum, 7 * pow(2, n, P) % P)

    def test_each_instance_uses_own_challenges(self):
        # 两个实例用同一多项式但不同挑战：转录必须不同，且各自独立验证通过
        instances = [
            BatchInstance("p-r1", POLY_A, (0, 0, 0)),
            BatchInstance("p-r2", POLY_A, (100, 100, 100)),
        ]
        bt = prove_batch(instances)
        self.assertNotEqual(
            bt.items[0].transcript.final_claim,
            bt.items[1].transcript.final_claim,
        )
        self.assertTrue(verify_batch(instances, bt).accepted)

    def test_per_instance_budget_and_claimed_sum(self):
        poly = make_poly(4, [(1, (0, 1, 2, 3))])  # 求和 1，立方体 16 点
        true_sum = independent_boolean_sum(poly)
        instances = [
            BatchInstance("tight", poly, (1, 2, 3, 4), eval_budget=16,
                          claimed_sum=true_sum),
            BatchInstance("loose", poly, (4, 3, 2, 1), eval_budget=1 << 16),
        ]
        self.assertTrue(verify_batch(instances, prove_batch(instances)).accepted)

    def test_batch_does_not_mutate_inputs(self):
        instances = hand_batch()
        before = [(i.instance_id, i.poly, i.challenges) for i in instances]
        prove_batch(instances)
        self.assertEqual(
            [(i.instance_id, i.poly, i.challenges) for i in instances], before
        )

    def test_batch_transcript_is_immutable(self):
        bt = prove_batch(hand_batch())
        with self.assertRaises(Exception):
            bt.items = ()  # type: ignore[misc]
        with self.assertRaises(Exception):
            bt.items[0].instance_id = "X"  # type: ignore[misc]


class BatchBoundaryTests(unittest.TestCase):
    def test_max_instances_accepted_and_over_limit_rejected(self):
        # 100 个 n=1 的便宜实例
        instances = [
            BatchInstance(f"inst-{k:03d}", make_poly(1, [(1 + k % 100, (0,))]), (k % P,))
            for k in range(MAX_INSTANCES)
        ]
        result = verify_batch(instances, prove_batch(instances))
        self.assertTrue(result.accepted)
        self.assertEqual(result.accepted_count, MAX_INSTANCES)

        over = instances + [BatchInstance("inst-100", make_poly(1, [(1, (0,))]), (1,))]
        with self.assertRaises(BatchError) as ctx:
            prove_batch(over)
        self.assertIn("101", str(ctx.exception))

    def test_eight_variable_instance_in_batch(self):
        rng = random.Random(99)
        subsets = [frozenset(j for j in range(8) if (mask >> j) & 1)
                   for mask in range(1, 1 << 8)]
        rng.shuffle(subsets)
        raw = [(rng.randrange(1, P), tuple(sorted(s))) for s in subsets[:20]]
        big = make_poly(8, raw)
        small = make_poly(1, [(3, ())])
        instances = [
            BatchInstance("small", small, (7,)),
            BatchInstance("big", big, (17, 42, 3, 60, 91, 25, 8, 50)),
        ]
        result = verify_batch(instances, prove_batch(instances))
        self.assertTrue(result.accepted, result.results[1].reason)
        self.assertEqual(result.results[1].direct_sum, ref_boolean_sum((8, raw)))

    def test_challenges_at_field_endpoints(self):
        instances = [BatchInstance("ends", POLY_A, (0, 100, 0))]
        self.assertTrue(verify_batch(instances, prove_batch(instances)).accepted)


class BatchFailureAggregationTests(unittest.TestCase):
    """任何实例失败 -> 整体失败；其余实例照常核验并汇报。"""

    def setUp(self):
        self.instances = hand_batch()
        self.bt = prove_batch(self.instances)

    def _replace_item(self, pos, **changes):
        items = list(self.bt.items)
        items[pos] = replace(items[pos], **changes)
        return replace(self.bt, items=tuple(items))

    def test_one_falsified_instance_fails_whole_batch_others_reported(self):
        # 篡改实例 B 第 0 轮常数项
        tr_b = self.bt.items[1].transcript
        bad_round = replace(tr_b.rounds[0], coeffs=((tr_b.rounds[0].coeffs[0] + 1) % P,))
        bad_bt = self._replace_item(1, transcript=replace(tr_b, rounds=(bad_round, tr_b.rounds[1])))
        result = verify_batch(self.instances, bad_bt)
        self.assertFalse(result.accepted)
        self.assertEqual(result.accepted_count, 2)
        self.assertEqual(result.failed_count, 1)
        statuses = {r.instance_id: r.accepted for r in result.results}
        self.assertEqual(statuses, {"A": True, "B": False, "C": True})
        rb = result.results[1]
        self.assertEqual(rb.failed_round, 0)
        self.assertIsNotNone(rb.verify_result)
        self.assertFalse(rb.verify_result.accepted)
        # 失败实例的独立小域求和仍被计算并报告
        self.assertEqual(rb.direct_sum, 5)

    def test_multiple_failures_all_reported(self):
        tr_a = self.bt.items[0].transcript
        tr_c = self.bt.items[2].transcript
        bad_a = replace(
            tr_a,
            rounds=(replace(tr_a.rounds[0],
                            coeffs=((tr_a.rounds[0].coeffs[0] + 1) % P,
                                    tr_a.rounds[0].coeffs[1])),)
                  + tr_a.rounds[1:],
        )
        bad_c = replace(
            tr_c,
            rounds=(replace(tr_c.rounds[0],
                            coeffs=((tr_c.rounds[0].coeffs[0] + 2) % P,)),),
        )
        items = list(self.bt.items)
        items[0] = replace(items[0], transcript=bad_a)
        items[2] = replace(items[2], transcript=bad_c)
        result = verify_batch(self.instances, replace(self.bt, items=tuple(items)))
        self.assertFalse(result.accepted)
        self.assertEqual(result.failed_count, 2)
        self.assertEqual([r.failed_round for r in result.results], [0, None, 0])

    def test_structurally_illegal_instance_transcript_raises(self):
        # B 的转录塞入二次系数：结构错误不与逻辑失败混合，直接抛出
        tr_b = self.bt.items[1].transcript
        bad_round = replace(tr_b.rounds[0], coeffs=(1, 2, 3))
        bad_bt = self._replace_item(1, transcript=replace(tr_b, rounds=(bad_round, tr_b.rounds[1])))
        with self.assertRaises(TranscriptFormatError) as ctx:
            verify_batch(self.instances, bad_bt)
        self.assertIn("rounds[0]", str(ctx.exception))

    def test_crafted_false_claim_caught_by_direct_sum_only(self):
        """单实例 verify 会接受的假 C 转录（终点恰好对上），批次小域核验必须拒绝。"""
        # f=x0*x1, r=(2,3)：真和=1，f(2,3)=6；伪造 C=24、两轮常多项式
        poly = make_poly(2, [(1, (0, 1))])
        r0_msg = RoundMessage(0, (12,), 24, 2, 12)
        r1_msg = RoundMessage(1, (6,), 12, None, None)
        fake_tr = Transcript(2, 24, (r0_msg, r1_msg), (2, 3), 6)
        self.assertTrue(verify(poly, fake_tr).accepted)  # 固定挑战下单实例检查不到 C
        instances = [BatchInstance("fake", poly, (2, 3))]
        bt = BatchTranscript(("fake",), (BoundTranscript(0, "fake", 2, (2, 3), fake_tr),))
        result = verify_batch(instances, bt)
        self.assertFalse(result.accepted)
        ir = result.results[0]
        self.assertEqual(ir.failed_round, -1)
        self.assertEqual(ir.direct_sum, 1)
        self.assertIn("小域直接求和", ir.reason)


class InstanceBindingTests(unittest.TestCase):
    """独立绑定：禁止把一份消息用于另一实例。"""

    def setUp(self):
        self.instances = hand_batch()
        self.bt = prove_batch(self.instances)

    def test_swap_items_across_instances_rejected(self):
        swapped = replace(self.bt, items=(self.bt.items[1], self.bt.items[0], self.bt.items[2]))
        with self.assertRaises(BatchError) as ctx:
            verify_batch(self.instances, swapped)
        self.assertIn("position", str(ctx.exception))

    def test_item_id_mismatch_rejected(self):
        item = replace(self.bt.items[0], instance_id="B")
        bt = replace(self.bt, items=(item,) + self.bt.items[1:])
        with self.assertRaises(BatchError) as ctx:
            verify_batch(self.instances, bt)
        self.assertIn("不一致", str(ctx.exception))

    def test_instance_ids_tuple_rewrite_rejected(self):
        bt = replace(self.bt, instance_ids=("B", "A", "C"))
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt)

    def test_position_field_rewrite_rejected(self):
        item = replace(self.bt.items[0], position=2)
        bt = replace(self.bt, items=(item,) + self.bt.items[1:])
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt)

    def test_num_vars_snapshot_mismatch_rejected(self):
        item = replace(self.bt.items[0], num_vars=2)
        bt = replace(self.bt, items=(item,) + self.bt.items[1:])
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt)

    def test_challenge_snapshot_mismatch_rejected(self):
        # 封套挑战被改
        item = replace(self.bt.items[1], challenges=(9, 9))
        bt = replace(self.bt, items=(self.bt.items[0], item, self.bt.items[2]))
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt)
        # 内层转录挑战被改而封套不改
        tr_b = self.bt.items[1].transcript
        inner = replace(tr_b, challenges=(9, 9))
        item2 = replace(self.bt.items[1], transcript=inner)
        bt2 = replace(self.bt, items=(self.bt.items[0], item2, self.bt.items[2]))
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt2)

    def test_unknown_transcript_bound_for_other_poly(self):
        """A 的合法整份转录放进 B 的槽位，即使改了 ID/位置快照也必须被拒。"""
        tr_a = self.bt.items[0].transcript
        forged = BoundTranscript(1, "B", POLY_B.num_vars, (2, 3), tr_a)
        bt = replace(self.bt, items=(self.bt.items[0], forged, self.bt.items[2]))
        # 内层挑战数组先对不上；即便长度凑巧相同的场景，结构校验也会拒绝
        with self.assertRaises((BatchError, TranscriptFormatError)):
            verify_batch(self.instances, bt)

    def test_item_count_mismatch_rejected(self):
        bt = replace(self.bt, items=self.bt.items[:2])
        with self.assertRaises(BatchError):
            verify_batch(self.instances, bt)


class InvalidBatchInputTests(unittest.TestCase):
    def test_empty_duplicate_blank_nonstr_ids(self):
        with self.assertRaises(BatchError):
            prove_batch([])
        with self.assertRaises(BatchError):
            prove_batch([
                BatchInstance("A", POLY_A, (1, 2, 3)),
                BatchInstance("A", POLY_B, (2, 3)),
            ])
        for bad_id in ("", "   "):
            with self.assertRaises(BatchError):
                prove_batch([BatchInstance(bad_id, POLY_B, (2, 3))])
        for bad_id in (1, None, b"A", True):
            with self.assertRaises(BatchError):
                prove_batch([BatchInstance(bad_id, POLY_B, (2, 3))])

    def test_non_instance_elements_and_non_iterable(self):
        with self.assertRaises(BatchError):
            prove_batch([("A", POLY_A, (1, 2, 3))])
        with self.assertRaises(BatchError):
            prove_batch("not-a-batch")
        with self.assertRaises(BatchError):
            prove_batch(None)

    def test_challenge_length_and_field_elements_per_instance(self):
        with self.assertRaises(BatchError):
            prove_batch([
                BatchInstance("A", POLY_A, (17, 42, 3)),
                BatchInstance("B", POLY_B, (2, 3, 9)),  # B 只有 2 变量
            ])
        for bad_ch in (101, -1, 1.5, True):
            with self.assertRaises(BatchError):
                prove_batch([BatchInstance("A", POLY_A, (bad_ch, 42, 3))])

    def test_empty_variable_instance_rejected(self):
        poly0 = MultilinearPoly(0, [Term(9, ())])
        with self.assertRaises(ProtocolError):
            prove_batch([BatchInstance("zero", poly0, ())])

    def test_bad_poly_and_budget_types(self):
        with self.assertRaises(BatchError):
            prove_batch([BatchInstance("x", "not-a-poly", (1,))])
        with self.assertRaises(BatchError):
            prove_batch([BatchInstance("x", POLY_B, (2, 3), eval_budget=True)])
        with self.assertRaises(BatchError):
            prove_batch([BatchInstance("x", POLY_B, (2, 3), claimed_sum=101)])

    def test_verify_batch_validates_inputs_before_envelope(self):
        # 输入实例本身非法时，即使给个空转录也应先报输入错误
        with self.assertRaises(BatchError):
            verify_batch([], BatchTranscript((), ()))


class BatchAtomicityTests(unittest.TestCase):
    def test_budget_exhausted_aborts_whole_batch_without_partial_output(self):
        good = BatchInstance("good", make_poly(1, [(1, (0,))]), (5,))
        broke = BatchInstance("broke", make_poly(4, [(1, (0, 1, 2, 3))]),
                              (1, 2, 3, 4), eval_budget=15)
        with self.assertRaises(BudgetExhausted):
            prove_batch([good, broke])
        # 同一批放宽预算即可全部成功（预算耗尽≠无解，且无残留半批次状态）
        broke_ok = BatchInstance("broke", broke.poly, (1, 2, 3, 4), eval_budget=16)
        result = verify_batch([good, broke_ok], prove_batch([good, broke_ok]))
        self.assertTrue(result.accepted)

    def test_false_claimed_sum_aborts_batch(self):
        with self.assertRaises(ProtocolError):
            prove_batch([
                BatchInstance("ok", POLY_C, (4,)),
                BatchInstance("lie", POLY_B, (2, 3), claimed_sum=6),  # 真和为 5
            ])

    def test_batch_level_default_budget_applies_to_all(self):
        polys = [
            BatchInstance(f"i{k}", make_poly(3, [(1, (0, 1, 2))]), (1, 2, 3))
            for k in range(4)
        ]
        with self.assertRaises(BudgetExhausted):
            prove_batch(polys, eval_budget=7)
        self.assertTrue(verify_batch(polys, prove_batch(polys, eval_budget=8)).accepted)


class SingleInstanceApiUnchangedTests(unittest.TestCase):
    """旧的单实例公开入口继续可用（旧测试已全覆盖，这里补一条导入/行为确认）。"""

    def test_legacy_prove_verify_still_works(self):
        tr = prove(POLY_A, (17, 42, 3))
        result = verify(POLY_A, tr)
        self.assertTrue(result.accepted)
        self.assertEqual(tr.initial_claim, 38)


if __name__ == "__main__":
    unittest.main()
