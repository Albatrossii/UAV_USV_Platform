using System;
using System.Collections.Generic;
using UnityEngine;

namespace UavUsv.PlatformTools
{
    /// <summary>
    /// WebGL-only observation camera used by the Vue system overview.
    /// It never writes to mission agents and does not change ChaseCamera source code.
    /// </summary>
    public sealed class WebDeviceObserverCamera : MonoBehaviour
    {
        private enum ObservationMode
        {
            None,
            Device,
            Overview,
            Lighthouse
        }

        private readonly List<Transform> sceneTargets = new List<Transform>();
        private readonly RaycastHit[] occlusionHits = new RaycastHit[16];
        private readonly Dictionary<Transform, LineRenderer> fleetTrails =
            new Dictionary<Transform, LineRenderer>();
        private readonly Dictionary<Transform, List<Vector3>> fleetTrailPoints =
            new Dictionary<Transform, List<Vector3>>();
        private readonly HashSet<Transform> initializedTrailTargets =
            new HashSet<Transform>();
        private Camera observedCamera;
        private UavUsv.ChaseCamera chaseCamera;
        private UavUsv.VirtualFleetManager fleetManager;
        private Transform selectedSubject;
        private Transform lighthouse;
        private ObservationMode mode;
        private float desiredFieldOfView = 52f;
        private Transform trailRoot;
        private Material uavTrailMaterial;
        private Material usvTrailMaterial;
        private float nextTrailSampleAt;
        private bool trailSampleLogged;
        private int lastTrailDeviceCount = -1;
        private bool trailRecordingEnabled;
        private float nextSceneTargetRefreshAt;
        private Vector3 overviewFocus;
        private float overviewDistance;
        private bool overviewFrameInitialized;
        private float overviewContractionStartedAt = -1f;

        private const float OverviewTransitionSeconds = 1.2f;
        private bool hasRenderedObserverFrame;
        private bool overviewTransitionActive;
        private float overviewTransitionStartedAt;
        private Vector3 overviewTransitionStartPosition;
        private Quaternion overviewTransitionStartRotation;
        private float overviewTransitionStartFieldOfView;

        // Algorithm simulation intentionally ships without world-space
        // trajectories. Keeping this as a compile-time gate prevents mission
        // state or camera-mode changes from silently enabling them again.
        private const bool TrajectoryRenderingEnabled = false;

        private ObservationMode interactionMode = ObservationMode.None;
        private Vector3 interactionFocusOffset;
        private float interactionYaw;
        private float interactionPitch;
        private float interactionRoll;
        private float interactionDistance;
        private float lastPrimaryClickAt = -10f;
        private float lastInteractionAt = -10f;

        private const int MaxTrailPoints = 180;
        private const float TrailSampleSeconds = .2f;
        private const float TrailMinDistance = 1.25f;
        private const float TrailMaxJumpDistance = 4f;

        public string CurrentDeviceCode { get; private set; } = string.Empty;
        public string CurrentModeName => ModeName(mode);
        public string CurrentProfileName => selectedSubject && IsUav(selectedSubject)
            ? "uav-overwatch"
            : selectedSubject ? "usv-chase" : CurrentModeName;

        public void Initialize(Camera camera, UavUsv.ChaseCamera chase)
        {
            observedCamera = camera;
            chaseCamera = chase;
            fleetManager = FindObjectOfType<UavUsv.VirtualFleetManager>();
            RefreshSceneTargets(true);
        }

        public bool TrySelectDevice(
            string requestedCode,
            out string canonicalCode,
            out string profile,
            out string error)
        {
            RefreshSceneTargets(true);
            string normalized = NormalizeDeviceCode(requestedCode);
            Transform match = null;
            for (int i = 0; i < sceneTargets.Count; i++)
            {
                Transform candidate = sceneTargets[i];
                if (!candidate || (!IsUsv(candidate) && !IsUav(candidate)))
                    continue;
                if (NormalizeDeviceCode(candidate.name) == normalized)
                {
                    match = candidate;
                    break;
                }
            }

            if (!match)
                match = FindSceneDevice(normalized);

            if (!match)
            {
                canonicalCode = normalized;
                profile = string.Empty;
                error = "Unity scene device not found: " + requestedCode;
                return false;
            }

            CancelOverviewTransition();
            ResetCameraInteraction();
            selectedSubject = match;
            CurrentDeviceCode = CanonicalDeviceCode(match);
            mode = ObservationMode.Device;
            ActivateObserver();
            canonicalCode = CurrentDeviceCode;
            profile = CurrentProfileName;
            error = string.Empty;
            return true;
        }

        private static Transform FindSceneDevice(string normalizedCode)
        {
            Transform[] transforms = FindObjectsOfType<Transform>(true);
            for (int i = 0; i < transforms.Length; i++)
            {
                Transform candidate = transforms[i];
                if (!candidate || (!IsUsv(candidate) && !IsUav(candidate)))
                    continue;
                if (NormalizeDeviceCode(candidate.name) == normalizedCode)
                    return candidate;
            }
            return null;
        }

        public bool TrySelectFirst(
            string kind,
            out string canonicalCode,
            out string profile,
            out string error)
        {
            RefreshSceneTargets(true);
            bool wantUav = string.Equals(kind, "UAV", StringComparison.OrdinalIgnoreCase);
            for (int i = 0; i < sceneTargets.Count; i++)
            {
                Transform candidate = sceneTargets[i];
                if (candidate && (wantUav ? IsUav(candidate) : IsUsv(candidate)))
                    return TrySelectDevice(candidate.name, out canonicalCode, out profile, out error);
            }

            canonicalCode = string.Empty;
            profile = string.Empty;
            error = "Unity scene has no " + kind + " device";
            return false;
        }

        public void SetOverview()
        {
            // Repeated bridge updates must not restart an in-flight pullback.
            if (mode == ObservationMode.Overview && interactionMode == ObservationMode.None)
                return;

            bool shouldTransition = mode != ObservationMode.None && hasRenderedObserverFrame;
            selectedSubject = null;
            CurrentDeviceCode = string.Empty;
            mode = ObservationMode.Overview;
            ResetCameraInteraction();
            overviewFrameInitialized = false;
            ActivateObserver();
            if (shouldTransition)
                BeginOverviewTransition();
            else
                CancelOverviewTransition();
            RefreshTrailVisibility();
        }

        public void SetLighthouse()
        {
            CancelOverviewTransition();
            RefreshSceneTargets(true);
            selectedSubject = null;
            CurrentDeviceCode = string.Empty;
            mode = ObservationMode.Lighthouse;
            ResetCameraInteraction();
            ActivateObserver();
        }

        public void ReleaseToOriginalCamera()
        {
            CancelOverviewTransition();
            mode = ObservationMode.None;
            ResetCameraInteraction();
            selectedSubject = null;
            CurrentDeviceCode = string.Empty;
            if (chaseCamera)
                chaseCamera.enabled = true;
            RefreshTrailVisibility();
        }

        public void SetMissionState(string state)
        {
            trailRecordingEnabled = false;
            DestroyFleetTrails();
            RefreshTrailVisibility();
        }

        public void ResetFleetTrails()
        {
            foreach (KeyValuePair<Transform, List<Vector3>> pair in fleetTrailPoints)
                pair.Value.Clear();
            initializedTrailTargets.Clear();

            foreach (KeyValuePair<Transform, LineRenderer> pair in fleetTrails)
            {
                if (!pair.Value)
                    continue;
                pair.Value.positionCount = 0;
                pair.Value.enabled = false;
            }

            trailRecordingEnabled = false;
            nextTrailSampleAt = Time.unscaledTime + TrailSampleSeconds;
            trailSampleLogged = false;
        }

        public bool RecenterCurrentDevice(out string error)
        {
            if (!selectedSubject)
            {
                error = "No Unity device is currently selected";
                return false;
            }

            CancelOverviewTransition();
            mode = ObservationMode.Device;
            ResetCameraInteraction();
            ActivateObserver();
            error = string.Empty;
            return true;
        }

        private void ActivateObserver()
        {
            if (!observedCamera)
                observedCamera = GetComponent<Camera>();
            if (!chaseCamera)
                chaseCamera = GetComponent<UavUsv.ChaseCamera>();
            if (chaseCamera)
                chaseCamera.enabled = false;
            var comparison = GetComponent<UavUsv.GazeboComparisonCamera>();
            if (comparison) comparison.comparisonActive = false;
            var orbit = GetComponent<UavUsv.OrbitCamera>();
            if (orbit) orbit.enabled = false;
        }

        private void LateUpdate()
        {
            if (mode == ObservationMode.None || !observedCamera)
                return;

            RefreshSceneTargets(false);
            UpdateFleetTrails();
            Vector3 desiredPosition;
            Vector3 focusPoint;

            if (mode == ObservationMode.Device && selectedSubject)
                CalculateDeviceView(out desiredPosition, out focusPoint);
            else if (mode == ObservationMode.Lighthouse)
                CalculateLighthouseView(out desiredPosition, out focusPoint);
            else
                CalculateOverview(out desiredPosition, out focusPoint);

            ApplyMouseCameraControl(ref desiredPosition, ref focusPoint);

            if (mode == ObservationMode.Device && selectedSubject)
                desiredPosition = ResolveDeviceOcclusion(focusPoint, desiredPosition);
            desiredPosition.y = Mathf.Max(desiredPosition.y, 2.4f);
            Quaternion desiredRotation = Quaternion.LookRotation(
                focusPoint - desiredPosition,
                Vector3.up
            );
            if (mode == ObservationMode.Overview && interactionMode == mode)
                desiredRotation = Quaternion.Euler(interactionPitch, interactionYaw, interactionRoll);
            float positionT = 1f - Mathf.Exp(-3.2f * Time.unscaledDeltaTime);
            float rotationT = 1f - Mathf.Exp(-4.8f * Time.unscaledDeltaTime);
            float fovT = 1f - Mathf.Exp(-3.5f * Time.unscaledDeltaTime);
            hasRenderedObserverFrame = true;
            if (mode == ObservationMode.Overview && interactionMode == ObservationMode.None)
            {
                if (ApplyOverviewTransition(
                        desiredPosition, desiredRotation, desiredFieldOfView, Time.unscaledTime))
                    return;
                // Overview already smooths its focus and distance together.
                // A second positional/rotational lag would break the fitted frustum.
                transform.SetPositionAndRotation(desiredPosition, desiredRotation);
                observedCamera.fieldOfView = desiredFieldOfView;
                return;
            }
            transform.position = Vector3.Lerp(transform.position, desiredPosition, positionT);
            transform.rotation = Quaternion.Slerp(transform.rotation, desiredRotation, rotationT);
            observedCamera.fieldOfView = Mathf.Lerp(
                observedCamera.fieldOfView,
                desiredFieldOfView,
                fovT
            );
        }

        private void BeginOverviewTransition()
        {
            if (!observedCamera || !hasRenderedObserverFrame)
                return;

            overviewTransitionStartPosition = transform.position;
            overviewTransitionStartRotation = transform.rotation;
            overviewTransitionStartFieldOfView = observedCamera.fieldOfView;
            overviewTransitionStartedAt = Time.unscaledTime;
            overviewTransitionActive = true;
        }

        private void CancelOverviewTransition()
        {
            overviewTransitionActive = false;
        }

        private bool ApplyOverviewTransition(
            Vector3 targetPosition,
            Quaternion targetRotation,
            float targetFieldOfView,
            float now)
        {
            if (!overviewTransitionActive || !observedCamera)
                return false;

            // Keep the start pose fixed, but refit the destination every frame:
            // moving craft and viewport resizes remain part of the overview.
            float progress = Mathf.Clamp01(
                (now - overviewTransitionStartedAt) / OverviewTransitionSeconds);
            float eased = progress * progress * (3f - 2f * progress);
            transform.SetPositionAndRotation(
                Vector3.Lerp(overviewTransitionStartPosition, targetPosition, eased),
                Quaternion.Slerp(overviewTransitionStartRotation, targetRotation, eased));
            observedCamera.fieldOfView = Mathf.Lerp(
                overviewTransitionStartFieldOfView, targetFieldOfView, eased);
            if (progress >= 1f)
                CancelOverviewTransition();
            return true;
        }

        private void ApplyMouseCameraControl(
            ref Vector3 desiredPosition,
            ref Vector3 focusPoint)
        {
            if (mode != ObservationMode.Overview && mode != ObservationMode.Device)
                return;

            bool pointerInside = Input.mousePosition.x >= 0f &&
                                 Input.mousePosition.y >= 0f &&
                                 Input.mousePosition.x <= Screen.width &&
                                 Input.mousePosition.y <= Screen.height;
            if (!pointerInside)
                return;

            bool reset = false;
            if (Input.GetMouseButtonDown(0))
            {
                float now = Time.unscaledTime;
                reset = now - lastPrimaryClickAt <= .32f;
                lastPrimaryClickAt = now;
            }
            if (reset)
            {
                ResetCameraInteractionToAutomatic();
                return;
            }

            float scroll = Mathf.Clamp(Input.mouseScrollDelta.y, -1f, 1f);
            bool interactionRequested = Input.GetMouseButton(1) ||
                                        Input.GetMouseButton(2) ||
                                        (Input.GetMouseButton(0) &&
                                         !Input.GetMouseButtonDown(0)) ||
                                        Mathf.Abs(scroll) > .001f;
            if (interactionRequested)
                lastInteractionAt = Time.unscaledTime;
            else if (interactionMode == mode &&
                     Time.unscaledTime - lastInteractionAt >= 2.5f)
            {
                // Manual inspection is temporary. Smoothly hand control back
                // to mission framing so a later threat manoeuvre cannot leave
                // active craft outside the WebGL viewport.
                ResetCameraInteractionToAutomatic();
                return;
            }
            if (interactionMode != mode)
            {
                if (!interactionRequested)
                    return;
                if (overviewTransitionActive)
                    InitializeInteractionFromCurrentView(focusPoint);
                else
                    InitializeCameraInteraction(desiredPosition, focusPoint);
            }

            if (Input.GetMouseButton(1))
            {
                interactionYaw += Input.GetAxis("Mouse X") * 2.2f;
                interactionPitch = Mathf.Clamp(
                    interactionPitch - Input.GetAxis("Mouse Y") * 1.8f,
                    18f,
                    82f
                );
            }

            if (Input.GetMouseButton(2) ||
                (Input.GetMouseButton(0) && !Input.GetMouseButtonDown(0)))
            {
                Quaternion orbit = Quaternion.Euler(
                    interactionPitch,
                    interactionYaw,
                    interactionRoll
                );
                Vector3 right = orbit * Vector3.right;
                Vector3 forward = orbit * Vector3.forward;
                right.y = 0f;
                forward.y = 0f;
                if (right.sqrMagnitude > .001f) right.Normalize();
                if (forward.sqrMagnitude > .001f) forward.Normalize();
                float scale = Mathf.Max(.012f, interactionDistance * .0018f);
                interactionFocusOffset -= right * Input.GetAxis("Mouse X") * scale;
                interactionFocusOffset -= forward * Input.GetAxis("Mouse Y") * scale;
            }

            if (Mathf.Abs(scroll) > .001f)
            {
                Vector3 currentFocus = focusPoint + interactionFocusOffset;
                Quaternion orbit = Quaternion.Euler(
                    interactionPitch,
                    interactionYaw,
                    interactionRoll
                );
                Vector3 currentPosition = currentFocus -
                                          orbit * Vector3.forward * interactionDistance;
                bool hasBefore = TryPointerPlanePoint(
                    currentPosition,
                    orbit,
                    currentFocus.y,
                    out Vector3 before
                );

                float zoomStep = scroll < 0f ? .030f : .045f;
                interactionDistance = Mathf.Clamp(
                    interactionDistance * Mathf.Exp(-scroll * zoomStep),
                    mode == ObservationMode.Device ? 3.5f : 8f,
                    mode == ObservationMode.Device ? 90f : 360f
                );
                Vector3 nextPosition = currentFocus -
                                       orbit * Vector3.forward * interactionDistance;
                if (hasBefore && TryPointerPlanePoint(
                        nextPosition,
                        orbit,
                        currentFocus.y,
                        out Vector3 after))
                {
                    Vector3 correction = before - after;
                    correction.y = 0f;
                    interactionFocusOffset += correction;
                }
            }

            focusPoint += interactionFocusOffset;
            Quaternion controlledRotation = Quaternion.Euler(
                interactionPitch,
                interactionYaw,
                interactionRoll
            );
            desiredPosition = focusPoint -
                              controlledRotation * Vector3.forward * interactionDistance;
        }

        private void InitializeCameraInteraction(Vector3 position, Vector3 focus)
        {
            Vector3 offset = position - focus;
            interactionDistance = Mathf.Max(1f, offset.magnitude);
            Quaternion look = Quaternion.LookRotation(focus - position, Vector3.up);
            interactionYaw = look.eulerAngles.y;
            interactionPitch = NormalizeSignedAngle(look.eulerAngles.x);
            interactionPitch = Mathf.Clamp(interactionPitch, 18f, 82f);
            interactionRoll = 0f;
            interactionFocusOffset = Vector3.zero;
            interactionMode = mode;
        }

        private void InitializeInteractionFromCurrentView(Vector3 baseFocus)
        {
            // A drag during the pullback takes over the visible pose, not the
            // destination pose that CalculateOverview has already calculated.
            Ray viewRay = new Ray(transform.position, transform.forward);
            Plane focusPlane = new Plane(Vector3.up, baseFocus);
            float distance = Vector3.Distance(transform.position, baseFocus);
            if (focusPlane.Raycast(viewRay, out float enter) && enter > 0f)
                distance = enter;
            Vector3 actualFocus = viewRay.GetPoint(Mathf.Max(1f, distance));
            InitializeCameraInteraction(transform.position, actualFocus);
            // Quaternion interpolation can contain a small transient roll.
            // Preserve it at handoff rather than silently levelling the view.
            interactionRoll = NormalizeSignedAngle(transform.eulerAngles.z);
            interactionFocusOffset = actualFocus - baseFocus;
            CancelOverviewTransition();
        }

        private bool TryPointerPlanePoint(
            Vector3 cameraPosition,
            Quaternion cameraRotation,
            float planeY,
            out Vector3 point)
        {
            point = Vector3.zero;
            if (!observedCamera)
                return false;

            Vector3 savedPosition = transform.position;
            Quaternion savedRotation = transform.rotation;
            transform.SetPositionAndRotation(cameraPosition, cameraRotation);
            Ray ray = observedCamera.ScreenPointToRay(Input.mousePosition);
            transform.SetPositionAndRotation(savedPosition, savedRotation);
            Plane plane = new Plane(Vector3.up, new Vector3(0f, planeY, 0f));
            if (!plane.Raycast(ray, out float enter) || enter < 0f)
                return false;
            point = ray.GetPoint(enter);
            return true;
        }

        private void ResetCameraInteraction()
        {
            interactionMode = ObservationMode.None;
            interactionFocusOffset = Vector3.zero;
            interactionDistance = 0f;
            interactionRoll = 0f;
        }

        private void ResetCameraInteractionToAutomatic()
        {
            bool hadManualView = interactionMode != ObservationMode.None;
            ResetCameraInteraction();
            if (mode == ObservationMode.Overview && hadManualView)
                BeginOverviewTransition();
        }

        private static float NormalizeSignedAngle(float angle)
        {
            angle %= 360f;
            return angle > 180f ? angle - 360f : angle;
        }

        private void CalculateDeviceView(out Vector3 position, out Vector3 focus)
        {
            Vector3 forward = DeviceForward(selectedSubject);
            Vector3 subject = selectedSubject.position;
            GetVisualMetrics(
                selectedSubject,
                out float visualRadius,
                out float visualTop,
                out float visualCenterHeight
            );
            if (IsUav(selectedSubject))
            {
                float height = Mathf.Clamp(
                    Mathf.Max(visualTop + 5f, 6f),
                    6f,
                    14f
                );
                float back = Mathf.Clamp(
                    Mathf.Max(visualRadius * 3.2f, 8f),
                    8f,
                    14f
                );
                position = subject - forward * back + Vector3.up * height;
                focus = subject +
                    forward * Mathf.Max(2f, visualRadius * 1.5f) +
                    Vector3.up * Mathf.Max(.8f, visualCenterHeight);
                desiredFieldOfView = 45f;
                return;
            }

            float distance = Mathf.Clamp(
                Mathf.Max(6f, visualRadius * 4f),
                6f,
                10f
            );
            float heightUsv = Mathf.Clamp(
                Mathf.Max(visualTop + 5f, 6f),
                6f,
                10f
            );
            focus = subject + Vector3.up * Mathf.Max(.35f, visualCenterHeight);
            position = focus - forward * distance + Vector3.up * heightUsv;
            desiredFieldOfView = 45f;
        }

        private Vector3 ResolveDeviceOcclusion(Vector3 focus, Vector3 desiredPosition)
        {
            Vector3 offset = desiredPosition - focus;
            float distance = offset.magnitude;
            if (distance < .1f)
                return desiredPosition;

            Vector3 direction = offset / distance;
            int hitCount = Physics.SphereCastNonAlloc(
                focus,
                .3f,
                direction,
                occlusionHits,
                distance,
                ~0,
                QueryTriggerInteraction.Ignore
            );
            float nearestObstacle = distance;
            for (int i = 0; i < hitCount; i++)
            {
                Transform hitTransform = occlusionHits[i].transform;
                if (!hitTransform || IsSelectedSubjectPart(hitTransform))
                    continue;
                nearestObstacle = Mathf.Min(nearestObstacle, occlusionHits[i].distance);
            }

            if (nearestObstacle >= distance)
                return desiredPosition;
            if (nearestObstacle <= 1.2f)
                return focus + Vector3.up * 8f - direction * 1.5f;

            return focus + direction * Mathf.Max(1.2f, nearestObstacle - .6f);
        }

        private bool IsSelectedSubjectPart(Transform candidate)
        {
            return selectedSubject &&
                (candidate == selectedSubject || candidate.IsChildOf(selectedSubject));
        }

        private static void GetVisualMetrics(
            Transform subject,
            out float radius,
            out float top,
            out float centerHeight)
        {
            radius = 0f;
            top = 0f;
            centerHeight = 0f;
            if (!subject)
                return;

            Renderer[] renderers = subject.GetComponentsInChildren<Renderer>(false);
            Bounds bounds = default;
            bool found = false;
            for (int i = 0; i < renderers.Length; i++)
            {
                Renderer renderer = renderers[i];
                if (!renderer || !renderer.enabled ||
                    !renderer.gameObject.activeInHierarchy)
                    continue;

                if (!found)
                {
                    bounds = renderer.bounds;
                    found = true;
                }
                else
                {
                    bounds.Encapsulate(renderer.bounds);
                }
            }

            if (!found)
                return;

            radius = Mathf.Max(bounds.extents.x, bounds.extents.z);
            top = Mathf.Max(0f, bounds.max.y - subject.position.y);
            centerHeight = Mathf.Max(0f, bounds.center.y - subject.position.y);
        }

        private readonly Dictionary<Transform, Renderer[]> framingRenderers = new Dictionary<Transform, Renderer[]>();
        private readonly List<Vector3> framingPoints = new List<Vector3>();

        private Bounds VisibleBounds(Transform subject)
        {
            if (!framingRenderers.TryGetValue(subject, out Renderer[] renderers))
            {
                renderers = subject.GetComponentsInChildren<Renderer>(false);
                framingRenderers[subject] = renderers;
            }
            Bounds bounds = new Bounds(subject.position, Vector3.one);
            bool found = false;
            foreach (Renderer renderer in renderers)
            {
                if (!renderer || !renderer.enabled || !renderer.gameObject.activeInHierarchy ||
                    renderer is LineRenderer || renderer is ParticleSystemRenderer) continue;
                if (!found) { bounds = renderer.bounds; found = true; }
                else bounds.Encapsulate(renderer.bounds);
            }
            return bounds;
        }

        private void CalculateOverview(out Vector3 position, out Vector3 focus)
        {
            // Fit every visible hull in camera coordinates, including altitude
            // and perspective depth. No percentile exclusion or far-distance cap.
            const float fov = 52f;
            Quaternion rotation = Quaternion.Euler(60f, -35f, 0f);
            Quaternion inverse = Quaternion.Inverse(rotation);
            framingPoints.Clear();
            Vector3 minimum = Vector3.one * float.PositiveInfinity;
            Vector3 maximum = Vector3.one * float.NegativeInfinity;
            foreach (Transform subject in sceneTargets)
            {
                if (!subject || !subject.gameObject.activeInHierarchy) continue;
                Bounds bounds = VisibleBounds(subject);
                for (int corner = 0; corner < 8; corner++)
                {
                    Vector3 point = bounds.center + Vector3.Scale(bounds.extents, new Vector3(
                        (corner & 1) == 0 ? -1f : 1f,
                        (corner & 2) == 0 ? -1f : 1f,
                        (corner & 4) == 0 ? -1f : 1f));
                    Vector3 projected = inverse * point;
                    minimum = Vector3.Min(minimum, projected);
                    maximum = Vector3.Max(maximum, projected);
                    framingPoints.Add(point);
                }
            }
            Vector3 rawFocus = framingPoints.Count == 0 ? Vector3.zero : rotation * ((minimum + maximum) * .5f);
            if (!overviewFrameInitialized) overviewFocus = rawFocus;
            else overviewFocus = Vector3.Lerp(overviewFocus, rawFocus, 1f-Mathf.Exp(-3f*Time.unscaledDeltaTime));

            float tanY = Mathf.Tan(fov * Mathf.Deg2Rad * .5f);
            float tanX = tanY * Mathf.Max(.1f, observedCamera.aspect);
            float required = 20f;
            foreach (Vector3 point in framingPoints)
            {
                Vector3 local = inverse * (point-overviewFocus);
                // Use binary overloads: the params overload allocates an array
                // for every corner of every vessel on every render frame.
                required = Mathf.Max(required, Mathf.Abs(local.x)/(tanX*.88f)-local.z);
                required = Mathf.Max(required, Mathf.Abs(local.y)/(tanY*.84f)-local.z);
                required = Mathf.Max(required, observedCamera.nearClipPlane+1f-local.z);
            }
            // Expansion is a visibility constraint; contraction is damped.
            // Normal motion changes the fitted distance continuously. Only a
            // newly generated scenario needs a one-time reframing jump.
            if (!overviewFrameInitialized || required > overviewDistance)
            {
                overviewDistance = required;
                overviewContractionStartedAt = -1f;
            }
            else if (required < overviewDistance*.94f)
            {
                // Wait for a persistent reduction before zooming in. Otherwise
                // alternating near/far ring assignments make the view breathe.
                if (overviewContractionStartedAt < 0f)
                    overviewContractionStartedAt = Time.unscaledTime;
                if (Time.unscaledTime - overviewContractionStartedAt >= .65f)
                    overviewDistance = Mathf.Lerp(overviewDistance, required,
                        1f-Mathf.Exp(-1.4f*Time.unscaledDeltaTime));
            }
            else overviewContractionStartedAt = -1f;
            overviewFrameInitialized = true;
            focus = overviewFocus;
            position = focus-rotation*Vector3.forward*overviewDistance;
            desiredFieldOfView = fov;
        }

        [Serializable]
        public sealed class FramingReport
        {
            public string controller;
            public int deviceCount;
            public int outsideCount;
            public float minimumX, maximumX, minimumY, maximumY, minimumDevicePixels;
            public float cameraX, cameraY, cameraZ, focusX, focusY, focusZ;
            public float aspect;
            public string revision;
        }

        public FramingReport GetFramingReport()
        {
            var report = new FramingReport { controller = CurrentModeName, revision = "full-hull-20260909",
                minimumX = 1f, minimumY = 1f, maximumX = 0f, maximumY = 0f,
                minimumDevicePixels = float.MaxValue };
            if (!observedCamera) return report;
            report.aspect = observedCamera.aspect;
            Vector3 cp = observedCamera.transform.position;
            report.cameraX=cp.x; report.cameraY=cp.y; report.cameraZ=cp.z;
            report.focusX=overviewFocus.x; report.focusY=overviewFocus.y; report.focusZ=overviewFocus.z;
            foreach (Transform subject in sceneTargets)
            {
                if (!subject || !subject.gameObject.activeInHierarchy) continue;
                report.deviceCount++;
                Bounds bounds = VisibleBounds(subject);
                Vector2 lo = Vector2.one * float.PositiveInfinity, hi = Vector2.one * float.NegativeInfinity;
                bool outside = false;
                for (int corner=0;corner<8;corner++)
                {
                    Vector3 point = bounds.center + Vector3.Scale(bounds.extents,new Vector3(
                        (corner&1)==0?-1:1, (corner&2)==0?-1:1, (corner&4)==0?-1:1));
                    Vector3 v = observedCamera.WorldToViewportPoint(point);
                    outside |= v.z <= 0 || v.x < 0 || v.x > 1 || v.y < 0 || v.y > 1;
                    lo=Vector2.Min(lo,new Vector2(v.x,v.y)); hi=Vector2.Max(hi,new Vector2(v.x,v.y));
                }
                if (outside) report.outsideCount++;
                report.minimumX=Mathf.Min(report.minimumX,lo.x); report.maximumX=Mathf.Max(report.maximumX,hi.x);
                report.minimumY=Mathf.Min(report.minimumY,lo.y); report.maximumY=Mathf.Max(report.maximumY,hi.y);
                report.minimumDevicePixels=Mathf.Min(report.minimumDevicePixels,
                    Mathf.Max((hi.x-lo.x)*observedCamera.pixelWidth,(hi.y-lo.y)*observedCamera.pixelHeight));
            }
            return report;
        }

        private void CalculateLighthouseView(out Vector3 position, out Vector3 focus)
        {
            Vector3 groupCenter;
            float spread;
            CalculateGroupFrame(out groupCenter, out spread);
            if (!lighthouse)
            {
                CalculateOverview(out position, out focus);
                return;
            }

            Vector3 outward = lighthouse.position - groupCenter;
            outward.y = 0f;
            if (outward.sqrMagnitude < .01f)
                outward = Vector3.back;
            outward.Normalize();
            position = lighthouse.position + outward * 12f + Vector3.up * 24f;
            focus = Vector3.Lerp(lighthouse.position, groupCenter, .8f) + Vector3.up * 2f;
            desiredFieldOfView = Mathf.Clamp(56f + spread * .12f, 56f, 70f);
        }

        private void CalculateGroupFrame(out Vector3 center, out float spread)
        {
            center = Vector3.zero;
            int count = 0;
            for (int i = 0; i < sceneTargets.Count; i++)
            {
                Transform item = sceneTargets[i];
                if (!item)
                    continue;
                center += item.position;
                count++;
            }

            if (count == 0)
            {
                center = selectedSubject ? selectedSubject.position : Vector3.zero;
                spread = 1f;
                return;
            }

            center /= count;
            spread = 1f;
            for (int i = 0; i < sceneTargets.Count; i++)
            {
                Transform item = sceneTargets[i];
                if (!item)
                    continue;
                Vector3 delta = item.position - center;
                delta.y *= .35f;
                spread = Mathf.Max(spread, delta.magnitude);
            }
        }

        private void RefreshSceneTargets(bool force = false)
        {
            if (!force && Time.unscaledTime < nextSceneTargetRefreshAt)
                return;
            nextSceneTargetRefreshAt = Time.unscaledTime + .10f;
            sceneTargets.Clear();
            if (framingRenderers.Count > 128) framingRenderers.Clear();
            if (!fleetManager)
                fleetManager = FindObjectOfType<UavUsv.VirtualFleetManager>();

            if (!chaseCamera)
                chaseCamera = GetComponent<UavUsv.ChaseCamera>();
            if (chaseCamera)
            {
                lighthouse = chaseCamera.lookAt;
                Transform[] targets = chaseCamera.groupTargets;
                if (targets != null)
                {
                    for (int i = 0; i < targets.Length; i++)
                    {
                        // The virtual fleet scene shares the legacy camera
                        // director with the demo scene. Once a fleet manager
                        // exists, only fleet devices belong in the overview
                        // bounds; legacy mission ships can be far away.
                        if (!fleetManager &&
                            targets[i] &&
                            !sceneTargets.Contains(targets[i]))
                            sceneTargets.Add(targets[i]);
                    }
                }
            }

            if (!fleetManager)
                return;

            AddFleetTargets(fleetManager.GetUavTransforms());
            AddFleetTargets(fleetManager.GetUsvTransforms());
            // Include only the current run's registered mission vessels. In
            // escort this keeps the protected ship and its close guards visible;
            // capture includes its target. No pads, berths or legacy ships.
            AddFleetTargets(fleetManager.GetTargetTransforms());
            if (TrajectoryRenderingEnabled)
                SyncFleetTrails();
            else
                DestroyFleetTrails();
        }

        private void AddFleetTargets(Transform[] targets)
        {
            if (targets == null)
                return;
            for (int i = 0; i < targets.Length; i++)
            {
                Transform target = targets[i];
                if (target && !sceneTargets.Contains(target))
                    sceneTargets.Add(target);
            }
        }

        private void SyncFleetTrails()
        {
            if (!TrajectoryRenderingEnabled)
            {
                DestroyFleetTrails();
                return;
            }
            if (!trailRoot)
            {
                GameObject root = new GameObject("VirtualFleetTrails");
                trailRoot = root.transform;
            }

            HashSet<Transform> activeTargets = new HashSet<Transform>();
            for (int i = 0; i < sceneTargets.Count; i++)
            {
                Transform target = sceneTargets[i];
                if (!target || (!IsUav(target) && !IsUsv(target)))
                    continue;

                activeTargets.Add(target);
                if (!fleetTrails.ContainsKey(target))
                {
                    GameObject trailObject = new GameObject(target.name + "-Trajectory");
                    trailObject.transform.SetParent(trailRoot, false);
                    LineRenderer line = trailObject.AddComponent<LineRenderer>();
                    line.useWorldSpace = true;
                    line.alignment = LineAlignment.View;
                    line.textureMode = LineTextureMode.Stretch;
                    line.numCapVertices = 3;
                    line.numCornerVertices = 3;
                    bool isUav = IsUav(target);
                    Color trailColor = isUav
                        ? new Color(.2f, .88f, 1f, .72f)
                        : new Color(1f, .58f, .08f, .72f);
                    line.widthMultiplier = isUav ? .10f : .12f;
                    line.sharedMaterial = GetTrailMaterial(isUav);
                    line.startColor = line.endColor = trailColor;
                    line.shadowCastingMode =
                        UnityEngine.Rendering.ShadowCastingMode.Off;
                    line.receiveShadows = false;
                    fleetTrails.Add(target, line);
                    fleetTrailPoints.Add(target, new List<Vector3>(MaxTrailPoints));
                }
            }

            List<Transform> staleTargets = new List<Transform>();
            foreach (KeyValuePair<Transform, LineRenderer> pair in fleetTrails)
            {
                if (!pair.Key || !activeTargets.Contains(pair.Key))
                    staleTargets.Add(pair.Key);
            }

            for (int i = 0; i < staleTargets.Count; i++)
            {
                Transform stale = staleTargets[i];
                if (fleetTrails.TryGetValue(stale, out LineRenderer line) && line)
                    Destroy(line.gameObject);
                fleetTrails.Remove(stale);
                fleetTrailPoints.Remove(stale);
                initializedTrailTargets.Remove(stale);
            }

            if (fleetTrails.Count != lastTrailDeviceCount)
            {
                lastTrailDeviceCount = fleetTrails.Count;
                Debug.Log(
                    "[WebDeviceObserverCamera] VirtualFleetTrails refreshed: " +
                    fleetTrails.Count + " devices."
                );
            }
        }

        private Material GetTrailMaterial(bool isUav)
        {
            Material material = isUav
                ? uavTrailMaterial
                : usvTrailMaterial;
            if (material)
                return material;

            Color trailColor = isUav
                ? new Color(.2f, .88f, 1f, .95f)
                : new Color(1f, .58f, .08f, .95f);
            material = SceneFactory.Material(
                "VirtualFleetTrajectoryMaterial-" + (isUav ? "UAV" : "USV"),
                trailColor,
                0f,
                .5f
            );
            if (isUav)
                uavTrailMaterial = material;
            else
                usvTrailMaterial = material;
            return material;
        }

        private void UpdateFleetTrails()
        {
            if (!TrajectoryRenderingEnabled)
                return;
            if (Time.unscaledTime < nextTrailSampleAt)
                return;
            nextTrailSampleAt = Time.unscaledTime + TrailSampleSeconds;

            foreach (KeyValuePair<Transform, LineRenderer> pair in fleetTrails)
            {
                Transform target = pair.Key;
                LineRenderer line = pair.Value;
                if (!target || !line || !fleetTrailPoints.TryGetValue(target, out List<Vector3> points))
                    continue;

                if (!trailRecordingEnabled)
                {
                    line.enabled = mode == ObservationMode.Overview && points.Count > 1;
                    continue;
                }

                Vector3 point = target.position + Vector3.up * (IsUav(target) ? .08f : .18f);
                if (!initializedTrailTargets.Contains(target))
                {
                    points.Clear();
                    points.Add(point);
                    line.positionCount = 1;
                    line.SetPosition(0, point);
                    line.enabled = false;
                    initializedTrailTargets.Add(target);
                    continue;
                }

                float distanceFromLastPoint = points.Count == 0
                    ? 0f
                    : Vector3.Distance(points[points.Count - 1], point);
                if (distanceFromLastPoint > TrailMaxJumpDistance)
                {
                    // A first algorithm frame can be far from the generated
                    // spawn position. Treat that jump as a new origin instead
                    // of drawing a long line across the scene.
                    points.Clear();
                    points.Add(point);
                    line.positionCount = 1;
                    line.SetPosition(0, point);
                    line.enabled = false;
                    continue;
                }

                if (points.Count == 0 ||
                    distanceFromLastPoint >= TrailMinDistance)
                {
                    points.Add(point);
                    if (points.Count > MaxTrailPoints)
                    {
                        points.RemoveAt(0);
                        line.positionCount = points.Count;
                        line.SetPositions(points.ToArray());
                    }
                    else
                    {
                        line.positionCount = points.Count;
                        line.SetPosition(points.Count - 1, point);
                    }

                    if (!trailSampleLogged && points.Count >= 2)
                    {
                        trailSampleLogged = true;
                        Debug.Log(
                            "[WebDeviceObserverCamera] Virtual fleet trajectory " +
                            "sampled: " + target.name +
                            " points=" + points.Count +
                            " mode=" + CurrentModeName
                        );
                    }
                }

                line.enabled = mode == ObservationMode.Overview && points.Count > 1;
            }
        }

        private void RefreshTrailVisibility()
        {
            foreach (KeyValuePair<Transform, LineRenderer> pair in fleetTrails)
            {
                if (!pair.Value)
                    continue;
                List<Vector3> points;
                fleetTrailPoints.TryGetValue(pair.Key, out points);
                pair.Value.enabled = TrajectoryRenderingEnabled &&
                    mode == ObservationMode.Overview &&
                    points != null &&
                    points.Count > 1;
            }
        }

        private void DestroyFleetTrails()
        {
            foreach (KeyValuePair<Transform, LineRenderer> pair in fleetTrails)
                if (pair.Value)
                    Destroy(pair.Value.gameObject);
            fleetTrails.Clear();
            fleetTrailPoints.Clear();
            initializedTrailTargets.Clear();
            lastTrailDeviceCount = 0;
            if (trailRoot)
                Destroy(trailRoot.gameObject);
            trailRoot = null;
        }

        private static Vector3 DeviceForward(Transform subject)
        {
            Vector3 forward = IsUsv(subject) ? subject.right : subject.forward;
            forward.y = 0f;
            return forward.sqrMagnitude > .001f ? forward.normalized : Vector3.forward;
        }

        private static bool IsUsv(Transform subject)
        {
            if (!subject)
                return false;
            string name = subject.name;
            return name.StartsWith("USV-", StringComparison.OrdinalIgnoreCase) ||
                   name.StartsWith("usv_", StringComparison.OrdinalIgnoreCase);
        }

        private static bool IsUav(Transform subject)
        {
            if (!subject)
                return false;
            string name = subject.name;
            return name.StartsWith("UAV-", StringComparison.OrdinalIgnoreCase) ||
                   name.StartsWith("uav_", StringComparison.OrdinalIgnoreCase);
        }

        public static string NormalizeDeviceCode(string value)
        {
            if (string.IsNullOrWhiteSpace(value))
                return string.Empty;
            string upper = value.Trim().ToUpperInvariant().Replace("_", "-");
            string prefix = upper.StartsWith("UAV") ? "UAV" : upper.StartsWith("USV") ? "USV" : string.Empty;
            if (string.IsNullOrEmpty(prefix))
                return upper;
            string digits = string.Empty;
            for (int i = prefix.Length; i < upper.Length; i++)
            {
                if (char.IsDigit(upper[i]))
                    digits += upper[i];
            }
            if (!int.TryParse(digits, out int index))
                return upper;
            return prefix + "-" + index.ToString("000");
        }

        private static string CanonicalDeviceCode(Transform subject)
        {
            return NormalizeDeviceCode(subject ? subject.name : string.Empty);
        }

        private static string ModeName(ObservationMode value)
        {
            switch (value)
            {
                case ObservationMode.Device: return "device-follow";
                case ObservationMode.Overview: return "overview";
                case ObservationMode.Lighthouse: return "lighthouse";
                default: return "action";
            }
        }

        private void OnDisable()
        {
            CancelOverviewTransition();
            hasRenderedObserverFrame = false;
        }

        private void OnDestroy()
        {
            CancelOverviewTransition();
            if (chaseCamera)
                chaseCamera.enabled = true;
            if (uavTrailMaterial)
                Destroy(uavTrailMaterial);
            if (usvTrailMaterial)
                Destroy(usvTrailMaterial);
            if (trailRoot)
                Destroy(trailRoot.gameObject);
        }
    }
}
