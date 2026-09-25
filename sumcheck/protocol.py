"""sum-check 协议（F_101 多线性多项式，布尔立方体求和）。

协议流程（n 个变量，调用者预先固定挑战数组 r_0..r_{n-1}）：

1. 主张 C = Σ_{b∈{0,1}^n} f(b)（mod p）。
2. 第 i 轮，prover 关于变量 X_i 发送一元多项式
   g_i(t)，次数不超过 X_i 在 f 中的次数（多线性 ⇒ ≤ 1）：
       g_i(t) = Σ_{剩余布尔赋值} f(r_0..r_{i-1}, t, b_{i+1}..b_n)
3. verifier 检查 g_i(0)+g_i(1) = 上一主张（首轮为 C，之后为 g_{i-1}(r_{i-1})），
   然后新主张 := g_i(r_i)。
4. 最后一轮后，verifier 直接计算原多项式点值 f(r_0,..,r_{n-1})，
   与最终主张比较。

真实交互协议中每轮挑战应在收到该轮消息后随机抽取；本教学库把挑战作为
调用者给定的固定数组（便于确定性测试与复现），不模拟随机性，也不提供
零知识性或知识可靠性之外的任何密码学承诺。
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ProtocolError, TranscriptFormatError
from .field import P, check_field_element
from .polynomial import MAX_VARS, MultilinearPoly

# 预算耗尽 vs 主张为假的语义说明（供 README/调用方引用）。
BUDGET_NOTE = (
    "BudgetExhausted 表示布尔立方体枚举预算不足（资源问题，证明可能为真）；"
    "主张不成立由 verify 返回 accepted=False 表达，二者严格区分。"
    "本协议不存在搜索过程：honest prover 对真主张总能在 2^n 预算内完成。"
)

AWAITING_POLYNOMIAL = "awaiting_polynomial"
AWAITING_CHALLENGE = "awaiting_challenge"
ACCEPTED = "accepted"
REJECTED = "rejected"


def _check_challenges(challenges, num_vars: int) -> tuple[int, ...]:
    if isinstance(challenges, (str, bytes)) or not hasattr(challenges, "__len__"):
        raise ProtocolError("challenges 必须为长度 num_vars 的域元素序列")
    if len(challenges) != num_vars:
        raise ProtocolError(
            f"challenges 长度 {len(challenges)} 与变量数 {num_vars} 不一致"
        )
    result = []
    for i, ch in enumerate(challenges):
        result.append(check_field_element(ch, f"challenges[{i}]"))
    return tuple(result)


@dataclass(frozen=True)
class RoundMessage:
    """单轮消息：关于当前变量的一元多项式及该轮记账信息。

    index: int
        轮次，0 基；
    coeffs: tuple[int, ...]
        升幂系数 (a0,) 或 (a0, a1)，即 g(t)=a0+a1*t，均为规范域元素；
    input_claim: int
        进入本轮时必须满足的 g(0)+g(1) 主张；
    challenge: int | None
        本轮后给定的挑战；最后一轮为 None；
    output_claim: int | None
        g(challenge)；最后一轮为 None（最终主张见 :class:`Transcript`）。
    """

    index: int
    coeffs: tuple[int, ...]
    input_claim: int
    challenge: int | None = None
    output_claim: int | None = None

    def eval_at(self, x: int) -> int:
        x = check_field_element(x, f"第 {self.index} 轮求值点")
        value = self.coeffs[0]
        if len(self.coeffs) == 2:
            value = (value + self.coeffs[1] * x) % P
        return value


@dataclass(frozen=True)
class Transcript:
    """完整协议转录：不可变值对象，所有数值均为 F_p 规范整数。"""

    num_vars: int
    initial_claim: int
    rounds: tuple[RoundMessage, ...]
    challenges: tuple[int, ...]
    final_claim: int

    def pretty(self) -> str:
        """返回便于阅读/打印的转录文本（不是预录结论，全部来自真实计算）。"""
        lines = [
            f"sum-check 转录 (F_{P}, {self.num_vars} 个变量)",
            f"初始主张 C = g0(0)+g0(1) = {self.initial_claim}",
        ]
        for msg in self.rounds:
            if len(msg.coeffs) == 2:
                poly_text = f"{msg.coeffs[0]} + {msg.coeffs[1]}*t"
            else:
                poly_text = f"{msg.coeffs[0]}"
            lines.append(
                f"  轮 {msg.index}: g{msg.index}(t) = {poly_text}"
                f"  [入主张={msg.input_claim}"
                + (f", 挑战 r{msg.index}={msg.challenge}, 出主张={msg.output_claim}]"
                   if msg.challenge is not None
                   else "]")
            )
        lines.append(f"最终主张 g{self.num_vars - 1}(r_{self.num_vars - 1}) = {self.final_claim}")
        return "\n".join(lines)


@dataclass(frozen=True)
class VerifyResult:
    """验证结论。

    accepted:
        全部检查通过为 True；
    failed_round:
        首个失败轮（0 基）；最终直接点值检查失败时为 num_vars；
        结构合法但主张为假时填充；结构不合法时 verify 直接抛
        :class:`TranscriptFormatError`；
    reason:
        人类可读的失败原因；
    final_claim / direct_evaluation:
        最终主张与 verifier 独立直接计算的 f(r_0..r_{n-1})。
    """

    accepted: bool
    failed_round: int | None
    reason: str | None
    final_claim: int
    direct_evaluation: int
    rounds_checked: int = 0


@dataclass(frozen=True)
class StreamRound:
    """流式公开转录中的一轮：仅包含调用方提交的多项式系数与挑战。"""

    index: int
    coeffs: tuple[int, ...]
    challenge: int | None = None


@dataclass(frozen=True)
class StreamingVerifierState:
    """可暂停/恢复的公开状态，不包含任何可直接改写的累计主张。

    恢复方必须从第 0 轮重放 ``rounds``，重新计算每轮输入/输出主张。
    """

    num_vars: int
    initial_claim: int
    rounds: tuple[StreamRound, ...]
    next_round: int


@dataclass(frozen=True)
class StreamingStatus:
    """一次流式提交后的状态视图。"""

    phase: str
    next_round: int
    rejected: bool = False
    accepted: bool = False
    reason: str | None = None
    output_claim: int | None = None
    direct_evaluation: int | None = None


class StreamingVerifier:
    """逐轮接收多项式和挑战的 sum-check 验证状态机。

    正确顺序为：提交第 ``i`` 轮多项式，通过后提交第 ``i`` 轮挑战；非末轮
    随后才能提交第 ``i+1`` 轮多项式。任何顺序、域外值、次数或主张错误都会
    使状态机进入不可恢复的 ``rejected`` 终态。
    """

    def __init__(self, poly: MultilinearPoly, initial_claim: int):
        if not isinstance(poly, MultilinearPoly):
            raise ProtocolError(f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}")
        if poly.num_vars == 0:
            raise ProtocolError("空变量多项式无法运行 sum-check：num_vars 至少为 1")
        self._poly = poly
        self._initial_claim = check_field_element(initial_claim, "initial_claim")
        self._rounds: list[StreamRound] = []
        self._input_claims: list[int] = []
        self._output_claims: list[int] = []
        self._current_claim = self._initial_claim
        self._next_round = 0
        self._phase = AWAITING_POLYNOMIAL
        self._reason: str | None = None

    @property
    def phase(self) -> str:
        return self._phase

    @property
    def next_round(self) -> int:
        return self._next_round

    @property
    def is_rejected(self) -> bool:
        return self._phase == REJECTED

    @property
    def is_accepted(self) -> bool:
        return self._phase == ACCEPTED

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def status(self) -> StreamingStatus:
        output = self._output_claims[-1] if self._output_claims else None
        direct = None
        if self._phase == ACCEPTED:
            output = self._current_claim
            direct = self._current_claim
        return StreamingStatus(
            self._phase,
            self._next_round,
            self._phase == REJECTED,
            self._phase == ACCEPTED,
            self._reason,
            output,
            direct,
        )

    @classmethod
    def restore(cls, poly: MultilinearPoly, state: StreamingVerifierState) -> "StreamingVerifier":
        """从公开状态恢复，并重放全部已提交轮次以重建内部累计主张。"""
        if not isinstance(state, StreamingVerifierState):
            raise ProtocolError(
                f"state 必须为 StreamingVerifierState，实际为 {type(state).__name__}"
            )
        if isinstance(state.num_vars, bool) or not isinstance(state.num_vars, int):
            raise ProtocolError("state.num_vars 必须为非 bool 整数")
        if not isinstance(state.rounds, tuple):
            raise ProtocolError("state.rounds 必须为 tuple")
        if isinstance(state.next_round, bool) or not isinstance(state.next_round, int):
            raise ProtocolError("state.next_round 必须为非 bool 整数")

        verifier = cls(poly, state.initial_claim)
        if state.num_vars != poly.num_vars:
            raise ProtocolError(
                f"state.num_vars={state.num_vars} 与多项式变量数 {poly.num_vars} 不一致"
            )
        if not 0 <= state.next_round <= poly.num_vars:
            raise ProtocolError(f"state.next_round={state.next_round} 超出允许范围")

        expected_index = 0
        for position, record in enumerate(state.rounds):
            if not isinstance(record, StreamRound):
                raise ProtocolError(
                    f"state.rounds[{position}] 必须为 StreamRound，实际为 {type(record).__name__}"
                )
            if record.index != expected_index:
                verifier._reject(
                    f"轮次顺序错误：状态位置 {position} 记录为轮 {record.index}，"
                    f"应提交轮 {expected_index}"
                )
                return verifier
            verifier.submit_polynomial(record.index, record.coeffs)
            if verifier.is_rejected:
                return verifier
            if record.challenge is not None:
                verifier.submit_challenge(record.index, record.challenge)
                if verifier.is_rejected:
                    return verifier
            elif state.rounds[position + 1:]:
                verifier._reject(f"轮 {record.index} 的挑战未提交，不能收下一轮")
                return verifier
            expected_index += 1

        if verifier._next_round != state.next_round:
            raise ProtocolError(
                f"state.next_round={state.next_round} 与重放结果 {verifier._next_round} 不一致"
            )
        return verifier

    def export_state(self) -> StreamingVerifierState:
        """导出公开转录和下一轮位置；拒绝终态不可导出。"""
        if self._phase == REJECTED:
            raise ProtocolError("验证器已处于不可恢复的拒绝终态，不能导出状态")
        return StreamingVerifierState(
            self._poly.num_vars,
            self._initial_claim,
            tuple(StreamRound(r.index, r.coeffs, r.challenge) for r in self._rounds),
            self._next_round,
        )

    def submit_polynomial(self, round_index: int, coeffs) -> StreamingStatus:
        """提交并立即验证一轮一元多项式。"""
        if self._terminal():
            return self.status
        if self._phase != AWAITING_POLYNOMIAL:
            return self._reject(f"轮 {self._next_round} 的挑战未提交，不能收下一轮")
        if isinstance(round_index, bool) or not isinstance(round_index, int):
            return self._reject(
                f"轮次必须为非 bool 整数，实际为 {type(round_index).__name__}"
            )
        if round_index != self._next_round:
            return self._reject(
                f"轮次顺序错误：当前应提交轮 {self._next_round}，实际收到轮 {round_index}"
            )

        error = self._validate_coefficients(round_index, coeffs)
        if error is not None:
            return self._reject(error)
        normalized = tuple(coeffs)

        g0 = self._eval(normalized, 0)
        g1 = self._eval(normalized, 1)
        actual_sum = (g0 + g1) % P
        if actual_sum != self._current_claim:
            return self._reject(
                f"轮 {round_index} 主张不闭合：g{round_index}(0)+g{round_index}(1)="
                f"{actual_sum}，要求等于上一主张 {self._current_claim}"
            )

        self._rounds.append(StreamRound(round_index, normalized))
        self._input_claims.append(self._current_claim)
        self._phase = AWAITING_CHALLENGE
        return self.status

    def submit_challenge(self, round_index: int, challenge: int) -> StreamingStatus:
        """在某轮多项式验证通过后提交该轮挑战。"""
        if self._terminal():
            return self.status
        if self._phase != AWAITING_CHALLENGE:
            return self._reject("当前轮尚未提交并通过多项式验证，不能提交挑战")
        if isinstance(round_index, bool) or not isinstance(round_index, int):
            return self._reject(
                f"轮次必须为非 bool 整数，实际为 {type(round_index).__name__}"
            )
        expected_index = self._next_round
        if round_index != expected_index:
            return self._reject(
                f"轮次顺序错误：当前应提交轮 {expected_index} 的挑战，实际收到轮 {round_index}"
            )
        try:
            challenge = check_field_element(challenge, f"challenges[{round_index}]")
        except (TypeError, ValueError) as exc:
            return self._reject(str(exc))

        coeffs = self._rounds[-1].coeffs
        output_claim = self._eval(coeffs, challenge)
        self._rounds[-1] = StreamRound(expected_index, coeffs, challenge)
        self._output_claims.append(output_claim)

        if expected_index == self._poly.num_vars - 1:
            self._current_claim = output_claim
            direct = self._poly.evaluate(
                tuple(record.challenge for record in self._rounds)
            )
            if direct != output_claim:
                return self._reject(
                    f"最终直接点值检查失败：f(r_0..r_{self._poly.num_vars - 1})={direct}，"
                    f"最终主张={output_claim}"
                )
            self._next_round = self._poly.num_vars
            self._phase = ACCEPTED
            self._reason = None
            return self.status

        self._current_claim = output_claim
        self._next_round += 1
        self._phase = AWAITING_POLYNOMIAL
        return self.status

    def to_transcript(self) -> Transcript:
        """接受后转换为旧的一次性批量验证转录。"""
        if self._phase != ACCEPTED:
            raise ProtocolError("只有已接受的流式验证才能转换为完整转录")
        challenges = tuple(record.challenge for record in self._rounds)
        messages = []
        for i, record in enumerate(self._rounds):
            if i < self._poly.num_vars - 1:
                messages.append(RoundMessage(
                    i, record.coeffs, self._input_claims[i],
                    record.challenge, self._output_claims[i],
                ))
            else:
                messages.append(RoundMessage(
                    i, record.coeffs, self._input_claims[i], None, None
                ))
        return Transcript(
            self._poly.num_vars,
            self._initial_claim,
            tuple(messages),
            challenges,
            self._current_claim,
        )

    def _terminal(self) -> bool:
        return self._phase in (ACCEPTED, REJECTED)

    def _reject(self, reason: str) -> StreamingStatus:
        self._phase = REJECTED
        self._reason = reason
        return self.status

    def _validate_coefficients(self, round_index: int, coeffs) -> str | None:
        if not isinstance(coeffs, tuple):
            return f"轮 {round_index} 系数必须为 tuple，实际为 {type(coeffs).__name__}"
        if len(coeffs) not in (1, 2):
            return (
                f"轮 {round_index} 系数长度 {len(coeffs)} 对应次数 "
                f"{len(coeffs) - 1}，超过多线性允许的次数 1"
            )
        prior = {
            record.index: record.challenge
            for record in self._rounds
            if record.challenge is not None
        }
        restricted = self._poly.restricted(prior) if prior else self._poly
        bound = restricted.degree_of(0)
        declared_degree = len(coeffs) - 1
        if declared_degree > bound:
            return (
                f"轮 {round_index} 声明次数 {declared_degree} 超过变量 X_{round_index} "
                f"在当前多项式中的实际次数 {bound}"
            )
        for j, coeff in enumerate(coeffs):
            try:
                check_field_element(coeff, f"rounds[{round_index}].coeffs[{j}]")
            except (TypeError, ValueError) as exc:
                return str(exc)
        return None

    @staticmethod
    def _eval(coeffs: tuple[int, ...], x: int) -> int:
        value = coeffs[0]
        if len(coeffs) == 2:
            value = (value + coeffs[1] * x) % P
        return value


def prove(
    poly: MultilinearPoly,
    challenges,
    *,
    claimed_sum: int | None = None,
    eval_budget: int | None = None,
) -> Transcript:
    """诚实 prover：逐轮真实枚举剩余布尔立方体生成转录。

    poly:
        至少 1 个变量的 :class:`MultilinearPoly`；
    challenges:
        长度恰为 num_vars 的固定挑战数组（调用者给定）；
    claimed_sum:
        初始主张；None 时由 prover 全枚举计算。显式给出时必须与真实求和
        一致——诚实 prover 不替假主张造证明（请通过篡改转录测试拒绝路径）；
    eval_budget:
        单次布尔立方体求和允许的最大求值点数；2^n 超预算抛
        :class:`BudgetExhausted`，与“主张为假”严格区分。
    """
    if not isinstance(poly, MultilinearPoly):
        raise ProtocolError(f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}")
    if poly.num_vars == 0:
        raise ProtocolError("空变量多项式无法运行 sum-check：num_vars 至少为 1")
    if poly.num_vars > MAX_VARS:  # 构造器已保证，防御性保留
        raise ProtocolError(f"num_vars 超过上限 {MAX_VARS}")
    challenges = _check_challenges(challenges, poly.num_vars)

    actual_sum = poly.boolean_sum(eval_budget=eval_budget)
    if claimed_sum is None:
        initial_claim = actual_sum
    else:
        initial_claim = check_field_element(claimed_sum, "claimed_sum")
        if initial_claim != actual_sum:
            raise ProtocolError(
                f"claimed_sum={initial_claim} 与布尔立方体真实求和 {actual_sum} 不一致；"
                "诚实 prover 不能证明假主张（拒绝路径请用篡改转录演示）"
            )

    rounds = []
    prev_claim = initial_claim
    for i in range(poly.num_vars):
        assignments = {j: challenges[j] for j in range(i)}
        remaining = poly.restricted(assignments) if assignments else poly

        g0 = remaining.restricted({0: 0}).boolean_sum(eval_budget=eval_budget)
        g1 = remaining.restricted({0: 1}).boolean_sum(eval_budget=eval_budget)
        a0 = g0
        a1 = (g1 - g0) % P
        # 次数不超过该变量在当前多项式中的实际次数（0 或 1）。
        coeffs = (a0,) if remaining.degree_of(0) == 0 else (a0, a1)

        if (g0 + g1) % P != prev_claim:  # pragma: no cover - 防御性不变量
            raise RuntimeError(
                f"内部错误：第 {i} 轮 g(0)+g(1)={(g0 + g1) % P} 与主张 {prev_claim} 不闭合"
            )

        if i < poly.num_vars - 1:
            challenge = challenges[i]
            output = (a0 + (a1 * challenge if len(coeffs) == 2 else 0)) % P
            rounds.append(RoundMessage(i, coeffs, prev_claim, challenge, output))
            prev_claim = output
        else:
            rounds.append(RoundMessage(i, coeffs, prev_claim, None, None))
            final_claim = (a0 + (a1 * challenges[i] if len(coeffs) == 2 else 0)) % P

    transcript = Transcript(
        poly.num_vars, initial_claim, tuple(rounds), challenges, final_claim
    )
    # 诚实性自检：最终主张必须等于原多项式在挑战点的直接求值。
    direct = poly.evaluate(challenges)
    if direct != final_claim:  # pragma: no cover - 防御性不变量
        raise RuntimeError("内部错误：诚实 prover 的最终主张与直接求值不一致")
    return transcript


def _validate_transcript_structure(transcript: Transcript, poly: MultilinearPoly) -> None:
    """转录结构校验：任何不合法都抛带轮次/位置的 :class:`TranscriptFormatError`。"""
    num_vars = poly.num_vars

    def tf_check(value, what: str) -> None:
        try:
            check_field_element(value, what)
        except (TypeError, ValueError) as exc:
            raise TranscriptFormatError(str(exc)) from exc

    if not isinstance(transcript, Transcript):
        raise TranscriptFormatError(
            f"transcript 必须为 Transcript，实际为 {type(transcript).__name__}"
        )
    if isinstance(transcript.num_vars, bool) or not isinstance(transcript.num_vars, int):
        raise TranscriptFormatError("transcript.num_vars 必须为非 bool 整数")
    if transcript.num_vars != num_vars:
        raise TranscriptFormatError(
            f"转录轮数元数据 num_vars={transcript.num_vars} 与多项式变量数 {num_vars} 不一致"
        )
    if len(transcript.rounds) != num_vars:
        raise TranscriptFormatError(
            f"转录轮数 {len(transcript.rounds)} 与变量数 {num_vars} 不一致"
        )
    if len(transcript.challenges) != num_vars:
        raise TranscriptFormatError(
            f"转录挑战数 {len(transcript.challenges)} 与变量数 {num_vars} 不一致"
        )
    where = "transcript.initial_claim"
    tf_check(transcript.initial_claim, where)
    tf_check(transcript.final_claim, "transcript.final_claim")
    for i, ch in enumerate(transcript.challenges):
        tf_check(ch, f"transcript.challenges[{i}]")
    for i, msg in enumerate(transcript.rounds):
        if not isinstance(msg, RoundMessage):
            raise TranscriptFormatError(
                f"rounds[{i}] 必须为 RoundMessage，实际为 {type(msg).__name__}"
            )
        if msg.index != i:
            raise TranscriptFormatError(
                f"rounds[{i}].index={msg.index} 与轮次位置 {i} 不一致"
            )
        if not isinstance(msg.coeffs, tuple):
            raise TranscriptFormatError(f"rounds[{i}].coeffs 必须为 tuple")
        if len(msg.coeffs) not in (1, 2):
            raise TranscriptFormatError(
                f"rounds[{i}].coeffs 长度 {len(msg.coeffs)} 对应次数 "
                f"{len(msg.coeffs) - 1}，超过多线性允许的次数 1（非法次数）"
            )
        declared_degree = len(msg.coeffs) - 1
        # 次数上界按“前序挑战限制后的多项式”计算，与 prover 口径一致：
        # 例如 f=x0*x1，r0=0 杀死全部项后，第 1 轮实际次数为 0。
        prior = {j: transcript.challenges[j] for j in range(i)}
        restricted = poly.restricted(prior) if prior else poly
        var_bound = restricted.degree_of(0)
        if declared_degree > var_bound:
            raise TranscriptFormatError(
                f"rounds[{i}] 声明次数 {declared_degree} 超过变量 X_{i} "
                f"在当前（前序挑战限制后）多项式中的实际次数 {var_bound}（次数上界违约）"
            )
        for j, c in enumerate(msg.coeffs):
            tf_check(c, f"rounds[{i}].coeffs[{j}]")
        tf_check(msg.input_claim, f"rounds[{i}].input_claim")
        if i < num_vars - 1:
            if msg.challenge is None or msg.output_claim is None:
                raise TranscriptFormatError(
                    f"rounds[{i}] 缺少非末轮必需的 challenge/output_claim"
                )
            tf_check(msg.challenge, f"rounds[{i}].challenge")
            tf_check(msg.output_claim, f"rounds[{i}].output_claim")
            if msg.challenge != transcript.challenges[i]:
                raise TranscriptFormatError(
                    f"rounds[{i}].challenge={msg.challenge} 与转录挑战数组 "
                    f"challenges[{i}]={transcript.challenges[i]} 不一致"
                )
        else:
            if msg.challenge is not None or msg.output_claim is not None:
                raise TranscriptFormatError(
                    f"rounds[{i}] 是末轮，不应携带 challenge/output_claim"
                )


def verify(poly: MultilinearPoly, transcript: Transcript) -> VerifyResult:
    """验证转录。

    结构不合法（轮数不符、非法次数、域外数、NaN/Infinity、类型错误等）抛
    :class:`TranscriptFormatError`；结构合法但某轮主张不闭合或最终点值不符，
    返回 ``accepted=False`` 与首个失败轮。
    """
    if not isinstance(poly, MultilinearPoly):
        raise ProtocolError(f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}")
    if poly.num_vars == 0:
        raise ProtocolError("空变量多项式无法运行 sum-check：num_vars 至少为 1")
    _validate_transcript_structure(transcript, poly)

    n = poly.num_vars
    prev_claim = transcript.initial_claim
    rounds_checked = 0
    for i, msg in enumerate(transcript.rounds):
        g0 = msg.eval_at(0)
        g1 = msg.eval_at(1)
        if (g0 + g1) % P != msg.input_claim or msg.input_claim != prev_claim:
            return VerifyResult(
                False, i,
                f"轮 {i} 主张不闭合：g{i}(0)+g{i}(1)={ (g0 + g1) % P }，"
                f"要求等于上一主张 {prev_claim}，转录记录为 {msg.input_claim}",
                transcript.final_claim, poly.evaluate(transcript.challenges), rounds_checked,
            )
        rounds_checked += 1
        if i < n - 1:
            expected_output = msg.eval_at(transcript.challenges[i])
            if expected_output != msg.output_claim:
                return VerifyResult(
                    False, i,
                    f"轮 {i} 记账错误：g{i}(r{i}) 应为 {expected_output}，"
                    f"转录记录 output_claim={msg.output_claim}",
                    transcript.final_claim, poly.evaluate(transcript.challenges), rounds_checked,
                )
            prev_claim = expected_output

    final_expected = transcript.rounds[-1].eval_at(transcript.challenges[n - 1])
    if final_expected != transcript.final_claim:
        return VerifyResult(
            False, n,
            f"最终主张记账错误：g{n - 1}(r_{n - 1}) 应为 {final_expected}，"
            f"转录记录 final_claim={transcript.final_claim}",
            transcript.final_claim, poly.evaluate(transcript.challenges), rounds_checked,
        )

    direct = poly.evaluate(transcript.challenges)
    if direct != transcript.final_claim:
        return VerifyResult(
            False, n,
            f"最终直接点值检查失败：f(r_0..r_{n - 1})={direct}，"
            f"最终主张={transcript.final_claim}",
            transcript.final_claim, direct, rounds_checked,
        )
    return VerifyResult(True, None, None, transcript.final_claim, direct, rounds_checked)
