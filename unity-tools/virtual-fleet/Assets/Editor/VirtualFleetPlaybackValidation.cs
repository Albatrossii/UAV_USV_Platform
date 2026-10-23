#if UNITY_EDITOR
using System;
using System.Collections;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using UavUsv.PlatformTools;

namespace UavUsv.Editor.Tools
{
    /// <summary>Deterministic presentation tests; no scene files are saved.</summary>
    public static class VirtualFleetPlaybackValidation
    {
        private static int passed;
        private static readonly Quaternion Identity = Quaternion.identity;

        [MenuItem("UAV-USV/Tests/Validate Timestamped Fleet Playback")]
        public static void Run()
        {
            passed = 0;
            Check("fixed-delay interpolation at constant velocity", () => {
                var p = LinearStream();
                p.Evaluate(.25, out Vector3 position, out _);
                Near(position.x, .7f);
            });
            Check("packet arrival cannot move the model before evaluation", () => {
                var p = LinearStream();
                p.Evaluate(.25, out Vector3 before, out _);
                p.Add(1300, new Vector3(3, 0, 0), Identity, .3, before, Identity);
                p.Evaluate(.3, out Vector3 after, out _);
                Near(after.x, 1.2f);
            });
            Check("long interruption stops at last authoritative pose", () => {
                var p = LinearStream();
                p.Evaluate(30, out Vector3 position, out _);
                Near(position.x, 2f);
                Require(p.IsStale(30), "Missing stale status");
            });
            Check("recovery begins at frozen position without a jump", () => {
                var p = LinearStream();
                p.Evaluate(3, out Vector3 frozen, out _);
                p.Add(4000, new Vector3(20, 0, 0), Identity, 3, frozen, Identity);
                p.Evaluate(3, out Vector3 recovered, out _);
                Near(recovered.x, frozen.x);
                Require(p.IsRecovering(3), "Missing recovery status");
                p.Evaluate(3.5, out recovered, out _);
                Near(recovered.x, 20);
                Require(!p.IsRecovering(3.5), "Recovery never completed");
            });
            Check("recovery stays on segment without overshoot", () => {
                var p = LinearStream();
                p.Evaluate(3, out Vector3 frozen, out _);
                p.Add(4000, new Vector3(20, 0, 0), Identity, 3, frozen, Identity);
                float previous = frozen.x;
                for (int i = 0; i <= 60; i++) {
                    p.Evaluate(3 + i * .01, out Vector3 pos, out _);
                    Require(pos.x >= previous - .0001f && pos.x <= 20.0001f,
                        "Non-monotonic or overshooting recovery");
                    Near(pos.y, 0); Near(pos.z, 0); previous = pos.x;
                }
            });
            Check("old source samples are rejected", () => {
                var p = LinearStream();
                Require(!p.Add(1199, new Vector3(99, 0, 0), Identity, .3,
                    Vector3.zero, Identity), "Accepted source rewind");
                p.Evaluate(1, out Vector3 pos, out _); Near(pos.x, 2);
            });
            Check("arrival jitter does not rewind rendered source", () => {
                var p = LinearStream();
                p.Evaluate(.38, out Vector3 before, out _);
                p.Add(1300, new Vector3(3, 0, 0), Identity, .4, before, Identity);
                p.Evaluate(.4, out Vector3 after, out _);
                Require(after.x >= before.x, "Jitter rewound the vehicle");
            });
            Check("explicit reset discards prior trajectory", () => {
                var p = LinearStream(); p.Clear();
                Require(!p.Evaluate(1, out _, out _), "Reset retained samples");
                var spawned = new Vector3(100, 20, 5);
                p.Add(100, spawned, Identity, 2, spawned, Identity);
                p.Evaluate(2, out Vector3 position, out _);
                Require(Vector3.Distance(position, spawned) < .001f, "Reset interpolated across runs");
            });
            Check("clockless compatibility updates within one frame remain accepted", () => {
                var p = new VirtualFleetPosePlayback();
                Require(p.Add(0, Vector3.zero, Identity, 0, Vector3.zero, Identity), "First rejected");
                Require(p.Add(0, Vector3.one, Identity, 0, Vector3.zero, Identity), "Second rejected");
            });
            Check("overview includes current mission vessels but no shore structure", ValidateMissionFraming);
            Debug.Log("[VirtualFleetPlaybackValidation] PASS " + passed + "/10 cases");
        }

        public static void BuildValidated()
        {
            Run();
            WebCameraTransitionValidation.Run();
            VirtualFleetReturnInfrastructureValidation.Run();
            VueWebGlBuildTool.BuildVirtualFleet();
        }

        private static VirtualFleetPosePlayback LinearStream()
        {
            var p = new VirtualFleetPosePlayback();
            for (int i = 0; i <= 2; i++)
                p.Add(1000 + i * 100, new Vector3(i, 0, 0), Identity,
                    i * .1, Vector3.zero, Identity);
            return p;
        }

        private static void ValidateMissionFraming()
        {
            var root = new GameObject("PlaybackFramingValidation");
            var ship = new GameObject("PROTECTED-999");
            var pad = new GameObject("island_uav_base-validation");
            try {
                var fleet = root.AddComponent<VirtualFleetManager>();
                var camera = root.AddComponent<Camera>(); camera.enabled = false;
                var observer = root.AddComponent<WebDeviceObserverCamera>(); observer.enabled = false;
                const BindingFlags flags = BindingFlags.NonPublic | BindingFlags.Instance;
                var state = new VirtualFleetDeviceState { deviceCode = "PROTECTED-999", deviceType = "TARGET" };
                typeof(VirtualFleetDeviceState).GetField("transform", flags).SetValue(state, ship.transform);
                ((IList)typeof(VirtualFleetManager).GetField("targets", flags).GetValue(fleet)).Add(state);
                typeof(WebDeviceObserverCamera).GetField("fleetManager", flags).SetValue(observer, fleet);
                typeof(WebDeviceObserverCamera).GetMethod("RefreshSceneTargets", flags).Invoke(observer, new object[] { true });
                var subjects = (IList)typeof(WebDeviceObserverCamera).GetField("sceneTargets", flags).GetValue(observer);
                Require(subjects.Contains(ship.transform), "Protected ship omitted from framing");
                Require(!subjects.Contains(pad.transform), "Shore structure incorrectly framed");
            }
            finally {
                UnityEngine.Object.DestroyImmediate(root);
                UnityEngine.Object.DestroyImmediate(ship);
                UnityEngine.Object.DestroyImmediate(pad);
            }
        }

        private static void Check(string name, Action check)
        {
            check(); passed++; Debug.Log("[VirtualFleetPlaybackValidation] PASS " + name);
        }
        private static void Require(bool condition, string message)
        { if (!condition) throw new InvalidOperationException(message); }
        private static void Near(float actual, float expected)
        { Require(Mathf.Abs(actual - expected) < .002f, "Expected " + expected + ", actual " + actual); }
    }
}
#endif
