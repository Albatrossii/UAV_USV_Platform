using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;

namespace UavUsv
{
    /// <summary>
    /// Draws stable UAV/USV identifiers above the runtime fleet. The labels are
    /// intentionally presentation-only: they never change fleet state or poses.
    /// </summary>
    [DisallowMultipleComponent]
    public sealed class VirtualFleetDeviceLabelOverlay : MonoBehaviour
    {
        private sealed class LabelTarget
        {
            public VirtualFleetDeviceState state;
            public float anchorHeight;
        }

        private sealed class VisibleLabel
        {
            public LabelTarget target;
            public Vector2 anchor;
            public Rect rect;
            public bool selected;
            public string code;
            public string status;
        }

        private readonly List<LabelTarget> targets = new List<LabelTarget>();
        private readonly List<VisibleLabel> visible = new List<VisibleLabel>();
        private readonly List<Rect> occupied = new List<Rect>();

        private VirtualFleetManager fleetManager;
        private Camera sceneCamera;
        private PlatformTools.WebDeviceObserverCamera observer;
        private GUIStyle codeStyle;
        private GUIStyle statusStyle;
        private Texture2D backgroundTexture;
        private Texture2D selectedBackgroundTexture;
        private float labelScale;
        private float nextBindingRefreshAt;

        private const float BindingRefreshSeconds = 0.75f;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Install()
        {
            if (FindObjectOfType<VirtualFleetDeviceLabelOverlay>())
                return;

            var host = new GameObject("VirtualFleetDeviceLabels");
            DontDestroyOnLoad(host);
            host.AddComponent<VirtualFleetDeviceLabelOverlay>();
        }

        private IEnumerator Start()
        {
            while (!BindRuntime())
                yield return null;
            RebuildTargets();
        }

        private void Update()
        {
            if (Time.unscaledTime < nextBindingRefreshAt)
                return;
            nextBindingRefreshAt = Time.unscaledTime + BindingRefreshSeconds;
            BindRuntime();
        }

        private bool BindRuntime()
        {
            if (!fleetManager)
            {
                fleetManager = FindObjectOfType<VirtualFleetManager>();
                if (fleetManager)
                    fleetManager.FleetChanged += RebuildTargets;
            }

            if (!sceneCamera)
                sceneCamera = Camera.main;
            if (!observer && sceneCamera)
                observer = sceneCamera.GetComponent<PlatformTools.WebDeviceObserverCamera>();
            return fleetManager && sceneCamera;
        }

        private void RebuildTargets()
        {
            targets.Clear();
            if (!fleetManager)
                return;
            AddTargets(fleetManager.Uavs);
            AddTargets(fleetManager.Usvs);
        }

        private void AddTargets(IReadOnlyList<VirtualFleetDeviceState> states)
        {
            for (int i = 0; i < states.Count; i++)
            {
                VirtualFleetDeviceState state = states[i];
                if (state == null || !state.transform || string.IsNullOrWhiteSpace(state.deviceCode))
                    continue;
                targets.Add(new LabelTarget
                {
                    state = state,
                    anchorHeight = CalculateAnchorHeight(state)
                });
            }
        }

        private static float CalculateAnchorHeight(VirtualFleetDeviceState state)
        {
            float fallback = string.Equals(state.deviceType, "UAV", StringComparison.OrdinalIgnoreCase)
                ? 1.15f
                : 0.85f;
            Renderer[] renderers = state.transform.GetComponentsInChildren<Renderer>(true);
            if (renderers.Length == 0)
                return fallback;

            float maximumY = state.transform.position.y + fallback;
            for (int i = 0; i < renderers.Length; i++)
            {
                Renderer renderer = renderers[i];
                if (renderer)
                    maximumY = Mathf.Max(maximumY, renderer.bounds.max.y);
            }
            return Mathf.Clamp(maximumY - state.transform.position.y + 0.28f, fallback, 4f);
        }

        private void OnGUI()
        {
            if (!fleetManager || !sceneCamera || Event.current.type != EventType.Repaint)
                return;
            EnsureGuiResources();
            BuildVisibleLabels();
            for (int i = 0; i < visible.Count; i++)
                DrawLabel(visible[i]);
        }

        private void BuildVisibleLabels()
        {
            visible.Clear();
            occupied.Clear();
            string selectedCode = observer ? observer.CurrentDeviceCode : string.Empty;
            float scale = Mathf.Clamp(Screen.height / 900f, 0.72f, 1.18f);
            labelScale = scale;
            // Preserve existing font sizes. Shrink empty space, never the text.
            float previousHeight = (targets.Count > 30 ? 23f : 31f) * scale;
            codeStyle.fontSize = Mathf.RoundToInt(Mathf.Clamp(previousHeight * 0.39f, 9f, 14f));
            statusStyle.fontSize = Mathf.RoundToInt(Mathf.Clamp(previousHeight * 0.29f, 8f, 11f));

            for (int i = 0; i < targets.Count; i++)
            {
                LabelTarget target = targets[i];
                if (target.state == null || !target.state.transform ||
                    !target.state.transform.gameObject.activeInHierarchy)
                    continue;

                string code = target.state.deviceCode;
                string status = NormalizeStatus(target.state.status);
                Vector2 size = MeasureLabel(code, status, scale, codeStyle, statusStyle);
                float width = size.x;
                float height = size.y;
                Vector3 world = target.state.transform.position + Vector3.up * target.anchorHeight;
                Vector3 screen = sceneCamera.WorldToScreenPoint(world);
                if (screen.z <= sceneCamera.nearClipPlane || screen.x < -width ||
                    screen.x > Screen.width + width || screen.y < -height ||
                    screen.y > Screen.height + height)
                    continue;

                Vector2 anchor = new Vector2(screen.x, Screen.height - screen.y);
                visible.Add(new VisibleLabel
                {
                    target = target,
                    code = code,
                    status = status,
                    anchor = anchor,
                    rect = new Rect(anchor.x - width * 0.5f, anchor.y - height - 9f * scale, width, height),
                    selected = !string.IsNullOrEmpty(selectedCode) &&
                        string.Equals(selectedCode, target.state.deviceCode, StringComparison.OrdinalIgnoreCase)
                });
            }

            visible.Sort((left, right) =>
            {
                int y = left.rect.y.CompareTo(right.rect.y);
                return y != 0 ? y : left.rect.x.CompareTo(right.rect.x);
            });

            for (int i = 0; i < visible.Count; i++)
            {
                VisibleLabel label = visible[i];
                label.rect.x = Mathf.Clamp(label.rect.x, 4f, Mathf.Max(4f, Screen.width - label.rect.width - 4f));
                label.rect.y = Mathf.Max(4f, label.rect.y);
                ResolveOverlap(label);
                occupied.Add(label.rect);
            }
        }

        private void ResolveOverlap(VisibleLabel label)
        {
            Rect original = label.rect;
            for (int attempt = 0; attempt < 8; attempt++)
            {
                bool overlaps = false;
                for (int i = 0; i < occupied.Count; i++)
                {
                    if (!label.rect.Overlaps(occupied[i]))
                        continue;
                    label.rect.y = occupied[i].yMax + 3f;
                    overlaps = true;
                }
                if (!overlaps)
                    break;
            }

            if (label.rect.yMax > Screen.height - 4f)
                label.rect.y = Mathf.Max(4f, original.y - label.rect.height - 8f);
        }

        private void DrawLabel(VisibleLabel label)
        {
            Color border = label.selected
                ? new Color(1f, 0.76f, 0.24f, 1f)
                : new Color(0.64f, 0.81f, 0.82f, 0.95f);

            GUI.DrawTexture(label.rect, label.selected ? selectedBackgroundTexture : backgroundTexture);
            DrawBorder(label.rect, border, 1f);
            float paddingX = 5f * labelScale;
            float paddingY = 3f * labelScale;
            float codeHeight = codeStyle.CalcSize(new GUIContent(label.code)).y;
            float statusHeight = statusStyle.CalcSize(new GUIContent(label.status)).y;
            codeStyle.normal.textColor = Color.white;
            statusStyle.normal.textColor = StatusColor(label.status);
            GUI.Label(new Rect(label.rect.x + paddingX, label.rect.y + paddingY,
                label.rect.width - paddingX * 2f, codeHeight), label.code, codeStyle);
            GUI.Label(new Rect(label.rect.x + paddingX, label.rect.y + paddingY + codeHeight + 2f * labelScale,
                label.rect.width - paddingX * 2f, statusHeight), label.status, statusStyle);
        }

        private static Vector2 MeasureLabel(string code, string status, float scale, GUIStyle codeFont, GUIStyle statusFont)
        {
            Vector2 codeSize = codeFont.CalcSize(new GUIContent(code));
            Vector2 statusSize = statusFont.CalcSize(new GUIContent(status));
            return new Vector2(Mathf.Ceil(Mathf.Max(codeSize.x, statusSize.x) + 10f * scale),
                Mathf.Ceil(codeSize.y + statusSize.y + 8f * scale));
        }

        private static void DrawBorder(Rect rect, Color color, float thickness)
        {
            Color previous = GUI.color;
            GUI.color = color;
            GUI.DrawTexture(new Rect(rect.x, rect.y, rect.width, thickness), Texture2D.whiteTexture);
            GUI.DrawTexture(new Rect(rect.x, rect.yMax - thickness, rect.width, thickness), Texture2D.whiteTexture);
            GUI.DrawTexture(new Rect(rect.x, rect.y, thickness, rect.height), Texture2D.whiteTexture);
            GUI.DrawTexture(new Rect(rect.xMax - thickness, rect.y, thickness, rect.height), Texture2D.whiteTexture);
            GUI.color = previous;
        }

        private static Color StatusColor(string status)
        {
            if (status.Contains("RETURN") || status.Contains("REJOIN"))
                return new Color(1f, 0.85f, 0.51f, 1f);
            if (status.Contains("HOVER") || status.Contains("HOLD") || status.Contains("PAUS"))
                return new Color(0.71f, 0.82f, 0.92f, 1f);
            if (status.Contains("RUN") || status.Contains("ACTIVE") || status.Contains("ONLINE") ||
                status.Contains("SUCCESS"))
                return new Color(0.76f, 0.9f, 0.89f, 1f);
            return new Color(0.65f, 0.8f, 0.8f, 1f);
        }

        private static string NormalizeStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return "ONLINE";
            return status.Trim().ToUpperInvariant();
        }

        private void EnsureGuiResources()
        {
            if (backgroundTexture)
                return;
            backgroundTexture = Solid(new Color(0.035f, 0.137f, 0.16f, 0.85f));
            selectedBackgroundTexture = Solid(new Color(0.12f, 0.105f, 0.045f, 0.94f));

            codeStyle = new GUIStyle(GUI.skin.label)
            {
                alignment = TextAnchor.MiddleCenter,
                fontStyle = FontStyle.Bold,
                clipping = TextClipping.Clip,
                richText = false,
                wordWrap = false,
                padding = new RectOffset(0, 0, 0, 0)
            };
            statusStyle = new GUIStyle(codeStyle)
            {
                fontStyle = FontStyle.Normal
            };
            statusStyle.normal.textColor = new Color(0.65f, 0.8f, 0.8f, 1f);
        }

        private static Texture2D Solid(Color color)
        {
            var texture = new Texture2D(1, 1, TextureFormat.RGBA32, false)
            {
                hideFlags = HideFlags.HideAndDontSave,
                filterMode = FilterMode.Point,
                wrapMode = TextureWrapMode.Clamp
            };
            texture.SetPixel(0, 0, color);
            texture.Apply(false, true);
            return texture;
        }

        private void OnDestroy()
        {
            if (fleetManager)
                fleetManager.FleetChanged -= RebuildTargets;
            DestroyTexture(backgroundTexture);
            DestroyTexture(selectedBackgroundTexture);
        }

        private static void DestroyTexture(Texture2D texture)
        {
            if (texture)
                Destroy(texture);
        }
    }
}
