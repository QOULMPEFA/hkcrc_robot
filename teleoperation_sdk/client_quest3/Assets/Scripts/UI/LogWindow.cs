using System.Collections;
using System.Collections.Concurrent;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public class LogWindow : MonoBehaviour
{
    public TextMeshProUGUI text;

    public ScrollRect scrollRect;

    private static LogWindow _instance;

    public RectTransform rectTransform;

    // Info/Warn/Error are called from background threads (socket callbacks,
    // send threads, decoders). MonoBehaviour APIs like StartCoroutine may
    // only run on the main thread, so messages are queued here and drained
    // in Update().
    private static readonly ConcurrentQueue<string> _pendingMessages = new ConcurrentQueue<string>();

    private void Awake()
    {
        _instance = this;
    }

    private void Update()
    {
        while (_pendingMessages.TryDequeue(out var message))
        {
            AppendText(message);
        }
    }

    private IEnumerator AutoScrollCoroutine()
    {
        LayoutRebuilder.ForceRebuildLayoutImmediate(scrollRect.content as RectTransform);
        yield return new WaitForEndOfFrame(); // Wait one frame for layout to update

        // Update rectTransform height based on text content
        UpdateRectTransformHeight();

        scrollRect.verticalNormalizedPosition = 0f;
    }

    private void UpdateRectTransformHeight()
    {
        if (rectTransform != null && text != null)
        {
            // Force the text to update its preferred height
            text.ForceMeshUpdate();

            // Get the preferred height of the text
            float preferredHeight = text.preferredHeight;

            // Update the rectTransform height
            Vector2 sizeDelta = rectTransform.sizeDelta;
            sizeDelta.y = preferredHeight + 10f; // Add some padding
            rectTransform.sizeDelta = sizeDelta;
        }
    }

    public void AppendText(string message)
    {
        // add time prefix of local timezone to the message
        string timePrefix = $"[{System.DateTime.Now:HH:mm:ss}] ";
        text.text += $"{timePrefix}{message}\n";

        StartCoroutine(AutoScrollCoroutine());
    }

    private static void Message(string message)
    {
        // Callers may be on background threads (socket callbacks, send
        // threads, decoders), so never touch Unity APIs here — just queue
        // the text and let Update() apply it on the main thread.
        _pendingMessages.Enqueue(message);
    }

    public static void Info(string info)
    {
        // white color text
        Message($"<color=white>{info}</color>");
    }

    public static void Warn(string info)
    {
        // yellow color text
        Message($"<color=yellow>{info}</color>");
    }

    public static void Error(string info)
    {
        // red color text
        Message($"<color=red>{info}</color>");
    }
}