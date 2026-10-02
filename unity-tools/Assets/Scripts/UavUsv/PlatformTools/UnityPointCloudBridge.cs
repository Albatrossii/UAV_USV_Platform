using System;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Scripting;

namespace UavUsv.PlatformTools
{
    /// <summary>
    /// Receives and validates the latest MID360 point-cloud frame forwarded by
    /// the system overview. It retains one frame and acknowledges only after
    /// Unity has parsed and validated the complete XYZ payload.
    /// </summary>
    [Preserve]
    public sealed class UnityPointCloudBridge : MonoBehaviour
    {
        private const string Protocol = "mid360-pointcloud-v1";
        private const string ExpectedStreamId = "usv_01_mid360";
        private const int MaxPointCount = 20_000;

        [Serializable]
        private sealed class IncomingEnvelope
        {
            public string type;
            public string requestId;
            public PointCloudPayload payload;
        }

        [Serializable]
        private sealed class PointCloudPayload
        {
            public long receivedAtMs;
            public PointCloudFrame frame;
        }

        [Serializable]
        private sealed class PointCloudFrame
        {
            public string schema_version;
            public string message_type;
            public long sequence;
            public PointCloudData data;
        }

        [Serializable]
        private sealed class PointCloudData
        {
            public string stream_id;
            public string vehicle_id;
            public string frame_id;
            public int point_count;
            public float[] xyz;
        }

        [Serializable]
        private sealed class ResponseEnvelope
        {
            public string type;
            public string requestId;
            public long timestamp;
            public ResponsePayload payload;
        }

        [Serializable]
        private sealed class ResponsePayload
        {
            public bool success;
            public bool ready;
            public string protocol;
            public string streamId;
            public string frameId;
            public long sequence;
            public int pointCount;
            public int xyzLength;
            public long receivedAtMs;
            public string status;
            public string source = "unity-webgl";
        }

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void VueWebGlPostMessage(string message);
#endif

        private float[] latestXyz = Array.Empty<float>();
        private long latestSequence;
        private int latestPointCount;

        public long LatestSequence => latestSequence;
        public int LatestPointCount => latestPointCount;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Install()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            GameObject host = GameObject.Find("UnityPointCloudBridge");
            if (!host)
                host = new GameObject("UnityPointCloudBridge");
            DontDestroyOnLoad(host);
            if (!host.GetComponent<UnityPointCloudBridge>())
                host.AddComponent<UnityPointCloudBridge>();
#endif
        }

        private void Start()
        {
            Post("pointCloudBridgeReady", new ResponsePayload
            {
                ready = true,
                protocol = Protocol,
                streamId = ExpectedStreamId,
                status = "Unity point-cloud receiver initialized"
            }, string.Empty);
        }

        [Preserve]
        public void ReceiveFromVue(string json)
        {
            IncomingEnvelope envelope = null;
            try
            {
                envelope = JsonUtility.FromJson<IncomingEnvelope>(json);
            }
            catch (Exception exception)
            {
                Acknowledge(envelope, false, "Invalid point-cloud JSON: " + exception.Message, null);
                return;
            }

            if (envelope == null || envelope.payload == null || envelope.payload.frame == null)
            {
                Acknowledge(envelope, false, "Point-cloud frame is missing", null);
                return;
            }

            PointCloudFrame frame = envelope.payload.frame;
            PointCloudData data = frame.data;
            string error = Validate(envelope, frame, data);
            if (!string.IsNullOrEmpty(error))
            {
                Acknowledge(envelope, false, error, data);
                return;
            }

            // Keep only the newest validated frame; do not queue point-cloud history.
            latestXyz = data.xyz;
            latestSequence = frame.sequence;
            latestPointCount = data.point_count;
            Acknowledge(envelope, true, "Unity parsed and retained the latest point-cloud frame", data);
        }

        private static string Validate(IncomingEnvelope envelope, PointCloudFrame frame, PointCloudData data)
        {
            if (envelope.type != "pointCloudFrame")
                return "Unsupported Unity message type";
            if (frame.message_type != "pointcloud_frame")
                return "Payload is not a pointcloud_frame";
            if (data == null)
                return "Point-cloud data is missing";
            if (data.stream_id != ExpectedStreamId || data.vehicle_id != "usv_01")
                return "Unexpected point-cloud stream identity";
            if (data.frame_id != "map")
                return "Point-cloud frame must use map coordinates";
            if (data.point_count <= 0 || data.point_count > MaxPointCount)
                return "Point count is outside the supported range";
            if (data.xyz == null || data.xyz.Length != data.point_count * 3)
                return "XYZ length does not match point_count * 3";
            for (int index = 0; index < data.xyz.Length; index++)
            {
                float coordinate = data.xyz[index];
                if (float.IsNaN(coordinate) || float.IsInfinity(coordinate))
                    return "Point-cloud contains a non-finite coordinate";
            }
            return string.Empty;
        }

        private static void Acknowledge(
            IncomingEnvelope envelope,
            bool success,
            string status,
            PointCloudData data
        )
        {
            PointCloudFrame frame = envelope != null && envelope.payload != null
                ? envelope.payload.frame
                : null;
            Post("pointCloudFrameApplied", new ResponsePayload
            {
                success = success,
                protocol = Protocol,
                streamId = data != null ? data.stream_id : ExpectedStreamId,
                frameId = data != null ? data.frame_id : string.Empty,
                sequence = frame != null ? frame.sequence : 0,
                pointCount = data != null ? data.point_count : 0,
                xyzLength = data != null && data.xyz != null ? data.xyz.Length : 0,
                receivedAtMs = envelope != null && envelope.payload != null
                    ? envelope.payload.receivedAtMs
                    : 0,
                status = status
            }, envelope != null ? envelope.requestId : string.Empty);
        }

        private static void Post(string type, ResponsePayload payload, string requestId)
        {
            var envelope = new ResponseEnvelope
            {
                type = type,
                requestId = requestId ?? string.Empty,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                payload = payload
            };
            string json = JsonUtility.ToJson(envelope);
#if UNITY_WEBGL && !UNITY_EDITOR
            VueWebGlPostMessage(json);
#else
            Debug.Log("[UnityPointCloudBridge] " + json);
#endif
        }
    }
}
