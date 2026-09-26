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


def _check_variable_id(value, num_vars: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{what} 必须是非 bool 整数变量 ID，实际为 {type(value).__name__}")
    if not 0 <= value < num_vars:
        raise ProtocolError(f"{what}={value} 越界，允许区间 [0, {num_vars - 1}]")
    return value


def _normalize_partial_variables(poly: MultilinearPoly, sum_variables, retained_point):
    """规范化并校验部分求和的变量分区、顺序和保留变量公开点。"""
    if (
        isinstance(sum_variables, (str, bytes, set, frozenset, dict))
        or not hasattr(sum_variables, "__getitem__")
    ):
        raise ProtocolError("sum_variables 必须是 tuple/list 这类显式有序序列")
    sum_order = tuple(
        _check_variable_id(v, poly.num_vars, f"sum_variables[{i}]")
        for i, v in enumerate(sum_variables)
    )
    if len(set(sum_order)) != len(sum_order):
        raise ProtocolError(f"sum_variables={sum_order} 含重复变量 ID")

    if not isinstance(retained_point, dict):
        raise ProtocolError("retained_point 必须是 {保留变量 ID: 公开域元素} 的 dict")
    for var, value in retained_point.items():
        _check_variable_id(var, poly.num_vars, "retained_point 的键")
        check_field_element(value, f"retained_point[{var}]")
    raw_retained = tuple(retained_point.keys())
    overlap = set(sum_order).intersection(raw_retained)
    if overlap:
        raise ProtocolError(f"变量 {sorted(overlap)} 同时出现在求和集合与保留集合中")
    retained = tuple(sorted(raw_retained))
    expected_retained = tuple(v for v in range(poly.num_vars) if v not in set(sum_order))
    if retained != expected_retained:
        raise ProtocolError(
            "变量分区必须覆盖全部变量且互不相交；"
            f"sum_variables={sum_order}，retained_point 键={retained}，"
            f"期望保留变量={expected_retained}"
        )
    point = tuple(retained_point[v] for v in retained)
    return sum_order, retained, point


def _partial_fixed_point(poly, sum_order, retained, retained_point, challenges):
    point = [0] * poly.num_vars
    for var, value in zip(retained, retained_point):
        point[var] = value
    for var, value in zip(sum_order, challenges):
        point[var] = value
    return point


def _partial_degree_bound(poly, sum_order, i, retained, retained_point,
                          prior_challenges) -> int:
    fixed = {var: value for var, value in zip(retained, retained_point)}
    fixed.update({sum_order[j]: prior_challenges[j] for j in range(i)})
    restricted = poly.restricted(fixed) if fixed else poly
    remaining_ids = tuple(v for v in range(poly.num_vars) if v not in fixed)
    current_position = remaining_ids.index(sum_order[i])
    return restricted.degree_of(current_position)


def _partial_round_values(poly, sum_order, i, retained, retained_point,
                          prior_challenges, eval_budget):
    fixed = {var: value for var, value in zip(retained, retained_point)}
    fixed.update({sum_order[j]: prior_challenges[j] for j in range(i)})
    current = sum_order[i]
    later = sum_order[i + 1:]

    def sum_with_current(value: int) -> int:
        all_fixed = dict(fixed)
        all_fixed[current] = value
        size = 1 << len(later)
        if eval_budget is not None and size > eval_budget:
            from .errors import BudgetExhausted

            raise BudgetExhausted(
                f"部分求和第 {i} 轮剩余布尔立方体含 {size} 个求值点，"
                f"超过预算 eval_budget={eval_budget}"
            )
        total = 0
        base_point = [0] * poly.num_vars
        for var, fixed_value in all_fixed.items():
            base_point[var] = fixed_value
        for mask in range(size):
            for pos, var in enumerate(later):
                base_point[var] = (mask >> pos) & 1
            total = (total + poly.evaluate(base_point)) % P
        return total

    g0 = sum_with_current(0)
    g1 = sum_with_current(1)

    degree_bound = _partial_degree_bound(
        poly, sum_order, i, retained, retained_point, prior_challenges
    )
    a1 = (g1 - g0) % P
    coeffs = (g0,) if degree_bound == 0 else (g0, a1)
    return g0, g1, coeffs


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
class PartialRoundMessage:
    """部分求和单轮消息。

    ``variable`` 是原电路的显式变量 ID，不是压缩后的下标；``coeffs``
    仍采用升幂紧规范形式，次数上界来自该变量在保留点及前序挑战
    固定后的原多项式次数。
    """

    index: int
    variable: int
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
class PartialTranscript:
    """部分求和协议转录；公开语句与证明数据绑定在同一不可变对象中。"""

    num_vars: int
    sum_variables: tuple[int, ...]
    retained_variables: tuple[int, ...]
    retained_point: tuple[int, ...]
    initial_claim: int
    rounds: tuple[PartialRoundMessage, ...]
    challenges: tuple[int, ...]
    final_claim: int

    def pretty(self) -> str:
        lines = [
            f"partial sum-check 转录 (F_{P}, 总变量数 {self.num_vars})",
            f"求和变量顺序={self.sum_variables}，保留变量={self.retained_variables}，"
            f"公开点={self.retained_point}",
            f"初始主张 C = {self.initial_claim}",
        ]
        for msg in self.rounds:
            poly_text = (
                f"{msg.coeffs[0]} + {msg.coeffs[1]}*t"
                if len(msg.coeffs) == 2
                else f"{msg.coeffs[0]}"
            )
            suffix = (
                f"，挑战={msg.challenge}，出主张={msg.output_claim}"
                if msg.challenge is not None
                else ""
            )
            lines.append(
                f"  轮 {msg.index}: X_{msg.variable}，g(t)={poly_text}，"
                f"入主张={msg.input_claim}{suffix}"
            )
        lines.append(f"最终主张 = {self.final_claim}")
        return "\n".join(lines)


@dataclass(frozen=True)
class PartialVerifyResult:
    """部分求和验证结论；空求和集合时 ``rounds_checked`` 为 0。"""

    accepted: bool
    failed_round: int | None
    reason: str | None
    final_claim: int
    direct_evaluation: int
    rounds_checked: int = 0


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


def prove_partial(
    poly: MultilinearPoly,
    sum_variables,
    retained_point: dict,
    challenges,
    *,
    claimed_sum: int | None = None,
    eval_budget: int | None = None,
) -> PartialTranscript:
    """诚实生成部分求和证明。

    目标主张是：在 ``retained_point`` 给定的公开点固定保留变量后，
    按 ``sum_variables`` 的显式顺序对其余布尔变量求和。``challenges``
    与 ``sum_variables`` 一一对应；空求和集合时两者都为空，协议退化
    为一次公开点直接求值检查。
    """
    if not isinstance(poly, MultilinearPoly):
        raise ProtocolError(f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}")
    sum_order, retained, point = _normalize_partial_variables(
        poly, sum_variables, retained_point
    )
    if (
        isinstance(challenges, (str, bytes, set, frozenset, dict))
        or not hasattr(challenges, "__getitem__")
    ):
        raise ProtocolError("challenges 必须是 tuple/list 这类显式有序序列")
    challenges = _check_challenges(challenges, len(sum_order))

    if not sum_order:
        actual_sum = poly.evaluate(point)
    else:
        g0, g1, _ = _partial_round_values(
            poly, sum_order, 0, retained, point, (), eval_budget
        )
        actual_sum = (g0 + g1) % P
    if claimed_sum is None:
        initial_claim = actual_sum
    else:
        initial_claim = check_field_element(claimed_sum, "claimed_sum")
        if initial_claim != actual_sum:
            raise ProtocolError(
                f"claimed_sum={initial_claim} 与部分求和真实值 {actual_sum} 不一致；"
                "诚实 prover 不能证明假主张"
            )

    rounds = []
    prev_claim = initial_claim
    final_claim = initial_claim
    for i, variable in enumerate(sum_order):
        g0, g1, coeffs = _partial_round_values(
            poly, sum_order, i, retained, point, challenges[:i], eval_budget
        )
        if (g0 + g1) % P != prev_claim:  # pragma: no cover - 防御性不变量
            raise RuntimeError(
                f"内部错误：部分求和第 {i} 轮 g(0)+g(1)={(g0 + g1) % P} "
                f"与主张 {prev_claim} 不闭合"
            )
        if i < len(sum_order) - 1:
            challenge = challenges[i]
            output = (coeffs[0] + (coeffs[1] * challenge if len(coeffs) == 2 else 0)) % P
            rounds.append(
                PartialRoundMessage(i, variable, coeffs, prev_claim, challenge, output)
            )
            prev_claim = output
        else:
            rounds.append(PartialRoundMessage(i, variable, coeffs, prev_claim, None, None))
            final_claim = (
                coeffs[0] + (coeffs[1] * challenges[i] if len(coeffs) == 2 else 0)
            ) % P

    full_point = _partial_fixed_point(poly, sum_order, retained, point, challenges)
    direct = poly.evaluate(full_point)
    if direct != final_claim:  # pragma: no cover - 防御性不变量
        raise RuntimeError("内部错误：诚实部分求和 prover 的最终主张与直接求值不一致")
    return PartialTranscript(
        poly.num_vars,
        sum_order,
        retained,
        point,
        initial_claim,
        tuple(rounds),
        challenges,
        final_claim,
    )


def _validate_partial_structure(poly: MultilinearPoly, transcript: PartialTranscript):
    def tf_check(value, what: str) -> None:
        try:
            check_field_element(value, what)
        except (TypeError, ValueError) as exc:
            raise TranscriptFormatError(str(exc)) from exc

    if not isinstance(transcript, PartialTranscript):
        raise TranscriptFormatError(
            f"transcript 必须为 PartialTranscript，实际为 {type(transcript).__name__}"
        )
    if isinstance(transcript.num_vars, bool) or not isinstance(transcript.num_vars, int):
        raise TranscriptFormatError("transcript.num_vars 必须为非 bool 整数")
    if transcript.num_vars != poly.num_vars:
        raise TranscriptFormatError(
            f"转录 num_vars={transcript.num_vars} 与多项式变量数 {poly.num_vars} 不一致"
        )
    if not isinstance(transcript.sum_variables, tuple):
        raise TranscriptFormatError("transcript.sum_variables 必须为 tuple")
    if not isinstance(transcript.retained_variables, tuple):
        raise TranscriptFormatError("transcript.retained_variables 必须为 tuple")
    if not isinstance(transcript.retained_point, tuple):
        raise TranscriptFormatError("transcript.retained_point 必须为 tuple")
    if len(transcript.retained_variables) != len(transcript.retained_point):
        raise TranscriptFormatError("保留变量列表与公开点长度不一致")
    try:
        retained_map = dict(zip(transcript.retained_variables, transcript.retained_point))
        sum_order, retained, point = _normalize_partial_variables(
            poly, transcript.sum_variables, retained_map
        )
    except (ProtocolError, TypeError, ValueError) as exc:
        raise TranscriptFormatError(f"转录变量分区/公开点不合法：{exc}") from exc
    if transcript.sum_variables != sum_order:
        raise TranscriptFormatError("转录求和变量顺序与规范化结果不一致")
    if transcript.retained_variables != retained:
        raise TranscriptFormatError("转录保留变量列表未按变量 ID 升序记录")
    if transcript.retained_point != point:
        raise TranscriptFormatError("转录公开点与保留变量列表不对应")

    round_count = len(sum_order)
    if len(transcript.rounds) != round_count:
        raise TranscriptFormatError(
            f"转录轮数 {len(transcript.rounds)} 与求和变量数 {round_count} 不一致"
        )
    if len(transcript.challenges) != round_count:
        raise TranscriptFormatError(
            f"转录挑战数 {len(transcript.challenges)} 与求和变量数 {round_count} 不一致"
        )
    tf_check(transcript.initial_claim, "transcript.initial_claim")
    tf_check(transcript.final_claim, "transcript.final_claim")
    for i, challenge in enumerate(transcript.challenges):
        tf_check(challenge, f"transcript.challenges[{i}]")
    for i, msg in enumerate(transcript.rounds):
        if not isinstance(msg, PartialRoundMessage):
            raise TranscriptFormatError(
                f"rounds[{i}] 必须为 PartialRoundMessage，实际为 {type(msg).__name__}"
            )
        if msg.index != i:
            raise TranscriptFormatError(
                f"rounds[{i}].index={msg.index} 与轮次位置 {i} 不一致"
            )
        if msg.variable != sum_order[i]:
            raise TranscriptFormatError(
                f"rounds[{i}].variable={msg.variable} 与显式顺序变量 "
                f"X_{sum_order[i]} 不一致"
            )
        if not isinstance(msg.coeffs, tuple):
            raise TranscriptFormatError(f"rounds[{i}].coeffs 必须为 tuple")
        if len(msg.coeffs) not in (1, 2):
            raise TranscriptFormatError(
                f"rounds[{i}].coeffs 长度 {len(msg.coeffs)} 对应次数 "
                f"{len(msg.coeffs) - 1}，超过多线性允许的次数 1（非法次数）"
            )
        declared_degree = len(msg.coeffs) - 1
        bound = _partial_degree_bound(
            poly, sum_order, i, retained, point, transcript.challenges[:i]
        )
        if declared_degree > bound:
            raise TranscriptFormatError(
                f"rounds[{i}] 声明次数 {declared_degree} 超过变量 X_{msg.variable} "
                f"在公开点及前序挑战限制后的实际次数 {bound}（次数上界违约）"
            )
        for j, coeff in enumerate(msg.coeffs):
            tf_check(coeff, f"rounds[{i}].coeffs[{j}]")
        tf_check(msg.input_claim, f"rounds[{i}].input_claim")
        if i < round_count - 1:
            if msg.challenge is None or msg.output_claim is None:
                raise TranscriptFormatError(
                    f"rounds[{i}] 缺少非末轮必需的 challenge/output_claim"
                )
            tf_check(msg.challenge, f"rounds[{i}].challenge")
            tf_check(msg.output_claim, f"rounds[{i}].output_claim")
            if msg.challenge != transcript.challenges[i]:
                raise TranscriptFormatError(
                    f"rounds[{i}].challenge={msg.challenge} 与转录挑战数组不一致"
                )
        elif msg.challenge is not None or msg.output_claim is not None:
            raise TranscriptFormatError(
                f"rounds[{i}] 是末轮，不应携带 challenge/output_claim"
            )
    return sum_order, retained, point


def verify_partial(
    poly: MultilinearPoly,
    transcript: PartialTranscript,
    *,
    sum_variables=None,
    retained_point: dict | None = None,
) -> PartialVerifyResult:
    """验证部分求和转录。

    可选的 ``sum_variables`` / ``retained_point`` 是调用方期望的公开语句；
    提供时必须与转录内绑定语句完全一致，否则抛 :class:`ProtocolError`，
    防止把一份证明复用到另一套变量分区或公开点。
    """
    if not isinstance(poly, MultilinearPoly):
        raise ProtocolError(f"poly 必须为 MultilinearPoly，实际为 {type(poly).__name__}")
    sum_order, retained, point = _validate_partial_structure(poly, transcript)
    if sum_variables is not None or retained_point is not None:
        if sum_variables is None or retained_point is None:
            raise ProtocolError("sum_variables 与 retained_point 必须同时提供")
        expected_sum, expected_retained, expected_point = _normalize_partial_variables(
            poly, sum_variables, retained_point
        )
        if (expected_sum, expected_retained, expected_point) != (sum_order, retained, point):
            raise ProtocolError(
                "转录绑定的变量分区/公开点与调用方期望不一致，不能复用该证明"
            )

    prev_claim = transcript.initial_claim
    rounds_checked = 0
    direct_point = _partial_fixed_point(
        poly, sum_order, retained, point, transcript.challenges
    )
    direct = poly.evaluate(direct_point)
    for i, msg in enumerate(transcript.rounds):
        g0 = msg.eval_at(0)
        g1 = msg.eval_at(1)
        if (g0 + g1) % P != msg.input_claim or msg.input_claim != prev_claim:
            return PartialVerifyResult(
                False,
                i,
                f"轮 {i} 主张不闭合：g(0)+g(1)={(g0 + g1) % P}，"
                f"要求等于上一主张 {prev_claim}，转录记录为 {msg.input_claim}",
                transcript.final_claim,
                direct,
                rounds_checked,
            )
        rounds_checked += 1
        if i < len(transcript.rounds) - 1:
            expected_output = msg.eval_at(transcript.challenges[i])
            if expected_output != msg.output_claim:
                return PartialVerifyResult(
                    False,
                    i,
                    f"轮 {i} 记账错误：g(r) 应为 {expected_output}，"
                    f"转录记录 output_claim={msg.output_claim}",
                    transcript.final_claim,
                    direct,
                    rounds_checked,
                )
            prev_claim = expected_output

    if transcript.rounds:
        last_index = len(transcript.rounds) - 1
        final_expected = transcript.rounds[-1].eval_at(
            transcript.challenges[last_index]
        )
        if final_expected != transcript.final_claim:
            return PartialVerifyResult(
                False,
                len(transcript.rounds),
                f"最终主张记账错误：末轮 g(r) 应为 {final_expected}，"
                f"转录记录 final_claim={transcript.final_claim}",
                transcript.final_claim,
                direct,
                rounds_checked,
            )
    elif transcript.initial_claim != transcript.final_claim:
        return PartialVerifyResult(
            False,
            0,
            "空求和集合要求 initial_claim 与 final_claim 相同",
            transcript.final_claim,
            direct,
            rounds_checked,
        )

    if direct != transcript.final_claim:
        return PartialVerifyResult(
            False,
            len(transcript.rounds),
            f"最终直接点值检查失败：f(公开点/挑战点)={direct}，"
            f"最终主张={transcript.final_claim}",
            transcript.final_claim,
            direct,
            rounds_checked,
        )
    return PartialVerifyResult(
        True, None, None, transcript.final_claim, direct, rounds_checked
    )
