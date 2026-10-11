#if UNITY_EDITOR
using System;
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
    /// <summary>No saved scenes, no synthetic coast: validates the existing scene contract.</summary>
    public static class VirtualFleetReturnInfrastructureValidation
    {
        private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;
        private const BindingFlags StaticPrivate = BindingFlags.Static | BindingFlags.NonPublic;

        [MenuItem("UAV-USV/Tests/Validate Existing Scene Return")]
        public static void Run()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new InvalidOperationException("Exit Play Mode before validation.");
            Scene previous = SceneManager.GetActiveScene();
            bool owns = !(Application.isBatchMode && previous.IsValid() && previous.isLoaded && string.IsNullOrEmpty(previous.path));
            Scene scene = owns ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive) : previous;
            var root = new GameObject("ExistingSceneReturnValidation");
            int passed = 0;
            try
            {
                SceneManager.SetActiveScene(scene);
                SceneManager.MoveGameObjectToScene(root, scene);
                var infrastructure = root.AddComponent<VirtualFleetReturnInfrastructure>();
                ReturnInfrastructureDefinition definition = Layout();
                Check(VirtualFleetReturnInfrastructure.IsValid(definition), "accepts existing-scene metadata", ref passed);
                Check(infrastructure.Configure(7001, definition) && infrastructure.SlotCount == 6, "loads original three pads and three anchors", ref passed);
                int generation = infrastructure.ConfigurationGeneration;
                for (int i = 0; i < 100; i++) infrastructure.Configure(7001, Clone(definition));
                Check(infrastructure.ConfigurationGeneration == generation, "equivalent pose frames keep metadata idempotent", ref passed);
                Check(root.transform.childCount == 0 && root.GetComponentsInChildren<Renderer>().Length == 0,
                    "metadata creates no geometry, labels, platforms or docks", ref passed);
                var legacy = Clone(definition);
                legacy.version = "fixed-shore-v1";
                infrastructure.Configure(7002, legacy);
                Check(infrastructure.SlotCount == 6 && root.transform.childCount == 0, "legacy metadata also never generates facilities", ref passed);
                infrastructure.Configure(7003, definition);

                Renderer[] originals = ExistingShorelineProbe.BuildOriginalBases(root.transform);
                Transform originalBase = root.transform.Find("island_uav_base");
                for (int index = 0; index < 3; index++)
                {
                    ReturnParkingSlot slot = definition.slots[index];
                    Transform pad = originalBase.Find("pad_0" + (index + 1));
                    Near(pad.position.x, slot.eastM * .18f, "original pad east");
                    Near(pad.position.z, slot.northM * .18f, "original pad north");
                    float top = float.NegativeInfinity;
                    foreach (Renderer renderer in pad.GetComponentsInChildren<Renderer>()) top = Mathf.Max(top, renderer.bounds.max.y);
                    Near(top, slot.upM * .18f, "original pad's highest painted surface");
                    var rotation = (Quaternion)typeof(VirtualFleetScenarioController).GetMethod("ProtocolHeadingToModelRotation", StaticPrivate)
                        .Invoke(null, new object[] { "UAV", slot.headingDeg });
                    Require(Quaternion.Angle(rotation, pad.rotation) < .01f, "UAV final heading must match the original pad axes.");
                }
                Check(true, "original three pad centers, contact heights and orientations match metadata", ref passed);

                Transform uav = SimulationBootstrap.BuildVirtualUav("UAV-contact-validation");
                uav.SetParent(root.transform, true);
                uav.position = new Vector3(0, 12, 0);
                var pose = Pose(definition.slots[0], "RETURNED");
                Vector3 raw = Coordinates.ToPresentation(pose.eastM, pose.northM, pose.upM);
                Vector3 corrected = infrastructure.AdjustGroundContact(pose, uav, raw);
                uav.position = corrected;
                float feet = float.PositiveInfinity;
                foreach (Renderer renderer in uav.GetComponentsInChildren<Renderer>()) feet = Mathf.Min(feet, renderer.bounds.min.y);
                Near(feet, pose.upM * .18f + .002f, "actual aircraft feet on the existing painted pad");
                Near(corrected.x, raw.x, "contact cannot alter east");
                Near(corrected.z, raw.z, "contact cannot alter north");
                Check(!uav.GetComponent<DroneVisual>().spinning, "grounded aircraft stop rotors without moving horizontally", ref passed);
                pose.state = "PARKED";
                Near(infrastructure.AdjustGroundContact(pose, uav, raw).y, corrected.y, "initial parking has the same pad contact as a completed return");
                Check(!uav.GetComponent<DroneVisual>().spinning, "initial parked aircraft keep rotors stopped", ref passed);
                pose.state = "REJOINING";
                Near(infrastructure.AdjustGroundContact(pose, uav, raw).y, corrected.y, "departure has no initial height jump");
                Check(uav.GetComponent<DroneVisual>().spinning, "safe rejoin restores rotors", ref passed);
                pose.upM += 8f;
                Vector3 airborne = Coordinates.ToPresentation(pose.eastM, pose.northM, pose.upM);
                Check(infrastructure.AdjustGroundContact(pose, uav, airborne) == airborne, "cruise pose remains authoritative", ref passed);
                pose.state = "PARKED";
                infrastructure.AdjustGroundContact(pose, uav, airborne);
                Check(uav.GetComponent<DroneVisual>().spinning, "stale parking state cannot stop airborne rotors", ref passed);
                pose = Pose(definition.slots[3], "RETURNED");
                raw = Coordinates.ToPresentation(pose.eastM, pose.northM, pose.upM);
                Check(infrastructure.AdjustGroundContact(pose, uav, raw) == raw && raw.y == 0f, "boats stay on the water without a dock", ref passed);
                var invalid = Clone(definition);
                invalid.slots = new ReturnParkingSlot[0];
                Check(!VirtualFleetReturnInfrastructure.IsValid(invalid), "empty slots are rejected", ref passed);
                invalid = Clone(definition);
                invalid.coordinateFrame = "FLEET_LOCAL_ENU";
                Check(!VirtualFleetReturnInfrastructure.IsValid(invalid), "local coordinates cannot be mistaken for global coordinates", ref passed);
                invalid = Clone(definition);
                invalid.slots[1].deviceCode = invalid.slots[0].deviceCode;
                Check(!VirtualFleetReturnInfrastructure.IsValid(invalid), "duplicate identities are rejected", ref passed);
                invalid = Clone(definition);
                invalid.slots[0].deviceCode = "UAV-000";
                Check(!VirtualFleetReturnInfrastructure.IsValid(invalid), "invalid device ordinals are rejected", ref passed);
                invalid = Clone(definition);
                var fourPads = new List<ReturnParkingSlot>(invalid.slots) { new ReturnParkingSlot { deviceCode = "UAV-004", kind = "HELIPAD" } };
                invalid.slots = fourPads.ToArray();
                Check(!VirtualFleetReturnInfrastructure.IsValid(invalid), "existing scene cannot silently grow a fourth pad", ref passed);

                var configPayload = JsonUtility.FromJson<VirtualFleetConfigPayload>("{\"returnInfrastructure\":" + JsonUtility.ToJson(definition) + "}");
                var mapped = (VirtualFleetConfig)typeof(VirtualFleetPlatformBridge).GetMethod("ToRuntimeConfig", StaticPrivate).Invoke(null, new object[] { configPayload });
                Check(mapped.returnInfrastructure.version == "scene-existing-v1", "loadScenario preserves the existing-scene version", ref passed);
                var batchPayload = JsonUtility.FromJson<VirtualPoseBatchPayload>("{\"returnInfrastructure\":" + JsonUtility.ToJson(definition) + "}");
                var batch = (VirtualPoseBatch)typeof(VirtualFleetPlatformBridge).GetMethod("ToRuntimePoseBatch", StaticPrivate).Invoke(null, new object[] { batchPayload });
                Check(batch.returnInfrastructure.slots[0].eastM == definition.slots[0].eastM, "pose-batch metadata is not translated twice", ref passed);

                var fleet = root.AddComponent<VirtualFleetManager>();
                var controller = root.AddComponent<VirtualFleetScenarioController>();
                controller.Bind(fleet);
                controller.Configure(new VirtualFleetConfig { runId = 7003, returnInfrastructure = definition });
                infrastructure.Configure(7003, definition);
                typeof(VirtualFleetScenarioController).GetField("returnInfrastructure", Private).SetValue(controller, infrastructure);
                controller.ApplyPoseBatch(new VirtualPoseBatch { runId = 7003, sequence = 1 });
                Check(infrastructure.SlotCount == 6, "legacy frame omissions preserve the active metadata", ref passed);
                controller.Configure(new VirtualFleetConfig { runId = 7004 });
                Check(infrastructure.SlotCount == 0, "new scenarios clear old parking metadata", ref passed);

                fleet.Initialize(1, 1, 17, new[] { "TARGET-001" });
                foreach (Transform item in fleet.GetUavTransforms()) item.SetParent(root.transform, true);
                foreach (Transform item in fleet.GetUsvTransforms()) item.SetParent(root.transform, true);
                foreach (Transform item in fleet.GetTargetTransforms()) item.SetParent(root.transform, true);
                fleet.GetUavTransforms()[0].position = new Vector3(0f, 5f, 0f);
                fleet.GetUsvTransforms()[0].position = new Vector3(5f, 0f, 0f);
                fleet.GetTargetTransforms()[0].position = new Vector3(1000f, 0f, 1000f);
                infrastructure.Configure(7004, definition);
                var cameraRoot = new GameObject("FleetOnlyCameraValidation");
                cameraRoot.transform.SetParent(root.transform, false);
                Camera camera = cameraRoot.AddComponent<Camera>();
                camera.enabled = false;
                camera.aspect = 16f / 9f;
                var observer = cameraRoot.AddComponent<WebDeviceObserverCamera>();
                observer.enabled = false;
                typeof(WebDeviceObserverCamera).GetField("observedCamera", Private).SetValue(observer, camera);
                typeof(WebDeviceObserverCamera).GetField("fleetManager", Private).SetValue(observer, fleet);
                typeof(WebDeviceObserverCamera).GetMethod("RefreshSceneTargets", Private).Invoke(observer, new object[] { true });
                object[] outputs = { Vector3.zero, Vector3.zero };
                typeof(WebDeviceObserverCamera).GetMethod("CalculateOverview", Private).Invoke(observer, outputs);
                Vector3 cameraPosition = (Vector3)outputs[0], focus = (Vector3)outputs[1];
                cameraRoot.transform.SetPositionAndRotation(cameraPosition, Quaternion.LookRotation(focus - cameraPosition));
                camera.fieldOfView = 52f;
                var report = observer.GetFramingReport();
                Check(report.deviceCount == 2 && report.outsideCount == 0 && focus.magnitude < 15f,
                    "overview fits UAV/USV only, ignoring far targets and static bases", ref passed);
                fleet.TryApplyPoseImmediate("UAV-001", fleet.GetUavTransforms()[0].position, Quaternion.identity, "RETURNED");
                typeof(WebDeviceObserverCamera).GetMethod("RefreshSceneTargets", Private).Invoke(observer, new object[] { true });
                Check(observer.GetFramingReport().deviceCount == 2, "returned aircraft remain part of the fleet overview", ref passed);

                ValidateApproaches(root.transform, definition, originals);
                Check(true, "natural-shore boat approaches avoid land and original base foundations", ref passed);
                Check(true, "UAV approach height clears terrain and original structures along the operating area routes", ref passed);
                Debug.Log("[VirtualFleetReturnInfrastructureValidation] PASSED " + passed + " existing-scene checks.");
            }
            finally
            {
                Object.DestroyImmediate(root);
                if (previous.IsValid() && previous.isLoaded) SceneManager.SetActiveScene(previous);
                if (owns && scene.IsValid()) EditorSceneManager.CloseScene(scene, true);
            }
        }

        private static void ValidateApproaches(Transform owner, ReturnInfrastructureDefinition layout, Renderer[] originals)
        {
            GameObject terrain = CatalinaIslandRuntime.CreateVisualTerrain(Coordinates.ToPresentationEnvironment(0f, 0f, -.8f), .18f);
            Require(terrain, "Catalina terrain resource is missing.");
            terrain.transform.SetParent(owner, true);
            foreach (MeshFilter filter in terrain.GetComponentsInChildren<MeshFilter>(true))
            {
                if (!filter.sharedMesh) continue;
                MeshCollider collider = filter.GetComponent<MeshCollider>();
                if (!collider) collider = filter.gameObject.AddComponent<MeshCollider>();
                collider.sharedMesh = filter.sharedMesh;
            }
            Physics.SyncTransforms();
            Collider[] colliders = terrain.GetComponentsInChildren<Collider>();
            foreach (ReturnParkingSlot slot in layout.slots)
            {
                var target = new Vector2(slot.eastM, slot.northM);
                if (slot.kind == "BERTH")
                {
                    var approach = new Vector2(slot.approachEastM, slot.approachNorthM);
                    Require(ExistingShorelineProbe.SegmentClear(colliders, approach, target, 6f)
                        && ExistingShorelineProbe.StaticSegmentClear(originals, approach, target, 6f), "Blocked natural-shore route: " + slot.deviceCode);
                }
                else
                    foreach (float east in new[] { -510f, -360f, -210f })
                        foreach (float north in new[] { -435f, -285f, -135f })
                        {
                            float high = ExistingShorelineProbe.MaximumRouteHeight(colliders, originals, new Vector2(east, north), target, 5f);
                            Require(slot.approachUpM >= high + 8f, "Insufficient UAV transit clearance for " + slot.deviceCode + ": obstacle=" + high);
                        }
            }
        }

        private static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException("[ExistingSceneReturn] " + message); }
        private static void Check(bool condition, string message, ref int passed) { Require(condition, message); passed++; }
        private static void Near(float a, float b, string message) { Require(Mathf.Abs(a - b) < .003f, message + ": expected=" + b + ", actual=" + a); }
        private static ReturnInfrastructureDefinition Clone(ReturnInfrastructureDefinition value) => JsonUtility.FromJson<ReturnInfrastructureDefinition>(JsonUtility.ToJson(value));
        private static VirtualPose Pose(ReturnParkingSlot slot, string state) => new VirtualPose { deviceCode = slot.deviceCode, eastM = slot.eastM, northM = slot.northM, upM = slot.upM, headingDeg = slot.headingDeg, state = state };
        private static ReturnInfrastructureDefinition Layout()
        {
            var slots = new List<ReturnParkingSlot>();
            for (int i = 0; i < 3; i++)
            {
                float x = -14f + i * 14f;
                float east = -75f + x * Mathf.Cos(.559f), north = -215f + x * Mathf.Sin(.559f);
                slots.Add(new ReturnParkingSlot { deviceCode = "UAV-" + (i + 1).ToString("000"), kind = "HELIPAD", eastM = east, northM = north, upM = 19.65f,
                    headingDeg = 57.971659252187f, approachEastM = east, approachNorthM = north, approachUpM = 39.65f });
            }
            // Final surveyed west-shore anchors are filled from the Editor probe.
            float[] easts = { -92f, -108f, -108f }, norths = { -180f, -160f, -140f };
            for (int i = 0; i < 3; i++) slots.Add(new ReturnParkingSlot { deviceCode = "USV-" + (i + 1).ToString("000"), kind = "BERTH", eastM = easts[i], northM = norths[i],
                upM = 0f, headingDeg = 90f, approachEastM = -240f, approachNorthM = norths[i] - 30f, approachUpM = 0f });
            return new ReturnInfrastructureDefinition { version = "scene-existing-v1", coordinateFrame = "GLOBAL_ENU", slots = slots.ToArray() };
        }
    }
}
#endif
