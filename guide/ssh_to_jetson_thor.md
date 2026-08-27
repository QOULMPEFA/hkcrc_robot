# Jetson Thor — SSH & Deployment

## SSH & Connection

- **局域网**: `192.168.20.66` | User: `hkcrc1` | Pass: ` `（空格键）
- Workspace: `/home/hkcrc1/robot_code`

### 免密登录（一次性配置）

把本机公钥加到 Jetson，后续 ssh 不再需要密码：

```bash
# 在本机 (Windows/Linux) 执行：
cat ~/.ssh/id_rsa.pub | ssh hkcrc1@192.168.20.66 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

（公钥与本机 `~/.ssh/id_rsa.pub`，同 `ssh_to_jetson.md` 中 `jialy@hkcrc.hk`。）

## 部署状态（2026-08-27 移植完成）

本机 `code/` 源码已打包上传至 `/home/hkcrc1/robot_code`，核心 Web GUI + 服务已运行。

| 项目 | 值 |
|------|-----|
| 设备 | NVIDIA Thor (aarch64) |
| 系统 | Ubuntu 24.04 (kernel 6.8.12-tegra) |
| Python | 3.12.3（系统 `/usr/bin/python3`）|
| GPU | NVIDIA Thor, Driver 580, CUDA 13.0 |
| Web GUI | `http://192.168.20.66:6080`（登录后使用）|
| 源码 | `/home/hkcrc1/robot_code` |

### systemd 服务（已启用自启）

- `robot_control_panel.service` — Web GUI (6080)，User=hkcrc1
- `setup-can0.service` — can0 @ 500 kbps（升降平台）
- `setup-can1.service` — can1 @ 1 Mbps（灵巧手 CAN 预留）

```bash
systemctl status robot_control_panel.service   # active (running)
systemctl restart robot_control_panel.service
tail -f /var/log/robot_control_panel.log
```

### 依赖（已安装）

- 系统 pip（apt `python3-pip` 24.0）
- Flask 栈：离线 wheels（`pip_wheels/`，cp312-aarch64）：flask 3.1.3 / flask-socketio 5.6.1 / engineio 4.13.3 / socketio 5.16.3
- `python3-numpy` (1.26.4)、`python3-serial`、`ffmpeg`（apt）
- `python-can` (4.6.1)、`bc_stark_sdk` (2.0.3)（pip，--break-system-packages）

### 模块启动情况（硬件未接，预期降级）

启动日志 `7 module(s) started: arm, ugv, lift, camera, hand, teleop, gimbal`（2026-08-27 起移除"电机控制"模块，见 server.py active config）：
- `arm` 连接循环 192.168.56.101（UR 未接 → 待连接）
- `ugv` bridge 127.0.0.1:9100（C++ 二进制未构建 → 待构建）
- `lift` 走 CAN，SDO 超时 → stub（无电机）
- `camera` 0 设备（无摄像头）
- `hand` bc_stark_sdk 已装，等待 RS485 检测（无端口）
- `teleop` xrobotoolkit_sdk 未装 → VR backend 初始化失败（遥操作暂不可用）
- `gimbal` CH340 串口扫描 7 设备未匹配 → 待接串口

### GUI 系统设置里的 Sudo 密码（默认空格）

Web GUI 系统设置里的 "Sudo 密码"（用户俗称"速度密码"）用于重启/关机/USB 重置/CAN 重启。**默认已改为空格**（`server.py` / `camera.py` 里 `_get_sudo_password()` / `_get_sudo_input()` 默认 `" "`，2026-08-27）。Thor 的 sudo 密码本来就是空格，改对。

> 2026-08-27 修过 bug：重启/关机曾用 `echo {pw}.strip() | sudo -S` 拼 shell，空格密码被 strip 掉 + shell 拆分 → sudo 认证失败、按重启没反应。已改为 `subprocess.Popen(["sudo","-S",...], stdin=PIPE)` 管道直传密码（与 `restart_can` 同款写法），空格密码实测 rc=0。

### 无显示器虚拟桌面（与旧 Jetson 相同机制，已验证）

Thor 已具备完整的无显示器虚拟桌面自启配置，与旧 Jetson（192.168.20.28）机制一致：

- `display-manager.service → gdm3.service`（enabled）+ `graphical.target` 默认
- `/etc/X11/xorg.conf`：nvidia 驱动 + `Option "AllowEmptyInitialConfiguration" "true"` + `BusID "PCI:1:0:0"`
- `/etc/X11/xorg.conf.d/tegra-drm-outputclass.conf`：nvidia OutputClass
- `/etc/gdm3/custom.conf`：`WaylandEnable=false`
- `/etc/systemd/system/gdm.service.d/override.conf`：`ExecStartPre=`（清掉 NVIDIA 无头失败步骤）
- 验证：`Xorg vt2` 运行中，`hkcrc1 :1` 有 GNOME 桌面（gnome-shell），`DISPLAY=:1 xrandr` 显示 1920x1080；无 VNC（与旧 Jetson 相同）

### AnyDesk 远程（已配置，开机自启）

- `anydesk.service`（8.0.4 arm64）已 enable + 运行，**ID `1285628773`**，无人值守密码已设（`echo '新密码' | sudo anydesk --set-password` 可重置）
- **自动登录已开**（2026-08-27）：`/etc/gdm3/custom.conf` 加 `AutomaticLoginEnable=true` / `AutomaticLogin=hkcrc1` → 重启直接进桌面，AnyDesk 一连即桌面
  - 关闭自动登录：注释掉 custom.conf 里这两行后 `sudo systemctl restart gdm3`（或重启）
  - AnyDesk 自启开关：`sudo systemctl enable/disable anydesk`
  - 虚拟桌面开关：`sudo systemctl set-default graphical.target`（开）/ `multi-user.target`（关，纯命令行）

### 虚拟桌面切换命令 to-screen / to-headless（2026-08-27 复刻旧 Jetson）

`~/.bashrc` 里加了两个别名（需新开终端或 `source ~/.bashrc` 生效）：

- `to-screen`：把 `/usr/share/X11/xorg.conf.d/xorg.conf`（dummy 虚拟显卡配置）移成 `.bak` → 重启 → 用物理显示器
- `to-headless`：恢复 `.bak` → 重启 → 无显示器虚拟桌面（1920x1080，AnyDesk 远程用）

配套已装：`xserver-xorg-video-dummy` 包 + `/usr/share/X11/xorg.conf.d/xorg.conf`（dummy 驱动 1920x1080，root:root 644）。运行别名时会提示输入 sudo 密码（hkcrc1 的密码是**单个空格**）。当前状态 = headless（配置文件在位，无 .bak）。

## 待办（硬件接入后）

1. **ugv_bridge**：`ugv_sdk` C++ 需构建 `build/bin/ugv_bridge`，再装 `ugv_bridge.service`（参考 `systemd/ugv_bridge.service`）
2. **XR 遥操作**：装 `xrobotoolkit_sdk`（`teleoperation_sdk/XRoboToolkit-PC-Service-Pybind`，`setup_orin.sh`）
3. **UR 机械臂**：UR5e 只在 `192.168.56.x` 网段可达，需接有线网 `eno1`/`eth` 后联调
4. **camera**：接 USB 摄像头 / RealSense 后 auto-detect
5. **灵巧手**：接 USB-RS485 (CH340) 后 Modbus auto-detect 端口
