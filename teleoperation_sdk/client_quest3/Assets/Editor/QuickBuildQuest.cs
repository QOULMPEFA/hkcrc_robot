using UnityEditor;
using UnityEngine;

/// <summary>
/// Single-APK build for on-device verification. The existing Build/Andriod
/// menu produces four APKs (cn/i18n x debug/release), which is far slower than
/// a test loop needs.
/// </summary>
public static class QuickBuildQuest
{
    public const string OutputPath = "D:/12_Projects/20260608_RobotSystem/code/teleoperation_sdk/output_apk/quest_verify.apk";

    [MenuItem("Build/Quick Quest APK (single)")]
    public static void Build()
    {
        var scenes = new System.Collections.Generic.List<string>();
        foreach (var s in EditorBuildSettings.scenes)
        {
            if (s != null && s.enabled) scenes.Add(s.path);
        }

        if (scenes.Count == 0)
        {
            Debug.LogError("[QuickBuild] No enabled scenes in Build Settings.");
            return;
        }

        Debug.Log($"[QuickBuild] Building {scenes.Count} scene(s) -> {OutputPath}");

        var report = BuildPipeline.BuildPlayer(
            scenes.ToArray(), OutputPath, BuildTarget.Android, BuildOptions.Development);

        Debug.Log($"[QuickBuild] Result: {report.summary.result}, " +
                  $"errors={report.summary.totalErrors}, size={report.summary.totalSize / 1048576}MB");
    }
}
