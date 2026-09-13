# -*- coding: utf-8 -*-
"""测试会话：状态机、虚拟时钟、现实计时、事件与日志。

状态流转：preparing -> countdown(5s) -> window(接口开放，等待 /enter)
         -> entered(程序计时开始) -> ended
"""
import json
import math
import os
import random
import threading
import time

from . import world
from .config import LOG_DIR
from .jsonutil import Num, fmt_us, fmt_fixed, fmt_num, dumps

PHASE_PREPARING = "preparing"
PHASE_COUNTDOWN = "countdown"
PHASE_WINDOW = "window"
PHASE_ENTERED = "entered"
PHASE_ENDED = "ended"

# 结束原因
END_USER_EXIT = "user_exit"
END_USER_ABORT = "user_abort"
END_WINDOW_TIMEOUT = "window_timeout"
END_PROGRAM_TIMEOUT = "program_timeout"
END_VIRTUAL_TIMEOUT = "virtual_timeout"

END_TEXT = {
    END_USER_EXIT: "机器狗主动退出（user_exit）",
    END_USER_ABORT: "手工中止测试",
    END_WINDOW_TIMEOUT: "25 分钟测试窗口超时",
    END_PROGRAM_TIMEOUT: "程序运行时间超时（/enter 后 20 分钟）",
    END_VIRTUAL_TIMEOUT: "虚拟世界时间超时（100 小时）",
}

_CASE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def now_ms():
    return int(time.time() * 1000)


def gen_case_code(problem, mode):
    tag = "P%d%s" % (problem, "F" if mode == "formal" else "U")
    rnd = "".join(random.choice(_CASE_ALPHABET) for _ in range(6))
    return "%s-%s" % (tag, rnd)


class ActionRecord:
    """幂等记录：同一 request_id + 同一内容 -> 重放首次完整响应。"""
    __slots__ = ("path", "canon", "status", "body")

    def __init__(self, path, canon, status, body):
        self.path = path
        self.canon = canon
        self.status = status
        self.body = body


class TestSession:
    """一局测试会话。所有状态变更均持锁进行。"""

    def __init__(self, app, problem, mode, cfg, sources, case_code):
        self.app = app
        self.problem = problem
        self.mode = mode  # "practice" | "formal"
        self.case_code = case_code
        # 参数快照：本局内配置修改不影响进行中的规则
        self.p = cfg.snapshot()
        self.sources = sources
        self.cfg = cfg

        self.mu = threading.RLock()
        self.phase = PHASE_PREPARING
        self.created_ms = now_ms()
        # 数据准备瞬时完成，直接进入倒计时
        self.phase = PHASE_COUNTDOWN
        self.countdown_end_ms = self.created_ms + int(self.p["countdown_s"] * 1000)
        self.window_end_ms = self.countdown_end_ms + int(self.p["window_s"] * 1000)

        self.enter_ms = None
        self.deadline_ms = None          # 现实截止（窗口与程序限时较早者）
        self.deadline_binding = None     # "program" | "window"
        self.virtual_us = 0              # 虚拟时钟（微秒）
        self.robot_x = 0.0
        self.robot_y = 0.0
        self.heading_deg = None
        self.current_channel = int(self.p["initial_channel"])
        self.entered = False
        self.end_reason = None
        self.ended_ms = None

        # 统计
        self.stat_measures = 0
        self.stat_clear_ok = 0
        self.stat_clear_fail = 0
        self.total_move_m = 0.0

        # 幂等与并发
        self.idem = {}
        self._in_flight = set()
        self._in_flight_mu = threading.Lock()

        # 事件流（UI 指令与反馈 + 可视化回放 + 日志）
        self.events = []
        self._seq = 0

        self._stop = threading.Event()
        self.monitor = threading.Thread(target=self._monitor_loop,
                                        name="session-monitor", daemon=True)
        self.monitor.start()

    # ------------------------------------------------------------------
    # 事件
    # ------------------------------------------------------------------
    def _event(self, kind, **kw):
        with self.mu:
            self._seq += 1
            ev = {"seq": self._seq, "t_ms": now_ms(), "vt_s": self.virtual_us / 1e6}
            ev.update(kw)
            ev["kind"] = kind
            self.events.append(ev)
            return ev

    # ------------------------------------------------------------------
    # 监控线程：阶段推进与超时结束
    # ------------------------------------------------------------------
    def _monitor_loop(self):
        while not self._stop.is_set():
            with self.mu:
                phase = self.phase
                t = now_ms()
                act = None
                if phase == PHASE_COUNTDOWN and t >= self.countdown_end_ms:
                    self.phase = PHASE_WINDOW
                    self._event("session", phase="window_open",
                                text="机器狗接口开放，测试窗口开始")
                elif phase == PHASE_WINDOW and t >= self.window_end_ms:
                    act = END_WINDOW_TIMEOUT
                elif phase == PHASE_ENTERED and t >= self.deadline_ms:
                    act = (END_PROGRAM_TIMEOUT
                           if self.deadline_binding == "program"
                           else END_WINDOW_TIMEOUT)
                elif self.virtual_us > int(self.p["max_virtual_s"] * 1e6):
                    act = END_VIRTUAL_TIMEOUT
            if act:
                self.end(act)
                return
            self._stop.wait(0.05)

    def stop_monitor(self):
        self._stop.set()

    # ------------------------------------------------------------------
    # 接口开放判定（api 层调用：未开放则直接断开连接）
    # ------------------------------------------------------------------
    def interface_open(self, t_ms=None):
        with self.mu:
            if self.phase not in (PHASE_WINDOW, PHASE_ENTERED):
                return False
            t = now_ms() if t_ms is None else t_ms
            limit = self.deadline_ms if self.phase == PHASE_ENTERED else self.window_end_ms
            return t < limit

    # ------------------------------------------------------------------
    # 并发保护（不同动作不得并发）
    # ------------------------------------------------------------------
    def in_flight_begin(self, request_id):
        """返回 True 表示可继续；False 表示存在其他并发动作（应答 409）。"""
        with self._in_flight_mu:
            others = [r for r in self._in_flight if r != request_id]
            if others:
                return False
            self._in_flight.add(request_id)
            return True

    def in_flight_end(self, request_id):
        with self._in_flight_mu:
            self._in_flight.discard(request_id)

    # ------------------------------------------------------------------
    # 幂等
    # ------------------------------------------------------------------
    def idem_size(self):
        with self.mu:
            return len(self.idem)

    def idem_lookup(self, request_id, canon):
        """返回 (replay_record, conflict)：二者至多一个非空。"""
        with self.mu:
            rec = self.idem.get(request_id)
            if rec is None:
                return None, False
            if rec.canon == canon:
                return rec, False
            return None, True

    def idem_record(self, request_id, path, canon, status, body):
        with self.mu:
            self.idem[request_id] = ActionRecord(path, canon, status, body)

    def note_http_error(self, path, status, error, request_id=None):
        """记录一次 HTTP 层错误（400/404/405/413/415/409/429），仅用于界面显示。"""
        self._event("reject", path=path, http_status=status,
                    result="http_%d" % status, detail=error,
                    request_id=request_id)

    # ------------------------------------------------------------------
    # 动作执行（api 层完成校验后调用；返回 (status, body_dict, event_kw)）
    # ------------------------------------------------------------------
    def execute(self, path, fields, t_ms):
        """执行一条已通过校验的指令。fields: dict(request_id, position?, channel?)。"""
        with self.mu:
            if path == "/enter":
                return self._do_enter(fields, t_ms)
            if path == "/measure":
                return self._do_measure(fields, t_ms)
            if path == "/clear":
                return self._do_clear(fields, t_ms)
            if path == "/exit":
                return self._do_exit(fields, t_ms)
            raise RuntimeError("unknown path " + path)

    @staticmethod
    def _ok(body):
        return 200, body, None

    def _vt_num(self):
        return fmt_us(self.virtual_us)

    def _live_source(self, channel):
        for s in self.sources:
            if s.channel == channel and not s.cleared:
                return s
        return None

    def _move_cost(self, x, y):
        dist = math.hypot(x - self.robot_x, y - self.robot_y)
        move_s = dist / float(self.p["move_speed"])
        return dist, move_s

    def _advance(self, x, y, dist):
        if dist > 1e-9:
            self.heading_deg = world.bearing_deg(self.robot_x, self.robot_y, x, y)
        self.robot_x, self.robot_y = x, y
        self.total_move_m += dist

    def _check_virtual_over(self):
        if self.virtual_us > int(self.p["max_virtual_s"] * 1e6):
            # 该动作已完整执行（截止前到达），随后结束测试
            threading.Thread(target=self._delayed_virtual_end,
                             daemon=True).start()

    def _delayed_virtual_end(self):
        # 让响应先发出再结束（动作允许完成）
        time.sleep(0.05)
        self.end(END_VIRTUAL_TIMEOUT)

    def _do_enter(self, fields, t_ms):
        if self.entered:
            return self._reject_state("already_entered", "/enter", fields)
        self.entered = True
        self.enter_ms = t_ms
        prog_end = t_ms + int(self.p["max_real_s"] * 1000)
        if prog_end <= self.window_end_ms:
            self.deadline_ms = prog_end
            self.deadline_binding = "program"
        else:
            self.deadline_ms = self.window_end_ms
            self.deadline_binding = "window"
        remaining = max(0, int((self.deadline_ms - t_ms) // 1000))
        body = {
            "accepted": True,
            "real_timestamp_ms": t_ms,
            "virtual_time_s": self._vt_num(),
            "max_virtual_duration_s": fmt_num(self.p["max_virtual_s"]),
            "max_real_duration_s": fmt_num(self.p["max_real_s"]),
            "remaining_real_duration_s": int(remaining),
        }
        self._event("enter", path="/enter", request_id=fields["request_id"],
                    robot=[self.robot_x, self.robot_y], result="accepted",
                    remaining_real_s=int(remaining))
        return self._ok(body)

    def _do_measure(self, fields, t_ms):
        if not self.entered:
            return self._reject_state("not_entered", "/measure", fields)
        x, y = fields["position"]
        ch = fields["channel"]
        dist, move_s = self._move_cost(x, y)
        switch_s = float(self.p["switch_time"]) if ch != self.current_channel else 0.0
        act_s = float(self.p["measure_time"])
        cost_us = int(round((move_s + switch_s + act_s) * 1e6))

        src = self._live_source(ch)
        if src is None:
            result, svd = "no_signal", None
        else:
            result, svd = world.measure_outcome(src, x, y, self.p)

        self.virtual_us += cost_us
        self._advance(x, y, dist)
        self.current_channel = ch
        self.stat_measures += 1

        body = {
            "accepted": True,
            "real_timestamp_ms": t_ms,
            "virtual_time_s": self._vt_num(),
            "measure_result": result,
        }
        if result == "direction":
            body["svd_deg"] = fmt_fixed(svd, 2)

        self._event("measure", path="/measure", request_id=fields["request_id"],
                    pos=[x, y], channel=ch, move_m=round(dist, 3),
                    move_s=round(move_s, 6), switch_s=switch_s, act_s=act_s,
                    result=result, svd=svd, robot=[self.robot_x, self.robot_y],
                    heading=self.heading_deg)
        self._check_virtual_over()
        return self._ok(body)

    def _do_clear(self, fields, t_ms):
        if not self.entered:
            return self._reject_state("not_entered", "/clear", fields)
        x, y = fields["position"]
        ch = fields["channel"]
        dist, move_s = self._move_cost(x, y)

        src = self._live_source(ch)
        if src is not None and world.clear_outcome(src, x, y, self.p):
            src.cleared = True
            result = "success"
            act_s = float(self.p["clear_success_time"])
            self.stat_clear_ok += 1
        else:
            result = "no_target_in_range"
            act_s = float(self.p["clear_fail_time"])
            self.stat_clear_fail += 1

        cost_us = int(round((move_s + act_s) * 1e6))
        self.virtual_us += cost_us
        self._advance(x, y, dist)

        body = {
            "accepted": True,
            "real_timestamp_ms": t_ms,
            "virtual_time_s": self._vt_num(),
            "clear_result": result,
        }
        self._event("clear", path="/clear", request_id=fields["request_id"],
                    pos=[x, y], channel=ch, move_m=round(dist, 3),
                    move_s=round(move_s, 6), act_s=act_s, result=result,
                    cleared=result == "success",
                    robot=[self.robot_x, self.robot_y], heading=self.heading_deg)
        self._check_virtual_over()
        return self._ok(body)

    def _do_exit(self, fields, t_ms):
        if not self.entered:
            return self._reject_state("not_entered", "/exit", fields)
        body = {
            "accepted": True,
            "real_timestamp_ms": t_ms,
            "virtual_time_s": self._vt_num(),
            "exit_reason": END_USER_EXIT,
        }
        self._event("exit", path="/exit", request_id=fields["request_id"],
                    result="user_exit", robot=[self.robot_x, self.robot_y])
        # 先记事件、再结束（结束会写日志）
        self._end_locked(END_USER_EXIT)
        return self._ok(body)

    def _reject_state(self, reason, path=None, fields=None):
        """业务状态拒绝（HTTP 200 + accepted=false，仅 3 个公共字段）。"""
        if path is not None:
            self._event("reject", path=path,
                        request_id=(fields or {}).get("request_id"),
                        http_status=200, result="rejected", detail=reason)
        body = {
            "accepted": False,
            "real_timestamp_ms": now_ms(),
            "virtual_time_s": Num("0"),
        }
        return 200, body, reason

    def note_state_reject(self, path, fields, reason):
        """记录通过结构校验但被业务拒绝的请求（未知字段/标识不匹配等）。"""
        pos = fields.get("position")
        self._event("reject", path=path,
                    request_id=fields.get("request_id"),
                    http_status=200, result="rejected", detail=reason,
                    pos=(list(pos) if pos else None),
                    channel=fields.get("channel"))

    # ------------------------------------------------------------------
    # 结束与日志
    # ------------------------------------------------------------------
    def end(self, reason):
        with self.mu:
            if self.phase == PHASE_ENDED:
                return
            self._end_locked(reason)

    def _end_locked(self, reason):
        self.phase = PHASE_ENDED
        self.end_reason = reason
        self.ended_ms = now_ms()
        summary = self._build_summary()
        self._event("session", phase="ended", text=END_TEXT.get(reason, reason),
                    reason=reason, summary=summary)
        self._write_log(summary)
        self._stop.set()
        try:
            self.app.on_session_ended(self, summary)
        except Exception:
            pass

    def cleared_count(self):
        return sum(1 for s in self.sources if s.cleared)

    def _build_summary(self):
        total = len(self.sources)
        omni = sum(1 for s in self.sources if s.kind == "omni")
        dirn = total - omni
        cleared = self.cleared_count()
        vt = self.virtual_us / 1e6
        run_s = None
        if self.enter_ms is not None:
            run_s = (self.ended_ms - self.enter_ms) / 1000.0
        return {
            "reason": self.end_reason,
            "cleared": cleared,
            "total": total,
            "omni_total": omni,
            "directional_total": dirn,
            "cleared_channels": [s.channel for s in self.sources if s.cleared],
            "measures": self.stat_measures,
            "clear_success": self.stat_clear_ok,
            "clear_fail": self.stat_clear_fail,
            "virtual_total_s": vt,
            "avg_clear_s": (vt / cleared) if cleared else None,
            "program_run_s": run_s,
            "total_move_m": round(self.total_move_m, 1),
        }

    def _write_log(self, summary):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            log = {
                "case_code": self.case_code,
                "problem": self.problem,
                "mode": self.mode,
                "created_ms": self.created_ms,
                "ended_ms": self.ended_ms,
                "params": self.p,
                "case": [s.to_dict() for s in self.sources],
                "summary": summary,
                "events": self.events,
            }
            path = os.path.join(LOG_DIR, "%s.json" % self.case_code)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(log, f, ensure_ascii=False)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 状态快照（管理接口用）
    # ------------------------------------------------------------------
    def snapshot(self, reveal):
        with self.mu:
            t = now_ms()
            snap = {
                "active": self.phase != PHASE_ENDED,
                "problem": self.problem,
                "mode": self.mode,
                "case_code": self.case_code,
                "phase": self.phase,
                "countdown_remaining_s": max(
                    0.0, (self.countdown_end_ms - t) / 1000.0),
                "window_remaining_s": max(
                    0.0, (self.window_end_ms - t) / 1000.0),
                "program_remaining_s": (
                    max(0.0, (self.deadline_ms - t) / 1000.0)
                    if self.entered else None),
                "interface_ready": self.phase in (PHASE_WINDOW, PHASE_ENTERED),
                "virtual_time_s": self.virtual_us / 1e6,
                "robot": {"x": self.robot_x, "y": self.robot_y,
                          "channel": self.current_channel,
                          "heading": self.heading_deg},
                "stats": {
                    "measures": self.stat_measures,
                    "clear_success": self.stat_clear_ok,
                    "clear_fail": self.stat_clear_fail,
                    "cleared": self.cleared_count(),
                },
                "end_reason": self.end_reason,
                # 可视化所需参数
                "arena_radius": float(self.p["arena_radius"]),
                "svd_error_deg": float(self.p["svd_error_deg"]),
                "clear_radius": float(self.p["clear_radius"]),
            }
            # 真值显示规则：演练测试可见；正式测试仅在允许调试显示时可见
            show = (self.mode == "practice") or (
                reveal and self.cfg.get("reveal_formal_truth"))
            snap["truth_visible"] = show
            if show:
                snap["sources"] = [s.to_dict() for s in self.sources]
            if self.phase == PHASE_ENDED:
                summary = dict(self._last_summary() or {})
                if not show:
                    # 正式测试不显示真值字段
                    for k in ("total", "omni_total", "directional_total",
                              "cleared_channels"):
                        summary.pop(k, None)
                snap["summary"] = summary
            return snap

    def summary(self):
        """本局统计摘要（结束后才有；供批量跑测等外部读取）。"""
        return self._last_summary()

    def _last_summary(self):
        for ev in reversed(self.events):
            if ev.get("kind") == "session" and "summary" in ev:
                return ev["summary"]
        return None

    def events_after(self, seq):
        with self.mu:
            if seq < 0:
                n = int(self.cfg.get("display_count"))
                return list(self.events[-n:]), self._seq
            return [e for e in self.events if e["seq"] > seq], self._seq
