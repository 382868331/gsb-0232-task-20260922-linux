"""GF(101) 素域工具。

契约：
- 域元素一律用规范形式的 Python ``int`` 表示，取值 0..p-1；越界整数
  （负数或 >= p）直接报 :class:`FieldValueError`，不做静默取模纠正；
- 拒绝 ``bool``（``True``/``False`` 不被当作 1/0，静默接受会让调用方
  难以定位把布尔值误传为域元素的错误）；
- 拒绝 ``float``（包括 NaN、+/-Infinity），有限域没有浮点这一数据类型。
"""

import math

from ._errors import FieldValueError

FIELD_P = 101
"""本库固定使用的素数（教学规模，非密码学安全参数）。"""


def check_field_int(value, *, what: str = "value", index: int | None = None) -> int:
    """校验单个域元素输入，返回其规范形式 (0..p-1)。

    ``what`` 描述值的角色，``index`` 非 None 时给出可定位的下标，
    错误消息形如 ``"coeff[3]: expected ..., got bool True"``。
    """
    label = what if index is None else f"{what}[{index}]"
    # bool 是 int 的子类，必须先排除：整数参数明确拒绝 bool。
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float):
            # NaN/Infinity 与普通 float 一样拒绝，但消息里点名，便于定位。
            if math.isnan(value):
                raise FieldValueError(f"{label}: NaN is not a field element of GF({FIELD_P})")
            if math.isinf(value):
                raise FieldValueError(f"{label}: Infinity is not a field element of GF({FIELD_P})")
        raise FieldValueError(
            f"{label}: expected int in GF({FIELD_P}), got {type(value).__name__} {value!r}"
        )
    if not 0 <= value < FIELD_P:
        raise FieldValueError(
            f"{label}: {value} is outside GF({FIELD_P}) canonical range [0, {FIELD_P - 1}]"
        )
    return value


def field_add(a: int, b: int) -> int:
    return (a + b) % FIELD_P


def field_sub(a: int, b: int) -> int:
    return (a - b) % FIELD_P


def field_mul(a: int, b: int) -> int:
    return (a * b) % FIELD_P
