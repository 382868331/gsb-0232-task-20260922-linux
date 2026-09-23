"""测试专用独立参考实现。

本模块**不得导入 sumcheck 包**：期望值全部由这里的独立代码经布尔立方体
枚举与独立模运算得到，避免“用被测核心算期望值再与被测核心比对”。

多项式用最朴素的字典表示：``{"coeff": int, "vars": tuple[int, ...]}``，
参考实现自己做模 101 运算。
"""

P = 101


def _eval_term(term, point):
    value = term["coeff"] % P
    for j in term["vars"]:
        value = (value * point[j]) % P
    return value


def eval_poly(terms, point):
    """在给定域点上独立求值（点元素允许任意整数，内部自行取模）。"""
    total = 0
    for term in terms:
        total = (total + _eval_term(term, tuple(x % P for x in point))) % P
    return total


def cube_sum(terms, n):
    """全枚举 {0,1}^n 求和，n<=8。"""
    total = 0
    for mask in range(1 << n):
        point = tuple((mask >> (n - 1 - k)) & 1 for k in range(n)) if n else ()
        total = (total + eval_poly(terms, point)) % P
    return total


def round_values(terms, n, prefix, j):
    """独立计算第 j 轮一元多项式 g_j 在 0 和 1 处的值。

    g_j(X) = Σ_{b_{j+1}..b_n} f(prefix..., X, b...)，逐点枚举后缀立方体。
    返回 (g(0), g(1))。
    """
    suffix_len = n - j - 1
    results = []
    for x_value in (0, 1):
        total = 0
        for mask in range(1 << suffix_len):
            point = list(prefix) + [x_value]
            point.extend((mask >> (suffix_len - 1 - k)) & 1 for k in range(suffix_len))
            total = (total + eval_poly(terms, point)) % P
        results.append(total)
    return results[0], results[1]


def fold(value0, value1):
    return (value0 + value1) % P
