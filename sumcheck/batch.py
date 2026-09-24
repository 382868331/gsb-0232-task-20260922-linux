"""多实例批处理：一次输入多个 sum-check 实例，逐实例运行协议并汇总。

每个实例保留自己的变量数与（多线性）次数上界，互不影响。批次的语义：

- 实例以调用者给定的 **实例 ID** 独立绑定：转录携带其所属 ID，
  :func:`verify_batch` 按 ID 一一对应核验，把 A 的消息拿去冒充 B 会得到
  明确的 :class:`~sumcheck.errors.BatchError`，而不是静默张冠李戴；
- F_101 是小域且 n≤8（布尔立方体至多 256 点），verifier 对每个实例
  **直接枚举布尔立方体求和**，独立核对转录的初始主张，不依赖转录自身；
- 任一实例未通过，则整批 ``accepted=False``，但仍逐实例给全汇总结论；
- 输入不合法（空批次、ID 重复/类型错误、实例与转录 ID 集合不一致等）
  在任何计算开始前抛 :class:`~sumcheck.errors.BatchError`，不产生半成品。

本模块不做随机线性批处理（不把多个实例压成一个和），也不做 Fiat–Shamir
转录绑定；ID 绑定只是显式的逐实例对应关系，不是密码学承诺。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import BatchError, BudgetExhausted, ProtocolError
from .field import P
from .polynomial import MAX_VARS, MultilinearPoly
from .protocol import (
    Transcript,
    VerifyResult,
    _check_challenges,
    prove,
    verify,
)

# 单次批处理允许的实例数上限（接口边界；n≤8 时计算量很小）。
MAX_BATCH_INSTANCES = 256


def _check_instance_id(value, what: str) -> str:
    """实例 ID 必须是非空 str（拒绝 bool 等）。"""
    if not isinstance(value, str):
        raise BatchError(
            f"{what} 必须为非空 str 实例 ID，实际为 {type(value).__name__}: {value!r}"
        )
    if not value:
        raise BatchError(f"{what} 不能为空字符串")
    return value


@dataclass(frozen=True)
class BatchInstance:
    """批处理中的一个独立实例。

    instance_id:
        调用者给定的非空字符串，批次内唯一；
    poly:
        该实例自己的多线性多项式（保留各自的变量数与次数上界）；
    challenges:
        长度恰为该实例变量数的固定挑战序列；
    claimed_sum:
        可选初始主张；None 时由 prover 直接枚举得出。显式给出时必须与
        布尔立方体真实求和一致（诚实 prover 不证明假主张）。
    """

    instance_id: str
    poly: MultilinearPoly
    challenges: tuple[int, ...]
    claimed_sum: int | None = None

    def __post_init__(self):
        _check_instance_id(self.instance_id, "instance_id")
        if not isinstance(self.poly, MultilinearPoly):
            raise BatchError(
                f"实例 {self.instance_id!r}: poly 必须为 MultilinearPoly，"
                f"实际为 {type(self.poly).__name__}"
            )
        if self.poly.num_vars == 0:
            raise BatchError(
                f"实例 {self.instance_id!r}: 空变量多项式无法运行 sum-check"
            )
        if self.poly.num_vars > MAX_VARS:  # 构造器已保证，防御性保留
            raise BatchError(f"实例 {self.instance_id!r}: num_vars 超过上限 {MAX_VARS}")
        # 归一化挑战为 tuple，并完成域元素校验（早失败，绝不进入计算阶段）。
        try:
            challenges = _check_challenges(self.challenges, self.poly.num_vars)
        except BatchError:
            raise
        except (ProtocolError, TypeError, ValueError) as exc:
            raise BatchError(f"实例 {self.instance_id!r}: {exc}") from exc
        object.__setattr__(self, "challenges", challenges)
        if self.claimed_sum is not None:
            from .field import check_field_element

            try:
                claimed = check_field_element(self.claimed_sum, "claimed_sum")
            except (TypeError, ValueError) as exc:
                raise BatchError(f"实例 {self.instance_id!r}: {exc}") from exc
            object.__setattr__(self, "claimed_sum", claimed)


@dataclass(frozen=True)
class InstanceReport:
    """单个实例的核验结论。"""

    instance_id: str
    accepted: bool
    failed_round: int | None
    reason: str | None
    num_vars: int
    claimed_sum: int
    direct_evaluation: int
    transcript: Transcript
    verify_result: VerifyResult

    def pretty(self) -> str:
        status = "接受" if self.accepted else f"拒绝（首个失败轮 {self.failed_round}）"
        lines = [
            f"实例 {self.instance_id!r} [n={self.num_vars}]: {status}",
            f"  初始主张={self.claimed_sum}，最终主张={self.transcript.final_claim}，"
            f"直接点值={self.direct_evaluation}",
        ]
        if self.reason:
            lines.append(f"  原因: {self.reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class BatchReport:
    """整批汇总：逐实例结论与整体判定。"""

    accepted: bool
    results: tuple[InstanceReport, ...]

    @property
    def count(self) -> int:
        return len(self.results)

    @property
    def accepted_count(self) -> int:
        return sum(1 for r in self.results if r.accepted)

    def by_id(self) -> dict[str, InstanceReport]:
        return {r.instance_id: r for r in self.results}

    def transcript_by_id(self) -> dict[str, Transcript]:
        """返回 ``{实例 ID: 转录}``，可直接交给另一进程的 :func:`verify_batch`。"""
        return {r.instance_id: r.transcript for r in self.results}

    def pretty(self) -> str:
        header = (
            f"批处理汇总 (F_{P}): {self.accepted_count}/{self.count} 个实例接受，"
            f"整批判定 accepted={self.accepted}"
        )
        return "\n".join([header] + [r.pretty() for r in self.results])


def _normalize_instances(instances) -> list[BatchInstance]:
    """把批次输入归一化为列表，并在任何计算前完成全部结构校验。"""
    if isinstance(instances, (str, bytes)) or not hasattr(instances, "__iter__"):
        raise BatchError("instances 必须为 BatchInstance 的可迭代序列")
    items = list(instances)
    if not items:
        raise BatchError("批次不能为空：至少需要 1 个实例")
    if len(items) > MAX_BATCH_INSTANCES:
        raise BatchError(
            f"批次含 {len(items)} 个实例，超过上限 {MAX_BATCH_INSTANCES}"
        )
    seen: set[str] = set()
    for pos, inst in enumerate(items):
        if not isinstance(inst, BatchInstance):
            raise BatchError(
                f"instances[{pos}] 必须为 BatchInstance，实际为 "
                f"{type(inst).__name__}"
            )
        if inst.instance_id in seen:
            raise BatchError(f"实例 ID {inst.instance_id!r} 在批次中重复")
        seen.add(inst.instance_id)
    return items


def _check_budget(eval_budget, what: str = "eval_budget"):
    if eval_budget is None:
        return None
    if isinstance(eval_budget, bool) or not isinstance(eval_budget, int):
        raise BatchError(f"{what} 必须为非 bool 整数或 None")
    if eval_budget < 0:
        raise BatchError(f"{what} 不可为负")
    return eval_budget


def prove_batch(instances, *, eval_budget: int | None = None) -> BatchReport:
    """对一批实例逐实例运行诚实 prover，并立刻独立核验、汇总。

    - 所有输入在任何计算前校验完毕（非法时抛 :class:`BatchError`，
      不返回半成品报告，输入对象不被修改）；
    - ``eval_budget`` 与单实例 :func:`~sumcheck.protocol.prove` 语义相同，
      作用于每个实例的每次布尔立方体枚举；任一实例超预算抛
      :class:`~sumcheck.errors.BudgetExhausted`（资源问题，可放宽预算重试）；
    - 转录通过 :func:`verify_batch` 同款检查后才汇总：诚实批次的
      ``accepted`` 恒为 True。
    """
    items = _normalize_instances(instances)
    budget = _check_budget(eval_budget)

    # 阶段 1（预算预检 + 主张预检）：在构造任何转录前发现全部资源/主张错误，
    # 保证失败时没有“部分实例已出证明”的半更新状态。
    for inst in items:
        size = 1 << inst.poly.num_vars
        if budget is not None and size > budget:
            raise BudgetExhausted(
                f"实例 {inst.instance_id!r}: 布尔立方体含 {size} 个求值点，"
                f"超过预算 eval_budget={budget}"
            )
        actual_sum = inst.poly.boolean_sum(eval_budget=budget)
        if inst.claimed_sum is not None and inst.claimed_sum != actual_sum:
            raise BatchError(
                f"实例 {inst.instance_id!r}: claimed_sum={inst.claimed_sum} "
                f"与布尔立方体真实求和 {actual_sum} 不一致；诚实 prover 不能证明假主张"
            )

    # 阶段 2：预检全部通过后才逐实例构造转录并独立核验。
    reports: list[InstanceReport] = []
    for inst in items:
        transcript = prove(
            inst.poly,
            inst.challenges,
            claimed_sum=inst.claimed_sum,
            eval_budget=budget,
        )
        # 把实例 ID 绑进转录（单实例 prove 不携带 ID，此处显式绑定）。
        transcript = replace(transcript, instance_id=inst.instance_id)
        result = _verify_one(inst, transcript, budget)
        reports.append(result)
    return BatchReport(
        accepted=all(r.accepted for r in reports),
        results=tuple(reports),
    )


def _verify_one(
    inst: BatchInstance,
    transcript: Transcript,
    eval_budget: int | None,
) -> InstanceReport:
    """核验单个实例（ID 绑定已在调用处或本函数内完成）。"""
    iid = inst.instance_id
    if not isinstance(transcript, Transcript):
        raise BatchError(
            f"实例 {iid!r}: 转录必须为 Transcript，实际为 "
            f"{type(transcript).__name__}"
        )
    # ID 绑定：转录只能用于它所声明的那个实例。
    # 声明缺失（如手工拼装/单实例转录混入批次）同样拒绝。
    bound = transcript.instance_id
    if bound is None:
        raise BatchError(
            f"实例 {iid!r}: 转录未绑定实例 ID（instance_id=None），"
            "不能在批次中使用，以防把一份消息用于另一实例"
        )
    if bound != iid:
        raise BatchError(
            f"实例 ID 绑定不符：提交给实例 {iid!r} 的转录实际绑定到 "
            f"{bound!r}；禁止把一个实例的消息用于另一个实例"
        )

    # 结构校验沿用单实例 verify，错误信息补上实例定位。
    try:
        result = verify(inst.poly, transcript)
    except ProtocolError as exc:
        raise BatchError(f"实例 {iid!r}: {exc}") from exc

    # 小域直接求和核验：verifier 自己枚举布尔立方体，独立核对初始主张。
    direct_sum = inst.poly.boolean_sum(eval_budget=eval_budget)
    if direct_sum != transcript.initial_claim:
        result = VerifyResult(
            False, 0,
            f"小域直接求和核验失败：布尔立方体真实求和为 {direct_sum}，"
            f"转录初始主张为 {transcript.initial_claim}",
            transcript.final_claim,
            result.direct_evaluation,
            result.rounds_checked,
        )

    return InstanceReport(
        instance_id=iid,
        accepted=result.accepted,
        failed_round=result.failed_round,
        reason=result.reason,
        num_vars=inst.poly.num_vars,
        claimed_sum=transcript.initial_claim,
        direct_evaluation=result.direct_evaluation,
        transcript=transcript,
        verify_result=result,
    )


def verify_batch(instances, transcripts, *, eval_budget: int | None = None) -> BatchReport:
    """独立核验一批实例的转录，逐实例汇总，任一失败则整批失败。

    instances:
        与 :func:`prove_batch` 相同的 :class:`BatchInstance` 序列
        （``claimed_sum`` 在核验路径不参与，主张以转录为准并做直接求和核验）；
    transcripts:
        ``{实例 ID: Transcript}`` 映射。ID 集合必须与实例集合**完全一致**，
        多一个、少一个、转录绑错 ID 都抛 :class:`BatchError`。

    与单实例 :func:`~sumcheck.protocol.verify` 一致：转录结构不合法抛异常；
    结构合法但主张为假时在对应 :class:`InstanceReport` 中返回拒绝。
    """
    items = _normalize_instances(instances)
    budget = _check_budget(eval_budget)

    if not hasattr(transcripts, "items") or isinstance(transcripts, (str, bytes, list, tuple)):
        raise BatchError(
            "transcripts 必须为 {{实例 ID: Transcript}} 映射（dict）"
        )
    try:
        mapping = dict(transcripts.items())
    except (AttributeError, TypeError) as exc:
        raise BatchError("transcripts 必须为 {实例 ID: Transcript} 映射（dict）") from exc

    expected_ids = {inst.instance_id for inst in items}
    given_ids = set(mapping)
    if expected_ids != given_ids:
        missing = sorted(expected_ids - given_ids)
        extra = sorted(given_ids - expected_ids)
        details = []
        if missing:
            details.append(f"缺少 {missing}")
        if extra:
            details.append(f"多出 {extra}")
        raise BatchError(
            "转录 ID 集合与实例 ID 集合不一致：" + "，".join(details)
        )
    for key in mapping:
        _check_instance_id(key, "transcripts 的键")

    # 预算预检：核验阶段也要枚举布尔立方体，先统一发现资源不足。
    for inst in items:
        size = 1 << inst.poly.num_vars
        if budget is not None and size > budget:
            raise BudgetExhausted(
                f"实例 {inst.instance_id!r}: 布尔立方体含 {size} 个求值点，"
                f"超过预算 eval_budget={budget}"
            )

    reports = [_verify_one(inst, mapping[inst.instance_id], budget) for inst in items]
    return BatchReport(
        accepted=all(r.accepted for r in reports),
        results=tuple(reports),
    )
