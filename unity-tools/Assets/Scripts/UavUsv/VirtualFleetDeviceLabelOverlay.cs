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
        private Texture2D lineTexture;
        private Texture2D uavTexture;
        private Texture2D usvTexture;
        private Texture2D activeTexture;
        private Texture2D warningTexture;
        private Texture2D inactiveTexture;
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
            float width = (targets.Count > 30 ? 92f : 112f) * scale;
            float height = (targets.Count > 30 ? 23f : 31f) * scale;

            for (int i = 0; i < targets.Count; i++)
            {
                LabelTarget target = targets[i];
                if (target.state == null || !target.state.transform ||
                    !target.state.transform.gameObject.activeInHierarchy)
                    continue;

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
            VirtualFleetDeviceState state = label.target.state;
            bool isUav = string.Equals(state.deviceType, "UAV", StringComparison.OrdinalIgnoreCase);
            Color border = label.selected
                ? new Color(1f, 0.76f, 0.24f, 1f)
                : isUav
                    ? new Color(0.35f, 0.94f, 0.89f, 0.95f)
                    : new Color(0.78f, 0.88f, 0.88f, 0.9f);

            DrawStem(label.anchor, label.rect, border);
            GUI.DrawTexture(label.rect, label.selected ? selectedBackgroundTexture : backgroundTexture);
            DrawBorder(label.rect, border, label.selected ? 2f : 1f);

            float padding = Mathf.Max(5f, label.rect.height * 0.17f);
            float markerSize = Mathf.Clamp(label.rect.height * 0.23f, 5f, 8f);
            Rect typeMarker = new Rect(
                label.rect.x + padding,
                label.rect.center.y - markerSize * 0.5f,
                markerSize,
                markerSize
            );
            GUI.DrawTexture(typeMarker, isUav ? uavTexture : usvTexture);

            codeStyle.fontSize = Mathf.RoundToInt(Mathf.Clamp(label.rect.height * 0.39f, 9f, 14f));
            statusStyle.fontSize = Mathf.RoundToInt(Mathf.Clamp(label.rect.height * 0.29f, 8f, 11f));
            codeStyle.normal.textColor = label.selected
                ? new Color(1f, 0.83f, 0.38f, 1f)
                : Color.white;
            Rect codeRect = new Rect(
                typeMarker.xMax + padding * 0.72f,
                label.rect.y + 1f,
                label.rect.width - typeMarker.width - padding * 2.7f,
                label.rect.height * 0.56f
            );
            GUI.Label(codeRect, state.deviceCode, codeStyle);

            string status = NormalizeStatus(state.status);
            Texture2D statusTexture = StatusTexture(status);
            float dotSize = Mathf.Clamp(label.rect.height * 0.19f, 4f, 7f);
            Rect statusDot = new Rect(codeRect.x, label.rect.yMax - label.rect.height * 0.31f, dotSize, dotSize);
            GUI.DrawTexture(statusDot, statusTexture);
            Rect statusRect = new Rect(statusDot.xMax + 3f, label.rect.y + label.rect.height * 0.52f,
                label.rect.width - (statusDot.xMax - label.rect.x) - padding, label.rect.height * 0.38f);
            GUI.Label(statusRect, status, statusStyle);
        }

        private void DrawStem(Vector2 anchor, Rect rect, Color color)
        {
            float top = Mathf.Min(anchor.y, rect.center.y);
            float bottom = Mathf.Max(anchor.y, rect.center.y);
            GUI.color = new Color(color.r, color.g, color.b, 0.62f);
            GUI.DrawTexture(new Rect(anchor.x - 0.5f, top, 1f, Mathf.Max(2f, bottom - top)), lineTexture);
            GUI.DrawTexture(new Rect(anchor.x - 2.5f, anchor.y - 2.5f, 5f, 5f), lineTexture);
            GUI.color = Color.white;
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

        private Texture2D StatusTexture(string status)
        {
            if (status.Contains("HOVER") || status.Contains("HOLD") || status.Contains("PAUS"))
                return warningTexture;
            if (status.Contains("RUN") || status.Contains("ACTIVE") || status.Contains("ONLINE") ||
                status.Contains("SUCCESS"))
                return activeTexture;
            return inactiveTexture;
        }

        private static string NormalizeStatus(string status)
        {
            if (string.IsNullOrWhiteSpace(status))
                return "ONLINE";
            string normalized = status.Trim().ToUpperInvariant();
            return normalized.Length <= 12 ? normalized : normalized.Substring(0, 12);
        }

        private void EnsureGuiResources()
        {
            if (backgroundTexture)
                return;
            backgroundTexture = Solid(new Color(0.018f, 0.085f, 0.102f, 0.9f));
            selectedBackgroundTexture = Solid(new Color(0.12f, 0.105f, 0.045f, 0.94f));
            lineTexture = Solid(Color.white);
            uavTexture = Solid(new Color(1f, 0.31f, 0.29f, 1f));
            usvTexture = Solid(new Color(0.78f, 0.88f, 0.88f, 1f));
            activeTexture = Solid(new Color(0.28f, 0.91f, 0.58f, 1f));
            warningTexture = Solid(new Color(1f, 0.76f, 0.24f, 1f));
            inactiveTexture = Solid(new Color(0.49f, 0.61f, 0.63f, 1f));

            codeStyle = new GUIStyle(GUI.skin.label)
            {
                alignment = TextAnchor.MiddleLeft,
                fontStyle = FontStyle.Bold,
                clipping = TextClipping.Clip,
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
            DestroyTexture(lineTexture);
            DestroyTexture(uavTexture);
            DestroyTexture(usvTexture);
            DestroyTexture(activeTexture);
            DestroyTexture(warningTexture);
            DestroyTexture(inactiveTexture);
        }

        private static void DestroyTexture(Texture2D texture)
        {
            if (texture)
                Destroy(texture);
        }
    }
}
