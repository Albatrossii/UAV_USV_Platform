using System;
using System.Collections.Generic;
using UnityEngine;

namespace UavUsv
{
    /// <summary>
    /// Owns virtual UAV/USV instances and exposes the stable A/B boundary.
    /// It deliberately contains no ROS or WebGL transport code.
    /// </summary>
    public sealed class VirtualFleetManager : MonoBehaviour
    {
        public const int DefaultUavCount = 3;
        public const int DefaultUsvCount = 3;
        public const int MaximumUavCount = 128;
        public const int MaximumUsvCount = 128;
        public const int MaximumTargetCount = 12;
        public const int DefaultRandomSeed = 20260814;

        private readonly List<VirtualFleetDeviceState> uavs = new List<VirtualFleetDeviceState>();
        private readonly List<VirtualFleetDeviceState> usvs = new List<VirtualFleetDeviceState>();
        private readonly List<VirtualFleetDeviceState> targets = new List<VirtualFleetDeviceState>();
        private int nextUavNumber = 1;
        private int nextUsvNumber = 1;
        private int currentRandomSeed = DefaultRandomSeed;
        private VirtualVehicleFactory factory;
        private Transform externalProtectedTargetTransform;
        private Transform externalThreatTargetTransform;
        private VirtualFleetMissionState missionState = VirtualFleetMissionState.Stopped;
        private readonly Dictionary<string, VirtualFleetPosePlayback> posePlayback =
            new Dictionary<string, VirtualFleetPosePlayback>(StringComparer.OrdinalIgnoreCase);

        public VirtualFleetMissionState MissionState => missionState;
        public int UavCount => uavs.Count;
        public int UsvCount => usvs.Count;
        public int TargetCount => targets.Count;
        public int CurrentRandomSeed => currentRandomSeed;
        public IReadOnlyList<VirtualFleetDeviceState> Uavs => uavs;
        public IReadOnlyList<VirtualFleetDeviceState> Usvs => usvs;
        public IReadOnlyList<VirtualFleetDeviceState> Targets => targets;
        public bool CanModifyFleet =>
            missionState == VirtualFleetMissionState.Stopped ||
            missionState == VirtualFleetMissionState.Reset;

        public event Action FleetChanged;
        public event Action<VirtualFleetMissionState> MissionStateChanged;

        public void SetFleetVisible(bool visible)
        {
            SetVisible(uavs, visible);
            SetVisible(usvs, visible);
            SetVisible(targets, visible);
        }

        private void LateUpdate()
        {
            float positionT = 1f - Mathf.Exp(-11f * Time.unscaledDeltaTime);
            SmoothDevices(uavs, positionT, Time.unscaledDeltaTime);
            SmoothDevices(usvs, positionT, Time.unscaledDeltaTime);
            SmoothDevices(targets, positionT, Time.unscaledDeltaTime);
        }

        public void ConfigureSpawnPoints(
            Transform[] uavPads,
            Vector3[] usvPositions,
            float[] usvYaws)
        {
            factory = new VirtualVehicleFactory(uavPads, usvPositions, usvYaws);
        }

        public void ConfigureTargetTransform(Transform targetTransform)
        {
            ConfigureTargetTransforms(null, targetTransform);
        }

        public void ConfigureTargetTransforms(
            Transform protectedTargetTransform,
            Transform threatTargetTransform)
        {
            externalProtectedTargetTransform = protectedTargetTransform;
            externalThreatTargetTransform = threatTargetTransform;
            if (factory == null)
                factory = new VirtualVehicleFactory(null, null, null);
            factory.SetTargetTransforms(
                protectedTargetTransform,
                threatTargetTransform
            );
        }

        public void Initialize(
            int uavCount = DefaultUavCount,
            int usvCount = DefaultUsvCount,
            int seed = DefaultRandomSeed,
            string[] targetCodes = null)
        {
            if (!CanModifyFleet)
                return;

            ClearFleet();
            if (factory == null)
                factory = new VirtualVehicleFactory(null, null, null);
            currentRandomSeed = seed == 0 ? DefaultRandomSeed : seed;
            factory.ResetRandom(currentRandomSeed);
            nextUavNumber = 1;
            nextUsvNumber = 1;
            for (int i = 0; i < Mathf.Clamp(uavCount, 1, MaximumUavCount); i++)
                AddUavInternal();
            for (int i = 0; i < Mathf.Clamp(usvCount, 1, MaximumUsvCount); i++)
                AddUsvInternal();
            string[] requestedTargets = targetCodes == null || targetCodes.Length == 0
                ? new[] { "TARGET-001" }
                : targetCodes;
            int targetLimit = Mathf.Clamp(requestedTargets.Length, 1, MaximumTargetCount);
            for (int i = 0; i < targetLimit; i++)
                AddTargetInternal(requestedTargets[i], i);
            NotifyFleetChanged();
        }

        public bool TryApplyPose(
            string deviceCode,
            Vector3 position,
            Quaternion rotation,
            string status,
            long sampleTime = 0)
        {
            VirtualFleetDeviceState state = FindDevice(deviceCode);
            if (state == null || !state.transform)
                return false;

            if (!posePlayback.TryGetValue(state.deviceCode, out VirtualFleetPosePlayback playback))
            {
                playback = new VirtualFleetPosePlayback();
                posePlayback[state.deviceCode] = playback;
            }
            double now = Time.realtimeSinceStartupAsDouble;
            if (!playback.Add(sampleTime, position, rotation, now,
                    state.transform.position, state.transform.rotation))
                return false;
            state.desiredPosition = position;
            state.desiredRotation = rotation;
            state.hasDesiredPose = true;
            if (!string.IsNullOrWhiteSpace(status))
                state.status = status.Trim().ToUpperInvariant();
            return true;
        }

        public bool TryApplyPoseImmediate(
            string deviceCode,
            Vector3 position,
            Quaternion rotation,
            string status)
        {
            VirtualFleetDeviceState state = FindDevice(deviceCode);
            if (state == null || !state.transform)
                return false;
            state.transform.SetPositionAndRotation(position, rotation);
            posePlayback.Remove(state.deviceCode);
            state.position = state.desiredPosition = position;
            state.rotation = state.desiredRotation = rotation;
            state.hasDesiredPose = true;
            if (!string.IsNullOrWhiteSpace(status))
                state.status = status.Trim().ToUpperInvariant();
            return true;
        }

        public bool TryApplyTargetPose(
            string deviceCode,
            Vector3 position,
            Quaternion rotation,
            string status)
        {
            if (string.IsNullOrWhiteSpace(deviceCode))
                return false;
            return TryApplyPose(deviceCode, position, rotation, status);
        }

        public Transform[] GetUavTransforms()
        {
            return ToTransformArray(uavs);
        }

        public Transform[] GetUsvTransforms()
        {
            return ToTransformArray(usvs);
        }

        public Transform[] GetTargetTransforms()
        {
            return ToTransformArray(targets);
        }

        public bool AddUav()
        {
            if (!CanModifyFleet || uavs.Count >= MaximumUavCount)
                return false;
            AddUavInternal();
            NotifyFleetChanged();
            return true;
        }

        public bool AddUsv()
        {
            if (!CanModifyFleet || usvs.Count >= MaximumUsvCount)
                return false;
            AddUsvInternal();
            NotifyFleetChanged();
            return true;
        }

        public bool RemoveUav(string deviceCode)
        {
            return Remove(uavs, deviceCode);
        }

        public bool RemoveUsv(string deviceCode)
        {
            return Remove(usvs, deviceCode);
        }

        public void StartMission()
        {
            SetMissionState(VirtualFleetMissionState.Running);
        }

        public void PauseMission()
        {
            if (missionState == VirtualFleetMissionState.Running)
                SetMissionState(VirtualFleetMissionState.Paused);
        }

        public void ResumeMission()
        {
            if (missionState == VirtualFleetMissionState.Paused)
                SetMissionState(VirtualFleetMissionState.Running);
        }

        public void StopMission()
        {
            SetMissionState(VirtualFleetMissionState.Stopped);
        }

        public void ResetMission()
        {
            SetMissionState(VirtualFleetMissionState.Reset);
            string[] targetCodes = new string[targets.Count];
            for (int i = 0; i < targets.Count; i++)
                targetCodes[i] = targets[i].deviceCode;
            Initialize(
                Mathf.Max(1, uavs.Count),
                Mathf.Max(1, usvs.Count),
                currentRandomSeed,
                targetCodes
            );
            SetMissionState(VirtualFleetMissionState.Stopped);
        }

        public VirtualFleetSnapshot GetSnapshot()
        {
            var all = new List<VirtualFleetDeviceState>(
                uavs.Count + usvs.Count + targets.Count
            );
            all.AddRange(uavs);
            all.AddRange(usvs);
            all.AddRange(targets);
            return new VirtualFleetSnapshot
            {
                missionState = missionState.ToString().ToUpperInvariant(),
                devices = all.ToArray()
            };
        }

        private void AddUavInternal()
        {
            string code = "UAV-" + nextUavNumber.ToString("000");
            nextUavNumber++;
            Transform model = factory.Create(VirtualFleetDeviceType.Uav, code, uavs.Count);
            uavs.Add(CreateState(code, "UAV", model));
        }

        private void AddUsvInternal()
        {
            string code = "USV-" + nextUsvNumber.ToString("000");
            nextUsvNumber++;
            Transform model = factory.Create(VirtualFleetDeviceType.Usv, code, usvs.Count);
            usvs.Add(CreateState(code, "USV", model));
        }

        private void AddTargetInternal(string code, int index)
        {
            Transform model = factory.Create(
                VirtualFleetDeviceType.Target,
                code,
                index
            );
            targets.Add(CreateState(code, "TARGET", model));
        }

        private static VirtualFleetDeviceState CreateState(
            string code,
            string type,
            Transform model)
        {
            return new VirtualFleetDeviceState
            {
                deviceCode = code,
                deviceType = type,
                status = "STOPPED",
                position = model.position,
                rotation = model.rotation,
                desiredPosition = model.position,
                desiredRotation = model.rotation,
                hasDesiredPose = true,
                transform = model
            };
        }

        private void SmoothDevices(
            List<VirtualFleetDeviceState> devices,
            float positionT,
            float deltaTime)
        {
            for (int i = 0; i < devices.Count; i++)
            {
                VirtualFleetDeviceState state = devices[i];
                if (state == null || !state.transform || !state.hasDesiredPose)
                    continue;
                // Reset/spawn uses TryApplyPoseImmediate explicitly. Never
                // infer a reset from a large streamed position difference.
                Vector3 positionBefore = state.transform.position;
                Quaternion displayedRotation = state.desiredRotation;
                if (posePlayback.TryGetValue(state.deviceCode, out VirtualFleetPosePlayback playback)
                    && playback.Evaluate(Time.realtimeSinceStartupAsDouble,
                        out Vector3 displayedPosition, out displayedRotation))
                    state.transform.position = displayedPosition;
                else
                    state.transform.position = Vector3.Lerp(state.transform.position,
                        state.desiredPosition, positionT);
                float movedSqr = (state.transform.position - positionBefore).sqrMagnitude;
                if (state.deviceType == "TARGET")
                {
                    // Surface targets only move bow-first. Derive the visual
                    // bow from the SAME smoothed displacement, not a second
                    // slower yaw filter (previously 36 versus 70 deg/s).
                    // Stationary turns remain rate-limited. Guard USVs keep
                    // their independent heading so deliberate astern is valid.
                    Vector3 motion = state.transform.position - positionBefore;
                    motion.y = 0f;
                    state.transform.rotation = motion.sqrMagnitude > 0.00000001f
                        ? Quaternion.Euler(0f, -Mathf.Atan2(motion.z, motion.x) * Mathf.Rad2Deg, 0f)
                        : Quaternion.RotateTowards(state.transform.rotation, displayedRotation,
                            72f * Mathf.Max(0f, deltaTime));
                    state.position = state.transform.position;
                    state.rotation = state.transform.rotation;
                    continue;
                }
                float maximumYawRate = state.deviceType == "USV"
                    // Algorithm frames may command up to seven degrees per
                    // 100 ms tick. Match that physical envelope so a moving
                    // boat does not visually trail its inward-facing ring
                    // heading after the geometry is already valid.
                    ? 72f
                    : state.deviceType == "UAV" ? 110f : 36f;
                // A captured surface craft may be position-stationary while
                // it still has to point its bow at the assigned target. Do
                // not freeze that final orientation. The angular dead-band
                // filters tiny 10 Hz heading noise without preventing a real
                // correction, and therefore applies at every fleet size.
                float headingError = Quaternion.Angle(
                    state.transform.rotation,
                    displayedRotation
                );
                bool shouldRotate = state.deviceType != "USV"
                    || movedSqr > 0.000004f
                    || headingError > 0.35f;
                if (shouldRotate)
                {
                    float appliedYawRate = state.deviceType == "USV"
                        && movedSqr <= 0.000004f
                        ? 120f
                        : maximumYawRate;
                    state.transform.rotation = Quaternion.RotateTowards(
                        state.transform.rotation,
                        displayedRotation,
                        appliedYawRate * Mathf.Max(0f, deltaTime)
                    );
                }
                state.position = state.transform.position;
                state.rotation = state.transform.rotation;
            }
        }

        private bool Remove(List<VirtualFleetDeviceState> devices, string deviceCode)
        {
            if (!CanModifyFleet || string.IsNullOrWhiteSpace(deviceCode))
                return false;
            int index = devices.FindIndex(item =>
                string.Equals(item.deviceCode, deviceCode.Trim(), StringComparison.OrdinalIgnoreCase));
            if (index < 0)
                return false;
            VirtualFleetDeviceState state = devices[index];
            if (state.transform)
                Destroy(state.transform.gameObject);
            devices.RemoveAt(index);
            NotifyFleetChanged();
            return true;
        }

        private void ClearFleet()
        {
            posePlayback.Clear();
            for (int i = 0; i < uavs.Count; i++)
                if (uavs[i].transform) Destroy(uavs[i].transform.gameObject);
            for (int i = 0; i < usvs.Count; i++)
                if (usvs[i].transform) Destroy(usvs[i].transform.gameObject);
            for (int i = 0; i < targets.Count; i++)
                if (targets[i].transform &&
                    targets[i].transform != externalProtectedTargetTransform &&
                    targets[i].transform != externalThreatTargetTransform)
                    Destroy(targets[i].transform.gameObject);
            uavs.Clear();
            usvs.Clear();
            targets.Clear();
        }

        private void SetMissionState(VirtualFleetMissionState nextState)
        {
            if (missionState == nextState)
                return;
            missionState = nextState;
            for (int i = 0; i < uavs.Count; i++)
                uavs[i].status = nextState.ToString().ToUpperInvariant();
            for (int i = 0; i < usvs.Count; i++)
                usvs[i].status = nextState.ToString().ToUpperInvariant();
            for (int i = 0; i < targets.Count; i++)
                targets[i].status = nextState.ToString().ToUpperInvariant();
            MissionStateChanged?.Invoke(missionState);
        }

        private void NotifyFleetChanged()
        {
            FleetChanged?.Invoke();
        }

        private VirtualFleetDeviceState FindDevice(string deviceCode)
        {
            if (string.IsNullOrWhiteSpace(deviceCode))
                return null;
            string normalized = deviceCode.Trim();
            for (int i = 0; i < uavs.Count; i++)
                if (string.Equals(uavs[i].deviceCode, normalized, StringComparison.OrdinalIgnoreCase))
                    return uavs[i];
            for (int i = 0; i < usvs.Count; i++)
                if (string.Equals(usvs[i].deviceCode, normalized, StringComparison.OrdinalIgnoreCase))
                    return usvs[i];
            for (int i = 0; i < targets.Count; i++)
                if (string.Equals(targets[i].deviceCode, normalized, StringComparison.OrdinalIgnoreCase))
                    return targets[i];
            return null;
        }

        private static Transform[] ToTransformArray(
            List<VirtualFleetDeviceState> devices)
        {
            var result = new Transform[devices.Count];
            for (int i = 0; i < devices.Count; i++)
                result[i] = devices[i].transform;
            return result;
        }

        private static void SetVisible(
            List<VirtualFleetDeviceState> devices,
            bool visible)
        {
            for (int i = 0; i < devices.Count; i++)
                if (devices[i] != null && devices[i].transform)
                    devices[i].transform.gameObject.SetActive(visible);
        }
    }
}
