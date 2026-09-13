# -*- coding: utf-8 -*-
"""虚拟世界：干扰源案例生成与检测/清除物理判定。"""
import hashlib
import math
import random


class Source:
    """一个干扰源。kind: "omni" 全向 / "directional" 定向。"""

    __slots__ = ("channel", "x", "y", "recv_radius", "kind",
                 "direction_deg", "cleared")

    def __init__(self, channel, x, y, recv_radius, kind, direction_deg=None):
        self.channel = int(channel)
        self.x = float(x)
        self.y = float(y)
        self.recv_radius = float(recv_radius)
        self.kind = kind
        self.direction_deg = None if direction_deg is None else float(direction_deg)
        self.cleared = False

    def to_dict(self):
        return {
            "channel": self.channel,
            "x": self.x,
            "y": self.y,
            "recv_radius": self.recv_radius,
            "kind": self.kind,
            "direction_deg": self.direction_deg,
            "cleared": self.cleared,
        }


# ---------------------------------------------------------------------------
# 案例生成
# ---------------------------------------------------------------------------

def generate_case(problem, cfg, seed=None):
    """按配置随机生成一局案例。problem=3 全向；problem=4 全向+定向混合。

    seed 为 None 时使用配置中的 random_seed；显式传入种子可覆盖配置
    （批量跑测用它逐局指定并记录种子，实现案例复现）。返回 (sources, seed)。
    """
    if seed is None:
        seed = cfg.get("random_seed")
    rng = random.Random(seed)
    n = rng.randint(cfg.get("source_count_min"), cfg.get("source_count_max"))
    ch_lo = cfg.get("channel_min")
    ch_hi = cfg.get("channel_max")
    channels = rng.sample(range(ch_lo, ch_hi + 1), n)

    r_arena = cfg.get("arena_radius")
    rr_lo = cfg.get("recv_radius_min")
    rr_hi = cfg.get("recv_radius_max")

    if problem == 4:
        # 定向干扰源个数至少 1、至多 n-1（保证既有全向又有定向）
        n_dir = rng.randint(1, n - 1)
    else:
        n_dir = 0

    sources = []
    for i, ch in enumerate(channels):
        # 圆内均匀分布
        r = r_arena * math.sqrt(rng.random())
        theta = rng.uniform(0, 2 * math.pi)
        x, y = r * math.cos(theta), r * math.sin(theta)
        rr = rng.uniform(rr_lo, rr_hi)
        if i < n_dir:
            kind = "directional"
            direction = rng.uniform(0, 360)
            sources.append(Source(ch, x, y, rr, kind, direction))
        else:
            sources.append(Source(ch, x, y, rr, "omni", None))
    return sources, seed


def load_custom_case(cfg):
    """从配置加载自定义案例；返回 Source 列表或 None（配置不合法时）。"""
    raw = cfg.get("custom_sources")
    if not cfg.get("custom_case_enabled") or not raw:
        return None
    sources = []
    for s in raw:
        direction = s.get("direction_deg")
        if s["kind"] == "directional":
            direction = float(direction)
        else:
            direction = None
        sources.append(Source(s["channel"], s["x"], s["y"],
                              s["recv_radius"], s["kind"], direction))
    return sources


# ---------------------------------------------------------------------------
# 几何与物理
# ---------------------------------------------------------------------------

def bearing_deg(x1, y1, x2, y2):
    """从 (x1,y1) 指向 (x2,y2) 的方位角：x 轴正向逆时针，[0,360)。"""
    a = math.degrees(math.atan2(y2 - y1, x2 - x1))
    return a % 360.0


def angle_diff_deg(a, b):
    """有向角差 a-b，归一化到 [-180,180]。"""
    return (a - b + 180.0) % 360.0 - 180.0


def in_coverage(source, px, py):
    """检测点是否位于干扰源有效覆盖角度范围内（全向恒 True；定向 ±90° 含边界）。"""
    if source.kind != "directional":
        return True
    b = bearing_deg(source.x, source.y, px, py)
    return abs(angle_diff_deg(b, source.direction_deg)) <= 90.0 + 1e-9


def position_error_deg(px, py, channel, max_err):
    """固定点位示向度误差。

    同一位置+频道重复检测误差不变（题目：同一地点电磁环境固定），
    不同地点误差在 [-max_err, max_err] 内呈统计规律。由坐标哈希确定性生成。
    """
    if max_err <= 0:
        return 0.0
    key = ("%r|%r|%d" % (px, py, channel)).encode("utf-8")
    h = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big")
    return (h / float(1 << 64)) * 2.0 * max_err - max_err


def measure_outcome(source, px, py, cfg):
    """对存活的干扰源判定检测结果。

    返回 ("no_signal"|"near"|"direction", svd_or_None)。
    调用方保证 source 是该频道未清除的干扰源。
    """
    dist = math.hypot(source.x - px, source.y - py)
    if dist > source.recv_radius:
        return "no_signal", None
    if not in_coverage(source, px, py):
        return "no_signal", None
    if dist <= cfg.get("near_distance"):
        return "near", None
    true_bearing = bearing_deg(px, py, source.x, source.y)
    err = position_error_deg(px, py, source.channel, cfg.get("svd_error_deg"))
    raw = (true_bearing + err) % 360.0
    svd = round(raw, 2)
    if svd >= 360.0:  # 例如 359.999 四舍五入到 360.00，归一化到 0
        svd -= 360.0
    return "direction", svd


def clear_outcome(source, px, py, cfg):
    """清除判定：距离 ≤ 清除半径即成功，与定向朝向无关。"""
    dist = math.hypot(source.x - px, source.y - py)
    return dist <= cfg.get("clear_radius")
