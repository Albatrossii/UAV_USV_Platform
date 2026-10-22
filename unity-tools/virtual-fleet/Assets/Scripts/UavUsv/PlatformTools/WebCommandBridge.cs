using System;
using System.Collections;
using System.Runtime.InteropServices;
using UnityEngine;
using UnityEngine.Scripting;

namespace UavUsv.PlatformTools
{
    /// <summary>
    /// Receives Vue postMessage commands through the WebGL page and controls only
    /// presentation-side camera tools. Mission behavior remains owned by the
    /// existing simulation and ROS control layers.
    /// </summary>
    [Preserve]
    public sealed class WebCommandBridge : MonoBehaviour
    {
        [Serializable]
        private sealed class VueMessage
        {
            public string type;
            public string requestId;
            public long timestamp;
            public VuePayload payload;
        }

        [Serializable]
        private sealed class VuePayload
        {
            public string deviceCode;
            public string[] deviceCodes;
            public string mode;
            public string command;
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
            public string deviceCode;
            public string mode;
            public string profile;
            public string status;
            public string source = "unity-webgl";
        }

        private WebDeviceObserverCamera observer;
        private WebVehicleCommandController vehicleController;
        private VirtualFleetPlatformBridge virtualFleetBridge;
        private static WebCommandBridge instance;
        private string lastCameraCommandKey = string.Empty;
        private float lastCameraCommandAt = -1f;

        private const float DuplicateCameraCommandWindow = .35f;

#if UNITY_WEBGL && !UNITY_EDITOR
        [DllImport("__Internal")]
        private static extern void VueWebGlPostMessage(string message);
#endif

        private void Awake()
        {
            if (instance && instance != this)
            {
                Debug.LogWarning(
                    "[WebCommandBridge] Duplicate component removed from " +
                    gameObject.name
                );
                Destroy(this);
                return;
            }

            instance = this;
        }

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Install()
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            GameObject platformHost = GameObject.Find("PlatformBridge");
            WebCommandBridge bridge = platformHost
                ? platformHost.GetComponent<WebCommandBridge>()
                : FindObjectOfType<WebCommandBridge>();
            GameObject host = bridge
                ? bridge.gameObject
                : platformHost
                    ? platformHost
                    : new GameObject("PlatformBridge");
            platformHost = host;
            DontDestroyOnLoad(host);
            if (!bridge) bridge = host.AddComponent<WebCommandBridge>();
            WebVehicleCommandController controller = host.GetComponent<WebVehicleCommandController>();
            if (!controller) controller = host.AddComponent<WebVehicleCommandController>();
            WebTrajectoryTelemetryBridge telemetry = host.GetComponent<WebTrajectoryTelemetryBridge>();
            if (!telemetry) telemetry = host.AddComponent<WebTrajectoryTelemetryBridge>();
            telemetry.Initialize(controller);
            bridge.vehicleController = controller;

            DontDestroyOnLoad(platformHost);
            bridge.virtualFleetBridge =
                platformHost.GetComponent<VirtualFleetPlatformBridge>();
            if (!bridge.virtualFleetBridge)
                bridge.virtualFleetBridge =
                    platformHost.AddComponent<VirtualFleetPlatformBridge>();
            UavUsv.VirtualFleetScenarioController scenario =
                FindObjectOfType<UavUsv.VirtualFleetScenarioController>();
            if (scenario)
                bridge.virtualFleetBridge.Initialize(scenario);
#endif
        }

        private IEnumerator Start()
        {
            while (!EnsureObserver())
                yield return null;
        }

        [Preserve]
        public void ReceiveFromVue(string json)
        {
            VueMessage message;
            try
            {
                message = JsonUtility.FromJson<VueMessage>(json);
            }
            catch (Exception exception)
            {
                PostCameraResult(string.Empty, false, string.Empty, string.Empty, string.Empty, "Invalid Vue message: " + exception.Message);
                return;
            }

            if (message == null || string.IsNullOrWhiteSpace(message.type))
            {
                PostCameraResult(string.Empty, false, string.Empty, string.Empty, string.Empty, "Vue message type is empty");
                return;
            }

            string normalizedType = message.type.Trim();
            if (virtualFleetBridge && virtualFleetBridge.CanHandle(normalizedType))
            {
                virtualFleetBridge.Receive(json);
                return;
            }

            if (!EnsureObserver())
            {
                PostCameraResult(message.requestId, false, string.Empty, string.Empty, string.Empty, "Unity camera is not ready");
                return;
            }

            string type = normalizedType.ToLowerInvariant();
            VuePayload payload = message.payload ?? new VuePayload();
            switch (type)
            {
                case "focuscommand":
                    bool focused = observer.FocusCommand(payload.deviceCodes);
                    PostCameraResult(message.requestId, focused, observer.CurrentDeviceCode,
                        observer.CurrentModeName, observer.CurrentProfileName, focused ? "Command focus active" : "Command nodes unavailable");
                    break;
                case "restorecommandcamera":
                    observer.RestoreCommandCamera();
                    PostCameraResult(message.requestId, true, observer.CurrentDeviceCode,
                        observer.CurrentModeName, observer.CurrentProfileName, "Previous camera restored");
                    break;
                case "selectdevice":
                    SelectDevice(message.requestId, payload.deviceCode);
                    break;
                case "focusdevice":
                    FocusDevice(message.requestId, payload.deviceCode);
                    break;
                case "setcameramode":
                    SetCameraMode(message.requestId, payload.mode);
                    break;
                case "switchcamera":
                    SwitchCamera(message.requestId, payload.mode);
                    break;
                case "sendcontrolcommand":
                    ExecuteVehicleCommand(message.requestId, payload.deviceCode, payload.command);
                    break;
            }
        }

        private bool EnsureObserver()
        {
            if (observer)
                return true;
            Camera camera = Camera.main;
            if (!camera)
                return false;
            UavUsv.ChaseCamera chase = camera.GetComponent<UavUsv.ChaseCamera>();
            if (!chase)
                return false;
            observer = camera.GetComponent<WebDeviceObserverCamera>();
            if (!observer)
                observer = camera.gameObject.AddComponent<WebDeviceObserverCamera>();
            observer.Initialize(camera, chase);
            return true;
        }

        [ContextMenu("Set Web Overview")]
        private void SetWebOverviewForTesting()
        {
            if (!EnsureObserver())
            {
                Debug.LogWarning(
                    "[WebCommandBridge] Web overview is unavailable because " +
                    "the main camera is not ready."
                );
                return;
            }

            observer.SetOverview();
            Debug.Log(
                "[WebCommandBridge] WebDeviceObserverCamera set to overview " +
                "for manual testing."
            );
        }

        public void SetVirtualFleetMissionState(string state)
        {
            if (EnsureObserver())
                observer.SetMissionState(state);
        }

        public void ResetVirtualFleetTrails()
        {
            if (EnsureObserver())
                observer.ResetFleetTrails();
        }

        private bool EnsureVehicleController()
        {
            if (!vehicleController)
                vehicleController = GetComponent<WebVehicleCommandController>();
            if (!vehicleController)
                vehicleController = gameObject.AddComponent<WebVehicleCommandController>();
            WebTrajectoryTelemetryBridge telemetry = GetComponent<WebTrajectoryTelemetryBridge>();
            if (!telemetry)
                telemetry = gameObject.AddComponent<WebTrajectoryTelemetryBridge>();
            telemetry.Initialize(vehicleController);
            return vehicleController && vehicleController.EnsureScenario();
        }

        [Preserve]
        public void SelectDevice(string json)
        {
            VueMessage message = ParseVueMessage(json);
            VuePayload payload = message != null && message.payload != null
                ? message.payload
                : new VuePayload();
            SelectDevice(
                message != null ? message.requestId : string.Empty,
                payload.deviceCode
            );
        }

        [Preserve]
        public void SelectDevice(string requestId, string requestedCode)
        {
            if (!EnsureObserver())
            {
                PostCameraResult(
                    requestId,
                    false,
                    requestedCode,
                    "device-follow",
                    string.Empty,
                    "Unity camera is not ready"
                );
                return;
            }

            bool success = observer.TrySelectDevice(
                requestedCode,
                out string code,
                out string profile,
                out string error
            );
            PostCameraResult(
                requestId,
                success,
                code,
                success ? observer.CurrentModeName : "device-follow",
                profile,
                success ? "Camera following " + code : error
            );
        }

        [Preserve]
        public void SetCameraMode(string json)
        {
            VueMessage message = ParseVueMessage(json);
            VuePayload payload = message != null && message.payload != null
                ? message.payload
                : new VuePayload();
            SetCameraMode(
                message != null ? message.requestId : string.Empty,
                payload.mode
            );
        }

        [Preserve]
        public void SetCameraMode(string requestId, string requestedMode)
        {
            string normalizedMode = string.IsNullOrWhiteSpace(requestedMode)
                ? "overview"
                : requestedMode.Trim().ToLowerInvariant();
            string selectedCode = observer ? observer.CurrentDeviceCode : string.Empty;
            string commandKey = normalizedMode + "|" + selectedCode;
            if (string.Equals(commandKey, lastCameraCommandKey, StringComparison.Ordinal) &&
                Time.unscaledTime - lastCameraCommandAt <
                DuplicateCameraCommandWindow)
                return;

            lastCameraCommandKey = commandKey;
            lastCameraCommandAt = Time.unscaledTime;
            if (!EnsureObserver())
            {
                PostCameraResult(
                    requestId,
                    false,
                    string.Empty,
                    requestedMode,
                    string.Empty,
                    "Unity camera is not ready"
                );
                return;
            }
            SwitchCamera(requestId, normalizedMode);
        }

        private static VueMessage ParseVueMessage(string json)
        {
            if (string.IsNullOrWhiteSpace(json))
                return new VueMessage { payload = new VuePayload() };

            try
            {
                VueMessage message = JsonUtility.FromJson<VueMessage>(json);
                if (message != null && message.payload != null)
                    return message;

                DirectCameraMessage direct =
                    JsonUtility.FromJson<DirectCameraMessage>(json);
                return direct == null
                    ? new VueMessage { payload = new VuePayload() }
                    : new VueMessage
                    {
                        requestId = direct.requestId,
                        payload = new VuePayload
                        {
                            deviceCode = direct.deviceCode,
                            mode = direct.mode
                        }
                    };
            }
            catch
            {
                return new VueMessage { payload = new VuePayload() };
            }
        }

        [Serializable]
        private sealed class DirectCameraMessage
        {
            public string requestId;
            public string deviceCode;
            public string mode;
        }

        [Preserve]
        public void FocusDevice(string requestId, string requestedCode)
        {
            if (!string.IsNullOrWhiteSpace(requestedCode))
            {
                SelectDevice(requestId, requestedCode);
                return;
            }

            bool success = observer.RecenterCurrentDevice(out string error);
            PostCameraResult(
                requestId,
                success,
                observer.CurrentDeviceCode,
                observer.CurrentModeName,
                observer.CurrentProfileName,
                success ? "Camera recentered" : error
            );
        }

        [Preserve]
        public void SwitchCamera(string requestId, string requestedMode)
        {
            string mode = string.IsNullOrWhiteSpace(requestedMode)
                ? "overview"
                : requestedMode.Trim().ToLowerInvariant();
            if (mode == "overview")
            {
                observer.SetOverview();
                PostCameraResult(requestId, true, string.Empty, "overview", "overview", "Global overview active");
                return;
            }
            if (mode == "lighthouse")
            {
                observer.SetLighthouse();
                PostCameraResult(requestId, true, string.Empty, "lighthouse", "lighthouse", "Lighthouse view active");
                return;
            }
            if (mode == "action")
            {
                observer.ReleaseToOriginalCamera();
                PostCameraResult(requestId, true, string.Empty, "action", "action", "Original action camera restored");
                return;
            }
            if (mode == "device-follow")
            {
                bool success = observer.RecenterCurrentDevice(out string error);
                PostCameraResult(requestId, success, observer.CurrentDeviceCode, mode, observer.CurrentProfileName, success ? "Device view active" : error);
                return;
            }
            if (mode == "follow-usv" || mode == "follow-uav")
            {
                bool success = observer.TrySelectFirst(
                    mode == "follow-uav" ? "UAV" : "USV",
                    out string code,
                    out string profile,
                    out string error
                );
                PostCameraResult(requestId, success, code, "device-follow", profile, success ? "Camera following " + code : error);
                return;
            }

            PostCameraResult(requestId, false, observer.CurrentDeviceCode, mode, string.Empty, "Unknown camera mode: " + requestedMode);
        }

        private void PostCameraResult(
            string requestId,
            bool success,
            string deviceCode,
            string mode,
            string profile,
            string status)
        {
            var response = new ResponseEnvelope
            {
                type = "cameraChanged",
                requestId = requestId ?? string.Empty,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                payload = new ResponsePayload
                {
                    success = success,
                    deviceCode = deviceCode ?? string.Empty,
                    mode = mode ?? string.Empty,
                    profile = profile ?? string.Empty,
                    status = status ?? string.Empty
                }
            };
            Emit(JsonUtility.ToJson(response));
        }

        private void ExecuteVehicleCommand(string requestId, string deviceCode, string command)
        {
            string state = "ERROR";
            string detail = "Unity vehicle controller is not ready";
            bool handledByVirtualFleet = virtualFleetBridge &&
                virtualFleetBridge.TryExecuteMissionCommand(command, out state, out detail);
            bool success = handledByVirtualFleet
                ? state != "ERROR"
                : EnsureVehicleController() &&
                    vehicleController.TryExecute(command, deviceCode, out state, out detail);
            var response = new ResponseEnvelope
            {
                type = "commandAck",
                requestId = requestId ?? string.Empty,
                timestamp = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                payload = new ResponsePayload
                {
                    success = success,
                    deviceCode = deviceCode ?? string.Empty,
                    mode = observer ? observer.CurrentModeName : string.Empty,
                    profile = observer ? observer.CurrentProfileName : string.Empty,
                    status = success ? state + ": " + detail : detail
                }
            };
            Emit(JsonUtility.ToJson(response));
        }

        private static void Emit(string json)
        {
#if UNITY_WEBGL && !UNITY_EDITOR
            VueWebGlPostMessage(json);
#else
            Debug.Log("[WebCommandBridge] " + json);
#endif
        }
    }
}
