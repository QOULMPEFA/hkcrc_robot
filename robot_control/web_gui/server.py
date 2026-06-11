"""Web GUI server — Flask + Flask-SocketIO.

Exposes a browser-based control panel for robot subsystems.
Hardware modules are discovered from Robot config — the GUI adapts automatically.

Run:
    python robot_control/web_gui/server.py
    # then open http://<jetson-ip>:6080
"""

import logging
import os
import sys
import subprocess
import threading
import time

from flask import Flask, render_template, Response, request, session, redirect, url_for
from flask_socketio import SocketIO, emit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from robot_control.robot import Robot

# auth module lives alongside this file
sys.path.insert(0, os.path.dirname(__file__))
import auth

log = logging.getLogger(__name__)

# ── Flask / SocketIO setup ────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = "robot-gui-secret"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Global robot instance — created in main()
robot: Robot = None

# ── Activity tracking (for camera watchdog) ───────────────────────────────────

_last_activity: float = time.time()

def _touch_activity():
    global _last_activity
    _last_activity = time.time()


# ── Permission helpers ────────────────────────────────────────────────────────

ROLE_LEVEL = {"viewer": 0, "operator": 1, "admin": 2}

def _get_session_user():
    return auth.get_current_user()

def _require_role(min_role: str) -> bool:
    user = _get_session_user()
    if not user:
        emit("error", {"msg": "请先登录"})
        return False
    if ROLE_LEVEL.get(user["role"], 0) < ROLE_LEVEL[min_role]:
        emit("error", {"msg": "权限不足"})
        return False
    return True


# ── Camera stream endpoint (uses CameraModule if loaded) ──────────────────────

def _camera_module():
    """Return the camera module instance, or None."""
    if robot:
        return robot.modules.get("camera")
    return None


def _gen_client_stream(cid):
    """Yield latest frames via condition variable — zero buffering, always fresh."""
    cam = _camera_module()
    count = 0
    try:
        while True:
            frame, _seq = cam.wait_frame(cid, timeout=5)
            if frame is None:
                log.info("[Camera] /stream: timeout — sent %d frames", count)
                return
            count += 1
            yield frame
    except GeneratorExit:
        log.info("[Camera] /stream: client disconnected — sent %d frames", count)
        raise


@app.route("/stream")
@auth.login_required
def camera_stream():
    cam = _camera_module()
    if not cam:
        return Response("Camera module not loaded", status=503)
    cid = cam.subscribe()
    log.info("[Camera] /stream: client subscribed (clients=%d)", cam.client_count())
    def generate():
        try:
            yield from _gen_client_stream(cid)
        finally:
            cam.unsubscribe(cid)
            log.info("[Camera] /stream: client unsubscribed (clients=%d)", cam.client_count())
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ── SRS WebRTC API proxy ────────────────────────────────────────────────────
# Browser connects to the same origin; Flask forwards internally to SRS.
# This avoids exposing SRS ports directly and works through FRP tunnels.

import urllib.request as _urllib_req

_SRS_API = "http://127.0.0.1:1985"


@app.route("/srs-api/", defaults={"subpath": ""}, methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
@app.route("/srs-api/<path:subpath>", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
def _srs_proxy(subpath):
    if robot is None and request.method != "OPTIONS":
        return {"code": -1, "msg": "robot not ready"}, 503
    target = f"{_SRS_API}/{subpath}"
    if request.query_string:
        target += "?" + request.query_string.decode()
    body = request.get_data() or None
    headers = {"Content-Type": request.content_type or "application/json"}
    try:
        req = _urllib_req.Request(target, data=body, headers=headers, method=request.method)
        with _urllib_req.urlopen(req, timeout=5) as resp:
            return resp.read(), resp.status, {"Content-Type": resp.headers.get("Content-Type", "application/json")}
    except Exception as e:
        log.warning("[SRS proxy] error: %s", e)
        return {"code": -1, "msg": str(e)}, 502


def _camera_watchdog():
    """Stop camera when no subscribers AND no activity for N minutes."""
    while True:
        time.sleep(30)
        try:
            cam = _camera_module()
            if not cam:
                continue
            timeout_sec = float(auth.get_setting("camera_timeout_min", "5")) * 60
            no_subs = cam.client_count() == 0
            no_activity = (time.time() - _last_activity) > timeout_sec
            if no_subs and no_activity:
                log.info("[Camera] Watchdog: stopping ffmpeg (idle)")
                cam.stop()
        except Exception as e:
            log.debug("[Camera] Watchdog error: %s", e)


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.route("/")
@auth.login_required
def index():
    user = auth.get_current_user()
    return render_template("index.html", current_user=dict(user), role=user["role"])


@app.route("/login", methods=["GET", "POST"])
def login():
    if _get_session_user():
        return redirect(url_for("index"))
    msg   = request.args.get("msg", "")
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = auth.check_password(username, password)
        if not user:
            error = "用户名或密码错误"
        elif user["status"] == "pending":
            return redirect(url_for("login", msg="pending"))
        elif user["status"] != "active":
            error = "账号已被禁用"
        else:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["session_token"] = auth.set_session_token(user["id"])
            return redirect(url_for("index"))
    return render_template("login.html", msg=msg, error=error)


@app.route("/register", methods=["GET", "POST"])
def register():
    if _get_session_user():
        return redirect(url_for("index"))
    error   = ""
    success = False
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not username or not password:
            error = "用户名和密码不能为空"
        elif len(username) < 2 or len(username) > 32:
            error = "用户名长度须在 2-32 个字符之间"
        elif password != confirm:
            error = "两次输入的密码不一致"
        elif len(password) < 6:
            error = "密码长度至少 6 位"
        else:
            ok, err = auth.create_user(username, password)
            if ok:
                success = True
            else:
                error = err
    return render_template("register.html", error=error, success=success)


@app.route("/logout")
def logout():
    user_id = session.get("user_id")
    if user_id:
        auth.clear_session_token(user_id)
    session.clear()
    return redirect(url_for("login"))


# ── SocketIO — module handler registration ────────────────────────────────────

def _register_module_handlers(robot: Robot):
    """Iterate over loaded modules and register each one's SocketIO handlers."""
    for name, module in robot.modules.items():
        handlers = module.get_socketio_handlers()
        if not handlers:
            continue

        for event, handler_fn in handlers.items():
            # Wrap so emit is passed through
            def make_listener(fn=handler_fn):
                def listener(data=None):
                    _touch_activity()
                    fn(data or {}, emit)
                return listener

            wrapped = make_listener()
            # Attach role check: all module events require at least "operator"
            socketio.on_event(event, _with_role("operator", wrapped))
        log.info("[GUI] Registered %d handler(s) for module '%s'", len(handlers), name)


def _with_role(min_role: str, handler):
    """Wrap a handler with role-based access control."""
    def wrapper(data=None):
        if _require_role(min_role):
            return handler(data)
    wrapper.__name__ = handler.__name__
    return wrapper


# ── SocketIO events — lifecycle ───────────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    _touch_activity()
    user = _get_session_user()
    if not user:
        emit("redirect", {"url": "/login"})
        return
    log.info("[GUI] Client connected: %s (%s)", user["username"], user["role"])
    emit("connected", {"status": "ok", "role": user["role"],
                       "modules": robot.module_names if robot else []})


@socketio.on("disconnect")
def on_disconnect():
    log.info("[GUI] Client disconnected")


# ── SocketIO events — admin / settings ────────────────────────────────────────

@socketio.on("restart_can")
def on_restart_can(_data=None):
    _touch_activity()
    if not _require_role("admin"): return
    try:
        sudo_password = "1114\n"
        subprocess.run(
            ["sudo", "-S", "ip", "link", "set", "can0", "down"],
            input=sudo_password, text=True, capture_output=True, timeout=10)
        subprocess.run(
            ["sudo", "-S", "ip", "link", "set", "can0", "up", "type", "can", "bitrate", "500000"],
            input=sudo_password, text=True, capture_output=True, timeout=10)
        subprocess.run(
            ["sudo", "-S", "systemctl", "restart", "ugv_bridge.service"],
            input=sudo_password, text=True, capture_output=True, timeout=10)
        emit("restart_can_result", {"ok": True, "msg": "CAN 接口已重启"})
        log.info("[CAN] Interface restarted via Web GUI")
    except Exception as e:
        emit("restart_can_result", {"ok": False, "msg": f"重启失败: {e}"})
        log.error("[CAN] Restart failed: %s", e)


@socketio.on("settings_get")
def on_settings_get(_data=None):
    """Return all stored settings."""
    keys = ("camera_timeout_min",)
    result = {}
    for key in keys:
        result[key] = auth.get_setting(key, "")
    emit("settings_data", result)


@socketio.on("settings_set")
def on_settings_set(data):
    _touch_activity()
    if not _require_role("admin"): return
    key   = data.get("key", "")
    value = str(data.get("value", ""))
    if key in ("camera_timeout_min",):
        auth.set_setting(key, value)
        emit("settings_ack", {"key": key, "value": value})


@socketio.on("admin_get_users")
def on_admin_get_users(_data=None):
    if not _require_role("admin"): return
    rows = auth.list_users()
    emit("admin_users", [dict(r) for r in rows])


@socketio.on("admin_update_user")
def on_admin_update_user(data):
    if not _require_role("admin"): return
    user_id = data.get("id")
    role    = data.get("role")
    status  = data.get("status")
    me = _get_session_user()
    if me and me["id"] == user_id and role and role != "admin":
        emit("error", {"msg": "不能修改自己的角色"}); return
    auth.update_user(user_id, role=role, status=status)
    rows = auth.list_users()
    emit("admin_users", [dict(r) for r in rows])


@socketio.on("admin_delete_user")
def on_admin_delete_user(data):
    if not _require_role("admin"): return
    user_id = data.get("id")
    me = _get_session_user()
    if me and me["id"] == user_id:
        emit("error", {"msg": "不能删除自己的账号"}); return
    auth.delete_user(user_id)
    rows = auth.list_users()
    emit("admin_users", [dict(r) for r in rows])


@socketio.on("system_power")
def on_system_power(data):
    if not _require_role("admin"): return
    action = data.get("action", "")
    if action not in ("reboot", "shutdown"):
        emit("error", {"msg": f"未知操作: {action}"}); return
    user = _get_session_user()
    uid = user["id"] if user else "?"
    log.warning("[System] %s requested by user id=%s", action, uid)
    emit("system_power_result", {"action": action, "ok": True})
    import subprocess
    if action == "reboot":
        subprocess.Popen(["systemctl", "reboot"])
    else:
        subprocess.Popen(["systemctl", "poweroff"])


# ── state broadcast loop ──────────────────────────────────────────────────────

def _state_broadcast_loop():
    """Push robot state to all connected clients at 5 Hz."""
    loop_count = 0
    while True:
        try:
            if robot:
                state = robot.get_state()
                socketio.emit("robot_state", state)
                loop_count += 1
                if loop_count % 300 == 0:   # every ~60 s
                    lift = state.get("lift", {})
                    log.info("[State] lift height=%.1f mm, drive_state=%s",
                             lift.get("height_mm", float("nan")),
                             lift.get("drive_state", "?"))
        except Exception as e:
            log.warning("[GUI] State broadcast error: %s", e)
        time.sleep(0.2)


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    global robot

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
    )

    auth.init_db()

    # ── Robot config — pick the preset that matches your hardware ──────────
    # Each module gracefully degrades to stub/standby when its SDK or
    # hardware is absent.  Set "enabled": false or remove the key to hide it.
    #
    # ── All modules — enable what you need ───────────────────────────────
    # Each module degrades to stub mode when SDK or hardware is absent.
    # To hide a module from the GUI, remove it or set "enabled": false.
    config = {
        "arm":    {"host": "192.168.56.101"},
        "ugv":    {"host": "127.0.0.1", "port": 9100},
        "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
        "camera": {"device": "/dev/video0"},
        "motor":  {},
        "hand":   {"protocol": "modbus", "port": None, "baudrate": 115200, "slave_id": 0x7E},
    }
    #
    # ── Pipe robot minimal ──────────────────────────────────────────────
    # config = {"camera": {...}, "motor": {}, "lift": {...}}

    robot = Robot(config)
    robot.start()

    # Register dynamic SocketIO handlers from loaded modules
    _register_module_handlers(robot)

    threading.Thread(target=_state_broadcast_loop, daemon=True, name="state-broadcast").start()
    threading.Thread(target=_camera_watchdog,      daemon=True, name="camera-watchdog").start()

    loaded = robot.module_names
    log.info("[GUI] Server starting at http://0.0.0.0:6080 — modules: %s",
             loaded if loaded else ["(none)"])

    try:
        socketio.run(app, host="0.0.0.0", port=6080, debug=False,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    finally:
        robot.stop()


if __name__ == "__main__":
    main()
