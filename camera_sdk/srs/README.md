# SRS WebRTC Streaming — frp-fit.com

## FRP 端口映射

| 协议 | 公网 (frp-fit.com) | 本地 | 用途 |
|------|--------------------|------|------|
| UDP  | `35500` | `8000` | WebRTC 媒体传输 |
| TCP  | `35900` | `1985` | HTTP API / 信令 |
| TCP  | `35700` | `8080` | 控制台 / 播放页面 |

## 启动命令

```bash
cd /home/yu/robot_code/camera_sdk/srs
docker compose up -d
```

### 验证服务

```bash
docker ps --filter name=srs-webrtc
ss -tlnp | grep -E '1985|8080'
ss -ulnp | grep 8000
curl -s http://127.0.0.1:1985/api/v1/ | python3 -m json.tool
```

## 推流（Jetson 本地）

```bash
ffmpeg -f v4l2 -i /dev/video0 -c:v libx264 -preset ultrafast \
  -tune zerolatency -b:v 1M -g 30 -f flv rtmp://127.0.0.1/live/cam
```

SRS 会自动将 RTMP 流转为 WebRTC。

## 手机拉流

### 推荐：自定义播放器

```
http://frp-fit.com:35700/player.html
```

自定义播放器已部署在 SRS HTTP Server 上，使用 `:35900` 作为 API 端口进行 WHEP 信令协商。

### SRS 内置播放器（备选）

内置播放器硬编码 API 端口为 1985，外网不可用（FRP 映射的是 35900）。仅局域网可用：

```
http://<jetson-ip>:8080/players/rtc_player.html?server=<jetson-ip>&app=live&stream=cam&autostart=true
```

## 改造方案

当前 Jetson 摄像头链路：
```
/dev/video0 → camera_server.py (port 6001, MJPEG) → Web GUI → 浏览器 MJPEG
```

改造后：
```
/dev/video0 → FFmpeg → SRS (RTMP) → WebRTC → 手机/浏览器
```

## 注意事项

- WebRTC 拉流不需要 HTTPS，HTTP 即可
- SRS 时区 UTC
- `candidate frp-fit.com:35500` 已配置在 srs.conf 中
