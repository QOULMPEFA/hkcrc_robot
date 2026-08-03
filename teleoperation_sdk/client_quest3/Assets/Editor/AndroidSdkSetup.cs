using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public class AndroidSdkSetup
{
    static AndroidSdkSetup()
    {
        string sdkPath = @"C:\Android\SDK";
        string ndkPath = @"C:\Android\NDK";
        string jdkPath = @"C:\Android\OpenJDK";

        bool changed = false;

        if (EditorPrefs.GetString("AndroidSdkRoot") != sdkPath && System.IO.Directory.Exists(sdkPath))
        {
            EditorPrefs.SetString("AndroidSdkRoot", sdkPath);
            Debug.Log("[AndroidSdkSetup] AndroidSdkRoot -> " + sdkPath);
            changed = true;
        }

        if (EditorPrefs.GetString("AndroidNdkRoot") != ndkPath && System.IO.Directory.Exists(ndkPath))
        {
            EditorPrefs.SetString("AndroidNdkRoot", ndkPath);
            Debug.Log("[AndroidSdkSetup] AndroidNdkRoot -> " + ndkPath);
            changed = true;
        }

        if (EditorPrefs.GetString("JdkPath") != jdkPath && System.IO.Directory.Exists(jdkPath))
        {
            EditorPrefs.SetString("JdkPath", jdkPath);
            Debug.Log("[AndroidSdkSetup] JdkPath -> " + jdkPath);
            changed = true;
        }

        if (changed)
        {
            Debug.Log("[AndroidSdkSetup] Android SDK paths configured. Please restart Unity for changes to take full effect.");
        }
    }
}
