/* Robot Control Panel — dashboard with draggable panels & layout persistence */

const ROLE = document.body.dataset.role || "viewer";

// ── module metadata ──────────────────────────────────────────────────────────
const MODULE_META = {
  camera: { icon: "📷", name: "摄像头", i18nKey: "module.camera", stub: false },
  ugv:    { icon: "🛞", name: "UGV 底盘", i18nKey: "module.ugv",    stub: false },
  lift:   { icon: "↕",  name: "升降平台", i18nKey: "module.lift",   stub: false },
  arm:    { icon: "🦾", name: "机械臂",   i18nKey: "module.arm",    stub: false },
  hand:   { icon: "🤖", name: "灵巧手",   i18nKey: "module.hand",   stub: false },
  motor:  { icon: "⚙",  name: "电机控制", i18nKey: "module.motor",  stub: false },
  servo:  { icon: "⚡", name: "总线舵机", i18nKey: "module.servo",  stub: false },
  joint:  { icon: "🔧", name: "关节控制", i18nKey: "module.joint", stub: false },
  drive:  { icon: "🚗", name: "驱动电机", i18nKey: "module.drive", stub: false },
  demo:   { icon: "🎬", name: "演示序列", i18nKey: "module.demo",  stub: false },
  teleop: { icon: "🥽", name: "XR 遥操作", i18nKey: "module.teleop", stub: false },
  gimbal: { icon: "📷", name: "云台相机", i18nKey: "module.gimbal", stub: false },
};
function _modName(meta) { return meta.i18nKey ? I18n.t(meta.i18nKey) : meta.name; }
let ACTIVE_MODULES = [];

const el  = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

// ── Socket ───────────────────────────────────────────────────────────────────
I18n.init();
const socket = io({ transports: ["websocket", "polling"], query: { lang: I18n._lang } });

const connBadge = el("conn-badge");
socket.on("connect", () => {
  if (connBadge) { connBadge.textContent = I18n.t("header.connected"); connBadge.className = "badge connected"; }
});
socket.on("disconnect", () => {
  if (connBadge) { connBadge.textContent = I18n.t("header.disconnected"); connBadge.className = "badge disconnected"; }
});
socket.on("redirect", (data) => { window.location.href = data.url; });

// ── Module visibility ────────────────────────────────────────────────────────
socket.on("connected", (info) => {
  ACTIVE_MODULES = info.modules || [];
  _renderSidebar();
  LayoutManager.populateSelector();
  // Request camera device list for any open panels
  if (ACTIVE_MODULES.includes("camera")) {
    socket.emit("camera_list_devices");
  }
});

// ═══════════════════════════════════════════════════════════════════════════════
// Sidebar
// ═══════════════════════════════════════════════════════════════════════════════

function _renderSidebar() {
  const list = el("module-list");
  if (!list) return;
  list.innerHTML = "";
  ACTIVE_MODULES.forEach((mod) => {
    const meta = MODULE_META[mod] || { icon: "❓", name: mod, i18nKey: null, stub: false };
    const isOpen = PanelManager.isOpen(mod);
    const item = document.createElement("div");
    item.className = "module-item" + (isOpen ? " active" : "") + (meta.stub ? " stub-module" : "");
    const stubTag = meta.stub ? `<span class="stub-tag">${I18n.t("sidebar.sdk_stub")}</span>` : '';
    item.innerHTML = `
      <span class="module-icon">${meta.icon}</span>
      <span class="module-name">${_modName(meta)}${stubTag}</span>
      <span class="module-status" id="mod-status-${mod}"></span>
    `;
    if (mod === "camera") {
      // Multi-camera: always open a new panel
      item.addEventListener("click", () => {
        PanelManager.open(mod);
        _renderSidebar();
      });
      if (isOpen) {
        const count = PanelManager.countOpen(mod);
        const nameEl = item.querySelector(".module-name");
        if (nameEl) nameEl.dataset.count = count;
      }
    } else if (!isOpen) {
      item.addEventListener("click", () => {
        PanelManager.open(mod);
        _renderSidebar();
      });
    } else {
      item.addEventListener("click", () => {
        // Close all panels of this module
        PanelManager.closeAll(mod);
        _renderSidebar();
      });
    }
    list.appendChild(item);
  });

  // Quick actions
  const btnOpenAll = el("btn-open-all");
  const btnCloseAll = el("btn-close-all");
  if (btnOpenAll) btnOpenAll.onclick = () => { ACTIVE_MODULES.forEach((m) => { if (!PanelManager.isOpen(m)) PanelManager.open(m); }); _renderSidebar(); };
  if (btnCloseAll) btnCloseAll.onclick = () => { PanelManager.closeAll(); _renderSidebar(); };
}

// ═══════════════════════════════════════════════════════════════════════════════
// Panel Manager
// ═══════════════════════════════════════════════════════════════════════════════

const PanelManager = {
  _panels: new Map(),  // id → {module, el, size}
  _counter: 0,

  open(module, size) {
    size = size || "medium";
    const id = `panel-${module}-${++this._counter}`;
    const meta = MODULE_META[module] || { icon: "❓", name: module, i18nKey: null };

    const panel = document.createElement("div");
    panel.className = `panel size-${size}`;
    panel.id = id;
    panel.dataset.module = module;
    panel.dataset.size = size;
    panel.innerHTML = `
      <div class="panel-header" data-panel-id="${id}">
        <span class="panel-icon">${meta.icon}</span>
        <span class="panel-title">${_modName(meta)}</span>
        <span class="panel-status" id="${id}-status"></span>
        <div class="panel-actions">
          <button class="panel-btn panel-size" title="切换大小" data-action="resize">⛶</button>
          <button class="panel-btn panel-minimize" title="最小化" data-action="minimize">–</button>
          <button class="panel-btn panel-close" title="关闭" data-action="close">✕</button>
        </div>
      </div>
      <div class="panel-body" id="${id}-body"></div>
    `;

    el("canvas").appendChild(panel);
    this._panels.set(id, { module, el: panel, size });

    // Render module-specific content
    _renderPanelContent(id, module);

    // Header button events
    panel.querySelector(".panel-close").addEventListener("click", (e) => {
      e.stopPropagation();
      this.close(id);
      _renderSidebar();
    });
    panel.querySelector(".panel-minimize").addEventListener("click", (e) => {
      e.stopPropagation();
      this.toggleMinimize(id);
    });
    panel.querySelector(".panel-size").addEventListener("click", (e) => {
      e.stopPropagation();
      this.cycleSize(id);
    });

    // Drag initiation
    DragManager.attach(panel);

    // Scroll to new panel
    panel.scrollIntoView({ behavior: "smooth", block: "center" });

    _checkEmptyState();
    return id;
  },

  close(id) {
    const p = this._panels.get(id);
    if (!p) return;
    _cleanupPanel(id, p.module);
    p.el.remove();
    this._panels.delete(id);
    _checkEmptyState();
  },

  closeAll(module) {
    this._panels.forEach((p, id) => {
      if (!module || p.module === module) {
        _cleanupPanel(id, p.module);
        p.el.remove();
        this._panels.delete(id);
      }
    });
    _checkEmptyState();
  },

  isOpen(module) {
    for (const p of this._panels.values()) {
      if (p.module === module) return true;
    }
    return false;
  },

  countOpen(module) {
    let n = 0;
    this._panels.forEach(p => { if (p.module === module) n++; });
    return n;
  },

  toggleMinimize(id) {
    const p = this._panels.get(id);
    if (!p) return;
    p.el.classList.toggle("minimized");
  },

  cycleSize(id) {
    const p = this._panels.get(id);
    if (!p) return;
    const sizes = ["small", "medium", "large"];
    const idx = sizes.indexOf(p.size);
    p.size = sizes[(idx + 1) % sizes.length];
    p.el.dataset.size = p.size;
    p.el.className = p.el.className.replace(/size-\w+/g, `size-${p.size}`);
  },

  getLayout() {
    const panels = [];
    const canvas = el("canvas");
    const children = canvas.querySelectorAll(".panel");
    children.forEach((panel) => {
      panels.push({
        module: panel.dataset.module,
        size: panel.dataset.size || "medium",
        minimized: panel.classList.contains("minimized"),
        camDevice: panel.dataset.camDevice || "",
      });
    });
    return { panels };
  },

  applyLayout(layout) {
    this.closeAll();
    if (!layout || !layout.panels) return;
    layout.panels.forEach((p) => {
      const id = this.open(p.module, p.size || "medium");
      if (p.minimized) this.toggleMinimize(id);
      // Restore camera device selection
      if (p.camDevice && p.module === "camera") {
        const panel = document.getElementById(id);
        if (panel) panel.dataset.camDevice = p.camDevice;
        setTimeout(() => {
          const sel = document.getElementById(id + "-cam-device");
          if (sel) { sel.value = p.camDevice; sel.dispatchEvent(new Event("change")); }
        }, 200);
      }
    });
    _renderSidebar();
  },

  // Health indicator updates
  updateStatus(module, connected, isFault) {
    // Sidebar dot
    const dot = el(`mod-status-${module}`);
    if (dot) {
      dot.className = "module-status " + (connected ? (isFault ? "fault" : "online") : "offline");
    }
    // Panel status text
    this._panels.forEach((p, id) => {
      if (p.module === module) {
        const st = el(`${id}-status`);
        if (st) {
          st.textContent = connected ? (isFault ? I18n.t("status.fault") : I18n.t("status.online")) : I18n.t("status.offline");
          st.style.color = connected ? (isFault ? "var(--warn)" : "var(--accent2)") : "var(--danger)";
        }
      }
    });
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// Drag Manager (pointer-based)
// ═══════════════════════════════════════════════════════════════════════════════

const DragManager = {
  _dragData: null,

  attach(panel) {
    const header = panel.querySelector(".panel-header");
    if (!header) return;

    header.addEventListener("pointerdown", (e) => {
      // Don't start drag on action buttons
      if (e.target.closest("[data-action]")) return;
      e.preventDefault();
      this._start(e, panel);
    });
  },

  _start(e, panel) {
    const canvas = el("canvas");
    const rect = panel.getBoundingClientRect();
    this._dragData = {
      panel,
      startX: e.clientX,
      startY: e.clientY,
      origLeft: rect.left,
      origTop: rect.top,
      width: rect.width,
      height: rect.height,
      placeholder: null,
      offsetX: e.clientX - rect.left,
      offsetY: e.clientY - rect.top,
    };

    panel.style.zIndex = "50";
    panel.classList.add("dragging");

    document.addEventListener("pointermove", this._onMove);
    document.addEventListener("pointerup", this._onUp);
    document.addEventListener("pointercancel", this._onUp);
  },

  _onMove(e) {
    const d = DragManager._dragData;
    if (!d) return;

    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;

    if (!d.placeholder && (Math.abs(dx) > 5 || Math.abs(dy) > 5)) {
      // Lock in: create placeholder
      d.placeholder = document.createElement("div");
      d.placeholder.className = "panel";
      d.placeholder.style.cssText = `width:${d.width}px;height:${d.height}px;border:2px dashed var(--border);background:transparent;`;
      d.placeholder.id = "drag-placeholder";
      d.panel.parentNode.insertBefore(d.placeholder, d.panel);
      d.panel.style.position = "fixed";
      d.panel.style.left = d.origLeft + "px";
      d.panel.style.top = d.origTop + "px";
      d.panel.style.width = d.width + "px";
      d.panel.style.margin = "0";
    }

    if (d.placeholder) {
      d.panel.style.left = (e.clientX - d.offsetX) + "px";
      d.panel.style.top = (e.clientY - d.offsetY) + "px";

      // Find target position
      const canvas = el("canvas");
      const children = [...canvas.querySelectorAll(".panel:not(.dragging):not(#drag-placeholder)")];
      let targetBefore = null;
      for (const child of children) {
        const cr = child.getBoundingClientRect();
        if (e.clientY < cr.top + cr.height / 2) {
          targetBefore = child;
          break;
        }
      }
      if (targetBefore) {
        canvas.insertBefore(d.placeholder, targetBefore);
      } else if (children.length > 0) {
        canvas.insertBefore(d.placeholder, children[children.length - 1].nextSibling);
      }
    }
  },

  _onUp(e) {
    document.removeEventListener("pointermove", DragManager._onMove);
    document.removeEventListener("pointerup", DragManager._onUp);
    document.removeEventListener("pointercancel", DragManager._onUp);

    const d = DragManager._dragData;
    if (!d) return;

    d.panel.classList.remove("dragging");
    d.panel.style.position = "";
    d.panel.style.left = "";
    d.panel.style.top = "";
    d.panel.style.width = "";
    d.panel.style.margin = "";
    d.panel.style.zIndex = "";

    if (d.placeholder) {
      d.placeholder.parentNode.insertBefore(d.panel, d.placeholder);
      d.placeholder.remove();
    }

    DragManager._dragData = null;
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// Layout Manager (localStorage)
// ═══════════════════════════════════════════════════════════════════════════════

const LS_KEY = "robot_control_layouts";

const LayoutManager = {
  _layouts: {},

  init() {
    try {
      this._layouts = JSON.parse(localStorage.getItem(LS_KEY)) || {};
    } catch (e) {
      this._layouts = {};
    }
    this.populateSelector();
  },

  save(name) {
    if (!name) { alert(I18n.t("layout.prompt_name")); return; }
    this._layouts[name] = {
      ...PanelManager.getLayout(),
      savedAt: new Date().toISOString(),
    };
    this._persist();
    this.populateSelector();
  },

  load(name) {
    const layout = this._layouts[name];
    if (!layout) return;
    PanelManager.applyLayout(layout);
    // Update selector
    const sel = el("layout-selector");
    if (sel) sel.value = name;
  },

  delete(name) {
    if (!confirm(I18n.t("layout.confirm_delete", [name]))) return;
    delete this._layouts[name];
    this._persist();
    this.populateSelector();
  },

  getList() {
    return Object.keys(this._layouts);
  },

  populateSelector() {
    const sel = el("layout-selector");
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = `<option value="">${I18n.t("header.select_layout")}</option>`;
    Object.keys(this._layouts).sort().forEach((name) => {
      const opt = document.createElement("option");
      opt.value = name;
      opt.textContent = name;
      sel.appendChild(opt);
    });
    sel.value = current;
  },

  _persist() {
    try {
      localStorage.setItem(LS_KEY, JSON.stringify(this._layouts));
    } catch (e) {
      console.warn("LayoutManager: localStorage full", e);
    }
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// Key Binding Manager
// ═══════════════════════════════════════════════════════════════════════════════

function _getActivePanelId(module) {
  for (const [id, p] of PanelManager._panels) {
    if (p.module === module) return id;
  }
  return null;
}

const KeyBindingManager = {
  _LS_KEY: "robot_key_bindings",
  _bindings: {},
  _heldKeys: new Set(),
  _holdTimers: {},
  _listeningFor: null,
  _onBoundCb: null,

  _defaults: {
    // UGV — primary locomotion (matches panel WASD hint)
    "w":          "ugv.forward",
    "a":          "ugv.left",
    "s":          "ugv.backward",
    "d":          "ugv.right",
    " ":          "ugv.stop",
    "r":          "ugv.enable",
    // Motor — secondary (arrow keys)
    "arrowup":    "motor.forward",
    "arrowdown":  "motor.backward",
    "arrowleft":  "motor.left",
    "arrowright": "motor.right",
    // Lift
    "t":          "lift.move",
    "y":          "lift.stop",
    "u":          "lift.home",
    "i":          "lift.reset",
    // Hand
    "1":          "hand.open",
    "2":          "hand.close",
    "3":          "hand.pinch",
    // "4": formerly hand.send — removed, slider changes emit directly
    // Servo
    "5":          "servo.spread",
    "6":          "servo.shrink",
    "7":          "servo.stop",
    // Joint (waist)
    "8":          "joint.shrink",
    "9":          "joint.spread",
    "0":          "joint.stop",
  },

  _actions: {
    "motor.forward":   { type:"hold", module:"motor", descKey:"motor.forward",
                         startFn: ()=>socket.emit("motor_forward"),
                         stopFn:  ()=>socket.emit("motor_stop"), interval:50 },
    "motor.backward":  { type:"hold", module:"motor", descKey:"motor.backward",
                         startFn: ()=>socket.emit("motor_backward"),
                         stopFn:  ()=>socket.emit("motor_stop"), interval:50 },
    "motor.left":      { type:"hold", module:"motor", descKey:"motor.left",
                         startFn: ()=>socket.emit("motor_left"),
                         stopFn:  ()=>socket.emit("motor_stop"), interval:50 },
    "motor.right":     { type:"hold", module:"motor", descKey:"motor.right",
                         startFn: ()=>socket.emit("motor_right"),
                         stopFn:  ()=>socket.emit("motor_stop"), interval:50 },
    "motor.stop":      { type:"click", module:"motor", descKey:"motor.stop",
                         fn: ()=>socket.emit("motor_stop") },

    "ugv.forward":     { type:"hold", module:"ugv", descKey:"motor.forward",
                         startFn: ()=>{ const p=_getActivePanelId("ugv"); if(!p)return; const slL=el(p+"-sl-linear"),slA=el(p+"-sl-angular"); socket.emit("ugv_velocity",{linear:1.0*parseFloat(slL?.value||0.5),angular:0}); },
                         stopFn:  ()=>socket.emit("ugv_stop"), interval:50 },
    "ugv.backward":    { type:"hold", module:"ugv", descKey:"motor.backward",
                         startFn: ()=>{ const p=_getActivePanelId("ugv"); if(!p)return; const slL=el(p+"-sl-linear"); socket.emit("ugv_velocity",{linear:-1.0*parseFloat(slL?.value||0.5),angular:0}); },
                         stopFn:  ()=>socket.emit("ugv_stop"), interval:50 },
    "ugv.left":        { type:"hold", module:"ugv", descKey:"motor.left",
                         startFn: ()=>{ const p=_getActivePanelId("ugv"); if(!p)return; const slA=el(p+"-sl-angular"); socket.emit("ugv_velocity",{linear:0,angular:1.0*parseFloat(slA?.value||0.5)}); },
                         stopFn:  ()=>socket.emit("ugv_stop"), interval:50 },
    "ugv.right":       { type:"hold", module:"ugv", descKey:"motor.right",
                         startFn: ()=>{ const p=_getActivePanelId("ugv"); if(!p)return; const slA=el(p+"-sl-angular"); socket.emit("ugv_velocity",{linear:0,angular:-1.0*parseFloat(slA?.value||0.5)}); },
                         stopFn:  ()=>socket.emit("ugv_stop"), interval:50 },
    "ugv.stop":        { type:"click", module:"ugv", descKey:"motor.stop",
                         fn: ()=>socket.emit("ugv_stop") },
    "ugv.enable":      { type:"click", module:"ugv", descKey:"ugv.enable_can",
                         fn: ()=>{ socket.emit("ugv_enable"); } },
    "ugv.reset":       { type:"click", module:"ugv", descKey:"ugv.clear_fault",
                         fn: ()=>socket.emit("ugv_reset") },
    "ugv.restart_can": { type:"click", module:"ugv", descKey:"ugv.restart_can",
                         fn: ()=>{ socket.emit("restart_can"); } },

    "lift.move":       { type:"click", module:"lift", descKey:"lift.move",
                         fn: ()=>{ const p=_getActivePanelId("lift"); if(!p)return; socket.emit("lift_move",{height_mm:parseFloat(el(p+"-lift-target")?.value||0),speed_mm_s:parseFloat(el(p+"-lift-speed")?.value||10)}); } },
    "lift.stop":       { type:"click", module:"lift", descKey:"lift.stop",
                         fn: ()=>socket.emit("lift_stop") },
    "lift.home":       { type:"click", module:"lift", descKey:"lift.home",
                         fn: ()=>{ const p=_getActivePanelId("lift"); if(!p)return; socket.emit("lift_move",{height_mm:0.0,speed_mm_s:parseFloat(el(p+"-lift-speed")?.value||10)}); } },
    "lift.reset":      { type:"click", module:"lift", descKey:"lift.reset_fault",
                         fn: ()=>socket.emit("lift_reset_fault") },

    "arm.enable":      { type:"click", module:"arm", descKey:"arm.enable",
                         fn: ()=>socket.emit("arm_enable") },
    "arm.disable":     { type:"click", module:"arm", descKey:"arm.disable",
                         fn: ()=>socket.emit("arm_disable") },
    "arm.home":        { type:"click", module:"arm", descKey:"arm.home",
                         fn: ()=>socket.emit("arm_home") },
    "arm.stop":        { type:"click", module:"arm", descKey:"arm.stop",
                         fn: ()=>socket.emit("arm_stop") },
    "arm.reset":       { type:"click", module:"arm", descKey:"arm.reset",
                         fn: ()=>socket.emit("arm_reset_fault") },

    "hand.open":       { type:"click", module:"hand", descKey:"hand.open",
                         fn: ()=>socket.emit("hand_open") },
    "hand.close":      { type:"click", module:"hand", descKey:"hand.close",
                         fn: ()=>socket.emit("hand_close") },
    "hand.pinch":      { type:"click", module:"hand", descKey:"hand.pinch",
                         fn: ()=>socket.emit("hand_pinch") },
    "servo.spread":    { type:"click", module:"servo", descKey:"servo.spread",
                         fn: ()=>{ const p=_getActivePanelId("servo"); if(!p)return; const slH=el(p+"-servo-head-angle"),slT=el(p+"-servo-tail-angle"),slS=el(p+"-sl-servo-speed"); socket.emit("servo_spread",{head_angle:parseFloat(slH?.value||90),tail_angle:parseFloat(slT?.value||-90),mean_dps:parseFloat(slS?.value||200)}); } },
    "servo.shrink":    { type:"click", module:"servo", descKey:"servo.shrink",
                         fn: ()=>{ const p=_getActivePanelId("servo"); if(!p)return; const slS=el(p+"-sl-servo-speed"); socket.emit("servo_shrink",{head_angle:0,tail_angle:0,mean_dps:parseFloat(slS?.value||200)}); } },
    "servo.stop":      { type:"click", module:"servo", descKey:"servo.stop",
                         fn: ()=>socket.emit("servo_stop",{}) },

    "joint.spread":    { type:"hold", module:"joint", descKey:"joint.waist_spread",
                         startFn: ()=>socket.emit("joint_waist_spread", {}),
                         stopFn:  ()=>socket.emit("joint_waist_stop"), interval:80 },
    "joint.shrink":    { type:"hold", module:"joint", descKey:"joint.waist_shrink",
                         startFn: ()=>socket.emit("joint_waist_shrink", {}),
                         stopFn:  ()=>socket.emit("joint_waist_stop"), interval:80 },
    "joint.stop":      { type:"click", module:"joint", descKey:"joint.stop",
                         fn: ()=>socket.emit("joint_stop") },

    "drive.forward":   { type:"hold", module:"drive", descKey:"drive.forward",
                         startFn: ()=>socket.emit("drive_forward"),
                         stopFn:  ()=>socket.emit("drive_stop"), interval:50 },
    "drive.backward":  { type:"hold", module:"drive", descKey:"drive.backward",
                         startFn: ()=>socket.emit("drive_backward"),
                         stopFn:  ()=>socket.emit("drive_stop"), interval:50 },
    "drive.stop":      { type:"click", module:"drive", descKey:"drive.stop",
                         fn: ()=>socket.emit("drive_stop") },

    "global.estop":    { type:"click", module:"global", descKey:"header.estop",
                         fn: ()=>{ const btn=el("btn-estop"); if(btn) btn.click(); } },
    "global.reboot":   { type:"click", module:"global", descKey:"header.reboot",
                         fn: ()=>_showPowerConfirm("reboot") },
    "global.shutdown": { type:"click", module:"global", descKey:"header.shutdown",
                         fn: ()=>_showPowerConfirm("shutdown") },
  },

  init() {
    this.load();
    document.addEventListener("keydown", this._onKeyDown.bind(this));
    document.addEventListener("keyup", this._onKeyUp.bind(this));
    window.addEventListener("blur", () => { this._heldKeys.forEach(k => this._stopHold(k)); this._heldKeys.clear(); });
  },

  load() {
    const saved = localStorage.getItem(this._LS_KEY);
    let overrides = {};
    if (saved) { try { overrides = JSON.parse(saved); } catch(e) {} }
    this._bindings = Object.assign({}, this._defaults, overrides);
  },

  save() {
    try { localStorage.setItem(this._LS_KEY, JSON.stringify(this._bindings)); } catch(e) {}
  },

  resetToDefaults() {
    this._bindings = Object.assign({}, this._defaults);
    this.save();
  },

  setBinding(key, actionId) {
    if (!key || !actionId) return;
    // Remove any existing binding for this key
    for (const k of Object.keys(this._bindings)) {
      if (this._bindings[k] === actionId) delete this._bindings[k];
    }
    this._bindings[key] = actionId;
    this.save();
  },

  clearBinding(actionId) {
    for (const k of Object.keys(this._bindings)) {
      if (this._bindings[k] === actionId) { delete this._bindings[k]; break; }
    }
    this.save();
  },

  getKeyForAction(actionId) {
    for (const [k, a] of Object.entries(this._bindings)) {
      if (a === actionId) return k;
    }
    return null;
  },

  _canonicalKey(e) {
    const k = e.key;
    if (k.startsWith("Arrow")) return k.toLowerCase();
    if (k === " ") return " ";
    if (k.length === 1) return k.toLowerCase();
    return k.toLowerCase();
  },

  beginRebind(actionId, cb) {
    this._listeningFor = actionId;
    this._onBoundCb = cb;
  },

  cancelRebind() {
    this._listeningFor = null;
    this._onBoundCb = null;
  },

  renderBindingsTable(tbody) {
    if (!tbody) return;
    const shown = new Set();
    tbody.innerHTML = "";
    ["motor","ugv","lift","arm","hand","servo","joint","drive","global"].forEach(mod => {
      if (mod !== "global" && !ACTIVE_MODULES.includes(mod)) return;
      for (const [actionId, action] of Object.entries(this._actions)) {
        if (action.module !== mod) continue;
        const boundKey = this.getKeyForAction(actionId);
        const displayKey = boundKey !== null ? boundKey : "—";
        const emptyClass = boundKey === null ? " empty" : "";
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${I18n.t(action.descKey)}</td>
          <td>${mod === "global" ? "—" : I18n.t("module." + mod)}</td>
          <td><span class="bind-key-display${emptyClass}" id="bind-key-${actionId}">${displayKey === " " ? "␣" : displayKey}</span></td>
          <td>
            <button class="btn-bind" id="btn-bind-${actionId}" data-action="${actionId}" data-i18n="settings.bind_key">${I18n.t("settings.bind_key")}</button>
            <button class="btn-bind-clear" id="btn-clear-${actionId}" data-action="${actionId}" data-i18n-title="settings.clear_binding" title="${I18n.t("settings.clear_binding")}">✕</button>
          </td>`;
        tbody.appendChild(tr);
        shown.add(actionId);
      }
    });
    // Attach event listeners
    const self = this;
    tbody.querySelectorAll(".btn-bind").forEach(btn => {
      btn.addEventListener("click", () => {
        const actionId = btn.dataset.action;
        btn.textContent = I18n.t("settings.press_key");
        btn.classList.add("listening");
        self.beginRebind(actionId, (result) => {
          btn.classList.remove("listening");
          if (result.status === "cancelled") {
            btn.textContent = I18n.t("settings.bind_key");
          } else {
            // Rebuild table to show new binding
            const tbodyEl = el("bind-table-body");
            if (tbodyEl) self.renderBindingsTable(tbodyEl);
          }
        });
      });
    });
    tbody.querySelectorAll(".btn-bind-clear").forEach(btn => {
      btn.addEventListener("click", () => {
        self.clearBinding(btn.dataset.action);
        const tbodyEl = el("bind-table-body");
        if (tbodyEl) self.renderBindingsTable(tbodyEl);
      });
    });
  },

  _onKeyDown(e) {
    if (e.repeat) return;
    // Skip when a modal overlay is visible (power confirm / settings)
    if (el("power-overlay")?.style.display === "flex") return;
    if (el("settings-overlay")?.style.display === "flex") return;
    const tag = document.activeElement?.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

    const key = this._canonicalKey(e);

    // If in rebind mode, capture this key
    if (this._listeningFor !== null) {
      e.preventDefault();
      if (key === "escape") {
        if (this._onBoundCb) this._onBoundCb({status:"cancelled"});
      } else {
        const actionId = this._listeningFor;
        this.setBinding(key, actionId);
        if (this._onBoundCb) this._onBoundCb({status:"done", key, actionId});
      }
      this._listeningFor = null;
      this._onBoundCb = null;
      return;
    }

    const actionId = this._bindings[key];
    if (!actionId) return;
    const action = this._actions[actionId];
    if (!action) return;
    e.preventDefault();

    if (action.type === "hold") {
      if (this._heldKeys.has(key)) return;
      this._heldKeys.add(key);
      action.startFn();
      this._holdTimers[key] = setInterval(() => action.startFn(), action.interval || 50);
    } else {
      action.fn();
    }
  },

  _onKeyUp(e) {
    const key = this._canonicalKey(e);
    if (this._heldKeys.has(key)) {
      this._stopHold(key);
    }
  },

  _stopHold(key) {
    this._heldKeys.delete(key);
    const timer = this._holdTimers[key];
    if (timer) { clearInterval(timer); delete this._holdTimers[key]; }
    const actionId = this._bindings[key];
    if (actionId) {
      const action = this._actions[actionId];
      if (action && action.stopFn) action.stopFn();
    }
  },
};

// ═══════════════════════════════════════════════════════════════════════════════
// Panel Content Renderers
// ═══════════════════════════════════════════════════════════════════════════════

function _renderPanelContent(panelId, module) {
  const body = el(panelId + "-body");
  if (!body) return;
  body.innerHTML = `<div style="color:var(--muted);text-align:center;padding:20px">${I18n.t("misc.loading")}</div>`;

  switch (module) {
    case "camera": _renderCameraPanel(panelId, body); break;
    case "ugv":    _renderUgvPanel(panelId, body); break;
    case "lift":   _renderLiftPanel(panelId, body); break;
    case "arm":    _renderArmPanel(panelId, body); break;
    case "hand":   _renderHandPanel(panelId, body); break;
    case "motor":  _renderMotorPanel(panelId, body); break;
    case "servo":  _renderServoPanel(panelId, body); break;
    case "joint":  _renderJointPanel(panelId, body); break;
    case "drive":  _renderDrivePanel(panelId, body); break;
    case "demo":   _renderDemoPanel(panelId, body); break;
    case "teleop": _renderTeleopPanel(panelId, body); break;
    case "gimbal": _renderGimbalPanel(panelId, body); break;
    default:       body.innerHTML = `<div style="color:var(--muted);padding:20px;text-align:center">${I18n.t("misc.no_ui", [module])}</div>`; break;
  }
}

function _cleanupPanel(panelId, module) {
  // Panel cleanup — KeyBindingManager handles keyboard safety via window blur
}

// ── Camera Panel ──────────────────────────────────────────────────────────────

function _renderCameraPanel(panelId, body) {
  const panel = document.getElementById(panelId);
  body.innerHTML = `
    <div class="cam-device-row">
      <label class="cam-device-label">${I18n.t("camera.select_device")}</label>
      <select id="${panelId}-cam-device" class="cam-device-select">
        <option value="">-- ${I18n.t("camera.select_device")} --</option>
      </select>
    </div>
    <div class="camera-wrap" id="${panelId}-cam-wrap">
      <img id="${panelId}-cam-img" src="" alt="camera" style="display:none" />
      <div class="camera-error" id="${panelId}-cam-err" style="display:none">${I18n.t("camera.offline")}</div>
    </div>
    <div class="cam-status-line" id="${panelId}-cam-status">${I18n.t("camera.status")}</div>
    <div class="btn-row center" style="margin-top:6px">
      <button class="btn-secondary" id="${panelId}-cam-reset">${I18n.t("camera.reset")}</button>
    </div>
  `;

  const deviceSel = el(panelId + "-cam-device");
  const img = el(panelId + "-cam-img");
  const err = el(panelId + "-cam-err");

  // Restore device from dataset if set during layout restore
  if (panel && panel.dataset.camDevice) {
    deviceSel.value = panel.dataset.camDevice;
  }

  // Device selection → update img src
  if (deviceSel) {
    deviceSel.addEventListener("change", () => {
      const dev = deviceSel.value;
      if (panel) panel.dataset.camDevice = dev;
      if (dev) {
        // Clear first to force browser to close old MJPEG connection
        img.src = "";
        setTimeout(() => {
          img.src = "/stream/" + dev + "?token=" + (window.STREAM_TOKEN || "") + "&_t=" + Date.now();
        }, 50);
        img.style.display = "block";
        if (err) err.style.display = "none";
      } else {
        img.src = "";
        img.style.display = "none";
      }
    });
  }

  if (img) {
    img.addEventListener("error", () => { img.style.display = "none"; if (err) err.style.display = "flex"; });
    img.addEventListener("load", () => { img.style.display = "block"; if (err) err.style.display = "none"; });
  }

  // Camera reset button (per-device)
  const btnReset = el(panelId + "-cam-reset");
  if (btnReset) {
    btnReset.addEventListener("click", () => {
      btnReset.disabled = true;
      btnReset.textContent = I18n.t("camera.resetting");
      const dev = (panel && panel.dataset.camDevice) || "";
      socket.emit("camera_reset", { device: dev });
    });
  }

  // Populate from embedded page data first (works for all roles, no SocketIO)
  if (window.CAMERA_DEVICES && window.CAMERA_DEVICES.length > 0) {
    _populateCameraDropdowns(window.CAMERA_DEVICES);
  }
  // Also request via SocketIO for live updates
  socket.emit("camera_list_devices");
}

// ── UGV Panel ─────────────────────────────────────────────────────────────────

function _renderUgvPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("ugv.connection")}<b id="${panelId}-ugv-connected">—</b></span>
      <span>${t("ugv.battery")}<b id="${panelId}-ugv-battery">—</b> V</span>
      <span>${t("ugv.soc")}<b id="${panelId}-ugv-soc">—</b>%</span>
      <span>${t("ugv.linear_vel")}<b id="${panelId}-ugv-linear">—</b></span>
      <span>${t("ugv.angular_vel")}<b id="${panelId}-ugv-angular">—</b></span>
    </div>
    <div class="btn-row">
      <button class="btn-primary ctrl"    id="${panelId}-ugv-enable" data-bind-action="ugv.enable">${t("ugv.enable_can")}</button>
      <button class="btn-warn ctrl"       id="${panelId}-ugv-reset" data-bind-action="ugv.reset">${t("ugv.clear_fault")}</button>
      <button class="btn-secondary"       id="${panelId}-restart-can" data-bind-action="ugv.restart_can">${t("ugv.restart_can")}</button>
    </div>
    <div id="${panelId}-ugv-disabled-msg" style="text-align:center;padding:20px;color:var(--warn);font-size:14px">
      ${t("ugv.standby_warn")}
    </div>
    <div id="${panelId}-ugv-controls" style="display:none">
      <div class="dpad">
        <button class="dpad-btn ctrl" data-l="1.0"  data-a="0.0"  data-bind-action="ugv.forward"  data-bind-type="hold">▲</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="1.0"  data-bind-action="ugv.left"     data-bind-type="hold">◄</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="0.0"  data-bind-action="ugv.stop">■</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="-1.0" data-bind-action="ugv.right"    data-bind-type="hold">►</button>
        <button class="dpad-btn ctrl" data-l="-1.0" data-a="0.0"  data-bind-action="ugv.backward" data-bind-type="hold">▼</button>
      </div>
      <div style="text-align:center;color:var(--muted);font-size:11px;margin:8px 0">
        ${t("ugv.wasd_hint")}
      </div>
      <div class="slider-row">
        <label>${t("ugv.linear")} <span id="${panelId}-lbl-linear">0.5</span> m/s
          <input type="range" id="${panelId}-sl-linear" min="0.1" max="1.5" step="0.1" value="0.5" />
        </label>
        <label>${t("ugv.angular")} <span id="${panelId}-lbl-angular">0.5</span> rad/s
          <input type="range" id="${panelId}-sl-angular" min="0.1" max="1.0" step="0.1" value="0.5" />
        </label>
      </div>
    </div>
    <div class="panel-diag">
      <span>${t("ugv.vehicle_state")}<b id="${panelId}-ugv-vehicle-state">—</b></span>
      <span>${t("ugv.control_mode")}<b id="${panelId}-ugv-control-mode">—</b></span>
      <button id="${panelId}-btn-toggle-ugv-log" class="btn-sm">${t("ugv.log_toggle")}</button>
    </div>
    <div class="panel-log" id="${panelId}-ugv-log-panel"></div>
  `;

  // D-pad
  const slLinear  = el(panelId + "-sl-linear");
  const slAngular = el(panelId + "-sl-angular");
  if (slLinear) {
    slLinear.addEventListener("input", () => {
      const lbl = el(panelId + "-lbl-linear"); if (lbl) lbl.textContent = slLinear.value;
    });
  }
  if (slAngular) {
    slAngular.addEventListener("input", () => {
      const lbl = el(panelId + "-lbl-angular"); if (lbl) lbl.textContent = slAngular.value;
    });
  }

  body.querySelectorAll(".dpad-btn").forEach((btn) => {
    let interval = null;
    const start = (e) => {
      btn.setPointerCapture(e.pointerId);
      const send = () => {
        const l = parseFloat(btn.dataset.l) * parseFloat(slLinear?.value || 0.5);
        const a = parseFloat(btn.dataset.a) * parseFloat(slAngular?.value || 0.5);
        socket.emit("ugv_velocity", { linear: l, angular: a });
      };
      send();
      interval = setInterval(send, 50);
    };
    const stop = () => { if (interval) { clearInterval(interval); interval = null; } socket.emit("ugv_stop"); };
    btn.addEventListener("pointerdown", start);
    btn.addEventListener("pointerup", stop);
    btn.addEventListener("pointercancel", stop);
  });

  const btnEn  = el(panelId + "-ugv-enable");
  const btnRst = el(panelId + "-ugv-reset");
  if (btnEn)  btnEn.addEventListener("click",  () => { socket.emit("ugv_enable"); });
  if (btnRst) btnRst.addEventListener("click", () => socket.emit("ugv_reset"));

  const btnCan = el(panelId + "-restart-can");
  if (btnCan) {
    btnCan.addEventListener("click", () => {
      btnCan.disabled = true; btnCan.textContent = I18n.t("ugv.restarting");
      socket.emit("restart_can");
    });
  }

  // Log toggle
  const btnLog = el(panelId + "-btn-toggle-ugv-log");
  const logPanel = el(panelId + "-ugv-log-panel");
  if (btnLog && logPanel) {
    btnLog.addEventListener("click", () => _togglePanelLog(logPanel, btnLog, I18n.t("ugv.log_label")));
  }

  // store refs for state update
  body.dataset.panelId = panelId;
}

// ── Lift Panel ────────────────────────────────────────────────────────────────
function _renderLiftPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("lift.height")}<b id="${panelId}-lift-height">—</b> mm</span>
      <span>${t("lift.velocity")}<b id="${panelId}-lift-vel">—</b> mm/s</span>
      <span>${t("lift.torque")}<b id="${panelId}-lift-torque">—</b> ‰</span>
      <span id="${panelId}-lift-fault-badge" class="badge fault" style="display:none">FAULT</span>
    </div>
    <div class="input-row">
      <label>${t("lift.target_mm")}
        <input type="number" id="${panelId}-lift-target" min="-9999" max="9999" step="10" value="200" />
      </label>
      <label>${t("lift.speed_mm_s")}
        <input type="number" id="${panelId}-lift-speed" min="1" max="50" step="1" value="10" />
      </label>
    </div>
    <div class="btn-row">
      <button class="btn-primary ctrl"   id="${panelId}-lift-move" data-bind-action="lift.move">${t("lift.move")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-lift-stop" data-bind-action="lift.stop">${t("lift.stop")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-lift-home" data-bind-action="lift.home">${t("lift.home")}</button>
      <button class="btn-warn ctrl"      id="${panelId}-lift-reset" data-bind-action="lift.reset">${t("lift.reset_fault")}</button>
    </div>
    <div class="btn-row" id="${panelId}-lift-presets"></div>
    <div class="panel-diag">
      <span>${t("lift.drive")}<b id="${panelId}-lift-drive-state">—</b></span>
      <span>${t("lift.sw")}<b id="${panelId}-lift-sw">—</b></span>
      <button id="${panelId}-btn-toggle-lift-log" class="btn-sm">${t("lift.log_toggle")}</button>
    </div>
    <div class="panel-log" id="${panelId}-lift-log-panel"></div>
  `;

  // Presets
  _rebuildPresetsFor(panelId, 0, 500);

  const btnMove  = el(panelId + "-lift-move");
  const btnStop  = el(panelId + "-lift-stop");
  const btnHome  = el(panelId + "-lift-home");
  const btnReset = el(panelId + "-lift-reset");
  if (btnMove) btnMove.addEventListener("click", () => {
    socket.emit("lift_move", {
      height_mm: parseFloat(el(panelId + "-lift-target")?.value || 0),
      speed_mm_s: parseFloat(el(panelId + "-lift-speed")?.value || 10),
    });
  });
  if (btnStop)  btnStop.addEventListener("click",  () => socket.emit("lift_stop"));
  if (btnHome)  btnHome.addEventListener("click",  () => socket.emit("lift_move", {
    height_mm: 0.0, speed_mm_s: parseFloat(el(panelId + "-lift-speed")?.value || 10),
  }));
  if (btnReset) btnReset.addEventListener("click", () => socket.emit("lift_reset_fault"));

  const btnLog = el(panelId + "-btn-toggle-lift-log");
  const logPanel = el(panelId + "-lift-log-panel");
  if (btnLog && logPanel) btnLog.addEventListener("click", () => _togglePanelLog(logPanel, btnLog, I18n.t("lift.log_label")));
}

function _rebuildPresetsFor(panelId, minMm, maxMm) {
  const container = el(panelId + "-lift-presets");
  if (!container) return;
  container.innerHTML = "";
  const range = maxMm - minMm;
  [0, 0.25, 0.5, 0.75, 1.0].forEach((f) => {
    const h = Math.round(minMm + range * f);
    const btn = document.createElement("button");
    btn.className = "btn-preset" + (ROLE === "viewer" ? " ctrl" : "");
    btn.textContent = `${h} mm`;
    if (ROLE !== "viewer") {
      btn.addEventListener("click", () => {
        const tgt = el(panelId + "-lift-target");
        const spd = el(panelId + "-lift-speed");
        if (tgt) tgt.value = h;
        socket.emit("lift_move", { height_mm: h, speed_mm_s: spd ? parseFloat(spd.value) : 10 });
      });
    } else { btn.disabled = true; }
    container.appendChild(btn);
  });
}

// ── Arm Panel ─────────────────────────────────────────────────────────────────
function _renderArmPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("arm.connection")}<b id="${panelId}-arm-connected">—</b></span>
      <span>${t("arm.enabled")}<b id="${panelId}-arm-enabled">—</b></span>
      <span>${t("arm.moving")}<b id="${panelId}-arm-moving">—</b></span>
      <span>${t("arm.mode")}<b id="${panelId}-arm-robot-mode">—</b></span>
      <span>${t("arm.safety")}<b id="${panelId}-arm-safety-mode">—</b></span>
      <span id="${panelId}-arm-fault-badge" class="badge fault" style="display:none">FAULT</span>
    </div>
    <div class="slider-row">
      <label>${t("arm.speed_pct")} <span id="${panelId}-lbl-arm-speed">20</span>%
        <input type="range" id="${panelId}-sl-arm-speed" min="1" max="100" step="1" value="20" />
      </label>
      <label>${t("arm.collision")} <span id="${panelId}-lbl-arm-cs">0.30</span>
        <input type="range" id="${panelId}-sl-arm-cs" min="0.05" max="1.0" step="0.05" value="0.30" />
      </label>
    </div>
    <div id="${panelId}-arm-collision-warn" class="collision-warn" style="display:none"></div>
    <div class="joint-grid" id="${panelId}-arm-joints"></div>
    <div class="btn-row">
      <button class="btn-primary ctrl"   id="${panelId}-arm-enable" data-bind-action="arm.enable">${t("arm.enable")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-arm-disable" data-bind-action="arm.disable">${t("arm.disable")}</button>
      <button class="btn-primary ctrl"   id="${panelId}-arm-home" data-bind-action="arm.home">${t("arm.home")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-arm-stop" data-bind-action="arm.stop">${t("arm.stop")}</button>
      <button class="btn-warn ctrl"      id="${panelId}-arm-reset" data-bind-action="arm.reset">${t("arm.reset")}</button>
    </div>
    <div class="panel-diag">
      <button id="${panelId}-btn-toggle-arm-log" class="btn-sm">${t("arm.log_toggle")}</button>
    </div>
    <div class="panel-log" id="${panelId}-arm-log-panel"></div>
  `;

  const ARM_JOINTS = ["J1", "J2", "J3", "J4", "J5", "J6"];
  const armGrid = el(panelId + "-arm-joints");
  const slSpeed = el(panelId + "-sl-arm-speed");

  const _sendCurrentAngles = () => {
    const angles = ARM_JOINTS.map((_, i) => {
      const inp = el(panelId + `-arm-j${i}`); return inp ? parseFloat(inp.value) : 0;
    });
    socket.emit("arm_move_joints", { angles, speed_pct: parseInt(slSpeed?.value || 20) });
  };

  if (armGrid) {
    ARM_JOINTS.forEach((name, i) => {
      const row = document.createElement("div");
      row.className = "joint-row arm-joint-row";
      row.innerHTML = `
        <label>${name}</label>
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px;margin-right:2px" data-j="${i}" data-d="-5">◀</button>
        <input type="range" id="${panelId}-arm-j${i}" min="-180" max="180" step="1" value="0" />
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px;margin-left:2px" data-j="${i}" data-d="5">▶</button>
        <span class="val" id="${panelId}-arm-j${i}-val">0.0°</span>
        <span class="joint-actual" id="${panelId}-arm-j${i}-act">${t("arm.actual_prefix")}—</span>
        <span class="joint-load" id="${panelId}-arm-j${i}-load">— A</span>
        <span class="joint-temp" id="${panelId}-arm-j${i}-temp">— °C</span>
      `;
      armGrid.appendChild(row);

      const slider = row.querySelector("input");
      slider.addEventListener("input", (e) => {
        const valEl = el(panelId + `-arm-j${i}-val`);
        if (valEl) valEl.textContent = parseFloat(e.target.value).toFixed(1) + "°";
      });
      slider.addEventListener("change", () => _sendCurrentAngles());

      row.querySelectorAll("button").forEach(btn => {
        let interval = null;
        btn.addEventListener("pointerdown", (e) => {
          btn.setPointerCapture(e.pointerId);
          const delta = parseFloat(btn.dataset.d);
          const update = () => {
            const sl = el(panelId + `-arm-j${btn.dataset.j}`);
            if (sl) {
              sl.value = Math.max(-180, Math.min(180, parseFloat(sl.value) + delta));
              sl.dispatchEvent(new Event("input", { bubbles: true }));
            }
          };
          update();
          interval = setInterval(update, 100);
        });
        btn.addEventListener("pointerup", () => {
          if (interval) { clearInterval(interval); interval = null; }
          _sendCurrentAngles();
        });
        btn.addEventListener("pointercancel", () => {
          if (interval) { clearInterval(interval); interval = null; }
        });
      });
    });
  }

  if (slSpeed) {
    slSpeed.addEventListener("input", () => {
      const lbl = el(panelId + "-lbl-arm-speed"); if (lbl) lbl.textContent = slSpeed.value;
    });
  }

  // Collision sensitivity slider
  const slCS = el(panelId + "-sl-arm-cs");
  if (slCS) {
    slCS.addEventListener("input", () => {
      const lbl = el(panelId + "-lbl-arm-cs"); if (lbl) lbl.textContent = parseFloat(slCS.value).toFixed(2);
    });
    slCS.addEventListener("change", () => {
      socket.emit("arm_set_collision_sensitivity", { level: parseFloat(slCS.value) });
    });
  }

  const btnHome    = el(panelId + "-arm-home");
  const btnStop    = el(panelId + "-arm-stop");
  const btnEnable  = el(panelId + "-arm-enable");
  const btnDisable = el(panelId + "-arm-disable");
  const btnReset   = el(panelId + "-arm-reset");

  if (btnHome)    btnHome.addEventListener("click",    () => socket.emit("arm_home"));
  if (btnStop)    btnStop.addEventListener("click",    () => socket.emit("arm_stop"));
  if (btnEnable)  btnEnable.addEventListener("click",  () => socket.emit("arm_enable"));
  if (btnDisable) btnDisable.addEventListener("click", () => socket.emit("arm_disable"));
  if (btnReset)   btnReset.addEventListener("click",   () => socket.emit("arm_reset_fault"));

  const btnLog = el(panelId + "-btn-toggle-arm-log");
  const logPanel = el(panelId + "-arm-log-panel");
  if (btnLog && logPanel) btnLog.addEventListener("click", () => _togglePanelLog(logPanel, btnLog, I18n.t("arm.log_label")));
}

// ── Hand Panel ────────────────────────────────────────────────────────────────
function _renderHandPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("hand.status")}<b id="${panelId}-hand-enabled">—</b></span>
      <span>${t("hand.force")}<b id="${panelId}-hand-force">—</b> N</span>
    </div>
    <div class="finger-grid" id="${panelId}-hand-fingers"></div>
    <div class="btn-row">
      <button class="btn-primary ctrl"   id="${panelId}-hand-open" data-bind-action="hand.open">${t("hand.open")}</button>
      <button class="btn-primary ctrl"   id="${panelId}-hand-close" data-bind-action="hand.close">${t("hand.close")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-hand-pinch" data-bind-action="hand.pinch">${t("hand.pinch")}</button>
    </div>
  `;

  const FINGERS = ["hand.finger.0", "hand.finger.1", "hand.finger.2", "hand.finger.3", "hand.finger.4", "hand.finger.5"];
  const grid = el(panelId + "-hand-fingers");
  if (grid) {
    FINGERS.forEach((key, i) => {
      const row = document.createElement("div");
      row.className = "finger-row";
      row.innerHTML = `
        <label>${t(key)}</label>
        <input type="range" id="${panelId}-hand-f${i}" min="0" max="1" step="0.01" value="0" />
        <span class="val" id="${panelId}-hand-f${i}-val">0%</span>
      `;
      grid.appendChild(row);
      row.querySelector("input").addEventListener("input", (e) => {
        const valEl = el(panelId + `-hand-f${i}-val`);
        if (valEl) valEl.textContent = Math.round(parseFloat(e.target.value) * 100) + "%";
        _sendHandFingers(panelId);
      });
    });
  }

  function _sendHandFingers(pid) {
    const pos = FINGERS.map((_, i) => {
      const inp = el(pid + `-hand-f${i}`); return inp ? parseFloat(inp.value) : 0;
    });
    socket.emit("hand_set_fingers", { positions: pos, speed_pct: 50 });
  }

  const btnOpen  = el(panelId + "-hand-open");
  const btnClose = el(panelId + "-hand-close");
  const btnPinch = el(panelId + "-hand-pinch");
  if (btnOpen)  btnOpen.addEventListener("click",  () => socket.emit("hand_open"));
  if (btnClose) btnClose.addEventListener("click", () => socket.emit("hand_close"));
  if (btnPinch) btnPinch.addEventListener("click", () => socket.emit("hand_pinch"));
}

// ── Motor Panel ────────────────────────────────────────────────────────────────
function _renderMotorPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("hand.status")}<b id="${panelId}-motor-connected">—</b></span>
      <span>${t("motor.direction")}<b id="${panelId}-motor-direction">${t("motor.stop")}</b></span>
    </div>
    <div class="dpad motor-dpad">
      <button class="dpad-btn ctrl" data-dir="forward" data-bind-action="motor.forward" data-bind-type="hold">▲</button>
      <button class="dpad-btn ctrl" data-dir="left" data-bind-action="motor.left" data-bind-type="hold">◄</button>
      <button class="dpad-btn ctrl" data-dir="stop" data-bind-action="motor.stop">■</button>
      <button class="dpad-btn ctrl" data-dir="right" data-bind-action="motor.right" data-bind-type="hold">►</button>
      <button class="dpad-btn ctrl" data-dir="backward" data-bind-action="motor.backward" data-bind-type="hold">▼</button>
    </div>
  `;

  const dirMap = {
    forward: "motor_forward",
    backward: "motor_backward",
    left: "motor_left",
    right: "motor_right",
    stop: "motor_stop",
  };

  body.querySelectorAll(".dpad-btn").forEach((btn) => {
    const dir = btn.dataset.dir;
    const event = dirMap[dir];
    if (!event) return;

    let interval = null;
    const start = (e) => {
      btn.setPointerCapture(e.pointerId);
      const send = () => socket.emit(event);
      send();
      interval = setInterval(send, 50);
    };
    const stop = () => {
      if (interval) { clearInterval(interval); interval = null; }
      if (dir !== "stop") socket.emit("motor_stop");
    };
    btn.addEventListener("pointerdown", start);
    btn.addEventListener("pointerup", stop);
    btn.addEventListener("pointercancel", stop);
  });
}

// ── Joint Panel (waist + pan/tilt) ──────────────────────────────────────────────

function _renderJointPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="panel-info">
      <span>${t("hand.status")}<b id="${panelId}-joint-connected">—</b></span>
      <span>${t("motor.direction")}<b id="${panelId}-joint-direction">${t("motor.stop")}</b></span>
      <span>H:<b id="${panelId}-joint-h">0°</b> P:<b id="${panelId}-joint-p">0°</b></span>
    </div>
    <div class="panel-info" style="font-size:11px">
      <span>H1:<b id="${panelId}-joint-h1">0°</b> H2:<b id="${panelId}-joint-h2">0°</b></span>
      <span>P1:<b id="${panelId}-joint-p1">0°</b> P2:<b id="${panelId}-joint-p2">0°</b></span>
    </div>
    <div class="panel-info" style="font-size:11px">
      <span>${t("joint.head20")}:<b id="${panelId}-joint-head">0°</b></span>
      <span>${t("joint.tail10")}:<b id="${panelId}-joint-tail">0°</b></span>
    </div>
    <div style="display:flex;justify-content:center;gap:24px;padding:8px;flex-wrap:wrap">
      <!-- waist -->
      <div style="text-align:center">
        <div style="font-size:12px;color:var(--muted);margin-bottom:4px">${t("joint.waist")}</div>
        <button class="dpad-btn ctrl waist-btn" data-waist="spread" style="width:50px;height:50px;margin:2px;font-size:20px">${t("joint.waist_spread")}</button>
        <button class="dpad-btn ctrl waist-btn" data-waist="shrink" style="width:50px;height:50px;margin:2px;font-size:20px">${t("joint.waist_shrink")}</button>
        <br/>
        <button class="dpad-btn ctrl" id="${panelId}-joint-waist-stop" style="width:104px;height:32px;margin:2px">${t("joint.stop")}</button>
      </div>
      <!-- pan/tilt -->
      <div style="text-align:center">
        <div style="font-size:12px;color:var(--muted);margin-bottom:4px">${t("joint.pt")}</div>
        <button class="dpad-btn ctrl pt-btn" data-pt="up" style="width:50px;height:50px;margin:2px">▲</button>
        <br/>
        <button class="dpad-btn ctrl pt-btn" data-pt="left" style="width:50px;height:50px;margin:2px">◄</button>
        <button class="dpad-btn ctrl pt-btn" data-pt="right" style="width:50px;height:50px;margin:2px">►</button>
        <br/>
        <button class="dpad-btn ctrl pt-btn" data-pt="down" style="width:50px;height:50px;margin:2px">▼</button>
        <br/>
        <button class="dpad-btn ctrl pt-btn" data-pt="home" style="width:50px;height:32px;margin:2px">${t("joint.pt_home")}</button>
        <button class="dpad-btn ctrl" id="${panelId}-joint-pt-stop" style="width:50px;height:32px;margin:2px">${t("joint.stop")}</button>
      </div>
    </div>
    <!-- limits -->
    <div class="joint-limits" style="padding:6px 8px;border-top:1px solid var(--border);display:flex;gap:8px;flex-wrap:wrap;justify-content:center;font-size:11px">
      <span><span data-i18n="joint.h_limit">H</span>:
        <input type="number" id="${panelId}-joint-hmin" value="-300" style="width:48px;height:20px;font-size:10px;text-align:center">~
        <input type="number" id="${panelId}-joint-hmax" value="300" style="width:48px;height:20px;font-size:10px;text-align:center">°
      </span>
      <span><span data-i18n="joint.p_limit">P</span>:
        <input type="number" id="${panelId}-joint-pmin" value="-300" style="width:48px;height:20px;font-size:10px;text-align:center">~
        <input type="number" id="${panelId}-joint-pmax" value="300" style="width:48px;height:20px;font-size:10px;text-align:center">°
      </span>
      <span>${t("joint.wh_limit")}:
        <input type="number" id="${panelId}-joint-whmin" value="850" style="width:48px;height:20px;font-size:10px;text-align:center">~
        <input type="number" id="${panelId}-joint-whmax" value="2100" style="width:48px;height:20px;font-size:10px;text-align:center">°
      </span>
      <span>${t("joint.wt_limit")}:
        <input type="number" id="${panelId}-joint-wtmin" value="-180" style="width:48px;height:20px;font-size:10px;text-align:center">~
        <input type="number" id="${panelId}-joint-wtmax" value="1070" style="width:48px;height:20px;font-size:10px;text-align:center">°
      </span>
      <button class="btn-secondary btn-sm" id="${panelId}-joint-apply-limits" style="height:20px;font-size:10px;padding:0 4px">${t("header.apply")}</button>
    </div>
  `;

  // Waist buttons — hold to spread/shrink, release to stop
  const waistMap = { spread: "joint_waist_spread", shrink: "joint_waist_shrink" };
  body.querySelectorAll(".waist-btn").forEach((btn) => {
    const dir = btn.dataset.waist;
    if (!waistMap[dir]) return;
    const event = waistMap[dir];
    let interval = null;
    btn.addEventListener("pointerdown", (e) => {
      btn.setPointerCapture(e.pointerId);
      socket.emit(event);
      interval = setInterval(() => socket.emit(event), 80);
    });
    btn.addEventListener("pointerup", () => {
      clearInterval(interval); interval = null;
      socket.emit("joint_waist_stop");
    });
    btn.addEventListener("pointerleave", () => {
      clearInterval(interval); interval = null;
      socket.emit("joint_waist_stop");
    });
  });
  // Pan/tilt buttons — hold to move, release to stop
  const ptMap = {
    up: "joint_pt_up", down: "joint_pt_down",
    left: "joint_pt_left", right: "joint_pt_right",
    home: "joint_pt_home",
  };
  body.querySelectorAll(".pt-btn").forEach((btn) => {
    const action = btn.dataset.pt;
    if (!ptMap[action]) return;
    const event = ptMap[action];
    if (action === "home") {
      btn.addEventListener("pointerdown", () => socket.emit(event));
      return;
    }
    let interval = null;
    btn.addEventListener("pointerdown", (e) => {
      btn.setPointerCapture(e.pointerId);
      socket.emit(event);
      interval = setInterval(() => socket.emit(event), 80);
    });
    btn.addEventListener("pointerup", () => {
      clearInterval(interval); interval = null;
      socket.emit("joint_pt_stop");
    });
    btn.addEventListener("pointerleave", () => {
      clearInterval(interval); interval = null;
      socket.emit("joint_pt_stop");
    });
  });
  // Stop buttons
  const wStopBtn = el(panelId + "-joint-waist-stop");
  if (wStopBtn) wStopBtn.addEventListener("pointerdown", () => socket.emit("joint_waist_stop"));
  const ptStopBtn = el(panelId + "-joint-pt-stop");
  if (ptStopBtn) ptStopBtn.addEventListener("pointerdown", () => socket.emit("joint_pt_stop"));
  // Limit apply button
  const applyBtn = el(panelId + "-joint-apply-limits");
  if (applyBtn) {
    applyBtn.addEventListener("click", () => {
      socket.emit("joint_set_limits", {
        h_min: parseInt(el(panelId + "-joint-hmin").value) || -300,
        h_max: parseInt(el(panelId + "-joint-hmax").value) || 300,
        p_min: parseInt(el(panelId + "-joint-pmin").value) || -300,
        p_max: parseInt(el(panelId + "-joint-pmax").value) || 300,
        wh_min: parseInt(el(panelId + "-joint-whmin").value) || 850,
        wh_max: parseInt(el(panelId + "-joint-whmax").value) || 2100,
        wt_min: parseInt(el(panelId + "-joint-wtmin").value) || -180,
        wt_max: parseInt(el(panelId + "-joint-wtmax").value) || 1070,
      });
    });
  }
  // Request current limits
  socket.emit("joint_get_limits");
}

// ── Drive Panel ─────────────────────────────────────────────────────────────────

function _renderDrivePanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="panel-info">
      <span>${t("hand.status")}<b id="${panelId}-drive-connected">—</b></span>
      <span>${t("motor.direction")}<b id="${panelId}-drive-direction">${t("motor.stop")}</b></span>
    </div>
    <div style="text-align:center;padding:8px">
      <button class="dpad-btn ctrl drive-btn" data-dir="forward" style="width:60px;height:60px;margin:2px">▲</button>
      <br/>
      <button class="dpad-btn ctrl drive-btn" data-dir="backward" style="width:60px;height:60px;margin:2px">▼</button>
      <br/>
      <button class="dpad-btn ctrl drive-btn" data-dir="stop" style="width:60px;height:60px;margin:2px">■</button>
    </div>
  `;
  const dirMap = { forward: "drive_forward", backward: "drive_backward", stop: "drive_stop" };
  body.querySelectorAll(".drive-btn").forEach((btn) => {
    btn.addEventListener("pointerdown", () => {
      const dir = btn.dataset.dir;
      if (dirMap[dir]) socket.emit(dirMap[dir]);
    });
    if (btn.dataset.dir !== "stop") {
      btn.addEventListener("pointerup", () => socket.emit("drive_stop"));
      btn.addEventListener("pointerleave", () => socket.emit("drive_stop"));
    }
  });
}

// ── Servo Panel ────────────────────────────────────────────────────────────────
function _renderServoPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row">
      <span>${t("servo.connection")}<b id="${panelId}-servo-connected">—</b></span>
      <span>${t("servo.direction")}<b id="${panelId}-servo-direction">${t("servo.stop")}</b></span>
    </div>
    <div class="slider-row">
      <label>${t("servo.speed")} <span id="${panelId}-lbl-servo-speed">200</span>
        <input type="range" id="${panelId}-sl-servo-speed" min="10" max="500" step="10" value="200" />
      </label>
      <label>${t("servo.power")} <span id="${panelId}-lbl-servo-power">4000</span>
        <input type="range" id="${panelId}-sl-servo-power" min="500" max="8000" step="100" value="4000" />
      </label>
    </div>
    <div class="joint-section">
      <div class="section-title">${t("servo.head_joint")} <span class="servo-ids" id="${panelId}-servo-head-ids"></span></div>
      <div class="joint-row servo-joint-row">
        <label>${t("servo.angle")}</label>
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px" data-servo="head" data-d="-5">◀</button>
        <input type="range" id="${panelId}-servo-head-angle" min="-135" max="135" step="1" value="90" />
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px" data-servo="head" data-d="5">▶</button>
        <span class="val" id="${panelId}-servo-head-angle-val">90°</span>
      </div>
      <div class="servo-monitor" id="${panelId}-servo-head-monitor">
        <span>${t("servo.voltage")}— V</span>
        <span>${t("servo.current")}— A</span>
        <span>${t("servo.temp")}— °C</span>
      </div>
    </div>
    <div class="joint-section">
      <div class="section-title">${t("servo.tail_joint")} <span class="servo-ids" id="${panelId}-servo-tail-ids"></span></div>
      <div class="joint-row servo-joint-row">
        <label>${t("servo.angle")}</label>
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px" data-servo="tail" data-d="-5">◀</button>
        <input type="range" id="${panelId}-servo-tail-angle" min="-135" max="135" step="1" value="-90" />
        <button class="dpad-btn" style="width:28px;height:28px;font-size:14px" data-servo="tail" data-d="5">▶</button>
        <span class="val" id="${panelId}-servo-tail-angle-val">-90°</span>
      </div>
      <div class="servo-monitor" id="${panelId}-servo-tail-monitor">
        <span>${t("servo.voltage")}— V</span>
        <span>${t("servo.current")}— A</span>
        <span>${t("servo.temp")}— °C</span>
      </div>
    </div>
    <div class="btn-row">
      <button class="btn-primary ctrl"   id="${panelId}-servo-spread" data-bind-action="servo.spread">${t("servo.spread")}</button>
      <button class="btn-primary ctrl"   id="${panelId}-servo-shrink" data-bind-action="servo.shrink">${t("servo.shrink")}</button>
      <button class="btn-warn ctrl"      id="${panelId}-servo-stop" data-bind-action="servo.stop">${t("servo.stop")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-servo-damping" data-bind-action="servo.damping">${t("servo.damping")}</button>
    </div>
  `;

  const slSpeed = el(panelId + "-sl-servo-speed");
  const slPower = el(panelId + "-sl-servo-power");
  const slHead  = el(panelId + "-servo-head-angle");
  const slTail  = el(panelId + "-servo-tail-angle");

  if (slSpeed) slSpeed.addEventListener("input", () => {
    const lbl = el(panelId + "-lbl-servo-speed"); if (lbl) lbl.textContent = slSpeed.value;
  });
  if (slPower) slPower.addEventListener("input", () => {
    const lbl = el(panelId + "-lbl-servo-power"); if (lbl) lbl.textContent = slPower.value;
  });

  const _sendHeadAngle = () => {
    const ang = parseFloat(slHead?.value || 90);
    const spd = parseFloat(slSpeed?.value || 200);
    const pwr = parseInt(slPower?.value || 4000);
    socket.emit("servo_set_angle", { servo_id: 20, angle: ang, mean_dps: spd, power: pwr, is_mturn: true });
  };
  const _sendTailAngle = () => {
    const ang = parseFloat(slTail?.value || -90);
    const spd = parseFloat(slSpeed?.value || 200);
    const pwr = parseInt(slPower?.value || 4000);
    socket.emit("servo_set_angle", { servo_id: 10, angle: ang, mean_dps: spd, power: pwr, is_mturn: true });
  };

  [slHead, slTail].forEach((sl) => {
    if (!sl) return;
    sl.addEventListener("input", (e) => {
      const id = e.target.id.includes("head") ? "head" : "tail";
      const valEl = el(panelId + `-servo-${id}-angle-val`);
      if (valEl) valEl.textContent = parseFloat(e.target.value).toFixed(0) + "°";
    });
    sl.addEventListener("change", () => {
      if (sl === slHead) _sendHeadAngle(); else _sendTailAngle();
    });
  });

  // Fine-adjust buttons
  body.querySelectorAll(".dpad-btn[data-servo]").forEach((btn) => {
    let interval = null;
    btn.addEventListener("pointerdown", (e) => {
      btn.setPointerCapture(e.pointerId);
      const delta = parseFloat(btn.dataset.d);
      const isHead = btn.dataset.servo === "head";
      const sl = isHead ? slHead : slTail;
      const update = () => {
        if (sl) {
          sl.value = Math.max(-135, Math.min(135, parseFloat(sl.value) + delta));
          sl.dispatchEvent(new Event("input", { bubbles: true }));
        }
      };
      update();
      interval = setInterval(update, 100);
    });
    btn.addEventListener("pointerup", () => {
      if (interval) { clearInterval(interval); interval = null; }
      if (btn.dataset.servo === "head") _sendHeadAngle(); else _sendTailAngle();
    });
    btn.addEventListener("pointercancel", () => {
      if (interval) { clearInterval(interval); interval = null; }
    });
  });

  // Action buttons
  const btnSpread  = el(panelId + "-servo-spread");
  const btnShrink  = el(panelId + "-servo-shrink");
  const btnStop    = el(panelId + "-servo-stop");
  const btnDamping = el(panelId + "-servo-damping");

  if (btnSpread) btnSpread.addEventListener("click", () => {
    socket.emit("servo_spread", {
      head_angle: parseFloat(slHead?.value || 90),
      tail_angle: parseFloat(slTail?.value || -90),
      mean_dps: parseFloat(slSpeed?.value || 200),
    });
  });
  if (btnShrink) btnShrink.addEventListener("click", () => {
    socket.emit("servo_shrink", {
      head_angle: 0,
      tail_angle: 0,
      mean_dps: parseFloat(slSpeed?.value || 200),
    });
  });
  if (btnStop) btnStop.addEventListener("click", () => socket.emit("servo_stop", {}));
  if (btnDamping) btnDamping.addEventListener("click", () => {
    const pwr = parseInt(slPower?.value || 500);
    socket.emit("servo_set_damping", { servo_id: 20, power: pwr });
    socket.emit("servo_set_damping", { servo_id: 10, power: pwr });
  });

  body.dataset.panelId = panelId;
}

function _updateCameraStatus(panelId, state) {
  const statusEl = el(panelId + "-cam-status");
  const resetBtn = el(panelId + "-cam-reset");
  if (!statusEl) return;
  let text = I18n.t("camera.status");
  if (state.signal === "lost") text = I18n.t("camera.status_lost");
  else if (state.signal === "stale") text = I18n.t("camera.status_stale");
  else if (state.signal === "waiting") text = I18n.t("camera.status_waiting");
  statusEl.textContent = text;
}

socket.on("camera_reset_result", (data) => {
  const resetDevice = (data && data.device) || "";
  // Find all camera panel reset buttons and re-enable matching ones
  document.querySelectorAll("[id$='-cam-reset']").forEach((btn) => {
    // Find parent panel to check device match
    const panel = btn.closest(".panel");
    const panelDevice = panel ? (panel.dataset.camDevice || "") : "";
    if (!resetDevice || panelDevice === resetDevice || !panelDevice) {
      btn.disabled = false;
      btn.textContent = I18n.t("camera.reset");
    }
  });
});

function _populateCameraDropdowns(devices) {
  PanelManager._panels.forEach((p, panelId) => {
    if (p.module !== "camera") return;
    const sel = el(panelId + "-cam-device");
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">-- ' + I18n.t("camera.select_device") + ' --</option>';
    devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.id;
      let label = d.id;
      if (d.active) label += " ✓";
      if (d.streaming) label += " ⚬";
      opt.textContent = label;
      sel.appendChild(opt);
    });
    if (currentVal && devices.some(d => d.id === currentVal)) {
      sel.value = currentVal;
    } else {
      const firstActive = devices.find(d => d.active);
      if (firstActive) {
        sel.value = firstActive.id;
        sel.dispatchEvent(new Event("change"));
      }
    }
  });
}

// Populate from embedded page data (no SocketIO needed, works for all roles)
if (window.CAMERA_DEVICES && window.CAMERA_DEVICES.length > 0) {
  _populateCameraDropdowns(window.CAMERA_DEVICES);
}

socket.on("camera_device_list", (data) => {
  _populateCameraDropdowns(data.devices || []);
});

function _updateMotorState(panelId, s) {
  _maybeSet(el(panelId + "-motor-connected"), s.connected ? I18n.t("motor.connected") : I18n.t("motor.mock"));
  const dirEl = el(panelId + "-motor-direction");
  if (dirEl) {
    const labels = { forward: I18n.t("motor.forward"), backward: I18n.t("motor.backward"), left: I18n.t("motor.left"), right: I18n.t("motor.right"), stop: I18n.t("motor.stop") };
    dirEl.textContent = labels[s.direction] || s.direction;
    dirEl.style.color = s.direction !== "stop" ? "var(--accent2)" : "var(--muted)";
  }
}

function _updateJointState(panelId, s) {
  _maybeSet(el(panelId + "-joint-connected"), s.connected ? I18n.t("motor.connected") : I18n.t("motor.mock"));
  const dirEl = el(panelId + "-joint-direction");
  if (dirEl) {
    const labels = { spread: I18n.t("joint.waist_spread"), shrink: I18n.t("joint.waist_shrink"), stop: I18n.t("joint.stop") };
    dirEl.textContent = labels[s.direction] || s.direction || I18n.t("joint.stop");
  }
  const hEl = el(panelId + "-joint-h");
  const pEl = el(panelId + "-joint-p");
  if (hEl) hEl.textContent = (s.h_angle != null ? s.h_angle.toFixed(1) : "0") + "°";
  if (pEl) pEl.textContent = (s.p_angle != null ? s.p_angle.toFixed(1) : "0") + "°";
  const fmtAngle = (v) => (v != null ? v.toFixed(1) : "0.0") + "°";
  _maybeSet(el(panelId + "-joint-h1"), fmtAngle(s.h1_angle));
  _maybeSet(el(panelId + "-joint-h2"), fmtAngle(s.h2_angle));
  _maybeSet(el(panelId + "-joint-p1"), fmtAngle(s.p1_angle));
  _maybeSet(el(panelId + "-joint-p2"), fmtAngle(s.p2_angle));
  _maybeSet(el(panelId + "-joint-head"), fmtAngle(s.head_angle));
  _maybeSet(el(panelId + "-joint-tail"), fmtAngle(s.tail_angle));
}

function _updateDriveState(panelId, s) {
  _maybeSet(el(panelId + "-drive-connected"), s.connected ? I18n.t("motor.connected") : I18n.t("motor.mock"));
  const dirEl = el(panelId + "-drive-direction");
  if (dirEl) {
    const labels = { forward: I18n.t("drive.forward"), backward: I18n.t("drive.backward"), stop: I18n.t("drive.stop") };
    dirEl.textContent = labels[s.direction] || s.direction || I18n.t("drive.stop");
  }
}

// ── Demo Panel ────────────────────────────────────────────────────────────────

function _renderDemoPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  const cfgFields = [
    ["init_wait",        t("demo.cfg_init_wait"),        0.5, 10,  0.1],
    ["spread_wait",      t("demo.cfg_spread_wait"),      0.5, 10,  0.1],
    ["drive_duration",   t("demo.cfg_drive_duration"),   0.5, 10,  0.1],
    ["drive_stop_pause", t("demo.cfg_drive_stop_pause"), 0.1, 5,   0.1],
    ["pt_duration",      t("demo.cfg_pt_duration"),      0.5, 10,  0.1],
    ["pt_stop_pause",    t("demo.cfg_pt_stop_pause"),    0.1, 5,   0.1],
    ["pause_after_home", t("demo.cfg_pause_after_home"), 0.5, 10,  0.1],
    ["cycle_wait",       t("demo.cfg_cycle_wait"),       0.5, 30,  0.5],
  ];
  let cfgHTML = cfgFields.map(([key, label, min, max, step]) =>
    `<label>${label}<input type="number" id="${panelId}-cfg-${key}" min="${min}" max="${max}" step="${step}" value="0" style="width:56px;height:20px;font-size:10px;text-align:center;margin-left:4px">s</label>`
  ).join("");

  body.innerHTML = `
    <div class="panel-info">
      <span>${t("hand.status")}<b id="${panelId}-demo-status">${t("demo.idle")}</b></span>
      <span>${t("demo.step")}<b id="${panelId}-demo-step">—</b></span>
    </div>
    <div style="text-align:center;padding:8px">
      <button class="btn-primary" id="${panelId}-demo-start" style="width:100px;height:42px;font-size:15px;margin:4px">${t("demo.start")}</button>
      <button class="btn-estop" id="${panelId}-demo-stop" style="width:100px;height:42px;font-size:15px;margin:4px">${t("demo.stop")}</button>
    </div>
    <div class="demo-config" style="padding:4px 8px;border-top:1px solid var(--border);font-size:10px;display:flex;flex-wrap:wrap;gap:4px 12px;justify-content:center">
      ${cfgHTML}
    </div>
    <div style="text-align:center;padding:4px">
      <button class="btn-secondary btn-sm" id="${panelId}-demo-apply-config" style="height:22px;font-size:10px">${t("demo.apply_config")}</button>
    </div>
    <div style="color:var(--muted);font-size:10px;text-align:center;padding:2px 8px 6px">
      ${t("demo.desc")}
    </div>
  `;
  el(panelId + "-demo-start").addEventListener("click", () => socket.emit("demo_start"));
  el(panelId + "-demo-stop").addEventListener("click", () => socket.emit("demo_stop"));
  el(panelId + "-demo-apply-config").addEventListener("click", () => {
    const cfg = {};
    cfgFields.forEach(([key]) => {
      const inp = el(panelId + "-cfg-" + key);
      if (inp) cfg[key] = parseFloat(inp.value);
    });
    socket.emit("demo_set_config", cfg);
  });
  // Request current config
  socket.emit("demo_get_config");
}

function _updateDemoState(panelId, s) {
  _maybeSet(el(panelId + "-demo-status"), s.active ? I18n.t("demo.running") : I18n.t("demo.idle"));
  _maybeSet(el(panelId + "-demo-step"), s.step ? I18n.t("demo.step_" + s.step) || s.step : "—");
  const startBtn = el(panelId + "-demo-start");
  if (startBtn) startBtn.disabled = s.active;
  // Update config inputs if present
  if (s.config) _applyDemoConfig(panelId, s.config);
}

function _applyDemoConfig(panelId, cfg) {
  for (const [key, val] of Object.entries(cfg)) {
    const inp = el(panelId + "-cfg-" + key);
    if (inp) inp.value = val;
  }
}

socket.on("demo_config", (data) => {
  PanelManager._panels.forEach((p, panelId) => {
    if (p.module !== "demo") return;
    _applyDemoConfig(panelId, data);
  });
});

socket.on("demo_status", (data) => {
  PanelManager._panels.forEach((p, panelId) => {
    if (p.module !== "demo") return;
    _maybeSet(el(panelId + "-demo-status"), data.active ? I18n.t("demo.running") : I18n.t("demo.idle"));
    const startBtn = el(panelId + "-demo-start");
    if (startBtn) startBtn.disabled = data.active;
  });
});

// ── Teleop Panel ──────────────────────────────────────────────────────────────

function _renderTeleopPanel(panelId, body) {
  const t = (k) => I18n.t(k);
  body.innerHTML = `
    <div class="status-row" style="font-size:13px;margin-bottom:10px">
      <span>${t("teleop.xr_status")}<b id="${panelId}-tl-xr">—</b></span>
      <span>${t("teleop.active")}<b id="${panelId}-tl-active">—</b></span>
      <span>${t("teleop.tracking")}<b id="${panelId}-tl-tracking">—</b></span>
      <span>${t("teleop.calibrated")}<b id="${panelId}-tl-calib">—</b></span>
      <span id="${panelId}-tl-fault-badge" class="badge fault" style="display:none">FAULT</span>
    </div>
    <div style="font-size:12px;margin-bottom:8px">
      <div><b>${t("teleop.position")}:</b> <span id="${panelId}-tl-pos">—</span></div>
      <div style="margin-top:4px">
        <b>${t("teleop.trigger")}:</b>
        <progress id="${panelId}-tl-trigger" value="0" max="100" style="width:120px;height:14px"></progress>
        <span id="${panelId}-tl-trigger-val" style="font-size:12px">0%</span>
      </div>
      <div style="margin-top:4px">
        <b>Grip:</b><span class="dot" id="${panelId}-tl-grip" style="font-size:14px">⚪</span>
        <b>A:</b><span class="dot" id="${panelId}-tl-a" style="font-size:14px">⚪</span>
        <b>B:</b><span class="dot" id="${panelId}-tl-b" style="font-size:14px">⚪</span>
      </div>
    </div>
    <div id="${panelId}-tl-joints" style="font-size:11px;margin-bottom:8px"></div>
    <div class="btn-row" style="gap:4px">
      <button class="btn-primary ctrl" id="${panelId}-tl-enable">${t("teleop.enable")}</button>
      <button class="btn-warn ctrl"    id="${panelId}-tl-disable">${t("teleop.disable")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-tl-calibrate">${t("teleop.calibrate")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-tl-log">${t("teleop.toggle_log")}</button>
    </div>
    <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:8px">
      <b>${t("teleop.gimbal_section")}</b>
    </div>
    <div class="status-row" style="font-size:13px;margin-bottom:6px">
      <span>${t("teleop.gimbal_status")}<b id="${panelId}-tl-gimbal-status">—</b></span>
      <span>${t("teleop.gimbal_tracking")}<b id="${panelId}-tl-gimbal-track">—</b></span>
      <span>${t("teleop.gimbal_calibrated")}<b id="${panelId}-tl-gimbal-calib">—</b></span>
    </div>
    <div style="font-size:12px;margin-bottom:6px">
      <div><b>${t("teleop.gimbal_head_yaw")}:</b> <span id="${panelId}-tl-head-yaw">0.0</span>°
           <b style="margin-left:8px">${t("teleop.gimbal_head_pitch")}:</b> <span id="${panelId}-tl-head-pitch">0.0</span>°</div>
    </div>
    <div style="font-size:12px;margin-bottom:2px">
      <span><b>${t("teleop.gimbal_deadband")}:</b>
        <input type="range" id="${panelId}-tl-deadband" min="0.02" max="2.0" step="0.02" value="0.30"
               style="width:60px;vertical-align:middle">
        <input type="number" id="${panelId}-tl-deadband-num" min="0.02" max="2.0" step="0.02" value="0.30"
               style="width:48px;font-size:11px">°</span>
      <span style="margin-left:8px"><b>${t("teleop.gimbal_smoothing")}:</b>
        <input type="range" id="${panelId}-tl-smoothing" min="0.02" max="1.0" step="0.02" value="0.25"
               style="width:60px;vertical-align:middle">
        <input type="number" id="${panelId}-tl-smoothing-num" min="0.02" max="1.0" step="0.02" value="0.25"
               style="width:48px;font-size:11px"></span>
    </div>
    <div style="font-size:12px;margin-bottom:2px">
      <span><b>${t("teleop.gimbal_rate_limit")}:</b>
        <input type="range" id="${panelId}-tl-rate-limit" min="5" max="500" step="5" value="60"
               style="width:60px;vertical-align:middle">
        <input type="number" id="${panelId}-tl-rate-limit-num" min="5" max="500" step="5" value="60"
               style="width:48px;font-size:11px">°/s</span>
    </div>
    <div style="font-size:12px;margin-bottom:4px">
      <span><b>${t("teleop.gimbal_yaw_gain")}:</b>
        <input type="range" id="${panelId}-tl-yaw-gain" min="0.1" max="3.0" step="0.05" value="1.0"
               style="width:60px;vertical-align:middle">
        <input type="number" id="${panelId}-tl-yaw-gain-num" min="0.1" max="3.0" step="0.05" value="1.0"
               style="width:48px;font-size:11px"></span>
      <span style="margin-left:8px"><b>${t("teleop.gimbal_pitch_gain")}:</b>
        <input type="range" id="${panelId}-tl-pitch-gain" min="0.1" max="3.0" step="0.05" value="1.0"
               style="width:60px;vertical-align:middle">
        <input type="number" id="${panelId}-tl-pitch-gain-num" min="0.1" max="3.0" step="0.05" value="1.0"
               style="width:48px;font-size:11px"></span>
      <button class="btn-secondary ctrl" id="${panelId}-tl-apply-config" style="font-size:11px;margin-left:8px">${t("teleop.gimbal_apply_config")}</button>
    </div>
    <div style="font-size:11px;color:var(--muted);margin-bottom:6px">
      ${t("teleop.gimbal_limits")}: Yaw [<span id="${panelId}-tl-yaw-limit-lo">-90</span>, <span id="${panelId}-tl-yaw-limit-hi">90</span>]°
      Pitch [<span id="${panelId}-tl-pitch-limit-lo">-90</span>, <span id="${panelId}-tl-pitch-limit-hi">60</span>]°
    </div>
    <div class="btn-row" style="gap:4px">
      <button class="btn-primary ctrl" id="${panelId}-tl-gimbal-enable">${t("teleop.gimbal_enable")}</button>
      <button class="btn-warn ctrl"    id="${panelId}-tl-gimbal-disable">${t("teleop.gimbal_disable")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-tl-gimbal-calibrate">${t("teleop.gimbal_calibrate")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-tl-gimbal-home">${t("teleop.gimbal_home")}</button>
    </div>
  `;

  const JOINT_NAMES = ["J1", "J2", "J3", "J4", "J5", "J6"];
  const jGrid = el(panelId + "-tl-joints");
  if (jGrid) {
    JOINT_NAMES.forEach((name, i) => {
      const row = document.createElement("div");
      row.style.cssText = "display:flex;gap:8px;align-items:center;margin-bottom:2px";
      row.innerHTML = `<label style="width:20px;font-weight:bold">${name}</label>
        <span class="val" id="${panelId}-tl-j${i}-tgt" style="width:60px">—</span>
        <span class="joint-actual" id="${panelId}-tl-j${i}-act" style="width:60px;color:var(--muted)">—</span>`;
      jGrid.appendChild(row);
    });
  }

  // Button events
  el(panelId + "-tl-enable")?.addEventListener("click",    () => socket.emit("teleop_enable"));
  el(panelId + "-tl-disable")?.addEventListener("click",   () => socket.emit("teleop_disable"));
  el(panelId + "-tl-calibrate")?.addEventListener("click", () => socket.emit("teleop_calibrate"));
  el(panelId + "-tl-log")?.addEventListener("click",       () => socket.emit("teleop_toggle_log"));
  el(panelId + "-tl-gimbal-enable")?.addEventListener("click",   () => socket.emit("teleop_gimbal_track_enable"));
  el(panelId + "-tl-gimbal-disable")?.addEventListener("click",  () => socket.emit("teleop_gimbal_track_disable"));
  el(panelId + "-tl-gimbal-calibrate")?.addEventListener("click", () => socket.emit("teleop_gimbal_calibrate"));
  el(panelId + "-tl-gimbal-home")?.addEventListener("click",     () => socket.emit("teleop_gimbal_home"));
  // Helper: sync range slider ↔ number input
  const _bindRangeNum = (sliderId, numId) => {
    const slider = el(sliderId);
    const num = el(numId);
    if (!slider || !num) return;
    slider.addEventListener("input", () => { num.value = parseFloat(slider.value).toFixed(2); });
    num.addEventListener("input", () => { slider.value = parseFloat(num.value) || 0; });
  };
  _bindRangeNum(panelId + "-tl-deadband", panelId + "-tl-deadband-num");
  _bindRangeNum(panelId + "-tl-smoothing", panelId + "-tl-smoothing-num");
  _bindRangeNum(panelId + "-tl-rate-limit", panelId + "-tl-rate-limit-num");
  _bindRangeNum(panelId + "-tl-yaw-gain", panelId + "-tl-yaw-gain-num");
  _bindRangeNum(panelId + "-tl-pitch-gain", panelId + "-tl-pitch-gain-num");

  // Apply button: sends all noise-suppression + gain params
  el(panelId + "-tl-apply-config")?.addEventListener("click", () => {
    socket.emit("teleop_set_head_config", {
      deadband: parseFloat(el(panelId + "-tl-deadband")?.value || 0.3),
      smoothing: parseFloat(el(panelId + "-tl-smoothing")?.value || 0.25),
      rate_limit: parseFloat(el(panelId + "-tl-rate-limit")?.value || 60),
    });
    socket.emit("teleop_set_head_gains", {
      yaw_gain: parseFloat(el(panelId + "-tl-yaw-gain")?.value || 1.0),
      pitch_gain: parseFloat(el(panelId + "-tl-pitch-gain")?.value || 1.0),
    });
  });
}

function _updateTeleopState(panelId, s) {
  _maybeSet(el(panelId + "-tl-xr"), s.xr_initialized ? "OK" : "N/A");
  _maybeSet(el(panelId + "-tl-active"), s.active ? I18n.t("teleop.active_yes") : I18n.t("teleop.active_no"));
  _maybeSet(el(panelId + "-tl-tracking"), s.tracking ? I18n.t("teleop.tracking_yes") : I18n.t("teleop.tracking_no"));
  _maybeSet(el(panelId + "-tl-calib"), s.calibrated ? I18n.t("teleop.calib_yes") : I18n.t("teleop.calib_no"));

  const pos = s.controller_position;
  _maybeSet(el(panelId + "-tl-pos"), pos ? `X:${fmt(pos[0],1)} Y:${fmt(pos[1],1)} Z:${fmt(pos[2],1)}` : "—");

  const prog = el(panelId + "-tl-trigger");
  const tval = el(panelId + "-tl-trigger-val");
  if (prog) prog.value = Math.round((s.trigger_value || 0) * 100);
  if (tval) tval.textContent = Math.round((s.trigger_value || 0) * 100) + "%";

  const setDot = (e, active) => { if (e) e.textContent = active ? "🟢" : "⚪"; };
  setDot(el(panelId + "-tl-grip"), s.grip_pressed);
  setDot(el(panelId + "-tl-a"), s.button_a);
  setDot(el(panelId + "-tl-b"), s.button_b);

  const fb = el(panelId + "-tl-fault-badge");
  if (fb) { fb.style.display = s.is_fault ? "inline-block" : "none"; if (s.is_fault && s.fault_text) fb.title = s.fault_text; }

  if (s.target_joints_deg) s.target_joints_deg.forEach((a, i) => _maybeSet(el(panelId + `-tl-j${i}-tgt`), I18n.t("teleop.target") + fmt(a, 1) + "°"));
  if (s.current_joints_deg) s.current_joints_deg.forEach((a, i) => _maybeSet(el(panelId + `-tl-j${i}-act`), I18n.t("teleop.actual") + fmt(a, 1) + "°"));

  // ── Gimbal head tracking section ──
  const gimbalAvail = s.gimbal_available;
  _maybeSet(el(panelId + "-tl-gimbal-status"),
    gimbalAvail ? I18n.t("teleop.active_yes") : I18n.t("teleop.gimbal_unavailable"));
  _maybeSet(el(panelId + "-tl-gimbal-track"),
    s.gimbal_tracking ? I18n.t("teleop.tracking_yes") : I18n.t("teleop.tracking_no"));
  _maybeSet(el(panelId + "-tl-gimbal-calib"),
    s.gimbal_calibrated ? I18n.t("teleop.calib_yes") : I18n.t("teleop.calib_no"));
  _maybeSet(el(panelId + "-tl-head-yaw"), fmt(s.head_yaw_deg || 0, 1));
  _maybeSet(el(panelId + "-tl-head-pitch"), fmt(s.head_pitch_deg || 0, 1));

  // Noise-suppression params — update sliders + number inputs from state
  const _syncSliderNum = (sliderId, numId, value, decimals) => {
    const slider = el(sliderId);
    const num = el(numId);
    if (slider) slider.value = value;
    if (num) num.value = value.toFixed(decimals);
  };
  const deadband = s.head_deadband != null ? s.head_deadband : 0.3;
  const smoothing = s.head_smoothing != null ? s.head_smoothing : 0.25;
  const rateLimit = s.head_rate_limit != null ? s.head_rate_limit : 60;
  const yawGain = s.head_yaw_gain != null ? s.head_yaw_gain : 1.0;
  const pitchGain = s.head_pitch_gain != null ? s.head_pitch_gain : 1.0;
  _syncSliderNum(panelId + "-tl-deadband", panelId + "-tl-deadband-num", deadband, 2);
  _syncSliderNum(panelId + "-tl-smoothing", panelId + "-tl-smoothing-num", smoothing, 2);
  _syncSliderNum(panelId + "-tl-rate-limit", panelId + "-tl-rate-limit-num", rateLimit, 0);
  _syncSliderNum(panelId + "-tl-yaw-gain", panelId + "-tl-yaw-gain-num", yawGain, 2);
  _syncSliderNum(panelId + "-tl-pitch-gain", panelId + "-tl-pitch-gain-num", pitchGain, 2);

  // Limits display
  if (s.gimbal_yaw_limits) {
    _maybeSet(el(panelId + "-tl-yaw-limit-lo"), s.gimbal_yaw_limits[0]);
    _maybeSet(el(panelId + "-tl-yaw-limit-hi"), s.gimbal_yaw_limits[1]);
  }
  if (s.gimbal_pitch_limits) {
    _maybeSet(el(panelId + "-tl-pitch-limit-lo"), s.gimbal_pitch_limits[0]);
    _maybeSet(el(panelId + "-tl-pitch-limit-hi"), s.gimbal_pitch_limits[1]);
  }

  const gimbalBtns = ["-tl-gimbal-enable", "-tl-gimbal-disable", "-tl-gimbal-calibrate", "-tl-gimbal-home"];
  gimbalBtns.forEach(suffix => {
    const b = el(panelId + suffix);
    if (b) b.disabled = !s.gimbal_available;
  });
}

function _renderGimbalPanel(panelId, body) {
  body.innerHTML = `
    <div class="gimbal-status" id="${panelId}-gimbal-status">${I18n.t("gimbal.not_connected")}</div>
    <div class="gimbal-angles">
      <div class="gimbal-row">
        <span class="gimbal-label">${I18n.t("gimbal.yaw")}</span>
        <span class="gimbal-val" id="${panelId}-gimbal-yaw-cur">0.0°</span>
        <span class="gimbal-val gimbal-target" id="${panelId}-gimbal-yaw-tgt">→ 0.0°</span>
      </div>
      <div class="gimbal-row">
        <span class="gimbal-label">${I18n.t("gimbal.pitch")}</span>
        <span class="gimbal-val" id="${panelId}-gimbal-pitch-cur">0.0°</span>
        <span class="gimbal-val gimbal-target" id="${panelId}-gimbal-pitch-tgt">→ 0.0°</span>
      </div>
      <div class="gimbal-row" style="font-size:11px; color:var(--accent2)">
        <span class="gimbal-label">${I18n.t("gimbal.rel_angle")}</span>
        <span class="gimbal-val" id="${panelId}-gimbal-rel-yaw">0.0°</span>
        <span class="gimbal-val" id="${panelId}-gimbal-rel-pitch">0.0°</span>
      </div>
      <div class="gimbal-row" style="font-size:11px; color:var(--text-muted)">
        <span class="gimbal-label">${I18n.t("gimbal.home_pos")}</span>
        <span class="gimbal-val" id="${panelId}-gimbal-home-yaw">0.0°</span>
        <span class="gimbal-val" id="${panelId}-gimbal-home-pitch">0.0°</span>
      </div>
    </div>
    <div class="gimbal-input-row">
      <label>${I18n.t("gimbal.yaw")}:</label>
      <input type="number" id="${panelId}-gimbal-yaw-in" value="0" step="1" min="-90" max="90" style="width:70px">
      <label>${I18n.t("gimbal.pitch")}:</label>
      <input type="number" id="${panelId}-gimbal-pitch-in" value="0" step="1" min="-90" max="60" style="width:70px">
      <button class="btn-primary btn-sm" id="${panelId}-gimbal-move-to">${I18n.t("gimbal.move_to")}</button>
    </div>
    <div class="btn-row center" style="margin-top:6px; gap:4px">
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-up">▲ ${I18n.t("gimbal.move_up")}</button>
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-left">◀ ${I18n.t("gimbal.move_left")}</button>
      <button class="btn-primary btn-sm" id="${panelId}-gimbal-home">${I18n.t("gimbal.home")}</button>
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-right">${I18n.t("gimbal.move_right")} ▶</button>
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-down">${I18n.t("gimbal.move_down")} ▼</button>
    </div>
    <div class="btn-row center" style="margin-top:4px">
      <label style="font-size:12px">${I18n.t("gimbal.step")}:</label>
      <select id="${panelId}-gimbal-step" style="font-size:12px;padding:2px 4px">
        <option value="1">1°</option>
        <option value="5" selected>5°</option>
        <option value="10">10°</option>
        <option value="15">15°</option>
        <option value="30">30°</option>
      </select>
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-damping">${I18n.t("gimbal.damping")}</button>
      <button class="btn-secondary btn-sm" id="${panelId}-gimbal-set-home">${I18n.t("gimbal.set_home")}</button>
    </div>
  `;
  var step = () => parseInt(el(panelId + "-gimbal-step")?.value || 5);
  el(panelId + "-gimbal-move-to")?.addEventListener("click", () => {
    var y = parseFloat(el(panelId + "-gimbal-yaw-in")?.value || 0);
    var p = parseFloat(el(panelId + "-gimbal-pitch-in")?.value || 0);
    socket.emit("gimbal_move_to", { yaw: y, pitch: p });
  });
  el(panelId + "-gimbal-up")?.addEventListener("click",    () => socket.emit("gimbal_move_by", { dyaw: 0, dpitch: -step() }));
  el(panelId + "-gimbal-down")?.addEventListener("click",  () => socket.emit("gimbal_move_by", { dyaw: 0, dpitch: step() }));
  el(panelId + "-gimbal-left")?.addEventListener("click",  () => socket.emit("gimbal_move_by", { dyaw: -step(), dpitch: 0 }));
  el(panelId + "-gimbal-right")?.addEventListener("click", () => socket.emit("gimbal_move_by", { dyaw: step(), dpitch: 0 }));
  el(panelId + "-gimbal-home")?.addEventListener("click",    () => socket.emit("gimbal_home"));
  el(panelId + "-gimbal-damping")?.addEventListener("click", () => socket.emit("gimbal_damping"));
  el(panelId + "-gimbal-set-home")?.addEventListener("click", () => socket.emit("gimbal_set_home"));
}

function _updateGimbalState(panelId, s) {
  var st = el(panelId + "-gimbal-status");
  if (st) {
    st.textContent = s.connected ? (s.is_fault ? s.fault_text || I18n.t("status.fault") : I18n.t("status.online")) : I18n.t("gimbal.not_connected");
    st.style.color = s.connected ? (s.is_fault ? "var(--warn)" : "var(--accent2)") : "var(--danger)";
  }
  _maybeSet(el(panelId + "-gimbal-yaw-cur"), fmt(s.yaw_deg, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-pitch-cur"), fmt(s.pitch_deg, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-yaw-tgt"), "→ " + fmt(s.target_yaw_deg, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-pitch-tgt"), "→ " + fmt(s.target_pitch_deg, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-rel-yaw"), fmt(s.rel_yaw_deg || 0, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-rel-pitch"), fmt(s.rel_pitch_deg || 0, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-home-yaw"), fmt(s.home_yaw_deg || 0, 1) + "°");
  _maybeSet(el(panelId + "-gimbal-home-pitch"), fmt(s.home_pitch_deg || 0, 1) + "°");
}

function _updateServoState(panelId, s) {
  _maybeSet(el(panelId + "-servo-connected"), s.connected ? I18n.t("servo.connected") : I18n.t("servo.mock"));
  const dirEl = el(panelId + "-servo-direction");
  if (dirEl) {
    const labels = { spread: I18n.t("servo.spread"), shrink: I18n.t("servo.shrink"), stop: I18n.t("servo.stop") };
    dirEl.textContent = labels[s.direction] || s.direction;
    dirEl.style.color = s.direction !== "stop" ? "var(--accent2)" : "var(--muted)";
  }

  // Display servo IDs
  _maybeSet(el(panelId + "-servo-head-ids"), "ID: " + (s.head_ids || []).join(","));
  _maybeSet(el(panelId + "-servo-tail-ids"), "ID: " + (s.tail_ids || []).join(","));

  // Update monitor data for head (first head ID)
  if (s.monitor && s.head_ids && s.head_ids.length > 0) {
    const headId = s.head_ids[0];
    const m = s.monitor[headId];
    if (m) {
      const headMon = el(panelId + "-servo-head-monitor");
      if (headMon) {
        const spans = headMon.querySelectorAll("span");
        if (spans[0]) spans[0].innerHTML = I18n.t("servo.voltage") + "<b>" + (m.voltage ? (m.voltage / 1000).toFixed(1) + " V" : "—") + "</b>";
        if (spans[1]) spans[1].innerHTML = I18n.t("servo.current") + "<b>" + (m.current ? (m.current / 1000).toFixed(2) + " A" : "—") + "</b>";
        if (spans[2]) spans[2].innerHTML = I18n.t("servo.temp") + "<b>" + (m.temp != null ? m.temp.toFixed(0) + " °C" : "—") + "</b>";
      }
      // Update angle slider
      if (m.angle != null) {
        const sl = el(panelId + "-servo-head-angle");
        const valEl = el(panelId + "-servo-head-angle-val");
        if (sl && !sl.matches(":active")) sl.value = m.angle;
        if (valEl) valEl.textContent = m.angle.toFixed(0) + "°";
      }
    }
  }

  // Update monitor data for tail (first tail ID)
  if (s.monitor && s.tail_ids && s.tail_ids.length > 0) {
    const tailId = s.tail_ids[0];
    const m = s.monitor[tailId];
    if (m) {
      const tailMon = el(panelId + "-servo-tail-monitor");
      if (tailMon) {
        const spans = tailMon.querySelectorAll("span");
        if (spans[0]) spans[0].innerHTML = I18n.t("servo.voltage") + "<b>" + (m.voltage ? (m.voltage / 1000).toFixed(1) + " V" : "—") + "</b>";
        if (spans[1]) spans[1].innerHTML = I18n.t("servo.current") + "<b>" + (m.current ? (m.current / 1000).toFixed(2) + " A" : "—") + "</b>";
        if (spans[2]) spans[2].innerHTML = I18n.t("servo.temp") + "<b>" + (m.temp != null ? m.temp.toFixed(0) + " °C" : "—") + "</b>";
      }
      // Update angle slider
      if (m.angle != null) {
        const sl = el(panelId + "-servo-tail-angle");
        const valEl = el(panelId + "-servo-tail-angle-val");
        if (sl && !sl.matches(":active")) sl.value = m.angle;
        if (valEl) valEl.textContent = m.angle.toFixed(0) + "°";
      }
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// State Handler
// ═══════════════════════════════════════════════════════════════════════════════

socket.on("robot_state", (s) => {
  // Find all open panels and update their content
  PanelManager._panels.forEach((p, panelId) => {
    const modState = s[p.module];
    if (!modState) return;

    switch (p.module) {
      case "ugv":
        _updateUgvState(panelId, modState);
        PanelManager.updateStatus("ugv", modState.connected, modState.vehicle_state === 2);
        break;
      case "lift":
        _updateLiftState(panelId, modState);
        PanelManager.updateStatus("lift", modState.connected, modState.is_fault);
        break;
      case "arm":
        _updateArmState(panelId, modState);
        PanelManager.updateStatus("arm", modState.connected, modState.is_fault);
        break;
      case "hand":
        _updateHandState(panelId, modState);
        PanelManager.updateStatus("hand", modState.connected, modState.is_fault);
        break;
      case "camera":
        PanelManager.updateStatus("camera", modState.connected, false);
        // Per-device state: look up the panel's selected device
        {
          const panel = document.getElementById(panelId);
          const dev = panel ? (panel.dataset.camDevice || "") : "";
          if (dev && modState.devices && modState.devices[dev]) {
            _updateCameraStatus(panelId, modState.devices[dev]);
          } else {
            _updateCameraStatus(panelId, modState);
          }
        }
        break;
      case "motor":
        _updateMotorState(panelId, modState);
        PanelManager.updateStatus("motor", modState.connected, false);
        break;
      case "servo":
        _updateServoState(panelId, modState);
        PanelManager.updateStatus("servo", modState.connected, false);
        break;
      case "joint":
        _updateJointState(panelId, modState);
        PanelManager.updateStatus("joint", modState.connected, false);
        break;
      case "drive":
        _updateDriveState(panelId, modState);
        PanelManager.updateStatus("drive", modState.connected, false);
        break;
      case "demo":
        _updateDemoState(panelId, modState);
        PanelManager.updateStatus("demo", modState.active, false);
        break;
      case "teleop":
        _updateTeleopState(panelId, modState);
        PanelManager.updateStatus("teleop", modState.connected, modState.is_fault);
        break;
      case "gimbal":
        _updateGimbalState(panelId, modState);
        PanelManager.updateStatus("gimbal", modState.connected, modState.is_fault);
        break;
    }
  });

  // Also update sidebar status dots for modules without panels
  ACTIVE_MODULES.forEach((mod) => {
    if (!PanelManager.isOpen(mod) && s[mod]) {
      PanelManager.updateStatus(mod, s[mod].connected,
        s[mod].is_fault || s[mod].vehicle_state === 2 || false);
    }
  });

  _maybeSet(el("last-update"), I18n.t("misc.last_update") + new Date().toLocaleTimeString());
});

function _updateUgvState(panelId, s) {
  _maybeSet(el(panelId + "-ugv-connected"), s.connected ? I18n.t("ugv.connected") : I18n.t("ugv.disconnected"));
  _maybeSet(el(panelId + "-ugv-battery"), fmt(s.battery_voltage, 1));
  _maybeSet(el(panelId + "-ugv-linear"), fmt(s.linear_velocity));
  _maybeSet(el(panelId + "-ugv-angular"), fmt(s.angular_velocity));
  _maybeSet(el(panelId + "-ugv-soc"), s.bms_soc != null ? s.bms_soc : "—");
  const vsEl = el(panelId + "-ugv-vehicle-state");
  if (vsEl) {
    const vsLabels = {0: I18n.t("ugv.state.normal"), 1: I18n.t("ugv.state.estop"), 2: I18n.t("ugv.state.fault")};
    vsEl.textContent = vsLabels[s.vehicle_state] || s.vehicle_state;
    vsEl.style.color = s.vehicle_state === 0 ? "#4caf50" : s.vehicle_state === 1 ? "#ff9800" : "#f44336";
  }
  const cmEl = el(panelId + "-ugv-control-mode");
  const enabled = s.control_mode === 1;
  if (cmEl) {
    const cmLabels = {0: I18n.t("ugv.mode.standby"), 1: I18n.t("ugv.mode.can"), 2: I18n.t("ugv.mode.uart"), 3: I18n.t("ugv.mode.remote")};
    cmEl.textContent = cmLabels[s.control_mode] || s.control_mode;
    cmEl.style.color = enabled ? "#4caf50" : s.control_mode === 0 ? "#f44336" : "#ff9800";
  }
  // Toggle controls visibility
  const controls = el(panelId + "-ugv-controls");
  const disabledMsg = el(panelId + "-ugv-disabled-msg");
  if (controls) controls.style.display = enabled ? "" : "none";
  if (disabledMsg) disabledMsg.style.display = enabled ? "none" : "";
  // Enable button state
  const btnEn = el(panelId + "-ugv-enable");
  if (btnEn) {
    if (enabled) { btnEn.textContent = I18n.t("ugv.enabled"); btnEn.disabled = true; btnEn.style.opacity = "0.6"; }
    else        { btnEn.textContent = I18n.t("ugv.enable_can"); btnEn.disabled = false; btnEn.style.opacity = ""; }
  }
  _updateLogPanel(panelId + "-ugv-log-panel", s.logs);
}

function _updateLiftState(panelId, s) {
  _maybeSet(el(panelId + "-lift-height"), fmt(s.height_mm, 1));
  _maybeSet(el(panelId + "-lift-vel"), fmt(s.velocity_mm_s, 1));
  _maybeSet(el(panelId + "-lift-torque"), s.torque_permille ?? "—");
  const fb = el(panelId + "-lift-fault-badge");
  if (fb) fb.style.display = s.is_fault ? "inline-block" : "none";

  if (typeof s.min_mm === "number") {
    _rebuildPresetsFor(panelId, s.min_mm, s.max_mm);
    const inMin = el(panelId + "-lift-target"); // keep current target
    const inSpd = el(panelId + "-lift-speed");
  }

  const dsEl = el(panelId + "-lift-drive-state");
  if (dsEl) {
    dsEl.textContent = s.drive_state || "—";
    const ds = s.drive_state || "";
    dsEl.style.color = ds.toLowerCase().includes("fault") ? "#f44336"
      : ds.toLowerCase().includes("operation") ? "#4caf50" : "#ff9800";
  }
  _maybeSet(el(panelId + "-lift-sw"), s.statusword || "—");
  _updateLogPanel(panelId + "-lift-log-panel", s.logs);
}

function _updateArmState(panelId, s) {
  _maybeSet(el(panelId + "-arm-connected"), s.connected ? I18n.t("arm.connected") : I18n.t("arm.disconnected"));
  _maybeSet(el(panelId + "-arm-enabled"), s.is_enabled ? I18n.t("arm.enabled_yes") : I18n.t("arm.enabled_no"));
  _maybeSet(el(panelId + "-arm-moving"), s.is_moving ? I18n.t("arm.moving_yes") : I18n.t("arm.moving_no"));
  _maybeSet(el(panelId + "-arm-robot-mode"), s.robot_mode || "—");
  _maybeSet(el(panelId + "-arm-safety-mode"), s.safety_mode || "—");
  const smEl = el(panelId + "-arm-safety-mode");
  if (smEl) {
    const sm = (s.safety_mode || "").toUpperCase();
    smEl.style.color = sm === "NORMAL" ? "#4caf50"
      : sm.includes("PROTECTIVE") || sm.includes("REDUCED") ? "#ff9800" : "#f44336";
  }
  const afb = el(panelId + "-arm-fault-badge");
  if (afb) {
    afb.style.display = s.is_fault ? "inline-block" : "none";
    if (s.is_fault && s.fault_text) afb.title = s.fault_text;
  }

  // Collision sensitivity slider (don't update while user is dragging)
  const slCS = el(panelId + "-sl-arm-cs");
  const lblCS = el(panelId + "-lbl-arm-cs");
  if (slCS && !slCS.matches(":active") && typeof s.collision_sensitivity === "number") {
    slCS.value = s.collision_sensitivity;
    if (lblCS) lblCS.textContent = s.collision_sensitivity.toFixed(2);
  }

  // Collision warning banner
  const cwEl = el(panelId + "-arm-collision-warn");
  if (cwEl) {
    if (s.collision_warning) {
      cwEl.style.display = "block";
      cwEl.textContent = "⚠ " + s.collision_warning;
    } else {
      cwEl.style.display = "none";
    }
  }

  // Commanded joint angles (update sliders)
  if (s.joint_angles_deg) {
    s.joint_angles_deg.forEach((angle, i) => {
      const sl = el(panelId + `-arm-j${i}`);
      const val = el(panelId + `-arm-j${i}-val`);
      if (sl && !sl.matches(":active")) sl.value = angle;
      if (val) val.textContent = fmt(angle, 1) + "°";
    });
  }

  // Actual joint angles from hardware
  if (s.joint_actual_deg) {
    s.joint_actual_deg.forEach((angle, i) => {
      const actEl = el(panelId + `-arm-j${i}-act`);
      if (actEl) {
        actEl.textContent = I18n.t("arm.actual_prefix") + fmt(angle, 1) + "°";
        // Color-code: green if close to commanded, yellow if diverging
        const cmd = (s.joint_angles_deg || [])[i] || 0;
        const diff = Math.abs(angle - cmd);
        actEl.style.color = diff < 1 ? "#4caf50" : diff < 5 ? "#ff9800" : "#f44336";
      }
    });
  }

  // Joint currents
  if (s.joint_currents_a) {
    s.joint_currents_a.forEach((cur, i) => {
      const ldEl = el(panelId + `-arm-j${i}-load`);
      if (ldEl) {
        ldEl.textContent = fmt(cur, 2) + " A";
        ldEl.style.color = Math.abs(cur) > 5 ? "#ff9800" : "#999";
      }
    });
  }

  // Joint temperatures
  if (s.joint_temps_c) {
    s.joint_temps_c.forEach((tmp, i) => {
      const tpEl = el(panelId + `-arm-j${i}-temp`);
      if (tpEl) {
        tpEl.textContent = fmt(tmp, 0) + " °C";
        tpEl.style.color = tmp > 65 ? "#ff9800" : tmp > 75 ? "#f44336" : "#999";
      }
    });
  }

  _updateLogPanel(panelId + "-arm-log-panel", s.logs);
}

function _updateHandState(panelId, s) {
  _maybeSet(el(panelId + "-hand-enabled"), s.is_enabled ? I18n.t("hand.enabled") : I18n.t("hand.disabled"));
  _maybeSet(el(panelId + "-hand-force"), fmt(s.grip_force_n, 1));
  if (s.finger_limits) {
    s.finger_limits.forEach((lim, i) => {
      const sl = el(panelId + `-hand-f${i}`);
      if (sl) {
        sl.min = lim.min / 1000;
        sl.max = lim.max / 1000;
      }
    });
  }
  if (s.finger_positions) {
    s.finger_positions.forEach((pos, i) => {
      const sl = el(panelId + `-hand-f${i}`);
      const val = el(panelId + `-hand-f${i}-val`);
      if (sl && !sl.matches(":active")) sl.value = pos;
      if (val) {
        const maxPct = s.finger_limits ? Math.round(s.finger_limits[i].max / 10) : 100;
        val.textContent = fmt(pos * 100, 0) + "%";
      }
    });
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Helpers
// ═══════════════════════════════════════════════════════════════════════════════

function _maybeSet(el, value) {
  if (el) el.textContent = value;
}

function _togglePanelLog(logPanel, btn, label) {
  if (logPanel.style.display === "block") {
    logPanel.style.display = "none";
    btn.innerHTML = label + " ▾";
  } else {
    logPanel.style.display = "block";
    btn.innerHTML = label + " ▴";
  }
}

function _updateLogPanel(panelId, logs) {
  const panel = el(panelId);
  if (!panel || !logs || logs.length === 0) return;
  const lastTs = parseFloat(panel.dataset.lastTs || "0");
  const latest = logs[logs.length - 1];
  if (latest.ts <= lastTs) return;
  panel.dataset.lastTs = latest.ts;
  panel.innerHTML = logs.map(e => {
    const t = new Date(e.ts * 1000).toLocaleTimeString();
    const cls = e.level === "error" ? "#f44336" : e.level === "warn" ? "#ff9800" : "#999";
    return `<div style="color:${cls};line-height:1.6">${t} [${e.level.toUpperCase()}] ${e.msg}</div>`;
  }).join("");
  panel.scrollTop = panel.scrollHeight;
}

function _checkEmptyState() {
  const canvas = el("canvas");
  if (!canvas) return;
  const hasPanels = canvas.querySelectorAll(".panel").length > 0;
  let emptyEl = canvas.querySelector(".canvas-empty");
  if (!hasPanels && !emptyEl) {
    emptyEl = document.createElement("div");
    emptyEl.className = "canvas-empty";
    emptyEl.innerHTML = "<span>" + I18n.t("sidebar.empty_hint") + "</span>";
    canvas.appendChild(emptyEl);
  } else if (hasPanels && emptyEl) {
    emptyEl.remove();
  }
}

// ═══════════════════════════════════════════════════════════════════════════════
// Emergency Stop
// ═══════════════════════════════════════════════════════════════════════════════

const btnEstop = el("btn-estop");
if (btnEstop) {
  btnEstop.addEventListener("click", () => {
    if (ACTIVE_MODULES.includes("ugv"))  socket.emit("ugv_estop");
    if (ACTIVE_MODULES.includes("lift")) socket.emit("lift_stop");
    if (ACTIVE_MODULES.includes("arm"))  socket.emit("arm_stop");
    if (ACTIVE_MODULES.includes("hand")) socket.emit("hand_stop");
    if (ACTIVE_MODULES.includes("motor")) socket.emit("motor_stop");
    if (ACTIVE_MODULES.includes("servo")) socket.emit("servo_stop");
    if (ACTIVE_MODULES.includes("joint")) { socket.emit("joint_waist_stop"); socket.emit("joint_pt_stop"); }
    if (ACTIVE_MODULES.includes("drive")) socket.emit("drive_stop");
    if (ACTIVE_MODULES.includes("teleop")) socket.emit("teleop_disable");
  });
}

// ── Power buttons ────────────────────────────────────────────────────────────

let _powerAction = null;
const powerOverlay = el("power-overlay");
const powerTitle = el("power-title");
const powerMsg = el("power-msg");
const btnPowerConfirm = el("btn-power-confirm");
const btnPowerCancel = el("btn-power-cancel");
const btnPowerCancel2 = el("btn-power-cancel2");

function _showPowerConfirm(action) {
  _powerAction = action;
  if (action === "reboot") {
    powerTitle.textContent = I18n.t("power.reboot_title");
    powerMsg.textContent = I18n.t("power.reboot_msg");
    btnPowerConfirm.style.background = "#f0c040";
    btnPowerConfirm.style.color = "#1a1a1a";
  } else {
    powerTitle.textContent = I18n.t("power.shutdown_title");
    powerMsg.textContent = I18n.t("power.shutdown_msg");
    btnPowerConfirm.style.background = "var(--danger)";
    btnPowerConfirm.style.color = "#fff";
  }
  powerOverlay.style.display = "flex";
}
function _hidePowerConfirm() {
  powerOverlay.style.display = "none";
  _powerAction = null;
}

const btnReboot = el("btn-reboot");
if (btnReboot) btnReboot.addEventListener("click", () => _showPowerConfirm("reboot"));
const btnShutdown = el("btn-shutdown");
if (btnShutdown) btnShutdown.addEventListener("click", () => _showPowerConfirm("shutdown"));
if (btnPowerCancel) btnPowerCancel.addEventListener("click", _hidePowerConfirm);
if (btnPowerCancel2) btnPowerCancel2.addEventListener("click", _hidePowerConfirm);
if (btnPowerConfirm) {
  btnPowerConfirm.addEventListener("click", () => {
    if (_powerAction) {
      socket.emit("system_power", { action: _powerAction });
      _hidePowerConfirm();
      // Show brief feedback — the page may close before response arrives
      setTimeout(() => alert(I18n.t(_powerAction === "reboot" ? "power.reboot_sent" : "power.shutdown_sent")), 200);
    }
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// Layout Selector Events
// ═══════════════════════════════════════════════════════════════════════════════

const layoutSel = el("layout-selector");
if (layoutSel) {
  layoutSel.addEventListener("change", () => {
    const name = layoutSel.value;
    if (name) LayoutManager.load(name);
  });
}

const btnSave = el("btn-layout-save");
if (btnSave) {
  btnSave.addEventListener("click", () => {
    const name = prompt(I18n.t("layout.prompt_name"), I18n.t("layout.default_name"));
    if (name) LayoutManager.save(name.trim());
  });
}

const btnDelete = el("btn-layout-delete");
if (btnDelete) {
  btnDelete.addEventListener("click", () => {
    const sel = el("layout-selector");
    if (sel && sel.value) LayoutManager.delete(sel.value);
  });
}

// ═══════════════════════════════════════════════════════════════════════════════
// lift_limits event (from server on set_zero/set_limits)
// ═══════════════════════════════════════════════════════════════════════════════

socket.on("lift_limits", (data) => {
  _maybeSet(el("lift-limit-min"), data.min_mm?.toFixed(1));
  _maybeSet(el("lift-limit-max"), data.max_mm?.toFixed(1));
  _maybeSet(el("lift-zero-offset"), data.zero_offset_mm?.toFixed(1));
  const inMin = el("lift-min-mm");
  const inMax = el("lift-max-mm");
  if (inMin) inMin.value = data.min_mm;
  if (inMax) inMax.value = data.max_mm;
  // Update presets in all open lift panels
  PanelManager._panels.forEach((p, panelId) => {
    if (p.module === "lift") _rebuildPresetsFor(panelId, data.min_mm, data.max_mm);
  });
});

socket.on("joint_limits", (data) => {
  PanelManager._panels.forEach((p, panelId) => {
    if (p.module !== "joint") return;
    const setVal = (id, v) => { const e = el(panelId + id); if (e) e.value = v; };
    setVal("-joint-hmin", data.h_min); setVal("-joint-hmax", data.h_max);
    setVal("-joint-pmin", data.p_min); setVal("-joint-pmax", data.p_max);
    setVal("-joint-whmin", data.wh_min); setVal("-joint-whmax", data.wh_max);
    setVal("-joint-wtmin", data.wt_min); setVal("-joint-wtmax", data.wt_max);
  });
});

// restart_can result
socket.on("restart_can_result", (r) => {
  PanelManager._panels.forEach((p, panelId) => {
    const btn = el(panelId + "-restart-can");
    if (btn && p.module === "ugv") { btn.disabled = false; btn.textContent = r.ok ? I18n.t("ugv.can_restarted") : I18n.t("ugv.restart_can"); }
  });
  if (!r.ok) alert(r.msg || I18n.t("ugv.restart_failed"));
});

// ═══════════════════════════════════════════════════════════════════════════════
// Settings Modal (admin only)
// ═══════════════════════════════════════════════════════════════════════════════

if (ROLE === "admin") {
  (function _initSettings() {
    const overlay = el("settings-overlay");
    if (!overlay) return;

    el("btn-settings")?.addEventListener("click", () => {
      overlay.style.display = "flex";
      socket.emit("admin_get_users");
      socket.emit("settings_get");
    });
    el("btn-settings-close")?.addEventListener("click", () => { overlay.style.display = "none"; });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.style.display = "none"; });

    el("btn-lift-set-zero")?.addEventListener("click", () => socket.emit("lift_set_zero"));
    el("btn-lift-set-limits")?.addEventListener("click", () => {
      const minVal = parseFloat(el("lift-min-mm")?.value);
      const maxVal = parseFloat(el("lift-max-mm")?.value);
      if (isNaN(minVal) || isNaN(maxVal) || minVal >= maxVal) { alert(I18n.t("misc.min_less_max")); return; }
      socket.emit("lift_set_limits", { min_mm: minVal, max_mm: maxVal });
    });
    el("btn-save-camera-timeout")?.addEventListener("click", () => {
      const val = parseInt(el("camera-timeout")?.value, 10);
      if (isNaN(val) || val < 1) { alert(I18n.t("misc.enter_valid_min")); return; }
      socket.emit("settings_set", { key: "camera_timeout_min", value: val });
    });
    el("btn-save-sudo-password")?.addEventListener("click", () => {
      const val = el("sudo-password")?.value || "";
      socket.emit("settings_set", { key: "sudo_password", value: val });
    });

    socket.on("settings_data", (data) => {
      if (data.camera_timeout_min) {
        const inp = el("camera-timeout"); if (inp) inp.value = data.camera_timeout_min;
      }
      if (data.sudo_password !== undefined) {
        const inp = el("sudo-password"); if (inp) inp.value = data.sudo_password;
      }
    });
    socket.on("settings_ack", (data) => {
      if (data.key === "camera_timeout_min") {
        const inp = el("camera-timeout"); if (inp) inp.value = data.value;
      }
      if (data.key === "sudo_password") {
        const inp = el("sudo-password"); if (inp) inp.value = data.value;
      }
    });

    // User management
    socket.on("admin_users", (users) => {
      const tbody = el("user-table-body"); if (!tbody) return;
      tbody.innerHTML = "";
      users.forEach((u) => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
          <td>${u.username}</td>
          <td><select class="role-select" data-id="${u.id}">
            <option value="viewer" ${u.role==="viewer"?"selected":""}>${I18n.t("settings.role_viewer")}</option>
            <option value="operator" ${u.role==="operator"?"selected":""}>${I18n.t("settings.role_operator")}</option>
            <option value="admin" ${u.role==="admin"?"selected":""}>${I18n.t("settings.role_admin")}</option>
          </select></td>
          <td><span class="badge ${u.status==='active'?'active':'pending'}">${u.status==='active' ? I18n.t("settings.user_active") : I18n.t("settings.user_pending")}</span></td>
          <td class="user-actions">
            ${u.status==='pending'?`<button class="btn-primary btn-sm btn-approve" data-id="${u.id}">${I18n.t("settings.approve")}</button>`:""}
            <button class="btn-secondary btn-sm btn-delete" data-id="${u.id}">${I18n.t("settings.delete_user")}</button>
          </td>`;
        tbody.appendChild(tr);
      });
      tbody.querySelectorAll(".role-select").forEach((s) => {
        s.addEventListener("change", () => socket.emit("admin_update_user", {id:parseInt(s.dataset.id),role:s.value}));
      });
      tbody.querySelectorAll(".btn-approve").forEach((b) => {
        b.addEventListener("click", () => socket.emit("admin_update_user", {id:parseInt(b.dataset.id),status:"active"}));
      });
      tbody.querySelectorAll(".btn-delete").forEach((b) => {
        b.addEventListener("click", () => { if(confirm(I18n.t("settings.confirm_delete"))) socket.emit("admin_delete_user",{id:parseInt(b.dataset.id)}); });
      });
    });
    socket.on("error", (data) => alert(data.msg));

    // Key binding table
    const bindTbody = el("bind-table-body");
    if (bindTbody) {
      KeyBindingManager.renderBindingsTable(bindTbody);
      el("btn-bindings-reset")?.addEventListener("click", () => {
        KeyBindingManager.resetToDefaults();
        KeyBindingManager.renderBindingsTable(bindTbody);
      });
    }
    // Rebuild bindings table when modules change (sidebar re-render)
    const _origRenderSidebar = _renderSidebar;
    _renderSidebar = function() {
      _origRenderSidebar();
      const tb = el("bind-table-body");
      if (tb) KeyBindingManager.renderBindingsTable(tb);
    };
  })();
}

// ═══════════════════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════════════════

LayoutManager.init();
KeyBindingManager.init();
_checkEmptyState();

// Auto-open panels from last layout (optional — restore last-saved or use a default)
const lastLayout = LayoutManager._layouts["_auto_restore"];
if (lastLayout && lastLayout.panels && lastLayout.panels.length > 0) {
  PanelManager.applyLayout(lastLayout);
  _renderSidebar();
}
