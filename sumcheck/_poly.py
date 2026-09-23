"""GF(101) 上的显式多线性多项式。

多项式表示为项（:class:`Term`）的列表：每项是系数乘以若干**不同**变量，
每个变量在单项中至多出现一次，因此整个多项式对每个变量次数不超过 1。

接口边界（题目契约）：
- 变量数 1..8 为一般情形，``num_vars=0`` 表示零元（常数）多项式，合法；
- 项数 1..100；
- 重复项（变量集合相同）与系数为 0 的项直接报错，不做静默合并/丢弃；
- 同一项内变量下标重复直接报错。
"""

from __future__ import annotations

from ._errors import PolynomialError
from ._field import FIELD_P, check_field_int

MAX_VARS = 8
MAX_TERMS = 100


class Term:
    """一个多线性单项 ``coeff * product(x_j for j in variables)``。

    ``variables`` 为空 tuple 表示常数项。构造时校验系数与下标类型，
    变量下标的非负/上界检查在 :class:`MultilinearPolynomial` 中完成
    （项本身不知道变量总数）。
    """

    __slots__ = ("coeff", "variables")

    def __init__(self, coeff: int, variables):
        c = check_field_int(coeff, what="term coeff")
        vars_tuple = tuple(variables)
        seen: set[int] = set()
        for j, v in enumerate(vars_tuple):
            if isinstance(v, bool) or not isinstance(v, int):
                raise PolynomialError(
                    f"term variable[{j}]: expected non-bool int index, "
                    f"got {type(v).__name__} {v!r}"
                )
            if v < 0:
                raise PolynomialError(f"term variable[{j}]: negative index {v}")
            if v in seen:
                raise PolynomialError(f"term variable[{j}]: duplicated variable {v} in one term")
            seen.add(v)
        # 规范化：变量下标升序，保证两项的“变量集合相同”可直接按 tuple 比较。
        vars_tuple = tuple(sorted(vars_tuple))
        self.coeff = c
        self.variables = vars_tuple

    def __eq__(self, other):
        return (
            isinstance(other, Term)
            and self.coeff == other.coeff
            and self.variables == other.variables
        )

    def __hash__(self):
        return hash((self.coeff, self.variables))

    def __repr__(self):
        return f"Term({self.coeff}, {self.variables!r})"


def _check_nonbool_int(value, what: str, *, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolynomialError(
            f"{what}: expected non-bool int, got {type(value).__name__} {value!r}"
        )
    if maximum is not None and value > maximum:
        raise PolynomialError(f"{what}: {value} exceeds limit {maximum}")
    return value


class MultilinearPolynomial:
    """GF(101) 上的显式多线性多项式，项在构造时全部校验后一次性入库。"""

    def __init__(self, terms, num_vars: int | None = None):
        raw = list(terms)
        if len(raw) > MAX_TERMS:
            raise PolynomialError(f"number of terms {len(raw)} exceeds limit {MAX_TERMS}")

        supports: set[tuple[int, ...]] = set()
        cleaned: list[Term] = []
        for idx, term in enumerate(raw):
            if not isinstance(term, Term):
                raise PolynomialError(
                    f"terms[{idx}]: expected Term, got {type(term).__name__}"
                )
            if term.coeff == 0:
                # 不静默丢弃零项：调用方应当从输入中移除，错误可定位到项下标。
                raise PolynomialError(f"terms[{idx}]: zero-coefficient term is not accepted")
            if term.variables in supports:
                raise PolynomialError(
                    f"terms[{idx}]: duplicated monomial support {term.variables}"
                )
            supports.add(term.variables)
            cleaned.append(term)

        if num_vars is None:
            if not cleaned:
                raise PolynomialError(
                    "num_vars cannot be inferred from an empty term list; "
                    "pass num_vars= explicitly"
                )
            max_index = max(
                (term.variables[-1] for term in cleaned if term.variables),
                default=-1,
            )
            n = max_index + 1
            if n > MAX_VARS:
                raise PolynomialError(
                    f"num_vars inferred as {n} exceeds limit {MAX_VARS}"
                )
        else:
            n = _check_nonbool_int(num_vars, "num_vars")
            if n < 0:
                raise PolynomialError(f"num_vars: negative value {n}")
            if n > MAX_VARS:
                raise PolynomialError(f"num_vars {n} exceeds limit {MAX_VARS}")

        for idx, term in enumerate(cleaned):
            if term.variables and term.variables[-1] >= n:
                raise PolynomialError(
                    f"terms[{idx}]: variable index {term.variables[-1]} out of range "
                    f"for num_vars={n}"
                )

        cleaned.sort(key=lambda t: (t.variables, t.coeff))
        self._num_vars = n
        self._terms = tuple(cleaned)

    @property
    def num_vars(self) -> int:
        return self._num_vars

    @property
    def terms(self) -> tuple[Term, ...]:
        return self._terms

    def degree_in(self, var: int) -> int:
        """变量 ``var`` 在多项式中的次数：多线性情形只可能是 0 或 1。"""
        return 1 if any(var in t.variables for t in self._terms) else 0

    def evaluate(self, point) -> int:
        """在域点 ``point``（长度为 ``num_vars``）上求值，结果为 0..p-1。"""
        pt = tuple(point)
        if len(pt) != self._num_vars:
            raise PolynomialError(
                f"point length {len(pt)} != num_vars {self._num_vars}"
            )
        xs = [check_field_int(x, what="point", index=i) for i, x in enumerate(pt)]
        total = 0
        for term in self._terms:
            value = term.coeff
            for j in term.variables:
                value = (value * xs[j]) % FIELD_P
            total = (total + value) % FIELD_P
        return total

    def boolean_cube_sum(self) -> int:
        """枚举布尔立方体 {0,1}^n 求和（n<=8，至多 256 个点）。"""
        n = self._num_vars
        total = 0
        for mask in range(1 << n):
            point = tuple((mask >> (n - 1 - j)) & 1 for j in range(n))
            total = (total + self.evaluate(point)) % FIELD_P
        return total

    def __repr__(self):
        return f"MultilinearPolynomial(num_vars={self._num_vars}, terms={list(self._terms)!r})"
