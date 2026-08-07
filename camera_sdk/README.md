# camera_sdk — Vendor-Specific Camera Integrations

Each sub-package provides native SDK integration for a specific camera vendor,
bypassing generic V4L2/UVC where the vendor's proprietary protocol is required
for full functionality (RGB colour, depth, IR stereo).

## Directory Structure

```
camera_sdk/
├── realsense/               Intel RealSense (D455, D435) — pyrealsense2 RGB pipeline
│   ├── pipeline.py          RealSensePipeline: SDK capture + in-process JPEG encode
│   └── detector.py          Device discovery via USB bus-port matching
├── orbbec/                  Orbbec Femto Bolt — K4A-compatible depth/stereo pipeline
│   ├── pipeline.py          OrbbecPipeline: depth map + IR stereo capture
│   └── detector.py          Device discovery via USB VID/PID (no K4A required)
├── srs/                     SRS WebRTC streaming stack (Docker)
│   ├── docker-compose.yml
│   ├── srs.conf
│   └── webrtc_player.html
├── realsense_quest_bridge.py   MJPEG→UDP bridge for Quest 3 VR headset
├── camera_server.py            MJPEG HTTP server (single camera)
├── camera_server_TCP.py        MJPEG TCP server variant
├── jetson_camera_server.py     Jetson-optimized single-camera server
├── srt_tunnel.py               SRT tunnel (low-latency video transport)
├── tunnel_jetson.py            Generic TCP tunnel helper
├── start_*.sh                  Convenience launch scripts
├── __init__.py
└── README.md
```

## Sub-Packages

### `realsense/` — Intel RealSense D455 / D435

Uses the pyrealsense2 SDK to access the RGB colour sensor, which is NOT
exposed via standard UVC/V4L2 on Jetson. The V4L2 nodes for RealSense
cameras expose only grayscale IR streams from the Stereo Module.

| File | Purpose |
|------|---------|
| `detector.py` | Cross-references `rs.context()` with V4L2 nodes by USB bus-port |
| `pipeline.py` | `RealSensePipeline` — threaded capture, BGRA→RGB, JPEG encode via Pillow |

### `orbbec/` — Orbbec Femto Bolt

The Femto Bolt exposes a standard UVC RGB sensor (usable via V4L2/ffmpeg)
AND depth/IR sensors accessible only through the Orbbec SDK (K4A wrapper).

**Detection** (`detector.py`) is USB VID/PID-based and works WITHOUT the K4A
wrapper installed. Detected devices are represented as `OrbbecSource` in
`robot_control/camera.py` with three-mode switching (colour / depth / stereo).

**Pipeline** (`pipeline.py`) requires the K4A wrapper for depth/stereo capture:
- Depth mode: 16-bit depth → Turbo colormap LUT (hand-rolled, no matplotlib) → JPEG
- Stereo mode: IR left + right → normalized 8-bit → side-by-side composite → JPEG

| File | Purpose |
|------|---------|
| `detector.py` | USB VID/PID matching (2bc5:066b for Femto Bolt), no SDK dependency |
| `pipeline.py` | `OrbbecPipeline` — K4A depth/IR capture + colour map + JPEG encode |

### `srs/` — SRS WebRTC Streaming

Docker-based WebRTC streaming stack. SRS ingests RTMP and serves WebRTC/WHEP
for ultra-low-latency video delivery to browsers and mobile devices.

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Production SRS container (frp-fit.com ports) |
| `docker-compose-local.yml` | Local-only variant |
| `srs.conf` | SRS configuration (RTMP, WebRTC, CORS, FRP candidate) |
| `srs_local.conf` | Local configuration variant |
| `webrtc_player.html` | Custom WHEP player (uses FRP-mapped API port) |
| `webrtc_player_local.html` | Local variant |
| `README.md` | Detailed setup and FRP port mapping |

## Utility Scripts

| Script | Description |
|--------|-------------|
| `realsense_quest_bridge.py` | Reads MJPEG from local `/stream` endpoint, forwards JPEG frames to Quest 3 over UDP (port 10004). TCP control on :13579 for Quest IP discovery. |
| `camera_server.py` | Standalone single-camera MJPEG HTTP server (V4L2 → ffmpeg → HTTP multipart). |
| `camera_server_TCP.py` | TCP variant of the MJPEG server. |
| `jetson_camera_server.py` | Jetson-optimized variant with hardware-accelerated encode. |
| `srt_tunnel.py` | SRT (Secure Reliable Transport) tunnel for low-latency video over unreliable networks. |
| `tunnel_jetson.py` | Generic TCP tunnel helper for port forwarding. |
| `start_realsense_quest.sh` | One-shot launcher for the Quest 3 bridge. |
| `start_srt.sh` | One-shot launcher for the SRT tunnel. |
| `start_stream.sh` | One-shot launcher for the MJPEG camera server. |

## Dependencies

| Package | Dependency | Install |
|---------|-----------|---------|
| realsense | pyrealsense2, Pillow, numpy | `pip install pyrealsense2 Pillow numpy` |
| orbbec (detect) | None — USB sysfs only | Always works on Linux |
| orbbec (pipeline) | OrbbecSDK-K4A-Wrapper, Pillow, numpy | See [Orbbec Setup](#orbbec-setup) |
| quest_bridge | None (stdlib only) | — |

## RealSense Setup (Jetson)

```bash
pip install pyrealsense2 Pillow numpy
```

Verify:

```bash
python3 -c "import pyrealsense2 as rs; print([d.get_info(rs.camera_info.name) for d in rs.context().devices])"
```

## Orbbec Setup

The Orbbec Femto Bolt's depth and IR sensors are not exposed via standard UVC.
The [OrbbecSDK-K4A-Wrapper](https://github.com/orbbec/OrbbecSDK-K4A-Wrapper)
provides an Azure Kinect (K4A) compatible API.

### Detection Only (no K4A — colour mode works)

No setup needed. The detector reads USB VID/PID from sysfs
(`/sys/class/video4linux/<dev>/device/../idVendor`) and does not import k4a.
Colour mode uses the base-class V4L2/ffmpeg path.

### Full Pipeline (depth + stereo modes)

```bash
cd /home/yu
git clone https://github.com/orbbec/OrbbecSDK-K4A-Wrapper
cd OrbbecSDK-K4A-Wrapper
# Follow Jetson build instructions from repo README (ARM64)
# Build produces libk4a.so and k4a Python module
```

Verify:

```bash
python3 -c "import k4a; print('OK')"
```

## Quest 3 Bridge Setup

The `realsense_quest_bridge.py` relays the robot server's MJPEG stream
(already sourced from RealSense SDK) to the Quest 3 VR headset over UDP:

```
Robot Server (/stream/cam0) → bridge reads MJPEG → UDP datagrams → Quest 3 :10004
```

```bash
# On Jetson, after robot server is running:
./start_realsense_quest.sh
```

The Quest 3 client listens on port 10004 for JPEG/UDP datagrams and uses
TCP port 13579 for IP auto-discovery.

## Architecture Notes

### Pipeline Interface

Each pipeline class follows the same interface:

| Method | Returns | Description |
|--------|---------|-------------|
| `start()` | — | Open device, configure streams, spawn capture thread |
| `stop()` | — | Stop capture, close device |
| `get_frame(timeout)` | `Optional[bytes]` | Single JPEG frame (thread-safe, queue-based) |
| `get_device_name()` | `str` | Human-readable device name |
| `is_running()` | `bool` | Capture loop active |

JPEG encoding is done in-process via Pillow (no OpenCV dependency).
Depth colourisation uses a hand-rolled 256-entry Turbo LUT (no matplotlib).

### Integration with robot_control

`robot_control/camera.py` provides source subclasses that wrap each pipeline:

- `RealSenseSource(CameraSource)` — `_type = "realsense"`, overrides `_start_ffmpeg`, `_frame_loop`, etc.
- `OrbbecSource(CameraSource)` — `_type = "usb"`, three-mode switching (color/depth/stereo)

Both emit MJPEG frames through the existing `/stream/<device>` HTTP endpoint,
so the web GUI sees them as standard multipart MJPEG streams regardless of the
underlying capture pipeline.

Frames are consumed by `robot_control/camera.py` source subclasses and streamed
over HTTP as multipart MJPEG via the existing `/stream/<device>` endpoint.

## Orbbec Detection Without K4A

The detector (`orbbec/detector.py`) reads USB VID/PID from sysfs and does NOT
import k4a. This means:

- Orbbec cameras are correctly identified and assigned to `OrbbecSource`
  even when the K4A wrapper is not installed.
- `OrbbecSource` falls back to V4L2/ffmpeg for colour mode.
- Depth and stereo modes require K4A at runtime (`_HAS_ORBBEC_K4A` guard).

Known Femto Bolt USB IDs: `2bc5:066b`, `2bc5:066c`, `2bc5:0800`, `2bc5:0801`, `2bc5:0900`.
