## SSH & Env
- Host: frp-fit.com:35200 | User: yu | Pass: 1114
- Workspace: `/home/yu/robot_code`
- AnyDesk: GUI available if needed.

## Ports
- TCP: frp-fit.com:35230 -> local:6080  (Web GUI — robot_control/web_gui/server.py)
- TCP: frp-fit.com:35240 -> local:5000  (未使用 / 备用)

## Services
- `camera_server.service` (systemd, Restart=always): `/home/yu/camera_server.py` 监听 **6001**，提供 MJPEG 摄像头流
- Web GUI 通过 `/stream` 路由代理转发 `127.0.0.1:6001/stream`，对外统一暴露 6000 端口
- Web GUI 手动启动：`nohup python /home/yu/robot_code/robot_control/web_gui/server.py > /tmp/web_gui.log 2>&1 &`

## Docker Rules
- ⚠️ **NEVER stop `natfrp-service`** (Crucial for SSH connection).
- Other containers can be stopped/updated safely if needed.