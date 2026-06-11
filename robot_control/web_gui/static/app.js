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
};
function _modName(meta) { return meta.i18nKey ? I18n.t(meta.i18nKey) : meta.name; }
let ACTIVE_MODULES = [];

const el  = (id) => document.getElementById(id);
const fmt = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");

// ── Socket ───────────────────────────────────────────────────────────────────
I18n.init();
const socket = io({ transports: ["websocket"], query: { lang: I18n._lang } });

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
    if (!isOpen) {
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
    default:       body.innerHTML = `<div style="color:var(--muted);padding:20px;text-align:center">${I18n.t("misc.no_ui", [module])}</div>`; break;
  }
}

function _cleanupPanel(panelId, module) {
  // Stop keyboard WASD when the UGV panel is closed
  if (module === "ugv" && _ugvKeysActivePanel === panelId) {
    _ugvKeysStop();
  }
}

// Stop WASD when window loses focus (safety)
window.addEventListener("blur", () => { if (_ugvKeysActivePanel) _ugvKeysStop(); });

// ── Camera Panel ──────────────────────────────────────────────────────────────

function _renderCameraPanel(panelId, body) {
  body.innerHTML = `
    <div class="camera-wrap" id="${panelId}-cam-wrap">
      <img id="${panelId}-cam-img" src="/stream" alt="camera" />
      <div class="camera-error" id="${panelId}-cam-err" style="display:none">${I18n.t("camera.offline")}</div>
    </div>
    <div class="cam-status-line" id="${panelId}-cam-status">${I18n.t("camera.status")}</div>
  `;

  const img = el(panelId + "-cam-img");
  const err = el(panelId + "-cam-err");

  if (img) {
    img.addEventListener("error", () => { img.style.display = "none"; if (err) err.style.display = "flex"; });
    img.addEventListener("load", () => { img.style.display = "block"; if (err) err.style.display = "none"; });
  }
}

// ── UGV Panel ─────────────────────────────────────────────────────────────────
// ── Keyboard WASD state ──────────────────────────────────────────────────────
const _ugvKeys = { w: false, a: false, s: false, d: false };
let _ugvKeysInterval = null;
let _ugvKeysActivePanel = null;

function _ugvKeysStart(panelId) {
  _ugvKeysActivePanel = panelId;
  if (_ugvKeysInterval) return;
  _ugvKeysInterval = setInterval(() => {
    if (!_ugvKeysActivePanel) return;
    let lin = 0, ang = 0;
    if (_ugvKeys.w) lin += 1;
    if (_ugvKeys.s) lin -= 1;
    if (_ugvKeys.a) ang += 1;
    if (_ugvKeys.d) ang -= 1;
    if (lin !== 0 || ang !== 0) {
      const slLin  = el(_ugvKeysActivePanel + "-sl-linear");
      const slAng  = el(_ugvKeysActivePanel + "-sl-angular");
      lin *= parseFloat(slLin?.value || 0.5);
      ang *= parseFloat(slAng?.value || 0.5);
      socket.emit("ugv_velocity", { linear: lin, angular: ang });
    } else {
      socket.emit("ugv_stop");
    }
  }, 80);
}

function _ugvKeysStop() {
  if (_ugvKeysInterval) { clearInterval(_ugvKeysInterval); _ugvKeysInterval = null; }
  _ugvKeysActivePanel = null;
  _ugvKeys.w = _ugvKeys.a = _ugvKeys.s = _ugvKeys.d = false;
}

document.addEventListener("keydown", (e) => {
  if (!_ugvKeysActivePanel) return;
  const tag = document.activeElement?.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
  const k = e.key.toLowerCase();
  if (k in _ugvKeys) { e.preventDefault(); _ugvKeys[k] = true; }
});

document.addEventListener("keyup", (e) => {
  const k = e.key.toLowerCase();
  if (k in _ugvKeys) { e.preventDefault(); _ugvKeys[k] = false; }
});


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
      <button class="btn-primary ctrl"    id="${panelId}-ugv-enable">${t("ugv.enable_can")}</button>
      <button class="btn-warn ctrl"       id="${panelId}-ugv-reset">${t("ugv.clear_fault")}</button>
      <button class="btn-secondary"       id="${panelId}-restart-can">${t("ugv.restart_can")}</button>
    </div>
    <div id="${panelId}-ugv-disabled-msg" style="text-align:center;padding:20px;color:var(--warn);font-size:14px">
      ${t("ugv.standby_warn")}
    </div>
    <div id="${panelId}-ugv-controls" style="display:none">
      <div class="dpad">
        <button class="dpad-btn ctrl" data-l="1.0"  data-a="0.0">▲</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="1.0">◄</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="0.0">■</button>
        <button class="dpad-btn ctrl" data-l="0.0"  data-a="-1.0">►</button>
        <button class="dpad-btn ctrl" data-l="-1.0" data-a="0.0">▼</button>
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
  if (btnEn)  btnEn.addEventListener("click",  () => { socket.emit("ugv_enable"); _ugvKeysStart(panelId); });
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
      <button class="btn-primary ctrl"   id="${panelId}-lift-move">${t("lift.move")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-lift-stop">${t("lift.stop")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-lift-home">${t("lift.home")}</button>
      <button class="btn-warn ctrl"      id="${panelId}-lift-reset">${t("lift.reset_fault")}</button>
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
      <button class="btn-primary ctrl"   id="${panelId}-arm-enable">${t("arm.enable")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-arm-disable">${t("arm.disable")}</button>
      <button class="btn-primary ctrl"   id="${panelId}-arm-home">${t("arm.home")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-arm-stop">${t("arm.stop")}</button>
      <button class="btn-warn ctrl"      id="${panelId}-arm-reset">${t("arm.reset")}</button>
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
      <button class="btn-primary ctrl"   id="${panelId}-hand-open">${t("hand.open")}</button>
      <button class="btn-primary ctrl"   id="${panelId}-hand-close">${t("hand.close")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-hand-pinch">${t("hand.pinch")}</button>
      <button class="btn-secondary ctrl" id="${panelId}-hand-send">${t("hand.send")}</button>
    </div>
  `;

  const FINGERS = ["hand.finger.0", "hand.finger.1", "hand.finger.2", "hand.finger.3", "hand.finger.4"];
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
      });
    });
  }

  const btnOpen  = el(panelId + "-hand-open");
  const btnClose = el(panelId + "-hand-close");
  const btnPinch = el(panelId + "-hand-pinch");
  const btnSend  = el(panelId + "-hand-send");
  if (btnOpen)  btnOpen.addEventListener("click",  () => socket.emit("hand_open"));
  if (btnClose) btnClose.addEventListener("click", () => socket.emit("hand_close"));
  if (btnPinch) btnPinch.addEventListener("click", () => socket.emit("hand_pinch"));
  if (btnSend)  btnSend.addEventListener("click",  () => {
    const positions = FINGERS.map((_, i) => {
      const inp = el(panelId + `-hand-f${i}`); return inp ? parseFloat(inp.value) : 0;
    });
    socket.emit("hand_set_fingers", { positions, speed_pct: 50 });
  });
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
      <button class="dpad-btn ctrl" data-dir="forward">▲</button>
      <button class="dpad-btn ctrl" data-dir="left">◄</button>
      <button class="dpad-btn ctrl" data-dir="stop">■</button>
      <button class="dpad-btn ctrl" data-dir="right">►</button>
      <button class="dpad-btn ctrl" data-dir="backward">▼</button>
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

function _updateMotorState(panelId, s) {
  _maybeSet(el(panelId + "-motor-connected"), s.connected ? I18n.t("motor.connected") : I18n.t("motor.mock"));
  const dirEl = el(panelId + "-motor-direction");
  if (dirEl) {
    const labels = { forward: I18n.t("motor.forward"), backward: I18n.t("motor.backward"), left: I18n.t("motor.left"), right: I18n.t("motor.right"), stop: I18n.t("motor.stop") };
    dirEl.textContent = labels[s.direction] || s.direction;
    dirEl.style.color = s.direction !== "stop" ? "var(--accent2)" : "var(--muted)";
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
        break;
      case "motor":
        _updateMotorState(panelId, modState);
        PanelManager.updateStatus("motor", modState.connected, false);
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
  // Keyboard WASD: start when enabled, stop when disabled
  if (enabled && PanelManager.isOpen("ugv")) {
    if (_ugvKeysActivePanel !== panelId) _ugvKeysStart(panelId);
  } else if (_ugvKeysActivePanel === panelId) {
    _ugvKeysStop();
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
  if (s.finger_positions) {
    s.finger_positions.forEach((pos, i) => {
      const sl = el(panelId + `-hand-f${i}`);
      const val = el(panelId + `-hand-f${i}-val`);
      if (sl && !sl.matches(":active")) sl.value = pos;
      if (val) val.textContent = fmt(pos * 100, 0) + "%";
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

    socket.on("settings_data", (data) => {
      if (data.camera_timeout_min) {
        const inp = el("camera-timeout"); if (inp) inp.value = data.camera_timeout_min;
      }
    });
    socket.on("settings_ack", (data) => {
      if (data.key === "camera_timeout_min") {
        const inp = el("camera-timeout"); if (inp) inp.value = data.value;
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
  })();
}

// ═══════════════════════════════════════════════════════════════════════════════
// Init
// ═══════════════════════════════════════════════════════════════════════════════

LayoutManager.init();
_checkEmptyState();

// Auto-open panels from last layout (optional — restore last-saved or use a default)
const lastLayout = LayoutManager._layouts["_auto_restore"];
if (lastLayout && lastLayout.panels && lastLayout.panels.length > 0) {
  PanelManager.applyLayout(lastLayout);
  _renderSidebar();
}
