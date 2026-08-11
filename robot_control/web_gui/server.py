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

from flask import Flask, render_template, Response, request, session, redirect, url_for, send_file
from flask_socketio import SocketIO, emit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from robot_control.robot import Robot

from robot_control.web_gui import auth, i18n

log = logging.getLogger(__name__)

# ── Flask / SocketIO setup ────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = "robot-gui-secret"
# WARNING: async_mode MUST be "threading" — NOT "eventlet".
# _state_broadcast_loop() runs in threading.Thread and socketio.emit()
# silently fails to deliver messages to eventlet-managed clients.
# Symptom of breakage: all Web GUI state shows "—" (no data).
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

def _lang():
    """Return the current user's language from Flask session, default zh."""
    return session.get("lang", "zh")

def _get_session_user():
    return auth.get_current_user()

def _require_role(min_role: str) -> bool:
    user = _get_session_user()
    if not user:
        emit("error", {"msg": i18n.t("login_required", _lang())})
        return False
    if ROLE_LEVEL.get(user["role"], 0) < ROLE_LEVEL[min_role]:
        emit("error", {"msg": i18n.t("permission_denied", _lang())})
        return False
    return True


# ── Camera stream endpoints (uses CameraModule if loaded) ──────────────────────

def _camera_module():
    """Return the camera module instance, or None."""
    if robot:
        return robot.modules.get("camera")
    return None


def _gen_device_stream(device_name, cid):
    """Yield latest frames for a specific device — zero buffering, always fresh."""
    cam = _camera_module()
    count = 0
    try:
        while True:
            frame, _seq = cam.wait_frame(device_name, cid, timeout=5)
            if frame is None:
                log.info("[Camera] /stream/%s: timeout — sent %d frames", device_name, count)
                return
            count += 1
            yield frame
    except GeneratorExit:
        log.info("[Camera] /stream/%s: client disconnected — sent %d frames", device_name, count)
        raise


def _validate_stream_token(token: str) -> bool:
    """Check if the given token matches an active session in the DB."""
    if not token:
        return False
    import sqlite3
    try:
        conn = sqlite3.connect(auth.DB_PATH)
        row = conn.execute(
            "SELECT COUNT(*) FROM users WHERE session_token = ?", (token,)
        ).fetchone()
        conn.close()
        return row[0] > 0 if row else False
    except Exception:
        return False


@app.route("/stream/<device_name>")
def camera_device_stream(device_name):
    # Auth via URL token (img tag can't send cookies reliably)
    token = request.args.get("token", "")
    if not _validate_stream_token(token):
        return Response("Unauthorized", status=401)
    cam = _camera_module()
    if not cam:
        return Response("Camera module not loaded", status=503)
    try:
        cid = cam.subscribe(device_name)
    except ValueError:
        return Response(f"Unknown device: {device_name}", status=404)
    log.info("[Camera] /stream/%s: client subscribed (total=%d)",
             device_name, cam.total_clients())
    def generate():
        try:
            yield from _gen_device_stream(device_name, cid)
        finally:
            cam.unsubscribe(device_name, cid)
            log.info("[Camera] /stream/%s: client unsubscribed (total=%d)",
                     device_name, cam.total_clients())
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/stream")
def camera_stream():
    """Legacy endpoint — delegates to the first active device stream."""
    cam = _camera_module()
    if not cam:
        return Response("Camera module not loaded", status=503)
    devices = cam.get_device_list()
    active = [d for d in devices if d.get("active")]
    if not active:
        return Response("No camera devices active", status=503)
    first = active[0]["id"]
    return redirect(url_for("camera_device_stream", device_name=first))


@app.route("/vr_stream")
def vr_stream():
    """Minimal fullscreen MJPEG viewer optimized for Quest 3 browser."""
    return render_template("vr_stream.html")


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
            no_subs = cam.total_clients() == 0
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
    import json as _json
    user = auth.get_current_user()
    token = session.get("session_token", "")
    cam = _camera_module()
    cam_devices = cam.get_device_list() if cam else []
    return render_template("index.html", current_user=dict(user), role=user["role"],
                           stream_token=token,
                           camera_devices=_json.dumps(cam_devices))


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
            error = i18n.t("wrong_credentials", _lang())
        elif user["status"] == "pending":
            return redirect(url_for("login", msg="pending"))
        elif user["status"] != "active":
            error = i18n.t("account_disabled", _lang())
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
            error = i18n.t("username_password_empty", _lang())
        elif len(username) < 2 or len(username) > 32:
            error = i18n.t("username_length", _lang())
        elif password != confirm:
            error = i18n.t("password_mismatch", _lang())
        elif len(password) < 6:
            error = i18n.t("password_length", _lang())
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


@app.route("/assets/urdf/<path:filename>")
def asset_urdf(filename):
    """Serve URDF description assets for the browser teleop simulator."""
    base = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "assets", "urdf"))
    path = os.path.realpath(os.path.join(base, filename))
    if not path.startswith(base) or not os.path.isfile(path):
        return Response("Not Found", status=404)
    return send_file(path)


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
            # Read-only events allow viewer; everything else requires operator
            _viewer_events = {"camera_list_devices", "camera_scan", "settings_get"}
            min_role = "viewer" if event in _viewer_events else "operator"
            socketio.on_event(event, _with_role(min_role, wrapped))
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
    # Store language preference from client query param
    from flask import request as flask_request
    lang = flask_request.args.get("lang", "zh")
    if lang in ("zh", "en"):
        session["lang"] = lang
    user = _get_session_user()
    if not user:
        emit("redirect", {"url": "/login"})
        return
    log.info("[GUI] Client connected: %s (%s)", user["username"], user["role"])
    emit("connected", {"status": "ok", "role": user["role"],
                       "modules": robot.module_names if robot else []})


@socketio.on("set_language")
def on_set_language(data):
    lang = data.get("lang", "zh") if data else "zh"
    if lang in ("zh", "en"):
        session["lang"] = lang


@socketio.on("disconnect")
def on_disconnect():
    log.info("[GUI] Client disconnected")


# ── SocketIO events — admin / settings ────────────────────────────────────────

def _get_sudo_password() -> str:
    """Return the configured sudo password (with trailing newline for stdin)."""
    pw = auth.get_setting("sudo_password", "orangepi")
    return pw + "\n" if pw else "orangepi\n"


@socketio.on("restart_can")
def on_restart_can(_data=None):
    _touch_activity()
    if not _require_role("admin"): return
    try:
        sp = _get_sudo_password()
        subprocess.run(
            ["sudo", "-S", "ip", "link", "set", "can0", "down"],
            input=sp, text=True, capture_output=True, timeout=10)
        subprocess.run(
            ["sudo", "-S", "ip", "link", "set", "can0", "up", "type", "can", "bitrate", "500000"],
            input=sp, text=True, capture_output=True, timeout=10)
        subprocess.run(
            ["sudo", "-S", "systemctl", "restart", "ugv_bridge.service"],
            input=sp, text=True, capture_output=True, timeout=10)
        emit("restart_can_result", {"ok": True, "msg": i18n.t("can_restarted", _lang())})
        log.info("[CAN] Interface restarted via Web GUI")
    except Exception as e:
        emit("restart_can_result", {"ok": False, "msg": i18n.t("restart_failed", _lang()).format(e)})
        log.error("[CAN] Restart failed: %s", e)


@socketio.on("settings_get")
def on_settings_get(_data=None):
    """Return all stored settings."""
    keys = ("camera_timeout_min", "sudo_password")
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
    if key in ("camera_timeout_min", "sudo_password"):
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
        emit("error", {"msg": i18n.t("cannot_modify_self", _lang())}); return
    auth.update_user(user_id, role=role, status=status)
    rows = auth.list_users()
    emit("admin_users", [dict(r) for r in rows])


@socketio.on("admin_delete_user")
def on_admin_delete_user(data):
    if not _require_role("admin"): return
    user_id = data.get("id")
    me = _get_session_user()
    if me and me["id"] == user_id:
        emit("error", {"msg": i18n.t("cannot_delete_self", _lang())}); return
    auth.delete_user(user_id)
    rows = auth.list_users()
    emit("admin_users", [dict(r) for r in rows])


@socketio.on("system_power")
def on_system_power(data):
    if not _require_role("admin"): return
    action = data.get("action", "")
    if action not in ("reboot", "shutdown"):
        emit("error", {"msg": i18n.t("unknown_action", _lang()).format(action)}); return
    user = _get_session_user()
    uid = user["id"] if user else "?"
    log.warning("[System] %s requested by user id=%s", action, uid)
    emit("system_power_result", {"action": action, "ok": True})
    import subprocess
    if action == "reboot":
        subprocess.Popen(f"echo {_get_sudo_password().strip()} | sudo -S systemctl reboot", shell=True)
    else:
        subprocess.Popen(f"echo {_get_sudo_password().strip()} | sudo -S systemctl poweroff", shell=True)


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


def _teleop_pose_broadcast_loop():
    """Push only the teleop hand pose at 30 Hz to keep the 3D view smooth."""
    interval = 1.0 / 30.0
    while True:
        try:
            if robot:
                teleop = robot.get("teleop")
                if teleop is not None:
                    socketio.emit("teleop_pose",
                                  teleop.get_controller_pose_state())
        except Exception as e:
            log.warning("[GUI] Teleop pose broadcast error: %s", e)
        time.sleep(interval)


def _arm_state_broadcast_loop():
    """Push arm actual-joint state at 10 Hz, matching UR secondary client.

    Keep this separate from the 5 Hz robot_state broadcast. The arm panel
    consumes only this event so a stale full-panel snapshot cannot overwrite
    newer arm data and cause displayed angles to roll back.
    """
    interval = 1.0 / 10.0
    while True:
        try:
            if robot:
                arm = robot.get("arm")
                if arm is not None:
                    socketio.emit("arm_state", arm.get_state())
        except Exception as e:
            log.warning("[GUI] Arm state broadcast error: %s", e)
        time.sleep(interval)


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
    # ═══════════════════════════════════════════════════════════════════════
    # 🦾 具身智能机器人 (Embodied Intelligence Robot) — Jetson AGX Orin
    #    硬件: UR5e 机械臂 + AgileX UGV 底盘 + BrainCo 灵巧手
    #          + 升降平台 (CAN bus) + 双目摄像头
    #    config = {
    #        "arm":    {"host": "192.168.56.101"},
    #        "ugv":    {"host": "127.0.0.1", "port": 9100},
    #        "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
    #        "camera": {"devices": ["/dev/video6", "/dev/video4"]},
    #        "motor":  {},
    #        "hand":   {"protocol": "can", "can_iface": "can1", "master_id": 1, "slave_id": 0x02},
    #        # candleLight USB-CAN FD adapter (Windows, no SocketCAN)
    #        # "hand":   {"protocol": "can_candlelight", "master_id": 1,
    #        #            "slave_id": 0x7E, "candlelight_channel": 0},
    #    }
    #
    # ── All modules (embodied intelligence robot default) ────────────────
    # config = {
    #     "arm":    {"host": "192.168.56.101"},
    #     "ugv":    {"host": "127.0.0.1", "port": 9100},
    #     "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
    #     "camera": {"devices": ["/dev/video6", "/dev/video4"]},
    #     "motor":  {},
    #     "hand":   {"protocol": "can", "can_iface": "can1", "master_id": 1, "slave_id": 0x02},
    # }
    #
    # ── Jetson AGX Orin (full embodied intelligence robot) — active ─────
    config = {
        "arm":    {
            "host": "192.168.56.101",
            "payload_mass": 0.5,
            "payload_cog": [0.0, 0.0, 0.05],
            "home_angles_deg": [90.0, 0.0, 90.0, -90.0, 90.0, -135.0],
        },
        "ugv":    {"host": "127.0.0.1", "port": 9100},
        "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
        "camera": {"auto_detect": True, "width": 1280, "height": 720},
        "motor":  {},
        # port omitted = auto-detect RS485
        "hand":   {"protocol": "modbus", "baudrate": 460800, "slave_id": 0x7F},
        "teleop": {
            "host": "192.168.56.101",
            "arm_controller": "left",         # installed left arm; right reserved
            "vr_backend": "xrt",              # XRoboToolkit-PC-Service via xrobotoolkit_sdk
            "vr_backend_kwargs": {},
            "urdf_path": "teleoperation_sdk/XRoboToolkit-Teleop-Sample-Python/assets/universal_robots_ur5e/ur5e.urdf",
            "link_name": "tool0",
            "joint_start": 7,
            "joint_end": 13,
            "control_mode": "pose",
            "scale_factor": 0.8,
            "control_rate_hz": 60,
            "servo_time": 0.017,
            "lookahead_time": 0.1,
            "servo_gain": 300.0,
            "max_velocity": 0.5,
            "max_acceleration": 1.0,
            "hand_follow_enabled": True,
            "hand_grip_speed_pct": 30.0,
            "hand_grip_max_position": 0.85,
            "hand_grip_max_force_n": 5.0,
            "head_yaw_sign": -1.0,
            "head_pitch_sign": 1.0,
        },
        "gimbal": {},
    }
    #
    # ═══════════════════════════════════════════════════════════════════════
    # 🔧 管道机器人 (Pipe Robot) — 区别于具身智能机器人
    #    硬件: 驱动电机 + 舵机/关节 + 摄像头 + 升降平台
    #    config = {
    #        "camera": {"devices": ["/dev/video1"]},
    #        "motor":  {},
    #        "joint":  {},
    #        "drive":  {},
    #        "demo":   {},
    #    }
    #
    # 🔧 管道机器人 Mini (Pipe Robot Mini) — 精简版 (Camera + Motor only)
    #    config = {"camera": {...}, "motor": {}}

    robot = Robot(config)
    robot.start()

    # Register dynamic SocketIO handlers from loaded modules
    _register_module_handlers(robot)

    threading.Thread(target=_state_broadcast_loop, daemon=True, name="state-broadcast").start()
    threading.Thread(target=_teleop_pose_broadcast_loop, daemon=True,
                     name="teleop-pose-broadcast").start()
    threading.Thread(target=_arm_state_broadcast_loop, daemon=True,
                     name="arm-state-broadcast").start()
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
