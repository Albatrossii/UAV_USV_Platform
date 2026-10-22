#if UNITY_EDITOR
using System;
using System.Collections;
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
    /// Deterministic camera regression checks, runnable without NUnit or Play Mode:
    /// -executeMethod UavUsv.Editor.Tools.WebCameraTransitionValidation.Run
    /// All objects live in a disposable additive scene; no existing scene is saved.
    /// </summary>
    public static class WebCameraTransitionValidation
    {
        private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;
        private const float Duration = 1.2f;
        private static readonly Vector3 StartPosition = new Vector3(13f, 17f, -29f);
        private static readonly Quaternion StartRotation = Quaternion.Euler(31f, 22f, 0f);
        private static readonly Vector3 TargetPosition = new Vector3(-80f, 140f, -120f);
        private static readonly Quaternion TargetRotation = Quaternion.Euler(52f, -35f, 0f);
        private const float StartFov = 45f;
        private const float TargetFov = 64f;

        [MenuItem("UAV-USV/Tests/Validate Web Camera Transition")]
        public static void Run()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode)
                throw new InvalidOperationException("Camera validation must run outside Play Mode.");

            Scene previousActiveScene = SceneManager.GetActiveScene();
            // A fresh batch-mode Editor starts with an unsaved, untitled scene;
            // Unity refuses an additive scene beside it. Reuse that temporary
            // scene without saving/closing it, and dispose only our own objects.
            bool ownsTestScene = !(Application.isBatchMode && previousActiveScene.IsValid()
                && previousActiveScene.isLoaded && string.IsNullOrEmpty(previousActiveScene.path));
            Scene testScene = ownsTestScene
                ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive)
                : previousActiveScene;
            int passed = 0;
            try
            {
                SceneManager.SetActiveScene(testScene);

                Case("begin requires an observed camera", fixture =>
                {
                    fixture.Set("observedCamera", null);
                    fixture.Invoke("BeginOverviewTransition");
                    Require(!fixture.Active, "A missing camera must not start a transition.");
                }, ref passed);

                Case("first overview does not animate a bootstrap pose", fixture =>
                {
                    fixture.SetMode("mode", "None");
                    fixture.Set("hasRenderedObserverFrame", false);
                    fixture.Observer.SetOverview();
                    Require(!fixture.Active, "The first overview must be framed immediately.");
                    fixture.AssertPose(StartPosition, StartRotation, StartFov);
                    Require(fixture.Observer.CurrentModeName == "overview", "Overview mode was not selected.");
                }, ref passed);

                Case("transition captures the visible pose without an initial jump", fixture =>
                {
                    fixture.Begin();
                    Near(fixture.Get<Vector3>("overviewTransitionStartPosition"), StartPosition, "Captured position");
                    Near(fixture.Get<Quaternion>("overviewTransitionStartRotation"), StartRotation, "Captured rotation");
                    Near(fixture.Get<float>("overviewTransitionStartFieldOfView"), StartFov, "Captured FOV");
                    Require(fixture.Apply(0f), "The first transition frame must be handled.");
                    fixture.AssertPose(StartPosition, StartRotation, StartFov);
                    Require(fixture.Active, "The first frame prematurely completed the transition.");
                }, ref passed);

                Case("quarter progress applies smoothstep to position rotation and FOV", fixture =>
                {
                    fixture.Begin();
                    fixture.Apply(Duration * .25f);
                    fixture.AssertInterpolated(TargetPosition, TargetRotation, TargetFov, .15625f);
                }, ref passed);

                Case("midpoint interpolates all three camera properties", fixture =>
                {
                    fixture.Begin();
                    fixture.Apply(Duration * .5f);
                    fixture.AssertInterpolated(TargetPosition, TargetRotation, TargetFov, .5f);
                    Require(fixture.Active, "The midpoint must still be active.");
                }, ref passed);

                Case("final frame reaches the exact target and completes", fixture =>
                {
                    fixture.Begin();
                    Require(fixture.Apply(Duration), "The final frame must be handled.");
                    fixture.AssertPose(TargetPosition, TargetRotation, TargetFov);
                    Require(!fixture.Active, "The final frame did not complete the transition.");
                }, ref passed);

                Case("late frames clamp to the final target", fixture =>
                {
                    fixture.Begin();
                    fixture.Apply(Duration + 10f);
                    fixture.AssertPose(TargetPosition, TargetRotation, TargetFov);
                    Require(!fixture.Active, "A late final frame must complete the transition.");
                }, ref passed);

                Case("explicit unscaled-clock progress completes while timeScale is zero", fixture =>
                {
                    float previousTimeScale = Time.timeScale;
                    try
                    {
                        Time.timeScale = 0f;
                        fixture.Begin();
                        fixture.Apply(Duration);
                        fixture.AssertPose(TargetPosition, TargetRotation, TargetFov);
                        Require(!fixture.Active, "The unscaled-clock transition did not complete while paused.");
                    }
                    finally { Time.timeScale = previousTimeScale; }
                }, ref passed);

                Case("moving overview targets interpolate from the original fixed start", fixture =>
                {
                    fixture.Begin();
                    fixture.Apply(Duration * .25f);
                    Vector3 movingPosition = TargetPosition + new Vector3(35f, 12f, 18f);
                    Quaternion movingRotation = Quaternion.Euler(59f, 11f, 0f);
                    const float movingFov = 71f;
                    fixture.Apply(movingPosition, movingRotation, movingFov, Duration * .5f);
                    fixture.AssertInterpolated(movingPosition, movingRotation, movingFov, .5f);
                    Near(fixture.Get<Vector3>("overviewTransitionStartPosition"), StartPosition, "The fixed start changed");
                    fixture.Apply(movingPosition, movingRotation, movingFov, Duration);
                    fixture.AssertPose(movingPosition, movingRotation, movingFov);
                }, ref passed);

                Case("inactive transition does not alter the camera", fixture =>
                {
                    Require(!fixture.Apply(Duration), "An inactive transition must return false.");
                    fixture.AssertPose(StartPosition, StartRotation, StartFov);
                }, ref passed);

                Case("explicit cancellation leaves the visible pose untouched", fixture =>
                {
                    fixture.Begin();
                    fixture.Apply(Duration * .5f);
                    fixture.Invoke("CancelOverviewTransition");
                    Require(!fixture.Active, "Cancel did not clear the active flag.");
                    Require(!fixture.Apply(Duration), "Cancelled transition must not resume.");
                    fixture.AssertInterpolated(TargetPosition, TargetRotation, TargetFov, .5f);
                }, ref passed);

                Case("repeated overview does not restart an in-flight transition", fixture =>
                {
                    fixture.Observer.SetOverview();
                    Require(fixture.Active, "Returning from a rendered device view should transition.");
                    fixture.Set("overviewTransitionStartedAt", 0f);
                    fixture.Apply(Duration * .25f);
                    fixture.Observer.SetOverview();
                    Near(fixture.Get<float>("overviewTransitionStartedAt"), 0f, "Repeated overview reset the clock");
                    Near(fixture.Get<Vector3>("overviewTransitionStartPosition"), StartPosition, "Repeated overview recaptured the start");
                    fixture.Apply(Duration * .5f);
                    fixture.AssertInterpolated(TargetPosition, TargetRotation, TargetFov, .5f);
                }, ref passed);

                Case("release to the original camera cancels the transition", fixture =>
                {
                    fixture.Begin();
                    fixture.Observer.ReleaseToOriginalCamera();
                    Require(!fixture.Active, "Release did not cancel the transition.");
                    Require(fixture.Observer.CurrentModeName == "action", "Release did not leave observer mode.");
                }, ref passed);

                Case("lighthouse mode cancels the transition", fixture =>
                {
                    fixture.Begin();
                    fixture.Observer.SetLighthouse();
                    Require(!fixture.Active, "Lighthouse mode did not cancel the transition.");
                    Require(fixture.Observer.CurrentModeName == "lighthouse", "Lighthouse mode was not selected.");
                }, ref passed);

                Case("successful device selection cancels the transition", fixture =>
                {
                    string deviceCode = fixture.CreateUniqueUav();
                    fixture.Begin();
                    bool selected = fixture.Observer.TrySelectDevice(deviceCode, out string canonical, out string profile, out string error);
                    Require(selected, "The fixture UAV could not be selected: " + error);
                    Require(canonical == deviceCode && profile == "uav-overwatch", "Unexpected selected device or profile.");
                    Require(!fixture.Active, "Device selection did not cancel the transition.");
                    Require(fixture.Observer.CurrentModeName == "device-follow", "Device-follow mode was not selected.");
                    Require(fixture.Get<Transform>("selectedSubject") == fixture.Device.transform, "Selection escaped the fixture scene.");
                }, ref passed);

                Case("failed device selection preserves an in-flight transition", fixture =>
                {
                    fixture.Observer.SetOverview();
                    fixture.Set("overviewTransitionStartedAt", 0f);
                    bool selected = fixture.Observer.TrySelectDevice("missing-camera-validation-" + Guid.NewGuid().ToString("N"), out _, out _, out string error);
                    Require(!selected && !string.IsNullOrEmpty(error), "A missing device should be rejected.");
                    Require(fixture.Active, "Rejected selection must not interrupt the existing overview transition.");
                    Require(fixture.Observer.CurrentModeName == "overview", "Rejected selection changed the camera mode.");
                    Near(fixture.Get<float>("overviewTransitionStartedAt"), 0f, "Rejected selection restarted the clock");
                }, ref passed);

                Case("automatic overview reset starts from the current manual pose", fixture =>
                {
                    fixture.SetMode("mode", "Overview");
                    fixture.SetMode("interactionMode", "Overview");
                    fixture.Set("interactionFocusOffset", new Vector3(7f, 0f, -5f));
                    fixture.Set("interactionDistance", 37f);
                    fixture.Invoke("ResetCameraInteractionToAutomatic");
                    Require(fixture.Active, "Manual overview reset should begin a transition.");
                    Require(fixture.Get<object>("interactionMode").ToString() == "None", "Manual interaction was not cleared.");
                    Near(fixture.Get<Vector3>("interactionFocusOffset"), Vector3.zero, "Interaction offset was not cleared");
                    Near(fixture.Get<float>("interactionDistance"), 0f, "Interaction distance was not cleared");
                    Near(fixture.Get<Vector3>("overviewTransitionStartPosition"), StartPosition, "Manual reset did not capture the visible pose");
                    fixture.Set("overviewTransitionStartedAt", 0f);
                    fixture.Invoke("ResetCameraInteractionToAutomatic");
                    Near(fixture.Get<float>("overviewTransitionStartedAt"), 0f, "An already automatic view restarted its transition");
                }, ref passed);

                Case("manual takeover preserves the actual interpolated view", fixture =>
                {
                    fixture.SetMode("mode", "Overview");
                    fixture.Begin();
                    fixture.Apply(Duration * .5f);
                    Vector3 visiblePosition = fixture.Root.transform.position;
                    Quaternion visibleRotation = fixture.Root.transform.rotation;
                    float visibleFov = fixture.Camera.fieldOfView;
                    Vector3 baseFocus = new Vector3(9f, 0f, -12f);
                    fixture.Invoke("InitializeInteractionFromCurrentView", baseFocus);
                    Require(!fixture.Active, "Manual takeover did not cancel the automatic transition.");
                    Require(fixture.Get<object>("interactionMode").ToString() == "Overview", "Manual takeover did not enter overview interaction.");
                    fixture.AssertPose(visiblePosition, visibleRotation, visibleFov);
                    Quaternion orbit = Quaternion.Euler(fixture.Get<float>("interactionPitch"), fixture.Get<float>("interactionYaw"), fixture.Get<float>("interactionRoll"));
                    Vector3 focus = baseFocus + fixture.Get<Vector3>("interactionFocusOffset");
                    Vector3 reconstructed = focus - orbit * Vector3.forward * fixture.Get<float>("interactionDistance");
                    Near(reconstructed, visiblePosition, "Manual takeover was initialized from the destination instead of the visible pose");
                    Near(orbit, visibleRotation, "Manual takeover changed the visible direction");
                }, ref passed);

                Case("camera and mission changes cannot enable trajectories", fixture =>
                {
                    FieldInfo gate = typeof(WebDeviceObserverCamera).GetField("TrajectoryRenderingEnabled", BindingFlags.NonPublic | BindingFlags.Static);
                    Require(gate != null && !(bool)gate.GetRawConstantValue(), "Trajectory rendering must remain compile-time disabled.");
                    fixture.Observer.SetOverview();
                    fixture.Observer.SetMissionState("RUNNING");
                    Require(!fixture.Get<bool>("trailRecordingEnabled"), "Mission start enabled trail recording.");
                    Require(fixture.Get<Transform>("trailRoot") == null, "A trajectory root was created.");
                    Require(fixture.Get<IDictionary>("fleetTrails").Count == 0, "Trajectory renderers were created.");
                }, ref passed);

                Case("completed overview contains every full hull including elevated UAVs", fixture =>
                {
                    Vector3[] positions = {
                        new Vector3(-160f, 42f, -75f), new Vector3(95f, 76f, 110f),
                        new Vector3(-90f, 1f, 140f), new Vector3(180f, 1f, -120f)
                    };
                    GameObject[] hulls = new GameObject[positions.Length];
                    try
                    {
                        IList targets = fixture.Get<IList>("sceneTargets");
                        targets.Clear();
                        for (int index = 0; index < hulls.Length; index++)
                        {
                            hulls[index] = GameObject.CreatePrimitive(PrimitiveType.Cube);
                            hulls[index].name = (index < 2 ? "UAV-" : "USV-") + (index + 1).ToString("000");
                            hulls[index].transform.position = positions[index];
                            hulls[index].transform.localScale = index < 2 ? new Vector3(8f, 3f, 8f) : new Vector3(14f, 4f, 5f);
                            hulls[index].transform.rotation = Quaternion.Euler(0f, index * 37f, 0f);
                            targets.Add(hulls[index].transform);
                        }
                        fixture.Camera.aspect = 16f / 9f;
                        fixture.SetMode("mode", "Overview");
                        fixture.Set("overviewFrameInitialized", false);
                        object[] output = { Vector3.zero, Vector3.zero };
                        fixture.Invoke("CalculateOverview", output);
                        Vector3 position = (Vector3)output[0];
                        Vector3 focus = (Vector3)output[1];
                        Quaternion rotation = Quaternion.LookRotation(focus - position, Vector3.up);
                        float fov = fixture.Get<float>("desiredFieldOfView");
                        fixture.Begin();
                        fixture.Apply(position, rotation, fov, Duration);
                        fixture.AssertPose(position, rotation, fov);
                        WebDeviceObserverCamera.FramingReport report = fixture.Observer.GetFramingReport();
                        Require(report.deviceCount == hulls.Length, "The overview report lost a fixture hull.");
                        Require(report.outsideCount == 0, "The completed overview clips " + report.outsideCount + " full hulls.");
                    }
                    finally
                    {
                        foreach (GameObject hull in hulls)
                            if (hull) Object.DestroyImmediate(hull);
                    }
                }, ref passed);

                Case("command focus restores original manual overview after replacement", fixture =>
                {
                    string code = fixture.CreateUniqueUav();
                    fixture.SetMode("mode", "Overview");
                    fixture.SetMode("interactionMode", "Overview");
                    fixture.Set("interactionYaw", 73f);
                    fixture.Set("interactionPitch", 48f);
                    fixture.Set("interactionDistance", 175f);
                    Require(fixture.Observer.FocusCommand(new[] {code}), "Focus rejected");
                    Require(fixture.Observer.FocusCommand(new[] {code}), "Replacement focus rejected");
                    fixture.Observer.RestoreCommandCamera();
                    Require(fixture.Observer.CurrentModeName == "overview", "Original view was lost");
                    Near(fixture.Get<float>("interactionYaw"), 73f, "Original yaw");
                    Near(fixture.Get<float>("interactionPitch"), 48f, "Original pitch");
                    Near(fixture.Get<float>("interactionDistance"), 175f, "Original zoom");
                    Require(fixture.Active, "Restore must transition smoothly");
                }, ref passed);
                Case("manual navigation prevents old command restore", fixture =>
                {
                    string code = fixture.CreateUniqueUav();
                    fixture.SetMode("mode", "Overview");
                    Require(fixture.Observer.FocusCommand(new[] {code}), "Focus rejected");
                    fixture.Observer.TrySelectDevice(code, out _, out _, out _);
                    fixture.Observer.RestoreCommandCamera();
                    Require(fixture.Observer.CurrentModeName == "device-follow", "Manual follow was overwritten");
                }, ref passed);
                Case("missing command target cannot alter the camera", fixture =>
                {
                    Require(!fixture.Observer.FocusCommand(new[] {"UAV-missing"}), "Missing target accepted");
                    fixture.Observer.RestoreCommandCamera();
                    fixture.AssertPose(StartPosition, StartRotation, StartFov);
                }, ref passed);
                Case("multiple addressed nodes are framed together and cleared on restore", fixture =>
                {
                    string first = fixture.CreateUniqueUav();
                    fixture.Device.transform.parent = fixture.Root.transform;
                    string second = fixture.CreateUniqueUav();
                    fixture.Device.transform.parent = fixture.Root.transform;
                    fixture.SetMode("mode", "Overview");
                    Require(fixture.Observer.FocusCommand(new[] {first, second}), "Group focus rejected");
                    Require(fixture.Get<IList>("commandSubjects").Count == 2, "Group membership changed");
                    Require(fixture.Observer.CurrentModeName == "overview", "Group must be framed together");
                    fixture.Observer.RestoreCommandCamera();
                    Require(fixture.Get<IList>("commandSubjects").Count == 0, "Temporary group survived restore");
                }, ref passed);
                Debug.Log("[WebCameraTransitionValidation] PASS " + passed + "/24 deterministic cases; transition duration=" + Duration + "s.");
            }
            finally
            {
                if (previousActiveScene.IsValid() && previousActiveScene.isLoaded)
                    SceneManager.SetActiveScene(previousActiveScene);
                if (ownsTestScene && testScene.IsValid() && testScene.isLoaded)
                    EditorSceneManager.CloseScene(testScene, true);
            }
        }

        [MenuItem("UAV-USV/Tools/Validate Camera And Build Virtual Fleet WebGL")]
        public static void BuildValidated()
        {
            Run();
            VirtualFleetReturnInfrastructureValidation.Run();
            VueWebGlBuildTool.BuildVirtualFleet();
        }

        private static void Case(string name, Action<Fixture> check, ref int passed)
        {
            using (var fixture = new Fixture())
            {
                try
                {
                    check(fixture);
                    passed++;
                    Debug.Log("[WebCameraTransitionValidation] PASS " + name);
                }
                catch (Exception error)
                {
                    Exception cause = error is TargetInvocationException && error.InnerException != null ? error.InnerException : error;
                    throw new InvalidOperationException("[WebCameraTransitionValidation] FAILED: " + name, cause);
                }
            }
        }

        private static void Require(bool condition, string message)
        {
            if (!condition) throw new InvalidOperationException(message);
        }

        private static void Near(Vector3 actual, Vector3 expected, string message)
        {
            Require(Vector3.Distance(actual, expected) <= .003f, message + ": expected " + expected + ", actual " + actual);
        }

        private static void Near(Quaternion actual, Quaternion expected, string message)
        {
            Require(Quaternion.Angle(actual, expected) <= .05f, message + ": angle error " + Quaternion.Angle(actual, expected));
        }

        private static void Near(float actual, float expected, string message)
        {
            Require(Mathf.Abs(actual - expected) <= .001f, message + ": expected " + expected + ", actual " + actual);
        }

        private sealed class Fixture : IDisposable
        {
            public readonly GameObject Root;
            public readonly Camera Camera;
            public readonly WebDeviceObserverCamera Observer;
            public GameObject Device { get; private set; }
            public bool Active => Get<bool>("overviewTransitionActive");

            public Fixture()
            {
                Root = new GameObject("CameraTransitionValidation-" + Guid.NewGuid().ToString("N"));
                try
                {
                    Camera = Root.AddComponent<Camera>();
                    Camera.enabled = false;
                    Observer = Root.AddComponent<WebDeviceObserverCamera>();
                    Observer.enabled = false;
                    Root.transform.SetPositionAndRotation(StartPosition, StartRotation);
                    Camera.fieldOfView = StartFov;
                    Set("observedCamera", Camera);
                    Set("hasRenderedObserverFrame", true);
                    SetMode("mode", "Device");
                    FieldInfo duration = typeof(WebDeviceObserverCamera).GetField("OverviewTransitionSeconds", BindingFlags.NonPublic | BindingFlags.Static);
                    Require(duration != null, "Missing OverviewTransitionSeconds constant.");
                    Near((float)duration.GetRawConstantValue(), Duration, "Unexpected transition duration");
                }
                catch
                {
                    if (Root) Object.DestroyImmediate(Root);
                    throw;
                }
            }

            public void Begin()
            {
                Invoke("BeginOverviewTransition");
                Require(Active, "The fixture did not start its transition.");
                Set("overviewTransitionStartedAt", 0f);
            }

            public bool Apply(float now) => Apply(TargetPosition, TargetRotation, TargetFov, now);

            public bool Apply(Vector3 position, Quaternion rotation, float fov, float now)
            {
                return (bool)Invoke("ApplyOverviewTransition", position, rotation, fov, now);
            }

            public void AssertInterpolated(Vector3 targetPosition, Quaternion targetRotation, float targetFov, float eased)
            {
                AssertPose(Vector3.Lerp(StartPosition, targetPosition, eased), Quaternion.Slerp(StartRotation, targetRotation, eased), Mathf.Lerp(StartFov, targetFov, eased));
            }

            public void AssertPose(Vector3 position, Quaternion rotation, float fov)
            {
                Near(Root.transform.position, position, "Camera position");
                Near(Root.transform.rotation, rotation, "Camera rotation");
                Near(Camera.fieldOfView, fov, "Camera FOV");
            }

            public string CreateUniqueUav()
            {
                // Avoid selecting an identically named device in an already open user scene.
                Transform[] existing = Object.FindObjectsOfType<Transform>(true);
                int index = 900000001;
                string code;
                bool found;
                do
                {
                    code = "UAV-" + index++;
                    found = false;
                    foreach (Transform item in existing)
                    {
                        if (WebDeviceObserverCamera.NormalizeDeviceCode(item.name) == code)
                        {
                            found = true;
                            break;
                        }
                    }
                } while (found);
                Device = new GameObject(code);
                return code;
            }

            public object Invoke(string method, params object[] args)
            {
                MethodInfo info = typeof(WebDeviceObserverCamera).GetMethod(method, PrivateInstance);
                Require(info != null, "Missing camera method " + method);
                return info.Invoke(Observer, args);
            }

            public T Get<T>(string name) => (T)Field(name).GetValue(Observer);
            public void Set(string name, object value) => Field(name).SetValue(Observer, value);
            public void SetMode(string name, string value) => Set(name, Enum.Parse(Field(name).FieldType, value));

            private static FieldInfo Field(string name)
            {
                FieldInfo field = typeof(WebDeviceObserverCamera).GetField(name, PrivateInstance);
                Require(field != null, "Missing camera field " + name);
                return field;
            }

            public void Dispose()
            {
                if (Device) Object.DestroyImmediate(Device);
                if (Root) Object.DestroyImmediate(Root);
            }
        }
    }
}
#endif
