# -*- coding: utf-8 -*-
"""无线电干扰源环境模拟器（本地复刻版）——程序入口。

用法：
    python simulator.py            # 默认 http://127.0.0.1:2026，自动打开浏览器
    python simulator.py --port 3000 --no-open

机器人接口（与官方完全一致）：POST http://127.0.0.1:<port>/enter|/measure|/clear|/exit
管理界面：浏览器打开 http://127.0.0.1:<port>/
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

from core import api, batch, world
from core.config import (Config, LOG_DIR, load_attempts, save_attempts,
                         SIM_DIR, DATA_DIR)
from core.jsonutil import dumps
from core.session import TestSession, gen_case_code, now_ms

__version__ = "1.0"

WEB_DIR = os.path.join(SIM_DIR, "web")
LOG_INDEX_FILE = os.path.join(DATA_DIR, "log_index.json")

_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/viz.js": ("viz.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


# ---------------------------------------------------------------------------
# 应用对象
# ---------------------------------------------------------------------------

class SimulatorApp:
    """持有配置、当前会话、正式测试次数与日志索引。"""

    def __init__(self):
        self.cfg = Config()
        self.session = None
        self.batch = None            # BatchRunner（当前/最近一次批量跑测）
        self.attempts = load_attempts()
        self.mu = threading.RLock()

    # ------- 测试控制 -------
    def start_test(self, problem, mode, seed=None, internal=False):
        """开局。internal=True 供批量跑测线程调用（跳过批量占用检查）。

        seed：None 时使用设置页的随机种子；批量跑测逐局显式传入。
        """
        with self.mu:
            if not internal and self.batch_live():
                return False, "批量跑测进行中，请先停止批量"
            if self.session is not None and self.session.phase != "ended":
                return False, "已有测试进行中，请先结束当前测试"
            if problem not in (3, 4):
                return False, "problem 必须为 3 或 4"
            if mode not in ("practice", "formal"):
                return False, "mode 必须为 practice 或 formal"
            key = "p%d" % problem
            limit = int(self.cfg.get("formal_attempts"))
            if mode == "formal":
                if self.attempts.get(key, 0) >= limit:
                    return False, "问题 %d 的正式测试次数已用完（可在设置中重置）" % problem
                self.attempts[key] = self.attempts.get(key, 0) + 1
                save_attempts(self.attempts)

            custom = world.load_custom_case(self.cfg)
            if custom is not None:
                sources = custom
            else:
                sources, _seed = world.generate_case(problem, self.cfg, seed)
            code = gen_case_code(problem, mode)
            self.session = TestSession(self, problem, mode, self.cfg,
                                       sources, code)
            return True, code

    def current_session(self):
        with self.mu:
            return self.session

    # ------- 批量跑测 -------
    def batch_live(self):
        b = self.batch
        return b is not None and b.is_live()

    def start_batch(self, config, port):
        """启动批量跑测。返回 (ok, batch_id 或错误信息)。"""
        with self.mu:
            if self.session is not None and self.session.phase != "ended":
                return False, "已有测试进行中，请先结束当前测试"
            if self.batch_live():
                return False, "已有批量跑测进行中"
            runner = batch.BatchRunner(self, config, port)
            self.batch = runner
            runner.start()
            return True, runner.batch_id

    def stop_batch(self):
        with self.mu:
            b = self.batch
        if b is None or not b.is_live():
            return False, "当前没有进行中的批量跑测"
        b.request_stop()
        return True, ""

    def abort_test(self):
        with self.mu:
            if self.session is None or self.session.phase == "ended":
                return False, "当前没有进行中的测试"
            self.session.end("user_abort")
            return True, ""

    def reset_attempts(self, problem):
        with self.mu:
            key = "p%d" % problem
            self.attempts[key] = 0
            save_attempts(self.attempts)

    def active(self):
        with self.mu:
            return self.session is not None and self.session.phase != "ended"

    # ------- 状态查询 -------
    def state_payload(self, after, reveal):
        with self.mu:
            session = self.session
            attempts = {
                "p3_remaining": max(0, int(self.cfg.get("formal_attempts"))
                                    - self.attempts.get("p3", 0)),
                "p4_remaining": max(0, int(self.cfg.get("formal_attempts"))
                                    - self.attempts.get("p4", 0)),
            }
            b = self.batch
            batch_info = b.progress() if b is not None else None
        events, last_seq = [], 0
        snap = None
        if session is not None:
            snap = session.snapshot(reveal)
            events, last_seq = session.events_after(after)
        return {
            "ok": True,
            "now_ms": now_ms(),
            "port": self.cfg.get("port"),
            "session": snap,
            "events": events,
            "last_seq": last_seq,
            "attempts": attempts,
            "batch": batch_info,
            "logs": self._load_log_index(),
            "formal_attempts_limit": int(self.cfg.get("formal_attempts")),
        }

    # ------- 日志管理 -------
    def delete_log(self, code):
        """删除单条测试日志（文件 + 索引项）。返回 (ok, error)。"""
        with self.mu:
            fp = os.path.join(LOG_DIR, "%s.json" % code)
            if not os.path.exists(fp):
                return False, "日志不存在"
            try:
                os.remove(fp)
            except OSError as e:
                return False, "删除失败：%s" % e
            self._save_log_index(
                [e for e in self._load_log_index() if e.get("case_code") != code])
            return True, ""

    def clear_logs(self):
        """删除全部测试日志，返回删除的文件数。"""
        with self.mu:
            n = 0
            if os.path.isdir(LOG_DIR):
                for fn in os.listdir(LOG_DIR):
                    if fn.endswith(".json"):
                        try:
                            os.remove(os.path.join(LOG_DIR, fn))
                            n += 1
                        except OSError:
                            pass
            self._save_log_index([])
            return n

    def open_logs_folder(self):
        """在系统文件管理器中打开日志目录。"""
        os.makedirs(LOG_DIR, exist_ok=True)
        if os.name == "nt":
            os.startfile(LOG_DIR)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", LOG_DIR])
        else:
            subprocess.Popen(["xdg-open", LOG_DIR])

    # ------- 日志索引 -------
    def _load_log_index(self):
        try:
            with open(LOG_INDEX_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return sorted(data, key=lambda x: x.get("ended_ms", 0),
                              reverse=True)
        except (OSError, ValueError):
            pass
        return []

    def _save_log_index(self, index):
        with self.mu:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                tmp = LOG_INDEX_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(index, f, ensure_ascii=False)
                os.replace(tmp, LOG_INDEX_FILE)
            except OSError:
                pass

    def on_session_ended(self, session, summary):
        """会话结束后登记日志索引（由 TestSession 回调）。"""
        entry = {
            "case_code": session.case_code,
            "problem": session.problem,
            "mode": session.mode,
            "created_ms": session.created_ms,
            "ended_ms": session.ended_ms,
            "summary": summary,
        }
        with self.mu:
            index = [e for e in self._load_log_index()
                     if e.get("case_code") != entry["case_code"]]
            index.append(entry)
            self._save_log_index(index)


# ---------------------------------------------------------------------------
# HTTP 处理器
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "RadioSim/%s" % __version__

    # ---- 快捷方法分发 ----
    def do_POST(self):
        self._route("POST")

    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("HEAD")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def do_OPTIONS(self):
        self._route("OPTIONS")

    def log_message(self, fmt, *args):  # 安静模式
        pass

    # ---- 路由 ----
    def _valid_host(self):
        """Host 头须为本机回环地址（防 DNS 重绑定）。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        if host.startswith("["):                 # [::1]:port
            host = host[1:].split("]")[0]
        else:                                    # 127.0.0.1:port
            host = host.split(":")[0]
        return host in ("127.0.0.1", "localhost", "::1")

    def _route(self, method):
        try:
            if not self._valid_host():
                return self._send_json_status(
                    421, {"ok": False, "error": "misdirected_request"})
            split = urlsplit(self.path)
            path, query = split.path, split.query
            app = self.server.app

            # 机器人接口：路径必须精确（带查询参数视为未知路径 -> 404）
            if path in api.ROBOT_PATHS and not query:
                return self._robot(method, path)
            if path in api.ROBOT_PATHS and query:
                return self._send_json_status(
                    404, api._error_body("unknown_path"))

            if path.startswith("/api/"):
                return self._admin(method, path, query)

            if method in ("GET", "HEAD") and path in _STATIC_FILES:
                return self._static(path, head_only=(method == "HEAD"))

            return self._send_json_status(404, api._error_body("unknown_path"))
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:  # 兜底：任何异常都不应拖垮服务器
            try:
                self._send_json_status(500, api._error_body("internal_error:%s" % e))
            except Exception:
                self.close_connection = True

    # ---- 机器人接口 ----
    def _robot(self, method, path):
        if method != "POST":
            return self._send_json_status(405,
                                          api._error_body("method_not_allowed"))
        length = 0
        cl = self.headers.get("Content-Length")
        if cl is not None:
            try:
                length = int(cl)
            except ValueError:
                length = 0
        if length < 0:
            length = 0
        # 防御性上限：即使声明超长也最多读 1MB，超限部分由 413 刄断
        body = self.rfile.read(min(length, 1 << 20)) if length else b""
        # “完整到达”时刻：请求体读取完毕
        t_ms = now_ms()
        status, payload = api.handle_robot_request(
            self.server.app, path, self.headers, body, t_ms)
        if status == "close":
            # 接口未开放/测试已结束：直接断开连接，无 HTTP 响应
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        self._send_bytes(status, payload, "application/json; charset=utf-8")

    # ---- 管理接口（本地 UI 专用） ----
    def _admin(self, method, path, query):
        app = self.server.app

        if method == "GET" and path == "/api/state":
            qs = parse_qs(query)
            after = int(qs.get("after", ["0"])[0])
            reveal = qs.get("reveal", ["0"])[0] == "1"
            return self._send_json(200, app.state_payload(after, reveal))

        if method == "GET" and path == "/api/config":
            return self._send_json(200, {"ok": True, "config": app.cfg.as_dict(),
                                         "active": app.active()})

        if method == "GET" and path == "/api/logs":
            return self._send_json(200, {"ok": True, "logs": app._load_log_index()})

        if method == "GET" and path.startswith("/api/log/"):
            code = path[len("/api/log/"):]
            if not code or not all(c.isalnum() or c == "-" for c in code):
                return self._send_json(404, {"ok": False, "error": "日志不存在"})
            fp = os.path.join(LOG_DIR, "%s.json" % code)
            if not os.path.exists(fp):
                return self._send_json(404, {"ok": False, "error": "日志不存在"})
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s.json"' % code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        # ------- 批量跑测 -------
        if method == "GET" and path == "/api/batch_state":
            b = app.batch
            return self._send_json(200, {"ok": True,
                                         "batch": b.state_dict() if b else None})

        if method == "GET" and path == "/api/batches":
            return self._send_json(200, {"ok": True,
                                         "batches": batch.load_index()})

        if method == "GET" and path.startswith("/api/batch_csv/"):
            bid = path[len("/api/batch_csv/"):]
            report = batch.load_report(bid) if self._valid_batch_id(bid) else None
            if report is None:
                return self._send_json(404, {"ok": False, "error": "批次报告不存在"})
            data = b"\xef\xbb\xbf" + batch.build_csv(report).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             'attachment; filename="%s.csv"' % bid)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if method == "GET" and path.startswith("/api/batch/"):
            bid = path[len("/api/batch/"):]
            report = batch.load_report(bid) if self._valid_batch_id(bid) else None
            if report is None:
                return self._send_json(404, {"ok": False, "error": "批次报告不存在"})
            return self._send_json(200, report)

        if method == "GET" and path.startswith("/api/robot_log/"):
            name = path[len("/api/robot_log/"):]
            if not self._valid_batch_id(name):
                return self._send_json(404, {"ok": False, "error": "日志不存在"})
            fp = os.path.join(batch.ROBOT_LOG_DIR, "%s.log" % name)
            if not os.path.exists(fp):
                return self._send_json(404, {"ok": False, "error": "日志不存在"})
            try:
                with open(fp, "rb") as f:
                    data = f.read()
            except OSError:
                return self._send_json(404, {"ok": False, "error": "日志不存在"})
            self._send_bytes(200, data, "text/plain; charset=utf-8")
            return

        if method == "POST":
            # 与机器人接口对齐：仅接受 application/json，阻断跨站表单 CSRF
            ct = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ct != "application/json":
                return self._send_json(415, {"ok": False,
                                             "error": "Content-Type 须为 application/json"})
            body = self._read_body()
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (ValueError, UnicodeDecodeError):
                return self._send_json(400, {"ok": False, "error": "JSON 解析失败"})
            if not isinstance(payload, dict):
                return self._send_json(400, {"ok": False, "error": "请求体须为 JSON 对象"})

            if path == "/api/start":
                problem = payload.get("problem")
                mode = payload.get("mode")
                if isinstance(problem, float) and problem.is_integer():
                    problem = int(problem)
                ok, msg = app.start_test(problem, mode)
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "error": None if ok else msg,
                                        "case_code": msg if ok else None})

            if path == "/api/abort":
                ok, msg = app.abort_test()
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "error": None if ok else msg})

            if path == "/api/reset_attempts":
                problem = payload.get("problem")
                if problem not in (3, 4):
                    return self._send_json(400, {"ok": False, "error": "problem 须为 3 或 4"})
                app.reset_attempts(problem)
                return self._send_json(200, {"ok": True})

            if path == "/api/log_delete":
                code = payload.get("case_code")
                if not isinstance(code, str) or not code \
                        or not all(c.isalnum() or c == "-" for c in code):
                    return self._send_json(400, {"ok": False, "error": "非法的测试案例编码"})
                ok, err = app.delete_log(code)
                return self._send_json(200 if ok else 404,
                                       {"ok": ok, "error": None if ok else err})

            if path == "/api/logs_clear":
                return self._send_json(200, {"ok": True,
                                             "deleted": app.clear_logs()})

            if path == "/api/open_logs_folder":
                app.open_logs_folder()
                return self._send_json(200, {"ok": True})

            if path == "/api/batch_start":
                if app.active():
                    return self._send_json(400, {"ok": False,
                                                 "error": "测试进行中，无法开始批量跑测"})
                config, err = batch.validate_batch_config(payload, app.cfg)
                if err:
                    return self._send_json(400, {"ok": False, "error": err})
                port = self.server.manager.current_port() \
                    or int(app.cfg.get("port"))
                ok, msg = app.start_batch(config, port)
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "error": None if ok else msg,
                                        "batch_id": msg if ok else None})

            if path == "/api/batch_stop":
                ok, msg = app.stop_batch()
                return self._send_json(200 if ok else 400,
                                       {"ok": ok, "error": None if ok else msg})

            if path == "/api/batch_delete":
                bid = payload.get("batch_id")
                if not self._valid_batch_id(bid):
                    return self._send_json(400, {"ok": False, "error": "非法的批次号"})
                ok, err = batch.delete_report(bid)
                return self._send_json(200 if ok else 404,
                                       {"ok": ok, "error": None if ok else err})

            if path == "/api/config":
                return self._admin_save_config(app, payload)

            return self._send_json(404, {"ok": False, "error": "unknown api path"})

        return self._send_json(405, {"ok": False, "error": "method not allowed"})

    def _admin_save_config(self, app, payload):
        if app.active() or app.batch_live():
            return self._send_json(400, {"ok": False,
                                         "error": "测试或批量跑测进行中，无法修改设置"})
        patch = payload.get("config")
        if not isinstance(patch, dict):
            return self._send_json(400, {"ok": False, "error": "缺少 config 字段"})
        new_port = patch.get("port")
        old_port = int(app.cfg.get("port"))
        port_changed = (isinstance(new_port, int) and not isinstance(new_port, bool)
                        and new_port != old_port)
        # 先试绑定新端口，成功后再提交配置
        if port_changed:
            mgr = self.server.manager
            ok, err = mgr.try_bind(new_port)
            if not ok:
                return self._send_json(400, {"ok": False, "error": err})
        ok, err = app.cfg.update(patch)
        if not ok:
            if port_changed:
                self.server.manager.cancel_pending_bind()
            return self._send_json(400, {"ok": False, "error": err})
        if port_changed:
            self.server.manager.commit_pending_bind()
            return self._send_json(200, {"ok": True, "port_changed": True,
                                         "new_port": new_port})
        return self._send_json(200, {"ok": True})

    @staticmethod
    def _valid_batch_id(bid):
        """批次号 / 案例编码风格 ID 的安全校验（防路径穿越）。"""
        return (isinstance(bid, str) and bid
                and all(c.isalnum() or c == "-" for c in bid))

    # ---- 静态文件 ----
    def _static(self, path, head_only=False):
        fname, ctype = _STATIC_FILES[path]
        fp = os.path.join(WEB_DIR, fname)
        try:
            with open(fp, "rb") as f:
                data = f.read()
        except OSError:
            return self._send_json_status(404, api._error_body("unknown_path"))
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    # ---- 工具 ----
    def _read_body(self):
        cl = self.headers.get("Content-Length")
        if cl is None:
            return b""
        try:
            length = int(cl)
        except ValueError:
            return b""
        return self.rfile.read(max(0, min(length, 1 << 22)))

    def _send_json_status(self, status, obj):
        self._send_bytes(status, dumps(obj).encode("utf-8"),
                         "application/json; charset=utf-8")

    def _send_json(self, status, obj):
        self._send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

    def _send_bytes(self, status, data, ctype):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


# ---------------------------------------------------------------------------
# 服务器管理（支持空闲时切换端口）
# ---------------------------------------------------------------------------

class ServerManager:
    def __init__(self, app):
        self.app = app
        self._server = None
        self._thread = None
        self._pending = None  # 待切换的新服务器
        self._mu = threading.Lock()

    def try_bind(self, port):
        """预绑定新端口（成功后暂存，等待配置提交）。"""
        with self._mu:
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            except OSError as e:
                return False, "端口 %d 不可用：%s" % (port, e)
            httpd.daemon_threads = True
            httpd.app = self.app
            httpd.manager = self
            self._pending = httpd
            return True, ""

    def cancel_pending_bind(self):
        with self._mu:
            if self._pending is not None:
                self._pending.server_close()
                self._pending = None

    def commit_pending_bind(self):
        with self._mu:
            new = self._pending
            self._pending = None
        if new is None:
            return
        old = self._server
        self._start_serving(new)
        if old is not None:
            threading.Thread(target=self._shutdown, args=(old,), daemon=True).start()

    @staticmethod
    def _shutdown(server):
        try:
            server.shutdown()
            server.server_close()
        except OSError:
            pass

    def bind_initial(self, port):
        with self._mu:
            try:
                httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            except OSError as e:
                return False, str(e)
            httpd.daemon_threads = True
            httpd.app = self.app
            httpd.manager = self
        self._start_serving(httpd)
        return True, ""

    def _start_serving(self, httpd):
        self._server = httpd
        t = threading.Thread(target=httpd.serve_forever,
                             kwargs={"poll_interval": 0.2},
                             name="http-server", daemon=True)
        t.start()
        self._thread = t

    def current_port(self):
        return self._server.server_address[1] if self._server else None


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

BANNER = r"""
=====================================================
  无线电干扰源环境模拟器（本地复刻版）  CUMCM 2026 B 题
=====================================================
  机器人接口 : http://127.0.0.1:%d/enter|/measure|/clear|/exit
  管理界面   : http://127.0.0.1:%d/
  Ctrl+C 退出
=====================================================
"""


def main():
    parser = argparse.ArgumentParser(description="无线电干扰源环境模拟器（本地复刻版）")
    parser.add_argument("--port", type=int, help="机器人接口端口（默认 2026 或已保存配置）")
    parser.add_argument("--no-open", action="store_true", help="启动后不自动打开浏览器")
    args = parser.parse_args()

    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    app = SimulatorApp()
    if args.port:
        app.cfg.update({"port": args.port})

    port = int(app.cfg.get("port"))
    mgr = ServerManager(app)
    ok, err = mgr.bind_initial(port)
    if not ok:
        print("端口 %d 启动失败：%s" % (port, err))
        print("可用 --port 指定其他端口，或关闭占用该端口的程序后重试。")
        sys.exit(1)

    print(BANNER % (port, port))
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(
            "http://127.0.0.1:%d/" % port)).start()

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n正在退出模拟器 ...")


if __name__ == "__main__":
    main()
