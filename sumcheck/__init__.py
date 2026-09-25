"""sumcheck: 素域 F_101 上的多线性多项式 sum-check 教学库。

公开接口见 :mod:`sumcheck` 包的 ``__init__``。
本库只做教学用途：prover 直接枚举布尔立方体，不提供零知识性，
也不具备任何生产级密码学安全性。
"""

from .errors import (
    BudgetExhausted,
    PolynomialError,
    ProtocolError,
    StreamStateError,
    SumcheckError,
    TranscriptFormatError,
    VerificationFailed,
)
from .field import P
from .polynomial import MAX_TERMS, MAX_VARS, MultilinearPoly, Term
from .protocol import (
    BUDGET_NOTE,
    RoundMessage,
    Transcript,
    VerifyResult,
    prove,
    verify,
)
from .streaming import (
    ACCEPTED,
    AWAITING_CHALLENGE,
    AWAITING_POLY,
    REJECTED,
    StreamState,
    StreamingVerifier,
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
    "StreamStateError",
    "VerificationFailed",
    "StreamingVerifier",
    "StreamState",
    "AWAITING_POLY",
    "AWAITING_CHALLENGE",
    "ACCEPTED",
    "REJECTED",
    "BUDGET_NOTE",
]
