# Changelog

## 2026-06-02

### 摄像头 — SRS WebRTC 低延时图传

- **新增** `camera_sdk/srs/` — 基于 SRS 6 的 WebRTC 流媒体服务
  - `docker-compose.yml` — host 网络模式容器部署
  - `srs.conf` — 适配 FRP 穿透（candidate: `frp-fit.com:35500`）
  - `README.md` — 启动命令与公网测试方法
- FRP 端口映射: UDP 35500→8000 / TCP 35900→1985 / TCP 35700→8080
- `socat` UDP 转发容器 (8000→35500) 处理 FRP 与 SRS 端口不一致
- FFmpeg 推流: `/dev/video0` → libx264 `rtmp://127.0.0.1/live/cam`
- **修改** `robot_control/web_gui/templates/index.html:28`
  - 新增 `<video>` 元素 + "WebRTC / MJPEG" 状态徽章
- **修改** `robot_control/web_gui/static/app.js:5`
  - 首选 WebRTC（SRS Play API: `frp-fit.com:35900`）→ ICE/断线自动降级 MJPEG

### 认证 — 单会话强制

- **修改** `robot_control/web_gui/auth.py`
  - `users` 表新增 `session_token` 列（自动迁移旧库）
  - 新增 `set_session_token()` / `clear_session_token()` / `validate_session()`
  - `get_current_user()` 加入 token 校验（不匹配 = 强制登出）
- **修改** `robot_control/web_gui/server.py:229` — 登录时生成 token
  - `server.py:264` — 登出时清除 token
  - `server.py:53` — `_get_session_user()` 改用 `auth.get_current_user()`
- **修改** `robot_control/web_gui/templates/login.html:17` — "已在其他设备登录" 提示

### 升降平台 — 运行日志 & 诊断

- **修改** `robot_control/lift.py`
  - 新增 `LiftLogEntry` dataclass，环形缓冲区（80 条）
  - `LiftState` 新增 `statusword` / `drive_state` / `logs` 字段
  - 所有关键操作（初始化、移动、回零、急停、堵转）记录日志
  - 每个 SDO 读取独立 try，报错去重（同错误不重复写入）
- **修改** `robot_control/web_gui/templates/index.html:87`
  - 新增驱动器状态 / Statusword 显示 + 运行日志折叠面板
- **修改** `robot_control/web_gui/static/app.js:143`
  - `robot_state` 事件中渲染 CAN 诊断 + 日志列表
  - 日志面板自动增量更新 + 滚底
  - 驱动状态颜色编码（绿=正常，红=故障，橙=其他）

### 升降平台 — 稳定性 & 控制逻辑

- **修改** `robot_control/robot.py`
  - `start()` / `stop()` 加固异常处理 → CAN 故障时服务器不崩溃
  - `start()` 首次失败后重置 CAN 节点并重试一次
  - `self._lift_fallback_state` — Lift 故障后保留日志上下文
- **修改** `robot_control/lift.py:179`
  - `move_to()` 自动清除故障 + 重新使能（急停后无需手动恢复）
- **修改** `robot_control/web_gui/static/app.js:235`
  - "回零"按钮改为发送 `lift_move {height_mm: 0}`（不再重校准零点）
  - 零点校准保留在管理员设置 → "将当前位置设为零点"

### SDK 修复 — SDO / 转矩

- **修改** `stepper_motor_sdk/stepper_motor_sdk/sdo.py:76`
  - `SDOClient.read()` — 暂态错误（CAN 噪音 / 0x60 响应）自动重试 3 次
  - SDO abort（硬错）直接抛出，不重试
- **修改** `stepper_motor_sdk/stepper_motor_sdk/motor.py:211`
  - `get_torque()` 先读 `sub=1`（CiA 402 标准），失败后回退 `sub=0`（flat object）
  - 均失败则静默返回 0，不刷日志

### Bug 修复 — 摄像头串流 & 前端

- **修复** `robot_control/web_gui/server.py:161-177`
  - `/stream` MJPEG 端点增加 5 秒超时：摄像头设备不可用时（如被 RTMP 占用），不再无限挂起浏览器
  - 每收到一帧重置倒计时，持续有帧则持续推送
  - 添加帧计数日志（每 150 帧 ~10s 输出一条）
- **修复** `robot_control/web_gui/server.py:185`
  - Camera watchdog 条件 `or` → `and`：有人正在观看时不会被误切断
- **修复** `robot_control/web_gui/static/app.js:74`
  - WebRTC `ontrack` 逻辑：不再在 SRS API 连接成功后就隐藏 MJPEG
  - 只有真正收到视频轨道时才切换为 WebRTC 画面
  - 8 秒无视频轨道自动回退 MJPEG 模式
- **修复** `robot_control/web_gui/static/style.css:284`
  - 摄像头画面 img/video 改为 `position: absolute; top: 0; left: 0`，避免布局偏移导致画面被裁切

### 运维

- **新增** `guide/backup_guide.md` — 备份操作流程
- `robot_control_panel.service` — systemd 自动重启，日志路径 `/var/log/robot_control_panel.log`
