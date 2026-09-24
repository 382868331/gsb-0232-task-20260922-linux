#!/usr/bin/env python3
"""sumcheck 教学库固定输入演示（纯标准库，离线，确定性数据）。

运行：python demo.py

打印内容：
1. 一个 3 变量多项式的诚实证明转录 —— 逐轮真实枚举布尔立方体计算 —— 并被接受；
2. 一个 8 变量多项式（接口上限）的端到端接受，附真实耗时；
3. 中间轮系数被篡改后，verifier 在首个失败轮拒绝；
4. 一个结构非法转录（域外数 / 非法次数）实际抛出的异常边界；
5. 多实例批次：不同变量数的实例一次提交、逐实例核验并汇总；
6. 批次失败：篡改其中一个实例，其余照常核验、整体拒绝；
7. 跨实例绑定：交换两条转录的槽位，BatchError 实际拒绝；
8. 非法批次输入（空批次 / 重复 ID）的明确报错。

不使用 sleep，不使用预录结果：所有数字均由现场计算得出。
"""

from __future__ import annotations

import time
from dataclasses import replace

from sumcheck import (
    BatchError,
    BatchInstance,
    MultilinearPoly,
    Term,
    TranscriptFormatError,
    prove,
    prove_batch,
    verify,
    verify_batch,
)

P = 101


def build_poly(n, raw_terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in raw_terms])


def main() -> None:
    print("=" * 70)
    print("演示 1：正常接受 —— f(x0,x1,x2) = 3 + 2*x0 + x1 + x0*x2")
    print("=" * 70)
    poly = build_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))])
    challenges = (17, 42, 3)
    t0 = time.perf_counter()
    transcript = prove(poly, challenges)
    result = verify(poly, transcript)
    elapsed = time.perf_counter() - t0
    print(transcript.pretty())
    print(f"verifier 独立直接求值 f{challenges} = {result.direct_evaluation}")
    print(f"结论: accepted={result.accepted}, 检查轮数={result.rounds_checked}"
          f"（真实计算耗时 {elapsed * 1000:.2f} ms）")
    assert result.accepted

    print()
    print("=" * 70)
    print("演示 2：接口上限规模 —— 8 变量、20 个确定性生成项")
    print("=" * 70)
    # 确定性生成：用简单线性同余挑选变量集合与系数（固定种子，无随机/无外部数据）
    raw_terms = []
    state = 123456789
    for k in range(20):
        state = (1103515245 * state + 12345) % (1 << 31)
        subset_mask = state & 0xFF
        coeff = (state >> 8) % 100 + 1
        variables = tuple(j for j in range(8) if (subset_mask >> j) & 1)
        # 避免与已选项的变量集合重复（重复项由构造器拒绝，这里演示合法输入）
        if any(set(v) == set(variables) for _, v in raw_terms):
            continue
        raw_terms.append((coeff, variables))
    big_poly = build_poly(8, raw_terms)
    big_challenges = (17, 42, 3, 60, 91, 25, 8, 50)
    t0 = time.perf_counter()
    big_transcript = prove(big_poly, big_challenges)
    big_result = verify(big_poly, big_transcript)
    elapsed = time.perf_counter() - t0
    print(f"实际项数={len(raw_terms)}，初始主张={big_transcript.initial_claim}，"
          f"最终主张={big_transcript.final_claim}，"
          f"直接求值={big_result.direct_evaluation}")
    print(f"结论: accepted={big_result.accepted}（真实计算耗时 {elapsed * 1000:.2f} ms）")
    assert big_result.accepted

    print()
    print("=" * 70)
    print("演示 3：篡改拒绝 —— 把演示 1 的第 1 轮一次项系数 +1")
    print("=" * 70)
    msg1 = transcript.rounds[1]
    tampered_coeffs = (msg1.coeffs[0], (msg1.coeffs[1] + 1) % P)
    tampered_rounds = list(transcript.rounds)
    tampered_rounds[1] = replace(msg1, coeffs=tampered_coeffs)
    tampered = replace(transcript, rounds=tuple(tampered_rounds))
    print(f"原 g1(t) 系数 = {msg1.coeffs}")
    print(f"篡改为         {tampered_coeffs}")
    bad_result = verify(poly, tampered)
    print(f"结论: accepted={bad_result.accepted}，"
          f"首个失败轮 failed_round={bad_result.failed_round}")
    print(f"原因: {bad_result.reason}")
    assert not bad_result.accepted and bad_result.failed_round == 1

    print()
    print("=" * 70)
    print("演示 4：结构非法异常 —— 注入域外数 101 与二次系数（非法次数）")
    print("=" * 70)
    bad_structure = replace(
        transcript,
        rounds=tuple(
            [replace(transcript.rounds[0], coeffs=(101, 2))]
            + list(transcript.rounds[1:])
        ),
    )
    try:
        verify(poly, bad_structure)
    except TranscriptFormatError as exc:
        print(f"如预期抛出 TranscriptFormatError：{exc}")
    else:  # pragma: no cover - 演示断言
        raise AssertionError("域外数必须被拒绝")

    illegal_degree = replace(
        transcript,
        rounds=tuple(
            list(transcript.rounds[:1])
            + [replace(transcript.rounds[1], coeffs=(1, 2, 3))]
            + list(transcript.rounds[2:])
        ),
    )
    try:
        verify(poly, illegal_degree)
    except TranscriptFormatError as exc:
        print(f"如预期抛出 TranscriptFormatError：{exc}")
    else:  # pragma: no cover - 演示断言
        raise AssertionError("非法次数必须被拒绝")

    print()
    print("=" * 70)
    print("演示 5：多实例批次 —— 3 个变量数不同（3/2/1）的实例一次提交")
    print("=" * 70)
    # 三个实例各自保留自己的变量数与次数上界，挑战数组也各自独立：
    #   A: f=3+2*x0+x1+x0*x2（3 变量），手算立方体和 = 38
    #   B: f=1+x0*x1（2 变量），逐点 1,1,1,2 -> 和 5
    #   C: f=5+7*x0（1 变量），逐点 5,12 -> 和 17
    poly_b = build_poly(2, [(1, ()), (1, (0, 1))])
    poly_c = build_poly(1, [(5, ()), (7, (0,))])
    instances = [
        BatchInstance("cube-A", poly, (17, 42, 3)),
        BatchInstance("square-B", poly_b, (2, 3)),
        BatchInstance("line-C", poly_c, (4,)),
    ]
    t0 = time.perf_counter()
    batch_transcript = prove_batch(instances)
    batch_result = verify_batch(instances, batch_transcript)
    elapsed = time.perf_counter() - t0
    for item, r in zip(batch_transcript.items, batch_result.results):
        tr = item.transcript
        print(
            f"  [{r.instance_id}] 变量数={item.num_vars}，"
            f"初始主张={tr.initial_claim}，"
            f"verifier 独立直接求和={r.direct_sum}，"
            f"accepted={r.accepted}"
        )
    print(f"整批结论: accepted={batch_result.accepted}"
          f"（接受 {batch_result.accepted_count}/{len(instances)}，"
          f"真实计算耗时 {elapsed * 1000:.2f} ms）")
    assert batch_result.accepted
    assert [r.direct_sum for r in batch_result.results] == [38, 5, 17]

    print()
    print("=" * 70)
    print("演示 6：批次失败 —— 只篡改 square-B 的第 0 轮，其余实例照常核验")
    print("=" * 70)
    tr_b = batch_transcript.items[1].transcript
    tampered_round = replace(
        tr_b.rounds[0], coeffs=((tr_b.rounds[0].coeffs[0] + 1) % P,)
    )
    tampered_tr_b = replace(tr_b, rounds=(tampered_round, tr_b.rounds[1]))
    tampered_items = list(batch_transcript.items)
    tampered_items[1] = replace(tampered_items[1], transcript=tampered_tr_b)
    tampered_batch = replace(batch_transcript, items=tuple(tampered_items))
    bad_batch_result = verify_batch(instances, tampered_batch)
    print(f"整批结论: accepted={bad_batch_result.accepted}"
          f"（接受 {bad_batch_result.accepted_count}，"
          f"失败 {bad_batch_result.failed_count}）")
    for r in bad_batch_result.results:
        print(f"  [{r.instance_id}] accepted={r.accepted}"
              + (f"，首个失败轮={r.failed_round}" if not r.accepted else ""))
    print(f"  失败原因: {bad_batch_result.results[1].reason}")
    assert not bad_batch_result.accepted
    assert bad_batch_result.accepted_count == 2
    assert bad_batch_result.results[1].failed_round == 0

    print()
    print("=" * 70)
    print("演示 7：独立绑定实例 ID —— 交换 cube-A / square-B 两条转录的槽位")
    print("=" * 70)
    swapped = replace(
        batch_transcript,
        items=(batch_transcript.items[1], batch_transcript.items[0],
               batch_transcript.items[2]),
    )
    try:
        verify_batch(instances, swapped)
    except BatchError as exc:
        print(f"如预期抛出 BatchError：{exc}")
    else:  # pragma: no cover - 演示断言
        raise AssertionError("跨实例消息错槽必须被拒绝")

    print()
    print("=" * 70)
    print("演示 8：非法批次输入 —— 空批次 / 重复实例 ID")
    print("=" * 70)
    for label, bad_instances in (
        ("空批次", []),
        ("重复 ID", [
            BatchInstance("dup", poly, (17, 42, 3)),
            BatchInstance("dup", poly_b, (2, 3)),
        ]),
    ):
        try:
            prove_batch(bad_instances)
        except BatchError as exc:
            print(f"{label} 如预期抛出 BatchError：{exc}")
        else:  # pragma: no cover - 演示断言
            raise AssertionError(f"{label} 必须被拒绝")

    print()
    print("全部演示完成：2 个正常接受（含批次）、2 个单实例拒绝/异常、"
          "1 个批次失败聚合、1 个跨实例绑定拒绝、2 个非法批次输入。")


if __name__ == "__main__":
    main()
