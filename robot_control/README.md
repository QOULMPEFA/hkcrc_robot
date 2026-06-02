# robot_control

基于 Flask + SocketIO 的机器人控制层，整合 UGV 底盘、升降平台、机械臂、灵巧手四个子系统，并提供浏览器控制面板。

---

## 目录结构

```
robot_control/
├── __init__.py
├── robot.py            # 顶层 Robot 类，聚合所有子系统
├── lift.py             # 升降平台控制器（CiA 402 / CANopen）
├── ugv.py              # UGV 底盘控制器（TCP bridge）
├── arm.py              # 机械臂控制器（stub，SDK 待接入）
├── hand.py             # 灵巧手控制器（stub，SDK 待接入）
├── requirements.txt
└── web_gui/
    ├── server.py       # Flask + SocketIO 服务端，含摄像头广播
    ├── templates/
    │   └── index.html  # 浏览器控制面板
    └── static/
        ├── app.js
        └── style.css
```

---

## 硬件环境

| 组件 | 规格 |
|------|------|
| 主控 | Jetson AGX Orin |
| 升降平台 | AIMotor CiA 402 步进驱动器，CAN0（500 kbps），Node ID=1 |
| UGV 底盘 | Ranger Mini V3，TCP bridge 127.0.0.1:9100 |
| 摄像头 | USB 摄像头，/dev/video0，MJPEG 1280×720 |
| 机械臂 | /dev/ttyUSB0（stub） |
| 灵巧手 | /dev/ttyUSB1（stub） |

---

## 安装

```bash
# 在 Jetson 上，激活目标 Python 环境后执行
pip install -r robot_control/requirements.txt
pip install -e ../stepper_motor_sdk   # 本地 SDK
```

依赖：

```
flask >= 3.0.0
flask-socketio >= 5.3.6
python-socketio >= 5.11.0
python-engineio >= 4.9.0
stepper_motor_sdk（本地包）
ffmpeg（系统级，用于摄像头流）
```

---

## 开机自启服务

Jetson 上通过 systemd 管理，开机顺序：

```
setup-can0.service          ← 配置 can0 接口（500 kbps）
    └── robot_control_panel.service   ← 启动 Web GUI + ffmpeg 摄像头
```

| 服务 | 描述 |
|------|------|
| `setup-can0.service` | oneshot，配置 can0 为 500 kbps |
| `robot_control_panel.service` | Web GUI，监听 6080 端口，含摄像头广播 |

常用命令：

```bash
sudo systemctl status robot_control_panel.service
sudo systemctl restart robot_control_panel.service
sudo journalctl -u robot_control_panel.service -f
sudo tail -f /var/log/robot_control_panel.log
```

---

## 访问地址

| 方式 | 地址 |
|------|------|
| 外网（frp） | http://frp-fit.com:35230 |
| 局域网 | http://192.168.20.48:6080 |
| 本机（AnyDesk） | http://127.0.0.1:6080 |

> Chrome 会拒绝访问 6000 端口（ERR_UNSAFE_PORT），请使用 6080。

---

## 手动启动

```bash
cd /home/yu/robot_code
python robot_control/web_gui/server.py
```

---

## 架构

```
Browser
  │  WebSocket (SocketIO)       HTTP GET /stream
  │                                    │
  ▼                                    ▼
server.py ──────────────────── _CameraBroadcaster
  │                               │
  │  robot.get_state() @ 5 Hz     │  ffmpeg → /dev/video0
  │  emit("robot_state", ...)     │  单进程，广播给所有客户端
  ▼
Robot
  ├── UGVController      TCP → Ranger Mini V3
  ├── LiftController     CANopen → AIMotor 驱动器
  ├── ArmController      stub
  └── HandController     stub
```

### 摄像头广播

`_CameraBroadcaster` 在后台线程中运行单个 ffmpeg 进程，解析 MJPEG 帧后推送到所有订阅客户端的队列，支持多用户同时观看而不重复启动 ffmpeg。

---

## API — Robot

```python
from robot_control.robot import Robot, RobotConfig

config = RobotConfig(
    lift_can_channel="can0",
    lift_node_id=1,
    lift_home_strategy="current_pos",  # "current_pos" | "limit_switch" | "mechanical"
)

with Robot(config) as robot:
    robot.ugv.set_velocity(0.5, 0.0)
    robot.lift.move_to(200.0)
    state = robot.get_state()
    robot.emergency_stop()
```

### RobotConfig

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `ugv_host` | `"127.0.0.1"` | UGV TCP bridge 地址 |
| `ugv_port` | `9100` | UGV TCP bridge 端口 |
| `lift_can_channel` | `"can0"` | CAN 接口名 |
| `lift_node_id` | `1` | 驱动器 Node ID |
| `arm_port` | `"/dev/ttyUSB0"` | 机械臂串口 |
| `hand_port` | `"/dev/ttyUSB1"` | 灵巧手串口 |
| `lift_home_strategy` | `"current_pos"` | 升降平台回零策略 |

---

## API — LiftController

| 方法 | 说明 |
|------|------|
| `setup()` | 使能驱动器，启动转矩监控 |
| `shutdown()` | 释放转矩，停止监控 |
| `home(strategy)` | 回零（`current_pos` / `limit_switch` / `mechanical`） |
| `move_to(mm, speed_mm_s)` | 绝对位置移动（阻塞） |
| `move_to_async(mm, speed_mm_s)` | 绝对位置移动（非阻塞） |
| `stop()` | 急停 |
| `reset_fault()` | 清除故障并重新使能 |
| `set_zero()` | 将当前位置设为用户空间零点 |
| `set_limits(min_mm, max_mm)` | 设置软限位（支持负值） |
| `get_limits()` | 返回当前限位和零点偏移 |
| `get_state()` | 返回 `LiftState` |

### 运动学参数（默认值）

| 参数 | 值 | 说明 |
|------|----|------|
| `GEARBOX_RATIO` | 10.0 | 减速比 |
| `PULLEY_RATIO` | 1.0 | 皮带轮比 |
| `LEADSCREW_LEAD` | 8.0 mm/rev | 丝杠导程 |
| `PULSES_PER_REV` | 1000 | 编码器脉冲/转 |
| `DEFAULT_SPEED_MM_S` | 10.0 mm/s | 默认运动速度 |
| `STALL_TORQUE` | 800 ‰ | 堵转检测阈值（80% 额定） |

---

## SocketIO 事件

### 客户端 → 服务端

| 事件 | 数据 | 说明 |
|------|------|------|
| `ugv_velocity` | `{linear, angular}` | 设置底盘速度 |
| `ugv_stop` | — | 停止底盘 |
| `ugv_estop` | — | 全系统急停 |
| `lift_move` | `{height_mm, speed_mm_s}` | 移动到目标高度 |
| `lift_stop` | — | 升降急停 |
| `lift_home` | `{strategy}` | 回零 |
| `lift_reset_fault` | — | 清除故障 |
| `lift_set_zero` | — | 设当前位置为零点 |
| `lift_set_limits` | `{min_mm, max_mm}` | 设置软限位 |

### 服务端 → 客户端

| 事件 | 频率 | 说明 |
|------|------|------|
| `robot_state` | 5 Hz | 全系统状态（UGV / Lift / Arm / Hand） |
| `lift_limits` | 按需 | 限位更新确认 |

---

## SSH 连接

```
Host:      frp-fit.com:35200
User:      yu
Password:  1114
Workspace: /home/yu/robot_code
```

> ⚠️ 不要停止 `natfrp-service`，否则 SSH 连接会断开。
