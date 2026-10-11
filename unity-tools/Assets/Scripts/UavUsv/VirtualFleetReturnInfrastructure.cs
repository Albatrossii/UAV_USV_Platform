using System;
using System.Collections.Generic;
using UnityEngine;

namespace UavUsv
{
    [Serializable]
    public sealed class ReturnShoreDefinition
    {
        public float eastMinM, eastMaxM, northMinM, northMaxM, surfaceUpM;
    }

    [Serializable]
    public sealed class ReturnParkingSlot
    {
        public string deviceCode, kind;
        public float eastM, northM, upM, headingDeg;
        public float approachEastM, approachNorthM, approachUpM;
    }

    [Serializable]
    public sealed class ReturnInfrastructureDefinition
    {
        public string version, coordinateFrame;
        // Kept as an optional wire-compatibility field, never used to construct land.
        public ReturnShoreDefinition shore;
        public ReturnParkingSlot[] slots;
    }

    /// <summary>
    /// Metadata and model-contact seam for the ORIGINAL scene's three pads and
    /// natural-shore anchors. This component creates no meshes, labels, docks,
    /// platforms, or camera-framing bounds, including for legacy v1 metadata.
    /// Python owns every destination and trajectory; all coordinates are GLOBAL_ENU.
    /// </summary>
    public sealed class VirtualFleetReturnInfrastructure : MonoBehaviour
    {
        private ReturnInfrastructureDefinition definition;
        private long runId;
        private readonly Dictionary<string, ReturnParkingSlot> slots =
            new Dictionary<string, ReturnParkingSlot>(StringComparer.OrdinalIgnoreCase);
        private readonly Dictionary<Transform, float> feetOffsets = new Dictionary<Transform, float>();

        public int SlotCount => slots.Count;
        public int ConfigurationGeneration { get; private set; }

        public bool Configure(long scenarioRunId, ReturnInfrastructureDefinition incoming)
        {
            if (scenarioRunId == runId && SameDefinition(definition, incoming)) return true;
            Clear();
            runId = scenarioRunId;
            if (incoming == null) return true;
            if (!IsValid(incoming)) return false;
            definition = JsonUtility.FromJson<ReturnInfrastructureDefinition>(JsonUtility.ToJson(incoming));
            foreach (ReturnParkingSlot slot in definition.slots) slots.Add(slot.deviceCode, slot);
            ConfigurationGeneration++;
            return true;
        }

        public static bool IsValid(ReturnInfrastructureDefinition value)
        {
            if (value == null || (value.version != "scene-existing-v1" && value.version != "fixed-shore-v1")
                || value.coordinateFrame != "GLOBAL_ENU" || value.slots == null || value.slots.Length == 0
                || value.slots.Length > 256) return false;
            var codes = new HashSet<string>(StringComparer.Ordinal);
            int uavs = 0, usvs = 0;
            foreach (ReturnParkingSlot slot in value.slots)
            {
                if (slot == null || !CanonicalCode(slot.deviceCode) || !codes.Add(slot.deviceCode)
                    || !Finite(slot.eastM, slot.northM, slot.upM, slot.headingDeg,
                        slot.approachEastM, slot.approachNorthM, slot.approachUpM)) return false;
                if (slot.kind == "HELIPAD" && slot.deviceCode.StartsWith("UAV-", StringComparison.Ordinal)) uavs++;
                else if (slot.kind == "BERTH" && slot.deviceCode.StartsWith("USV-", StringComparison.Ordinal)) usvs++;
                else return false;
            }
            // The original scene has three pads. Never fabricate capacity by
            // adding geometry or silently stacking a fourth craft on a pad.
            return value.version != "scene-existing-v1" || (uavs <= 3 && usvs <= 3);
        }

        private static bool CanonicalCode(string code)
        {
            if (code == null || code.Length != 7) return false;
            for (int index = 4; index < 7; index++) if (code[index] < '0' || code[index] > '9') return false;
            int ordinal = (code[4] - '0') * 100 + (code[5] - '0') * 10 + code[6] - '0';
            return ordinal >= 1 && ordinal <= 128;
        }

        private static bool Finite(params float[] values)
        {
            foreach (float value in values) if (float.IsNaN(value) || float.IsInfinity(value)) return false;
            return true;
        }

        private static bool SameDefinition(ReturnInfrastructureDefinition a, ReturnInfrastructureDefinition b)
        {
            if (ReferenceEquals(a, b)) return true;
            if (a == null || b == null || a.slots == null || b.slots == null
                || a.version != b.version || a.coordinateFrame != b.coordinateFrame || a.slots.Length != b.slots.Length) return false;
            // Optional historical shore extents do not affect rendering/contact.
            for (int i = 0; i < a.slots.Length; i++)
            {
                ReturnParkingSlot p = a.slots[i], q = b.slots[i];
                if (p == null || q == null || p.deviceCode != q.deviceCode || p.kind != q.kind
                    || p.eastM != q.eastM || p.northM != q.northM || p.upM != q.upM || p.headingDeg != q.headingDeg
                    || p.approachEastM != q.approachEastM || p.approachNorthM != q.approachNorthM
                    || p.approachUpM != q.approachUpM) return false;
            }
            return true;
        }

        public Vector3 AdjustGroundContact(VirtualPose pose, Transform vehicle, Vector3 position)
        {
            if (!vehicle || pose == null || !slots.TryGetValue(pose.deviceCode ?? "", out ReturnParkingSlot slot)
                || slot.kind != "HELIPAD") return position;
            // Positional blending also covers departure, so toggling from
            // RETURNED to REJOINING cannot abruptly change the rendered height.
            float horizontal = Vector2.Distance(new Vector2(pose.eastM, pose.northM), new Vector2(slot.eastM, slot.northM));
            DroneVisual drone = vehicle.GetComponent<DroneVisual>();
            bool parked = string.Equals(pose.state, "PARKED", StringComparison.OrdinalIgnoreCase)
                || string.Equals(pose.state, "RETURNED", StringComparison.OrdinalIgnoreCase);
            if (drone) drone.spinning = !(parked && horizontal <= .2f && Mathf.Abs(pose.upM - slot.upM) <= .2f);
            float nearPad = 1f - Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(2f, 8f, horizontal));
            float lowEnough = 1f - Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(.15f, 4f, pose.upM - slot.upM));
            if (nearPad <= 0f || lowEnough <= 0f) return position;
            if (!feetOffsets.TryGetValue(vehicle, out float offset))
            {
                float minimum = float.PositiveInfinity;
                foreach (Renderer renderer in vehicle.GetComponentsInChildren<Renderer>(false))
                    if (!(renderer is LineRenderer) && !(renderer is ParticleSystemRenderer) && renderer.enabled)
                        minimum = Mathf.Min(minimum, renderer.bounds.min.y);
                offset = float.IsInfinity(minimum) ? 0f : vehicle.position.y - minimum;
                feetOffsets[vehicle] = offset;
            }
            position.y += (offset + .002f) * nearPad * lowEnough;
            return position;
        }

        public void Clear()
        {
            slots.Clear();
            feetOffsets.Clear();
            definition = null;
            // Remove only this component's previously created child after an
            // Editor hot reload. Normal fresh players never create that child.
            Transform obsolete = transform.Find("fixed_return_infrastructure");
            if (obsolete)
            {
                obsolete.gameObject.SetActive(false);
                if (Application.isPlaying) Destroy(obsolete.gameObject);
                else DestroyImmediate(obsolete.gameObject);
            }
        }

        private void OnDestroy() { Clear(); }
    }
}
