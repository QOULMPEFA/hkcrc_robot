# robot_control

基于 Flask + SocketIO 的通用机器人控制系统，支持通过配置文件自由添加/删除硬件模块。

---

## 设计理念

`robot_control` 是一个**模块化**的机器人控制层。每个硬件子系统（UGV 底盘、升降平台、机械臂、灵巧手、摄像头等）都是一个独立的 `RobotModule`，由 `Robot` 容器根据配置动态加载。

- **按需加载**：只实例化配置中启用的模块，缺少 SDK 时自动降级为 stub 模式
- **即插即用**：添加新硬件只需新建一个 `RobotModule` 子类并注册
- **Web GUI 自适应**：浏览器控制面板根据已加载模块自动显示/隐藏控制界面

---

## 目录结构

```
robot_control/
├── __init__.py          # 公共导出
├── module_base.py       # RobotModule 抽象基类
├── robot.py             # Robot 容器（配置驱动，动态加载模块）
├── ugv.py               # UGV 底盘控制器（TCP bridge）
├── lift.py              # 升降平台控制器（CANopen / CiA 402）
├── arm.py               # 机械臂控制器（TCP Dashboard + URScript）
├── hand.py              # 灵巧手控制器（stub）
├── camera.py            # USB 摄像头模块（ffmpeg MJPEG + RTMP）
├── motor.py             # 直流电机差分驱动（Orange Pi GPIO）
├── requirements.txt
└── web_gui/
    ├── server.py        # Flask + SocketIO 服务端
    ├── auth.py          # 用户认证与 session 管理
    ├── templates/
    │   ├── index.html
    │   └── login.html
    └── static/
        ├── app.js
        └── style.css
```

---

## 快速开始 — 配置硬件模块

编辑 `robot_control/web_gui/server.py` 中 `main()` 函数的 `config` 字典。

**默认启用全部 6 个模块**，每个模块在 SDK 或硬件未就绪时自动降级为 stub/standby 模式，不影响其他模块运行。

### 管道机器人 mini（电机 + 摄像头 + 升降）

```python
config = {
    "motor":  {},   # 直流电机差分驱动（Orange Pi GPIO 29/31/35/37）
    "camera": {"device": "/dev/video0", "rtmp_url": "rtmp://localhost/live/cam"},
    "lift":   {"can_channel": "can0", "node_id": 1, "home_strategy": "current_pos"},
}
```

### 标准机器人（UGV + 机械臂 + 灵巧手）

```python
config = {
    "ugv":    {"host": "127.0.0.1", "port": 9100},     # AgileX UGV via TCP bridge
    "arm":    {"host": "192.168.1.100"},                # UR5e via TCP Dashboard
    "hand":   {"protocol": "modbus", "port": None},     # BrainCo hand (auto-detect)
    "camera": {"device": "/dev/video0", "rtmp_url": "rtmp://localhost/live/cam"},
    "lift":   {"can_channel": "can0", "node_id": 1},
}
```

### 全功能机器人（所有模块）

默认配置 —— 无需修改，缺省硬件自动 stub。

### 禁用某个模块

```python
config = {
    "lift":   {"enabled": True, "can_channel": "can0", "node_id": 1},
    "camera": {"enabled": False},  # 禁用摄像头
}
```

### 灵巧手配置说明

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `protocol` | `"modbus"` | 通信协议：`"modbus"`（串口 RS485）或 `"can"`（SocketCAN） |
| `port` | `None` | 串口路径，`None` 为自动检测 |
| `baudrate` | `115200` | 串口波特率 |
| `slave_id` | `0x7E` | Modbus 从站 ID |

SDK 依赖：`pip install bc_stark_sdk`

---

## 添加自定义模块

1. 新建文件 `robot_control/my_sensor.py`：

```python
from robot_control.module_base import RobotModule

class MySensor(RobotModule):
    @property
    def name(self) -> str:
        return "my_sensor"

    def get_state_schema(self) -> dict:
        return {"connected": False, "value": 0.0}

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def get_state(self) -> dict:
        return {"connected": True, "value": 42.0}

    def get_socketio_handlers(self) -> dict:
        return {"sensor_read": self._on_read}

    def _on_read(self, data, emit=None):
        emit("sensor_data", {"value": 42.0})
```

2. 在 `robot_control/robot.py` 的 `_MODULE_REGISTRY` 中注册：

```python
_MODULE_REGISTRY = {
    ...
    "my_sensor": ("robot_control.my_sensor", "MySensor"),
}
```

3. 在 `config` 中启用：

```python
config = {
    "my_sensor": {},
}
```

---

## API — Robot

```python
from robot_control.robot import Robot, register_module

config = {
    "lift":   {"can_channel": "can0", "node_id": 1},
    "camera": {"device": "/dev/video0"},
}

with Robot(config) as robot:
    # 访问已加载的模块
    if robot.is_loaded("lift"):
        robot.modules["lift"].move_to(200.0)

    # 聚合状态
    state = robot.get_state()  # -> {"lift": {...}, "camera": {...}, "timestamp": ...}

    # 全局急停
    robot.emergency_stop()

    # 模块列表
    print(robot.module_names)  # -> ["lift", "camera"]
```

### Robot 方法

| 方法 | 说明 |
|------|------|
| `Robot(config)` | 根据配置创建容器，只加载配置中出现的模块 |
| `start()` | 启动所有已启用模块 |
| `stop()` | 停止所有模块 |
| `get_state()` | 返回 `{module_name: module_state, timestamp: float}` |
| `emergency_stop()` | 对所有模块调用 `emergency_stop()` |
| `modules` | `dict[str, RobotModule]` — 已加载模块 |
| `module_names` | 已加载模块名称列表 |
| `get(name)` | 按名称获取模块，不存在返回 `None` |
| `is_loaded(name)` | 检查模块是否已成功加载 |

---

## RobotModule 接口

| 成员 | 说明 |
|------|------|
| `name` (property) | 唯一标识，如 `"ugv"`, `"lift"` |
| `display_name` (property) | 中文显示名，如 `"UGV 底盘"` |
| `start()` | 初始化硬件连接 |
| `stop()` | 释放硬件资源 |
| `get_state()` | 返回模块状态 dict |
| `get_state_schema()` | 返回默认状态 dict（模块未加载时使用） |
| `emergency_stop()` | 紧急停止（默认调用 `stop()`） |
| `get_socketio_handlers()` | 返回 `{event: handler(data, emit)}` 映射 |

### EventHandler 签叏

```python
def handler(data: dict, emit: callable) -> None:
    # data: 客户端发来的 JSON 数据
    # emit: socketio.emit，用于向客户端推送事件
    emit("some_event", {"key": "value"})
```

---

## 硬件环境（默认参考）

| 组件 | 规格 |
|------|------|
| 主控 | Orange Pi 2W / Jetson AGX Orin |
| 直流电机 | L298N / TB6612 H-bridge，GPIO 引脚 29/31/35/37（物理编号） |
| 升降平台 | AIMotor CiA 402 步进驱动器，CAN0（500 kbps），Node ID=1 |
| UGV 底盘 | Ranger Mini V3，CAN → ugv_bridge → TCP 127.0.0.1:9100 |
| 摄像头 | USB 摄像头，/dev/video0，MJPEG 1280×720 |
| 机械臂 | UR5e TCP（Dashboard :29999，URScript :30001） |
| 灵巧手 | /dev/ttyUSB1（stub） |

---

## 安装

```bash
pip install -r robot_control/requirements.txt
pip install -e ../stepper_motor_sdk   # 仅当启用 lift 模块时需要
```

---

## 手动启动

```bash
cd /home/yu/robot_code
python robot_control/web_gui/server.py
```

---

## 访问地址

| 方式 | 地址 |
|------|------|
| 外网（frp） | http://frp-fit.com:35000 |
| 局域网 | http://192.168.20.48:6080 |
| 本机（AnyDesk） | http://127.0.0.1:6080 |

---

## 架构

```
Browser
  │  WebSocket (SocketIO)       HTTP GET /stream
  │                                    │
  ▼                                    ▼
server.py ──────────────────── CameraModule (camera.py)
  │                               │
  │  robot.get_state() @ 5 Hz     │  ffmpeg → /dev/video0
  │  emit("robot_state", ...)     │  单进程，广播给所有客户端
  ▼
Robot (robot.py)
  ├── UGVController  (ugv.py)    ← 仅当 config 包含 "ugv" 时加载
  ├── LiftController  (lift.py)  ← 仅当 config 包含 "lift" 时加载
  ├── ArmController   (arm.py)   ← 仅当 config 包含 "arm" 时加载
  ├── HandController  (hand.py)  ← 仅当 config 包含 "hand" 时加载
  └── CameraModule    (camera.py) ← 仅当 config 包含 "camera" 时加载
```

所有模块统一实现 `RobotModule` 接口，由 `Robot` 容器根据配置动态发现和加载。缺失 SDK 时自动降级为 stub 模式，不影响其他模块的运行。

---

## 开机自启服务

Jetson 上通过 systemd 管理：

```bash
sudo systemctl status robot_control_panel.service
sudo systemctl restart robot_control_panel.service
sudo journalctl -u robot_control_panel.service -f
```
