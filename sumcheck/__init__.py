"""sumcheck: 素域 F_101 上的多线性多项式 sum-check 教学库。

公开接口见 :mod:`sumcheck` 包的 ``__init__``。
本库只做教学用途：prover 直接枚举布尔立方体，不提供零知识性，
也不具备任何生产级密码学安全性。
"""

from .errors import (
    BatchError,
    BudgetExhausted,
    PolynomialError,
    ProtocolError,
    SumcheckError,
    TranscriptFormatError,
)
from .field import P
from .polynomial import MAX_TERMS, MAX_VARS, MultilinearPoly, Term
from .batch import (
    MAX_INSTANCES,
    BatchInstance,
    BatchResult,
    BatchTranscript,
    BoundTranscript,
    InstanceResult,
    independent_boolean_sum,
    prove_batch,
    verify_batch,
)
from .protocol import (
    BUDGET_NOTE,
    RoundMessage,
    Transcript,
    VerifyResult,
    prove,
    verify,
)

__all__ = [
    "P",
    "MAX_VARS",
    "MAX_TERMS",
    "Term",
    "MultilinearPoly",
    "PolynomialError",
    "RoundMessage",
    "Transcript",
    "VerifyResult",
    "prove",
    "verify",
    "SumcheckError",
    "ProtocolError",
    "TranscriptFormatError",
    "BudgetExhausted",
    "BatchError",
    "BUDGET_NOTE",
    # 多实例批处理
    "MAX_INSTANCES",
    "BatchInstance",
    "BatchTranscript",
    "BoundTranscript",
    "InstanceResult",
    "BatchResult",
    "prove_batch",
    "verify_batch",
    "independent_boolean_sum",
]
