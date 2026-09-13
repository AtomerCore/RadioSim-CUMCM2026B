# -*- coding: utf-8 -*-
"""模拟器配置管理。

所有参数默认值与官方"无线电干扰源环境模拟器"完全一致，且均可通过
Web 设置界面修改，持久化到 simulator/data/config.json。
"""
import json
import os
import threading

SIM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SIM_DIR, "data")
LOG_DIR = os.path.join(DATA_DIR, "logs")
CONFIG_FILE = os.path.join(DATA_DIR, "config.json")
ATTEMPTS_FILE = os.path.join(DATA_DIR, "attempts.json")

# 默认配置：数值均来自题目/附件（官方值）
DEFAULTS = {
    # 通用
    "port": 2026,                    # 机器人接口端口（空闲时可修改）
    "display_count": 1000,           # “指令与反馈”显示条数（100..5000）
    # 场地
    "arena_radius": 1800.0,          # 目标区域半径（米）
    # 干扰源案例生成
    "source_count_min": 10,          # 干扰源个数范围
    "source_count_max": 16,
    "recv_radius_min": 1000.0,       # 有效接收半径范围（米）
    "recv_radius_max": 1500.0,
    "channel_min": 1,                # 频道范围
    "channel_max": 20,
    "initial_channel": 1,            # 测向机初始频道
    # 物理与计时（官方默认值）
    "move_speed": 5.0,               # 机器狗移动速度 m/s
    "measure_time": 5.0,             # 检测动作耗时 s
    "switch_time": 1.0,              # 切换频道耗时 s
    "clear_fail_time": 3.0,          # 清除未发现（仅光学定位）耗时 s
    "clear_success_time": 5.0,       # 清除成功（定位+清除）耗时 s
    "near_distance": 5.0,            # 近距离阈值 m（≤ 该值返回 near）
    "clear_radius": 20.0,            # 清除半径 m
    "svd_error_deg": 1.0,            # 示向度误差范围 ±度
    # 测试流程时限
    "countdown_s": 5.0,              # 准备就绪后的倒计时 s
    "window_s": 1500.0,              # 测试窗口 25 分钟 s
    "max_real_s": 1200.0,            # /enter 后程序运行限时 20 分钟 s
    "max_virtual_s": 360000.0,       # 虚拟世界限时 100 小时 s
    "formal_attempts": 3,            # 每个问题正式测试次数
    # 接口限制（附件2）
    "coord_abs_max": 2000000.0,      # 坐标分量绝对值上限
    "body_max_bytes": 65536,         # 请求体最大字节数
    "idempotency_limit": 10000,      # 每局幂等记录上限
    # 自定义案例
    "custom_case_enabled": False,    # 启用后演练/正式均使用自定义干扰源列表
    "custom_sources": [],            # [{channel,x,y,recv_radius,kind,direction_deg}]
    "random_seed": None,             # 指定后案例可复现
    # 本地工具选项（官方无此项）
    "reveal_formal_truth": True,     # 正式测试中是否允许界面“显示真值”调试开关
}

# 数值型参数的合法范围，用于保存时校验
_BOUNDS = {
    "port": (1, 65535),
    "display_count": (100, 5000),
    "arena_radius": (100, 100000),
    "source_count_min": (1, 20),
    "source_count_max": (1, 20),
    "recv_radius_min": (10, 100000),
    "recv_radius_max": (10, 100000),
    "channel_min": (1, 64),
    "channel_max": (1, 64),
    "initial_channel": (1, 64),
    "move_speed": (0.1, 1000),
    "measure_time": (0, 3600),
    "switch_time": (0, 3600),
    "clear_fail_time": (0, 3600),
    "clear_success_time": (0, 3600),
    "near_distance": (0, 10000),
    "clear_radius": (0, 10000),
    "svd_error_deg": (0, 45),
    "countdown_s": (0, 3600),
    "window_s": (10, 86400),
    "max_real_s": (1, 86400),
    "max_virtual_s": (1, 10 ** 7),
    "formal_attempts": (1, 100),
    "idempotency_limit": (10, 1000000),
    "coord_abs_max": (1000, 1e9),
    "body_max_bytes": (1024, 10 ** 7),
}

_INT_FIELDS = {
    "port", "display_count", "source_count_min", "source_count_max",
    "channel_min", "channel_max", "initial_channel", "formal_attempts",
    "idempotency_limit", "body_max_bytes",
}


class Config:
    """线程安全的配置对象。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._data = dict(DEFAULTS)
        self.load()

    # ---------- 持久化 ----------
    def load(self):
        with self._lock:
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                        stored = json.load(f)
                    if isinstance(stored, dict):
                        self._data.update(
                            {k: v for k, v in stored.items() if k in DEFAULTS})
                except (OSError, ValueError):
                    pass
            self._normalize()

    def save(self):
        with self._lock:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, CONFIG_FILE)

    # ---------- 访问 ----------
    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def as_dict(self):
        with self._lock:
            return dict(self._data)

    def snapshot(self):
        """供日志使用的配置快照。"""
        return self.as_dict()

    def update(self, patch):
        """用 patch 更新配置；返回 (ok, error_message)。"""
        with self._lock:
            new = dict(self._data)
            for k, v in patch.items():
                if k not in DEFAULTS:
                    return False, "未知配置项: %s" % k
                if k in ("custom_case_enabled", "reveal_formal_truth"):
                    if not isinstance(v, bool):
                        return False, "%s 必须为布尔值" % k
                    new[k] = v
                elif k == "random_seed":
                    if v is None:
                        new[k] = None
                    elif isinstance(v, bool) or not isinstance(v, (int, str)):
                        return False, "random_seed 须为整数或字符串"
                    else:
                        new[k] = v
                elif k == "custom_sources":
                    ok, err = self._check_custom_sources(v)
                    if not ok:
                        # 禁用自定义案例时允许清空列表
                        enabled = new.get("custom_case_enabled", False)
                        if not (isinstance(v, list) and not v and enabled is False):
                            return False, err
                    new[k] = v
                else:
                    lo, hi = _BOUNDS[k]
                    if k in _INT_FIELDS:
                        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                                or float(v) != int(v):
                            return False, "%s 必须为整数" % k
                        v = int(v)
                        if not (lo <= v <= hi):
                            return False, "%s 须在 %d..%d 之间" % (k, lo, hi)
                    else:
                        if isinstance(v, bool) or not isinstance(v, (int, float)):
                            return False, "%s 必须为数值" % k
                        v = float(v)
                        if not (lo <= v <= hi):
                            return False, "%s 须在 %g..%g 之间" % (k, lo, hi)
                    new[k] = v
            self._data = new
            self._normalize()
            self.save()
            return True, ""

    @staticmethod
    def _check_custom_sources(sources):
        if not isinstance(sources, list) or not sources:
            return False, "自定义干扰源列表不能为空"
        if len(sources) > 20:
            return False, "干扰源数量不能超过 20"
        channels = set()
        for i, s in enumerate(sources):
            if not isinstance(s, dict):
                return False, "第 %d 个干扰源格式错误" % (i + 1)
            try:
                ch = s["channel"]
                x = float(s["x"])
                y = float(s["y"])
                rr = float(s["recv_radius"])
                kind = s["kind"]
                direction = s.get("direction_deg")
            except (KeyError, TypeError, ValueError):
                return False, "第 %d 个干扰源字段缺失或类型错误" % (i + 1)
            if isinstance(ch, bool) or not isinstance(ch, int) or not (1 <= ch <= 20):
                return False, "第 %d 个干扰源频道须为 1..20 整数" % (i + 1)
            if ch in channels:
                return False, "频道 %d 重复" % ch
            channels.add(ch)
            if kind not in ("omni", "directional"):
                return False, "第 %d 个干扰源类型须为 omni 或 directional" % (i + 1)
            if kind == "directional":
                if isinstance(direction, bool) or not isinstance(direction, (int, float)) \
                        or not (0 <= float(direction) < 360):
                    return False, "第 %d 个干扰源定向方向须在 [0,360)" % (i + 1)
            if not (10 <= rr <= 100000):
                return False, "第 %d 个干扰源接收半径超出范围" % (i + 1)
        return True, ""

    def _normalize(self):
        """修正相互依赖的参数，保证内部一致。"""
        d = self._data
        if d["source_count_min"] > d["source_count_max"]:
            d["source_count_min"] = d["source_count_max"]
        if d["recv_radius_min"] > d["recv_radius_max"]:
            d["recv_radius_min"] = d["recv_radius_max"]
        if d["channel_min"] > d["channel_max"]:
            d["channel_min"] = d["channel_max"]
        if d["initial_channel"] < d["channel_min"] or d["initial_channel"] > d["channel_max"]:
            d["initial_channel"] = d["channel_min"]
        # 干扰源数量不能超过频道容量
        capacity = d["channel_max"] - d["channel_min"] + 1
        d["source_count_max"] = min(d["source_count_max"], capacity)
        d["source_count_min"] = min(d["source_count_min"], d["source_count_max"])


def load_attempts():
    """读取正式测试已用次数。"""
    if os.path.exists(ATTEMPTS_FILE):
        try:
            with open(ATTEMPTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {"p3": int(data.get("p3", 0)), "p4": int(data.get("p4", 0))}
        except (OSError, ValueError, TypeError):
            pass
    return {"p3": 0, "p4": 0}


def save_attempts(attempts):
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = ATTEMPTS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(attempts, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ATTEMPTS_FILE)
