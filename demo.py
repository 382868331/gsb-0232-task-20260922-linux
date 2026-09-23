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
    MultilinearPoly,
    Term,
    TranscriptFormatError,
    prove,
    verify,
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
    print("全部演示完成：1 个正常接受、1 个上限规模接受、1 个篡改拒绝、2 个结构异常。")


if __name__ == "__main__":
    main()
