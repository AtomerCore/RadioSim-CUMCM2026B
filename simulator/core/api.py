# -*- coding: utf-8 -*-
"""机器人接口协议实现：POST /enter /measure /clear /exit。

逐条对照附件 2《模拟器通信接口说明及编程指南》：
- 路径精确匹配、仅 POST；未知路径 404，已知路径非 POST 405
- Content-Type/Content-Encoding 校验 415；请求体超限 413
- JSON 语法/重复键/嵌套深度/字段类型/取值范围 400
- 未知字段、arena_id 不匹配 -> 200 + accepted=false（robot_id 仅校验格式，不校验取值）
- request_id 幂等：同 ID 同内容重放首次响应，同 ID 不同内容 409
- 并发不同动作 409；幂等记录达上限 429
- 接口未开放/测试结束时直接断开连接（无 HTTP 响应）
"""
import json
import math
import unicodedata

from .jsonutil import Num, dumps
from .session import now_ms

ROBOT_PATHS = ("/enter", "/measure", "/clear", "/exit")

_TOP_FIELDS = {
    "/enter": ("arena_id", "robot_id", "request_id"),
    "/exit": ("arena_id", "robot_id", "request_id"),
    "/measure": ("arena_id", "robot_id", "request_id", "position", "channel"),
    "/clear": ("arena_id", "robot_id", "request_id", "position", "channel"),
}


# ---------------------------------------------------------------------------
# JSON 解析
# ---------------------------------------------------------------------------

class _DuplicateKey(ValueError):
    pass


def _pairs_hook(pairs):
    d = {}
    for k, v in pairs:
        if k in d:
            raise _DuplicateKey(k)
        d[k] = v
    return d


def _reject_constant(name):
    raise ValueError("json constant not allowed: %s" % name)


def _json_depth(o):
    """迭代计算 JSON 嵌套层数（顶层为 1）。"""
    stack = [(o, 1)]
    depth = 1
    while stack:
        v, d = stack.pop()
        if isinstance(v, dict):
            depth = max(depth, d)
            for x in v.values():
                stack.append((x, d + 1))
        elif isinstance(v, list):
            depth = max(depth, d)
            for x in v:
                stack.append((x, d + 1))
    return depth


def parse_json_body(raw):
    """解析请求体。返回 (obj, None) 或 (None, 错误码)。"""
    if raw.startswith(b"\xef\xbb\xbf"):
        return None, "bom_not_allowed"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "invalid_utf8"
    try:
        obj = json.loads(text, object_pairs_hook=_pairs_hook,
                         parse_constant=_reject_constant)
    except _DuplicateKey:
        return None, "duplicate_key"
    except (ValueError, RecursionError):
        return None, "invalid_json"
    if not isinstance(obj, dict):
        return None, "not_json_object"
    if _json_depth(obj) > 16:
        return None, "nesting_too_deep"
    return obj, None


# ---------------------------------------------------------------------------
# 字段校验
# ---------------------------------------------------------------------------

def _bad_identifier(s, max_bytes):
    """robot_id/request_id 校验：UTF-8 长度限制，禁控制字符与不可见格式字符。"""
    b = s.encode("utf-8")
    if not (1 <= len(b) <= max_bytes):
        return True
    for ch in s:
        if unicodedata.category(ch) in ("Cc", "Cf"):
            return True
    return False


def _check_coord(v, p):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    if not math.isfinite(f):
        return None
    if abs(f) > float(p["coord_abs_max"]):
        return None
    return f


def _check_channel(v, p):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        iv = v
    elif isinstance(v, float):
        if not v.is_integer():
            return None
        iv = int(v)
    else:
        return None
    if iv < int(p["channel_min"]) or iv > int(p["channel_max"]):
        return None
    return iv


def validate_fields(path, obj, p):
    """结构与取值校验。

    返回 (fields, err, has_unknown)：
    - err 非空：HTTP 400（结构/类型/范围错误）
    - has_unknown：结构合法但含未声明字段 -> HTTP 200 + accepted=false
    - fields: {"arena_id","robot_id","request_id", "position":[x,y]?, "channel":int?}
    """
    for key in ("arena_id", "robot_id", "request_id"):
        if key not in obj:
            return None, "missing_field:%s" % key, False
        if not isinstance(obj[key], str):
            return None, "field_type:%s" % key, False
    if _bad_identifier(obj["robot_id"], 64):
        return None, "bad_robot_id", False
    if _bad_identifier(obj["request_id"], 128):
        return None, "bad_request_id", False

    fields = {
        "arena_id": obj["arena_id"],
        "robot_id": obj["robot_id"],
        "request_id": obj["request_id"],
        "position": None,
        "channel": None,
    }

    if path in ("/measure", "/clear"):
        if "position" not in obj:
            return None, "missing_field:position", False
        pos = obj["position"]
        if not isinstance(pos, dict):
            return None, "field_type:position", False
        for key in ("x", "y"):
            if key not in pos:
                return None, "missing_field:position.%s" % key, False
        x = _check_coord(pos["x"], p)
        y = _check_coord(pos["y"], p)
        if x is None:
            return None, "bad_coordinate:position.x", False
        if y is None:
            return None, "bad_coordinate:position.y", False
        fields["position"] = [x, y]

        if "channel" not in obj:
            return None, "missing_field:channel", False
        ch = _check_channel(obj["channel"], p)
        if ch is None:
            return None, "bad_channel", False
        fields["channel"] = ch

        unknown = [k for k in obj if k not in _TOP_FIELDS[path]]
        unknown += ["position.%s" % k for k in pos if k not in ("x", "y")]
    else:
        unknown = [k for k in obj if k not in _TOP_FIELDS[path]]

    return fields, None, bool(unknown)


def canonical_content(path, fields):
    """幂等比较用规范化内容（字段语义级比较，与键顺序无关）。"""
    return json.dumps({
        "path": path,
        "arena_id": fields["arena_id"],
        "robot_id": fields["robot_id"],
        "request_id": fields["request_id"],
        "position": fields["position"],
        "channel": fields["channel"],
    }, sort_keys=True, separators=(",", ":"))


# ---------------------------------------------------------------------------
# 响应构造
# ---------------------------------------------------------------------------

def _error_body(error):
    return {
        "accepted": False,
        "real_timestamp_ms": now_ms(),
        "virtual_time_s": Num("0"),
        "error": error,
    }


def check_content_headers(headers):
    """返回 None（合法）或错误码（415）。"""
    ct = headers.get("Content-Type")
    if ct is None:
        return "missing_content_type"
    parts = [s.strip() for s in ct.split(";")]
    if parts[0].lower() != "application/json":
        return "unsupported_content_type"
    for param in parts[1:]:
        if not param:
            continue
        name, _, value = param.partition("=")
        if name.strip().lower() != "charset" or value.strip().lower() != "utf-8":
            return "unsupported_media_type_parameter"
    ce = headers.get("Content-Encoding")
    if ce is not None and ce.strip().lower() != "identity":
        return "unsupported_content_encoding"
    return None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def handle_robot_request(app, path, headers, body, t_ms):
    """处理一条机器人指令。

    返回 ("close", None) 表示直接断开连接；否则 (status, body_bytes)。
    """
    session = app.session
    if session is None or not session.interface_open(t_ms):
        return "close", None
    p = session.p  # 本局参数快照

    # --- 415 ---
    err = check_content_headers(headers)
    if err:
        return _respond_error(session, 415, err, path, None)

    # --- 413 ---
    if len(body) > int(p["body_max_bytes"]):
        return _respond_error(session, 413, "body_too_large", path, None)

    # --- 400: JSON 结构 ---
    obj, jerr = parse_json_body(body)
    if jerr:
        return _respond_error(session, 400, jerr, path, None)

    # --- 400: 字段结构/类型/范围 ---
    fields, verr, has_unknown = validate_fields(path, obj, p)
    if verr:
        return _respond_error(session, 400, verr, path,
                              obj.get("request_id") if isinstance(obj.get("request_id"), str) else None)

    # --- 200 + accepted=false：未知字段 ---
    if has_unknown:
        session.note_state_reject(path, fields, "unknown_field")
        return 200, dumps({
            "accepted": False,
            "real_timestamp_ms": now_ms(),
            "virtual_time_s": Num("0"),
        }).encode("utf-8")

    # --- 200 + accepted=false：arena_id 不匹配（robot_id 不再校验，任意值均可接入） ---
    if fields["arena_id"] != "default":
        session.note_state_reject(path, fields, "arena_id_mismatch")
        return _false_body()

    rid = fields["request_id"]
    canon = canonical_content(path, fields)

    # --- 幂等：同 ID 不同内容 -> 409；同 ID 同内容 -> 重放 ---
    rec, conflict = session.idem_lookup(rid, canon)
    if conflict:
        return _respond_error(session, 409, "request_id_conflict", path, rid)
    if rec is not None:
        return rec.status, rec.body.encode("utf-8")

    # --- 并发不同动作 -> 409 ---
    if not session.in_flight_begin(rid):
        return _respond_error(session, 409, "concurrent_action", path, rid)
    try:
        # --- 幂等记录上限 -> 429 ---
        if session.idem_size() >= int(p["idempotency_limit"]):
            return _respond_error(session, 429, "idempotency_limit", path, rid)

        status, body, _reason = session.execute(path, fields, t_ms)
        body_str = dumps(body)
        if body.get("accepted") is True:
            session.idem_record(rid, path, canon, status, body_str)
        return status, body_str.encode("utf-8")
    finally:
        session.in_flight_end(rid)


def _false_body():
    return 200, dumps({
        "accepted": False,
        "real_timestamp_ms": now_ms(),
        "virtual_time_s": Num("0"),
    }).encode("utf-8")


def _respond_error(session, status, error, path, request_id):
    session.note_http_error(path, status, error, request_id)
    return status, dumps(_error_body(error)).encode("utf-8")
