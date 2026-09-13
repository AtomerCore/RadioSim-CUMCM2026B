# -*- coding: utf-8 -*-
"""批量跑测：自动连续开局、驱动机器人程序、汇总统计。

一次批量由若干"分组"组成，每个分组指定问题（3/4）、模式（演练/正式）、
局数与机器人程序命令。批量线程逐局执行：

1. 通过 SimulatorApp.start_test(internal=True) 开局（指定种子可复现案例，
   批量忽略设置页的 random_seed，逐局记录实际使用的种子）；
2. 倒计时结束、接口开放后按 shell 方式启动机器人程序，标准输出/错误
   重定向到 data/batches/robot_logs/<案例编码>.log；
3. 等待本局结束（每局超时则中止并回收机器人进程），记录该局 summary；
4. 全部结束后计算统计（总体 + 分组），保存批次报告与索引，
   报告可从 Web 界面查看并导出 JSON / CSV。

机器人程序通过环境变量获知接入信息：
SIMULATOR_PORT / SIMULATOR_BASE_URL / SIMULATOR_CASE_CODE /
SIMULATOR_PROBLEM / SIMULATOR_MODE / SIMULATOR_SEED
"""
import csv
import io
import json
import math
import os
import random
import subprocess
import threading
import time

from .config import DATA_DIR
from .session import now_ms

BATCH_DIR = os.path.join(DATA_DIR, "batches")
ROBOT_LOG_DIR = os.path.join(BATCH_DIR, "robot_logs")
BATCH_INDEX_FILE = os.path.join(BATCH_DIR, "index.json")

MAX_GROUPS = 10
MAX_RUNS_PER_GROUP = 200
MAX_SEEDS = 1000

_ID_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_batch_id():
    t = time.strftime("%Y%m%d-%H%M%S")
    rnd = "".join(random.choice(_ID_ALPHABET) for _ in range(3))
    return "B-%s-%s" % (t, rnd)


def describe_groups(groups):
    return " + ".join(
        "%s(%s%s×%d)" % (g.get("name") or "",
                         "P" + str(g.get("problem")),
                         "正式" if g.get("mode") == "formal" else "演练",
                         g.get("count", 0))
        for g in groups)


# ---------------------------------------------------------------------------
# 配置校验
# ---------------------------------------------------------------------------

def _as_int(v):
    """把 3 / 3.0 转成 int；非法返回 None。"""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if float(v) != int(v):
        return None
    return int(v)


def validate_batch_config(payload, cfg):
    """校验 /api/batch_start 请求体。返回 (config, None) 或 (None, 错误信息)。"""
    if not isinstance(payload, dict):
        return None, "请求体须为 JSON 对象"

    groups_raw = payload.get("groups")
    if not isinstance(groups_raw, list) or not groups_raw:
        return None, "groups 不能为空"
    if len(groups_raw) > MAX_GROUPS:
        return None, "分组数量最多 %d 个" % MAX_GROUPS

    groups = []
    total = 0
    for i, g in enumerate(groups_raw):
        if not isinstance(g, dict):
            return None, "第 %d 个分组格式错误" % (i + 1)
        problem = _as_int(g.get("problem"))
        if problem not in (3, 4):
            return None, "第 %d 个分组 problem 须为 3 或 4" % (i + 1)
        mode = g.get("mode")
        if mode not in ("practice", "formal"):
            return None, "第 %d 个分组 mode 须为 practice 或 formal" % (i + 1)
        count = _as_int(g.get("count", 1))
        if count is None or not (1 <= count <= MAX_RUNS_PER_GROUP):
            return None, ("第 %d 个分组次数须为 1..%d 的整数"
                          % (i + 1, MAX_RUNS_PER_GROUP))
        name = g.get("name")
        if not isinstance(name, str) or not name.strip():
            name = "P%d%s" % (problem, "F" if mode == "formal" else "U")
        name = name.strip()[:40]
        cmd = g.get("robot_command")
        if not isinstance(cmd, str) or not cmd.strip():
            return None, "第 %d 个分组缺少机器人程序命令" % (i + 1)
        if len(cmd) > 1000:
            return None, "第 %d 个分组机器人命令过长" % (i + 1)
        cwd = g.get("cwd")
        if cwd in ("", None):
            cwd = None
        else:
            if not isinstance(cwd, str) or len(cwd) > 500 or not os.path.isdir(cwd):
                return None, "第 %d 个分组工作目录不存在：%s" % (i + 1, cwd)
        total += count
        groups.append({"name": name, "problem": problem, "mode": mode,
                       "count": count, "robot_command": cmd.strip(),
                       "cwd": cwd})

    seeds_raw = payload.get("seeds")
    if seeds_raw in (None, ""):
        seeds_raw = []
    if not isinstance(seeds_raw, list):
        return None, "seeds 须为数组"
    if len(seeds_raw) > MAX_SEEDS:
        return None, "种子数量最多 %d 个" % MAX_SEEDS
    seeds = []
    for s in seeds_raw:
        if isinstance(s, bool):
            return None, "种子须为整数或字符串"
        iv = _as_int(s) if isinstance(s, (int, float)) else None
        if iv is not None:
            seeds.append(iv)
        elif isinstance(s, str) and s.strip():
            seeds.append(s.strip()[:64])
        else:
            return None, "种子须为整数或非空字符串"
    if seeds and total % len(seeds) != 0 and len(seeds) < total:
        pass  # 种子少于局数时循环使用，不视为错误

    auto_timeout = float(cfg.get("countdown_s")) + float(cfg.get("window_s")) + 60.0
    timeout = payload.get("per_run_timeout_s")
    if timeout in (None, ""):
        timeout = auto_timeout
    else:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
                or not (10 <= float(timeout) <= 86400):
            return None, "每局超时须为 10..86400 秒"
        timeout = float(timeout)

    delay = payload.get("inter_run_delay_s", 2)
    if delay in (None, ""):
        delay = 2.0
    else:
        if isinstance(delay, bool) or not isinstance(delay, (int, float)) \
                or not (0 <= float(delay) <= 600):
            return None, "局间间隔须为 0..600 秒"
        delay = float(delay)

    on_err = payload.get("continue_on_error", True)
    if not isinstance(on_err, bool):
        return None, "continue_on_error 须为布尔值"

    return {
        "groups": groups,
        "seeds": seeds,
        "per_run_timeout_s": timeout,
        "inter_run_delay_s": delay,
        "continue_on_error": on_err,
        "total_runs": total,
    }, None


# ---------------------------------------------------------------------------
# 统计聚合
# ---------------------------------------------------------------------------

# (summary 字段, 界面名称, 单位)
METRIC_DEFS = (
    ("cleared", "已清除数量", "个"),
    ("cleared_ratio", "清除比例", "%"),
    ("measures", "检测次数", "次"),
    ("clear_success", "清除成功", "次"),
    ("clear_fail", "清除失败", "次"),
    ("virtual_total_s", "虚拟时间", "s"),
    ("avg_clear_s", "平均定位清除耗时", "s/个"),
    ("program_run_s", "程序运行时间", "s"),
    ("total_move_m", "移动距离", "m"),
)


def _series(values):
    """均值/标准差/最小/中位/最大（样本标准差；n<2 时为 0）。"""
    out = {"n": len(values)}
    if not values:
        return out
    s = sorted(values)
    n = len(values)
    mean = sum(values) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    median = s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2.0
    out.update({"mean": round(mean, 4), "std": round(std, 4),
                "min": round(s[0], 4), "median": round(median, 4),
                "max": round(s[-1], 4)})
    return out


def _metric_values(runs, key):
    vals = []
    for r in runs:
        s = r.get("summary")
        if not s:
            continue
        if key == "cleared_ratio":
            total = s.get("total") or 0
            if total > 0:
                vals.append(100.0 * (s.get("cleared") or 0) / total)
        elif s.get(key) is not None:
            vals.append(float(s[key]))
    return vals


def _aggregate(runs, wall_s=None):
    ok_runs = [r for r in runs if r.get("summary")]
    reasons = {}
    for r in ok_runs:
        reason = r["summary"].get("reason") or "unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
    cleared_total = sum(r["summary"].get("cleared") or 0 for r in ok_runs)
    sources_total = sum(r["summary"].get("total") or 0 for r in ok_runs)
    full = sum(1 for r in ok_runs
               if (r["summary"].get("total") or 0) > 0
               and r["summary"].get("cleared") == r["summary"].get("total"))
    zero = sum(1 for r in ok_runs if not r["summary"].get("cleared"))
    metrics = {}
    for key, label, unit in METRIC_DEFS:
        metrics[key] = dict(label=label, unit=unit, **_series(_metric_values(runs, key)))
    return {
        "runs_total": len(runs),
        "runs_ok": len(ok_runs),
        "runs_error": len(runs) - len(ok_runs),
        "wall_total_s": wall_s,
        "end_reasons": reasons,
        "cleared_total": cleared_total,
        "sources_total": sources_total,
        "clear_rate_overall": (round(100.0 * cleared_total / sources_total, 2)
                               if sources_total else None),
        "full_clear_runs": full,
        "full_clear_rate": (round(100.0 * full / len(ok_runs), 2) if ok_runs else None),
        "zero_clear_runs": zero,
        "timeout_runs": sum(1 for r in runs if r.get("timeout")),
        "metrics": metrics,
    }


def compute_stats(runs, config, wall_s):
    """总体统计 + 分组统计。runs 为批量运行记录列表。"""
    stats = _aggregate(runs, wall_s)
    groups_out = []
    for gi, g in enumerate(config["groups"]):
        sub = _aggregate([r for r in runs if r.get("group_index") == gi])
        sub["group"] = {"name": g["name"], "problem": g["problem"],
                        "mode": g["mode"], "count": g["count"]}
        groups_out.append(sub)
    stats["groups"] = groups_out
    return stats


# ---------------------------------------------------------------------------
# 报告与索引
# ---------------------------------------------------------------------------

def load_index():
    try:
        with open(BATCH_INDEX_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return sorted(data, key=lambda x: x.get("created_ms", 0), reverse=True)
    except (OSError, ValueError):
        pass
    return []


def save_index(index):
    try:
        os.makedirs(BATCH_DIR, exist_ok=True)
        tmp = BATCH_INDEX_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        os.replace(tmp, BATCH_INDEX_FILE)
    except OSError:
        pass


def report_path(batch_id):
    return os.path.join(BATCH_DIR, "%s.json" % batch_id)


def load_report(batch_id):
    fp = report_path(batch_id)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def delete_report(batch_id):
    """删除批次报告与机器人输出日志；返回 (ok, error)。"""
    report = load_report(batch_id)
    if report is None:
        return False, "批次报告不存在"
    # 机器人日志按 "<案例编码>.log" 命名，先从报告中收集再删除
    robot_logs = [r.get("robot_log") for r in report.get("runs", [])
                  if r.get("robot_log")]
    try:
        os.remove(report_path(batch_id))
    except OSError as e:
        return False, "删除失败：%s" % e
    for name in robot_logs:
        try:
            os.remove(os.path.join(ROBOT_LOG_DIR, name))
        except OSError:
            pass
    save_index([e for e in load_index() if e.get("batch_id") != batch_id])
    return True, ""


_CSV_HEADER = ["序号", "分组", "问题", "模式", "种子", "案例编码", "结束原因",
               "已清除", "总数", "清除比例%", "检测次数", "清除成功", "清除失败",
               "虚拟时间s", "平均清除s", "程序运行s", "移动距离m",
               "机器人退出码", "超时", "本局耗时s", "错误"]


def build_csv(report):
    """把批次报告展开为 CSV 文本（utf-8-sig 便于 Excel 直接打开）。"""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(_CSV_HEADER)
    for r in report.get("runs", []):
        s = r.get("summary") or {}
        total = s.get("total")
        ratio = ("" if not total
                 else round(100.0 * (s.get("cleared") or 0) / total, 2))
        w.writerow([
            r.get("index"), r.get("group_name"),
            r.get("problem"), "正式" if r.get("mode") == "formal" else "演练",
            r.get("seed"), r.get("case_code"), s.get("reason", ""),
            s.get("cleared", ""), total if total is not None else "",
            ratio, s.get("measures", ""), s.get("clear_success", ""),
            s.get("clear_fail", ""),
            s.get("virtual_total_s", ""), s.get("avg_clear_s", ""),
            s.get("program_run_s", ""), s.get("total_move_m", ""),
            r.get("robot_exit_code") if r.get("robot_exit_code") is not None else "",
            "是" if r.get("timeout") else "",
            r.get("wall_s", ""), r.get("error") or "",
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 批量运行器
# ---------------------------------------------------------------------------

class BatchRunner:
    """在后台线程中逐局驱动「开局 → 机器人程序 → 结束 → 记录」。"""

    def __init__(self, app, config, port):
        self.app = app
        self.cfg = config
        self.port = int(port)
        self.batch_id = gen_batch_id()
        self.created_ms = now_ms()
        self.status = "running"   # running -> stopping -> finished/stopped
        self.error = None
        self.ended_ms = None
        self.stats = None
        self._runs = []
        self._current = None
        self._seed_i = 0
        self._mu = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._main,
                                        name="batch-runner", daemon=True)

    # ------- 对外接口 -------
    def start(self):
        self._thread.start()

    def is_live(self):
        return self.status in ("running", "stopping")

    def request_stop(self):
        with self._mu:
            if self.status == "running":
                self.status = "stopping"
        self._stop.set()

    def progress(self):
        with self._mu:
            return {"status": self.status, "batch_id": self.batch_id,
                    "done": len(self._runs),
                    "total": self.cfg.get("total_runs", 0)}

    def state_dict(self):
        with self._mu:
            return {
                "batch_id": self.batch_id,
                "status": self.status,
                "created_ms": self.created_ms,
                "ended_ms": self.ended_ms,
                "port": self.port,
                "config": self.cfg,
                "total_runs": self.cfg.get("total_runs", 0),
                "done_runs": len(self._runs),
                "current": dict(self._current) if self._current else None,
                "runs": [dict(r) for r in self._runs],
                "stats": self.stats,
                "error": self.error,
            }

    # ------- 内部工具 -------
    def _set_current(self, **kw):
        with self._mu:
            self._current = kw

    def _next_seed(self):
        with self._mu:
            i = self._seed_i
            self._seed_i += 1
        seeds = self.cfg["seeds"]
        if seeds:
            return seeds[i % len(seeds)]
        return random.randrange(1, 2 ** 31 - 1)

    @staticmethod
    def _wait_ended(session, timeout_s):
        t0 = time.time()
        while session.phase != "ended" and time.time() - t0 < timeout_s:
            time.sleep(0.1)

    @staticmethod
    def _reap_process(proc, grace_s=5.0):
        """等待机器人自行退出；超时先礼后兵强杀。返回退出码或 None。"""
        if proc is None:
            return None
        try:
            return proc.wait(timeout=grace_s)
        except subprocess.TimeoutExpired:
            pass
        try:
            if os.name == "nt":
                # shell 启动的进程树需要 taskkill /T 才能连带子进程
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=5)
            else:
                proc.terminate()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return None

    # ------- 主循环 -------
    def _main(self):
        t0 = time.time()
        try:
            for gi, g in enumerate(self.cfg["groups"]):
                if self._stop.is_set():
                    break
                for _ in range(g["count"]):
                    if self._stop.is_set():
                        break
                    rec = self._run_one(gi, g)
                    with self._mu:
                        self._runs.append(rec)
                        rec["index"] = len(self._runs)
                    if rec.get("fatal"):
                        break               # 无法开局（次数用完等），终止本分组
                    if rec.get("error") and not self.cfg["continue_on_error"]:
                        self._stop.set()
                        break
                    if not self._stop.is_set():
                        self._stop.wait(self.cfg["inter_run_delay_s"])
        except Exception as e:  # 任何异常都要落盘已完成的局
            self.error = "批量线程异常：%s" % e
        stopped = self._stop.is_set()
        with self._mu:
            self._current = None
            self.ended_ms = now_ms()
            self.status = "stopped" if stopped else "finished"
            runs = list(self._runs)
        self.stats = compute_stats(runs, self.cfg,
                                   round(time.time() - t0, 1))
        self._save_report()

    # ------- 单局 -------
    def _run_one(self, gi, g):
        t0 = time.time()
        seed = self._next_seed()
        rec = {"index": 0, "group_index": gi, "group_name": g["name"],
               "problem": g["problem"], "mode": g["mode"], "seed": seed,
               "case_code": None, "ok": False, "error": None, "timeout": False,
               "fatal": False, "robot_exit_code": None, "robot_log": None,
               "wall_s": 0.0, "summary": None}
        self._set_current(phase="starting", group_index=gi,
                          group_name=g["name"], problem=g["problem"],
                          mode=g["mode"], seed=seed)

        ok, msg = self.app.start_test(g["problem"], g["mode"],
                                      seed=seed, internal=True)
        if not ok:
            rec["error"] = msg
            rec["fatal"] = True
            return rec
        session = self.app.current_session()
        if session is None:
            rec["error"] = "内部错误：会话丢失"
            rec["fatal"] = True
            return rec
        code = session.case_code
        rec["case_code"] = code
        self._set_current(phase="countdown", case_code=code, group_index=gi,
                          group_name=g["name"], problem=g["problem"],
                          mode=g["mode"], seed=seed)

        # 等倒计时结束（接口开放）或本局提前结束
        open_deadline = time.time() + float(session.p["countdown_s"]) + 10
        while session.phase in ("preparing", "countdown"):
            if session.phase == "ended" or time.time() > open_deadline:
                break
            if self._stop.is_set():
                self.app.abort_test()
            time.sleep(0.1)
        if session.phase == "ended":
            rec["summary"] = session.summary()
            rec["ok"] = rec["summary"] is not None
            rec["wall_s"] = round(time.time() - t0, 3)
            if not rec["ok"]:
                rec["error"] = rec["error"] or "本局在接口开放前结束"
            return rec

        # 启动机器人程序（输出重定向到文件，避免管道阻塞）
        self._set_current(phase="running_robot", case_code=code, group_index=gi,
                          group_name=g["name"], problem=g["problem"],
                          mode=g["mode"], seed=seed)
        logf = None
        proc = None
        try:
            try:
                os.makedirs(ROBOT_LOG_DIR, exist_ok=True)
                logf = open(os.path.join(ROBOT_LOG_DIR, "%s.log" % code),
                            "w", encoding="utf-8", errors="replace")
                rec["robot_log"] = "%s.log" % code
            except OSError:
                logf = subprocess.DEVNULL
            env = dict(os.environ)
            env.update({
                "SIMULATOR_PORT": str(self.port),
                "SIMULATOR_BASE_URL": "http://127.0.0.1:%d" % self.port,
                "SIMULATOR_CASE_CODE": code,
                "SIMULATOR_PROBLEM": str(g["problem"]),
                "SIMULATOR_MODE": g["mode"],
                "SIMULATOR_SEED": str(seed),
            })
            try:
                proc = subprocess.Popen(g["robot_command"], shell=True,
                                        cwd=g["cwd"], stdout=logf,
                                        stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, env=env)
            except OSError as e:
                rec["error"] = "机器人程序启动失败：%s" % e
                self.app.abort_test()
                self._wait_ended(session, 10)
                rec["summary"] = session.summary()
                rec["ok"] = rec["summary"] is not None
                rec["wall_s"] = round(time.time() - t0, 3)
                return rec

            # 等待本局结束（每局超时 / 停止批量 -> 中止本局）
            timeout_at = t0 + float(self.cfg["per_run_timeout_s"])
            aborted = False
            while session.phase != "ended":
                if not aborted:
                    if self._stop.is_set():
                        self._set_current(phase="aborting", case_code=code,
                                          group_index=gi,
                                          group_name=g["name"],
                                          problem=g["problem"], mode=g["mode"],
                                          seed=seed)
                        self.app.abort_test()
                        aborted = True
                    elif time.time() >= timeout_at:
                        rec["timeout"] = True
                        self._set_current(phase="aborting", case_code=code,
                                          group_index=gi,
                                          group_name=g["name"],
                                          problem=g["problem"], mode=g["mode"],
                                          seed=seed)
                        self.app.abort_test()
                        aborted = True
                time.sleep(0.15)

            rec["robot_exit_code"] = self._reap_process(proc)
            rec["summary"] = session.summary()
            rec["ok"] = rec["summary"] is not None
            if not rec["ok"]:
                rec["error"] = "未取得本局统计摘要"
            rec["wall_s"] = round(time.time() - t0, 3)
            return rec
        finally:
            if logf not in (None, subprocess.DEVNULL):
                try:
                    logf.close()
                except OSError:
                    pass

    # ------- 报告 -------
    def _save_report(self):
        data = self.state_dict()
        stats = data.get("stats") or {}
        entry = {
            "batch_id": self.batch_id,
            "created_ms": self.created_ms,
            "ended_ms": self.ended_ms,
            "status": self.status,
            "desc": describe_groups(self.cfg["groups"]),
            "total_runs": data["total_runs"],
            "done_runs": data["done_runs"],
            "clear_rate_overall": stats.get("clear_rate_overall"),
            "full_clear_rate": stats.get("full_clear_rate"),
        }
        try:
            os.makedirs(BATCH_DIR, exist_ok=True)
            tmp = report_path(self.batch_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, report_path(self.batch_id))
        except OSError:
            return
        index = [e for e in load_index() if e.get("batch_id") != self.batch_id]
        index.append(entry)
        save_index(index)
