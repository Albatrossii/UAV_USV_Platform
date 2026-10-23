using System;
using UnityEngine;
using UavUsv.PlatformTools;

namespace UavUsv
{
    /// <summary>
    /// Small integration facade for PlatformBridge and future front-end code.
    /// Transport code should call this facade instead of touching transforms.
    /// </summary>
    public sealed class VirtualFleetScenarioController :
        MonoBehaviour,
        IVirtualFleetRuntime
    {
        [SerializeField] private VirtualFleetManager fleetManager;
        private VirtualFleetSceneSafety initializationSafety;
        private SimulationBootstrap simulationBootstrap;
        private VirtualFleetReturnInfrastructure returnInfrastructure;

        public VirtualFleetManager FleetManager => fleetManager;
        public bool CanModifyFleet => fleetManager && fleetManager.CanModifyFleet;
        public VirtualFleetConfig CurrentConfig { get; private set; }
        public long CurrentRunId => CurrentConfig != null ? CurrentConfig.runId : 0;
        public long LastAppliedSequence { get; private set; } = -1;

        private void Awake()
        {
            if (!fleetManager)
                fleetManager = GetComponent<VirtualFleetManager>();
            simulationBootstrap = GetComponent<SimulationBootstrap>();
        }

        public void Bind(VirtualFleetManager manager)
        {
            fleetManager = manager;
        }

        public void Configure(VirtualFleetConfig config)
        {
            if (config == null)
                throw new ArgumentNullException(nameof(config));
            EnsureRuntime();
            if (!fleetManager.CanModifyFleet)
                throw new InvalidOperationException("scenario_locked");
            ValidateConfig(config);
            if (returnInfrastructure) returnInfrastructure.Clear();
            CurrentConfig = CloneConfig(config);
            if (simulationBootstrap)
            {
                simulationBootstrap.SetVirtualSimulationEnvironment(true);
                simulationBootstrap.SetVirtualScenarioReady(false);
            }
            LastAppliedSequence = -1;
        }

        public void Regenerate()
        {
            EnsureRuntime();
            if (!fleetManager.CanModifyFleet)
                throw new InvalidOperationException("scenario_locked");
            if (CurrentConfig == null)
                CurrentConfig = new VirtualFleetConfig();

            if (simulationBootstrap)
                simulationBootstrap.SetVirtualSimulationEnvironment(true);

            fleetManager.Initialize(
                CurrentConfig.uavCount,
                CurrentConfig.usvCount,
                CurrentConfig.seed,
                TargetCodes(CurrentConfig.initialPoses, CurrentConfig.targetCount)
            );
            UpdateReturnInfrastructure(CurrentConfig.returnInfrastructure);
            ApplyInitialScenarioPoses();
            ValidateInitialScenarioPoses();
            if (simulationBootstrap)
                simulationBootstrap.SetVirtualScenarioReady(true);
            LastAppliedSequence = -1;
        }

        public VirtualPoseBatchApplyResult ApplyPoseBatch(VirtualPoseBatch batch)
        {
            EnsureRuntime();
            VirtualPoseBatchApplyResult result = new VirtualPoseBatchApplyResult
            {
                success = false,
                runId = batch != null ? batch.runId : 0,
                sequence = batch != null ? batch.sequence : 0,
                missingDeviceCodes = new string[0],
                unknownDeviceCodes = new string[0]
            };

            if (batch == null)
                return Failure(result, "invalid_payload", "位姿批次为空");
            if (!string.Equals(batch.runtimeMode, "VIRTUAL_SIMULATION", StringComparison.OrdinalIgnoreCase))
                return Failure(result, "invalid_runtime_mode", "仅支持 VIRTUAL_SIMULATION");
            if (CurrentConfig == null || CurrentConfig.runId <= 0)
                return Failure(result, "run_not_loaded", "尚未加载场景");
            if (batch.runId != CurrentConfig.runId)
                return Failure(result, "run_mismatch", "runId 与当前场景不一致");
            if (batch.sequence <= LastAppliedSequence)
                return Failure(result, "sequence_rewind", "sequence 未严格递增");

            // Older cached frames may omit this optional extension. Preserve
            // the metadata loaded for this run; Configure clears it on reload.
            if (batch.returnInfrastructure != null)
                UpdateReturnInfrastructure(batch.returnInfrastructure);

            var missing = new System.Collections.Generic.List<string>();
            var unknown = new System.Collections.Generic.List<string>();
            int applied = 0;
            VirtualPose[] poses = batch.vehicles ?? new VirtualPose[0];
            var seen = new System.Collections.Generic.HashSet<string>(
                StringComparer.OrdinalIgnoreCase
            );
            for (int i = 0; i < poses.Length; i++)
            {
                applied += ApplyPose(
                    poses[i],
                    false,
                    batch.sampleTime,
                    seen,
                    unknown
                );
            }

            VirtualPose[] targetPoses = batch.targets ?? new VirtualPose[0];
            for (int i = 0; i < targetPoses.Length; i++)
            {
                applied += ApplyPose(
                    targetPoses[i],
                    true,
                    batch.sampleTime,
                    seen,
                    unknown
                );
            }

            AddMissingCodes(missing, fleetManager.Uavs, seen);
            AddMissingCodes(missing, fleetManager.Usvs, seen);
            AddMissingCodes(missing, fleetManager.Targets, seen);
            LastAppliedSequence = batch.sequence;
            result.success = true;
            result.code = "ok";
            result.message = "位姿批次已应用";
            result.appliedCount = applied;
            result.missingDeviceCodes = missing.ToArray();
            result.unknownDeviceCodes = unknown.ToArray();
            return result;
        }

        public bool AddUav()
        {
            return fleetManager && fleetManager.AddUav();
        }

        public bool AddUsv()
        {
            return fleetManager && fleetManager.AddUsv();
        }

        public bool RemoveUav(string deviceCode)
        {
            return fleetManager && fleetManager.RemoveUav(deviceCode);
        }

        public bool RemoveUsv(string deviceCode)
        {
            return fleetManager && fleetManager.RemoveUsv(deviceCode);
        }

        public bool TryApplyTargetPose(
            string deviceCode,
            Vector3 position,
            Quaternion rotation,
            string status)
        {
            return fleetManager &&
                fleetManager.TryApplyTargetPose(deviceCode, position, rotation, status);
        }

        public void StartMission()
        {
            if (fleetManager) fleetManager.StartMission();
        }

        public void PauseMission()
        {
            if (fleetManager) fleetManager.PauseMission();
        }

        public void ResumeMission()
        {
            if (fleetManager) fleetManager.ResumeMission();
        }

        public void StopMission()
        {
            if (fleetManager) fleetManager.StopMission();
        }

        public void ResetMission()
        {
            if (fleetManager) fleetManager.ResetMission();
        }

        public VirtualFleetSnapshot GetSnapshot()
        {
            return fleetManager ? fleetManager.GetSnapshot() : new VirtualFleetSnapshot();
        }

        private void EnsureRuntime()
        {
            if (!fleetManager)
                fleetManager = GetComponent<VirtualFleetManager>();
            if (!fleetManager)
                throw new InvalidOperationException("VirtualFleetManager 未绑定");
        }

        private void UpdateReturnInfrastructure(ReturnInfrastructureDefinition definition)
        {
            if (!returnInfrastructure)
                returnInfrastructure = GetComponent<VirtualFleetReturnInfrastructure>();
            if (!returnInfrastructure && definition != null)
                returnInfrastructure = gameObject.AddComponent<VirtualFleetReturnInfrastructure>();
            if (returnInfrastructure)
                returnInfrastructure.Configure(CurrentRunId, definition);
        }

        private void ValidateInitialScenarioPoses()
        {
            if (!initializationSafety)
                initializationSafety = GetComponent<VirtualFleetSceneSafety>();
            if (!initializationSafety)
                initializationSafety = gameObject.AddComponent<VirtualFleetSceneSafety>();

            initializationSafety.Bind(fleetManager);
            // Validation is intentionally one-shot. Continuous transform
            // rollback fights the streamed algorithm poses and causes the
            // visible jumping reported in WebGL.
            initializationSafety.enabled = false;
            int corrected = initializationSafety.ValidateCurrentFleet();
            if (corrected > 0)
                Debug.LogWarning(
                    "[VirtualFleet] Corrected unsafe initial poses: " + corrected
                );
        }

        private static void ValidateConfig(VirtualFleetConfig config)
        {
            if (!string.Equals(config.runtimeMode, "VIRTUAL_SIMULATION", StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("invalid_runtime_mode");
            if (config.runId <= 0)
                throw new ArgumentException("invalid_run_id");
            if (!VirtualFleetAlgorithms.IsSupported(config.algorithmCode))
                throw new ArgumentException("invalid_algorithm");
            if (config.uavCount < 1 || config.uavCount > VirtualFleetManager.MaximumUavCount ||
                config.usvCount < 1 || config.usvCount > VirtualFleetManager.MaximumUsvCount)
                throw new ArgumentException("invalid_count");
            if (config.targetCount < 1 ||
                config.targetCount > VirtualFleetManager.MaximumTargetCount)
                throw new ArgumentException("invalid_target_count");
            if (config.initialSpeedMps < 0f)
                throw new ArgumentException("invalid_speed");
        }

        private static VirtualFleetConfig CloneConfig(VirtualFleetConfig config)
        {
            return new VirtualFleetConfig
            {
                runtimeMode = "VIRTUAL_SIMULATION",
                algorithmCode = config.algorithmCode,
                runId = config.runId,
                uavCount = config.uavCount,
                usvCount = config.usvCount,
                targetCount = config.targetCount,
                formationType = config.formationType,
                initialSpeedMps = config.initialSpeedMps,
                initialHeadingDeg = NormalizeHeading(config.initialHeadingDeg),
                seed = config.seed,
                initialPosesCoordinateFrame = config.initialPosesCoordinateFrame,
                initialPoses = config.initialPoses,
                returnInfrastructure = config.returnInfrastructure
            };
        }

        private static string[] TargetCodes(VirtualPose[] poses, int targetCount)
        {
            var codes = new System.Collections.Generic.List<string>();
            var seen = new System.Collections.Generic.HashSet<string>(
                StringComparer.OrdinalIgnoreCase
            );
            if (poses != null)
                for (int i = 0; i < poses.Length; i++)
                {
                    VirtualPose pose = poses[i];
                    if (pose == null || string.IsNullOrWhiteSpace(pose.deviceCode))
                        continue;
                    string type = (pose.deviceType ?? string.Empty).Trim().ToUpperInvariant();
                    if (!type.Contains("TARGET"))
                        continue;
                    string code = pose.deviceCode.Trim().ToUpperInvariant().Replace('_', '-');
                    if (seen.Add(code))
                        codes.Add(code);
                }
            for (int i = codes.Count; i < targetCount; i++)
                codes.Add("TARGET-" + (i + 1).ToString("000"));
            if (codes.Count > targetCount)
                codes.RemoveRange(targetCount, codes.Count - targetCount);
            return codes.ToArray();
        }

        private void ApplyInitialScenarioPoses()
        {
            if (CurrentConfig == null
                || CurrentConfig.initialPoses == null
                || CurrentConfig.initialPoses.Length == 0)
                return;

            if (!string.Equals(
                    CurrentConfig.initialPosesCoordinateFrame,
                    VirtualFleetProtocol.GlobalCoordinateFrame,
                    StringComparison.OrdinalIgnoreCase))
            {
                Debug.LogWarning(
                    "[VirtualFleet] Ignoring initialPoses because coordinateFrame=" +
                    CurrentConfig.initialPosesCoordinateFrame +
                    " is not GLOBAL_ENU"
                );
                return;
            }

            int applied = 0;
            for (int i = 0; i < CurrentConfig.initialPoses.Length; i++)
            {
                VirtualPose pose = CurrentConfig.initialPoses[i];
                if (pose == null
                    || !pose.valid
                    || string.IsNullOrWhiteSpace(pose.deviceCode))
                    continue;

                Vector3 position = Coordinates.ToPresentation(
                    pose.eastM,
                    pose.northM,
                    pose.upM
                );
                VirtualFleetDeviceState known = FindState(pose.deviceCode);
                Quaternion rotation = ProtocolHeadingToModelRotation(
                    known == null ? string.Empty : known.deviceType,
                    pose.headingDeg
                );
                if (returnInfrastructure && known != null)
                    position = returnInfrastructure.AdjustGroundContact(pose, known.transform, position);
                bool updated = fleetManager.TryApplyPoseImmediate(
                    pose.deviceCode,
                    position,
                    rotation,
                    pose.state
                );
                if (updated)
                    applied++;
            }

            Debug.Log(
                "[VirtualFleet] Applied initial scenario poses: " +
                applied + "/" + CurrentConfig.initialPoses.Length
            );
        }

        private VirtualFleetDeviceState FindState(string deviceCode)
        {
            for (int i = 0; i < fleetManager.Uavs.Count; i++)
                if (string.Equals(fleetManager.Uavs[i].deviceCode, deviceCode, StringComparison.OrdinalIgnoreCase))
                    return fleetManager.Uavs[i];
            for (int i = 0; i < fleetManager.Usvs.Count; i++)
                if (string.Equals(fleetManager.Usvs[i].deviceCode, deviceCode, StringComparison.OrdinalIgnoreCase))
                    return fleetManager.Usvs[i];
            for (int i = 0; i < fleetManager.Targets.Count; i++)
                if (string.Equals(fleetManager.Targets[i].deviceCode, deviceCode, StringComparison.OrdinalIgnoreCase))
                    return fleetManager.Targets[i];
            return null;
        }

        private int ApplyPose(
            VirtualPose pose,
            bool target,
            long sampleTime,
            System.Collections.Generic.HashSet<string> seen,
            System.Collections.Generic.List<string> unknown)
        {
            if (pose == null || !pose.valid || string.IsNullOrWhiteSpace(pose.deviceCode))
                return 0;

            string code = pose.deviceCode.Trim();
            if (!seen.Add(code))
                return 0;

            VirtualFleetDeviceState known = FindState(code);
            if (known == null)
            {
                unknown.Add(code);
                return 0;
            }

            Vector3 position = Coordinates.ToPresentation(
                pose.eastM,
                pose.northM,
                pose.upM
            );
            Quaternion rotation = ProtocolHeadingToModelRotation(
                known.deviceType,
                pose.headingDeg
            );
            if (returnInfrastructure && !target)
                position = returnInfrastructure.AdjustGroundContact(pose, known.transform, position);
            bool applied = fleetManager.TryApplyPose(code, position, rotation, pose.state, sampleTime);
            return applied ? 1 : 0;
        }

        private static void AddMissingCodes(
            System.Collections.Generic.List<string> missing,
            System.Collections.Generic.IReadOnlyList<VirtualFleetDeviceState> devices,
            System.Collections.Generic.HashSet<string> seen)
        {
            for (int i = 0; i < devices.Count; i++)
                if (devices[i] != null && !string.IsNullOrEmpty(devices[i].deviceCode))
                {
                    if (!seen.Contains(devices[i].deviceCode))
                        missing.Add(devices[i].deviceCode);
                }
        }

        private static float NormalizeHeading(float heading)
        {
            float normalized = heading % 360f;
            return normalized < 0f ? normalized + 360f : normalized;
        }

        /// <summary>
        /// Converts the protocol heading (0=east, 90=north) to the local
        /// forward axis used by each rendered model. Surface-vessel meshes
        /// use local +X (Transform.right) as their bow; UAV meshes retain the
        /// legacy local +Z mapping.
        /// </summary>
        internal static Quaternion ProtocolHeadingToModelRotation(
            string deviceType,
            float headingDeg)
        {
            float heading = NormalizeHeading(headingDeg);
            bool bowUsesLocalRight =
                string.Equals(deviceType, "USV", StringComparison.OrdinalIgnoreCase) ||
                string.Equals(deviceType, "TARGET", StringComparison.OrdinalIgnoreCase);
            float unityYaw = bowUsesLocalRight ? -heading : heading - 90f;
            return Quaternion.Euler(0f, unityYaw, 0f);
        }

        private static VirtualPoseBatchApplyResult Failure(
            VirtualPoseBatchApplyResult result,
            string code,
            string message)
        {
            result.code = code;
            result.message = message;
            return result;
        }
    }
}
