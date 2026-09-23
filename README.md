# sum-check 教学库（GF(101) 多线性多项式求和证明）

一个**教学用途**的小型 sum-check 协议库：在素域 GF(p)（固定 p=101）上，
对显式给出的多线性多项式 f(x_1,…,x_n)，由 prover 逐轮提交一元多项式
系数，证明布尔立方体求和主张

```
C = Σ_{b ∈ {0,1}^n} f(b)  (mod 101)
```

verifier 逐轮检查 `g(0)+g(1) == 上一轮主张`，挑战点由**调用方给定的固定
数组**提供（库不生成随机数），最后一轮后直接在挑战点计算原多项式做终核。

> 本库是教学演示，**不提供零知识、不抵抗恶意 prover 之外的任何威胁，
> 不可用于生产或密码学用途**；p=101 只是便于手算的教学规模。

## 环境与依赖

- Python **3.14.7**（标准库；未使用第三方包，理论上任何 Python 3.10+
  都可运行，仓库按 3.14.7 验收）。
- Linux 原生运行，无需网络、账号、密钥、Docker、数据库或任何外部服务。
- 无额外依赖，`requirements.txt` 仅为占位注释。

## 运行命令

在仓库根目录执行：

```bash
# 全部必要测试（发现 tests/ 下的用例）
python -m unittest discover -s tests -v

# 固定输入演示：一个被接受的正常证明 + 一个第 2 轮系数篡改后被实际拒绝的证明
python demo.py
```

演示为真实计算（枚举/逐轮求值），无 `sleep`、无预录结果，实测约 0.02 秒。

## 公开接口

```python
from sumcheck import (
    FIELD_P,                 # 101
    # 数据结构
    Term,                    # Term(coeff, variables)：单项 coeff * ∏ x_j
    MultilinearPolynomial,   # MultilinearPolynomial(terms, num_vars=None)
    Proof,                   # Proof(rounds)：轮系数序列，每轮为系数 tuple
    ProverOutput,            # .claimed_sum, .proof
    RoundRecord,             # 转录中一轮的全部公开数据
    VerificationResult,      # .accepted/.failed_round/.reason_code/.message/
                             # .transcript/.final_value/.claimed_sum
    # 协议
    prove,                   # prove(poly, challenges) -> ProverOutput（诚实 prover）
    verify,                  # verify(poly, claimed_sum, challenges, proof) -> VerificationResult
    run_sumcheck,            # prove 后立即 verify，返回 (ProverOutput, VerificationResult)
    # 域工具
    check_field_int, field_add, field_sub, field_mul,
    # 错误
    SumCheckError, FieldValueError, PolynomialError,
    ChallengeError, ProofFormatError,
)
```

### 数据结构

- `Term(coeff: int, variables: tuple[int, ...])`：多线性单项，`variables`
  为空 tuple 表示常数项；构造时变量下标自动升序排列。
- `MultilinearPolynomial(terms, num_vars=None)`：
  - 不允许重复支撑（同一变量集合出现两次）与零系数项，直接报错，不静默合并；
  - 不允许项内重复变量、负下标、越界下标；
  - `num_vars` 可省略（由最大下标 +1 推断；空项列表必须显式传 `num_vars`）。
  - 方法：`evaluate(point)`、`boolean_cube_sum()`、`degree_in(j)`；
    只读属性 `num_vars`、`terms`。
- `Proof(rounds)`：`rounds` 是长度为 n 的序列，第 j 轮是次数升序的系数
  序列（多线性下长度 1 或 2，长度 1 视为 `[c0, 0]`）。构造只检查外层是否
  为序列；系数类型/值域/次数由 verifier 逐轮裁定（这样篡改的证明得到
  “首个失败轮”，而不是构造期异常）。
- `RoundRecord` 字段：`round`（1 起）、`variable`（0 起）、`coeffs`、
  `expected_claim`、`g_at_zero`、`g_at_one`、`challenge`、`next_claim`。
- `VerificationResult.reason_code`：
  - `'claim'`：该轮 `g(0)+g(1)` 不等于上一主张；
  - `'degree'`：该轮系数超过 2 个（次数 > 1，违反多线性次数上界）；
  - `'field'`：系数是整数但超出 `[0,100]`；
  - `'format'`：轮不是序列、系数不是 int（含 bool/字符串/浮点）、空轮等；
  - `'final'`：轮次全部通过，但终末原多项式点值不等于最后主张；
  - 成功时为 `None`。
  - `failed_round` 为首个失败轮（1 起）；`'final'` 终核失败时为 `None`；
    转录 `transcript` 只含失败轮之前成功完成的轮次。

### 错误语义（API 误用 vs 证明被证伪）

- **API 误用抛异常**（均继承 `SumCheckError`）：
  - `FieldValueError`：域元素位置出现 bool、float（NaN/Infinity 会在消息中
    点名）、或越界整数（负数或 ≥ 101）——库**不做静默取模/纠正**；
  - `PolynomialError`：变量数/项数超限、下标越界、重复项、零系数项、
    非 Term、`num_vars` 不是非 bool 整数等；
  - `ChallengeError`：挑战不是序列或长度与变量数不一致；
  - `ProofFormatError`：`proof` 不是 `Proof`，或轮数与变量数不符
    （无法归属于某一轮的结构性错误）。
  - 所有异常消息带可定位信息（如 `coeff[3]: ...`、`terms[7]: ...`）。
- **证明内容被证伪不抛异常**：返回 `accepted=False` 的
  `VerificationResult`，给出首个失败轮、原因码与说明。
- 本库没有“搜索预算”类算法（无回溯搜索），因此不存在预算耗尽情形；
  协议层面的“无解”即 verifier 的拒绝结果，与输入异常显式分开。

### 输入上限（接口边界）

| 项目 | 限制 |
|---|---|
| 素域 | 固定 p = 101，不可配置 |
| 变量数 n | 0 ≤ n ≤ 8（n=0 为零变量常数多项式，合法） |
| 项数 | 0 ≤ 项数 ≤ 100（空项列表需显式 `num_vars`，表示零多项式） |
| 域元素 | 规范形式 int，0..100；bool/float/越界 int 一律拒绝 |
| 每轮次数 | ≤ 1（系数最多 2 个），超过即判 `'degree'` |
| 挑战 | 调用方提供长度恰为 n 的域元素数组，长度不符抛异常 |

## 设计取舍

- **多线性专用**：多项式对每个变量次数 ≤ 1，因此每轮 g_j 是一次式，
  系数固定为 `[g(0), g(1)-g(0)]`；verifier 把任何高于一次的轮判为非法，
  而不是尝试支持一般电路/一般多项式。
- **prover 用布尔后缀枚举求 g_j(0/1)**：教学库优先透明与可核验，不做
  电路分层、流处理或 witness 优化；n≤8 时每轮至多枚举 128 个后缀点，
  上限规模（8 变量、100 项）prove+verify 实测约 16 ms。
- **挑战由调用方给定**：库不实现 Fiat–Shamir、不读随机源，使转录完全
  确定、可复现、可测试；随机性的责任在调用方。
- **终末直算原多项式**：最后一轮后 verifier 自己在 `(r_1,…,r_n)` 计算
  f（不依赖 prover），这是教科书 sum-check 的标准终核。
- **严格的规范域元素**：越界整数直接报错而非取模——静默归约会掩盖调用方
  把“普通整数计数”误当域元素的错误。
- **无浮点误差口径问题**：全部运算为精确整数模运算，输出要么精确相等、
  要么拒绝；库内没有任何浮点数值结果，因此不存在近似误差/容差，也就
  不可能用宽容差掩盖边界错误。（`float('NaN')` 等仅作为“被拒绝的输入”
  出现在测试里。）
- **布尔立方体枚举参考独立**：`tests/reference.py` 是独立实现的朴素
  字典表示 + 枚举，不导入 `sumcheck` 包；被测核心的期望值（立方和、
  各轮 g(0)/g(1)、终末点值）全部来自它，诚实 prover 的“往返成功”只作
  补充测试。
- **有状态操作**：verifier 不修改任何输入对象；构造校验在对象入库前
  一次性完成，校验失败不会产生半成品对象。

## 已知限制

- p=101 极小，纯教学，**无密码学安全性**；不实现零知识、不隐藏多项式
  结构（转录直接暴露系数），不宣称与任何行业标准协议兼容。
- 仅支持多线性、显式项列表多项式；不支持一般算术电路、一般次数多项式、
  Product-Check / GKR 等扩展。
- prover 是朴素枚举复杂度 O(2^n · 项数)，被 n≤8 的接口边界限制。
- 不实现序列化/持久化、网络传输或并发；`Proof` 等对象就是内存数据类。
- verifier 的检查顺序固定为 结构 → 次数 → 系数类型 → 系数据值域 →
  折叠主张；同一轮同时有多种问题时报告该顺序下的第一个原因码。
- 诚实 prover 按固定挑战数组出证；若挑战本身被敌手选择，协议安全性不
  属于本库保证范围。

## 目录结构

```
sumcheck/__init__.py    # 公开 API
sumcheck/_errors.py     # 异常层次
sumcheck/_field.py      # GF(101) 校验与模运算
sumcheck/_poly.py       # Term / MultilinearPolynomial
sumcheck/_protocol.py   # prove / verify / 转录结构
tests/reference.py      # 独立参考（不导入被测核心）
tests/test_field_poly.py
tests/test_protocol.py
demo.py                 # 固定输入演示（接受 + 篡改拒绝）
```
