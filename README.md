# sumcheck 教学库：F_101 多线性多项式的全量/部分求和证明

这是一个**仅用于教学**的小型 sum-check 协议实现。当前版本在固定提交
`e63e187d351a0f2d72fa491278a165b390d6ac9b` 的全变量求和协议基础上，新增
**指定变量部分求和协议**：调用方把变量显式分成“要求和的变量”和“保留变量”，
并为每个保留变量给出公开求值点。

部分求和的主张是

```text
C = Σ_{b_v ∈ {0,1}, v ∈ sum_variables}
    f(保留变量=公开点, 求和变量=b_v)   (mod 101)
```

协议只按 `sum_variables` 给出的显式变量 ID 顺序逐轮处理这些变量；保留变量
始终固定在公开点，**不会被擅自求和**。空求和集合没有交互轮次，直接退化为
一次公开点求值检查。

> 免责声明：本库**不提供零知识性**，不模拟随机性（挑战非 Fiat–Shamir、非随机
> 抽取），不做多项式承诺，不实现任何完整行业标准协议，**不可用于生产安全场景**。
> 它只实现本仓库契约所定义的有限数学子集。

## 运行环境

- Python **3.14.7**（验收环境；代码只使用标准库语法，3.10+ 通常也可运行）
- **仅标准库**，没有第三方依赖；`requirements.txt` 只有说明性注释
- Linux 原生、完全离线：不需要网络、账号、密钥、数据库、中间件、云 API 或 Docker

### 准备 venv（可选但推荐）

```bash
python3.14 -m venv .venv
. .venv/bin/activate
python --version   # 应显示 Python 3.14.7
```

不需要执行 `pip install`。

## 准确命令

```bash
# 全部测试：保留原全变量协议测试，并新增部分求和独立参考对拍
python -m unittest discover -s tests -v

# 本轮新增演示：部分求和正常接受、空求和、错误分区、篡改公开点、原入口兼容
python demo_iteration.py

# 原始全变量求和演示仍保留
python demo.py
```

`demo_iteration.py` 在普通 Linux 环境实测为毫秒级，远低于 90 秒预算；所有输出
均由现场计算得到，不使用 `sleep` 或预录结果。

## 部分求和快速上手

```python
from sumcheck import MultilinearPoly, Term, prove_partial, verify_partial

# f(x0,x1,x2,x3) = 3 + 2*x0 + x1 + x0*x3
poly = MultilinearPoly(4, [
    Term(3, ()),
    Term(2, (0,)),
    Term(1, (1,)),
    Term(1, (0, 3)),
])

sum_variables = (3, 0)          # 显式顺序：先 X3，再 X0
retained_point = {1: 17, 2: 42} # 保留变量及公开点
challenges = (60, 25)           # 与 sum_variables 一一对应

transcript = prove_partial(poly, sum_variables, retained_point, challenges)
result = verify_partial(
    poly,
    transcript,
    sum_variables=sum_variables,
    retained_point=retained_point,
)
assert result.accepted
```

空求和集合：

```python
point = {0: 5, 1: 6, 2: 7, 3: 8}
transcript = prove_partial(poly, (), point, ())
result = verify_partial(poly, transcript)
assert result.accepted and result.rounds_checked == 0
```

## 公开接口

全部从 `sumcheck` 包导入。

### 数据结构

| 名称 | 说明 |
| --- | --- |
| `P = 101` | 素域模数；域元素一律为区间 `[0, 100]` 内的规范 `int` |
| `Term(coeff, variables)` | 单项式 `coeff * Π X_i`；`variables` 为 0 基变量 ID 可迭代对象，空集合表示常量项 |
| `MultilinearPoly(num_vars, terms)` | 多线性多项式；构造即校验，重复变量集合项被拒绝，不静默合并 |
| `RoundMessage` / `Transcript` / `VerifyResult` | 原全变量求和协议的不可变值对象 |
| `PartialRoundMessage` / `PartialTranscript` / `PartialVerifyResult` | 部分求和协议的不可变值对象 |

### 原全变量入口（保留）

```python
prove(poly, challenges, *, claimed_sum=None, eval_budget=None) -> Transcript
verify(poly, transcript) -> VerifyResult
```

行为与原实现一致：对所有 `num_vars` 个布尔变量求和，按变量 ID 升序逐轮处理。

### 新增部分求和入口

```python
prove_partial(
    poly,
    sum_variables,
    retained_point,
    challenges,
    *,
    claimed_sum=None,
    eval_budget=None,
) -> PartialTranscript

verify_partial(
    poly,
    transcript,
    *,
    sum_variables=None,
    retained_point=None,
) -> PartialVerifyResult
```

参数约定：

- `sum_variables`：要求和的显式变量 ID 有序序列（`tuple`/`list`），例如 `(3, 0)`；
  可以非连续、非升序，但不得重复，也不接受无序集合；
- `retained_point`：`{保留变量 ID: 公开域元素}`；其键必须恰好是
  `sum_variables` 的补集，两个集合互不相交并覆盖全部变量；
- `challenges`：长度等于 `len(sum_variables)`，第 `i` 个挑战对应
  `sum_variables[i]`；
- `claimed_sum`：可选主张值；显式给出时必须等于真实部分求和，否则诚实 prover
  抛 `ProtocolError`；
- `eval_budget`：单轮剩余布尔立方体枚举预算，语义与原协议一致。

`PartialTranscript` 绑定公开语句与证明数据：

- `num_vars`
- `sum_variables`（显式轮次顺序）
- `retained_variables`（按变量 ID 升序规范化记录）
- `retained_point`（与 `retained_variables` 一一对应）
- `initial_claim`、`rounds`、`challenges`、`final_claim`

`verify_partial` 会先校验转录内绑定语句的结构；如果调用方同时传入期望的
`sum_variables` 和 `retained_point`，二者必须与转录完全一致。换一种变量分区、
改变保留变量集合或公开点时，不能复用同一份证明，验证入口会抛出
`ProtocolError` 或在最终直接点值检查拒绝。

## 协议与次数界

对部分求和顺序 `(v_0, …, v_{k-1})`：

1. prover 主张在保留变量公开点下，对这 `k` 个布尔变量求和的值为 `C`；
2. 第 `i` 轮只处理显式变量 `v_i`，发送一元多项式 `g_i(t)`；
3. verifier 检查 `g_i(0) + g_i(1)` 等于上一主张；
4. 新主张为 `g_i(r_i)`；
5. 最后 verifier 把保留变量设为公开点、求和变量设为对应挑战，直接计算
   原多项式点值并与最终主张比较。

每轮次数界从原多线性电路推导：先把保留变量固定到公开点，再固定本轮之前的
求和变量挑战，然后读取当前变量在限制后多项式中的实际次数（0 或 1）。例如
`f=x0*x1`，若保留变量 `x1` 的公开点为 0，则关于 `x0` 的轮多项式次数界为 0。

空求和集合时 `rounds=()`、`challenges=()`，要求 `initial_claim == final_claim`，
并直接检查 `f(公开点)`。

## 输入上限与错误语义

- 变量数 `0 ≤ num_vars ≤ 8`；原全变量 `prove`/`verify` 仍要求至少 1 个变量；
  部分求和允许空求和集合，但变量分区仍必须覆盖全部变量；
- 项数 `≤ 100`；每项中同一变量至多出现一次；
- 挑战和公开点必须是 `[0,100]` 的整数域元素；
- 数值输入拒绝 NaN、Infinity、一切 `float`；整数参数拒绝 `bool`；
- 非法结构（轮数不符、变量 ID 不符、非法次数、域外数、类型错误）抛
  `TranscriptFormatError`；
- 结构合法但主张不闭合或最终点值不符时返回 `accepted=False`，并给出首个失败轮；
- `eval_budget` 耗尽抛 `BudgetExhausted`，表示资源不足，不等同于主张为假；
- `prove` 与 `prove_partial` 都是纯函数，失败时不会返回半成品转录，也不修改输入。

## 测试与独立参考

`tests/reference.py` 是不导入被测核心的独立参考实现。本轮新增测试覆盖：

- 非连续、非升序的显式求和变量顺序；
- 某变量未出现在多项式项中，对应轮次次数为 0；
- 公开点固定后次数界退化；
- 空求和集合的直接求值检查；
- 错误变量分区不能复用同一份证明；
- 篡改公开点后最终直接点值检查拒绝；
- 不完整、重叠或重复的变量分区在 prover 入口拒绝。

原有全变量求和测试全部保留，往返一致测试仅作为补充，期望值来自独立枚举。

## 误差口径

全部计算为精确整数模 101 算术，任何路径都不产生或接受浮点数，因此不存在
浮点误差，也没有容差参数。所有相等判断均为模 101 下的精确相等。

## 已知限制

- 仅支持 p=101、多线性多项式（每变量次数 ≤ 1）、最多 8 个变量、100 个项；
- prover 直接枚举布尔立方体，时间/空间随求和变量数指数增长；
- 挑战固定外部给定，无随机性、无绑定承诺、无零知识性；
- verifier 的最终直接点值检查要求其持有完整原多项式；
- 不声称知识可靠性之外的密码学性质，也不声称行业标准兼容或生产安全。

## 仓库结构

```text
sumcheck/             被测库（field/polynomial/protocol/errors）
tests/                unittest 测试 + 独立参考实现 tests/reference.py
demo.py               原全变量求和演示
demo_iteration.py     本轮部分求和演示
README.md
requirements.txt      仅注释说明标准库；无第三方依赖
```
