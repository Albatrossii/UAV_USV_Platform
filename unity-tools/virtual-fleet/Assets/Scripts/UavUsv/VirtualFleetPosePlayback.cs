using System;
using System.Collections.Generic;
using UnityEngine;

namespace UavUsv
{
    /// <summary>
    /// Presentation-only, timestamped interpolation. Never predicts a new pose,
    /// modifies algorithm state, or infers a teleport from a phase/distance change.
    /// </summary>
    public sealed class VirtualFleetPosePlayback
    {
        public const double DelayMs = 180.0;
        public const double StaleSeconds = 1.5;
        private const double RecoveryGapSeconds = .35;
        private const double RecoverySeconds = .45;
        private const int MaximumSamples = 32;

        private struct Sample
        {
            public double time;
            public Vector3 position;
            public Quaternion rotation;
        }

        private readonly List<Sample> samples = new List<Sample>();
        private double lastArrival;
        private double lastSource;
        private double lastRenderedSource = double.NegativeInfinity;
        private double recoveryStartedAt = double.NegativeInfinity;
        private double recoveryClockOffset;

        public bool IsStale(double now) => samples.Count > 0 && now - lastArrival > StaleSeconds;
        public bool IsRecovering(double now) => now - recoveryStartedAt < RecoverySeconds;

        public void Clear()
        {
            samples.Clear();
            lastSource = 0;
            lastRenderedSource = double.NegativeInfinity;
            recoveryStartedAt = double.NegativeInfinity;
            recoveryClockOffset = 0;
        }

        public bool Add(long sampleTime, Vector3 position, Quaternion rotation,
            double now, Vector3 visiblePosition, Quaternion visibleRotation)
        {
            double source = sampleTime > 0 ? sampleTime : Math.Max(now * 1000.0, lastSource + .001);
            if (samples.Count > 0 && source <= lastSource)
                return false;

            bool recovering = samples.Count > 0 &&
                (now - lastArrival > RecoveryGapSeconds ||
                 source - lastSource > RecoveryGapSeconds * 1000.0);
            if (samples.Count == 0 || recovering)
            {
                // The synthetic point is only the already displayed pose. A
                // missing segment is linearly reconciled, never spline-extrapolated.
                // Explicit spawn/reset is handled separately by the manager.
                samples.Clear();
                double span = recovering ? RecoverySeconds * 1000.0 : DelayMs;
                samples.Add(new Sample { time = source - span,
                    position = visiblePosition, rotation = visibleRotation });
                lastRenderedSource = source - span;
                recoveryClockOffset = span - DelayMs;
                recoveryStartedAt = recovering ? now : double.NegativeInfinity;
            }
            samples.Add(new Sample { time = source, position = position, rotation = rotation });
            while (samples.Count > MaximumSamples) samples.RemoveAt(0);
            lastSource = source;
            lastArrival = now;
            return true;
        }

        public bool Evaluate(double now, out Vector3 position, out Quaternion rotation)
        {
            position = Vector3.zero;
            rotation = Quaternion.identity;
            if (samples.Count == 0) return false;
            double source = lastSource + Math.Max(0, now - lastArrival) * 1000.0 - DelayMs;
            if (IsRecovering(now))
            {
                double fraction = Math.Max(0, Math.Min(1, (now - recoveryStartedAt) / RecoverySeconds));
                double eased = fraction * fraction * (3 - 2 * fraction);
                source -= recoveryClockOffset * (1 - eased);
            }
            // Stop exactly at the last received sample if the stream stalls.
            // Source time cannot rewind when arrival jitter changes its estimate.
            source = Math.Min(lastSource, Math.Max(lastRenderedSource, source));
            lastRenderedSource = source;
            while (samples.Count > 2 && samples[1].time <= source) samples.RemoveAt(0);
            Sample from = samples[0];
            Sample to = samples[samples.Count > 1 ? 1 : 0];
            float t = to.time > from.time
                ? Mathf.Clamp01((float)((source - from.time) / (to.time - from.time))) : 1f;
            position = Vector3.Lerp(from.position, to.position, t);
            rotation = Quaternion.Slerp(from.rotation, to.rotation, t);
            return true;
        }
    }
}
