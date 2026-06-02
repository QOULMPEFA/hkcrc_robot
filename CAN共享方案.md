# CAN 总线共享方案：Ranger Mini V3 + 步进电机升降平台

## 项目概述

在 Jetson AGX 上通过单条 CAN 总线（`can0`，500 kbps）同时通讯控制：

- **Ranger Mini V3.0**（UGV）— 由 `ranger_base_node`（C++ ROS2 节点）通过 `ugv_sdk` 驱动
- **AIMotor 步进电机**（升降平台）— 由 `stepper_motor_sdk`（Python，CiA 402）控制

---

## 核心冲突与解决方案

### 冲突原因

`ranger_base_node` 调用 `ugv_sdk` 内部的 `Connect(port_name_)` 打开 socketcan，会独占 `can0` 的原始 socket。`stepper_motor_sdk` 的 `CANopenNode` 也需要打开同一个 socket，两者直接冲突。

### CAN ID 分区（无冲突）

两套设备的 CAN ID 范围完全不重叠：

| 设备 | CAN ID 范围 | 说明 |
|------|-------------|------|
| Ranger Mini V3 | `0x111`～`0x4FF` | ugv_sdk 内部协议帧 |
| 步进电机 SDO TX | `0x581`（Node 1） | 驱动器 → 主站 |
| 步进电机 SDO RX | `0x601`（Node 1） | 主站 → 驱动器 |
| 步进电机心跳 | `0x701`（Node 1） | 驱动器心跳 |

ID 不重叠，说明**帧内容不冲突，socket 层需要共享**。

### 解决方案：vcan bridge（推荐）

使用 Linux 内核的 `can-gw`（CAN Gateway）模块，将 `can0` 的所有帧双向转发到虚拟接口 `vcan0`：

```
can0 (物理接口)
  ├── ranger_base_node  →  直接绑定 can0（C++ ugv_sdk）
  └── vcan0 (虚拟接口，由 can-gw 镜像 can0)
        └── stepper_motor_sdk  →  绑定 vcan0（Python）
```

`can-gw` 在内核层做双向转发，两侧完全透明，没有额外延迟，也不需要修改 ranger_ros2 源码。

---

## 环境配置

### 第一步：加载内核模块

```bash
sudo modprobe vcan
sudo modprobe can-gw
```

开机自动加载（写入 `/etc/modules`）：

```bash
echo -e "vcan\ncan-gw" | sudo tee -a /etc/modules
```

### 第二步：创建并配置接口

```bash
# 创建虚拟接口
sudo ip link add dev vcan0 type vcan
sudo ip link set vcan0 up

# 配置物理接口（如尚未配置）
sudo ip link set can0 down
sudo ip link set can0 type can bitrate 500000
sudo ip link set can0 up

# 双向网关：can0 <-> vcan0
sudo cangw -A -s can0 -d vcan0 -e    # can0 → vcan0
sudo cangw -A -s vcan0 -d can0 -e    # vcan0 → can0
```

验证网关：

```bash
cangw -L    # 列出所有转发规则，应显示两条规则
candump can0 &
candump vcan0 &   # 两侧应看到相同的帧
```

### 第三步：封装为启动脚本

```bash
# 见 scripts/setup_shared_can.sh
sudo bash stepper_motor_sdk/scripts/setup_shared_can.sh
```

---

## 软件架构

```
┌──────────────────────────────────────────────────────┐
│                   Jetson AGX                         │
│                                                      │
│  ┌─────────────────────┐  ┌────────────────────────┐ │
│  │  ranger_base_node   │  │   lift_control.py      │ │
│  │  (C++ ROS2)         │  │   (Python)             │ │
│  │                     │  │                        │ │
│  │  ugv_sdk            │  │  stepper_motor_sdk     │ │
│  │  └─ can0 (socket)   │  │  └─ vcan0 (socket)     │ │
│  └────────┬────────────┘  └──────────┬─────────────┘ │
│           │                          │                │
│      ┌────┴──────────────────────────┴────┐           │
│      │       Linux can-gw (kernel)        │           │
│      │       can0  ←──────────→  vcan0    │           │
│      └────────────────┬───────────────────┘           │
│                       │                               │
└───────────────────────┼───────────────────────────────┘
                        │ SN65HVD230
                        │
              ┌─────────┴──────────┐
              │   CAN 总线  500kbps │
              ├────────────────────┤
              │  Ranger Mini V3    │  ID: 0x111~0x4FF
              │  步进电机驱动器    │  ID: 0x581/0x601/0x701
              └────────────────────┘
```

---

## 启动顺序

```bash
# 1. 配置 CAN 接口和网关
sudo bash stepper_motor_sdk/scripts/setup_shared_can.sh

# 2. 启动 ROS2（Ranger Mini V3 使用 can0）
source /opt/ros/humble/setup.bash
source ~/agilex_ws/install/setup.bash
ros2 launch ranger_bringup ranger_mini_v3.launch.py port_name:=can0

# 3. 启动步进电机控制（使用 vcan0）
# 修改 lift_control.py 中的 channel="vcan0"
python3 lift_control.py
```

---

## lift_control.py 配置修改

将 `CANopenNode` 的 `channel` 改为 `vcan0`：

```python
with CANopenNode(channel="vcan0", node_id=1) as node:
    controller = LiftingPlatformController(node)
```

---

## 常见问题

### can-gw 模块不存在

```bash
# Jetson JetPack 某些版本未编译 can-gw，检查：
modinfo can-gw

# 若不存在，改用 candump + canplayer 软件桥（性能略低）：
# 见下方备选方案
```

### 备选方案：python-can 虚拟总线桥

若 `can-gw` 不可用，可在 Python 层实现桥接：在 `stepper_motor_sdk` 的 `SharedCANBus` 中增加 ID 过滤，忽略 Ranger 的帧（`0x000`～`0x4FF`），只处理 CiA 402 帧（`0x580`～`0x7FF`）。此方案需要 `stepper_motor_sdk` 和 `ugv_sdk` 共用同一个 socket，并在应用层做分发——实现复杂度较高，不推荐。

### 步进电机 Node ID 冲突

若 Ranger 内部 CAN 帧 ID 恰好落在 `0x580+NodeID` 或 `0x600+NodeID` 范围内，需将步进电机 Node ID 改为不冲突的值（通过 AIMotor 上位机修改 `H0C_00`）。当前 Node ID=1 对应 `0x581/0x601`，与 Ranger 协议不冲突。

---

## 文件说明

| 文件 | 说明 |
|------|------|
| `stepper_motor_sdk/scripts/setup_shared_can.sh` | 一键配置 vcan0 + can-gw 网关 |
| `stepper_motor_sdk/scripts/setup_jetson_can.sh` | 配置物理 can0 接口 |
| `lift_control.py` | 升降平台控制（使用 vcan0） |
| `ugv_sdk/ranger_ros2-humble/` | Ranger Mini V3 ROS2 驱动 |
