using System.IO;
using System.IO.Compression;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;
using System.Xml;

public class MetaAarNamespacePatcher : IPreprocessBuildWithReport
{
    public int callbackOrder => 0;

    public void OnPreprocessBuild(BuildReport report)
    {
        if (report.summary.platform != BuildTarget.Android) return;
        PatchMetaAars();
    }

    [MenuItem("Build/Patch Meta AAR Namespaces")]
    public static void PatchMetaAars()
    {
        string packageCache = Path.Combine(Application.dataPath, "..", "Library", "PackageCache");

        string[] aarMappings =
        {
            // path_glob_fragment -> new_namespace
            "com.meta.xr.sdk.core@*/Plugins/AndroidOpenXR/OVRPlugin.aar|com.oculus.Integration.core",
            "com.meta.xr.sdk.interaction@*/Runtime/Plugins/Android/InteractionSdk.aar|com.oculus.Integration.interaction",
            "com.unity.xr.oculus@*/Runtime/Android/OVRPlugin.aar|com.oculus.Integration.oculus",
        };

        foreach (string mapping in aarMappings)
        {
            string[] parts = mapping.Split('|');
            string globPattern = parts[0];
            string newNamespace = parts[1];

            // Resolve glob pattern to actual path
            string globDir = Path.GetDirectoryName(globPattern);
            string globFile = Path.GetFileName(globPattern);

            if (globDir == null) continue;

            string baseDir = Path.Combine(packageCache, globDir);
            if (!Directory.Exists(baseDir))
            {
                // Try finding the directory with wildcard
                string parentDir = Path.GetDirectoryName(baseDir);
                string dirPattern = Path.GetFileName(baseDir);
                if (parentDir != null && Directory.Exists(parentDir))
                {
                    string[] dirs = Directory.GetDirectories(parentDir, dirPattern);
                    if (dirs.Length > 0)
                        baseDir = dirs[0];
                    else
                        continue;
                }
                else
                    continue;
            }

            string aarPath = Path.Combine(baseDir, globFile);
            if (!File.Exists(aarPath)) continue;

            PatchAar(aarPath, newNamespace);
        }
    }

    private static void PatchAar(string aarPath, string newNamespace)
    {
        string tempDir = Path.Combine(Path.GetTempPath(), "meta_aar_patch_" + Path.GetFileNameWithoutExtension(aarPath));
        string backupPath = aarPath + ".orig";

        try
        {
            // Only patch if we haven't already (check backup)
            if (File.Exists(backupPath))
            {
                Debug.Log($"[MetaAarPatcher] {Path.GetFileName(aarPath)} already patched, skipping.");
                return;
            }

            // Create backup
            File.Copy(aarPath, backupPath, true);

            // Clean temp
            if (Directory.Exists(tempDir))
                Directory.Delete(tempDir, true);
            Directory.CreateDirectory(tempDir);

            // Extract AAR (it's a ZIP)
            ZipFile.ExtractToDirectory(aarPath, tempDir);

            // Find and patch AndroidManifest.xml
            string manifestPath = Path.Combine(tempDir, "AndroidManifest.xml");
            if (!File.Exists(manifestPath))
            {
                Debug.LogWarning($"[MetaAarPatcher] No AndroidManifest.xml in {Path.GetFileName(aarPath)}, skipping.");
                return;
            }

            string manifest = File.ReadAllText(manifestPath);
            string oldNamespace = ExtractPackage(manifest);

            if (string.IsNullOrEmpty(oldNamespace))
            {
                Debug.LogWarning($"[MetaAarPatcher] No package attribute in {Path.GetFileName(aarPath)}, skipping.");
                return;
            }

            if (oldNamespace == newNamespace)
            {
                Debug.Log($"[MetaAarPatcher] {Path.GetFileName(aarPath)} namespace already '{newNamespace}', skipping.");
                return;
            }

            manifest = manifest.Replace($"package=\"{oldNamespace}\"", $"package=\"{newNamespace}\"");
            File.WriteAllText(manifestPath, manifest);

            // Repack AAR
            File.Delete(aarPath);
            ZipFile.CreateFromDirectory(tempDir, aarPath);

            Debug.Log($"[MetaAarPatcher] Patched {Path.GetFileName(aarPath)}: '{oldNamespace}' -> '{newNamespace}'");
        }
        catch (System.Exception ex)
        {
            Debug.LogError($"[MetaAarPatcher] Failed to patch {Path.GetFileName(aarPath)}: {ex.Message}");

            // Restore backup on failure
            if (File.Exists(backupPath))
            {
                if (File.Exists(aarPath))
                    File.Delete(aarPath);
                File.Move(backupPath, aarPath);
            }
        }
        finally
        {
            if (Directory.Exists(tempDir))
                Directory.Delete(tempDir, true);
        }
    }

    private static string ExtractPackage(string manifestXml)
    {
        // Simple regex-free extraction: find package="..." in the <manifest> tag
        int pkgIdx = manifestXml.IndexOf("package=\"");
        if (pkgIdx < 0) return null;

        pkgIdx += "package=\"".Length;
        int endIdx = manifestXml.IndexOf("\"", pkgIdx);
        if (endIdx < 0) return null;

        return manifestXml.Substring(pkgIdx, endIdx - pkgIdx);
    }
}
