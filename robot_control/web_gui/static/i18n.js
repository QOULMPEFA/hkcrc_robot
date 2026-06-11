/* i18n.js — Internationalization for Robot Control Panel
 *
 * Usage:
 *   HTML:  <span data-i18n="key">original Chinese</span>
 *   JS:    I18n.t("key")    or    I18n.t("key", [arg0, arg1])
 *
 * The HTML textContent is overwritten by I18n.init() and I18n.setLanguage().
 * Keep the Chinese text in data-i18n elements as fallback / readability aid.
 */
const I18n = {
  _lang: "zh",
  _LS_KEY: "robot_lang",

  zh: {},
  en: {},

  /* ── Lookup ──────────────────────────────────────────────────────────── */

  t(key, params) {
    let text = this[this._lang][key];
    if (text === undefined) text = this.zh[key];
    if (text === undefined) return key;
    if (params && params.length) {
      params.forEach((p, i) => { text = text.replace("{" + i + "}", p); });
    }
    return text;
  },

  /* ── Lifecycle ────────────────────────────────────────────────────────── */

  init() {
    const saved = localStorage.getItem(this._LS_KEY);
    if (saved === "en" || saved === "zh") this._lang = saved;
    else if (navigator.language && navigator.language.startsWith("en")) this._lang = "en";
    document.documentElement.lang = this._lang === "en" ? "en" : "zh-CN";
    this._sweepHTML();
    this._renderSwitcher();
  },

  setLanguage(lang) {
    if (lang !== "zh" && lang !== "en") return;
    this._lang = lang;
    localStorage.setItem(this._LS_KEY, lang);
    document.documentElement.lang = lang === "en" ? "en" : "zh-CN";
    this._sweepHTML();
    if (typeof socket !== "undefined") socket.emit("set_language", { lang });
    if (typeof _renderSidebar === "function") _renderSidebar();
    if (typeof PanelManager !== "undefined") {
      const layout = PanelManager.getLayout();
      PanelManager.closeAll();
      if (layout && layout.panels && layout.panels.length) PanelManager.applyLayout(layout);
    }
  },

  /* ── HTML sweeper ────────────────────────────────────────────────────── */

  _sweepHTML() {
    document.querySelectorAll("[data-i18n]").forEach(el => {
      el.textContent = this.t(el.dataset.i18n);
    });
    document.querySelectorAll("[data-i18n-title]").forEach(el => {
      el.title = this.t(el.dataset.i18nTitle);
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach(el => {
      el.placeholder = this.t(el.dataset.i18nPlaceholder);
    });
    document.querySelectorAll("[data-i18n-value]").forEach(el => {
      el.value = this.t(el.dataset.i18nValue);
    });
  },

  /* ── Language switcher UI ────────────────────────────────────────────── */

  _renderSwitcher() {
    const prev = document.getElementById("lang-switcher");
    if (prev) prev.remove();

    const sel = document.createElement("select");
    sel.id = "lang-switcher";
    sel.className = "lang-switcher";
    sel.innerHTML = '<option value="zh">中文</option><option value="en">English</option>';
    sel.value = this._lang;

    sel.addEventListener("change", () => this.setLanguage(sel.value));

    const header = document.querySelector("header");
    if (header) {
      const right = header.querySelector(".header-user");
      if (right) right.insertBefore(sel, right.firstChild);
      else header.appendChild(sel);
    }
  }
};

/* ─────────────────────────────────────────────────────────────────────────
 * Translation dictionaries
 * ─────────────────────────────────────────────────────────────────────── */

/* === header / chrome === */
I18n.zh["header.connected"]    = "已连接";
I18n.zh["header.disconnected"] = "未连接";
I18n.zh["header.select_layout"] = "— 选择布局 —";
I18n.zh["header.switch_layout"] = "切换布局";
I18n.zh["header.save_layout"]   = "保存当前布局";
I18n.zh["header.delete_layout"] = "删除当前布局";
I18n.zh["header.estop"]     = "EMERGENCY STOP";
I18n.zh["header.reboot"]    = "重启";
I18n.zh["header.shutdown"]  = "关机";
I18n.zh["header.settings"]  = "系统设置";
I18n.zh["header.logout"]    = "登出";
I18n.zh["header.save"]      = "保存";
I18n.zh["header.delete"]    = "删除";
I18n.zh["header.close"]     = "关闭";
I18n.zh["header.cancel"]    = "取消";
I18n.zh["header.confirm"]   = "确认";
I18n.zh["header.role_viewer"]   = "观察员";
I18n.zh["header.role_operator"] = "操作员";
I18n.zh["header.role_admin"]    = "管理员";

I18n.en["header.connected"]    = "Connected";
I18n.en["header.disconnected"] = "Disconnected";
I18n.en["header.select_layout"] = "— Select Layout —";
I18n.en["header.switch_layout"] = "Switch Layout";
I18n.en["header.save_layout"]   = "Save Layout";
I18n.en["header.delete_layout"] = "Delete Layout";
I18n.en["header.estop"]     = "EMERGENCY STOP";
I18n.en["header.reboot"]    = "Reboot";
I18n.en["header.shutdown"]  = "Shutdown";
I18n.en["header.settings"]  = "Settings";
I18n.en["header.logout"]    = "Logout";
I18n.en["header.save"]      = "Save";
I18n.en["header.delete"]    = "Delete";
I18n.en["header.close"]     = "Close";
I18n.en["header.cancel"]    = "Cancel";
I18n.en["header.confirm"]   = "Confirm";
I18n.en["header.role_viewer"]   = "Viewer";
I18n.en["header.role_operator"] = "Operator";
I18n.en["header.role_admin"]    = "Admin";

/* === sidebar === */
I18n.zh["sidebar.hardware_modules"] = "硬件模块";
I18n.zh["sidebar.quick_actions"]    = "快捷操作";
I18n.zh["sidebar.open_all"]         = "全部打开";
I18n.zh["sidebar.close_all"]        = "全部关闭";
I18n.zh["sidebar.sdk_stub"]         = "SDK待接入";
I18n.zh["sidebar.empty_hint"]       = "点击右侧硬件模块打开控制面板";

I18n.en["sidebar.hardware_modules"] = "Hardware Modules";
I18n.en["sidebar.quick_actions"]    = "Quick Actions";
I18n.en["sidebar.open_all"]         = "Open All";
I18n.en["sidebar.close_all"]        = "Close All";
I18n.en["sidebar.sdk_stub"]         = "SDK Pending";
I18n.en["sidebar.empty_hint"]       = "Click a module on the right to open its panel";

/* === module display names === */
I18n.zh["module.camera"] = "摄像头";
I18n.zh["module.ugv"]    = "UGV 底盘";
I18n.zh["module.lift"]   = "升降平台";
I18n.zh["module.arm"]    = "机械臂";
I18n.zh["module.hand"]   = "灵巧手";
I18n.zh["module.motor"]  = "电机控制";

I18n.en["module.camera"] = "Camera";
I18n.en["module.ugv"]    = "UGV Chassis";
I18n.en["module.lift"]   = "Lift Platform";
I18n.en["module.arm"]    = "Robot Arm";
I18n.en["module.hand"]   = "Dexterous Hand";
I18n.en["module.motor"]  = "Motor Control";

/* === camera panel === */
I18n.zh["camera.offline"]  = "摄像头未连接";
I18n.zh["camera.loading"]  = "加载中...";
I18n.zh["camera.status"]   = "MJPEG";

I18n.en["camera.offline"]  = "Camera Offline";
I18n.en["camera.loading"]  = "Loading...";
I18n.en["camera.status"]   = "MJPEG";

/* === UGV panel === */
I18n.zh["ugv.connection"]    = "连接：";
I18n.zh["ugv.battery"]       = "电池：";
I18n.zh["ugv.soc"]           = "SOC：";
I18n.zh["ugv.linear_vel"]    = "线速度：";
I18n.zh["ugv.angular_vel"]   = "角速度：";
I18n.zh["ugv.linear"]        = "线速度";
I18n.zh["ugv.angular"]       = "角速度";
I18n.zh["ugv.vehicle_state"] = "车辆状态:";
I18n.zh["ugv.control_mode"]  = "控制模式:";
I18n.zh["ugv.enable_can"]    = "使能 CAN 模式";
I18n.zh["ugv.clear_fault"]   = "清除故障";
I18n.zh["ugv.restart_can"]   = "重启 CAN";
I18n.zh["ugv.restarting"]    = "重启中...";
I18n.zh["ugv.can_restarted"] = "CAN 已重启";
I18n.zh["ugv.restart_failed"] = "重启失败";
I18n.zh["ugv.connected"]     = "✓ 已连接";
I18n.zh["ugv.disconnected"]  = "✗ 未连接";
I18n.zh["ugv.enabled"]       = "✓ 已使能 (CAN指令)";
I18n.zh["ugv.standby_warn"]  = "⚠ 底盘处于<b>待机模式</b> — 点击<em>「使能 CAN 模式」</em>后可控制";
I18n.zh["ugv.wasd_hint"]     = "⌨ 键盘: <b>W</b>前 <b>S</b>后 <b>A</b>左 <b>D</b>右 — 组合键支持斜面移动";
I18n.zh["ugv.log_label"]     = "运行日志";
I18n.zh["ugv.log_toggle"]    = "运行日志 ▾";
I18n.zh["ugv.state.normal"]         = "正常";
I18n.zh["ugv.state.estop"]          = "急停";
I18n.zh["ugv.state.fault"]          = "异常";
I18n.zh["ugv.mode.standby"]         = "待机";
I18n.zh["ugv.mode.can"]             = "CAN指令";
I18n.zh["ugv.mode.uart"]            = "UART";
I18n.zh["ugv.mode.remote"]          = "遥控器";

I18n.en["ugv.connection"]    = "Connection:";
I18n.en["ugv.battery"]       = "Battery:";
I18n.en["ugv.soc"]           = "SOC:";
I18n.en["ugv.linear_vel"]    = "Linear:";
I18n.en["ugv.angular_vel"]   = "Angular:";
I18n.en["ugv.linear"]        = "Linear Speed";
I18n.en["ugv.angular"]       = "Angular Speed";
I18n.en["ugv.vehicle_state"] = "Vehicle State:";
I18n.en["ugv.control_mode"]  = "Control Mode:";
I18n.en["ugv.enable_can"]    = "Enable CAN Mode";
I18n.en["ugv.clear_fault"]   = "Clear Fault";
I18n.en["ugv.restart_can"]   = "Restart CAN";
I18n.en["ugv.restarting"]    = "Restarting...";
I18n.en["ugv.can_restarted"] = "CAN Restarted";
I18n.en["ugv.restart_failed"] = "Restart Failed";
I18n.en["ugv.connected"]     = "✓ Connected";
I18n.en["ugv.disconnected"]  = "✗ Disconnected";
I18n.en["ugv.enabled"]       = "✓ Enabled (CAN)";
I18n.en["ugv.standby_warn"]  = "⚠ Chassis in <b>standby mode</b> — click <em>Enable CAN Mode</em> to take control";
I18n.en["ugv.wasd_hint"]     = "⌨ Keyboard: <b>W</b>Forward <b>S</b>Back <b>A</b>Left <b>D</b>Right — combo keys for diagonal";
I18n.en["ugv.log_label"]     = "Log";
I18n.en["ugv.log_toggle"]    = "Log ▾";
I18n.en["ugv.state.normal"]  = "Normal";
I18n.en["ugv.state.estop"]   = "E-Stop";
I18n.en["ugv.state.fault"]   = "Fault";
I18n.en["ugv.mode.standby"]  = "Standby";
I18n.en["ugv.mode.can"]      = "CAN Cmd";
I18n.en["ugv.mode.uart"]     = "UART";
I18n.en["ugv.mode.remote"]   = "Remote";

/* === lift panel === */
I18n.zh["lift.height"]       = "高度：";
I18n.zh["lift.velocity"]     = "速度：";
I18n.zh["lift.torque"]       = "转矩：";
I18n.zh["lift.target_mm"]    = "目标高度 (mm)";
I18n.zh["lift.speed_mm_s"]   = "速度 (mm/s)";
I18n.zh["lift.move"]         = "移动";
I18n.zh["lift.stop"]         = "停止";
I18n.zh["lift.home"]         = "回零";
I18n.zh["lift.reset_fault"]  = "清除故障";
I18n.zh["lift.drive"]        = "驱动器:";
I18n.zh["lift.sw"]           = "SW:";
I18n.zh["lift.fault"]        = "FAULT";
I18n.zh["lift.log_label"]    = "运行日志";
I18n.zh["lift.log_toggle"]   = "运行日志 ▾";
I18n.zh["lift.online"]       = "✓ 在线";
I18n.zh["lift.offline"]      = "✗ 离线";
I18n.zh["lift.fault_status"] = "⚠ 故障";

I18n.en["lift.height"]       = "Height:";
I18n.en["lift.velocity"]     = "Speed:";
I18n.en["lift.torque"]       = "Torque:";
I18n.en["lift.target_mm"]    = "Target (mm)";
I18n.en["lift.speed_mm_s"]   = "Speed (mm/s)";
I18n.en["lift.move"]         = "Move";
I18n.en["lift.stop"]         = "Stop";
I18n.en["lift.home"]         = "Home";
I18n.en["lift.reset_fault"]  = "Clear Fault";
I18n.en["lift.drive"]        = "Drive:";
I18n.en["lift.sw"]           = "SW:";
I18n.en["lift.fault"]        = "FAULT";
I18n.en["lift.log_label"]    = "Log";
I18n.en["lift.log_toggle"]   = "Log ▾";
I18n.en["lift.online"]       = "✓ Online";
I18n.en["lift.offline"]      = "✗ Offline";
I18n.en["lift.fault_status"] = "⚠ Fault";

/* === arm panel === */
I18n.zh["arm.connection"]      = "连接：";
I18n.zh["arm.enabled"]         = "使能：";
I18n.zh["arm.moving"]          = "运动中：";
I18n.zh["arm.mode"]            = "模式：";
I18n.zh["arm.safety"]          = "安全：";
I18n.zh["arm.speed_pct"]       = "运动速度";
I18n.zh["arm.collision"]       = "碰撞检测";
I18n.zh["arm.enable"]          = "使能";
I18n.zh["arm.disable"]         = "断开动力";
I18n.zh["arm.home"]            = "回零";
I18n.zh["arm.stop"]            = "停止";
I18n.zh["arm.reset"]           = "解除保护性停止";
I18n.zh["arm.connected"]       = "已连接";
I18n.zh["arm.disconnected"]    = "未连接";
I18n.zh["arm.enabled_yes"]     = "已使能";
I18n.zh["arm.enabled_no"]      = "未使能";
I18n.zh["arm.moving_yes"]      = "运动中";
I18n.zh["arm.moving_no"]       = "静止";
I18n.zh["arm.fault"]           = "FAULT";
I18n.zh["arm.log_label"]       = "运行日志";
I18n.zh["arm.log_toggle"]      = "运行日志 ▾";
I18n.zh["arm.actual_prefix"]   = "实 ";

I18n.en["arm.connection"]      = "Connection:";
I18n.en["arm.enabled"]         = "Enabled:";
I18n.en["arm.moving"]          = "Moving:";
I18n.en["arm.mode"]            = "Mode:";
I18n.en["arm.safety"]          = "Safety:";
I18n.en["arm.speed_pct"]       = "Speed";
I18n.en["arm.collision"]       = "Collision Sensitivity";
I18n.en["arm.enable"]          = "Enable";
I18n.en["arm.disable"]         = "Disable Power";
I18n.en["arm.home"]            = "Home";
I18n.en["arm.stop"]            = "Stop";
I18n.en["arm.reset"]           = "Reset Protective Stop";
I18n.en["arm.connected"]       = "Connected";
I18n.en["arm.disconnected"]    = "Disconnected";
I18n.en["arm.enabled_yes"]     = "Enabled";
I18n.en["arm.enabled_no"]      = "Disabled";
I18n.en["arm.moving_yes"]      = "Moving";
I18n.en["arm.moving_no"]       = "Idle";
I18n.en["arm.fault"]           = "FAULT";
I18n.en["arm.log_label"]       = "Log";
I18n.en["arm.log_toggle"]      = "Log ▾";
I18n.en["arm.actual_prefix"]   = "Act ";

/* === hand panel === */
I18n.zh["hand.status"]       = "状态：";
I18n.zh["hand.force"]        = "夹持力：";
I18n.zh["hand.open"]         = "张开";
I18n.zh["hand.close"]        = "握拳";
I18n.zh["hand.pinch"]        = "捏取";
I18n.zh["hand.send"]         = "发送";
I18n.zh["hand.enabled"]      = "已使能";
I18n.zh["hand.disabled"]     = "未使能";
I18n.zh["hand.finger.0"]     = "拇指";
I18n.zh["hand.finger.1"]     = "食指";
I18n.zh["hand.finger.2"]     = "中指";
I18n.zh["hand.finger.3"]     = "无名指";
I18n.zh["hand.finger.4"]     = "小指";

I18n.en["hand.status"]       = "Status:";
I18n.en["hand.force"]        = "Grip Force:";
I18n.en["hand.open"]         = "Open";
I18n.en["hand.close"]        = "Fist";
I18n.en["hand.pinch"]        = "Pinch";
I18n.en["hand.send"]         = "Send";
I18n.en["hand.enabled"]      = "Enabled";
I18n.en["hand.disabled"]     = "Disabled";
I18n.en["hand.finger.0"]     = "Thumb";
I18n.en["hand.finger.1"]     = "Index";
I18n.en["hand.finger.2"]     = "Middle";
I18n.en["hand.finger.3"]     = "Ring";
I18n.en["hand.finger.4"]     = "Pinky";

/* === motor panel === */
I18n.zh["motor.connected"]   = "已连接";
I18n.zh["motor.mock"]        = "mock";
I18n.zh["motor.direction"]   = "方向：";
I18n.zh["motor.forward"]     = "前进";
I18n.zh["motor.backward"]    = "后退";
I18n.zh["motor.left"]        = "左转";
I18n.zh["motor.right"]       = "右转";
I18n.zh["motor.stop"]        = "停止";

I18n.en["motor.connected"]   = "Connected";
I18n.en["motor.mock"]        = "mock";
I18n.en["motor.direction"]   = "Direction:";
I18n.en["motor.forward"]     = "Forward";
I18n.en["motor.backward"]    = "Backward";
I18n.en["motor.left"]        = "Turn Left";
I18n.en["motor.right"]       = "Turn Right";
I18n.en["motor.stop"]        = "Stop";

/* === error / permission messages === */
I18n.zh["error.login_required"]    = "请先登录";
I18n.zh["error.permission_denied"] = "权限不足";
I18n.zh["error.cannot_modify_self"] = "不能修改自己的角色";
I18n.zh["error.cannot_delete_self"] = "不能删除自己的账号";
I18n.zh["error.unknown_action"]    = "未知操作: {0}";

I18n.en["error.login_required"]    = "Please log in";
I18n.en["error.permission_denied"] = "Permission denied";
I18n.en["error.cannot_modify_self"] = "Cannot modify your own role";
I18n.en["error.cannot_delete_self"] = "Cannot delete your own account";
I18n.en["error.unknown_action"]    = "Unknown action: {0}";

/* === settings modal === */
I18n.zh["settings.title"]            = "系统设置";
I18n.zh["settings.lift_section"]     = "升降平台 — 零点 & 限位";
I18n.zh["settings.zero_offset"]      = "零点偏移：";
I18n.zh["settings.current_limits"]   = "当前限位：";
I18n.zh["settings.min_height"]       = "最小高度 (mm)";
I18n.zh["settings.max_height"]       = "最大高度 (mm)";
I18n.zh["settings.set_zero"]         = "将当前位置设为零点";
I18n.zh["settings.apply_limits"]     = "应用限位";
I18n.zh["settings.camera_section"]   = "摄像头 — 自动关闭";
I18n.zh["settings.camera_timeout"]   = "无活动超时 (分钟)";
I18n.zh["settings.save_timeout"]     = "保存";
I18n.zh["settings.user_management"]  = "用户管理";
I18n.zh["settings.th_username"]      = "用户名";
I18n.zh["settings.th_role"]          = "角色";
I18n.zh["settings.th_status"]        = "状态";
I18n.zh["settings.th_actions"]       = "操作";
I18n.zh["settings.user_active"]      = "已激活";
I18n.zh["settings.user_pending"]     = "待审批";
I18n.zh["settings.approve"]          = "批准";
I18n.zh["settings.delete_user"]      = "删除";
I18n.zh["settings.confirm_delete"]   = "确认删除该用户？";
I18n.zh["settings.role_viewer"]      = "观察员";
I18n.zh["settings.role_operator"]    = "操作员";
I18n.zh["settings.role_admin"]       = "管理员";

I18n.en["settings.title"]            = "Settings";
I18n.en["settings.lift_section"]     = "Lift Platform — Zero & Limits";
I18n.en["settings.zero_offset"]      = "Zero Offset:";
I18n.en["settings.current_limits"]   = "Current Limits:";
I18n.en["settings.min_height"]       = "Min Height (mm)";
I18n.en["settings.max_height"]       = "Max Height (mm)";
I18n.en["settings.set_zero"]         = "Set Current as Zero";
I18n.en["settings.apply_limits"]     = "Apply Limits";
I18n.en["settings.camera_section"]   = "Camera — Auto Shutdown";
I18n.en["settings.camera_timeout"]   = "Inactivity Timeout (min)";
I18n.en["settings.save_timeout"]     = "Save";
I18n.en["settings.user_management"]  = "User Management";
I18n.en["settings.th_username"]      = "Username";
I18n.en["settings.th_role"]          = "Role";
I18n.en["settings.th_status"]        = "Status";
I18n.en["settings.th_actions"]       = "Actions";
I18n.en["settings.user_active"]      = "Active";
I18n.en["settings.user_pending"]     = "Pending";
I18n.en["settings.approve"]          = "Approve";
I18n.en["settings.delete_user"]      = "Delete";
I18n.en["settings.confirm_delete"]   = "Confirm delete user?";
I18n.en["settings.role_viewer"]      = "Viewer";
I18n.en["settings.role_operator"]    = "Operator";
I18n.en["settings.role_admin"]       = "Admin";

/* === power confirmation === */
I18n.zh["power.confirm_title"]   = "确认操作";
I18n.zh["power.reboot_title"]    = "确认重启";
I18n.zh["power.reboot_msg"]      = "确定要重启系统吗？重启后请等待约 30 秒重新连接。";
I18n.zh["power.shutdown_title"]  = "确认关机";
I18n.zh["power.shutdown_msg"]    = "确定要关闭系统吗？关机后需手动上电才能恢复。";
I18n.zh["power.reboot_sent"]     = "命令已发送，系统即将重启。";
I18n.zh["power.shutdown_sent"]   = "命令已发送，系统即将关机。";

I18n.en["power.confirm_title"]   = "Confirm Action";
I18n.en["power.reboot_title"]    = "Confirm Reboot";
I18n.en["power.reboot_msg"]      = "Are you sure you want to reboot? Reconnect after ~30 seconds.";
I18n.en["power.shutdown_title"]  = "Confirm Shutdown";
I18n.en["power.shutdown_msg"]    = "Are you sure you want to shut down? Manual power-on will be required.";
I18n.en["power.reboot_sent"]     = "Command sent, system will reboot shortly.";
I18n.en["power.shutdown_sent"]   = "Command sent, system will shut down shortly.";

/* === layout manager === */
I18n.zh["layout.prompt_name"]   = "布局名称：";
I18n.zh["layout.default_name"]  = "默认布局";
I18n.zh["layout.confirm_delete"] = "删除布局 \"{0}\"？";

I18n.en["layout.prompt_name"]   = "Layout name:";
I18n.en["layout.default_name"]  = "Default Layout";
I18n.en["layout.confirm_delete"] = "Delete layout \"{0}\"?";

/* === misc === */
I18n.zh["misc.last_update"]    = "最后更新：";
I18n.zh["misc.loading"]        = "加载中...";
I18n.zh["misc.no_ui"]          = "模块 \"{0}\" 无 UI";
I18n.zh["misc.enter_valid_min"] = "请输入有效的分钟数";
I18n.zh["misc.min_less_max"]   = "最小高度必须小于最大高度";

I18n.en["misc.last_update"]    = "Last update:";
I18n.en["misc.loading"]        = "Loading...";
I18n.en["misc.no_ui"]          = "Module \"{0}\" has no UI";
I18n.en["misc.enter_valid_min"] = "Please enter a valid number of minutes";
I18n.en["misc.min_less_max"]   = "Min height must be less than max height";

/* === status === */
I18n.zh["status.online"]  = "✓ 在线";
I18n.zh["status.offline"] = "✗ 离线";
I18n.zh["status.fault"]   = "⚠ 故障";

I18n.en["status.online"]  = "✓ Online";
I18n.en["status.offline"] = "✗ Offline";
I18n.en["status.fault"]   = "⚠ Fault";

/* === auth pages (login / register) === */
I18n.zh["auth.title_login"]    = "登录";
I18n.zh["auth.title_register"] = "注册账号";
I18n.zh["auth.username"]       = "用户名";
I18n.zh["auth.password"]       = "密码";
I18n.zh["auth.confirm_password"] = "确认密码";
I18n.zh["auth.login_btn"]      = "登录";
I18n.zh["auth.register_btn"]   = "注册";
I18n.zh["auth.no_account"]     = "没有账号？";
I18n.zh["auth.has_account"]    = "已有账号？";
I18n.zh["auth.register_link"]  = "注册";
I18n.zh["auth.login_link"]     = "登录";
I18n.zh["auth.back_to_login"]  = "返回登录";
I18n.zh["auth.pending_msg"]    = "账号正在等待管理员审批，请稍后再试。";
I18n.zh["auth.relogin_msg"]    = "您的账号已在其他设备登录，当前会话已失效。";
I18n.zh["auth.register_ok"]    = "注册成功！您的账号正在等待管理员审批，审批通过后即可登录。";
I18n.zh["auth.username_exists"]    = "用户名已存在";
I18n.zh["auth.username_password_empty"] = "用户名和密码不能为空";
I18n.zh["auth.username_length"]     = "用户名长度须在 2-32 个字符之间";
I18n.zh["auth.password_mismatch"]   = "两次输入的密码不一致";
I18n.zh["auth.password_length"]     = "密码长度至少 6 位";
I18n.zh["auth.wrong_credentials"]   = "用户名或密码错误";
I18n.zh["auth.account_disabled"]    = "账号已被禁用";

I18n.en["auth.title_login"]    = "Login";
I18n.en["auth.title_register"] = "Register";
I18n.en["auth.username"]       = "Username";
I18n.en["auth.password"]       = "Password";
I18n.en["auth.confirm_password"] = "Confirm Password";
I18n.en["auth.login_btn"]      = "Login";
I18n.en["auth.register_btn"]   = "Register";
I18n.en["auth.no_account"]     = "No account?";
I18n.en["auth.has_account"]    = "Already have an account?";
I18n.en["auth.register_link"]  = "Register";
I18n.en["auth.login_link"]     = "Login";
I18n.en["auth.back_to_login"]  = "Back to Login";
I18n.en["auth.pending_msg"]    = "Your account is pending admin approval. Please try again later.";
I18n.en["auth.relogin_msg"]    = "Your account was logged in on another device. This session is now invalid.";
I18n.en["auth.register_ok"]    = "Registration successful! Your account is pending admin approval.";
I18n.en["auth.username_exists"]    = "Username already exists";
I18n.en["auth.username_password_empty"] = "Username and password are required";
I18n.en["auth.username_length"]     = "Username must be 2-32 characters";
I18n.en["auth.password_mismatch"]   = "Passwords do not match";
I18n.en["auth.password_length"]     = "Password must be at least 6 characters";
I18n.en["auth.wrong_credentials"]   = "Incorrect username or password";
I18n.en["auth.account_disabled"]    = "Account has been disabled";
