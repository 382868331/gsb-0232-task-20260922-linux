"""sum-check 协议：prover、verifier 与公开转录结构。

协议（教科书式 sum-check，非零知识）：

    多项式 f(x_1..x_n) 多线性，主张 C = Σ_{b∈{0,1}^n} f(b) (mod p)。
    第 j 轮（变量下标 0 起，轮次 1 起）prover 发送一元多项式
        g_j(X) = Σ_{b_{j+1}..b_n ∈ {0,1}} f(r_1, ..., r_{j-1}, X, b...)，
    系数按次数升序给出 [c0, c1]，多线性保证 deg(g_j) ≤ 1。
    verifier 检查 g_j(0)+g_j(1) 等于上一轮主张（第 1 轮等于 C），
    然后用调用方给定的挑战 r_j 计算新主张 g_j(r_j)。
    最后一轮后 verifier 直接在 (r_1..r_n) 计算原多项式并与最后主张比较。

错误语义区分：
- 调用方把 API 用错（挑战长度不符、域外挑战、非法多项式/主张、证明轮数不符）
  抛出 ``SumCheckError`` 子类；
- 证明内容本身被证伪（系数被篡改、次数非法、域外系数、终值不符）不抛异常，
  返回 :class:`VerificationResult`，其中 ``accepted=False`` 并给出
  ``reason_code`` 与首个失败轮 ``failed_round``。
"""

from __future__ import annotations

from collections.abc import Sequence

from ._errors import ChallengeError, FieldValueError, PolynomialError, ProofFormatError
from ._field import FIELD_P, check_field_int
from ._poly import MultilinearPolynomial


class RoundRecord:
    """协议一轮的公开转录。"""

    __slots__ = (
        "round",
        "variable",
        "coeffs",
        "expected_claim",
        "g_at_zero",
        "g_at_one",
        "challenge",
        "next_claim",
    )

    def __init__(
        self,
        round: int,
        variable: int,
        coeffs: tuple[int, ...],
        expected_claim: int,
        g_at_zero: int,
        g_at_one: int,
        challenge: int,
        next_claim: int,
    ):
        self.round = round
        self.variable = variable
        self.coeffs = coeffs
        self.expected_claim = expected_claim
        self.g_at_zero = g_at_zero
        self.g_at_one = g_at_one
        self.challenge = challenge
        self.next_claim = next_claim

    def __repr__(self):
        return (
            f"RoundRecord(round={self.round}, variable=x{self.variable}, "
            f"coeffs={self.coeffs}, expected={self.expected_claim}, "
            f"g(0)={self.g_at_zero}, g(1)={self.g_at_one}, "
            f"challenge={self.challenge}, next={self.next_claim})"
        )


class Proof:
    """prover 输出：逐轮一元多项式系数（次数升序）。

    外层只做最浅的结构检查；系数类型、值域、次数合法性由 verifier
    逐轮裁定，以便篡改证明得到“首个失败轮”而不是构造期异常。
    """

    __slots__ = ("rounds",)

    def __init__(self, rounds):
        if isinstance(rounds, (str, bytes)) or not isinstance(rounds, Sequence):
            raise ProofFormatError(
                f"proof rounds: expected a sequence, got {type(rounds).__name__}"
            )
        self.rounds = tuple(rounds)

    def __repr__(self):
        return f"Proof({list(self.rounds)!r})"


class ProverOutput:
    def __init__(self, claimed_sum: int, proof: Proof):
        self.claimed_sum = claimed_sum
        self.proof = proof

    def __repr__(self):
        return f"ProverOutput(claimed_sum={self.claimed_sum}, proof={self.proof!r})"


class VerificationResult:
    """verifier 裁定结果。

    - ``accepted``：是否通过；
    - ``failed_round``：首个失败轮（1 起）；终值直接核验失败记 ``None``
      （见 ``reason_code == 'final'``），无失败时为 ``None``；
    - ``reason_code``：``'claim' | 'degree' | 'field' | 'format' | 'final'``；
    - ``final_value``：最后直接计算的原多项式点值（未走到终值阶段为 None）；
    - ``transcript``：成功完成的各轮转录（失败轮及其后无记录）。
    """

    __slots__ = (
        "accepted",
        "claimed_sum",
        "failed_round",
        "reason_code",
        "message",
        "transcript",
        "final_value",
    )

    def __init__(self, *, claimed_sum, accepted, failed_round, reason_code,
                 message, transcript, final_value):
        self.claimed_sum = claimed_sum
        self.accepted = accepted
        self.failed_round = failed_round
        self.reason_code = reason_code
        self.message = message
        self.transcript = tuple(transcript)
        self.final_value = final_value

    def __repr__(self):
        if self.accepted:
            return f"VerificationResult(accepted=True, claimed_sum={self.claimed_sum})"
        return (
            f"VerificationResult(accepted=False, failed_round={self.failed_round}, "
            f"reason_code={self.reason_code!r}, message={self.message!r})"
        )


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def _validate_poly(poly) -> MultilinearPolynomial:
    if not isinstance(poly, MultilinearPolynomial):
        raise PolynomialError(
            f"poly: expected MultilinearPolynomial, got {type(poly).__name__}"
        )
    return poly


def _validate_challenges(challenges, n: int) -> tuple[int, ...]:
    if isinstance(challenges, (str, bytes)) or not isinstance(challenges, Sequence):
        raise ChallengeError(
            f"challenges: expected a sequence of {n} field elements, "
            f"got {type(challenges).__name__}"
        )
    if len(challenges) != n:
        raise ChallengeError(
            f"challenges length {len(challenges)} != num_vars {n}"
        )
    return tuple(
        check_field_int(r, what="challenge", index=j)
        for j, r in enumerate(challenges)
    )


# ---------------------------------------------------------------------------
# Prover
# ---------------------------------------------------------------------------


def _suffix_sum(poly: MultilinearPolynomial, prefix: tuple[int, ...],
                j: int, x_value: int) -> int:
    """枚举第 j 个变量之后的布尔后缀求和：

        Σ_{b_{j+1}..} f(prefix[0..j-1], x_value, b...)  (mod p)
    """
    n = poly.num_vars
    suffix_len = n - j - 1
    total = 0
    for mask in range(1 << suffix_len):
        point = list(prefix)
        point.append(x_value)
        for k in range(suffix_len):
            point.append((mask >> (suffix_len - 1 - k)) & 1)
        total = (total + poly.evaluate(point)) % FIELD_P
    return total


def prove(poly: MultilinearPolynomial, challenges) -> ProverOutput:
    """诚实 prover：输入多项式与调用方固定的挑战数组，生成 sum-check 证明。

    挑战长度必须等于变量数，否则抛 :class:`ChallengeError`；
    挑战元素必须是规范域元素，否则抛 :class:`FieldValueError`。
    """
    poly = _validate_poly(poly)
    n = poly.num_vars
    rs = _validate_challenges(challenges, n)

    claimed_sum = poly.boolean_cube_sum()
    if n == 0:
        return ProverOutput(claimed_sum, Proof(()))

    coeff_rounds: list[tuple[int, int]] = []
    prefix: tuple[int, ...] = ()
    for j in range(n):
        g0 = _suffix_sum(poly, prefix, j, 0)
        g1 = _suffix_sum(poly, prefix, j, 1)
        c0 = g0
        c1 = (g1 - g0) % FIELD_P  # 多线性 ⇒ g_j 至多一次
        coeff_rounds.append((c0, c1))
        prefix = prefix + (rs[j],)

    return ProverOutput(claimed_sum, Proof(tuple(coeff_rounds)))


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------


def _reject(claimed_sum, failed_round, reason_code, message, transcript,
            final_value=None) -> VerificationResult:
    return VerificationResult(
        claimed_sum=claimed_sum,
        accepted=False,
        failed_round=failed_round,
        reason_code=reason_code,
        message=message,
        transcript=transcript,
        final_value=final_value,
    )


def verify(poly: MultilinearPolynomial, claimed_sum, challenges,
           proof: Proof) -> VerificationResult:
    """verifier：逐轮核验并在终末直接计算原多项式点值。

    API 级输入错误抛异常；证明内容错误返回 ``accepted=False`` 的结果，
    转录停在首个失败轮之前。
    """
    poly = _validate_poly(poly)
    n = poly.num_vars
    original_claim = check_field_int(claimed_sum, what="claimed_sum")
    claim = original_claim
    rs = _validate_challenges(challenges, n)

    if not isinstance(proof, Proof):
        raise ProofFormatError(
            f"proof: expected Proof, got {type(proof).__name__}"
        )
    if len(proof.rounds) != n:
        raise ProofFormatError(
            f"proof rounds {len(proof.rounds)} != num_vars {n}"
        )

    transcript: list[RoundRecord] = []

    # 零变量：没有轮次，主张必须直接等于常数多项式的值。
    if n == 0:
        value = poly.evaluate(())
        if value == claim:
            return VerificationResult(
                claimed_sum=claim, accepted=True, failed_round=None,
                reason_code=None, message="accepted (zero-variable constant)",
                transcript=transcript, final_value=value,
            )
        return _reject(
            original_claim, None, "final",
            f"final check failed: f() = {value} != claimed {claim}",
            transcript, final_value=value,
        )

    for j in range(n):
        round_no = j + 1
        raw = proof.rounds[j]

        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            return _reject(
                original_claim, round_no, "format",
                f"round {round_no}: coefficient list must be a sequence, "
                f"got {type(raw).__name__}",
                transcript,
            )
        coeffs = list(raw)
        if len(coeffs) == 0:
            return _reject(
                original_claim, round_no, "format",
                f"round {round_no}: empty coefficient list", transcript,
            )
        if len(coeffs) > 2:
            return _reject(
                original_claim, round_no, "degree",
                f"round {round_no}: polynomial degree {len(coeffs) - 1} exceeds "
                f"multilinear bound 1",
                transcript,
            )

        canon_coeffs: list[int] = []
        for k, c in enumerate(coeffs):
            if isinstance(c, bool) or not isinstance(c, int):
                kind = type(c).__name__
                return _reject(
                    original_claim, round_no, "format",
                    f"round {round_no}: coeff[{k}] expected int, got {kind} {c!r}",
                    transcript,
                )
            if not 0 <= c < FIELD_P:
                return _reject(
                    original_claim, round_no, "field",
                    f"round {round_no}: coeff[{k}] = {c} is outside GF({FIELD_P}) "
                    f"[0, {FIELD_P - 1}]",
                    transcript,
                )
            canon_coeffs.append(c)
        if len(canon_coeffs) == 1:
            canon_coeffs.append(0)
        c0, c1 = canon_coeffs

        g0 = c0 % FIELD_P
        g1 = (c0 + c1) % FIELD_P
        folded = (g0 + g1) % FIELD_P
        if folded != claim:
            return _reject(
                original_claim, round_no, "claim",
                f"round {round_no}: g(0)+g(1) = {g0}+{g1} = {folded} "
                f"!= previous claim {claim}",
                transcript,
            )

        next_claim = (c0 + c1 * rs[j]) % FIELD_P
        transcript.append(
            RoundRecord(
                round=round_no, variable=j, coeffs=(c0, c1),
                expected_claim=claim, g_at_zero=g0, g_at_one=g1,
                challenge=rs[j], next_claim=next_claim,
            )
        )
        claim = next_claim

    value = poly.evaluate(rs)
    if value != claim:
        return _reject(
            original_claim, None, "final",
            f"final check failed: f{rs} = {value} != last claim {claim}",
            transcript, final_value=value,
        )
    return VerificationResult(
        claimed_sum=original_claim,
        accepted=True, failed_round=None, reason_code=None,
        message="accepted", transcript=transcript, final_value=value,
    )


def run_sumcheck(poly: MultilinearPolynomial, challenges):
    """便捷流程：诚实 prover 出证后立即 verifier 核验，返回 (输出, 结果)。"""
    output = prove(poly, challenges)
    result = verify(poly, output.claimed_sum, challenges, output.proof)
    return output, result
