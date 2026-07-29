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
I18n.zh["header.page_title"]        = "机器人控制面板 | Robot Control Panel";
I18n.zh["header.panel_title"]       = "机器人控制面板";
I18n.zh["header.robot_type_tag"]    = "机器人";
I18n.zh["header.hardware_summary"]  = "";
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
I18n.zh["header.apply"]     = "应用";
I18n.zh["header.role_viewer"]   = "观察员";
I18n.zh["header.role_operator"] = "操作员";
I18n.zh["header.role_admin"]    = "管理员";

I18n.en["header.page_title"]        = "Robot Control Panel | 机器人控制面板";
I18n.en["header.panel_title"]       = "Robot Control Panel";
I18n.en["header.robot_type_tag"]    = "Robot";
I18n.en["header.hardware_summary"]  = "";
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
I18n.en["header.apply"]     = "Apply";
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
I18n.zh["module.servo"]  = "总线舵机";
I18n.zh["module.gimbal"] = "云台控制";
I18n.zh["module.joint"]  = "关节控制";
I18n.zh["module.drive"]  = "驱动电机";
I18n.zh["module.demo"]   = "演示序列";
I18n.zh["module.teleop"] = "XR 遥操作";

I18n.en["module.camera"] = "Camera";
I18n.en["module.ugv"]    = "UGV Chassis";
I18n.en["module.lift"]   = "Lift Platform";
I18n.en["module.arm"]    = "Robot Arm";
I18n.en["module.hand"]   = "Dexterous Hand";
I18n.en["module.motor"]  = "Motor Control";
I18n.en["module.servo"]  = "Bus Servo";
I18n.en["module.gimbal"] = "Gimbal";
I18n.en["module.joint"]  = "Joint";
I18n.en["module.drive"]  = "Drive Motor";
I18n.en["module.demo"]   = "Demo Sequence";
I18n.en["module.teleop"] = "XR Teleop";

/* === teleop panel === */
I18n.zh["teleop.xr_status"]    = "XR:";
I18n.zh["teleop.active"]       = "遥操作:";
I18n.zh["teleop.tracking"]     = "跟踪中:";
I18n.zh["teleop.calibrated"]   = "已标定:";
I18n.zh["teleop.position"]     = "位置";
I18n.zh["teleop.trigger"]      = "扳机:";
I18n.zh["teleop.enable"]       = "启用遥操作";
I18n.zh["teleop.disable"]      = "禁用遥操作";
I18n.zh["teleop.calibrate"]    = "标定参考位";
I18n.zh["teleop.toggle_log"]   = "数据记录";
I18n.zh["teleop.active_yes"]   = "已启用";
I18n.zh["teleop.active_no"]    = "未启用";
I18n.zh["teleop.tracking_yes"] = "跟踪中";
I18n.zh["teleop.tracking_no"]  = "待机";
I18n.zh["teleop.calib_yes"]    = "已标定";
I18n.zh["teleop.calib_no"]     = "未标定";
I18n.zh["teleop.target"]       = "目 ";
I18n.zh["teleop.actual"]       = "实 ";

I18n.en["teleop.xr_status"]    = "XR:";
I18n.en["teleop.active"]       = "Teleop:";
I18n.en["teleop.tracking"]     = "Tracking:";
I18n.en["teleop.calibrated"]   = "Calibrated:";
I18n.en["teleop.position"]     = "Position";
I18n.en["teleop.trigger"]      = "Trigger:";
I18n.en["teleop.enable"]       = "Enable Teleop";
I18n.en["teleop.disable"]      = "Disable Teleop";
I18n.en["teleop.calibrate"]    = "Calibrate Ref";
I18n.en["teleop.toggle_log"]   = "Data Log";
I18n.en["teleop.active_yes"]   = "Active";
I18n.en["teleop.active_no"]    = "Inactive";
I18n.en["teleop.tracking_yes"] = "Tracking";
I18n.en["teleop.tracking_no"]  = "Standby";
I18n.en["teleop.calib_yes"]    = "Yes";
I18n.en["teleop.calib_no"]     = "No";
I18n.en["teleop.target"]       = "Tgt ";
I18n.en["teleop.actual"]       = "Act ";

/* === teleop gimbal head tracking === */
I18n.zh["teleop.gimbal_section"]      = "头部跟踪 (云台)";
I18n.zh["teleop.gimbal_status"]       = "云台:";
I18n.zh["teleop.gimbal_tracking"]     = "跟踪:";
I18n.zh["teleop.gimbal_calibrated"]   = "已标定:";
I18n.zh["teleop.gimbal_head_yaw"]     = "头部偏航";
I18n.zh["teleop.gimbal_head_pitch"]   = "头部俯仰";
I18n.zh["teleop.gimbal_enable"]       = "启用头部跟踪";
I18n.zh["teleop.gimbal_disable"]      = "禁用头部跟踪";
I18n.zh["teleop.gimbal_calibrate"]    = "标定头部参考位";
I18n.zh["teleop.gimbal_home"]         = "云台归位";
I18n.zh["teleop.gimbal_unavailable"]  = "不可用";
I18n.zh["teleop.gimbal_yaw_gain"]   = "偏航增益";
I18n.zh["teleop.gimbal_pitch_gain"] = "俯仰增益";
I18n.zh["teleop.gimbal_set_gains"]  = "应用增益";
I18n.zh["teleop.gimbal_limits"]     = "限位";
I18n.zh["teleop.gimbal_deadband"]  = "死区";
I18n.zh["teleop.gimbal_smoothing"] = "平滑";
I18n.zh["teleop.gimbal_rate_limit"] = "速率限制";
I18n.zh["teleop.gimbal_apply_config"] = "应用";

I18n.en["teleop.gimbal_section"]      = "Head Tracking (Gimbal)";
I18n.en["teleop.gimbal_status"]       = "Gimbal:";
I18n.en["teleop.gimbal_tracking"]     = "Tracking:";
I18n.en["teleop.gimbal_calibrated"]   = "Calibrated:";
I18n.en["teleop.gimbal_head_yaw"]     = "Head Yaw";
I18n.en["teleop.gimbal_head_pitch"]   = "Head Pitch";
I18n.en["teleop.gimbal_enable"]       = "Enable Head Track";
I18n.en["teleop.gimbal_disable"]      = "Disable Head Track";
I18n.en["teleop.gimbal_calibrate"]    = "Calibrate Head Ref";
I18n.en["teleop.gimbal_home"]         = "Gimbal Home";
I18n.en["teleop.gimbal_unavailable"]  = "N/A";
I18n.en["teleop.gimbal_yaw_gain"]   = "Yaw Gain";
I18n.en["teleop.gimbal_pitch_gain"] = "Pitch Gain";
I18n.en["teleop.gimbal_set_gains"]  = "Apply Gains";
I18n.en["teleop.gimbal_limits"]     = "Limits";
I18n.en["teleop.gimbal_deadband"]  = "Deadband";
I18n.en["teleop.gimbal_smoothing"] = "Smoothing";
I18n.en["teleop.gimbal_rate_limit"] = "Rate Limit";
I18n.en["teleop.gimbal_apply_config"] = "Apply";

/* === camera panel === */
I18n.zh["camera.offline"]  = "摄像头未连接";
I18n.zh["camera.loading"]  = "加载中...";
I18n.zh["camera.status"]   = "MJPEG";
I18n.zh["camera.reset"]    = "重置摄像头";
I18n.zh["camera.resetting"] = "正在重置...";
I18n.zh["camera.reset_ok"]  = "摄像头已重置";
I18n.zh["camera.status_lost"] = "信号丢失";
I18n.zh["camera.status_stale"] = "信号延迟";
I18n.zh["camera.status_waiting"] = "等待画面...";
I18n.zh["camera.select_device"] = "选择设备";

I18n.en["camera.offline"]  = "Camera Offline";
I18n.en["camera.loading"]  = "Loading...";
I18n.en["camera.status"]   = "MJPEG";
I18n.en["camera.reset"]    = "Reset Camera";
I18n.en["camera.resetting"] = "Resetting...";
I18n.en["camera.reset_ok"]  = "Camera Reset OK";
I18n.en["camera.status_lost"] = "Signal Lost";
I18n.en["camera.status_stale"] = "Stale";
I18n.en["camera.status_waiting"] = "Waiting...";
I18n.en["camera.select_device"] = "Select Device";

/* === gimbal panel === */
I18n.zh["gimbal.title"]       = "云台相机控制";
I18n.zh["gimbal.yaw"]         = "偏航";
I18n.zh["gimbal.pitch"]       = "俯仰";
I18n.zh["gimbal.target"]      = "目标";
I18n.zh["gimbal.current"]     = "当前";
I18n.zh["gimbal.home"]        = "归零";
I18n.zh["gimbal.damping"]     = "阻尼模式";
I18n.zh["gimbal.move_to"]     = "移动到";
I18n.zh["gimbal.move_up"]     = "上仰";
I18n.zh["gimbal.move_down"]   = "下俯";
I18n.zh["gimbal.move_left"]   = "左转";
I18n.zh["gimbal.move_right"]  = "右转";
I18n.zh["gimbal.step"]        = "步长";
I18n.zh["gimbal.not_connected"] = "云台未连接";
I18n.zh["gimbal.home_pos"]     = "原点";
I18n.zh["gimbal.set_home"]     = "设为原点";
I18n.zh["gimbal.rel_angle"]    = "相对原点";
I18n.zh["module.gimbal"]      = "云台相机";

I18n.en["gimbal.title"]       = "Gimbal Camera Control";
I18n.en["gimbal.yaw"]         = "Yaw";
I18n.en["gimbal.pitch"]       = "Pitch";
I18n.en["gimbal.target"]      = "Target";
I18n.en["gimbal.current"]     = "Cur";
I18n.en["gimbal.home"]        = "Home";
I18n.en["gimbal.damping"]     = "Damping";
I18n.en["gimbal.move_to"]     = "Move To";
I18n.en["gimbal.move_up"]     = "Up";
I18n.en["gimbal.move_down"]   = "Down";
I18n.en["gimbal.move_left"]   = "Left";
I18n.en["gimbal.move_right"]  = "Right";
I18n.en["gimbal.step"]        = "Step";
I18n.en["gimbal.not_connected"] = "Gimbal offline";
I18n.en["gimbal.home_pos"]     = "Home Pos";
I18n.en["gimbal.set_home"]     = "Set Home";
I18n.en["gimbal.rel_angle"]    = "Rel to Home";
I18n.en["module.gimbal"]      = "Gimbal Camera";

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
I18n.zh["hand.finger.1"]     = "拇指第二关节";
I18n.zh["hand.finger.2"]     = "食指";
I18n.zh["hand.finger.3"]     = "中指";
I18n.zh["hand.finger.4"]     = "无名指";
I18n.zh["hand.finger.5"]     = "小指";

I18n.en["hand.status"]       = "Status:";
I18n.en["hand.force"]        = "Grip Force:";
I18n.en["hand.open"]         = "Open";
I18n.en["hand.close"]        = "Fist";
I18n.en["hand.pinch"]        = "Pinch";
I18n.en["hand.send"]         = "Send";
I18n.en["hand.enabled"]      = "Enabled";
I18n.en["hand.disabled"]     = "Disabled";
I18n.en["hand.finger.0"]     = "Thumb";
I18n.en["hand.finger.1"]     = "Thumb J2";
I18n.en["hand.finger.2"]     = "Index";
I18n.en["hand.finger.3"]     = "Middle";
I18n.en["hand.finger.4"]     = "Ring";
I18n.en["hand.finger.5"]     = "Pinky";

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

/* === gimbal panel === */
I18n.zh["gimbal.connected"] = "已连接";
I18n.zh["gimbal.h_angle"]   = "水平";
I18n.zh["gimbal.p_angle"]   = "俯仰";
I18n.zh["gimbal.h_left"]    = "左";
I18n.zh["gimbal.h_right"]   = "右";
I18n.zh["gimbal.p_up"]      = "上";
I18n.zh["gimbal.p_down"]    = "下";
I18n.zh["gimbal.home"]      = "归中";
I18n.zh["gimbal.stop"]      = "停止";

I18n.en["gimbal.connected"] = "Connected";
I18n.en["gimbal.h_angle"]   = "H";
I18n.en["gimbal.p_angle"]   = "P";
I18n.en["gimbal.h_left"]    = "L";
I18n.en["gimbal.h_right"]   = "R";
I18n.en["gimbal.p_up"]      = "Up";
I18n.en["gimbal.p_down"]    = "Dn";
I18n.en["gimbal.home"]      = "Home";
I18n.en["gimbal.stop"]      = "Stop";

/* === joint panel === */
I18n.zh["joint.stop"]         = "停止";
I18n.zh["joint.waist"]        = "腰部";
I18n.zh["joint.pt"]           = "云台";
I18n.zh["joint.waist_spread"] = "张开";
I18n.zh["joint.waist_shrink"] = "收缩";
I18n.zh["joint.pt_up"]        = "▲ 上";
I18n.zh["joint.pt_down"]      = "▼ 下";
I18n.zh["joint.pt_left"]      = "◄ 左";
I18n.zh["joint.pt_right"]     = "► 右";
I18n.zh["joint.pt_home"]      = "归中";
I18n.zh["joint.connected"]    = "已连接";
I18n.zh["joint.h_limit"]      = "H限位";
I18n.zh["joint.p_limit"]      = "P限位";
I18n.zh["joint.h_min"]        = "水平下限";
I18n.zh["joint.h_max"]        = "水平上限";
I18n.zh["joint.p_min"]        = "俯仰下限";
I18n.zh["joint.p_max"]        = "俯仰上限";
I18n.zh["joint.head20"]       = "头(ID20)";
I18n.zh["joint.tail10"]       = "尾(ID10)";
I18n.zh["joint.wh_limit"]     = "Hd(20)";
I18n.zh["joint.wt_limit"]     = "Tl(10)";

I18n.en["joint.stop"]         = "Stop";
I18n.en["joint.waist"]        = "Waist";
I18n.en["joint.pt"]           = "Pan/Tilt";
I18n.en["joint.waist_spread"] = "Spread";
I18n.en["joint.waist_shrink"] = "Shrink";
I18n.en["joint.pt_up"]        = "▲ Up";
I18n.en["joint.pt_down"]      = "▼ Down";
I18n.en["joint.pt_left"]      = "◄ Left";
I18n.en["joint.pt_right"]     = "► Right";
I18n.en["joint.pt_home"]      = "Home";
I18n.en["joint.connected"]    = "Connected";
I18n.en["joint.h_limit"]      = "H Limit";
I18n.en["joint.p_limit"]      = "P Limit";
I18n.en["joint.h_min"]        = "H Min";
I18n.en["joint.h_max"]        = "H Max";
I18n.en["joint.p_min"]        = "P Min";
I18n.en["joint.p_max"]        = "P Max";
I18n.en["joint.head20"]       = "Head(20)";
I18n.en["joint.tail10"]       = "Tail(10)";
I18n.en["joint.wh_limit"]     = "Hd(20)";
I18n.en["joint.wt_limit"]     = "Tl(10)";

/* === drive panel === */
I18n.zh["drive.connected"] = "已连接";
I18n.zh["drive.forward"]   = "前进";
I18n.zh["drive.backward"]  = "后退";
I18n.zh["drive.stop"]      = "停止";

I18n.en["drive.connected"] = "Connected";
I18n.en["drive.forward"]   = "Forward";
I18n.en["drive.backward"]  = "Backward";
I18n.en["drive.stop"]      = "Stop";

/* === demo panel === */
I18n.zh["demo.start"]   = "启动";
I18n.zh["demo.stop"]    = "停止";
I18n.zh["demo.idle"]    = "空闲";
I18n.zh["demo.running"] = "运行中";
I18n.zh["demo.step"]    = "步骤:";
I18n.zh["demo.desc"]    = "自动循环序列：归中收缩 → 张开 → 前进 → 后退 → 云台四向(各3秒) → 等待 → 循环";
I18n.zh["demo.step_init"]      = "初始化 (归中+收缩)";
I18n.zh["demo.step_spread"]    = "张开到最大";
I18n.zh["demo.step_forward"]   = "前进";
I18n.zh["demo.step_backward"]  = "后退";
I18n.zh["demo.step_pt_up"]     = "云台上";
I18n.zh["demo.step_pt_down"]   = "云台下";
I18n.zh["demo.step_pt_left"]   = "云台左";
I18n.zh["demo.step_pt_right"]  = "云台右";
I18n.zh["demo.step_shrink"]    = "收缩";
I18n.zh["demo.step_wait"]      = "等待";
I18n.zh["demo.apply_config"]   = "应用";
I18n.zh["demo.cfg_init_wait"]         = "初始化等待";
I18n.zh["demo.cfg_spread_wait"]       = "张开等待";
I18n.zh["demo.cfg_drive_duration"]    = "前进后退";
I18n.zh["demo.cfg_drive_stop_pause"]  = "驱动停顿";
I18n.zh["demo.cfg_pt_duration"]       = "云台方向";
I18n.zh["demo.cfg_pt_stop_pause"]     = "云台停顿";
I18n.zh["demo.cfg_pause_after_home"]  = "归中停顿";
I18n.zh["demo.cfg_cycle_wait"]        = "循环等待";

I18n.en["demo.start"]   = "Start";
I18n.en["demo.stop"]    = "Stop";
I18n.en["demo.idle"]    = "Idle";
I18n.en["demo.running"] = "Running";
I18n.en["demo.step"]    = "Step:";
I18n.en["demo.desc"]    = "Auto loop: home+shrink → spread → forward → backward → PT 4-dir (3s each) → wait → loop";
I18n.en["demo.step_init"]      = "Init (home+shrink)";
I18n.en["demo.step_spread"]    = "Spread max";
I18n.en["demo.step_forward"]   = "Forward";
I18n.en["demo.step_backward"]  = "Backward";
I18n.en["demo.step_pt_up"]     = "PT Up";
I18n.en["demo.step_pt_down"]   = "PT Down";
I18n.en["demo.step_pt_left"]   = "PT Left";
I18n.en["demo.step_pt_right"]  = "PT Right";
I18n.en["demo.step_shrink"]    = "Shrink";
I18n.en["demo.step_wait"]      = "Wait";
I18n.en["demo.apply_config"]   = "Apply";
I18n.en["demo.cfg_init_wait"]         = "Init Wait";
I18n.en["demo.cfg_spread_wait"]       = "Spread Wait";
I18n.en["demo.cfg_drive_duration"]    = "Drive Time";
I18n.en["demo.cfg_drive_stop_pause"]  = "Drive Pause";
I18n.en["demo.cfg_pt_duration"]       = "PT Time";
I18n.en["demo.cfg_pt_stop_pause"]     = "PT Pause";
I18n.en["demo.cfg_pause_after_home"]  = "Home Pause";
I18n.en["demo.cfg_cycle_wait"]        = "Cycle Wait";

/* === servo panel === */
I18n.zh["servo.connection"]  = "连接：";
I18n.zh["servo.direction"]   = "方向：";
I18n.zh["servo.angle"]       = "角度";
I18n.zh["servo.speed"]       = "速度 (dps)";
I18n.zh["servo.power"]       = "功率 (mW)";
I18n.zh["servo.head_joint"]  = "头部关节";
I18n.zh["servo.tail_joint"]  = "尾部关节";
I18n.zh["servo.spread"]      = "张开";
I18n.zh["servo.shrink"]      = "收缩";
I18n.zh["servo.stop"]        = "停止";
I18n.zh["servo.damping"]     = "阻尼";
I18n.zh["servo.voltage"]     = "电压：";
I18n.zh["servo.current"]     = "电流：";
I18n.zh["servo.temp"]        = "温度：";
I18n.zh["servo.connected"]   = "已连接";
I18n.zh["servo.mock"]        = "mock";
I18n.zh["servo.status"]      = "状态：";

I18n.en["servo.connection"]  = "Connection:";
I18n.en["servo.direction"]   = "Direction:";
I18n.en["servo.angle"]       = "Angle";
I18n.en["servo.speed"]       = "Speed (dps)";
I18n.en["servo.power"]       = "Power (mW)";
I18n.en["servo.head_joint"]  = "Head Joint";
I18n.en["servo.tail_joint"]  = "Tail Joint";
I18n.en["servo.spread"]      = "Spread";
I18n.en["servo.shrink"]      = "Shrink";
I18n.en["servo.stop"]        = "Stop";
I18n.en["servo.damping"]     = "Damping";
I18n.en["servo.voltage"]     = "Voltage:";
I18n.en["servo.current"]     = "Current:";
I18n.en["servo.temp"]        = "Temp:";
I18n.en["servo.connected"]   = "Connected";
I18n.en["servo.mock"]        = "mock";
I18n.en["servo.status"]      = "Status:";

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
I18n.zh["settings.sudo_section"]        = "Sudo 密码";
I18n.zh["settings.sudo_desc"]           = "用于 USB 重置、CAN 重启、关机等系统操作。不同机器人的 sudo 密码可能不同。";
I18n.zh["settings.sudo_password_label"] = "Sudo 密码";
I18n.zh["settings.save_sudo"]           = "保存";
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
I18n.en["settings.sudo_section"]        = "Sudo Password";
I18n.en["settings.sudo_desc"]           = "Used for USB reset, CAN restart, shutdown, and other system operations. Different robots may have different sudo passwords.";
I18n.en["settings.sudo_password_label"] = "Sudo Password";
I18n.en["settings.save_sudo"]           = "Save";
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

/* === settings — key bindings === */
I18n.zh["settings.key_bindings_section"] = "按键绑定";
I18n.zh["settings.th_action"]   = "操作";
I18n.zh["settings.th_module"]   = "模块";
I18n.zh["settings.th_key"]      = "按键";
I18n.zh["settings.th_bind"]     = "绑定";
I18n.zh["settings.bind_key"]    = "绑定";
I18n.zh["settings.press_key"]   = "按下按键...";
I18n.zh["settings.clear_binding"] = "清除绑定";
I18n.zh["settings.reset_bindings"] = "重置为默认";
I18n.zh["settings.binding_conflict"] = "该按键已绑定到 {0}，将被覆盖。";

I18n.en["settings.key_bindings_section"] = "Key Bindings";
I18n.en["settings.th_action"]   = "Action";
I18n.en["settings.th_module"]   = "Module";
I18n.en["settings.th_key"]      = "Key";
I18n.en["settings.th_bind"]     = "Bind";
I18n.en["settings.bind_key"]    = "Bind";
I18n.en["settings.press_key"]   = "Press a key...";
I18n.en["settings.clear_binding"] = "Clear";
I18n.en["settings.reset_bindings"] = "Reset to Defaults";
I18n.en["settings.binding_conflict"] = "Key already bound to {0}, will be overwritten.";

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
