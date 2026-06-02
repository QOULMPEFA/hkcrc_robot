/* Robot Control Panel — client-side logic */

const ROLE = document.body.dataset.role || "viewer";

// ── Camera (WebRTC primary, MJPEG fallback) ─────────────────────────────────────
const SRS_API = "http://frp-fit.com:35900";
const cameraVideo = document.getElementById("camera-video");
const cameraImg = document.getElementById("camera-img");
const cameraErr = document.getElementById("camera-error");
const camStatus = document.getElementById("cam-status");

cameraImg.addEventListener("error", () => {
  cameraImg.style.display = "none";
  cameraErr.style.display = "flex";
});
cameraImg.addEventListener("load", () => {
  cameraImg.style.display = "block";
  cameraErr.style.display = "none";
});

function _switchToVideo() {
  cameraImg.style.display = "none";
  cameraErr.style.display = "none";
  cameraVideo.style.display = "block";
}

(async function _connectWebRTC() {
  try {
    const pc = new RTCPeerConnection({
      iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
    });

    pc.oniceconnectionstatechange = () => {
      if (pc.iceConnectionState === "disconnected" ||
          pc.iceConnectionState === "failed") {
        camStatus.textContent = "WebRTC 断线 (MJPEG)";
        camStatus.className = "badge disconnected";
        cameraImg.style.display = "block";
        cameraVideo.style.display = "none";
      }
    };

    pc.addTransceiver("video", { direction: "recvonly" });
    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    await new Promise((r) => {
      if (pc.iceGatheringState === "complete") r();
      else pc.addEventListener("icecandidate", (e) => { if (!e.candidate) r(); });
    });

    const resp = await fetch(SRS_API + "/rtc/v1/play/", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sdp: pc.localDescription.sdp,
        streamurl: "webrtc://frp-fit.com/live/cam",
        api: SRS_API + "/rtc/v1/play/",
      }),
    });

    if (!resp.ok) throw new Error("API " + resp.status);

    const data = await resp.json();
    if (data.code !== 0) throw new Error("SRS " + data.code);

    await pc.setRemoteDescription({ type: "answer", sdp: data.sdp });
    camStatus.textContent = "WebRTC 协商中";
    camStatus.className = "badge";
    // Keep MJPEG visible until ontrack actually fires.
    // If no video track within 8 s, assume the stream is empty and stay on MJPEG.
    let _fallbackTimer = setTimeout(() => {
      camStatus.textContent = "MJPEG";
      camStatus.className = "badge";
    }, 8000);
    pc.ontrack = (evt) => {
      clearTimeout(_fallbackTimer);
      cameraVideo.srcObject = evt.streams[0];
      _switchToVideo();
      camStatus.textContent = "WebRTC ✓";
      camStatus.className = "badge connected";
    };
  } catch (e) {
    console.log("WebRTC camera fallback — " + e.message);
    camStatus.textContent = "MJPEG";
    camStatus.className = "badge";
    cameraImg.style.display = "block";  // show MJPEG fallback
  }
})();

const socket = io({ transports: ["websocket"] });

// ── connection badge ──────────────────────────────────────────────────────────
const connBadge = document.getElementById("conn-badge");
socket.on("connect",    () => { connBadge.textContent = "Connected";    connBadge.className = "badge connected"; });
socket.on("disconnect", () => { connBadge.textContent = "Disconnected"; connBadge.className = "badge disconnected"; });

// Redirect to login if server says session is gone
socket.on("redirect", (data) => { window.location.href = data.url; });

// ── helpers ───────────────────────────────────────────────────────────────────
const el  = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

// ── viewer: disable all control buttons/inputs ────────────────────────────────
function _applyViewerRestrictions() {
  document.querySelectorAll("button.ctrl, .dpad-btn.ctrl").forEach((b) => {
    b.disabled = true;
    b.title = "观察员无操作权限";
  });
  ["lift-target", "lift-speed", "sl-linear", "sl-angular"].forEach((id) => {
    const el_ = el(id);
    if (el_) el_.disabled = true;
  });
}
if (ROLE === "viewer") _applyViewerRestrictions();

// ── state display ─────────────────────────────────────────────────────────────
socket.on("robot_state", (s) => {
  // UGV
  el("ugv-connected").textContent = s.ugv.connected ? "✓ 已连接" : "✗ 未连接";
  el("ugv-battery").textContent   = fmt(s.ugv.battery_voltage, 1);
  el("ugv-linear").textContent    = fmt(s.ugv.linear_velocity);
  el("ugv-angular").textContent   = fmt(s.ugv.angular_velocity);

  // Lift
  el("lift-height").textContent = fmt(s.lift.height_mm, 1);
  el("lift-vel").textContent    = fmt(s.lift.velocity_mm_s, 1);
  el("lift-torque").textContent = s.lift.torque_permille ?? "—";
  const faultBadge = el("lift-fault-badge");
  faultBadge.style.display = s.lift.is_fault ? "inline-block" : "none";

  if (typeof s.lift.min_mm === "number") {
    // Update modal display (admin only — elements may not exist)
    const lmin = el("lift-limit-min");
    const lmax = el("lift-limit-max");
    const loff = el("lift-zero-offset");
    if (lmin) lmin.textContent = s.lift.min_mm.toFixed(1);
    if (lmax) lmax.textContent = s.lift.max_mm.toFixed(1);
    if (loff) loff.textContent = s.lift.zero_offset_mm.toFixed(1);
    const inMin = el("lift-min-mm");
    const inMax = el("lift-max-mm");
    if (inMin && document.activeElement !== inMin) inMin.value = s.lift.min_mm;
    if (inMax && document.activeElement !== inMax) inMax.value = s.lift.max_mm;
    _rebuildPresets(s.lift.min_mm, s.lift.max_mm);
  }

  // Lift CAN diagnostics + operation log
  const dsEl2 = el("lift-drive-state");
  const swEl2 = el("lift-sw");
  if (dsEl2) {
    dsEl2.textContent = s.lift.drive_state || "—";
    const ds = s.lift.drive_state || "";
    if (ds.toLowerCase().includes("fault")) dsEl2.style.color = "#f44336";
    else if (ds.toLowerCase().includes("operation")) dsEl2.style.color = "#4caf50";
    else dsEl2.style.color = "#ff9800";
  }
  if (swEl2) swEl2.textContent = s.lift.statusword || "—";
  const logPanel = el("lift-log-panel");
  if (logPanel && s.lift.logs && s.lift.logs.length > 0) {
    const lastTs = logPanel.dataset.lastTs || "0";
    const latest = s.lift.logs[s.lift.logs.length - 1];
    if (latest.ts > parseFloat(lastTs)) {
      logPanel.dataset.lastTs = latest.ts;
      logPanel.innerHTML = s.lift.logs.map(e => {
        const t = new Date(e.ts * 1000).toLocaleTimeString();
        const cls = e.level === "error" ? "#f44336" : e.level === "warn" ? "#ff9800" : "#999";
        return `<div style="color:${cls};line-height:1.6">${t} [${e.level.toUpperCase()}] ${e.msg}</div>`;
      }).join("");
      logPanel.scrollTop = logPanel.scrollHeight;
    }
  }

  // Arm
  el("arm-enabled").textContent = s.arm.is_enabled ? "已使能" : "未使能";
  el("arm-moving").textContent  = s.arm.is_moving  ? "运动中" : "静止";
  if (s.arm.joint_angles_deg) {
    s.arm.joint_angles_deg.forEach((angle, i) => {
      const sl  = el(`arm-j${i}`);
      const val = el(`arm-j${i}-val`);
      if (sl && !sl.matches(":active")) sl.value = angle;
      if (val) val.textContent = fmt(angle, 1) + "°";
    });
  }

  // Hand
  el("hand-enabled").textContent = s.hand.is_enabled ? "已使能" : "未使能";
  el("hand-force").textContent   = fmt(s.hand.grip_force_n, 1);
  if (s.hand.finger_positions) {
    s.hand.finger_positions.forEach((pos, i) => {
      const sl  = el(`hand-f${i}`);
      const val = el(`hand-f${i}-val`);
      if (sl && !sl.matches(":active")) sl.value = pos;
      if (val) val.textContent = fmt(pos * 100, 0) + "%";
    });
  }

  el("last-update").textContent = "最后更新：" + new Date().toLocaleTimeString();
});

// ── emergency stop ────────────────────────────────────────────────────────────
el("btn-estop").addEventListener("click", () => {
  socket.emit("ugv_estop");
  socket.emit("lift_stop");
  socket.emit("arm_stop");
  socket.emit("hand_stop");
});

// ── UGV ───────────────────────────────────────────────────────────────────────
const slLinear  = el("sl-linear");
const slAngular = el("sl-angular");
const lblLinear  = el("lbl-linear");
const lblAngular = el("lbl-angular");

slLinear.addEventListener("input",  () => { lblLinear.textContent  = slLinear.value; });
slAngular.addEventListener("input", () => { lblAngular.textContent = slAngular.value; });

document.querySelectorAll(".dpad-btn").forEach((btn) => {
  const send = () => {
    const l = parseFloat(btn.dataset.l) * parseFloat(slLinear.value);
    const a = parseFloat(btn.dataset.a) * parseFloat(slAngular.value);
    socket.emit("ugv_velocity", { linear: l, angular: a });
  };
  btn.addEventListener("mousedown",  send);
  btn.addEventListener("touchstart", send, { passive: true });
  const stop = () => socket.emit("ugv_stop");
  btn.addEventListener("mouseup",    stop);
  btn.addEventListener("mouseleave", stop);
  btn.addEventListener("touchend",   stop);
});

// ── Lift ──────────────────────────────────────────────────────────────────────
let _lastPresetKey = "";
function _rebuildPresets(minMm, maxMm) {
  const key = `${minMm}:${maxMm}`;
  if (key === _lastPresetKey) return;
  _lastPresetKey = key;
  const container = el("lift-presets");
  container.innerHTML = "";
  const range = maxMm - minMm;
  [0, 0.25, 0.5, 0.75, 1.0].forEach((f) => {
    const h   = Math.round(minMm + range * f);
    const btn = document.createElement("button");
    btn.className = "btn-preset" + (ROLE === "viewer" ? " ctrl" : "");
    btn.dataset.h = h;
    btn.textContent = `${h} mm`;
    if (ROLE === "viewer") { btn.disabled = true; }
    else {
      btn.addEventListener("click", () => {
        el("lift-target").value = h;
        socket.emit("lift_move", { height_mm: h, speed_mm_s: parseFloat(el("lift-speed").value) });
      });
    }
    container.appendChild(btn);
  });
}
_rebuildPresets(0, 500);

el("btn-lift-move").addEventListener("click", () => {
  socket.emit("lift_move", {
    height_mm:  parseFloat(el("lift-target").value),
    speed_mm_s: parseFloat(el("lift-speed").value),
  });
});
el("btn-lift-stop").addEventListener("click",  () => socket.emit("lift_stop"));
el("btn-lift-home").addEventListener("click",  () => {
  socket.emit("lift_move", { height_mm: 0.0, speed_mm_s: parseFloat(el("lift-speed").value) });
});
el("btn-lift-reset").addEventListener("click", () => socket.emit("lift_reset_fault"));

// Lift log panel toggle
const btnToggleLog = el("btn-toggle-lift-log");
if (btnToggleLog) {
  btnToggleLog.addEventListener("click", () => {
    const panel = el("lift-log-panel");
    if (panel.style.display === "none") {
      panel.style.display = "block";
      btnToggleLog.innerHTML = "运行日志 &#9650;";
    } else {
      panel.style.display = "none";
      btnToggleLog.innerHTML = "运行日志 &#9660;";
    }
  });
}

// Server confirms updated limits
socket.on("lift_limits", (data) => {
  const lmin = el("lift-limit-min");
  const lmax = el("lift-limit-max");
  const loff = el("lift-zero-offset");
  const inMin = el("lift-min-mm");
  const inMax = el("lift-max-mm");
  if (lmin) lmin.textContent = data.min_mm.toFixed(1);
  if (lmax) lmax.textContent = data.max_mm.toFixed(1);
  if (loff) loff.textContent = data.zero_offset_mm.toFixed(1);
  if (inMin) inMin.value = data.min_mm;
  if (inMax) inMax.value = data.max_mm;
  _rebuildPresets(data.min_mm, data.max_mm);
});

// ── Arm — build joint sliders ─────────────────────────────────────────────────
const ARM_JOINTS = ["J1", "J2", "J3", "J4", "J5", "J6"];
const armGrid = el("arm-joints");
ARM_JOINTS.forEach((name, i) => {
  const row = document.createElement("div");
  row.className = "joint-row";
  row.innerHTML = `
    <label>${name}</label>
    <input type="range" id="arm-j${i}" min="-180" max="180" step="1" value="0" />
    <span class="val" id="arm-j${i}-val">0.0°</span>
  `;
  armGrid.appendChild(row);
  row.querySelector("input").addEventListener("input", (e) => {
    el(`arm-j${i}-val`).textContent = parseFloat(e.target.value).toFixed(1) + "°";
  });
});
el("btn-arm-send").addEventListener("click", () => {
  const angles = ARM_JOINTS.map((_, i) => parseFloat(el(`arm-j${i}`).value));
  socket.emit("arm_move_joints", { angles, speed_pct: 20 });
});
el("btn-arm-home").addEventListener("click", () => socket.emit("arm_home"));
el("btn-arm-stop").addEventListener("click", () => socket.emit("arm_stop"));

// ── Hand — build finger sliders ───────────────────────────────────────────────
const FINGER_NAMES = ["拇指", "食指", "中指", "无名指", "小指"];
const handGrid = el("hand-fingers");
FINGER_NAMES.forEach((name, i) => {
  const row = document.createElement("div");
  row.className = "finger-row";
  row.innerHTML = `
    <label>${name}</label>
    <input type="range" id="hand-f${i}" min="0" max="1" step="0.01" value="0" />
    <span class="val" id="hand-f${i}-val">0%</span>
  `;
  handGrid.appendChild(row);
  row.querySelector("input").addEventListener("input", (e) => {
    el(`hand-f${i}-val`).textContent = Math.round(parseFloat(e.target.value) * 100) + "%";
  });
});
el("btn-hand-open").addEventListener("click",  () => socket.emit("hand_open"));
el("btn-hand-close").addEventListener("click", () => socket.emit("hand_close"));
el("btn-hand-pinch").addEventListener("click", () => socket.emit("hand_pinch"));
el("btn-hand-send").addEventListener("click",  () => {
  const positions = FINGER_NAMES.map((_, i) => parseFloat(el(`hand-f${i}`).value));
  socket.emit("hand_set_fingers", { positions, speed_pct: 50 });
});

// ── Settings Modal (admin only) ───────────────────────────────────────────────
if (ROLE === "admin") {
  const overlay = el("settings-overlay");

  el("btn-settings").addEventListener("click", () => {
    overlay.style.display = "flex";
    socket.emit("admin_get_users");
    // load current camera timeout
    const inp = el("camera-timeout");
    if (inp) socket.emit("settings_get");
  });

  el("btn-settings-close").addEventListener("click", () => {
    overlay.style.display = "none";
  });
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.style.display = "none";
  });

  // Lift zero & limits (inside modal)
  el("btn-lift-set-zero").addEventListener("click", () => socket.emit("lift_set_zero"));
  el("btn-lift-set-limits").addEventListener("click", () => {
    const minVal = parseFloat(el("lift-min-mm").value);
    const maxVal = parseFloat(el("lift-max-mm").value);
    if (isNaN(minVal) || isNaN(maxVal) || minVal >= maxVal) {
      alert("最小高度必须小于最大高度"); return;
    }
    socket.emit("lift_set_limits", { min_mm: minVal, max_mm: maxVal });
  });

  // Camera timeout
  el("btn-save-camera-timeout").addEventListener("click", () => {
    const val = parseInt(el("camera-timeout").value, 10);
    if (isNaN(val) || val < 1) { alert("请输入有效的分钟数"); return; }
    socket.emit("settings_set", { key: "camera_timeout_min", value: val });
  });
  socket.on("settings_ack", (data) => {
    if (data.key === "camera_timeout_min") {
      const inp = el("camera-timeout");
      if (inp) inp.value = data.value;
    }
  });

  // User management table
  socket.on("admin_users", (users) => {
    const tbody = el("user-table-body");
    if (!tbody) return;
    tbody.innerHTML = "";
    users.forEach((u) => {
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${u.username}</td>
        <td>
          <select class="role-select" data-id="${u.id}">
            <option value="viewer"   ${u.role === "viewer"   ? "selected" : ""}>观察员</option>
            <option value="operator" ${u.role === "operator" ? "selected" : ""}>操作员</option>
            <option value="admin"    ${u.role === "admin"    ? "selected" : ""}>管理员</option>
          </select>
        </td>
        <td><span class="badge ${u.status === 'active' ? 'active' : 'pending'}">${u.status === 'active' ? '已激活' : '待审批'}</span></td>
        <td class="user-actions">
          ${u.status === 'pending'
            ? `<button class="btn-primary btn-sm btn-approve" data-id="${u.id}">批准</button>`
            : ""}
          <button class="btn-secondary btn-sm btn-delete" data-id="${u.id}">删除</button>
        </td>
      `;
      tbody.appendChild(tr);
    });

    // Role change
    tbody.querySelectorAll(".role-select").forEach((sel) => {
      sel.addEventListener("change", () => {
        socket.emit("admin_update_user", { id: parseInt(sel.dataset.id), role: sel.value });
      });
    });
    // Approve
    tbody.querySelectorAll(".btn-approve").forEach((btn) => {
      btn.addEventListener("click", () => {
        socket.emit("admin_update_user", { id: parseInt(btn.dataset.id), status: "active" });
      });
    });
    // Delete
    tbody.querySelectorAll(".btn-delete").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (confirm("确认删除该用户？")) {
          socket.emit("admin_delete_user", { id: parseInt(btn.dataset.id) });
        }
      });
    });
  });

  // Error feedback
  socket.on("error", (data) => alert(data.msg));
}
