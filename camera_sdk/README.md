# camera_sdk — Vendor-Specific Camera Integrations

Each sub-package provides native SDK integration for a specific camera vendor,
bypassing generic V4L2/UVC where the vendor's proprietary protocol is required
for full functionality (RGB colour, depth, IR stereo).

## Directory Structure

```
camera_sdk/
├── realsense/        Intel RealSense (D455, D435) — pyrealsense2 RGB pipeline
│   ├── pipeline.py   RealSensePipeline: SDK capture + in-process JPEG encode
│   └── detector.py   Device discovery via USB bus-port matching
├── orbbec/           Orbbec Femto Bolt — K4A-compatible depth/stereo pipeline
│   ├── pipeline.py   OrbbecPipeline: depth map + IR stereo capture
│   └── detector.py   Device discovery via USB VID/PID matching
├── srs/              SRS WebRTC streaming stack (Docker)
│   └── ...
├── __init__.py
└── README.md
```

## Dependencies

| Package | Dependency | Install |
|---------|-----------|---------|
| realsense | pyrealsense2, Pillow, numpy | `pip install pyrealsense2 Pillow numpy` |
| orbbec | OrbbecSDK-K4A-Wrapper, Pillow, numpy | See [Orbbec Setup](#orbbec-setup) below |

## RealSense Setup (Jetson)

```bash
pip install pyrealsense2 Pillow numpy
```

The `realsense` package uses the pyrealsense2 SDK to access the RGB colour sensor
on Intel RealSense cameras (D455, D435). Standard V4L2/UVC only exposes the Stereo
Module IR streams — these are grayscale, not RGB.

## Orbbec Setup

The Orbbec Femto Bolt's depth and IR sensors are not exposed via standard UVC.
The [OrbbecSDK-K4A-Wrapper](https://github.com/orbbec/OrbbecSDK-K4A-Wrapper)
provides an Azure Kinect (K4A) compatible API for accessing depth, IR, and IMU.

### Build on Jetson (ARM64)

```bash
cd /home/yu
git clone https://github.com/orbbec/OrbbecSDK-K4A-Wrapper
cd OrbbecSDK-K4A-Wrapper
# Follow Jetson build instructions from repo README
# Build produces libk4a.so and Python bindings (k4a module)
```

Verify: `python3 -c "import k4a; print('OK')"`

## Architecture Notes

Each pipeline class follows the same interface:
- `start()` — open device, configure streams, spawn capture thread
- `stop()` — stop capture, close device
- `get_frame(timeout) -> Optional[bytes]` — return a single JPEG frame
- `get_device_name() -> str` — human-readable device name
- `is_running() -> bool` — whether the capture loop is active

JPEG encoding is done in-process via Pillow (no OpenCV dependency).
Frames are consumed by `robot_control/camera.py` source subclasses and streamed
over HTTP as multipart MJPEG via the existing `/stream/<device>` endpoint.
