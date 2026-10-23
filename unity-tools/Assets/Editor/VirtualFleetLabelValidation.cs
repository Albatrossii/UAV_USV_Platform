#if UNITY_EDITOR
using System;
using System.Reflection;
using UnityEditor;
using UnityEngine;

namespace UavUsv.Editor.Tools
{
    public static class VirtualFleetLabelValidation
    {
        public static void Run()
        {
            Type overlay = typeof(VirtualFleetDeviceLabelOverlay);
            MethodInfo measure = overlay.GetMethod("MeasureLabel", BindingFlags.NonPublic | BindingFlags.Static);
            MethodInfo normalize = overlay.GetMethod("NormalizeStatus", BindingFlags.NonPublic | BindingFlags.Static);
            if (measure == null || normalize == null) throw new Exception("Label helpers missing");
            int cases = 0;
            foreach (float scale in new[] { .72f, 1f, 1.18f })
            foreach (bool dense in new[] { false, true })
            {
                float originalHeight = (dense ? 23f : 31f) * scale;
                var code = new GUIStyle {
                    font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"),
                    fontSize = Mathf.RoundToInt(Mathf.Clamp(originalHeight * .39f, 9f, 14f)),
                    fontStyle = FontStyle.Bold, alignment = TextAnchor.MiddleCenter,
                    padding = new RectOffset(), richText = false, wordWrap = false
                };
                var status = new GUIStyle(code) {
                    fontSize = Mathf.RoundToInt(Mathf.Clamp(originalHeight * .29f, 8f, 11f)),
                    fontStyle = FontStyle.Normal
                };
                foreach (string device in new[] { "UAV-002", "USV-003" })
                foreach (string state in new[] { "ACTIVE", "HOLDING", "RETURNING", "REJOINING", "STOPPED", "WAITING_FOR_CLEAR_PATH" })
                {
                    Vector2 size = (Vector2)measure.Invoke(null, new object[] { device, state, scale, code, status });
                    Vector2 a = code.CalcSize(new GUIContent(device));
                    Vector2 b = status.CalcSize(new GUIContent(state));
                    float contentWidth = Mathf.Max(a.x, b.x);
                    Require(size.x >= contentWidth + 10f * scale, "Text clipped horizontally");
                    Require(size.x < contentWidth + 10f * scale + 1f, "Unnecessary fixed-width space");
                    Require(size.y >= a.y + b.y + 8f * scale, "Text clipped vertically");
                    Require((string)normalize.Invoke(null, new object[] { state }) == state, "Status truncated");
                    if (state == "ACTIVE") Require(size.x < (dense ? 92f : 112f) * scale, "Label did not shrink");
                    Require(code.fontSize == Mathf.RoundToInt(Mathf.Clamp(originalHeight * .39f, 9f, 14f)), "Code font shrunk");
                    cases++;
                }
            }
            Require(overlay.GetMethod("DrawStem", BindingFlags.NonPublic | BindingFlags.Instance) == null, "Connector still present");
            Require((string)normalize.Invoke(null, new object[] { "  returning  " }) == "RETURNING", "Status normalization changed");
            Debug.Log("[VirtualFleetLabelValidation] PASS " + cases + " sizing cases; full statuses and no connectors");
        }

        public static void BuildValidated()
        {
            Run();
            VirtualFleetPlaybackValidation.BuildValidated();
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new Exception("[VirtualFleetLabelValidation] " + message);
        }
    }
}
#endif
