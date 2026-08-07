# 管道机器人 Mini 部署流程

## 设备信息

| 项目 | 值 |
|------|-----|
| 设备 | Orange Pi Zero 2W (aarch64) |
| 系统 | Ubuntu 24.04 Noble (Orange Pi 1.0.4) |
| Python | 3.12.3 |
| SSH 别名 | `pr_mini` |
| SSH 连接 | `frp-all.com:35000` / `orangepi` / `orangepi` |
| 工作目录 | `/home/orangepi/pr_mini/` |

## 一、SSH 配置

`~/.ssh/config` 中必须包含 `# password:` 注释元数据，ssh-skill 脚本依赖它：

```
# ===== pr_mini =====
# description: 管道机器人 Mini
# password: orangepi
Host pr_mini
    HostName frp-all.com
    User orangepi
    Port 35000
```

## 二、PyArmor 加密（Python 版本必须匹配）

**核心原则**：加密机器的 Python 版本 + 目标平台架构 = Mini 的 Python 版本 + 架构。

| | Python | 架构 |
|---|---|---|
| 本地加密机 | 3.12.13 (conda `robot_enc`) | Windows x86_64 |
| Mini 目标 | 3.12.3 | Linux aarch64 |

**本地加密命令**：

```bash
# 1. 创建 Python 3.12 加密环境（一次性）
conda create -n robot_enc python=3.12 -y
conda run -n robot_enc pip install pyarmor

# 2. 加密（每次源码更新后执行）
cd code/
conda run -n robot_enc pyarmor gen --recursive \
    --platform linux.aarch64 \
    --exclude "__pycache__" --exclude "*.db" \
    -O robot_encrypted robot_control/

# 3. 补充 web 静态资源（templates/static/ 不会被加密工具复制）
cp -r robot_control/web_gui/templates robot_encrypted/robot_control/web_gui/
cp -r robot_control/web_gui/static robot_encrypted/robot_control/web_gui/

# 4. 补充辅助文件（手动维护）
cp run_server.py robot_encrypted/
cp -r systemd/ robot_encrypted/systemd/
```

**为什么不能跨 Python 版本**：

PyArmor 的 `pyarmor_runtime.so` 是编译的 C 扩展，绑定到加密时的 Python C API。Python 3.10 加密的 runtime 在 3.12 上报 `undefined symbol: _PyFloat_Pack8`。

`--platform linux.aarch64` 解决 x86_64 → ARM64 的跨架构问题。

## 三、上传部署

**推荐方式：tar.gz 打包上传**（避免递归上传因中文路径 / 文件数量多而失败）

```bash
# 1. 本地打包（在 robot_encrypted 目录下）
cd D:/12_Projects/.../code/robot_encrypted/
tar -czf /tmp/pr_mini_deploy.tar.gz robot_control pyarmor_runtime_000000 run_server.py

# 2. motor_sdk 单独打包
cd D:/12_Projects/.../code/
tar -czf /tmp/motor_sdk.tar.gz motor_sdk

# 3. 创建干净目录 + 修复权限
python ~/.claude/skills/ssh-skill/scripts/ssh_execute.py pr_mini \
    "echo orangepi | sudo -S bash -c 'rm -rf /home/orangepi/pr_mini/*; mkdir -p /home/orangepi/pr_mini; chown -R orangepi:orangepi /home/orangepi/pr_mini'" --no-daemon --timeout 10

# 4. 上传 tar.gz（注意：Windows 下 /tmp 映射到 %LOCALAPPDATA%/Temp）
#    先用 cygpath -w /tmp/xxx.tar.gz 获取 Windows 路径
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py pr_mini \
    "C:/Users/.../AppData/Local/Temp/pr_mini_deploy.tar.gz" \
    "/home/orangepi/pr_mini/deploy.tar.gz"
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py pr_mini \
    "C:/Users/.../AppData/Local/Temp/motor_sdk.tar.gz" \
    "/home/orangepi/pr_mini/motor_sdk.tar.gz"

# 5. 解压 + 清理
python ~/.claude/skills/ssh-skill/scripts/ssh_execute.py pr_mini \
    "cd /home/orangepi/pr_mini && tar -xzf deploy.tar.gz && tar -xzf motor_sdk.tar.gz && rm *.tar.gz" --no-daemon --timeout 10
```

> **为什么不用 `--recursive` 逐个上传？** `ssh_upload.py --recursive` 遇到包含中文的路径时会报 `[Errno 2] No such file`，即使使用 `MSYS_NO_PATHCONV=1` 也无法解决。打包成单个 tar.gz 后再上传一次成功。

> **为什么需要 `chown`？** 上一步 `mkdir` 如果通过 `sudo` 执行，目录属主会变成 root，导致后续 SFTP 上传报 `Permission denied`。

**备选：逐个文件上传**（仅当路径不含中文且文件较少时可用）

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py pr_mini \
    "D:/12_Projects/.../code/robot_encrypted/robot_control" \
    "/home/orangepi/pr_mini/robot_control" --recursive
# ... 其余文件同理
```

**注意**：`robot_encrypted` 目录的内容直接写入 `/home/orangepi/pr_mini/`，不保留外层 `robot_encrypted/` 目录名。`run_server.py` 自动检测项目根目录，无需修改路径。

## 四、依赖安装

### 系统依赖

```bash
# ⚠️ wiringpi 被 Orange Pi 镜像 hold，不能和 ffmpeg 一起装，必须分开
echo orangepi | sudo -S apt install -y ffmpeg python3-pip
```

> **为什么不能加 wiringpi？** Orange Pi 用 `apt-mark hold wiringpi` 锁定了 wiringpi 包，防止 Ubuntu 的 v2.50 覆盖 Orange Pi 专用版。如果和 ffmpeg 一起 `apt install`，整个事务会因 `--allow-change-held-packages` 缺失而失败。

> **apt 锁冲突**：系统首次启动后 `unattended-upgrades` 可能占用 apt 锁。如果遇到 `Could not get lock`，先 `sudo kill -9` 清理 apt 进程，再 `sudo rm -f /var/lib/apt/lists/lock /var/lib/dpkg/lock-frontend`。

- `ffmpeg` — 摄像头 MJPEG 流采集
- `python3-pip` — Python 包管理器（Orange Pi 镜像可能预装）

### wiringOP 编译（关键！）

**Ubuntu 的 `wiringpi` 包（v2.50）只支持树莓派，不支持 Orange Pi。** 系统自带 Orange Pi 专用的 wiringOP v2.56 源码，必须编译安装：

```bash
echo orangepi | sudo -S bash -c 'cd /usr/src/wiringOP && ./build clean && ./build'
```

编译后 `/usr/local/bin/gpio`（v2.46）会覆盖 Ubuntu 的 `/usr/bin/gpio`（v2.50）。验证：

```bash
gpio -v
# 预期输出包含: *--> OrangePi Zero2 W
```

### gpio sudoers 配置

电机控制需要 sudo 权限操作 GPIO：

```bash
echo orangepi | sudo -S bash -c 'echo "orangepi ALL=(ALL) NOPASSWD: /usr/local/bin/gpio" > /etc/sudoers.d/orangepi-gpio && chmod 440 /etc/sudoers.d/orangepi-gpio'
```

### Python 依赖

```bash
python3 -m pip install --break-system-packages \
    'flask>=3.0.0' 'flask-socketio>=5.3.6' \
    'python-socketio>=5.11.0' 'python-engineio>=4.9.0'
```

**踩坑**：Orange Pi 系统镜像使用华为 apt 源，`python3-setuptools` 包在镜像上 404。不要跟 apt 较劲——直接用 `pip3`（`/home/orangepi/.local/bin/` 下的 pip 不在 PATH 里，用 `python3 -m pip` 命令即可）。

`motor_sdk` 是本地包，使用 wiringOP `gpio` CLI 控制 GPIO，无 Python 依赖，无需 pip install。`robot_control/motor.py` 会在启动时将 `motor_sdk/` 目录加入 sys.path。

## 五、systemd 服务

### robot_control_panel.service — 机器人 Web 控制面板

**必须包含 `Environment=PATH=`**，否则 ffmpeg 找不到（systemd 默认 PATH 不含 `/usr/bin`）：

```ini
[Unit]
Description=Robot Control Panel (Encrypted)
After=network.target

[Service]
Type=simple
User=orangepi
WorkingDirectory=/home/orangepi/pr_mini
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/orangepi/.local/bin
ExecStart=/usr/bin/python3 /home/orangepi/pr_mini/run_server.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/robot_control_panel.log
StandardError=append:/var/log/robot_control_panel.log

[Install]
WantedBy=multi-user.target
```

**安装**：

```bash
echo orangepi | sudo -S bash -c 'cat > /etc/systemd/system/robot_control_panel.service << EOF
[Unit]
Description=Robot Control Panel (Encrypted)
After=network.target

[Service]
Type=simple
User=orangepi
WorkingDirectory=/home/orangepi/pr_mini
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/home/orangepi/.local/bin
ExecStart=/usr/bin/python3 /home/orangepi/pr_mini/run_server.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/robot_control_panel.log
StandardError=append:/var/log/robot_control_panel.log

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable robot_control_panel.service
systemctl start robot_control_panel.service'
```

### pipe_ap.service — WiFi 热点（确认后再部署）

```ini
[Unit]
Description=PipeRobot WiFi Hotspot
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/bin/create_ap -n --no-virt --isolate-clients -w 2 --freq-band 2.4 wlan0 AAA_Robot_mini 88888888
ExecStop=/usr/bin/create_ap --stop wlan0
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**此服务等到机器人控制面板验证通过后再部署。** 部署命令见第六节。

## 六、热点部署（确认后执行）

**先验证 robot_control_panel 正常运行（摄像头、电机都 OK），再开热点。**

```bash
# 安装启用启动（执行后 WiFi 断开，FRP 不可达）
echo orangepi | sudo -S bash -c '
cp /home/orangepi/pr_mini/systemd/pipe_ap.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable pipe_ap.service
pkill create_ap 2>/dev/null || true
nmcli device disconnect wlan0 2>/dev/null || true
sleep 1
systemctl start pipe_ap.service
'
```

**热点启动后：**
- SSID: `AAA_Robot_mini`
- 密码: `88888888`
- Web GUI: `http://10.42.0.1:6080`
- 机器人不再连接互联网，FRP 不可用

### 开机自启热点

```bash
echo orangepi | sudo -S systemctl enable pipe_ap.service
```

### 从热点切回 WiFi

`create_ap` 启动热点时会在 `/etc/NetworkManager/NetworkManager.conf` 插入 `unmanaged-devices=interface-name:wlan0`，导致 NetworkManager 放弃管理 wlan0。关闭热点后这条配置不会自动删除，wlan0 永远显示 `unmanaged`（灰色），无法连接 WiFi。

使用 `/home/orangepi/switch_to_wifi.sh` 一键切回：

```bash
bash /home/orangepi/switch_to_wifi.sh
```

**脚本原理**：停热点 → 杀残留进程 → wlan0 从 AP 切回 managed 模式 → 删除 unmanaged-devices 行 → 重启 NetworkManager → 连接 WiFi。全程 `nohup` 后台执行，不依赖 SSH 连接。

脚本内容见 `guide/switch_to_wifi.sh`，首次部署时上传：

```bash
MSYS_NO_PATHCONV=1 python ~/.claude/skills/ssh-skill/scripts/ssh_upload.py pr_mini \
    "D:/12_Projects/.../code/guide/switch_to_wifi.sh" \
    "/home/orangepi/switch_to_wifi.sh"
```

### 移动端 WebSocket 修复

**现象**：Windows 浏览器打开 Web GUI 正常，手机浏览器显示「未连接」。

**原因**：`app.js` 中 SocketIO 只允许 `transports: ["websocket"]`。移动端浏览器或运营商网络可能阻止 WebSocket 握手，且没有 HTTP polling 回退，导致永远连不上。

**修复**（`robot_control/web_gui/static/app.js` 第 26 行）：

```diff
- const socket = io({ transports: ["websocket"], query: { lang: I18n._lang } });
+ const socket = io({ transports: ["websocket", "polling"], query: { lang: I18n._lang } });
```

加上 `polling` 后，WebSocket 失败时自动降级到 HTTP 长轮询。静态文件，上传后刷新即生效，无需重启服务。

## 七、sudo 密码处理

Mini 用密码认证，sudo 需要管道传密码：

```bash
echo orangepi | sudo -S <command>
```

**注意**：多个 `echo xxx | sudo -S` 串在同一行时，前面的失败可能消耗掉密码尝试次数导致后面的也失败。关键操作（如写文件）先用 `cat > /tmp/file` 无 sudo 写入，再 `sudo cp`。

## 八、模块说明

| 模块 | Mini 支持 | 依赖 | 说明 |
|------|----------|------|------|
| camera | 是 | ffmpeg, /dev/video0 | USB 摄像头 MJPEG 流 |
| motor | 是 | motor_sdk + wiringOP gpio | GPIO H 桥直流电机驱动 |
| drive | 降级 | motor_sdk.motor_sdk.uart2_stm32 | STM32 串口驱动（Mini 无此硬件） |
| joint | 降级 | pr_v2_sdk | 舵机/云台控制（Mini 不需要） |
| demo | 是 | 无 | 演示自动化流程 |
| ugv | 否 | CAN 总线 | 仅 Jetson 全尺寸机器人使用 |

**电机控制逻辑**（app.js 简化后）：按键/按钮按下 → 发送一次 move 命令 → GPIO 锁存 → 松开 → 发送一次 stop。不再每 50ms 重复发送，避免 keyup 丢失导致电机失控。

## 九、验证清单

```bash
# 服务状态
systemctl is-active robot_control_panel.service   # active

# gpio 识别
gpio -v 2>&1 | grep OrangePi                      # *--> OrangePi Zero2 W

# 端口
ss -tlnp | grep 6080                              # 0.0.0.0:6080

# 关键日志
grep -E "ffmpeg started|Motor.*init" /var/log/robot_control_panel.log
# 预期：
#   [Camera:cam0] ffmpeg started (pid=...) on /dev/video0 → MJPEG passthrough
#   [Motor] 左电机 init — phys 29/31 → wPi 19/20
#   [Motor] 右电机 init — phys 35/37 → wPi 23/25
#   [Motor] Controller started — differential drive ready

# 异常日志
grep -E "ERROR|WARNING" /var/log/robot_control_panel.log
# 以下 WARNING 可忽略：
#   [Joint] SDK not available: No module named 'pr_v2_sdk'     ← Mini 不需要
#   [Drive] Uart2Stm32 not available                            ← Mini 无 STM32
# 以下 ERROR 不可接受：
#   [Motor] gpio mode failed                                     ← wiringOP 未编译

# 导入验证
cd /home/orangepi/pr_mini && python3 -c "
import sys; sys.path.insert(0,'.')
from robot_control.web_gui.server import main
print('import OK')
"
```

## 十、关键坑位总结

| 坑 | 原因 | 解法                                            |
|----|------|-----------------------------------------------|
| PyArmor 加密后 Mini 上 import 报 `undefined symbol` | 本地 Python 3.10 加密的 runtime 用到 3.10 C API | 本地升级到 Python 3.12 再加密                         |
| `--platform` 不能少 | 本地 Windows x86_64, Mini Linux arm64 | `pyarmor gen --platform linux.aarch64`        |
| apt 华为镜像 404 | `python3-setuptools` 包缺失 | 放弃 apt，用 `pip3`                               |
| `pip` 命令找不到 | pip 装在 `/home/orangepi/.local/bin/`，不在 PATH | 用 `python3 -m pip`                            |
| sudo 密码管道串联失败 | 多个 sudo 串接时密码验证混乱 | 分步执行，关键操作先写 /tmp 再 sudo cp                    |
| `systemctl start` 报 `inactive` | 可能旧进程占用端口 | 先 pkill 再 start                               |
| **ffmpeg 未找到 (No such file)** | systemd 服务默认 PATH 不含 `/usr/bin` | service 文件加 `Environment=PATH=...`            |
| **电机控制离线** | `motor_sdk` 未上传 | 上传 `motor_sdk/` 目录到 Mini                      |
| **松开按键电机继续转** | app.js 每 50ms 重复发送命令，keyup 丢失后失控 | 简化为按住发一次、松开发 stop，不重复                         |
| **gpio 报 "Unable to determine board revision"** | Ubuntu `wiringpi` 包（v2.50）只认树莓派 | 编译 `/usr/src/wiringOP/`（v2.56 Orange Pi 版）    |
| **电机 mock mode / gpio mode failed** | gpio 二进制不对 + sudoers 未配置 | 编译 Orange Pi 版 wiringOP + 配置 NOPASSWD sudoers |
| **热点开启后 FRP 断开** | create_ap 独占 wlan0，机器人断网 | 正常行为——热点模式下通过直连 10.42.0.1:6080 访问              |
| **ssh_upload --recursive 全部 [Errno 2]** | 路径含中文（如 `机器人系统`）导致文件读取失败 | 用 tar.gz 打包成单个文件再上传                         |
| **SFTP 上传 Permission denied** | `sudo mkdir` 创建的目录属主为 root，orangepi 无写权限 | `sudo chown -R orangepi:orangepi /home/orangepi/pr_mini` |
| **ssh_execute sudo 管道被切后台失败** | 长超时触发后台模式，后台 sudo 管道执行失败 | 加 `--no-daemon` 且用 `bash -c '...'` 包裹命令       |
| **wiringpi 被 hold 导致 apt install 整体失败** | Orange Pi 镜像 hold 了 wiringpi 防止被 Ubuntu 包覆盖 | 分开安装：`apt install -y ffmpeg python3-pip`（不含 wiringpi） |
| **apt 被自动更新进程锁住** | 系统首次启动后 unattended-upgrades 自动运行 | `kill -9` 清理进程 + `rm -f /var/lib/apt/lists/lock` 后重试 |
