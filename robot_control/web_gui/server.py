"""Web GUI server — Flask + Flask-SocketIO.

Exposes a browser-based control panel for all robot subsystems.
Real-time state is pushed to the browser via WebSocket every 200 ms.

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
from dataclasses import asdict

from flask import Flask, render_template, Response, request, session, redirect, url_for
from flask_socketio import SocketIO, emit

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from robot_control.robot import Robot, RobotConfig

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
    """Return active user Row from session, or None.
    Enforces single-session: rejects stale sessions whose token was overwritten.
    """
    return auth.get_current_user()

def _require_role(min_role: str) -> bool:
    """Emit error and return False if current user lacks the required role."""
    user = _get_session_user()
    if not user:
        emit("error", {"msg": "请先登录"})
        return False
    if ROLE_LEVEL.get(user["role"], 0) < ROLE_LEVEL[min_role]:
        emit("error", {"msg": "权限不足"})
        return False
    return True


# ── Camera stream broadcaster ─────────────────────────────────────────────────

CAMERA_DEVICE = "/dev/video0"

class _CameraBroadcaster:
    def __init__(self):
        self._lock    = threading.Lock()
        self._clients = {}
        self._frame   = None
        self._thread  = None
        self._running = False

    def _fetch_loop(self):
        cmd = [
            "ffmpeg", "-fflags", "nobuffer",
            "-f", "v4l2", "-input_format", "mjpeg",
            "-framerate", "30", "-video_size", "1280x720",
            "-i", CAMERA_DEVICE,
            "-f", "image2pipe", "-vf", "fps=15",
            "-vcodec", "mjpeg", "-q:v", "5", "pipe:1",
        ]
        while self._running:
            proc = None
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                log.info("[Camera] ffmpeg started (pid=%d)", proc.pid)
                buf = b""
                frame_n = 0
                while self._running:
                    chunk = proc.stdout.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while True:
                        s = buf.find(b"\xff\xd8")
                        e = buf.find(b"\xff\xd9", s + 2) if s != -1 else -1
                        if s == -1 or e == -1:
                            break
                        frame = buf[s:e + 2]
                        buf   = buf[e + 2:]
                        msg   = (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                 + frame + b"\r\n")
                        with self._lock:
                            self._frame = msg
                            frame_n += 1
                            if frame_n % 150 == 1:   # log every ~10 s at 15 fps
                                log.info("[Camera] frame #%d (%d bytes), %d client(s)",
                                         frame_n, len(frame), len(self._clients))
                            for q in self._clients.values():
                                q.append(msg)
            except Exception as exc:
                log.debug("[Camera] ffmpeg error: %s — retrying in 2 s", exc)
            finally:
                if proc:
                    proc.kill()
                    proc.wait()
            if self._running:
                time.sleep(2)

    def _ensure_running(self):
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._running = True
                self._thread  = threading.Thread(
                    target=self._fetch_loop, daemon=True, name="camera-fetch"
                )
                self._thread.start()

    def subscribe(self):
        self._ensure_running()
        q = []
        with self._lock:
            if self._frame:
                q.append(self._frame)
            self._clients[id(q)] = q
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._clients.pop(id(q), None)

    def stop(self):
        """Stop ffmpeg; will auto-restart on next subscribe()."""
        with self._lock:
            self._running = False
            self._frame   = None
            self._thread  = None

    def client_count(self):
        with self._lock:
            return len(self._clients)


_broadcaster = _CameraBroadcaster()


def _gen_client_stream(q):
    """Yield frames from queue. Bails out after 5 s if no frame arrives,
    so the browser <img> tag doesn't spin forever when the camera is gone."""
    deadline = time.time() + 5
    count = 0
    try:
        while True:
            if q:
                yield q.pop(0)
                count += 1
                deadline = time.time() + 5  # reset countdown on each frame
            elif time.time() > deadline:
                log.info("[Camera] /stream: timeout — sent %d frames", count)
                return
            else:
                time.sleep(0.05)
    except GeneratorExit:
        log.info("[Camera] /stream: client disconnected — sent %d frames", count)
        raise


def _camera_watchdog():
    """Stop ffmpeg when no subscribers AND no activity for N minutes."""
    while True:
        time.sleep(30)
        try:
            timeout_sec = float(auth.get_setting("camera_timeout_min", "5")) * 60
            no_subs     = _broadcaster.client_count() == 0
            no_activity = (time.time() - _last_activity) > timeout_sec
            if no_subs and no_activity:
                log.info("[Camera] Watchdog: stopping ffmpeg (idle)")
                _broadcaster.stop()
        except Exception as e:
            log.debug("[Camera] Watchdog error: %s", e)


# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.route("/stream")
@auth.login_required
def camera_stream():
    q = _broadcaster.subscribe()
    log.info("[Camera] /stream: client subscribed (clients=%d)",
             _broadcaster.client_count())
    def generate():
        try:
            yield from _gen_client_stream(q)
        finally:
            _broadcaster.unsubscribe(q)
            log.info("[Camera] /stream: client unsubscribed (clients=%d)",
                     _broadcaster.client_count())
    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


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


# ── SocketIO events — client → server ─────────────────────────────────────────

@socketio.on("connect")
def on_connect():
    _touch_activity()
    user = _get_session_user()
    if not user:
        emit("redirect", {"url": "/login"})
        return
    log.info("[GUI] Client connected: %s (%s)", user["username"], user["role"])
    emit("connected", {"status": "ok", "role": user["role"]})


@socketio.on("disconnect")
def on_disconnect():
    log.info("[GUI] Client disconnected")


# UGV
@socketio.on("ugv_velocity")
def on_ugv_velocity(data):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.ugv:
        robot.ugv.set_velocity(
            float(data.get("linear", 0.0)),
            float(data.get("angular", 0.0)),
        )


@socketio.on("ugv_stop")
def on_ugv_stop(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.ugv:
        robot.ugv.stop_motion()


@socketio.on("ugv_estop")
def on_ugv_estop(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot:
        robot.emergency_stop()


@socketio.on("ugv_enable")
def on_ugv_enable(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.ugv:
        robot.ugv.enable()


@socketio.on("ugv_reset")
def on_ugv_reset(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.ugv:
        robot.ugv.reset()


# Lift
@socketio.on("lift_move")
def on_lift_move(data):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.lift:
        robot.lift.move_to_async(
            float(data.get("height_mm", 0.0)),
            float(data.get("speed_mm_s", 10.0)),
        )


@socketio.on("lift_stop")
def on_lift_stop(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.lift:
        robot.lift.stop()


@socketio.on("lift_home")
def on_lift_home(data=None):
    _touch_activity()
    if not _require_role("operator"): return
    strategy = (data or {}).get("strategy", "current_pos")
    if robot and robot.lift:
        threading.Thread(target=robot.lift.home, args=(strategy,), daemon=True).start()


@socketio.on("lift_reset_fault")
def on_lift_reset_fault(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.lift:
        robot.lift.reset_fault()


@socketio.on("lift_set_zero")
def on_lift_set_zero(_data=None):
    _touch_activity()
    if not _require_role("admin"): return
    if robot and robot.lift:
        robot.lift.set_zero()
        emit("lift_limits", robot.lift.get_limits())


@socketio.on("lift_set_limits")
def on_lift_set_limits(data):
    _touch_activity()
    if not _require_role("admin"): return
    if robot and robot.lift:
        robot.lift.set_limits(
            float(data.get("min_mm", 0.0)),
            float(data.get("max_mm", 500.0)),
        )
        emit("lift_limits", robot.lift.get_limits())


# Arm
@socketio.on("arm_move_joints")
def on_arm_move_joints(data):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.arm:
        robot.arm.move_joints(
            data.get("angles", [0.0] * 6),
            float(data.get("speed_pct", 20.0)),
        )


@socketio.on("arm_home")
def on_arm_home(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.arm:
        robot.arm.go_home()


@socketio.on("arm_stop")
def on_arm_stop(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.arm:
        robot.arm.stop()


# Hand
@socketio.on("hand_open")
def on_hand_open(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.hand:
        robot.hand.open()


@socketio.on("hand_close")
def on_hand_close(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.hand:
        robot.hand.close()


@socketio.on("hand_pinch")
def on_hand_pinch(_data=None):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.hand:
        robot.hand.pinch()


@socketio.on("hand_set_fingers")
def on_hand_set_fingers(data):
    _touch_activity()
    if not _require_role("operator"): return
    if robot and robot.hand:
        robot.hand.set_all_fingers(
            data.get("positions", [0.0] * 5),
            float(data.get("speed_pct", 50.0)),
        )


# Admin — settings
@socketio.on("settings_set")
def on_settings_set(data):
    _touch_activity()
    if not _require_role("admin"): return
    key   = data.get("key", "")
    value = str(data.get("value", ""))
    if key in ("camera_timeout_min",):
        auth.set_setting(key, value)
        emit("settings_ack", {"key": key, "value": value})


# Admin — user management
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
    # prevent admin from demoting themselves
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


# ── state broadcast loop ──────────────────────────────────────────────────────

def _state_broadcast_loop():
    """Push robot state to all connected clients at 5 Hz."""
    loop_count = 0
    while True:
        try:
            if robot:
                state = robot.get_state()
                payload = {
                    "ugv":  asdict(state.ugv),
                    "lift": asdict(state.lift),
                    "arm":  asdict(state.arm),
                    "hand": asdict(state.hand),
                    "timestamp": state.timestamp,
                }
                socketio.emit("robot_state", payload)
                loop_count += 1
                if loop_count % 300 == 0:   # every ~60 s
                    h = payload["lift"].get("height_mm", "N/A")
                    log.info("[State] lift height=%.1f mm, drive_state=%s",
                             h, payload["lift"].get("drive_state", "?"))
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

    config = RobotConfig(
        lift_home_strategy="current_pos",
    )
    robot = Robot(config)
    robot.start()

    threading.Thread(target=_state_broadcast_loop, daemon=True, name="state-broadcast").start()
    threading.Thread(target=_camera_watchdog,      daemon=True, name="camera-watchdog").start()

    log.info("[GUI] Server starting at http://0.0.0.0:6080")
    try:
        socketio.run(app, host="0.0.0.0", port=6080, debug=False,
                     use_reloader=False, allow_unsafe_werkzeug=True)
    finally:
        robot.stop()


if __name__ == "__main__":
    main()
