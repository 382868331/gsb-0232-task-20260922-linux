"""独立参考实现（仅供测试使用）。

本文件刻意不 import 被测核心 ``sumcheck``：F_101 算术、布尔立方体枚举、
逐轮 g_i 的定义都在这里独立重写一遍，作为计算期望值的预言机。
被测核心只允许与本参考的结论对拍；“往返一致”类测试仅作为补充。

多项式规格 spec = (n, terms)，terms 为 [(coeff, (var_index, ...)), ...]，
允许同变量集合重复项（独立做模加合并），以与核心的“拒绝重复项”契约区分。
"""

from __future__ import annotations

P = 101


def _norm(x: int) -> int:
    return x % P


def ref_eval(spec, point) -> int:
    """直接按定义在任意 F_p 点求值。"""
    n, terms = spec
    assert len(point) == n
    total = 0
    for coeff, vars_ in terms:
        product = 1
        for i in vars_:
            product = _norm(product * point[i])
        total = _norm(total + _norm(coeff) * product)
    return _norm(total)


def ref_boolean_sum(spec) -> int:
    """全枚举 {0,1}^n 求和（独立实现，迭代位掩码）。"""
    n, _ = spec
    total = 0
    for mask in range(1 << n):
        point = tuple((mask >> j) & 1 for j in range(n))
        total = _norm(total + ref_eval(spec, point))
    return total


def _fixed_eval(spec, fixed: dict) -> int:
    """对未固定变量全部取布尔枚举；固定变量取给定域值。返回求和。"""
    n, _ = spec
    free = [j for j in range(n) if j not in fixed]
    total = 0
    for mask in range(1 << len(free)):
        point = [0] * n
        for k, j in enumerate(free):
            point[j] = (mask >> k) & 1
        for j, v in fixed.items():
            point[j] = v
        total = _norm(total + ref_eval(spec, tuple(point)))
    return total


def ref_round_linear(spec, prior_challenges) -> tuple[int, int]:
    """独立计算第 i 轮（i=len(prior_challenges)）关于当前变量的 g_i。

    返回 (a0, a1)：g_i(t)=a0+a1*t。其中
      a0 = g_i(0) = Σ_{剩余布尔位} f(前序挑战, 0, 布尔位)
      a1 = g_i(1)-g_i(0)
    """
    i = len(prior_challenges)
    fixed0 = {j: r for j, r in enumerate(prior_challenges)}
    fixed0[i] = 0
    fixed1 = {j: r for j, r in enumerate(prior_challenges)}
    fixed1[i] = 1
    a0 = _fixed_eval(spec, fixed0)
    a1 = _norm(_fixed_eval(spec, fixed1) - a0)
    return a0, a1


def ref_expected_transcript_values(spec, challenges) -> dict:
    """逐轮给出期望值：系数、入主张、出主张，以及初始/最终主张与直接点值。"""
    n, _ = spec
    initial = ref_boolean_sum(spec)
    rounds = []
    prev = initial
    for i in range(n):
        a0, a1 = ref_round_linear(spec, challenges[:i])
        rounds.append({
            "coeffs": (a0, a1),
            "input_claim": prev,
            "g0_plus_g1": _norm(a0 + (a0 + a1)),
        })
        prev = _norm(a0 + a1 * challenges[i])
        rounds[-1]["output_claim"] = prev
    final_claim = prev
    direct = ref_eval(spec, tuple(challenges))
    return {
        "initial_claim": initial,
        "rounds": rounds,
        "final_claim": final_claim,
        "direct_evaluation": direct,
    }
