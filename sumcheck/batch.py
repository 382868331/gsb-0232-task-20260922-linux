"""多实例批处理（batch）：一次输入多个 sum-check 实例，逐实例运行协议并汇总。

设计要点（严格遵循本轮题面，不做随机线性组合、不做 Fiat–Shamir）：

- 每个实例**保留自己的变量数与次数上界**：实例 A 可以 3 变量、实例 B 可以
  7 变量，互不影响；实例内逐轮次数上界仍由单实例
  :func:`sumcheck.protocol.verify` 按“前序挑战限制后的多项式”检查。
- **独立绑定实例 ID**：每个实例由调用者给定非空字符串 ID；批次转录的每个
  封套 :class:`BoundTranscript` 携带其 ID 与位置序号，verifier 按**位置 +
  精确 ID 匹配**重新绑定。把实例 A 的转录塞进实例 B 的槽位（错槽/整体换位/
  未知 ID/重复 ID/ID 缺失）一律抛 :class:`~sumcheck.errors.BatchError`，
  不会拿一份消息去核验另一个实例。
- **小域直接求和核验**：小域 F_101 上直接对 {0,1}^n 全枚举独立重算
  ``Σ f(b)``（独立于 prover 的逐项布尔点枚举，按真值表/含变量项数解析求和，
  不调用 :meth:`MultilinearPoly.boolean_sum` 也不调用 prover），与各实例转录
  的初始主张逐项比对；不符即该实例失败。
- **任何实例失败则整体失败**：单个实例的失败不中断其余实例的核验，
  :class:`BatchResult` 聚合全部实例结论，``accepted`` 为各实例的合取。

原子性：

- :func:`prove_batch` 在**任何**计算开始前校验整个批次的结构与全部挑战；
  任一实例非法（多项式、挑战长度/域元素、预算不足、显式假主张等）都直接抛
  异常，不返回“跑了一半”的批次转录，不修改任何输入对象。
- :func:`verify_batch` 同样先整体校验批次封套，再逐实例核验。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import BatchError, ProtocolError, TranscriptFormatError
from .field import P, check_field_element
from .polynomial import MAX_VARS, MultilinearPoly
from .protocol import (
    Transcript,
    VerifyResult,
    prove,
    verify,
)

# 单次批处理允许的实例数上限（教学库接口边界；与 MAX_TERMS 同量级）。
MAX_INSTANCES = 100


def _check_instance_id(instance_id) -> str:
    """实例 ID 必须为非空、非全空白的 str（拒绝 bool/bytes/数字等）。"""
    if not isinstance(instance_id, str):
        raise BatchError(
            f"实例 ID 必须为 str，实际为 {type(instance_id).__name__}: {instance_id!r}"
        )
    if not instance_id or not instance_id.strip():
        raise BatchError("实例 ID 不能为空字符串或纯空白字符串")
    return instance_id


@dataclass(frozen=True)
class BatchInstance:
    """批次中的一个实例：自带 ID、多项式、挑战（与可选主张/预算）。

    各实例的 ``poly.num_vars`` 互相独立（都在 1..MAX_VARS 内），
    ``challenges`` 长度必须恰为该实例自己的变量数。
    """

    instance_id: str
    poly: MultilinearPoly
    challenges: tuple[int, ...]
    claimed_sum: int | None = None
    eval_budget: int | None = None


@dataclass(frozen=True)
class BoundTranscript:
    """绑定到具体实例的转录封套。

    position:
        该实例在输入批次中的 0 基位置；绑定校验要求位置与 ID 同时匹配；
    instance_id / num_vars / challenges:
        生成转录时该实例的身份与上下文快照，核验时逐项重新比对；
    transcript:
        该实例的普通单实例 :class:`~sumcheck.protocol.Transcript`
        （旧数据结构与 :func:`sumcheck.protocol.verify` 完全复用）。
    """

    position: int
    instance_id: str
    num_vars: int
    challenges: tuple[int, ...]
    transcript: Transcript


@dataclass(frozen=True)
class BatchTranscript:
    """整批的诚实证明输出：实例 ID 元组 + 逐条绑定的转录。"""

    instance_ids: tuple[str, ...]
    items: tuple[BoundTranscript, ...]


@dataclass(frozen=True)
class InstanceResult:
    """单个实例的核验结论。

    accepted:
        单实例协议验证与小域直接求和核验全部通过；
    failed_round:
        首个失败轮（0 基）；最终直接点值检查失败时为 num_vars；
        小域直接求和核验失败时为 -1（区别于任何协议轮次）；通过时为 None；
    direct_sum:
        verifier 独立重算的布尔立方体求和；
    verify_result:
        单实例 :func:`~sumcheck.protocol.verify` 的完整结论（转录结构不合法
        导致它无法产生时为 None）。
    """

    position: int
    instance_id: str
    accepted: bool
    failed_round: int | None
    reason: str | None
    direct_sum: int | None
    verify_result: VerifyResult | None


@dataclass(frozen=True)
class BatchResult:
    """整批评核结论（不可变值对象）。

    accepted:
        全部实例接受才为 True；**任何实例失败则整体失败**；
    results:
        与输入同序的逐实例 :class:`InstanceResult`；
    accepted_count / failed_count:
        接受/失败实例数（二者之和等于实例总数）。
    """

    accepted: bool
    results: tuple[InstanceResult, ...]
    accepted_count: int = field(init=False)
    failed_count: int = field(init=False)

    def __post_init__(self):
        accepted_count = sum(1 for r in self.results if r.accepted)
        object.__setattr__(self, "accepted_count", accepted_count)
        object.__setattr__(self, "failed_count", len(self.results) - accepted_count)


def _coerce_instances(instances) -> tuple[BatchInstance, ...]:
    """校验批次容器本身：可迭代、非空、数量上限、元素类型、ID 唯一。"""
    if isinstance(instances, (str, bytes)):
        raise BatchError("instances 必须为 BatchInstance 的可迭代列表，不能是字符串")
    try:
        items = tuple(instances)
    except TypeError as exc:
        raise BatchError("instances 必须为 BatchInstance 的可迭代列表") from exc
    if len(items) == 0:
        raise BatchError("批次至少需要 1 个实例（空批次不允许）")
    if len(items) > MAX_INSTANCES:
        raise BatchError(f"实例数 {len(items)} 超过单批上限 {MAX_INSTANCES}")
    seen_ids: set[str] = set()
    for pos, inst in enumerate(items):
        if not isinstance(inst, BatchInstance):
            raise BatchError(
                f"instances[{pos}] 必须为 BatchInstance，实际为 {type(inst).__name__}"
            )
        _check_instance_id(inst.instance_id)
        if inst.instance_id in seen_ids:
            raise BatchError(f"实例 ID 重复：{inst.instance_id!r}（每个实例必须独立绑定）")
        seen_ids.add(inst.instance_id)
    return items


def independent_boolean_sum(poly: MultilinearPoly) -> int:
    """小域直接求和核验：独立重算 Σ_{b∈{0,1}^n} f(b)（mod p）。

    刻意**不**复用 :meth:`MultilinearPoly.boolean_sum`、
    :meth:`MultilinearPoly.evaluate` 或 prover，而是沿布尔立方体直接求和，
    并用与枚举实现不同的布尔单项式事实：在布尔点 b 上，
    单项式 ``Π_{i∈S} X_i`` 取值 1 当且仅当 ``S ⊆ support(b)``，否则为 0。
    因此对每个位掩码 mask（一个布尔赋值），

        f(mask) = Σ_{项 t: variables(t) ⊆ bits(mask)} coeff(t)   (mod p)

    遍历 2^n 个掩码累加即得总求和——一条与 prover 的逐点 restrict/枚举
    完全独立的计算路径（n≤8，至多 256 个掩码）。
    """
    n = poly.num_vars
    terms = tuple(poly.terms)
    # 预计算每项的 (系数, 变量集合位掩码)，集合包含判定用位运算。
    coeff_masks = tuple(
        (term.coeff, sum(1 << i for i in term.variables))
        for term in terms
        if term.coeff != 0
    )
    total = 0
    for assignment in range(1 << n):
        value = 0
        for coeff, var_mask in coeff_masks:
            if var_mask & ~assignment == 0:  # variables ⊆ support(assignment)
                value += coeff
        total = (total + value) % P
    return total


def _validate_instance_inputs(items: tuple[BatchInstance, ...]) -> None:
    """逐个校验实例的多项式/挑战/主张/预算（批次结构已由 _coerce 校验）。

    prove_batch 与 verify_batch 共用同一口径；任何非法都在结果产出前抛出，
    保证不留下半更新状态。
    """
    for pos, inst in enumerate(items):
        where = f"instances[{pos}](id={inst.instance_id!r})"
        if not isinstance(inst.poly, MultilinearPoly):
            raise BatchError(
                f"{where}.poly 必须为 MultilinearPoly，实际为 {type(inst.poly).__name__}"
            )
        if inst.poly.num_vars == 0:
            raise ProtocolError(f"{where}: 空变量多项式无法运行 sum-check：num_vars 至少为 1")
        if inst.poly.num_vars > MAX_VARS:
            raise ProtocolError(f"{where}: num_vars 超过上限 {MAX_VARS}")
        if isinstance(inst.challenges, (str, bytes)) or not hasattr(inst.challenges, "__len__"):
            raise BatchError(f"{where}.challenges 必须为长度 num_vars 的域元素序列")
        if len(inst.challenges) != inst.poly.num_vars:
            raise BatchError(
                f"{where}.challenges 长度 {len(inst.challenges)} 与该实例变量数 "
                f"{inst.poly.num_vars} 不一致（各实例保留自己的变量数）"
            )
        for j, ch in enumerate(inst.challenges):
            try:
                check_field_element(ch, f"{where}.challenges[{j}]")
            except (TypeError, ValueError) as exc:
                raise BatchError(str(exc)) from exc
        if inst.claimed_sum is not None:
            try:
                check_field_element(inst.claimed_sum, f"{where}.claimed_sum")
            except (TypeError, ValueError) as exc:
                raise BatchError(str(exc)) from exc
        if inst.eval_budget is not None:
            if isinstance(inst.eval_budget, bool) or not isinstance(inst.eval_budget, int):
                raise BatchError(f"{where}.eval_budget 必须为非 bool 整数或 None")
            if inst.eval_budget < 0:
                raise BatchError(f"{where}.eval_budget 不可为负")


def prove_batch(instances, *, eval_budget: int | None = None) -> BatchTranscript:
    """诚实批量 prover：先整批校验，再逐实例调用单实例 :func:`prove`。

    instances:
        :class:`BatchInstance` 的非空可迭代（1..MAX_INSTANCES 个），ID 唯一；
    eval_budget:
        批次级默认预算；实例自身带 ``eval_budget`` 时以实例为准。

    任何实例不合法（多项式/挑战/主张/预算/ID 等）都在批次结果产出前抛出，
    不会留下部分生成的批次转录，也不修改输入对象。
    """
    items = _coerce_instances(instances)
    _validate_instance_inputs(items)
    # 输入已全部校验；prove 自身的失败仍会整体中止，但由于此处尚未构造任何对外
    # 返回对象，不存在半更新状态。
    bound: list[BoundTranscript] = []
    for pos, inst in enumerate(items):
        budget = inst.eval_budget if inst.eval_budget is not None else eval_budget
        transcript = prove(
            inst.poly,
            tuple(inst.challenges),
            claimed_sum=inst.claimed_sum,
            eval_budget=budget,
        )
        bound.append(
            BoundTranscript(
                position=pos,
                instance_id=inst.instance_id,
                num_vars=inst.poly.num_vars,
                challenges=tuple(inst.challenges),
                transcript=transcript,
            )
        )
    return BatchTranscript(tuple(inst.instance_id for inst in items), tuple(bound))


def _check_bound_envelope(instances: tuple[BatchInstance, ...],
                          batch_transcript: BatchTranscript) -> None:
    """绑定校验：封套数量/类型、位置序号、ID 精确匹配、num_vars/挑战快照一致。

    任何把一份消息用于另一实例的尝试（错槽、换位、改 ID、未知 ID…）都在此被
    :class:`BatchError` / :class:`TranscriptFormatError` 拒绝。
    """
    if not isinstance(batch_transcript, BatchTranscript):
        raise BatchError(
            f"batch_transcript 必须为 BatchTranscript，实际为 {type(batch_transcript).__name__}"
        )
    if len(batch_transcript.instance_ids) != len(instances):
        raise BatchError(
            f"批次转录声明 {len(batch_transcript.instance_ids)} 个实例，"
            f"输入实例数为 {len(instances)}"
        )
    if len(batch_transcript.items) != len(instances):
        raise BatchError(
            f"批次转录含 {len(batch_transcript.items)} 条绑定转录，应为 {len(instances)} 条"
        )
    for pos, inst in enumerate(instances):
        expected_id = batch_transcript.instance_ids[pos]
        if expected_id != inst.instance_id:
            raise BatchError(
                f"instance_ids[{pos}]={expected_id!r} 与输入实例 ID "
                f"{inst.instance_id!r} 不一致（身份绑定失败，拒绝跨实例复用消息）"
            )
        item = batch_transcript.items[pos]
        if not isinstance(item, BoundTranscript):
            raise TranscriptFormatError(
                f"items[{pos}] 必须为 BoundTranscript，实际为 {type(item).__name__}"
            )
        if item.position != pos:
            raise BatchError(
                f"位置 {pos} 的绑定转录自称 position={item.position}"
                "（位置序号绑定失败，拒绝错槽消息）"
            )
        if item.instance_id != inst.instance_id:
            raise BatchError(
                f"位置 {pos} 的绑定转录 ID={item.instance_id!r} 与该位置实例 ID "
                f"{inst.instance_id!r} 不一致（拒绝把一份消息用于另一实例）"
            )
        if item.num_vars != inst.poly.num_vars:
            raise BatchError(
                f"实例 {inst.instance_id!r} 的绑定转录 num_vars={item.num_vars}，"
                f"实际变量数 {inst.poly.num_vars}（身份快照不一致）"
            )
        if tuple(item.challenges) != tuple(inst.challenges):
            raise BatchError(
                f"实例 {inst.instance_id!r} 的绑定转录挑战 {tuple(item.challenges)} "
                f"与该实例挑战 {tuple(inst.challenges)} 不一致（挑战绑定失败）"
            )
        if not isinstance(item.transcript, Transcript):
            raise TranscriptFormatError(
                f"实例 {inst.instance_id!r} 的 transcript 必须为单实例 Transcript，"
                f"实际为 {type(item.transcript).__name__}"
            )
        if item.transcript.challenges != tuple(inst.challenges):
            raise BatchError(
                f"实例 {inst.instance_id!r} 内层转录挑战 {item.transcript.challenges} "
                f"与该实例挑战 {tuple(inst.challenges)} 不一致（内层挑战绑定失败）"
            )


def verify_batch(instances, batch_transcript: BatchTranscript) -> BatchResult:
    """批量 verifier：绑定校验后逐实例验证，并用小域直接求和独立核验。

    批次封套不合法（数量/位置/ID/挑战绑定失败）抛 :class:`BatchError`；
    某条单实例转录结构不合法抛 :class:`TranscriptFormatError`（结构错误不与
    逻辑失败混合，与单实例语义一致）；结构合法但主张为假、或小域直接求和
    与初始主张不符的实例聚合为 ``accepted=False``，**任一失败则整批失败**。
    """
    items = _coerce_instances(instances)
    _validate_instance_inputs(items)
    _check_bound_envelope(items, batch_transcript)

    results: list[InstanceResult] = []
    for pos, inst in enumerate(items):
        item = batch_transcript.items[pos]
        direct_sum = independent_boolean_sum(inst.poly)
        # 单实例协议验证（结构不合法会抛 TranscriptFormatError，按契约向上传播）。
        vr = verify(inst.poly, item.transcript)
        accepted = vr.accepted
        failed_round = vr.failed_round
        reason = vr.reason
        # 小域直接求和核验：初始主张必须等于独立重算的布尔立方体求和。
        if accepted and item.transcript.initial_claim != direct_sum:
            accepted = False
            failed_round = -1
            reason = (
                f"小域直接求和核验失败：独立重算 Σ f(b)={direct_sum}，"
                f"转录初始主张={item.transcript.initial_claim}"
            )
        results.append(
            InstanceResult(
                position=pos,
                instance_id=inst.instance_id,
                accepted=accepted,
                failed_round=failed_round,
                reason=reason,
                direct_sum=direct_sum,
                verify_result=vr,
            )
        )
    overall = all(r.accepted for r in results)
    return BatchResult(overall, tuple(results))
