# Bug Handoff — Teleop Home/Re-enable + Rotation Reference (2026-08-10)

## 状态

未解决，需要后续 AI Agent 继续处理。

## 用户反馈

1. 按“回零”后无法遥操作，需要重新启用遥操作。
   - 期望：按回零后控制面板显示“未启用”，之后重新启用即可。
   - 实际：仍存在按回零后无法恢复遥操作控制的问题。
2. 身体旋转 180° 后标定，旋转方向反向。
   - 已尝试“头部参考”修复，但未转身时正常逻辑又被破坏。
   - 当前已默认回退到“手柄参考”，并提供手动切换按钮。
   - 用户表示“这个 bug 依旧没有解决”。

## 当前代码状态

- `robot_control/arm.py`
  - `_on_home()` 会先调用 `teleop._on_disable()`，再执行 `go_home()`。
- `teleoperation_sdk/teleop.py`
  - `_on_enable()` 创建 RTDE 控制器前先调用 `arm.stop_motion()`。
  - 增加 XR 断流检测：超过 2 秒无新数据会停止遥操作并回默认归零角度。
  - 旋转参考系默认 `controller`，可手动切换 `head`。
- `teleoperation_sdk/xr_ur5e.py`
  - `URRTDEAdapter.start_control_script()` 检查 `moveJ()` 返回值。
  - `_process_xr_pose()` 在 `rotation_reference == "head"` 时使用头部局部旋转增量。
- 前端 `app.js?v=62`
  - 新增“旋转参考”按钮，可切换 `手柄参考 / 头部参考`。
  - 回零仍由机械臂面板按钮触发。

## 已知日志证据

远程日志：

```text
RTDEControlInterface: RTDE control script is not running!
```

出现大量该错误时，日志通常同时存在：

```text
[Arm] teleop move_joints(... home ...)
[Arm] stop()
```

说明 URScript 回零/手动 movej 可能与 RTDE 控制脚本冲突，导致重新启用遥操作失败或 `servoJ()` 不生效。

## 可能根因

1. `arm.stop_motion()` 使用 Dashboard `stop` + `stopj(2.0)`，但可能没有彻底结束 URScript 程序。
2. RTDE `moveJ()` 启动控制脚本时，如果 UR 仍在执行 URScript 的 `movej`，可能返回失败或启动后立即失效。
3. `URRTDEAdapter.stop()` 中 `servoStop()` / `stopScript()` 与后续 URScript 回零的先后顺序可能需要调整。
4. 旋转参考系数学：
   - 默认 `controller` 不依赖头部姿态，方向正常。
   - 手动 `head` 模式用于 180° 补偿，但当前数学可能与 Quest/PC Service 实际坐标约定不完全一致。

## 下一步排查

1. 实机复现：
   - 启用遥操作 -> 移动机械臂 -> 按回零 -> 等待完成 -> 再次“标定参考值/启用遥操作”。
   - 抓 `/var/log/robot_control_panel.log` 中：
     - `Teleop Enabled`
     - `Teleop controller connected`
     - `RTDE control script is not running`
     - `Enable failed`
     - `XR data stale`
2. 单独测试 RTDE 状态：
   - 执行一次 URScript 回零后，直接调用 `RTDEControlInterface.moveJ(current_q)`，确认返回值。
   - 如果返回 `False`，需要在 `_on_enable()` 中更彻底地终止 URScript。
3. 旋转参考系：
   - 分别测试“手柄参考”和“头部参考”：
     - 未转身：平移/旋转是否正常。
     - 转身 180°：平移/旋转是否正常。
   - 记录手柄原始 quaternion、头部 quaternion、`controller_pose_3d`、目标关节，判断反向发生在显示还是机械臂执行。
4. 若旋转参考系复杂，可考虑彻底回退到之前可用的方向矩阵版本，并只保留手动切换按钮。

## 相关文件

- `teleoperation_sdk/teleop.py`
- `teleoperation_sdk/xr_ur5e.py`
- `teleoperation_sdk/vr_backends.py`
- `teleoperation_sdk/xr_protocol.py`
- `robot_control/arm.py`
- `robot_control/web_gui/static/app.js`

## 远程命令

```bash
grep -a -E "Teleop|XR-UR5e|RTDE|Enable|Calibrat|stale|Enable failed|RTDE control script" /var/log/robot_control_panel.log | tail -200
```

```bash
ss -tnp | grep -E ":63901|:60061"
```

```bash
systemctl is-active robot_control_panel.service robotics-service.service
```
