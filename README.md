# sumcheck 教学库：F_101 多线性多项式的有限域求和证明

一个**仅用于教学**的小型 sum-check 协议实现：对素域 F_p（p=101）上的多线性
多项式 f，证明其在布尔立方体上的求和主张

```
C = Σ_{(b_0,…,b_{n-1}) ∈ {0,1}^n} f(b_0,…,b_{n-1})   (mod 101)
```

prover 逐轮发送关于一个变量的一元多项式（多线性 ⇒ 次数 ≤ 1），verifier 逐轮
检查 `g_i(0)+g_i(1) = 上一主张`，挑战由**调用者预先给定固定数组**，最后
verifier 直接对原多项式在挑战点求值完成判定。

**本轮新增多实例批处理**：`prove_batch`/`verify_batch` 一次接收多个实例，
每个实例**保留自己的变量数与次数上界**（可 3 变量与 7 变量混在一批），逐实例
运行上面的旧协议并把结论聚合进 `BatchResult`；每个实例由调用者给定独立 ID，
批次转录按**位置 + 精确 ID + 挑战数组**重新绑定，把一份消息塞进另一实例的槽位
一律拒绝；verifier 另用一条独立的小域布尔立方体直接求和核对每实例的初始主张，
**任一实例失败则整批失败**。本轮不做随机线性批处理，也不做 Fiat–Shamir
转录绑定。

> 免责声明：本库**不提供零知识性**，不模拟随机性（挑战非 Fiat–Shamir、非随机
> 抽取），不做多项式承诺，不实现任何完整行业标准协议，**不可用于生产安全场景**。
> 它只实现本题契约所定义的有限数学子集。

## 运行环境

- Python **3.14.7**（代码不使用 3.14 之外的特性；3.10+ 亦可运行，验收环境为 3.14.7）
- **仅标准库**，安装 Python 后无需 `pip install` 任何依赖
- Linux 原生、完全离线：不需要网络、账号、密钥、数据库、中间件、云 API 或 Docker

## 准确命令

```bash
# 必要测试（单实例对拍独立参考 + 批次正常/边界/失败/绑定；共 71 个测试）
python -m unittest discover -s tests -v

# 固定输入演示（实测约 0.05 秒，远低于 90 秒预算；无 sleep、无预录结果）
# 8 个场景：单实例接受/上限规模/篡改拒绝/结构异常 + 批次接受/批次失败聚合/
# 跨实例绑定拒绝/非法批次输入
python demo.py
```

## 快速上手

```python
from sumcheck import MultilinearPoly, Term, prove, verify

# f(x0,x1,x2) = 3 + 2*x0 + x1 + x0*x2
poly = MultilinearPoly(3, [
    Term(3, ()),       # 常量项
    Term(2, (0,)),     # 2*x0
    Term(1, (1,)),     # x1
    Term(1, (0, 2)),   # x0*x2
])

challenges = (17, 42, 3)            # 调用者给定的固定挑战（每个 ∈ [0,100]）
transcript = prove(poly, challenges)
print(transcript.pretty())

result = verify(poly, transcript)
assert result.accepted
print(result.final_claim, result.direct_evaluation)
```

### 多实例批处理

```python
from sumcheck import BatchInstance, prove_batch, verify_batch

# 三个变量数不同的实例（3/2/1），各自独立的挑战数组与实例 ID
instances = [
    BatchInstance("A", poly,                          (17, 42, 3)),
    BatchInstance("B", MultilinearPoly(2, [Term(1, ()), Term(1, (0, 1))]), (2, 3)),
    BatchInstance("C", MultilinearPoly(1, [Term(5, ()), Term(7, (0,))]),   (4,)),
]
batch_transcript = prove_batch(instances)
batch_result = verify_batch(instances, batch_transcript)
assert batch_result.accepted                 # 任一实例失败则整体 False
for r in batch_result.results:               # 逐实例结论，与输入同序
    print(r.instance_id, r.accepted, r.direct_sum)
# A True 38   B True 5   C True 17
```

## 公开接口与数据结构

全部从 `sumcheck` 包导入：

| 名称 | 说明 |
| --- | --- |
| `P = 101` | 素域模数；域元素一律为区间 `[0, 100]` 内的规范 `int` |
| `Term(coeff, variables)` | 单项式 `coeff * Π X_i`；`variables` 为变量下标可迭代（0 基），空集合表示常量项；内部存为不可变 `frozenset` |
| `MultilinearPoly(num_vars, terms)` | 多线性多项式；构造即校验，重复的变量集合项被**拒绝**（不静默合并） |
| `prove(poly, challenges, *, claimed_sum=None, eval_budget=None) -> Transcript` | 诚实 prover，逐轮真实枚举剩余布尔立方体 |
| `verify(poly, transcript) -> VerifyResult` | 先做转录结构校验，再逐轮核对，最后独立直接求值 |
| `Transcript` / `RoundMessage` / `VerifyResult` | 不可变（frozen dataclass）值对象 |
| `PolynomialError` / `ProtocolError` / `TranscriptFormatError` / `BudgetExhausted` | 异常类型，公共基类 `SumcheckError` |
| `BatchInstance(instance_id, poly, challenges, claimed_sum=None, eval_budget=None)` | 批次中的一个实例：自带非空字符串 ID、多项式与该实例自己的挑战数组 |
| `prove_batch(instances, *, eval_budget=None) -> BatchTranscript` | 整批先校验、再逐实例调用 `prove`；每条转录包成携带位置/ID/变量数/挑战快照的 `BoundTranscript` |
| `verify_batch(instances, batch_transcript) -> BatchResult` | 绑定校验后逐实例 `verify`，并做独立小域直接求和核验；任一实例失败则整体失败 |
| `BatchTranscript` / `BoundTranscript` / `InstanceResult` / `BatchResult` | 批次值对象（不可变）；`MAX_INSTANCES=100` 为单批实例数上限 |
| `independent_boolean_sum(poly)` | 批次 verifier 使用的独立小域直接求和（也可单独调用） |
| `BatchError` | 批次级错误（空批次、ID 缺失/重复/非 str、跨实例绑定失败等），是 `ProtocolError` 子类 |

`RoundMessage` 字段：`index`、`coeffs`（升幂系数 `(a0,)` 或 `(a0,a1)`，
表示 `g(t)=a0+a1*t`）、`input_claim`、非末轮的 `challenge`/`output_claim`。

`VerifyResult` 字段：`accepted`、`failed_round`（首个失败轮，0 基；
最终直接点值检查失败时为 `num_vars`；通过时为 `None`）、`reason`、
`final_claim`、`direct_evaluation`、`rounds_checked`。

辅助方法：`poly.evaluate(point)`（任意域点直接求值）、
`poly.boolean_sum(eval_budget=None)`（布尔立方体全枚举）、
`poly.restricted({var: value})`（固定变量后重新压缩下标）、
`poly.degree_of(var)`（0 或 1）。

## 输入上限（接口边界）

- 变量数 `1 ≤ num_vars ≤ 8`（`MultilinearPoly` 本身允许 0 变量常量多项式，
  但 `prove`/`verify` 明确拒绝空变量并报错）；
- 项数 `≤ 100`；
- 每个项中同一变量至多出现一次（多线性）；
- 挑战数组长度必须恰为 `num_vars`，每个元素为 `[0,100]` 的整数。
- 批次实例数 `1 ≤ len(instances) ≤ 100`（`MAX_INSTANCES`）；实例 ID 必须为
  非空、非纯空白的 `str`，且同批内唯一。

短演示不穷举上限规模；`demo.py` 的演示 2 覆盖 8 变量场景。

## 错误语义（不静默纠正）

- **数值输入拒绝 NaN / Infinity / 一切 `float`**：有限域只接受整数，避免隐式取整；
- **整数参数拒绝 `bool`**（`True/False` 虽是 `int` 子类但语义不同），
  覆盖 `num_vars`、系数、变量下标、挑战、预算等所有整数入口；
- **域外整数**（如 101、-1）拒绝；错误信息带可定位路径，如
  `rounds[1].coeffs[0]: 域元素 101 越界，必须落在 [0, 100]`；
- **非法次数**（多线性下系数元组长度 > 2）以及**次数超过该变量在当前
  （前序挑战限制后）多项式中的实际次数**，抛 `TranscriptFormatError`；
- **结构合法但主张为假**（系数篡改、轮间不闭合、最终点值不符）不抛异常，
  返回 `accepted=False` 与**首个失败轮**；
- **空变量**：`prove`/`verify` 抛 `ProtocolError`；
- **有状态/失败原子性**：`prove` 是纯函数，失败时抛异常且不返回半成品转录，
  输入对象不被修改；转录为不可变对象，篡改只能通过构造新对象完成；
- **预算耗尽 ≠ 证明无解**：`eval_budget` 给出单次布尔立方体枚举的求值点上限，
  `2^n` 超预算抛 `BudgetExhausted`（资源不足，证明本身可能为真；同一输入
  放宽预算即可成功，测试中有对拍）。本协议没有搜索过程：诚实 prover 对真主张
  总能在 `2^n` 预算内完成，因此不存在“搜索预算耗尽”与“无解”混淆的路径。
- 诚实 prover 拒绝为与真实求和不符的 `claimed_sum` 造证明（抛 `ProtocolError`）；
  拒绝路径通过对真实转录做篡改来演示与测试。

### 批处理语义（本轮新增）

- **逐实例独立**：每个实例保留自己的 `num_vars`、挑战数组与次数上界；实例内
  的次数检查仍按旧协议“前序挑战限制后的多项式”口径。
- **身份绑定**：`BoundTranscript` 携带 `position`、`instance_id`、`num_vars`、
  `challenges` 快照。`verify_batch` 按位置比对 ID、位置序号、变量数、封套
  挑战和内层转录挑战：交换两条转录、改 ID、错槽、未知 ID 一律抛 `BatchError`，
  一份消息不可能被用于另一个实例的核验。
- **小域直接求和核验**：`independent_boolean_sum` 用一条独立于 prover 的路径
  （遍历 2^n 个布尔位掩码，按“变量集合 ⊆ 赋值支撑”判定单项式取值）重算
  `Σ f(b)`，与转录初始主张比对。它与测试里的独立参考、解析公式
  `Σ_term c·2^(n−|S|)`、朴素逐点求和四路互相对拍。注意固定挑战协议下单实例
  verifier 不检查初始主张 C（演示与测试中有“假 C 但终点对上”的转录，
  单实例 `verify` 接受、批次核验拒绝的实际例子）。
- **聚合而非短路**：某实例结构合法但主张为假时，其余实例照常核验，
  `BatchResult.results` 与输入同序给出每实例结论（`failed_round` 含义同单实例；
  小域求和失败为 `-1`），`accepted = 全部接受`，并带 `accepted_count`/
  `failed_count`。某实例转录**结构不合法**（域外数、非法次数等）仍直接抛
  `TranscriptFormatError`，与单实例的“结构错误 vs 逻辑拒绝”分层保持一致。
- **失败原子性**：`prove_batch` 在任何证明计算开始前校验整批结构与全部实例的
  多项式/挑战/主张/预算；任一非法直接抛异常，不返回半成品批次，输入对象不改。
  批次级 `eval_budget` 作为默认值，实例自带 `eval_budget` 时以实例为准。

## 设计取舍

1. **prover 直接枚举**剩余布尔立方体（n≤8 ⇒ 至多 256 点），代码短、结果可独立
   核验；没有用流式/多线性扩展等优化。
2. **轮多项式表示为升幂系数元组**：次数 0 时只发 `(a0,)`（紧规范形式），
   次数 1 时发 `(a0,a1)`；verifier 按“前序挑战限制后的多项式”计算该变量的
   实际次数上界，与 prover 同一口径（例：`f=x0*x1` 且 `r0=0` 时第二轮次数降为 0）。
3. **挑战固定且外部给定**：确定性、可复现、便于测试；不使用也不模拟随机预言机。
4. **结构错误与逻辑拒绝分离**：转录格式问题抛异常，主张为假返回结构化结论，
   调用者可分别处理。
5. **最终判定是直接点值检查**：verifier 自己用原多项式计算
   `f(r_0,…,r_{n-1})`，不信任转录中的最终主张。
6. 测试中的期望值来自 `tests/reference.py` —— 一份**完全独立重写**的 F_101
   算术与逐轮定义，不 import 被测核心；prove/verify 往返一致仅作为补充测试。

## 误差口径

全部计算为**精确整数模 101 算术**，任何路径都不产生或接受浮点数，因此
**不存在浮点误差，也没有容差（tolerance）参数**。所有相等判断均为模 101 下
的精确相等；不会用宽容差掩盖边界错误。

## 已知限制

- 仅支持 p=101、多线性多项式（每变量次数 ≤ 1）、n≤8、项数≤100；
- 单批实例数 ≤100；批处理是“逐实例独立证明 + 结果聚合 + 身份绑定”，
  **不是**随机线性组合批处理，不缩短总证明长度，也没有 Fiat–Shamir 哈希转录；
- prover 时间/空间为 O(2^n · 项数) 量级（批次为各实例之和），不适合大规模电路；
- 挑战固定外部给定，无随机性、无绑定承诺、无零知识性；
- 不防范恶意 prover 之外的任何威胁模型，不声称行业标准兼容；
- verifier 的直接点值检查要求其持有完整原多项式（本题“直接算原多项式点值”
  的契约如此）。

## 仓库结构

```
sumcheck/          被测库（field/polynomial/protocol/batch/errors）
tests/             unittest 测试 + 独立参考实现 tests/reference.py
demo.py            固定输入演示：单实例与批次的正常、上限、篡改拒绝与结构异常
README.md
requirements.txt   仅注释说明标准库；无第三方依赖
```
