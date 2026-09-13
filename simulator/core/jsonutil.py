# -*- coding: utf-8 -*-
"""JSON 序列化工具：控制数字的输出格式（去尾零、固定位数）。"""
import json


class Num:
    """裸 JSON 数字占位（由 dumps 渲染为不带引号的数字字面量）。"""
    __slots__ = ("s",)

    def __init__(self, s):
        self.s = str(s)

    def __repr__(self):
        return "Num(%s)" % self.s


def fmt_fixed(x, nd):
    """格式化为最多 nd 位小数并去除末尾无意义的零：105.0->105, 123.40->123.4。"""
    s = ("%." + str(nd) + "f") % float(x)
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("", "-", "-0"):
        s = "0"
    return Num(s)


def fmt_us(us):
    """虚拟时钟（内部微秒）转秒的输出格式：最多 6 位小数。"""
    return fmt_fixed(us / 1000000.0, 6)


def fmt_num(x):
    """普通配置数值的输出格式：整数不带小数点。"""
    f = float(x)
    if f == int(f) and abs(f) < 1e15:
        return Num(str(int(f)))
    return Num(repr(f))


def dumps(obj):
    """json.dumps，但 Num 实例输出为裸数字。"""
    def default(o):
        if isinstance(o, Num):
            return "@@NUM" + o.s + "@@"
        raise TypeError(repr(o))

    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"),
                   allow_nan=False, default=default)
    return s.replace('"@@NUM', "").replace('@@"', "")
