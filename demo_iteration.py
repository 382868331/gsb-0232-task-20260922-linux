#!/usr/bin/env python3
"""部分求和协议演示：真实计算正常接受与边界拒绝（离线、无 sleep）。

运行：python demo_iteration.py
"""

from __future__ import annotations

import time
from dataclasses import replace

from sumcheck import (
    MultilinearPoly,
    ProtocolError,
    Term,
    prove,
    prove_partial,
    verify,
    verify_partial,
)


def make_poly(n, raw_terms):
    return MultilinearPoly(n, [Term(c, tuple(v)) for c, v in raw_terms])


def main() -> None:
    started = time.perf_counter()
    print("=" * 72)
    print("演示 1：非连续显式顺序的部分求和 —— 保留变量不被求和")
    print("=" * 72)
    # f = 5 + 2*x0 + 3*x1 + 4*x0*x3 + 6*x3*x4 + 7*x1*x4
    poly = make_poly(
        5,
        [
            (5, ()),
            (2, (0,)),
            (3, (1,)),
            (4, (0, 3)),
            (6, (3, 4)),
            (7, (1, 4)),
        ],
    )
    sum_variables = (3, 0)
    retained_point = {1: 17, 2: 42, 4: 9}
    challenges = (60, 25)
    transcript = prove_partial(poly, sum_variables, retained_point, challenges)
    result = verify_partial(
        poly,
        transcript,
        sum_variables=sum_variables,
        retained_point=retained_point,
    )
    print(transcript.pretty())
    print(f"结论: accepted={result.accepted}，直接求值={result.direct_evaluation}")
    assert result.accepted

    print()
    print("=" * 72)
    print("演示 2：空求和集合 —— 退化为一次公开点直接求值检查")
    print("=" * 72)
    empty_point = {0: 5, 1: 6, 2: 7, 3: 8, 4: 9}
    empty_transcript = prove_partial(poly, (), empty_point, ())
    empty_result = verify_partial(poly, empty_transcript)
    print(f"轮数={len(empty_transcript.rounds)}，主张={empty_transcript.initial_claim}，"
          f"直接求值={empty_result.direct_evaluation}")
    print(f"结论: accepted={empty_result.accepted}")
    assert empty_result.accepted and empty_result.rounds_checked == 0

    print()
    print("=" * 72)
    print("演示 3：换一种变量分区 —— 同一份证明不能复用")
    print("=" * 72)
    try:
        verify_partial(
            poly,
            transcript,
            sum_variables=(0, 3),
            retained_point=retained_point,
        )
    except ProtocolError as exc:
        print(f"如预期拒绝：{exc}")
    else:
        raise AssertionError("错误分区必须被拒绝")

    print()
    print("=" * 72)
    print("演示 4：篡改公开点 —— 最终直接点值检查拒绝")
    print("=" * 72)
    tampered = replace(
        transcript,
        retained_point=(
            (transcript.retained_point[0] + 1) % 101,
            transcript.retained_point[1],
            transcript.retained_point[2],
        ),
    )
    tampered_result = verify_partial(poly, tampered)
    print(f"原公开点={transcript.retained_point}，篡改后={tampered.retained_point}")
    print(f"结论: accepted={tampered_result.accepted}，"
          f"failed_round={tampered_result.failed_round}")
    print(f"原因: {tampered_result.reason}")
    assert not tampered_result.accepted

    print()
    print("=" * 72)
    print("演示 5：原全变量求和入口保持可用")
    print("=" * 72)
    full_challenges = (17, 42, 3, 60, 91)
    full_transcript = prove(poly, full_challenges)
    full_result = verify(poly, full_transcript)
    print(f"全变量初始主张={full_transcript.initial_claim}，"
          f"最终主张={full_transcript.final_claim}，"
          f"accepted={full_result.accepted}")
    assert full_result.accepted

    elapsed = time.perf_counter() - started
    print()
    print(f"全部演示真实计算完成，总耗时 {elapsed:.3f} 秒（预算 90 秒）。")


if __name__ == "__main__":
    main()
