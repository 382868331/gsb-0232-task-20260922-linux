"""素域 F_p, p = 101。

域元素在公开数据结构中一律以规范 ``int``（区间 ``[0, p-1]``）表示。
全部运算为精确整数模算术，不存在浮点误差。
"""

P = 101


def check_field_element(value, what: str = "value") -> int:
    """校验单个域元素，失败时抛出带位置信息的异常。

    - 拒绝 ``bool``（``True``/``False`` 虽为 ``int`` 子类但语义不是域元素）；
    - 拒绝 ``float``（含 NaN/Infinity 及一切非整数浮点，避免隐式取整）；
    - 拒绝区间外整数。

    类型问题抛 :class:`TypeError`，区间问题抛 :class:`ValueError`。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        if isinstance(value, float):
            if value != value:
                raise ValueError(f"{what}: 拒绝 NaN（有限域只接受整数域元素）")
            if value in (float("inf"), float("-inf")):
                raise ValueError(f"{what}: 拒绝 Infinity（有限域只接受整数域元素）")
        raise TypeError(
            f"{what}: 需要 F_{P} 中的整数域元素（不可为 bool），实际为 {type(value).__name__}"
        )
    if not 0 <= value < P:
        raise ValueError(f"{what}: 域元素 {value} 越界，必须落在 [0, {P - 1}]")
    return value
