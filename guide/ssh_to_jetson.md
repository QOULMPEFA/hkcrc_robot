## SSH & Connection

- **局域网**: `192.168.20.63` | User: `yu` | Pass: `1114`（优先）
- **外网**: `frp-all.com:35200` | User: `yu` | Pass: `1114`
- Workspace: `/home/yu/robot_code`

### 免密登录（一次性配置）

把本机公钥加到 Jetson，后续 ssh 不再需要密码：

```bash
# 在本机 (Windows/Linux) 执行：
cat ~/.ssh/id_rsa.pub | ssh yu@192.168.20.63 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

本机已有公钥（`C:\Users\qoulm\.ssh\id_rsa.pub`）：
```
ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDpYjOoL9h2GEHqLxQhqL8j9iSeqGV4y9mK1wZ4ZjnWSTmuaTEOmXH9Qo7OA8S8PQ8Jb58rjSGQaD5WppPvEF24jAIFOHwjrlD9iYGpy6/sOamcP0zLmfxbmgFKxyYkbNqv147o5HcteXy5KRz32KRLo7CeIsoMj9Ufm4N2hhWEEZydbEiYWvK5b0Pk95c4H1YsJemn4xBuPYlG3uaiLKhs19KziCzau0YOYehb3FKxy9Ls/VEUf0Rp7Ds+hle92h2bnDQC50T5lRz70uHxtD9uDVqC7gA08BZl+kc1PrV5ZgPS5gEi40c/DODrmZZhIUGZOTRWWNh0mDtwoXjIB66fThUtYlidr4TREGR6Nm2cWfbN2fXEz71VJX69UWkGslTBA4gvgM312r7EJmJ8WF8E9YXbrzpm4x2iGcKS/iPO9/rYND2nEs1In7vyXtarcZkEXFASTuwLkIYpS4SOI0kULf5IxjZjrsoqZfHE8BGRV6mCH1wIxo6PC3zljaes4c1PCocKY2gMqAD1IFs2AkuQ7RkBpb+V0FjST0lYSNCRxqgH97EVNmDKPuwY5LYZKhEizGtPH1O363aPMnGPo21RefnsYrWG/YgIUtyn4ds4M1wKyWEfjgn0a8Aq/kgze/0xBdrB77+5yT85T0i5orsa5SUMkPMsBNrZXbHAtNcOUQ== jialy@hkcrc.hk
```

### SSH Config（本机 `~/.ssh/config`）

```ssh-config
# ===== jetson-local =====
Host jetson-local
    HostName 192.168.20.63
    User yu
    Port 22
    IdentityFile ~/.ssh/id_rsa

# ===== jetson-frp =====
Host jetson-frp
    HostName frp-all.com
    User yu
    Port 35200
    IdentityFile ~/.ssh/id_rsa
```

免密后直接 `ssh jetson-local` 即可登录。

## FRP Tunnels

| Tunnel Name | Type | External | Local Target | Purpose |
|-------------|------|----------|--------------|---------|
| HKCRC_Jetson | TCP | frp-all.com:35200 | 127.0.0.1:22 | SSH |
| HKCRC_JETSON_TCP | TCP | frp-fit.com:35000 | 127.0.0.1:6080 | Web GUI |

## Web GUI

```bash
# 启动
cd /home/yu/robot_code
nohup python robot_control/web_gui/server.py > /tmp/web_gui.log 2>&1 &

# 停止
pkill -f "web_gui/server.py"

# 查看日志
tail -f /tmp/web_gui.log
```

**访问**：
- 局域网: `http://192.168.20.63:6080`
- 外网: `http://frp-fit.com:35000`

当前激活的硬件模块（`server.py` 中配置）：
- `camera` — 摄像头
- `motor` — 电机驱动
- `hand` — 灵巧手（**RS485/Modbus**，自动检测端口）

## 灵巧手连接

- 协议: RS485 / Modbus RTU
- 波特率: 460800
- 设备 ID: 0x7F（右手），0x7E（左手）
- 型号: Revo2Basic, SN: BCXER2079J2600007, FW: 1.0.20.U
- 连接: USB-RS485 插入 Jetson → 自动检测 `/dev/ttyUSB*`
- CAN FD 代码保留（`hand.py` 内 `_connect_can` / `_connect_candlelight`），固件不支持时默认走 Modbus

## Notes

- ⚠️ **NEVER stop `natfrp-service`** Docker 容器（维持 SSH FRP 穿透）
- 其他容器可以安全启停
- `camera_server.service`（systemd）提供 MJPEG 流在 6001 端口
