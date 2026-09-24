# sumcheck 教学库：F_101 多线性多项式的有限域求和证明

一个**仅用于教学**的小型 sum-check 协议实现：对素域 F_p（p=101）上的多线性
多项式 f，证明其在布尔立方体上的求和主张

```
C = Σ_{(b_0,…,b_{n-1}) ∈ {0,1}^n} f(b_0,…,b_{n-1})   (mod 101)
```

prover 逐轮发送关于一个变量的一元多项式（多线性 ⇒ 次数 ≤ 1），verifier 逐轮
检查 `g_i(0)+g_i(1) = 上一主张`，挑战由**调用者预先给定固定数组**，最后
verifier 直接对原多项式在挑战点求值完成判定。

**多实例批处理**（本轮新增）：一次输入多个实例，每个实例保留各自的变量数
与次数上界，逐实例运行同一套单实例协议并汇总；实例以调用者给定的 **ID
独立绑定**（转录携带所属 ID，禁止把一份消息用于另一实例），verifier 对每个
实例在小域上**直接枚举布尔立方体求和**独立核对初始主张，任何实例失败则
整批失败。本库**不做**随机线性批处理（不把多个实例压成一个和），也不做
Fiat–Shamir 转录绑定。

> 免责声明：本库**不提供零知识性**，不模拟随机性（挑战非 Fiat–Shamir、非随机
> 抽取），不做多项式承诺，不实现任何完整行业标准协议，**不可用于生产安全场景**。
> 它只实现本题契约所定义的有限数学子集。

## 运行环境

- Python **3.14.7**（代码不使用 3.14 之外的特性；3.10+ 亦可运行，验收环境为 3.14.7）
- **仅标准库**，安装 Python 后无需 `pip install` 任何依赖
- Linux 原生、完全离线：不需要网络、账号、密钥、数据库、中间件、云 API 或 Docker

## 准确命令

```bash
# 必要测试（含独立参考实现对拍、篡改/非法次数/域外数/空变量等边界）
python -m unittest discover -s tests -v

# 固定输入演示（单实例 4 段 + 多实例批处理 1 段；实测约 0.05 秒，
# 远低于 90 秒预算；无 sleep、无预录结果）
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

# 不同实例可有不同变量数；各自保留 n 与（多线性）次数上界
instances = [
    BatchInstance("inst-a", MultilinearPoly(2, [Term(1, ()), Term(1, (0, 1))]), (2, 3)),
    BatchInstance("inst-b", poly,                       # 上面的 3 变量多项式
                  (17, 42, 3), claimed_sum=None),
    BatchInstance("inst-c", MultilinearPoly(1, [Term(1, (0,))]), (5,)),
]

report = prove_batch(instances)          # 逐实例 prove 并立即独立核验
assert report.accepted                   # 任一则整体失败
for r in report.results:
    print(r.instance_id, r.claimed_sum, r.direct_evaluation, r.accepted)

# prover/verifier 可分开发送：只交 {实例 ID: 转录}，ID 必须一一对应
transcripts = report.transcript_by_id()
report2 = verify_batch(instances, transcripts)
assert report2.accepted
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
| `BatchInstance(instance_id, poly, challenges, *, claimed_sum=None)` | 批处理中的一个独立实例（非空字符串 ID + 自己的多项式与挑战） |
| `prove_batch(instances, *, eval_budget=None) -> BatchReport` | 逐实例 prove 并独立核验后汇总；输入预检全过才开始，失败不留半成品 |
| `verify_batch(instances, transcripts, *, eval_budget=None) -> BatchReport` | 按 ID 一一对应核验 `{ID: Transcript}`，小域直接求和 + 逐轮检查 + 直接点值 |
| `BatchReport` / `InstanceReport` | 整批汇总（`accepted`/`count`/`accepted_count`/`by_id()`）与逐实例结论 |
| `BatchError` | 批处理结构/ID 错误（`ProtocolError` 子类）；主张为假则在报告中返回 |

`RoundMessage` 字段：`index`、`coeffs`（升幂系数 `(a0,)` 或 `(a0,a1)`，
表示 `g(t)=a0+a1*t`）、`input_claim`、非末轮的 `challenge`/`output_claim`。

`VerifyResult` 字段：`accepted`、`failed_round`（首个失败轮，0 基；
最终直接点值检查失败时为 `num_vars`；通过时为 `None`）、`reason`、
`final_claim`、`direct_evaluation`、`rounds_checked`。

`Transcript` 新增可选字段 `instance_id`（默认 `None`）：单实例
`prove`/`verify` 不使用它，批处理把转录显式绑定到实例 ID。旧的 5 个位置
参数构造方式完全不变。`BatchReport` 字段/方法：`accepted`、`results`、
`count`、`accepted_count`、`by_id()`、`transcript_by_id()`、`pretty()`；
`InstanceReport` 含 `instance_id`、`accepted`、`failed_round`、`reason`、
`num_vars`、`claimed_sum`、`direct_evaluation`、`transcript`、`verify_result`。

辅助方法：`poly.evaluate(point)`（任意域点直接求值）、
`poly.boolean_sum(eval_budget=None)`（布尔立方体全枚举）、
`poly.restricted({var: value})`（固定变量后重新压缩下标）、
`poly.degree_of(var)`（0 或 1）。

## 输入上限（接口边界）

- 变量数 `1 ≤ num_vars ≤ 8`（`MultilinearPoly` 本身允许 0 变量常量多项式，
  但 `prove`/`verify` 明确拒绝空变量并报错）；
- 项数 `≤ 100`；
- 每个项中同一变量至多出现一次（多线性）；
- 挑战数组长度必须恰为 `num_vars`，每个元素为 `[0,100]` 的整数；
- 批处理实例数 `1 ≤ 批大小 ≤ 256`（`MAX_BATCH_INSTANCES`），实例 ID 为
  批次内唯一的非空 `str`。

短演示不穷举上限规模；`demo.py` 的演示 2 覆盖 8 变量场景，演示 5 的批处理
段落覆盖 256 实例的批大小边界（约 9 ms）。

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

### 批处理错误与原子性语义

- **实例 ID 独立绑定**：`prove_batch` 产出的每份转录都带 `instance_id`；
  `verify_batch` 要求提交的转录绑定 ID 与实例 ID 完全一致。把 A 的转录
  提交给 B（张冠李戴）、或转录未绑定 ID（如单实例 `prove` 的产物混入批次）
  都抛 `BatchError`；转录 ID 集合与实例 ID 集合多一个/少一个同样抛错；
- **小域直接求和核验**：除单实例的逐轮闭合与最终点值检查外，批处理
  verifier 自己枚举每个实例的布尔立方体，独立核对转录的初始主张。这能抓住
  固定挑战下旧单实例检查的一个已知缺口——作弊者围绕挑战点重选一个和不同、
  但逐轮闭合且 `f(r)=g(r)` 的转录（demo 演示 5 的失败用例 3 与
  `tests/test_batch.py` 给出了手算构造：f=x₀、r₀=5、g₀(t)≡5、主张 10）；
- **任一实例失败则整批失败**，但报告仍逐实例给全：
  `BatchReport.accepted=False` 时可通过 `by_id()` 看到其余实例仍接受；
  结构非法（轮数/次数/域外数等）在对应实例处抛 `BatchError`（信息带实例 ID）；
- **失败原子性**：`prove_batch` 在构造任何转录前先完成全部实例的预算预检与
  主张预检；空批次、ID 重复、假 `claimed_sum`、预算不足都在计算阶段前报错，
  不返回半成品报告，输入对象不被修改，修正后可原样重试；
- 批处理同样区分 **`BudgetExhausted`（资源不足，放宽预算即可重试）与主张为假
  （报告中拒绝）**；`eval_budget` 作用于每个实例的每次布尔立方体枚举。

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
7. **批处理逐实例完整运行单实例协议，不做线性压缩**：每个实例保留自己的轮数
   与次数上界；额外只做两件事——转录与实例 ID 显式绑定（防张冠李戴）、
   verifier 直接枚举布尔立方体独立核对初始主张（小域上代价 ≤256 点/实例，
   且补上了固定挑战下“围绕挑战点重选和”的可靠性缺口）。

## 误差口径

全部计算为**精确整数模 101 算术**，任何路径都不产生或接受浮点数，因此
**不存在浮点误差，也没有容差（tolerance）参数**。所有相等判断均为模 101 下
的精确相等；不会用宽容差掩盖边界错误。

## 已知限制

- 仅支持 p=101、多线性多项式（每变量次数 ≤ 1）、n≤8、项数≤100；
- 批处理最多 256 个实例（接口边界，非密码学参数）；
- prover 时间/空间为 O(批大小 · 2^n · 项数) 量级，不适合大规模电路；
- 挑战固定外部给定，无随机性、无绑定承诺、无零知识性；批处理的实例 ID
  绑定只是显式逐实例对应关系，**不是** Fiat–Shamir 转录绑定，也不做随机
  线性批处理（多实例不压缩成一个和，逐实例完整核验）；
- 不防范恶意 prover 之外的任何威胁模型，不声称行业标准兼容；
- verifier 的直接点值检查与小域直接求和都要求其持有完整原多项式
  （本题“直接算原多项式点值/求和”的契约如此），n≤8 使枚举成本至多 256 点。

## 仓库结构

```
sumcheck/          被测库（field/polynomial/protocol/errors + batch 多实例批处理）
tests/             unittest 测试 + 独立参考实现 tests/reference.py
demo.py            固定输入演示：单实例接受/上限规模/篡改拒绝/结构异常 +
                   批处理多规模接受/256 实例边界/整批失败/跨实例冒用/直接求和抓假
README.md
requirements.txt   仅注释说明标准库；无第三方依赖
```
