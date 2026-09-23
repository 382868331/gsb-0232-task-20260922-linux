"""多线性多项式表示与求值。

多项式以显式多线性项列表给出：``f = Σ coefficient * Π_{i in vars} X_i``，
变量以 0 基下标引用（变量 0 即 X_0）。每项中同一变量至多出现一次
（多线性），合并重复项由构造器负责（拒绝而不是静默合并）。
"""

from __future__ import annotations

from .errors import PolynomialError
from .field import P, check_field_element

MAX_VARS = 8
MAX_TERMS = 100


class Term:
    """一个多线性单项式 ``coefficient * product(X_i for i in variables)``。

    常量项使用空变量集合。字段：

    coeff: int
        F_p 规范表示的系数，``[0, p-1]``；
    variables: frozenset[int]
        出现的变量下标集合（无序、去重、不可变）。
    """

    __slots__ = ("coeff", "variables")

    def __init__(self, coeff, variables):
        self.coeff = check_field_element(coeff, "Term 系数")
        if isinstance(variables, int):
            raise TypeError("variables 必须是可迭代的变量下标集合，不能是单个整数")
        try:
            indices = frozenset(variables)
        except TypeError as exc:
            raise TypeError("variables 必须是可迭代的变量下标集合") from exc
        for i in indices:
            if isinstance(i, bool) or not isinstance(i, int):
                raise PolynomialError(
                    f"Term 变量下标必须为非 bool 整数，实际为 {type(i).__name__}: {i!r}"
                )
            if not 0 <= i < MAX_VARS:
                raise PolynomialError(
                    f"Term 变量下标 {i} 越界，允许区间 [0, {MAX_VARS - 1}]"
                )
        self.variables = indices

    def degree_in(self, var: int) -> int:
        """该变量在本项中的次数：0 或 1。"""
        return 1 if var in self.variables else 0

    def __eq__(self, other):
        return (
            isinstance(other, Term)
            and self.coeff == other.coeff
            and self.variables == other.variables
        )

    def __hash__(self):
        return hash((self.coeff, self.variables))

    def __repr__(self):
        vars_text = ",".join(str(i) for i in sorted(self.variables))
        return f"Term({self.coeff}, {{{vars_text}}})"


class MultilinearPoly:
    """F_p 上至多 :data:`MAX_VARS` 个变量、至多 :data:`MAX_TERMS` 项的多线性多项式。

    构造参数 ``num_vars`` 必须为非 bool 的 ``int``，且 ``1 <= num_vars <= 8``。
    空变量多项式（``num_vars=0``）在协议层被拒绝，错误信息明确指出问题；
    此类本身允许 0 变量（仅含一个常量项），便于独立测试与求值。
    """

    __slots__ = ("num_vars", "_terms", "_nonzero")

    def __init__(self, num_vars, terms):
        if isinstance(num_vars, bool) or not isinstance(num_vars, int):
            raise PolynomialError(
                f"num_vars 必须为非 bool 整数，实际为 {type(num_vars).__name__}"
            )
        if not 0 <= num_vars <= MAX_VARS:
            raise PolynomialError(
                f"num_vars={num_vars} 越界，允许区间 [0, {MAX_VARS}]"
                f"（sum-check 协议要求至少 1 个变量）"
            )
        if isinstance(terms, (str, bytes)):
            raise TypeError("terms 必须是 Term 的可迭代列表")
        try:
            term_list = list(terms)
        except TypeError as exc:
            raise TypeError("terms 必须是 Term 的可迭代列表") from exc
        if len(term_list) > MAX_TERMS:
            raise PolynomialError(
                f"项数 {len(term_list)} 超过上限 {MAX_TERMS}"
            )
        seen = {}
        for pos, term in enumerate(term_list):
            if not isinstance(term, Term):
                raise PolynomialError(
                    f"terms[{pos}] 必须为 Term，实际为 {type(term).__name__}"
                )
            out_of_range = [i for i in term.variables if i >= num_vars]
            if out_of_range:
                raise PolynomialError(
                    f"terms[{pos}] 引用了超出 num_vars={num_vars} 的变量下标 "
                    f"{sorted(out_of_range)}"
                )
            if term.variables in seen:
                first = seen[term.variables]
                raise PolynomialError(
                    f"terms[{pos}] 与 terms[{first}] 是同一变量集合的重复项；"
                    "请在构造前合并同类项，库不做静默纠正"
                )
            seen[term.variables] = pos
        self.num_vars = num_vars
        self._terms = tuple(term_list)
        # 零系数项保留在列表中（调用者显式给出），但求值时跳过；
        # degree_of / round 多项式构造时同样忽略，避免虚高次数。
        self._nonzero = tuple(t for t in self._terms if t.coeff != 0)

    @property
    def terms(self) -> tuple[Term, ...]:
        """构造时给出的全部项（按原顺序，含显式零系数项）。"""
        return self._terms

    def degree_of(self, var: int) -> int:
        """变量 ``var`` 的实际次数（0 或 1）；下标非法时抛出 :class:`PolynomialError`。"""
        if isinstance(var, bool) or not isinstance(var, int) or not 0 <= var < self.num_vars:
            raise PolynomialError(
                f"变量下标 {var!r} 非法，num_vars={self.num_vars}"
            )
        return max((t.degree_in(var) for t in self._nonzero), default=0)

    def evaluate(self, point) -> int:
        """在一个点上求值。

        ``point`` 为长度 ``num_vars`` 的序列，元素可为任意 F_p 整数
        （协议最后一步使用挑战值；参考实现枚举时使用 0/1）。
        纯整数算术，不存在浮点误差。
        """
        if isinstance(point, (str, bytes)) or not hasattr(point, "__len__"):
            raise TypeError("point 必须为长度 num_vars 的序列")
        if len(point) != self.num_vars:
            raise PolynomialError(
                f"point 长度 {len(point)} 与 num_vars={self.num_vars} 不一致"
            )
        values = [
            check_field_element(v, f"point[{i}]") for i, v in enumerate(point)
        ]
        total = 0
        for term in self._nonzero:
            product = 1
            for i in term.variables:
                product = (product * values[i]) % P
            total = (total + term.coeff * product) % P
        return total

    def restricted(self, assignments: dict) -> "MultilinearPoly":
        """返回把部分变量固定为域元素后的新多项式（剩余变量重新压缩下标）。

        ``assignments``: ``{旧变量下标: 域元素}``。新多项式按未赋值变量的
        升序重新编号（赋值顺序不影响结果），与 sum-check 逐轮剥离一致。
        """
        if not isinstance(assignments, dict):
            raise TypeError("assignments 必须为 dict")
        fixed = {}
        for var, val in assignments.items():
            if isinstance(var, bool) or not isinstance(var, int) or not 0 <= var < self.num_vars:
                raise PolynomialError(f"assignments 含非法变量下标 {var!r}")
            fixed[var] = check_field_element(val, f"assignments[{var}]")
        remaining = [v for v in range(self.num_vars) if v not in fixed]
        reindex = {old: new for new, old in enumerate(remaining)}
        merged: dict[frozenset, int] = {}
        for term in self._nonzero:
            coeff = term.coeff
            new_vars = []
            dead = False
            for var in term.variables:
                if var in fixed:
                    value = fixed[var]
                    if value == 0:
                        dead = True
                        break
                    coeff = (coeff * value) % P
                else:
                    new_vars.append(reindex[var])
            if dead:
                continue
            key = frozenset(new_vars)
            merged[key] = (merged.get(key, 0) + coeff) % P
        new_terms = [Term(coeff, vars_) for vars_, coeff in merged.items() if coeff != 0]
        return MultilinearPoly(len(remaining), new_terms)

    def boolean_sum(self, eval_budget: int | None = None) -> int:
        """枚举整个布尔立方体 {0,1}^n 求和，返回 F_p 中的值。

        ``eval_budget`` 为允许的求值点上限；立方体大小 2^n 超过预算时抛出
        :class:`sumcheck.errors.BudgetExhausted`（资源不足，与“主张为假”区分）。
        """
        size = 1 << self.num_vars
        if eval_budget is not None:
            if isinstance(eval_budget, bool) or not isinstance(eval_budget, int):
                raise TypeError("eval_budget 必须为非 bool 整数或 None")
            if eval_budget < 0:
                raise PolynomialError("eval_budget 不可为负")
            if size > eval_budget:
                from .errors import BudgetExhausted

                raise BudgetExhausted(
                    f"布尔立方体含 {size} 个求值点，超过预算 eval_budget={eval_budget}"
                )
        total = 0
        point = [0] * self.num_vars
        for mask in range(size):
            for j in range(self.num_vars):
                point[j] = (mask >> j) & 1
            total = (total + self.evaluate(point)) % P
        return total
