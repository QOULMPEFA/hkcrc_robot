# 备份指南 / Quest 客户端可用状态记录

> 本文档记录 Quest 3 遥操作客户端（`teleoperation_sdk/client_quest3`）当前**可用状态**与关键修复，
> 用于故障后恢复或重建。最近更新：2026-08-03。

## 当前可用版本

- 最终 APK：`teleoperation_sdk/output_apk/client_quest3_test.apk`
- 应用名：`client_quest3_test`
- 包名：`com.xrobotoolkit.client.quest`（版本 1.0.1）
- 启动入口：`com.unity3d.player.UnityPlayerGameActivity`（GameActivity）
- 已确认全部正常：手柄可移动/交互、虚拟键盘可弹出输入、无崩溃、无交互异常

## 关键构建配置（不要随意改动）

1. **Unity 入口**：`PlayerSettings.Android.applicationEntry = GameActivity`
   （Unity 6 + 新版 Meta XR SDK 强制 GameActivity，不能改回 UnityPlayerActivity）
2. **targetSdk**：`AndroidApiLevel34`（匹配设备；36 会导致设备无法解析）
3. **HandSkeletonVersion = OpenXR**（OVRRuntimeSettings）
   - 若设为 OVR，Interaction SDK 的 `FromOVRHandDataSource` 每帧抛
     `NullReferenceException`，导致手柄在应用内锁定。
   - 修复工具：`client_quest3/Assets/Editor/MCPInteractionFix.cs` 菜单
     `Tools/Set Hand Skeleton OpenXR`
4. **自定义 AndroidManifest.xml 必须声明 GameActivity（不是 UnityPlayerActivity）**
   - 当前 `Assets/Plugins/Android/AndroidManifest.xml`：声明 `UnityPlayerGameActivity`
     （exported=true + launcher intent）+ `oculus.software.overlay_keyboard` uses-feature（虚拟键盘必需）。
   - ⚠️ 若声明 `UnityPlayerActivity`，会与 GameActivity 冲突，设备报 `result code=-92` 无法解析。
   - 原（有问题的）manifest 备份在 `AndroidManifest.xml.bak`。

## 手柄交互问题的历史修复

| 问题 | 根因 | 修复 |
|------|------|------|
| 无法通过手柄交互界面 | `FromOVRHandDataSource._ovrHand` 为 null | 场景里设置 _ovrHand 引用 |
| 手柄位置锁定 | HandSkeletonVersion=OVR 与 OpenXR 不匹配 | 设为 OpenXR |
| 设备无法启动 (result -92) | 自定义 manifest 声明 UnityPlayerActivity，与 GameActivity 冲突 | manifest 声明 UnityPlayerGameActivity（exported=true + launcher） |

## 已确认功能（2026-08-03）

- [x] GameActivity 启动（UnityPlayerGameActivity）
- [x] 手柄移动/交互（ControllerRayInteractor + PointableCanvasModule）
- [x] 虚拟键盘（oculus.software.overlay_keyboard + 自定义 manifest 声明 GameActivity）
- [x] 无交互异常（HandSkeletonVersion=OpenXR 是关键）
- [x] 无崩溃

## 备份流程（常规）

1. 同步 PC 与 Jetson 间代码。
2. 参照 `guide/git_info.md` 执行备份（含版本 tag：`git tag -a vX.Y && git push --tags`）。
3. 在 `log/backuplog` 目录下新建 Markdown 日志，文件名：`YYYYMMDD_HHMMSS.md`。
