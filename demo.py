#!/usr/bin/env python3
"""sumcheck 教学库固定输入演示（纯标准库，离线，确定性数据）。

运行：python demo.py

打印内容：
1. 一个 3 变量多项式的诚实证明转录 —— 逐轮真实枚举布尔立方体计算 —— 并被接受；
2. 一个 8 变量多项式（接口上限）的端到端接受，附真实耗时；
3. 中间轮系数被篡改后，verifier 在首个失败轮拒绝；
4. 一个结构非法转录（域外数 / 非法次数）实际抛出的异常边界。

不使用 sleep，不使用预录结果：所有数字均由现场计算得出。
"""

from __future__ import annotations

import time
from dataclasses import replace

from sumcheck import (
    BatchError,
    BatchInstance,
    MultilinearPoly,
    RoundMessage,
    Term,
    Transcript,
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
    demo_batch()

    print()
    print("全部演示完成：单实例 1 个正常接受、1 个上限规模接受、1 个篡改拒绝、"
          "2 个结构异常；批处理 1 个多规模整批接受、1 个边界批次、"
          "1 个实例失败致整批失败、1 个跨实例冒用、1 个直接求和抓假、1 个非法批次。")


def demo_batch() -> None:
    """多实例批处理：不同 n 的实例逐实例运行，独立 ID 绑定 + 小域直接求和核验。"""
    print("=" * 70)
    print("演示 5：多实例批处理（不同变量数，各自保留 n 与次数上界）")
    print("=" * 70)
    # inst-a: f=1+x0*x1（2 变量，立方体和 5）
    # inst-b: f=3+2*x0+x1+x0*x2（3 变量）
    # inst-c: 仅 1 变量 f=x0（立方体和 1）
    instances = [
        BatchInstance("inst-a", build_poly(2, [(1, ()), (1, (0, 1))]), (2, 3)),
        BatchInstance("inst-b",
                      build_poly(3, [(3, ()), (2, (0,)), (1, (1,)), (1, (0, 2))]),
                      (17, 42, 3)),
        BatchInstance("inst-c", build_poly(1, [(1, (0,))]), (5,)),
    ]
    t0 = time.perf_counter()
    produced = prove_batch(instances)
    # 模拟 prover 与 verifier 分离：只交出 {ID: 转录}，verifier 独立重核
    report = verify_batch(instances, produced.transcript_by_id())
    elapsed = time.perf_counter() - t0
    print(report.pretty())
    print(f"整批结论 accepted={report.accepted}（真实计算耗时 {elapsed * 1000:.2f} ms）")
    assert report.accepted and report.count == 3

    print()
    print("-" * 70)
    print("边界：一次性 256 个 n=1 实例（批大小上限 MAX_BATCH_INSTANCES）")
    print("-" * 70)
    boundary = [
        BatchInstance(f"m{k:03d}", build_poly(1, [(1, (0,))]), (k % P,))
        for k in range(256)
    ]
    t0 = time.perf_counter()
    boundary_report = prove_batch(boundary)
    elapsed = time.perf_counter() - t0
    print(f"{boundary_report.accepted_count}/{boundary_report.count} 接受，"
          f"accepted={boundary_report.accepted}（真实计算耗时 {elapsed * 1000:.2f} ms）")
    assert boundary_report.accepted

    print()
    print("-" * 70)
    print("失败用例 1：篡改 inst-b 第 1 轮一次项系数 —— 整批失败，inst-a/inst-c 仍接受")
    print("-" * 70)
    mapping = produced.transcript_by_id()
    tr_b = mapping["inst-b"]
    msg = tr_b.rounds[1]
    mapping["inst-b"] = replace(
        tr_b,
        rounds=tuple(
            [replace(msg, coeffs=(msg.coeffs[0], (msg.coeffs[1] + 1) % P))
             if i == 1 else m for i, m in enumerate(tr_b.rounds)]
        ),
    )
    fail_report = verify_batch(instances, mapping)
    print(f"整批 accepted={fail_report.accepted}，"
          f"接受 {fail_report.accepted_count}/{fail_report.count}")
    print(fail_report.by_id()["inst-b"].pretty())
    assert not fail_report.accepted
    assert fail_report.by_id()["inst-a"].accepted
    assert fail_report.by_id()["inst-c"].accepted

    print()
    print("-" * 70)
    print("失败用例 2：跨实例冒用 —— 把 inst-c 的转录当作 inst-a 提交（ID 绑定拦截）")
    print("-" * 70)
    good_map = produced.transcript_by_id()
    swapped = {"inst-a": good_map["inst-c"], "inst-b": good_map["inst-b"],
               "inst-c": good_map["inst-a"]}
    try:
        verify_batch(instances, swapped)
    except BatchError as exc:
        print(f"如预期抛出 BatchError：{exc}")
    else:  # pragma: no cover - 演示断言
        raise AssertionError("跨实例冒用必须被拒绝")

    print()
    print("-" * 70)
    print("失败用例 3：小域直接求和抓假 —— f=x0、挑战 r0=5，作弊 g0(t)≡5、主张 10")
    print("-" * 70)
    # 闭合：g(0)+g(1)=10；最终：g(5)=5=f(5)，固定挑战下单实例 verify 会被绕过；
    # 但真实立方体和为 f(0)+f(1)=1，批处理直接枚举求和必然拒绝。
    cheat_poly = build_poly(1, [(1, (0,))])
    cheat_tr = Transcript(
        1, 10, (RoundMessage(0, (5,), 10, None, None),), (5,), 5,
        instance_id="cheat",
    )
    single_accepts = verify(cheat_poly, cheat_tr).accepted
    cheat_report = verify_batch(
        [BatchInstance("cheat", cheat_poly, (5,))], {"cheat": cheat_tr}
    )
    print(f"旧单实例 verify 结论 accepted={single_accepts}（固定挑战、无独立求和）")
    print(cheat_report.by_id()["cheat"].pretty())
    assert single_accepts and not cheat_report.accepted

    print()
    print("-" * 70)
    print("失败用例 4：非法批次结构 —— 空批次 / 重复实例 ID")
    print("-" * 70)
    for bad, label in (([], "空批次"), (instances[:1] + instances[:1], "重复 ID")):
        try:
            prove_batch(bad)
        except BatchError as exc:
            print(f"{label} -> BatchError：{exc}")
        else:  # pragma: no cover - 演示断言
            raise AssertionError(f"{label} 必须被拒绝")


if __name__ == "__main__":
    main()
