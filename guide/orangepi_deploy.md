# Orange Pi 部署指南

## 环境概览

| 项目 | 值 |
|------|-----|
| 硬件 | Orange Pi Zero 2W (AArch64) |
| OS | Orange Pi 1.0.4 Noble (Ubuntu 24.04) |
| Python | 3.12.3（系统预装，无 pip） |
| 工作目录 | `/home/orangepi/` |
| 源码目录 | `pr_mini/` |
| 加密产物 | `robot_encrypted/` |
| 服务端口 | 6080 |
| 日志文件 | `/var/log/robot_control_panel.log` |

---

## 第一步：准备系统环境

### 1.1 安装 pip

系统 Python 不带 pip，需手动安装：

```bash
curl -s https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
python3 /tmp/get-pip.py --break-system-packages
```

安装后 pip 在 `~/.local/bin/`，使用时加 PATH 或通过 `python3 -m pip` 调用。

> **注意**：Orange Pi 官方系统启用了 PEP 668，所有 pip install 必须加 `--break-system-packages`。

### 1.2 安装 ffmpeg（摄像头推流必需）

```bash
echo orangepi | sudo -S apt-get update
echo orangepi | sudo -S apt-get install -y ffmpeg
```

### 1.3 安装 Python 依赖

```bash
export PATH="$HOME/.local/bin:$PATH"
python3 -m pip install --break-system-packages \
  flask \
  flask-socketio \
  python-can
```

### 1.4 安装 PyArmor（代码加密）

从 PC 下载对应架构的 wheel，上传到香橙派后离线安装：

```bash
# PC 上：
pip download pyarmor==9.2.5 --platform manylinux2014_aarch64 --python-version 312 --only-binary=:all -d ./wheels
# 上传 wheels/ 到香橙派 /tmp/wheels/

# 香橙派上：
python3 -m pip install --break-system-packages /tmp/wheels/*.whl
```

---

## 第二步：上传源码

通过局域网 SCP 上传整个 `robot_control/` 到 `/home/orangepi/pr_mini/robot_control/`，同时上传本地 SDK 包：

```
pr_mini/
  robot_control/          # 源码（明文，保留用于后续加密）
    __init__.py
    robot.py
    arm.py
    camera.py
    hand.py
    lift.py
    module_base.py
    motor.py
    ugv.py
    requirements.txt
    web_gui/
      __init__.py
      server.py
      auth.py
      i18n.py
      templates/
      static/
  motor_sdk/              # 直流电机 SDK（GPIO）
  stepper_motor_sdk/      # 升降平台 SDK（CAN 总线）
  hand_brainco_sdk/       # 灵巧手 SDK（CAN 总线，可选）
```

---

## 第三步：运行 PyArmor 加密

```bash
cd /home/orangepi/pr_mini
rm -rf /home/orangepi/robot_encrypted
pyarmor gen --output /home/orangepi/robot_encrypted robot_control
```

加密后产物在 `/home/orangepi/robot_encrypted/`，包含：
- `pyarmor_runtime_000000/pyarmor_runtime.so` — AArch64 运行时
- `robot_control/*.py` — 混淆后的 .py 文件
- `robot_control/web_gui/*.py` — 混淆后的 Web GUI

### 复制明文资源

模板和静态文件不需要加密，直接复制：

```bash
cp -r /home/orangepi/pr_mini/robot_control/web_gui/templates \
      /home/orangepi/robot_encrypted/robot_control/web_gui/
cp -r /home/orangepi/pr_mini/robot_control/web_gui/static \
      /home/orangepi/robot_encrypted/robot_control/web_gui/
```

### 上传启动器

`run_server.py` 是明文启动脚本（不参与加密），放在加密目录根下：

```bash
cp /home/orangepi/pr_mini/run_server.py /home/orangepi/robot_encrypted/run_server.py
```

### 复制 SDK 到加密目录

SDK 路径相对于加密后的模块，需放在 `robot_encrypted/` 下：

```bash
cp -r /home/orangepi/pr_mini/motor_sdk /home/orangepi/robot_encrypted/motor_sdk
cp -r /home/orangepi/pr_mini/stepper_motor_sdk /home/orangepi/robot_encrypted/stepper_motor_sdk
```

最终加密目录结构：

```
robot_encrypted/
  run_server.py                     # 明文启动器
  pyarmor_runtime_000000/           # PyArmor 运行时
  robot_control/                    # 加密后的源码
  motor_sdk/                        # 电机 SDK
  stepper_motor_sdk/                # 升降台 SDK
```

---

## 第四步：安装 Systemd 服务

### 控制面板服务

创建 `/etc/systemd/system/robot_control_panel.service`：

```ini
[Unit]
Description=Robot Control Panel (Encrypted)
After=network.target

[Service]
Type=simple
User=orangepi
WorkingDirectory=/home/orangepi/robot_encrypted
ExecStart=/usr/bin/python3 /home/orangepi/robot_encrypted/run_server.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/robot_control_panel.log
StandardError=append:/var/log/robot_control_panel.log

[Install]
WantedBy=multi-user.target
```

启用并启动：

```bash
echo orangepi | sudo -S systemctl daemon-reload
echo orangepi | sudo -S systemctl enable robot_control_panel.service
echo orangepi | sudo -S systemctl start robot_control_panel.service
```

### WiFi 热点服务（选配）

创建 `/etc/systemd/system/pipe_ap.service`：

```ini
[Unit]
Description=PipeRobot WiFi Hotspot
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/create_ap -n --no-virt --isolate-clients -w 2 --freq-band 2.4 wlan0 PipeRobot pipemaker
ExecStop=/usr/bin/create_ap --stop wlan0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **注意**：开启热点后 Orange Pi 自身无法通过 WiFi 上网（单网卡限制）。需要远程访问时请关闭热点并连回外部 WiFi。

---

## 第五步：验证

```bash
# 查看服务状态
sudo systemctl status robot_control_panel

# 查看启动日志
tail -30 /var/log/robot_control_panel.log

# 预期看到 6 个模块加载
# [Robot] 6 module(s) started: ['arm', 'ugv', 'lift', 'camera', 'motor', 'hand']

# 测试 HTTP
curl -s -o /dev/null -w "%{http_code}" http://localhost:6080/login
# 预期 200

# 验证加密模块正常导入
cd /home/orangepi/robot_encrypted
python3 -c "from robot_control.robot import Robot; print('OK')"
```

---

## 常见问题排查

### 网站显示"未连接"

1. 确认连接了正确的 WiFi（热点模式 `PipeRobot` 或局域网 IP）
2. SSH 登录：`ssh orangepi@<机器人IP>`，密码 `orangepi`
3. 重启服务：`echo orangepi | sudo -S systemctl restart robot_control_panel`
4. 详见过 `guide/troubleshooting.md`

### 摄像头无画面

1. 确认 USB 摄像头已插入 Orange Pi
2. 检查设备：`ls /dev/video*`（应看到 `/dev/video1`）
3. 检查 USB：`lsusb`（应看到摄像头设备）
4. 确认 ffmpeg 已安装：`which ffmpeg`
5. 摄像头模块支持热插拔，插入后自动检测，无需重启

### 电机不转 / GPIO 报错

- 检查日志：`grep -i motor /var/log/robot_control_panel.log`
- 预期看到：`Motor] Controller started — differential drive ready`
- 如果报 `motor_sdk not available`：检查 `robot_encrypted/motor_sdk/` 是否存在
- GPIO 引脚：左轮 IN1=29, IN2=31；右轮 IN1=35, IN2=37

### 升降台不工作

- 检查 `python-can` 是否安装：`python3 -c "import can; print(can.__version__)"`
- 如果报 `No such device`：CAN 总线硬件未连接（正常情况）
- 如果报 `No module named 'stepper_motor_sdk'`：检查 SDK 是否复制到 `robot_encrypted/stepper_motor_sdk/`

### PyArmor 加密报错

- `undefined symbol: _PyFloat_Pack8`：Python 版本不匹配。加密时的 Python 版本必须和运行时的版本一致（都是 3.12）
- 加密文件必须在目标机器上生成，不能跨设备复用不同 Python 版本的加密产物
- 加密文件生成后，复制回 PC 本地备份，避免重新部署时丢失

### 查看实时日志

```bash
tail -f /var/log/robot_control_panel.log
```

---

## 部署检查清单

- [ ] ffmpeg 已安装
- [ ] Flask / flask-socketio / python-can 已安装
- [ ] PyArmor 已安装且在机器人上运行过加密
- [ ] `motor_sdk/` 和 `stepper_motor_sdk/` 已复制到加密目录
- [ ] `run_server.py` 已就位
- [ ] `templates/` 和 `static/` 已复制
- [ ] systemd 服务已安装并 enabled
- [ ] 服务启动后 6 个模块全部加载
- [ ] HTTP 200 正常响应
- [ ] USB 摄像头已插入（如需画面）
- [ ] 管理员账户已注册（首次启动）
