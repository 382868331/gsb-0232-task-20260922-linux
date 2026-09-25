"""流式 sum-check 验证状态机。

与一次性 :func:`sumcheck.protocol.verify` 不同，本模块把验证拆成交互轮次：
调用方（扮演 verifier 的驱动方）按轮提交 prover 发来的一元多项式系数，
该轮检查通过后再提交本轮挑战；状态机严格交替推进：

    awaiting_poly --submit_round--> awaiting_challenge --submit_challenge-->
    awaiting_poly（下一轮） ... 最后一轮挑战提交后 --> accepted

规则：

* 挑战未提交前不能收下一轮多项式（顺序错误抛 :class:`StreamStateError`，
  状态机保持不变）；
* 任一轮多项式检查失败（求和关系不闭合）或最终直接点值不符，
  状态机进入不可恢复的拒绝终态 `rejected`，此后一切提交都抛
  :class:`StreamStateError`；
* 结构非法（系数非规范域整数、次数超界等）同样进入拒绝终态，
  并抛 :class:`TranscriptFormatError`；
* 最后一个挑战提交后，状态机自己用原多项式在挑战点直接求值
  `f(r_0,..,r_{n-1})` 与最终主张比较，不只依赖各轮求和关系。

暂停/恢复：:meth:`StreamingVerifier.export_state` 只导出公开转录
（初始主张、各轮系数、各轮挑战）与下一轮位置，**不导出任何派生的累计
主张**；:meth:`StreamingVerifier.restore` 从第 0 轮开始逐项重新检查
已提交部分，因此篡改导出状态中的累计数字无法影响恢复后的验证结论。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import (
    ProtocolError,
    StreamStateError,
    TranscriptFormatError,
    VerificationFailed,
)
from .field import P, check_field_element
from .polynomial import MultilinearPoly

# 状态机相位（字符串常量，便于导出状态直接序列化）。
AWAITING_POLY = "awaiting_poly"
AWAITING_CHALLENGE = "awaiting_challenge"
ACCEPTED = "accepted"
REJECTED = "rejected"


@dataclass(frozen=True)
class StreamState:
    """流式验证的可导出状态：仅公开转录 + 下一轮位置。

    rounds: tuple[tuple[int, ...], ...]
        已提交并通过检查的各轮系数（升幂，`(a0,)` 或 `(a0, a1)`）；
    challenges: tuple[int, ...]
        已提交的各轮挑战；
    next_round: int
        下一轮下标（0 基），等于 `len(rounds)`；
    phase: str
        `awaiting_poly` / `awaiting_challenge` / `accepted` / `rejected`。

    注意：不保存累计主张等派生数据；恢复时全部从头重算并重新检查。
    """

    num_vars: int
    initial_claim: int
    rounds: tuple[tuple[int, ...], ...]
    challenges: tuple[int, ...]
    next_round: int
    phase: str

    def to_dict(self) -> dict:
        """转为纯 dict（值为 int/str/list），便于 JSON 序列化做暂停/恢复。"""
        return {
            "num_vars": self.num_vars,
            "initial_claim": self.initial_claim,
            "rounds": [list(c) for c in self.rounds],
            "challenges": list(self.challenges),
            "next_round": self.next_round,
            "phase": self.phase,
        }

    @staticmethod
    def from_dict(data: dict) -> "StreamState":
        """从 :meth:`to_dict` 的产出还原；结构问题抛 :class:`ProtocolError`。"""
        if not isinstance(data, dict):
            raise ProtocolError(
                f"导出状态必须为 dict，实际为 {type(data).__name__}"
            )
        try:
            rounds = tuple(tuple(c) for c in data["rounds"])
            challenges = tuple(data["challenges"])
            return StreamState(
                data["num_vars"], data["initial_claim"], rounds, challenges,
                data["next_round"], data["phase"],
            )
        except KeyError as exc:
            raise ProtocolError(f"导出状态缺少字段 {exc}") from exc
        except TypeError as exc:
            raise ProtocolError(f"导出状态字段类型不合法: {exc}") from exc


class StreamingVerifier:
    """逐轮推进的 sum-check 验证状态机（教学用途，与 verify 同一套检查口径）。

    用法::

        v = StreamingVerifier(poly, initial_claim)
        for i in range(poly.num_vars):
            v.submit_round(coeffs_i)      # prover 的第 i 轮一元多项式
            v.submit_challenge(r_i)       # verifier 的挑战（调用方给定）
        assert v.state == ACCEPTED

    与一次性 verify 的差异仅在于交互形式：域约束（F_101 规范整数）、
    次数上界（多线性 ⇒ 每轮 ≤ 1，且按前序挑战限制后的实际次数）、
    最终直接点值检查完全一致。
    """

    def __init__(self, poly: MultilinearPoly, initial_claim):
        if not isinstance(poly, MultilinearPoly):
            raise ProtocolError(
                f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}"
            )
        if poly.num_vars == 0:
            raise ProtocolError("空变量多项式无法运行 sum-check：num_vars 至少为 1")
        self._poly = poly
        try:
            self._initial_claim = check_field_element(initial_claim, "initial_claim")
        except (TypeError, ValueError) as exc:
            raise ProtocolError(str(exc)) from exc
        self._rounds: list[tuple[int, ...]] = []
        self._challenges: list[int] = []
        self._current_claim = self._initial_claim
        self._state = AWAITING_POLY
        self._rejected_reason: str | None = None

    # ---- 只读属性 ------------------------------------------------------

    @property
    def state(self) -> str:
        """当前相位：awaiting_poly / awaiting_challenge / accepted / rejected。"""
        return self._state

    @property
    def next_round(self) -> int:
        """下一轮下标（0 基），等于已接受的轮数。"""
        return len(self._rounds)

    @property
    def current_claim(self) -> int:
        """当前待闭合的主张（派生自已检查转录，仅供调用方查看）。"""
        return self._current_claim

    @property
    def rejected_reason(self) -> str | None:
        """进入拒绝终态的原因；未拒绝时为 None。"""
        return self._rejected_reason

    @property
    def challenges(self) -> tuple[int, ...]:
        return tuple(self._challenges)

    @property
    def rounds(self) -> tuple[tuple[int, ...], ...]:
        return tuple(self._rounds)

    # ---- 内部工具 ------------------------------------------------------

    def _reject(self, reason: str) -> None:
        self._state = REJECTED
        self._rejected_reason = reason

    def _require_state(self, expected: str, what: str) -> None:
        if self._state == REJECTED:
            raise StreamStateError(
                f"{what}: 状态机已处于不可恢复的拒绝终态"
                f"（原因：{self._rejected_reason}），不能继续送入"
            )
        if self._state == ACCEPTED:
            raise StreamStateError(f"{what}: 验证已接受（终态），不能继续送入")
        if self._state != expected:
            raise StreamStateError(
                f"{what}: 当前相位为 {self._state}，需要先"
                + ("提交本轮多项式" if self._state == AWAITING_POLY else "提交本轮挑战")
            )

    def _degree_bound(self, round_index: int) -> int:
        """第 round_index 轮变量在前序挑战限制后的实际次数（0 或 1）。"""
        prior = {j: self._challenges[j] for j in range(round_index)}
        restricted = self._poly.restricted(prior) if prior else self._poly
        return restricted.degree_of(0)

    @staticmethod
    def _eval(coeffs: tuple[int, ...], x: int) -> int:
        value = coeffs[0]
        if len(coeffs) == 2:
            value = (value + coeffs[1] * x) % P
        return value

    # ---- 协议推进 ------------------------------------------------------

    def submit_round(self, coeffs) -> None:
        """提交本轮一元多项式系数（升幂，`(a0,)` 或 `(a0, a1)`）。

        成功则进入 awaiting_challenge。系数结构非法（非规范域整数、
        次数超界）抛 :class:`TranscriptFormatError`；求和关系
        `g(0)+g(1) == 当前主张` 不成立抛 :class:`VerificationFailed`；
        两种失败都使状态机进入不可恢复的 `rejected` 终态。
        """
        self._require_state(AWAITING_POLY, "submit_round")
        i = len(self._rounds)
        where = f"rounds[{i}].coeffs"

        def fail_structural(msg: str):
            self._reject(msg)
            raise TranscriptFormatError(msg)

        if isinstance(coeffs, (str, bytes)) or not isinstance(coeffs, (tuple, list)):
            fail_structural(
                f"{where} 必须为升幂系数 tuple/list，实际为 {type(coeffs).__name__}"
            )
        coeffs = tuple(coeffs)
        if len(coeffs) not in (1, 2):
            fail_structural(
                f"{where} 长度 {len(coeffs)} 对应次数 {len(coeffs) - 1}，"
                "超过多线性允许的次数 1（非法次数）"
            )
        for j, c in enumerate(coeffs):
            try:
                check_field_element(c, f"{where}[{j}]")
            except (TypeError, ValueError) as exc:
                fail_structural(str(exc))
        bound = self._degree_bound(i)
        if len(coeffs) - 1 > bound:
            fail_structural(
                f"rounds[{i}] 声明次数 {len(coeffs) - 1} 超过变量 X_{i} "
                f"在当前（前序挑战限制后）多项式中的实际次数 {bound}（次数上界违约）"
            )

        g0 = self._eval(coeffs, 0)
        g1 = self._eval(coeffs, 1)
        if (g0 + g1) % P != self._current_claim:
            reason = (
                f"轮 {i} 主张不闭合：g{i}(0)+g{i}(1)={(g0 + g1) % P}，"
                f"要求等于当前主张 {self._current_claim}"
            )
            self._reject(reason)
            raise VerificationFailed(reason)
        self._rounds.append(coeffs)
        self._state = AWAITING_CHALLENGE

    def submit_challenge(self, challenge) -> None:
        """提交本轮挑战（F_101 规范整数），推进到下一轮或终态。

        挑战值不合法（非规范域整数）抛 :class:`TranscriptFormatError`，
        状态机保持不变（调用方修正后可重试）。最后一轮挑战提交后，
        状态机直接计算原多项式点值 `f(r_0,..,r_{n-1})` 与最终主张比较：
        相符进入 `accepted`，不符进入 `rejected` 并抛
        :class:`VerificationFailed`。
        """
        self._require_state(AWAITING_CHALLENGE, "submit_challenge")
        i = len(self._challenges)
        try:
            r = check_field_element(challenge, f"challenges[{i}]")
        except (TypeError, ValueError) as exc:
            raise TranscriptFormatError(str(exc)) from exc
        self._challenges.append(r)
        self._current_claim = self._eval(self._rounds[-1], r)

        if len(self._challenges) < self._poly.num_vars:
            self._state = AWAITING_POLY
            return
        direct = self._poly.evaluate(tuple(self._challenges))
        if direct != self._current_claim:
            reason = (
                f"最终直接点值检查失败：f(r_0..r_{self._poly.num_vars - 1})={direct}，"
                f"最终主张={self._current_claim}"
            )
            self._reject(reason)
            raise VerificationFailed(reason)
        self._state = ACCEPTED

    # ---- 暂停 / 恢复 ---------------------------------------------------

    def export_state(self) -> StreamState:
        """导出公开转录与下一轮位置（不含累计主张等派生数据）。"""
        return StreamState(
            self._poly.num_vars,
            self._initial_claim,
            tuple(self._rounds),
            tuple(self._challenges),
            len(self._rounds),
            self._state,
        )

    @classmethod
    def restore(cls, poly: MultilinearPoly, state) -> "StreamingVerifier":
        """从导出状态恢复：从第 0 轮起重新检查全部已提交部分。

        `state` 可为 :class:`StreamState` 或其 :meth:`StreamState.to_dict`
        产出的 dict。恢复过程不信任状态中的任何派生结论：所有轮次的求和
        关系、次数上界、（若已完成的）最终直接点值都重新验证；任何篡改
        （包括篡改累计主张、调换轮次）都会按原始检查口径抛出异常。
        """
        if isinstance(state, dict):
            state = StreamState.from_dict(state)
        if not isinstance(state, StreamState):
            raise ProtocolError(
                f"state 必须为 StreamState 或 dict，实际为 {type(state).__name__}"
            )
        if not isinstance(poly, MultilinearPoly):
            raise ProtocolError(
                f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}"
            )
        if state.num_vars != poly.num_vars:
            raise ProtocolError(
                f"导出状态 num_vars={state.num_vars} 与多项式变量数 "
                f"{poly.num_vars} 不一致"
            )
        if state.next_round != len(state.rounds):
            raise ProtocolError(
                f"导出状态 next_round={state.next_round} 与已提交轮数 "
                f"{len(state.rounds)} 不一致（状态被篡改？）"
            )
        expected_phase = (
            AWAITING_CHALLENGE
            if len(state.challenges) == len(state.rounds) - 1
            else None
        )
        if len(state.challenges) == len(state.rounds):
            expected_phase = (
                ACCEPTED if len(state.rounds) == poly.num_vars else AWAITING_POLY
            )
        if expected_phase is None or (
            state.phase != expected_phase
            and not (state.phase == REJECTED)
        ):
            raise ProtocolError(
                f"导出状态 phase={state.phase!r} 与轮次/挑战数量"
                f"（{len(state.rounds)}/{len(state.challenges)}）不一致"
            )

        verifier = cls(poly, state.initial_claim)
        rejected = False
        for i, coeffs in enumerate(state.rounds):
            try:
                verifier.submit_round(coeffs)
                if i < len(state.challenges):
                    verifier.submit_challenge(state.challenges[i])
            except (TranscriptFormatError, VerificationFailed):
                if state.phase != REJECTED:
                    # 导出状态自称进行中/已接受，但重放检查失败：
                    # 已提交部分被篡改，按原始检查口径抛出。
                    raise
                # 导出状态如实标记了拒绝终态：重放到失败点即可。
                rejected = True
                break
        if state.phase == REJECTED and not rejected:
            raise ProtocolError(
                "导出状态标记为 rejected，但重放已提交部分全部通过；"
                "状态与公开转录不一致（状态被篡改？）"
            )
        if state.phase != REJECTED and verifier.state != state.phase:
            raise ProtocolError(
                f"重放后相位 {verifier.state} 与导出状态 {state.phase} 不一致"
            )
        return verifier
