#if UNITY_EDITOR
using System;
using System.Collections;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using UavUsv.PlatformTools;
using Object = UnityEngine.Object;

namespace UavUsv.Editor.Tools
{
    /// <summary>
    /// CPU-only camera geometry checks, without NUnit, Play Mode, or scene saves.
    /// -executeMethod UavUsv.Editor.Tools.WebCameraFramingValidation.Run
    /// Synthetic renderers are checked independently of the camera's framing report.
    /// </summary>
    public static class WebCameraFramingValidation
    {
        private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
        private const float Tolerance = .002f;

        [MenuItem("UAV-USV/Tests/Validate Web Camera Framing")]
        public static void Run()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Camera framing validation must run outside Play Mode.");

            Scene previous = SceneManager.GetActiveScene();
            // Batch mode can start with an unsaved untitled scene, beside which
            // Unity refuses to create an additive scene. Only our fixtures are
            // disposed in that case; existing user objects are never touched.
            bool ownsScene = !(Application.isBatchMode && previous.IsValid() &&
                previous.isLoaded && string.IsNullOrEmpty(previous.path));
            Scene scene = ownsScene
                ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive)
                : previous;
            int passed = 0;
            try
            {
                SceneManager.SetActiveScene(scene);
                int[,] viewports = { { 2560, 1080 }, { 1280, 960 }, { 600, 1000 }, { 420, 280 } };
                for (int index = 0; index < viewports.GetLength(0); index++)
                {
                    int width = viewports[index, 0], height = viewports[index, 1];
                    Case("all hulls and mission targets fit " + width + "x" + height, fixture =>
                    {
                        fixture.SetViewport(width, height);
                        fixture.AddDispersedFleet();
                        fixture.Frame();
                        fixture.AssertAllVisible();
                        Require(fixture.Subjects.Count == 6, "The fixture must include UAVs, USVs, protected and threat targets.");
                    }, ref passed);
                }

                Case("shore view is oblique and materially tighter than the old overview", fixture =>
                {
                    fixture.AddShoreFleet();
                    PoseFrame current = fixture.Frame();
                    fixture.AssertAllVisible();
                    float currentPixels = fixture.MeanHullPixels();
                    float pitch = fixture.Camera.transform.eulerAngles.x;
                    Require(pitch >= 38f && pitch <= 46f, "Expected an oblique shore view near 42 degrees, got " + pitch);
                    PoseFrame legacy = fixture.LegacyOverview();
                    fixture.Apply(legacy);
                    fixture.AssertAllVisible();
                    float legacyPixels = fixture.MeanHullPixels();
                    Require(current.Distance < legacy.Distance * .9f,
                        "The representative shore view did not move materially closer: " + current.Distance + " vs " + legacy.Distance);
                    Require(currentPixels > legacyPixels * 1.1f,
                        "Moving the camera did not materially enlarge projected hulls: " + currentPixels + " vs " + legacyPixels);
                }, ref passed);

                Case("a live resize keeps all hulls inside the next fitted frame", fixture =>
                {
                    fixture.AddDispersedFleet();
                    fixture.Frame();
                    fixture.SetViewport(420, 900);
                    fixture.Frame();
                    fixture.AssertAllVisible();
                    fixture.SetViewport(2560, 1080);
                    fixture.Frame();
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("moving outliers remain visible with a previously cached focus", fixture =>
                {
                    fixture.AddDispersedFleet();
                    PoseFrame first = fixture.Frame();
                    fixture.Subjects[0].position += new Vector3(-470f, 110f, 280f);
                    fixture.Subjects[5].position += new Vector3(620f, 0f, -340f);
                    // Supply a deliberately stale focus and distance, preserving
                    // the initialized state used by the real smoothing path.
                    fixture.Set("overviewFocus", first.Focus + new Vector3(65f, -20f, 40f));
                    fixture.Set("overviewDistance", first.Distance);
                    fixture.Frame();
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("inactive far subjects do not widen the overview", fixture =>
                {
                    fixture.AddShoreFleet();
                    PoseFrame expected = fixture.Frame();
                    Transform inactive = fixture.AddHull("USV-Inactive", new Vector3(8000f, 0f, -8000f), Vector3.one * 20f);
                    inactive.gameObject.SetActive(false);
                    fixture.Set("overviewFrameInitialized", false);
                    PoseFrame actual = fixture.Frame();
                    Near(actual.Position, expected.Position, "An inactive subject changed the view");
                    Near(actual.Focus, expected.Focus, "An inactive subject changed the focus");
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("disabled far child renderers do not widen the overview", fixture =>
                {
                    fixture.AddShoreFleet();
                    Transform child = fixture.AddChild(fixture.Subjects[0], "DisabledVisual", new Vector3(9000f, 80f, -7000f));
                    child.GetComponent<Renderer>().enabled = false;
                    PoseFrame actual = fixture.Frame();
                    child.gameObject.SetActive(false);
                    fixture.Set("overviewFrameInitialized", false);
                    PoseFrame expected = fixture.Frame();
                    Near(actual.Position, expected.Position, "A disabled renderer changed the view");
                    Require(actual.Distance < 150f, "A disabled far renderer inflated the fitted distance.");
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("a child activated after renderer caching is included", fixture =>
                {
                    Transform subject = fixture.AddHull("UAV-001", new Vector3(0f, 12f, 0f), new Vector3(3f, 1f, 3f));
                    Transform child = fixture.AddChild(subject, "FlightVisual", new Vector3(140f, 45f, -100f));
                    child.gameObject.SetActive(false);
                    fixture.Frame();
                    child.gameObject.SetActive(true);
                    fixture.Frame();
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("an empty fleet produces a finite useful view", fixture =>
                {
                    PoseFrame frame = fixture.Frame();
                    Require(frame.Distance >= 11.9f && frame.Distance < 100f, "An empty fleet has an invalid fallback distance.");
                    Require(frame.FieldOfView >= 40f && frame.FieldOfView <= 70f, "An empty fleet has an invalid FOV.");
                    Near(frame.Focus, Vector3.zero, "The empty fleet fallback focus is not the origin");
                }, ref passed);

                Case("rendererless live targets retain a finite visible fallback", fixture =>
                {
                    Transform first = fixture.AddEmpty("UAV-001", new Vector3(-65f, 40f, 30f), true);
                    Transform second = fixture.AddEmpty("Threat-001", new Vector3(80f, 1f, -60f), true);
                    fixture.Frame();
                    fixture.AssertBounds(new Bounds(first.position, Vector3.one), first.name);
                    fixture.AssertBounds(new Bounds(second.position, Vector3.one), second.name);
                }, ref passed);

                Case("framing does not mutate mission geometry", fixture =>
                {
                    fixture.AddDispersedFleet();
                    var snapshots = new List<GeometrySnapshot>();
                    foreach (Transform subject in fixture.Subjects)
                        snapshots.Add(new GeometrySnapshot(subject));
                    fixture.Frame();
                    fixture.SetViewport(600, 1000);
                    fixture.Frame();
                    fixture.Set("selectedSubject", fixture.Subjects[0]);
                    fixture.Frame("CalculateDeviceView");
                    foreach (GeometrySnapshot snapshot in snapshots) snapshot.AssertUnchanged();
                }, ref passed);

                foreach (string kind in new[] { "UAV", "USV" })
                {
                    string deviceKind = kind;
                    Case(deviceKind + " follow keeps the selected hull and a wider context FOV", fixture =>
                    {
                        Transform selected = fixture.AddHull(deviceKind + "-001", new Vector3(12f, 10f, -7f),
                            deviceKind == "UAV" ? new Vector3(3.2f, 1f, 3.2f) : new Vector3(6f, 1.6f, 2.4f));
                        selected.rotation = Quaternion.Euler(0f, 63f, 0f);
                        fixture.Set("selectedSubject", selected);
                        PoseFrame first = fixture.Frame("CalculateDeviceView");
                        fixture.AssertAllVisible();
                        Require(first.FieldOfView >= 50f && first.FieldOfView <= 60f, "Follow mode did not widen the context FOV.");
                        Require(Vector3.Distance(first.Focus, selected.position) < 10f, "Follow focus lost the selected subject.");
                        fixture.AddHull("USV-Remote", new Vector3(5000f, 1f, 5000f), Vector3.one * 10f);
                        PoseFrame withRemote = fixture.Frame("CalculateDeviceView");
                        Near(withRemote.Position, first.Position, "A remote craft turned follow into global framing");
                        Near(withRemote.Focus, first.Focus, "A remote craft changed the selected focus");
                        fixture.AssertBounds(selected.GetComponent<Renderer>().bounds, selected.name);
                    }, ref passed);
                }

                Case("lighthouse view contains a dispersed fleet", fixture =>
                {
                    fixture.AddDispersedFleet();
                    fixture.Set("lighthouse", fixture.AddEmpty("ShoreLighthouse", new Vector3(-130f, 4f, -150f), false));
                    fixture.SetViewport(700, 1000);
                    fixture.Frame("CalculateLighthouseView");
                    fixture.AssertAllVisible();
                }, ref passed);

                Case("very distant targets remain inside the far clipping plane", fixture =>
                {
                    fixture.Camera.farClipPlane = 100f;
                    fixture.AddHull("UAV-001", new Vector3(-1400f, 160f, -900f), new Vector3(4f, 2f, 4f));
                    fixture.AddHull("Protected-001", new Vector3(1700f, 1f, 1100f), new Vector3(12f, 4f, 5f));
                    fixture.Frame();
                    fixture.AssertAllVisible();
                    Require(fixture.Camera.farClipPlane > 100f, "The far plane did not extend for a dispersed fleet.");
                }, ref passed);

                Debug.Log("[WebCameraFramingValidation] PASS " + passed + "/17 deterministic cases.");
            }
            finally
            {
                if (previous.IsValid() && previous.isLoaded) SceneManager.SetActiveScene(previous);
                if (ownsScene && scene.IsValid() && scene.isLoaded) EditorSceneManager.CloseScene(scene, true);
            }
        }

        [MenuItem("UAV-USV/Tools/Validate Framing And Build Virtual Fleet WebGL")]
        public static void BuildValidated()
        {
            Run();
            VirtualFleetLabelValidation.BuildValidated();
        }

        private static void Case(string name, Action<Fixture> check, ref int passed)
        {
            using (var fixture = new Fixture())
            {
                try
                {
                    check(fixture);
                    passed++;
                    Debug.Log("[WebCameraFramingValidation] PASS " + name);
                }
                catch (Exception error)
                {
                    Exception cause = error is TargetInvocationException && error.InnerException != null ? error.InnerException : error;
                    throw new InvalidOperationException("[WebCameraFramingValidation] FAILED: " + name, cause);
                }
            }
        }

        private static void Require(bool value, string message)
        {
            if (!value) throw new InvalidOperationException(message);
        }

        private static bool Finite(float value) => !float.IsNaN(value) && !float.IsInfinity(value);
        private static bool Finite(Vector3 value) => Finite(value.x) && Finite(value.y) && Finite(value.z);

        private static void Near(Vector3 actual, Vector3 expected, string message)
        {
            Require(Vector3.Distance(actual, expected) <= .003f, message + ": expected " + expected + ", actual " + actual);
        }

        private static Vector3 Corner(Bounds bounds, int corner)
        {
            return bounds.center + Vector3.Scale(bounds.extents, new Vector3(
                (corner & 1) == 0 ? -1f : 1f, (corner & 2) == 0 ? -1f : 1f, (corner & 4) == 0 ? -1f : 1f));
        }

        private struct PoseFrame
        {
            public Vector3 Position, Focus;
            public float FieldOfView;
            public float Distance => Vector3.Distance(Position, Focus);
        }

        private sealed class GeometrySnapshot
        {
            private readonly Transform subject, parent;
            private readonly Vector3 position, scale, boundsCenter, boundsSize;
            private readonly Quaternion rotation;
            private readonly bool active, rendererEnabled;
            private readonly Renderer renderer;

            public GeometrySnapshot(Transform value)
            {
                subject = value; parent = value.parent;
                position = value.position; rotation = value.rotation; scale = value.localScale;
                active = value.gameObject.activeSelf;
                renderer = value.GetComponent<Renderer>();
                rendererEnabled = renderer.enabled;
                boundsCenter = renderer.bounds.center; boundsSize = renderer.bounds.size;
            }

            public void AssertUnchanged()
            {
                Require(subject && subject.parent == parent && subject.gameObject.activeSelf == active,
                    "Framing changed mission hierarchy or activation.");
                Near(subject.position, position, "Framing moved " + subject.name);
                Near(subject.localScale, scale, "Framing scaled " + subject.name);
                Require(Quaternion.Angle(subject.rotation, rotation) <= .01f, "Framing rotated " + subject.name);
                Require(renderer && renderer.enabled == rendererEnabled, "Framing changed a renderer.");
                Near(renderer.bounds.center, boundsCenter, "Framing changed model bounds");
                Near(renderer.bounds.size, boundsSize, "Framing changed model size");
            }
        }

        private sealed class Fixture : IDisposable
        {
            public readonly List<Transform> Subjects = new List<Transform>();
            public readonly Camera Camera;
            private readonly GameObject root;
            private readonly WebDeviceObserverCamera observer;
            private readonly List<GameObject> owned = new List<GameObject>();
            private RenderTexture viewport;

            public Fixture()
            {
                root = new GameObject("CameraFramingValidation-" + Guid.NewGuid().ToString("N"));
                try
                {
                    Camera = root.AddComponent<Camera>();
                    Camera.enabled = false;
                    Camera.nearClipPlane = .3f;
                    Camera.farClipPlane = 1000f;
                    observer = root.AddComponent<WebDeviceObserverCamera>();
                    observer.enabled = false;
                    Set("observedCamera", Camera);
                    Set("overviewFrameInitialized", false);
                    SetViewport(1920, 1080);
                }
                catch { Dispose(); throw; }
            }

            public void SetViewport(int width, int height)
            {
                Camera.targetTexture = null;
                if (viewport) Object.DestroyImmediate(viewport);
                // Merely setting dimensions does not allocate a GPU texture.
                // Never call Create or Render; projection uses camera metadata.
                viewport = new RenderTexture(width, height, 0) { hideFlags = HideFlags.HideAndDontSave };
                Camera.targetTexture = viewport;
                Camera.rect = new Rect(0f, 0f, 1f, 1f);
                Camera.aspect = (float)width / height;
                Require(Camera.pixelWidth == width && Camera.pixelHeight == height, "The test viewport dimensions were not applied.");
            }

            public Transform AddEmpty(string name, Vector3 position, bool target)
            {
                var item = new GameObject(name);
                owned.Add(item);
                item.transform.position = position;
                if (target) Register(item.transform);
                return item.transform;
            }

            public Transform AddHull(string name, Vector3 position, Vector3 size)
            {
                GameObject item = GameObject.CreatePrimitive(PrimitiveType.Cube);
                owned.Add(item);
                item.name = name;
                item.transform.position = position;
                item.transform.localScale = size;
                Register(item.transform);
                return item.transform;
            }

            public Transform AddChild(Transform parent, string name, Vector3 worldPosition)
            {
                GameObject item = GameObject.CreatePrimitive(PrimitiveType.Cube);
                owned.Add(item);
                item.name = name;
                item.transform.position = worldPosition;
                item.transform.localScale = new Vector3(9f, 4f, 7f);
                item.transform.SetParent(parent, true);
                return item.transform;
            }

            private void Register(Transform target)
            {
                Subjects.Add(target);
                Get<IList>("sceneTargets").Add(target);
            }

            public void AddDispersedFleet()
            {
                AddHull("UAV-001", new Vector3(-160f, 42f, -75f), new Vector3(8f, 3f, 8f));
                AddHull("UAV-002", new Vector3(95f, 76f, 110f), new Vector3(5f, 2f, 5f));
                AddHull("USV-001", new Vector3(-90f, 1f, 140f), new Vector3(14f, 4f, 5f));
                AddHull("USV-002", new Vector3(180f, 1f, -120f), new Vector3(10f, 3f, 4f));
                AddHull("Protected-001", new Vector3(15f, 1f, 20f), new Vector3(18f, 5f, 7f));
                AddHull("Threat-001", new Vector3(230f, 1f, 175f), new Vector3(12f, 4f, 5f));
                for (int i = 0; i < Subjects.Count; i++) Subjects[i].rotation = Quaternion.Euler(0f, i * 37f, 0f);
            }

            public void AddShoreFleet()
            {
                for (int i = 0; i < 4; i++)
                {
                    AddHull("USV-00" + (i + 1), new Vector3(-18f + i * 12f, 1f, 0f), new Vector3(6f, 1.6f, 2.2f));
                    AddHull("UAV-00" + (i + 1), new Vector3(-15f + i * 10f, 5f + i, 9f), new Vector3(3.4f, 1.2f, 3.4f));
                }
                AddHull("Protected-001", new Vector3(4f, 1f, 17f), new Vector3(8f, 2.4f, 3.2f));
                AddHull("Threat-001", new Vector3(-18f, 1f, 22f), new Vector3(8f, 2.4f, 3.2f));
            }

            public PoseFrame Frame(string method = "CalculateOverview")
            {
                object[] output = { Vector3.zero, Vector3.zero };
                MethodInfo info = typeof(WebDeviceObserverCamera).GetMethod(method, PrivateInstance);
                Require(info != null, "Missing camera method " + method);
                info.Invoke(observer, output);
                var frame = new PoseFrame { Position = (Vector3)output[0], Focus = (Vector3)output[1], FieldOfView = Get<float>("desiredFieldOfView") };
                Require(Finite(frame.Position) && Finite(frame.Focus) && Finite(frame.FieldOfView) && frame.Distance > .01f,
                    "Camera calculation returned a non-finite or degenerate view.");
                Apply(frame);
                return frame;
            }

            public void Apply(PoseFrame frame)
            {
                root.transform.SetPositionAndRotation(frame.Position, Quaternion.LookRotation(frame.Focus - frame.Position, Vector3.up));
                Camera.fieldOfView = frame.FieldOfView;
            }

            private IEnumerable<Renderer> VisibleRenderers()
            {
                foreach (Transform subject in Subjects)
                {
                    if (!subject || !subject.gameObject.activeInHierarchy) continue;
                    foreach (Renderer renderer in subject.GetComponentsInChildren<Renderer>(true))
                        if (renderer && renderer.enabled && renderer.gameObject.activeInHierarchy &&
                            !(renderer is LineRenderer) && !(renderer is ParticleSystemRenderer)) yield return renderer;
                }
            }

            public void AssertAllVisible()
            {
                int checkedCount = 0;
                foreach (Renderer renderer in VisibleRenderers())
                {
                    AssertBounds(renderer.bounds, renderer.name);
                    checkedCount++;
                }
                Require(checkedCount > 0, "No hulls were checked.");
            }

            public void AssertBounds(Bounds bounds, string name)
            {
                for (int corner = 0; corner < 8; corner++)
                {
                    Vector3 projected = Camera.WorldToViewportPoint(Corner(bounds, corner));
                    Require(Finite(projected) && projected.z >= Camera.nearClipPlane - Tolerance &&
                        projected.z <= Camera.farClipPlane + Tolerance &&
                        projected.x >= -Tolerance && projected.x <= 1f + Tolerance &&
                        projected.y >= -Tolerance && projected.y <= 1f + Tolerance,
                        "Clipped hull " + name + " corner " + corner + " at viewport " + projected +
                        "; near/far=" + Camera.nearClipPlane + "/" + Camera.farClipPlane);
                }
            }

            public float MeanHullPixels()
            {
                float sum = 0f;
                int count = 0;
                foreach (Renderer renderer in VisibleRenderers())
                {
                    Vector2 low = Vector2.one * float.PositiveInfinity, high = Vector2.one * float.NegativeInfinity;
                    for (int corner = 0; corner < 8; corner++)
                    {
                        Vector3 p = Camera.WorldToViewportPoint(Corner(renderer.bounds, corner));
                        low = Vector2.Min(low, new Vector2(p.x, p.y));
                        high = Vector2.Max(high, new Vector2(p.x, p.y));
                    }
                    sum += Mathf.Max((high.x - low.x) * Camera.pixelWidth, (high.y - low.y) * Camera.pixelHeight);
                    count++;
                }
                Require(count > 0, "Cannot measure an empty fixture.");
                return sum / count;
            }

            public PoseFrame LegacyOverview()
            {
                Quaternion rotation = Quaternion.Euler(60f, -35f, 0f), inverse = Quaternion.Inverse(rotation);
                var points = new List<Vector3>();
                Vector3 low = Vector3.one * float.PositiveInfinity, high = Vector3.one * float.NegativeInfinity;
                foreach (Renderer renderer in VisibleRenderers())
                    for (int corner = 0; corner < 8; corner++)
                    {
                        Vector3 point = Corner(renderer.bounds, corner);
                        points.Add(point);
                        low = Vector3.Min(low, inverse * point);
                        high = Vector3.Max(high, inverse * point);
                    }
                Vector3 focus = rotation * ((low + high) * .5f);
                float tanY = Mathf.Tan(52f * Mathf.Deg2Rad * .5f), tanX = tanY * Camera.aspect;
                float distance = 20f;
                foreach (Vector3 point in points)
                {
                    Vector3 local = inverse * (point - focus);
                    distance = Mathf.Max(distance, Mathf.Abs(local.x) / (tanX * .88f) - local.z);
                    distance = Mathf.Max(distance, Mathf.Abs(local.y) / (tanY * .84f) - local.z);
                    distance = Mathf.Max(distance, Camera.nearClipPlane + 1f - local.z);
                }
                return new PoseFrame { Position = focus - rotation * Vector3.forward * distance, Focus = focus, FieldOfView = 52f };
            }

            public T Get<T>(string name) => (T)Field(name).GetValue(observer);
            public void Set(string name, object value) => Field(name).SetValue(observer, value);

            private static FieldInfo Field(string name)
            {
                FieldInfo field = typeof(WebDeviceObserverCamera).GetField(name, PrivateInstance);
                Require(field != null, "Missing camera field " + name);
                return field;
            }

            public void Dispose()
            {
                if (Camera) Camera.targetTexture = null;
                if (viewport) Object.DestroyImmediate(viewport);
                if (root) Object.DestroyImmediate(root);
                for (int i = owned.Count - 1; i >= 0; i--)
                    if (owned[i]) Object.DestroyImmediate(owned[i]);
            }
        }
    }
}
#endif
