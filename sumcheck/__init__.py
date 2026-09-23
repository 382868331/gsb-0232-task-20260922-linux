"""sum-check 教学库：素域 GF(101) 上的多线性多项式求和证明。

公开接口见 :mod:`sumcheck` 包的 ``__init__``。
本模块只使用 Python 3.14 标准库，全部运算为精确整数模运算，不使用浮点。
"""

from ._errors import (
    ChallengeError,
    FieldValueError,
    PolynomialError,
    ProofFormatError,
    SumCheckError,
)
from ._field import FIELD_P, check_field_int, field_add, field_mul, field_sub
from ._poly import MultilinearPolynomial, Term
from ._protocol import (
    Proof,
    ProverOutput,
    RoundRecord,
    VerificationResult,
    prove,
    run_sumcheck,
    verify,
)

__all__ = [
    "FIELD_P",
    "SumCheckError",
    "FieldValueError",
    "PolynomialError",
    "ChallengeError",
    "ProofFormatError",
    "Term",
    "MultilinearPolynomial",
    "Proof",
    "ProverOutput",
    "RoundRecord",
    "VerificationResult",
    "prove",
    "verify",
    "run_sumcheck",
    "check_field_int",
    "field_add",
    "field_sub",
    "field_mul",
]
