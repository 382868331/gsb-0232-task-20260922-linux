"""sum-check 库的异常层次。

所有异常都继承 :class:`SumCheckError`，调用方既可以分别捕获具体语义，
也可以统一捕获库内一切输入/协议错误。
"""


class SumCheckError(Exception):
    """本库所有错误的基类。"""


class FieldValueError(SumCheckError, ValueError):
    """数值不满足域元素契约：非整数、bool、float（含 NaN/Infinity）或越界。"""


class PolynomialError(SumCheckError, ValueError):
    """多项式结构非法：变量数越界、项数越界、下标越界、重复项等。"""


class ChallengeError(SumCheckError, ValueError):
    """挑战数组非法：长度与变量数不一致（元素本身越界报 FieldValueError）。"""


class ProofFormatError(SumCheckError, ValueError):
    """证明结构非法：轮数与变量数不符等无法归属于某一轮的格式错误。"""
