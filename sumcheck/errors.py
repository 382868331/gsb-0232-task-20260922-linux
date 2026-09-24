"""公开错误类型。

所有本库抛出的异常都继承 :class:`SumcheckError`，便于调用者统一捕获。
"""

from __future__ import annotations


class SumcheckError(Exception):
    """sumcheck 库所有异常的公共基类。"""


class PolynomialError(SumcheckError, ValueError):
    """多项式输入不合法（越界、重复项、非法系数等）。"""


class ProtocolError(SumcheckError, ValueError):
    """协议级错误（转录结构/参数不合法）。"""


class TranscriptFormatError(ProtocolError):
    """转录在结构上不合法：轮数不符、系数次数超限、域外数、类型错误等。

    与“转录结构合法但主张为假”相区别：后者由
    :class:`sumcheck.protocol.VerifyResult` 以
    ``accepted=False`` 返回，而不是抛出异常。
    """


class BatchError(ProtocolError):
    """批次级错误：批次输入/批次转录封套不合法。

    典型场景：实例数为 0 或超限、实例 ID 缺失/重复/类型非法、
    :class:`~sumcheck.batch.BoundTranscript` 的 ID 与对应位置的实例不一致
    （把一份消息塞进另一个实例的槽位）等。
    与“某实例转录结构合法但主张为假”相区别：后者聚合进
    :class:`~sumcheck.batch.BatchResult`，不抛异常。
    """


class BudgetExhausted(SumcheckError, RuntimeError):
    """布尔立方体枚举的求值预算耗尽。

    注意与“证明无解/主张为假”区分：预算耗尽是调用者给的计算资源不足，
    证明本身可能完全正确；主张为假则由 verifier 返回拒绝结论。
    """
