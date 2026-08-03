using UnityEditor;
using UnityEngine;

public class PrefabMissingScriptCleaner : EditorWindow
{
    [MenuItem("Build/Clean Missing Scripts from Prefabs")]
    public static void CleanMissingScripts()
    {
        string[] prefabPaths =
        {
            "Assets/Resources/Main.prefab",
            "Assets/Resources/RemoteCamera.prefab",
        };

        foreach (string path in prefabPaths)
        {
            GameObject prefab = AssetDatabase.LoadAssetAtPath<GameObject>(path);
            if (prefab == null)
            {
                Debug.LogWarning($"[Cleaner] Prefab not found: {path}");
                continue;
            }

            // Instantiate to modify
            GameObject instance = PrefabUtility.InstantiatePrefab(prefab) as GameObject;
            if (instance == null) continue;

            int removed = 0;
            // Check all children recursively
            foreach (Transform t in instance.GetComponentsInChildren<Transform>(true))
            {
                // GameObjectUtility.RemoveMonoBehavioursWithMissingScript removes all missing scripts
                int before = GameObjectUtility.GetMonoBehavioursWithMissingScriptCount(t.gameObject);
                if (before > 0)
                {
                    GameObjectUtility.RemoveMonoBehavioursWithMissingScript(t.gameObject);
                    removed += before;
                    Debug.Log($"[Cleaner] Removed {before} missing script(s) from '{t.name}' in {path}");
                }
            }

            if (removed > 0)
            {
                PrefabUtility.SaveAsPrefabAsset(instance, path);
                Debug.Log($"[Cleaner] Saved {path} with {removed} missing script(s) removed.");
            }

            DestroyImmediate(instance);
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log("[Cleaner] Missing script cleanup complete.");
    }
}
