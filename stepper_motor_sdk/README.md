# stepper_motor_sdk

基于 **CiA 402 / CANopen** 标准的步进电机控制 Python SDK，适用于 Jetson AGX + SN65HVD230 + AIMotor 系列驱动器。

---

## 目录结构

```
stepper_motor_sdk/
├── setup.py
├── requirements.txt
├── scripts/
│   ├── install.sh              # 系统级一键安装（Ubuntu/Jetson）
│   ├── install_conda.sh        # conda/venv 环境安装
│   └── setup_jetson_can.sh     # Jetson CAN 接口配置
├── stepper_motor_sdk/
│   ├── __init__.py
│   ├── constants.py            # CiA 402 对象字典、控制字/状态字位定义
│   ├── sdo.py                  # SDO 通信层（阻塞式 expedited read/write）
│   ├── cia402.py               # CiA 402 状态机
│   ├── bus.py                  # SharedCANBus（多节点共享总线）
│   ├── node.py                 # CANopenNode（总线监听 + SDO 路由）
│   └── motor.py                # 高层控制 API
└── examples/
    ├── 00_diagnose.py          # CAN 链路诊断
    ├── 01_velocity_mode.py     # PV 轮廓速度模式
    ├── 02_profile_position.py  # PP 轮廓位置模式
    ├── 03_homing.py            # HM 回原点模式
    └── 04_multi_node.py        # 多节点共享总线
```

---

## 硬件要求

| 项目 | 规格 |
|------|------|
| 主控 | Jetson AGX（CAN0：Pin 29/31） |
| 收发器 | SN65HVD230（3.3 V） |
| 驱动器 | AIMotor CiA 402，出厂默认 Node ID=1，波特率=500 kbps，1000 Pul/rev |
| 总线终端 | 两端各接 120 Ω 终端电阻 |

---

## 接线图

### Jetson AGX ↔ SN65HVD230 ↔ AIMotor 驱动器

```
Jetson AGX 40-Pin Header
┌─────────────────────────────┐
│  Pin 29  CAN0_RX  ──────────┼──→  SN65HVD230  R   (接收输出)
│  Pin 31  CAN0_TX  ──────────┼──→  SN65HVD230  D   (发送输入)
│  Pin  1  3.3V     ──────────┼──→  SN65HVD230  VCC
│  Pin  6  GND      ──────────┼──→  SN65HVD230  GND
└─────────────────────────────┘

SN65HVD230
┌──────────────────────────────┐
│  VCC  3.3V                   │
│  GND  0V                     │
│  D    ← CAN0_TX (Pin 31)     │
│  R    → CAN0_RX (Pin 29)     │
│  Rs   → GND  (高速模式，直接接地)│
│  CANH ──────────────────────┼──→ AIMotor  CANH
│  CANL ──────────────────────┼──→ AIMotor  CANL
└──────────────────────────────┘

终端电阻（两端各一个）
  CANH ──┬── 120Ω ──┬── CANL
         │          │
    (Jetson端)  (驱动器端)
```

### 引脚对照表

| Jetson Pin | 信号 | SN65HVD230 引脚 | 说明 |
|-----------|------|----------------|------|
| Pin 29 | CAN0_RX | R | Jetson 接收，收发器输出 |
| Pin 31 | CAN0_TX | D | Jetson 发送，收发器输入 |
| Pin 1 | 3.3V | VCC | 收发器电源 |
| Pin 6 | GND | GND / Rs | GND 同时接 Rs（高速模式） |

| SN65HVD230 引脚 | AIMotor 端子 | 说明 |
|----------------|-------------|------|
| CANH | CANH | CAN 差分高 |
| CANL | CANL | CAN 差分低 |

> **注意**
> - Rs 引脚直接接 GND 为高速模式（最高 1 Mbps），悬空为斜率控制模式（EMI 较低）。
> - 总线两端（Jetson 侧和最远端驱动器侧）各接一个 120 Ω 终端电阻，接在 CANH 与 CANL 之间。
> - 多台驱动器串联时，CANH/CANL 手拉手连接，只有首尾两端接终端电阻。
> - SN65HVD230 工作电压 3.3 V，与 Jetson IO 电平直接兼容，无需电平转换。

---

## 安装

### 第一步：开启 Jetson CAN 引脚

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
# 选择 Configure Jetson 40pin Header → Configure header pins manually
# 勾选 can0（Pin 29/31）→ Save and reboot
```

### 第二步：配置 CAN 接口

```bash
sudo bash scripts/setup_jetson_can.sh can0 500000
```

### 第三步：安装 SDK（conda/venv 环境）

```bash
conda activate robot_env        # 或 source venv/bin/activate
bash scripts/install_conda.sh
```

验证安装：

```bash
python3 -c "import stepper_motor_sdk; print(stepper_motor_sdk.__version__)"
```

---

## 快速开始

### 诊断 CAN 链路

```bash
python3 examples/00_diagnose.py
```

### 速度控制（PV 模式）

```python
from stepper_motor_sdk import CANopenNode, StepperMotor

with CANopenNode(channel="can0", node_id=1) as node:
    motor = StepperMotor(node, pulses_per_rev=1000)
    motor.enable()
    motor.set_velocity_mode(acceleration=100_000, deceleration=100_000)
    motor.set_velocity_rpm(600)          # 600 r/min = 10000 Pul/s
    # ... do work ...
    motor.stop()
    motor.shutdown()
```

### 位置控制（PP 模式）

```python
motor.enable()
motor.set_position_mode(profile_velocity=10_000)
motor.move_absolute_rev(5.0)    # 移动到绝对位置 5 圈
motor.move_relative_rev(2.0)    # 再相对移动 2 圈
motor.move_absolute(0)          # 返回原点
```

### 回原点（HM 模式）

```python
from stepper_motor_sdk import HomingMethod

motor.enable()
motor.set_homing_mode(method=HomingMethod.CURRENT_POSITION)  # 方式 35：当前位置即原点
motor.home(wait=True, timeout=10.0)
print(motor.get_position())     # 应为 0
```

### 多节点控制

```python
from stepper_motor_sdk import SharedCANBus, CANopenNode, StepperMotor

with SharedCANBus(channel="can0") as shared:
    node1 = CANopenNode(node_id=1, shared_bus=shared).start()
    node2 = CANopenNode(node_id=2, shared_bus=shared).start()
    motor1 = StepperMotor(node1)
    motor2 = StepperMotor(node2)
    motor1.enable(); motor2.enable()
    motor1.set_velocity_mode(); motor2.set_velocity_mode()
    motor1.set_velocity_rpm(300)
    motor2.set_velocity_rpm(-300)
```

---

## API 参考

### `StepperMotor`

| 方法 | 说明 |
|------|------|
| `enable()` | 状态机自动切换至 Operation Enabled |
| `disable()` | 切换至 Switched On（保持上电，无力矩） |
| `shutdown()` | 释放力矩（Ready to Switch On） |
| `quick_stop()` | 急停减速 |
| `reset_fault()` | 清除故障 |
| `set_velocity_mode(accel, decel)` | 切换 PV 模式并设置加减速 |
| `set_velocity(puls_per_s)` | 设置目标速度（Pul/s） |
| `set_velocity_rpm(rpm)` | 设置目标速度（r/min） |
| `stop()` | 减速至零 |
| `set_position_mode(vel, accel, decel)` | 切换 PP 模式并设置运动参数 |
| `move_absolute(puls)` | 绝对位置移动（Pul） |
| `move_relative(puls)` | 相对位置移动（Pul） |
| `move_absolute_rev(rev)` | 绝对位置移动（圈） |
| `move_relative_rev(rev)` | 相对位置移动（圈） |
| `set_torque_mode(max_vel)` | 切换 PT 模式 |
| `set_torque(permille)` | 设置目标转矩（1000 = 额定） |
| `set_homing_mode(method, ...)` | 配置回原点参数 |
| `home(wait, timeout)` | 执行回原点 |
| `get_position()` | 读取实际位置（Pul） |
| `get_velocity()` | 读取实际速度（Pul/s） |
| `get_torque()` | 读取实际转矩（0.1%） |
| `get_position_rev()` | 读取实际位置（圈） |
| `get_velocity_rpm()` | 读取实际速度（r/min） |
| `rpm_to_puls(rpm)` | 单位转换：r/min → Pul/s |
| `rev_to_puls(rev)` | 单位转换：圈 → Pul |

### 单位换算

| 量 | SDK 单位 | 换算（默认 1000 Pul/rev） |
|----|----------|--------------------------|
| 速度 | Pul/s | 600 r/min = 10000 Pul/s |
| 加速度 | Pul/s² | 0→600 r/min in 3 s = 3333 Pul/s² |
| 位置 | Pul | 5 rev = 5000 Pul |

---

## 示例说明

### 00_diagnose.py — CAN 链路诊断

运行其他示例前先执行此脚本，分两步验证通信：

- **Step 1**：监听总线 3 秒，打印所有收到的原始帧。若无任何帧，说明接线或接口配置有问题
- **Step 2**：通过 SDO 读取状态字（0x6041），成功则打印十六进制值。失败时给出四条常见原因提示

```
python3 examples/00_diagnose.py
```

---

### 01_velocity_mode.py — PV 轮廓速度模式

对应 test log.md 中的手动 `cansend` 指令序列，用 SDK 复现相同操作：

1. `CANopenNode` 打开 socketcan 接口，启动后台监听线程
2. `motor.enable()` 自动驱动 CiA 402 状态机：`Switch On Disabled → Ready to Switch On → Switched On → Operation Enabled`，等价于依次发送 `0x06 / 0x07 / 0x0F`
3. `set_velocity_mode()` 写入模式寄存器（6060h = 3）并配置加减速（6083h / 6084h）
4. `set_velocity_rpm(600)` 将 600 r/min 换算为 10000 Pul/s 写入目标速度寄存器（60FFh）
5. 运行 3 秒后通过 SDO 读回实际速度和位置
6. `stop()` 将目标速度置 0，驱动器按减速曲线停车
7. `shutdown()` 写控制字 `0x06`，释放力矩

```
python3 examples/01_velocity_mode.py
```

---

### 02_profile_position.py — PP 轮廓位置模式

演示绝对定位和相对定位：

1. `set_position_mode()` 切换至 PP 模式（6060h = 1），同时写入轮廓速度（6081h）、加速度（6083h）、减速度（6084h）
2. `move_absolute_rev(5.0)` 将 5 圈换算为 5000 Pul 写入目标位置（607Ah），然后在控制字上产生 `NEW_SETPOINT` 上升沿（0x0F → 0x1F）触发运动，阻塞等待 `TARGET_REACHED`（状态字 bit 10）置位后返回
3. `move_relative_rev(2.0)` 同理，但控制字额外置位 `ABS_REL`（bit 6），驱动器以当前位置为基准做相对移动
4. `move_absolute(0)` 返回绝对零点

```
python3 examples/02_profile_position.py
```

---

### 03_homing.py — HM 回原点模式

演示方式 35（当前位置即原点）：

1. `set_homing_mode()` 切换至 HM 模式（6060h = 6），写入回零方法（6098h = 35）、高速（6099:01）、低速（6099:02）、加速度（609Ah）、原点偏移（607Ch）
2. `home()` 在控制字上产生 bit 4 上升沿（0x0F → 0x1F）启动回零；方式 35 不产生物理运动，驱动器立即将位置计数器清零并置位状态字 `HOME_ATTAINED`（bit 15）
3. 归零后切换至 PP 模式验证：从位置 0 移动到 3 圈，确认位置坐标系已正确建立

> **注意**：方式 35 仅做电子清零，电机轴不会转动。需要找限位开关或 Z 信号归零时，请使用 `HomingMethod.HOME_SWITCH_POS`（方式 24）等其他方法。

```
python3 examples/03_homing.py
```

---

### 04_multi_node.py — 多节点共享总线

演示在同一条 CAN 总线上同时控制多个电机：

**核心问题**：多个 `CANopenNode` 若各自启动监听线程对同一个 socket 调用 `recv()`，会互相抢帧——node2 的线程可能先拿到 node1 的 SDO 回包并丢弃，导致超时。

**解决方案**：`SharedCANBus` 持有唯一监听线程，按 `node_id` 精确路由到各自的 `SDOClient`：

```
SharedCANBus（1个监听线程）
├── SDOClient  node_id=1  ←  0x581
└── SDOClient  node_id=2  ←  0x582
```

`make_motor()` 函数在 enable 失败时捕获 `SDOError` 并跳过不存在的节点，总线上有几个电机就控制几个，不会崩溃。

> **注意**：`NODE_IDS = [1, 2]` 需与实际驱动器的 Node ID 一致。出厂默认均为 1，多电机时需通过上位机软件（AIMotor RS485 调试工具）修改 H0C_00 参数为不同 ID。

```
python3 examples/04_multi_node.py
```

---

## 支持的运行模式

| 模式 | 值 | 说明 |
|------|----|------|
| PP | 1 | 轮廓位置模式 |
| PV | 3 | 轮廓速度模式 |
| PT | 4 | 轮廓转矩模式 |
| HM | 6 | 回原点模式 |
| CSP | 8 | 循环同步位置模式 |
