using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

/// <summary>
/// One-shot scene fixup: the close button lives on a world-space canvas behind
/// CanvCE's opaque fullscreen video plane, so it can never be seen or clicked.
/// The original can't be reparented (Unity forbids moving a prefab instance's
/// child out), so an unlinked clone is placed on CanvCE instead.
/// </summary>
public static class FixCameraWindowUI
{
    [MenuItem("Tools/Fix Camera Window UI")]
    public static void Fix()
    {
        var window = Object.FindFirstObjectByType<RemoteCameraWindow>(FindObjectsInactive.Include);
        var setLere = Object.FindFirstObjectByType<SetLERE>(FindObjectsInactive.Include);
        if (window == null || setLere == null)
        {
            Debug.LogError("[Fix] RemoteCameraWindow or SetLERE not found in scene.");
            return;
        }

        var so = new SerializedObject(window);
        var prop = so.FindProperty("setLere");
        if (prop == null)
        {
            Debug.LogError("[Fix] 'setLere' field missing — scripts not recompiled?");
            return;
        }
        prop.objectReferenceValue = setLere;
        so.ApplyModifiedPropertiesWithoutUndo();
        Debug.Log($"[Fix] Wired setLere -> {setLere.name}");

        var canvCE = setLere.CanvCE;
        if (canvCE == null)
        {
            Debug.LogError("[Fix] SetLERE.CanvCE is null.");
            return;
        }

        RectTransform original = null;
        foreach (var rt in window.GetComponentsInChildren<RectTransform>(true))
        {
            if (rt.name == "closeBtn") { original = rt; break; }
        }
        if (original == null)
        {
            Debug.LogError("[Fix] closeBtn not found under RemoteCameraWindow.");
            return;
        }

        // Undo the earlier in-place rect edits — the original stays where it was
        // authored, since only the clone is meant to be visible on Quest.
        original.anchorMin = original.anchorMax = original.pivot = new Vector2(0.5f, 0.5f);
        original.anchoredPosition = new Vector2(272.64f, 156.3f);
        original.sizeDelta = new Vector2(50.33f, 30f);
        original.localRotation = Quaternion.identity;
        original.localScale = Vector3.one;
        Debug.Log("[Fix] Reverted original closeBtn rect to authored values.");

        // Drop any clone from a previous run so this is re-runnable.
        var existing = canvCE.transform.Find("closeBtnCE");
        if (existing != null) Object.DestroyImmediate(existing.gameObject);

        var clone = (GameObject)Object.Instantiate(original.gameObject, canvCE.transform, false);
        clone.name = "closeBtnCE";
        clone.SetActive(true);

        var r = (RectTransform)clone.transform;
        r.anchorMin = r.anchorMax = r.pivot = new Vector2(1f, 1f);
        r.sizeDelta = new Vector2(96f, 64f);
        // Inset from the true corner: the Quest 3's rendered area extends past
        // what the user can comfortably see, so a corner-flush button is lost.
        r.anchoredPosition = new Vector2(-140f, -110f);
        r.localScale = Vector3.one;
        r.localRotation = Quaternion.identity;
        r.localPosition = new Vector3(r.localPosition.x, r.localPosition.y, 0f);
        r.SetAsLastSibling();

        var btn = clone.GetComponent<Button>();
        if (btn == null)
        {
            Debug.LogError("[Fix] Clone has no Button component.");
            return;
        }
        // The cloned persistent listener may point at a stale target; rebind it.
        for (int i = btn.onClick.GetPersistentEventCount() - 1; i >= 0; i--)
        {
            UnityEditor.Events.UnityEventTools.RemovePersistentListener(btn.onClick, i);
        }
        UnityEditor.Events.UnityEventTools.AddPersistentListener(
            btn.onClick, new UnityEngine.Events.UnityAction(window.OnCloseBtn));

        var img = clone.GetComponent<Image>();
        if (img != null) img.raycastTarget = true;

        // Without a raycaster on CanvCE the button renders but never receives input.
        if (canvCE.GetComponent<GraphicRaycaster>() == null)
        {
            canvCE.AddComponent<GraphicRaycaster>();
            Debug.Log("[Fix] Added GraphicRaycaster to CanvCE.");
        }

        Debug.Log($"[Fix] Created closeBtnCE under {canvCE.name} at {r.anchoredPosition}, size {r.sizeDelta}");

        EditorSceneManager.MarkSceneDirty(window.gameObject.scene);
        EditorSceneManager.SaveScene(window.gameObject.scene);
        Debug.Log("[Fix] Scene saved.");
    }
}
