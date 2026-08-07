using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR;

public class SetLERE : MonoBehaviour
{

    public GameObject CanvCE;
    public RemoteCameraWindow remoteCameraWindow;
    public Material matCE;

    //private float visibleRatio = 0.75f;
    //private float contentRatio = 0.88f;
    private float visibleRatio = 0.75f;
    private float contentRatio = 1.0f;
    private float heightCompressionFactor = 1.0f;
    private float stereoMode = 1.0f;

    public void UpdateParameters(float visible, float content, float heightCompression, float stereo)
    {
        // Adjust the ratios based on the height compression factor
        visibleRatio = visible;
        contentRatio = content;
        heightCompressionFactor = heightCompression;
        stereoMode = stereo;

        // Log the updated values
        Debug.Log(
            $"Updated Ratios - visible: {visibleRatio}, content: {contentRatio}, heightCompression: {heightCompressionFactor}, stereoMode: {stereoMode}");
    }

    public void ResetCanvases()
    {
        CanvCE.SetActive(false);
    }

    void Update()
    {
        if (!CanvCE.activeSelf)
        {
            CanvCE.SetActive(true);
        }
        var texture = remoteCameraWindow.Texture;
        if (texture != null)
        {
            matCE.SetTexture("_mainRT", texture);
            matCE.SetFloat("_visibleRatio", visibleRatio);
            matCE.SetFloat("_contentRatio", contentRatio);
            matCE.SetFloat("_heightCompressionFactor", heightCompressionFactor);
            matCE.SetFloat("_stereoMode", stereoMode);
            if (stereoMode < 0.5f)
            {
                matCE.SetVector("_monoScale", ComputeMonoFit(texture));
            }
        }
    }

    /// <summary>
    /// Aspect-fit scale mapping the fullscreen canvas UV onto the source frame.
    /// Values >1 on an axis shrink the sampled region, producing letterbox bars
    /// on that axis rather than stretching the image.
    /// </summary>
    private Vector4 ComputeMonoFit(Texture texture)
    {
        if (texture.height <= 0) return new Vector4(1f, 1f, 0f, 0f);

        // The canvas is ScreenSpace-Camera, so its RectTransform is driven at
        // runtime — pixelRect is the reliable source for on-screen dimensions.
        var pixelRect = CanvCE.GetComponent<Canvas>().pixelRect;
        if (pixelRect.width <= 0f || pixelRect.height <= 0f) return new Vector4(1f, 1f, 0f, 0f);

        float canvasAspect = pixelRect.width / pixelRect.height;
        float sourceAspect = (float)texture.width / texture.height;

        return sourceAspect > canvasAspect
            ? new Vector4(1f, sourceAspect / canvasAspect, 0f, 0f)  // wider — bars top/bottom
            : new Vector4(canvasAspect / sourceAspect, 1f, 0f, 0f); // taller — bars left/right
    }
}
