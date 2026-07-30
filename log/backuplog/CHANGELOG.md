# Changelog — PipeRobot 机器人控制系统

> 涵盖时间：2026-06-02 → 2026-07-30
> 版本范围：v1.0 → v1.21

---

## v1.21 (2026-07-30) — 灵巧手模块修复

### Bug Fix — 手指无法运动
- **根因:** `_handle_cmd` 将归一化目标值 (0-1000) 钳制到硬件原生限位（拇指 max=60），导致"握拳"指令 1000 被截断为 60 (=6%)
- **修复:** 移除原生限位钳制，直接使用 0-1000 范围，由 SDK 的 `FingerUnitMode.Normalized` 处理映射
- 手指滑块范围修复：不再被硬件限位覆盖（0-6% → 0-100%）

### Feature — 手部使能/断开 UI
- 灵巧手面板新增"使能"/"断开使能"按钮
- `hand.enable` / `hand.disable` 按键绑定（角色权限控制）
- 中英文 i18n 翻译
- SocketIO 事件注册：`hand_enable` / `hand_disable`

### 涉及文件
- `robot_control/hand.py` — 使能/断开处理器, 限位钳制移除, 诊断日志
- `robot_control/web_gui/static/app.js` — 手部按钮 + 监听器 + 绑定, 滑块范围修复
- `robot_control/web_gui/static/i18n.js` — 翻译词条
- `scripts/_deploy_all.py` — 部署列表新增 hand.py + 模板

### 设备信息
- BrainCo Revo2 Basic Right, fw 1.0.20.U, bc_stark_sdk v2.0.2
- Modbus RS485, slave_id=0x7F, 原生限位: 0-60/0-90/0-81

---

## v1.15 – v1.20 (2026-07-29) — 云台抖动消除系列

### v1.15 — XR 遥操作 SDK 重构 + 头部→云台跟踪
- **Commit:** `2a09358`
- `teleop.py` / `ur5_ik.py` 从 `robot_control/` 迁移至独立的 `teleoperation_sdk/` 包
- Quest 3 头部四元数 → yaw/pitch 转换（修正 OpenXR 坐标轴映射）
- EMA 指数平滑滤波器（α=0.35，可通过 `head_smoothing` 配置）
- 20Hz 更新率 + 0.15° 死区
- 独立的手臂/头部跟踪开关
- 新增 `robot_control/gimbal.py` — 双轴 Fashion Star UART 舵机云台模块
- Web GUI 新增遥操作面板（云台状态、角度、控制按钮）

### v1.16 — 头部→云台增益系数 + 角度限制覆盖
- **Commit:** `2c20948`
- `head_yaw_gain` / `head_pitch_gain` — 头部至云台角度缩放因子（默认 1.0，范围 0.1–5.0）
- `gimbal_yaw_limits` / `gimbal_pitch_limits` 配置覆盖（优先级: teleop > 模块默认 > 硬编码）
- GUI 增益滑块 + 实时角度限制显示

### v1.17 — 可配置死区 + EMA 平滑抑制云台抖动
- **Commit:** `ed1f916`
- 死区从 0.15° → 0.3°（默认），GUI 可调（0.05–1.0°）
- 平滑系数 GUI 滑块（0.05–1.0）
- `teleop_set_head_config` SocketIO 处理器 — 热更新死区+平滑
- 调优指南：抖动→提高死区/降低平滑；迟滞→相反

### v1.18 — 速率限制器 + 宽范围滑块
- **Commit:** `9782f74`
- **新增速率限制器**: `head_rate_limit` 限制云台目标角速度（默认 60 °/s，范围 5–500）
- 滑块范围拓宽：死区 0.02–2.0°，平滑 0.02–1.0
- 每个滑块配 `<input type="number">` 精确输入
- 三层降噪管线: `Head IMU → EMA → Deadband → Rate Limit → Gimbal`

### v1.19 — 迟滞死区
- **Commit:** `7eea9dc`
- 头部移动 < 阈值（默认 3°）时云台冻结，仅跟随大幅主动转头
- 参考: 1€ Filter (Casiez et al. 2012, ACM CHI)

### v1.20 — 目标角度量化
- **Commit:** `9d9a573`
- 根因: 高精度浮点数目标导致舵机编码器震荡
- 修复: `round(target / precision) * precision`（默认 0.5°，范围 0.1–2.0°）
- GUI 可调精度参数
- 最终五层噪声抑制管线:
  ```
  Head IMU → EMA → Dead Zone ±3° → Deadband 0.3° → Rate Limit 300°/s → Quantize 0.5° → Servo
  ```
- 默认配置:
  ```python
  "teleop": {"head_smoothing": 0.25, "head_deadzone": 3.0, "head_deadband": 0.3, "head_rate_limit": 300.0, "head_precision": 0.5}
  ```
- Jetson 已同步至 `9d9a573`，服务运行中

---

## v1.13 (2026-06-28) — Demo 模块 + 关节控制器 + 舵机控制

- **Commit:** `16ac7da`
- **新增** `robot_control/demo.py` — 循环演示序列: 归零→展开→前进→后退→PT 四向→收缩→等待→循环
- **新增** `robot_control/pipi_joint.py` — 关节模块: 云台俯仰/偏航 + 腰部展开/收缩，可配置限位
- **新增** `robot_control/pipi_drive.py` — 驱动模块: 直流电机差速驱动
- **新增** `robot_control/pipi_gimbal.py` — 独立云台模块
- **新增** `robot_control/servo.py` — 总线舵机控制，舵机角度实时显示
- 8 项时序参数均可通过 Web GUI 配置并持久化
- Orange Pi 部署: `192.168.50.229:6080`，模块 active: camera, motor, joint, drive, demo

---

## v1.12 (2026-06-26) — PyArmor 加密 + WiFi 热点

- **Commit:** `88dc3d3`
- **PyArmor 加密部署**: `robot_encrypted/` 含 AArch64 运行时，保护源码
- **WiFi 热点**: 机器人自建 `PipeRobot` 热点（SSID=PipeRobot，IP=192.168.12.1），替代 FRP 远程穿透
- 绝对导入修复: `auth.py`/`server.py` 改为绝对导入，兼容加密环境
- **新增** `guide/troubleshooting.md` — 客户排障指南
- Orange Pi 已部署加密版，FRP 停用

---

## v1.11 (2026-06-23) — 摄像头架构合并 + 键位绑定恢复

- **Commit:** `d0162e4`
- 摄像头: 合并多源/TCP 架构与 `_cycle_device()` 恢复机制，USB 重置使用来自 DB 的 sudo 密码
- 服务器: Web GUI 设置页新增 sudo 密码配置，应用于 CAN 重启、USB 重置、关机/重启
- 键盘: 重新启用 `KeyBindingManager`，默认绑定 WASD→UGV, 方向键→motor, T/Y/U/I→lift, 1-4→hand
- 修复按钮点击后键盘被抑制问题
- Orange Pi 部署: frp-fit.com:35300，PID 11022

---

## v1.10 (2026-06-12) — UGV Bridge 死锁修复 + TCP 帧同步

- **Commit:** `cd708f0`
- **死锁修复**: 命令处理器不再直接调用 SDK，仅更新跟踪变量。运动心跳（50Hz）是 `SetMotionCommand` 的唯一调用者
- **TCP 帧同步**: 新增 `rx_line_buf` 累加器 + 换行分割，防止部分/多行读取导致 "Unknown cmd"
- `SetMotionMode` 仅在模式实际变化时调用（自旋 ↔ Ackerman）
- `Enable` 处理器在 `EnableCommandedMode()` 前先调用 `ResetRobotState()` 清除故障 0x0200
- CAN 连接时自动使能（不再需要手动）
- sudo 密码修正: `orangepi` → `1114`
- Jetson 部署后 UGV 状态: vehicle_state=0 (NORMAL), error_code=0, control_mode=1 (CAN) ✓

---

## v1.9 (2026-06-12) — 摄像头 USB 重置恢复 + 键盘绑定 + 关机修复

- **Commit:** `78a3af7`
- **摄像头模块**:
  - 非阻塞帧循环: `select.select()` + `os.read()` 替代阻塞式 `proc.stdout.read(4096)`
  - **USB 设备重置恢复** (`_reset_usb()`): 通过 sysfs unbind/bind 恢复 UVC 固件错误（EPIPE on GET_DEF）
  - ffmpeg stderr 捕获用于诊断
  - 设备检测: 检查 Device Caps（非仅 Capabilities）过滤 metadata-only 设备
  - `camera_reset` SocketIO 处理器供 GUI 手动触发重置
  - `fuser -k` 清理僵尸 ffmpeg 进程
  - 单设备自动重置: `_cycle_device()` 触发 USB 重置
- **根因分析**: Alcor Micro PC Camera (058f:1412) 固件 Bug — `Failed to query (GET_DEF) UVC control 13 on unit 1: -32 (EPIPE)`，设备进入死锁状态
- **服务器**: 关机/重启使用 `echo 1114 | sudo -S systemctl poweroff/reboot`
- **Web GUI**: `KeyBindingManager` — 键盘-动作映射，localStorage 持久化，支持 hold/click 类型、冲突解决、监听模式重绑定
- **Systemd**: `ExecStartPre` 清理残留 ffmpeg，`TimeoutStopSec=15`
- Jetson 部署 ✓，Orange Pi 暂不可达

---

## v1.8 (2026-06-11) — i18n 国际化英文化

- **Commit:** `56b5f10`
- **新增** `web_gui/static/i18n.js` — 前端 I18n 模块（~100 组 中/英 键值对）
  - 覆盖 Header、Sidebar、全部 6 个模块面板、设置模态框、认证页面
  - 语言持久化 `localStorage`（`robot_lang` 键）
  - 语言切换 `<select>` 下拉注入 Header
  - `data-i18n` / `data-i18n-title` / `data-i18n-placeholder` / `data-i18n-value` HTML 扫描器
- **新增** `web_gui/i18n.py` — 后端翻译辅助（14 条服务端消息键）
- `app.js` — 全部 ~145 处硬编码中文字符串 → `I18n.t()` 调用
- `index.html` / `login.html` / `register.html` — 完整 i18n 化
- `server.py` / `auth.py` — 所有 emit/HTTP 字符串使用 `i18n.t()`
- Orange Pi 已部署

---

## v1.7 (2026-06-11) — Orange Pi 部署 + 多机器人配置

- **Commit:** `cac4c8c`
- `lift.py`: try/except ImportError 优雅降级至 stub 模式
- `hand.py`: stub 模式下 `_connected=False`，GUI 正确显示 "offline"
- `server.py`: 多机器人配置预设（管道机器人 / 全机器人 / 全模块）
- `camera.py`: 零延迟帧传输（Condition 变量 latest-frame 模式）
- **Orange Pi Zero 2W** 首次部署: IP 192.168.1.112, 6 模块运行
  - Camera: `/dev/video1`, 800x600 MJPEG, 30fps
  - Motor: GPIO 差速驱动（wiringOP）
  - 其余 stub 模式

---

## v1.6 (2026-06-11) — 摄像头回退至纯 MJPEG

- **Commit:** `f4bb724`
- **回退**: 移除 WebRTC (SRS RTMP) 和 HTTP-FLV (flv.js) 路径，回归纯 MJPEG
- ffmpeg 移除 H.264 RTMP 输出，增加低延迟参数（`-flags low_delay -analyzeduration 0 -probesize 32`）
- 订阅者队列: `list` → `queue.Queue(maxsize=10)` + 阻塞 `get(timeout=5)`（消除 50ms 轮询延迟）
- `app.js`: 摄像头面板恢复 `<img src="/stream">`
- SRS: `crossdomain on;` 持久化

---

## v1.5 (2026-06-10) — WebRTC NAT 穿透

- **Commit:** `9fd2771`
- WebRTC iceServers 新增 TURN 服务器（stun.evan-brass.net UDP+TCP）用于 NAT 穿透
- 状态标签细化: "MJPEG (WebRTC 协商中)" / "MJPEG (WebRTC 不可用)"

---

## v1.4 (2026-06-10) — Arm/Lift/UGV/Hand 多项修复

- **Commit:** `7bdb848`
- **Lift**: CAN 电机不可达时优雅 stub 模式，GUI 无硬件时仍可显示
- **SDO**: 修复竞态条件 — `_wait_response()` 校验响应 index/subindex 过滤共享 CAN 总线上的过期帧
- **SDO**: 超时 1.0s → 3.0s（共享 CAN 总线 UGV 流量）
- **UGV**: D-pad 按钮 `mouseleave` → `pointerdown` + `setPointerCapture`（修复按住时断触）
- **UGV**: D-pad 速度因子统一 1.0×（原 0.5× 导致与键盘不匹配）
- **UGV**: D-pad CSS grid 定位改用 `data-l`/`data-a` 属性
- **Arm**: 每个关节 ±5° 点动按钮，支持长按持续点动
- **Arm**: 滑块松开发送 `movej`（不再需要额外"发送"按钮）
- **Camera**: Orbbec Femto Bolt 检测: v4l2-ctl 输出为空时 sysfs 回退
- **Camera**: USB 热插拔扫描（10s 轮询）
- **Motor/Hand**: 新增模块桩，为后续硬件准备

---

## v1.3 (2026-06-03) — WebRTC 摄像头 RTMP 推流

- **Tag:** `1c219af`
- `_CameraBroadcaster` ffmpeg 增加第二路输出 H.264 RTMP → SRS
  - `libx264 -preset ultrafast -tune zerolatency -pix_fmt yuv420p`
  - SRS 自动 RTMP→WebRTC 转换（rtmp_to_rtc on）
- 单进程读取 `/dev/video0` 同时输出 MJPEG（`/stream`）和 H.264（RTMP），无设备冲突
- `pc.ontrack` 移至 `setRemoteDescription` 之前注册（修复事件丢失导致 8s 降级）
- FRP 隧道列表完整记录（7 条映射）
- 旧 `camera_server.service` 停止并禁用

---

## v1.2 (2026-06-03) — 零速转向差速自旋

- **Tag:** `a46272f`
- `ugv_bridge.cpp`: velocity 命令智能选择运动模式
  - linear≈0 且 angular≠0 → `kSpinning`（angular = angular_vel）
  - 其他 → `kDualAckerman`（angular = steering_angle）
- 修复 D-pad 左转/右转无效
- 备份流程新增版本 tag 规范

---

## v1.1 (2026-06-02 – 2026-06-03) — 基础设施搭建

### 摄像头串流 (2026-06-02 17:00)
- **Commit:** `4c506fb` / `6266d42`
- MJPEG `/stream`: 5 秒超时 + 每帧重置倒计时（修复无设备时无限挂起）
- Camera watchdog: `no_subs or no_activity` → `no_subs and no_activity`（修复误切断）
- WebRTC ontrack 时序: MJPEG 保持显示，仅收到视频轨道时切换，8 秒无轨道回退
- CSS: img/video 绝对定位修复画面偏移裁切

### SRS WebRTC 低延时图传 (2026-06-02 17:26)
- **新增** `camera_sdk/srs/` — SRS 6 WebRTC 流媒体服务（Docker Compose host 网络模式）
- FRP 端口映射: UDP 35500→8000 / TCP 35900→1985 / TCP 35700→8080
- socat UDP 转发容器处理 FRP 与 SRS 端口不一致
- FFmpeg 推流: `/dev/video0` → libx264 `rtmp://127.0.0.1/live/cam`
- 前端优选 WebRTC（SRS Play API），ICE/断线自动降级 MJPEG

### 认证单会话强制 (2026-06-02)
- `auth.py`: `users` 表新增 `session_token`，`get_current_user()` token 校验（不匹配=强制登出）
- 登录生成 token / 登出清除 token

### 升降平台 (2026-06-02)
- `LiftLogEntry` dataclass + 80 条环形缓冲
- `LiftState` 新增 `statusword` / `drive_state` / `logs`
- GUI: 驱动器状态/Statusword 显示 + 运行日志折叠面板（增量更新+滚底，颜色编码）
- `move_to()` 自动清除故障+重新使能
- robot.py: `start()`/`stop()` 异常加固 + CAN 重置重试

### UGV Bridge (2026-06-02 19:50)
- **新增** `ugv_bridge/ugv_bridge.cpp` — C++ TCP Bridge（基于 ugv_sdk，JSON-line 协议）
- 监听 TCP :9100，CAN 连接 Ranger Mini 3.0
- `UGVState` 从 5 字段扩展至 40+ 字段（运动状态、遥控器、里程计、电机、BMS 等），向后兼容

### UGV 运维 (2026-06-03)
- **新增** `ugv_bridge.service` systemd 服务（依赖 `setup-can0.service`），自动启动
- UGV 运行日志窗口（环形缓冲 + 车辆状态/控制模式/SOC 颜色编码 + "使能 CAN"/"清除故障"按钮）
- **死锁修复**: `threading.Lock()` → `threading.RLock()`（`_handle_message` 持锁调用 `_add_log` 自身死锁）
- **Bridge**: 取消自动使能，改为 GUI 手动切换
- **D-pad**: 长按持续发送（setInterval 100ms），松开停止
- **Error code 监控**: 自动记录变化 + 中文故障标签（0x0200→HL通信）
- **新增** "重启 CAN" 按钮（admin only）
- **CAN BUS-OFF 诊断**: 底盘断电→无终端电阻→tx 错误累积→BUS-OFF

### 文档 & DevOps
- **新增** `guide/backup_guide.md` / `git/info.md` — 备份流程、Remote URL、PAT
- **新增** `guide/ssh_to_jetson.md` — SSH config + 完整 FRP 隧道表
- `robot_control_panel.service` systemd 自动重启 (`Restart=always`)
- SSH config 修复: jetson 别名缺失 `# password:` 注释元数据

---

## 版本总览

| 版本 | 日期 | 关键变更 |
|------|------|----------|
| v1.20 | 07-29 | 目标角度量化消抖（最终层） |
| v1.19 | 07-29 | 迟滞死区 |
| v1.18 | 07-29 | 速率限制器 + 宽范围滑块 |
| v1.17 | 07-29 | 可配置死区+平滑 |
| v1.16 | 07-29 | 增益系数 + 角度限制 |
| v1.15 | 07-29 | XR 遥操作 SDK 重构 + 头部→云台 |
| v1.13 | 06-28 | Demo/Joint/Drive/Gimbal/Servo |
| v1.12 | 06-26 | PyArmor 加密 + WiFi 热点 |
| v1.11 | 06-23 | 摄像头架构合并 + 键位恢复 |
| v1.10 | 06-12 | UGV 死锁修复 |
| v1.9  | 06-12 | 摄像头 USB 恢复 + 键盘绑定 |
| v1.8  | 06-11 | i18n 英文化 |
| v1.7  | 06-11 | Orange Pi 部署 |
| v1.6  | 06-11 | 纯 MJPEG 回退 |
| v1.5  | 06-10 | WebRTC NAT 穿透 |
| v1.4  | 06-10 | Arm/Lift/UGV/Hand 修复 |
| v1.3  | 06-03 | WebRTC RTMP 推流 |
| v1.2  | 06-03 | 差速自旋模式 |
| v1.1  | 06-02 | 基础设施（摄像头/认证/桥接/文档） |

---

## 部署目标

| 设备 | 角色 | 访问方式 |
|------|------|----------|
| **Orange Pi Zero 2W** | 管道机器人主机 | WiFi 热点 `PipeRobot` / `192.168.12.1:6080` |
| **Jetson Orin** | 遥操作机器人主机 | FRP `frp-fit.com:35230` 或本地 |
| **Quest 3** | XR 头部/手部追踪 | PC Service → SDK → TeleopModule |

---

*最后更新: 2026-07-29 — Generated from 22 session/backup logs*
