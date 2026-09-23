#!/usr/bin/env python3
"""固定输入的 sum-check 演示。

运行：``python demo.py``

真实计算两部分（无 sleep、无预录结果）：
1. 正常情形：对一个 3 变量多线性多项式，用调用方固定的挑战数组
   (2, 9, 4) 跑 prover/verifier，逐轮打印转录，最后 verifier 接受；
2. 篡改边界：把同一证明第 2 轮的一次项系数改 1（仍在域内、次数仍合法），
   verifier 必须拒绝，并报告首个失败轮为第 2 轮。
"""

from sumcheck import (
    FIELD_P,
    MultilinearPolynomial,
    Proof,
    Term,
    prove,
    verify,
)

# 固定演示多项式：f(x0,x1,x2) = 11*x0*x2 + 13*x1 + 17*x0*x1*x2
DEMO_TERMS = [
    Term(11, (0, 2)),
    Term(13, (1,)),
    Term(17, (0, 1, 2)),
]
DEMO_CHALLENGES = (2, 9, 4)


def print_transcript(result):
    for rec in result.transcript:
        print(
            f"    轮 {rec.round} (变量 x{rec.variable}): "
            f"g(X) = {rec.coeffs[0]} + {rec.coeffs[1]}*X, "
            f"g(0)+g(1) = {rec.g_at_zero}+{rec.g_at_one} "
            f"= {(rec.g_at_zero + rec.g_at_one) % FIELD_P} "
            f"== 上一主张 {rec.expected_claim}, "
            f"挑战 r={rec.challenge} -> 新主张 {rec.next_claim}"
        )


def main():
    poly = MultilinearPolynomial(DEMO_TERMS)
    print(f"演示多项式（GF({FIELD_P}) 上，{poly.num_vars} 个变量）:")
    print("  f(x0,x1,x2) = 11*x0*x2 + 13*x1 + 17*x0*x1*x2")
    print(f"调用方固定挑战: {DEMO_CHALLENGES}")
    print()

    # ---- 1. 正常证明，verifier 接受 ----
    print("[1] 诚实 prover 出证：")
    output = prove(poly, DEMO_CHALLENGES)
    print(f"  主张 C = Σ_b f(b) = {output.claimed_sum}  (布尔立方体 2^3 点枚举)")
    result = verify(poly, output.claimed_sum, DEMO_CHALLENGES, output.proof)
    print_transcript(result)
    print(
        f"  终末直接计算 f{DEMO_CHALLENGES} = {result.final_value}，"
        f"与最后主张一致"
    )
    print(f"  => verifier 裁定: accepted={result.accepted} ({result.message})")
    assert result.accepted, "诚实证明必须被接受"
    print()

    # ---- 2. 篡改第 2 轮系数，verifier 拒绝 ----
    print("[2] 边界演示：篡改第 2 轮一次项系数 (+1 mod 101)：")
    tampered = [list(r) for r in output.proof.rounds]
    old = tampered[1][1]
    tampered[1][1] = (old + 1) % FIELD_P
    print(f"  第 2 轮系数 {old} -> {tampered[1][1]}（仍在域内、次数仍为 1）")
    bad_result = verify(poly, output.claimed_sum, DEMO_CHALLENGES, Proof(tampered))
    print(f"  => verifier 裁定: accepted={bad_result.accepted}")
    print(
        f"     首个失败轮: {bad_result.failed_round}，"
        f"原因码: {bad_result.reason_code}"
    )
    print(f"     说明: {bad_result.message}")
    print(f"     转录仅保留失败轮之前 {len(bad_result.transcript)} 轮")
    assert not bad_result.accepted and bad_result.failed_round == 2
    print()
    print("演示结束：正常证明被接受，被篡改的证明在第 2 轮被实际拒绝。")


if __name__ == "__main__":
    main()
