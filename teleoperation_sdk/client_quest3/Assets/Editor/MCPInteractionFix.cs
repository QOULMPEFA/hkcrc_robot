using UnityEngine;
using UnityEditor;
using UnityEditor.SceneManagement;
using System.Reflection;
using Oculus.Interaction.Input;

public static class MCPInteractionFix
{
    [MenuItem("Tools/Check Hand Skeleton Version")]
    public static void CheckHandSkeleton()
    {
        var settings = OVRRuntimeSettings.GetRuntimeSettings();
        Debug.Log("MCPHS HandSkeletonVersion=" + settings.HandSkeletonVersion);
    }

    [MenuItem("Tools/Set Hand Skeleton OpenXR")]
    public static void SetHandSkeletonOpenXR()
    {
        var settings = OVRRuntimeSettings.GetRuntimeSettings();
        var old = settings.HandSkeletonVersion;
        settings.HandSkeletonVersion = OVRHandSkeletonVersion.OpenXR;
        EditorUtility.SetDirty(settings);
        AssetDatabase.SaveAssets();
        Debug.Log("MCPHS HandSkeletonVersion changed: " + old + " -> " + settings.HandSkeletonVersion);
    }

    [MenuItem("Tools/Diagnose Interaction")]
    public static void Diagnose()
    {
        var rigRef = Object.FindFirstObjectByType<OVRCameraRigRef>();
        Debug.Log("MCPDIAG rigRef=" + (rigRef != null));
        if (rigRef == null) return;

        // Hands
        var handSources = Object.FindObjectsByType<FromOVRHandDataSource>(FindObjectsSortMode.None);
        foreach (var ds in handSources)
        {
            var hf = typeof(FromOVRHandDataSource).GetField("_handedness", BindingFlags.NonPublic | BindingFlags.Instance);
            var of = typeof(FromOVRHandDataSource).GetField("_ovrHand", BindingFlags.NonPublic | BindingFlags.Instance);
            int h = (int)hf.GetValue(ds);
            Debug.Log("MCPDIAG handDS handedness=" + h + " _ovrHand=" + (of.GetValue(ds) != null ? "SET" : "NULL"));
        }

        // Controllers
        var ctrlSources = Object.FindObjectsByType<Oculus.Interaction.Input.FromOVRControllerDataSource>(FindObjectsSortMode.None);
        Debug.Log("MCPDIAG FromOVRControllerDataSource count=" + ctrlSources.Length);
        foreach (var ds in ctrlSources)
        {
            Debug.Log("MCPDIAG ctrlDS name=" + ds.name + " go=" + ds.gameObject.name);
        }
    }

    [MenuItem("Tools/Fix Interaction Hand Refs")]
    public static void Fix()
    {
        var rigRef = Object.FindFirstObjectByType<OVRCameraRigRef>();
        var dataSources = Object.FindObjectsByType<FromOVRHandDataSource>(FindObjectsSortMode.None);
        var handednessField = typeof(FromOVRHandDataSource).GetField("_handedness", BindingFlags.NonPublic | BindingFlags.Instance);
        var ovrHandField = typeof(FromOVRHandDataSource).GetField("_ovrHand", BindingFlags.NonPublic | BindingFlags.Instance);

        Debug.Log("MCPFIX rigRef=" + (rigRef != null) + " dataSources=" + dataSources.Length);
        if (rigRef == null) return;

        int fixedCount = 0;
        foreach (var ds in dataSources)
        {
            int h = (int)handednessField.GetValue(ds);
            OVRHand target = (h == 0) ? rigRef.LeftHand : rigRef.RightHand;
            Debug.Log("MCPFIX handedness=" + h + " target=" + (target != null ? target.name : "NULL"));
            if (target != null)
            {
                ovrHandField.SetValue(ds, target);
                EditorUtility.SetDirty(ds);
                fixedCount++;
            }
        }
        AssetDatabase.SaveAssets();
        EditorSceneManager.SaveOpenScenes();
        Debug.Log("MCPFIX fixed=" + fixedCount + " LeftValid=" + (rigRef.LeftHand != null) + " RightValid=" + (rigRef.RightHand != null));
    }
}
