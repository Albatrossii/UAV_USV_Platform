#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.Reflection;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

namespace UavUsv.Editor.Tools
{
    /// <summary>Read-only scene-asset survey; no saved scene or player build.</summary>
    public static class ExistingShorelineProbe
    {
        public static void Run()
        {
            var owner = new GameObject("ExistingShorelineProbe");
            GameObject terrain = CatalinaIslandRuntime.CreateVisualTerrain(Coordinates.ToPresentationEnvironment(0f, 0f, -.8f), .18f);
            if (!terrain) throw new InvalidOperationException("Catalina resource missing");
            terrain.transform.SetParent(owner.transform, true);
            try
            {
                foreach (MeshFilter filter in terrain.GetComponentsInChildren<MeshFilter>(true))
                {
                    if (!filter.sharedMesh) continue;
                    MeshCollider collider = filter.GetComponent<MeshCollider>();
                    if (!collider) collider = filter.gameObject.AddComponent<MeshCollider>();
                    collider.sharedMesh = filter.sharedMesh;
                }
                Physics.SyncTransforms();
                Collider[] colliders = terrain.GetComponentsInChildren<Collider>();
                Renderer[] original = BuildOriginalBases(owner.transform);
                for (int east = -230; east <= -40; east += 10)
                {
                    float firstLand = float.NaN;
                    for (int north = -420; north <= -110; north += 2)
                        if (Land(colliders, east, north)) { firstLand = north; break; }
                    if (float.IsNaN(firstLand)) { Debug.Log("[ExistingShorelineProbe] east=" + east + " no south land found"); continue; }
                    float slotNorth = firstLand - 14f;
                    Vector2 approach = new Vector2(-240f, slotNorth - 30f);
                    bool clear = SegmentClear(colliders, approach, new Vector2(east, slotNorth), 6f);
                    Debug.Log("[ExistingShorelineProbe] east=" + east + " firstLandNorth=" + firstLand
                        + " proposedSlotNorth=" + slotNorth + " approach=" + approach + " width12mRouteClear=" + clear);
                }
                for (int north = -190; north <= -120; north += 10)
                {
                    float firstLand = float.NaN;
                    for (int east = -230; east <= -20; east += 2)
                        if (Land(colliders, east, north)) { firstLand = east; break; }
                    if (float.IsNaN(firstLand)) continue;
                    var slot = new Vector2(firstLand - 14f, north);
                    var approach = new Vector2(-240f, north - 30f);
                    bool clear = SegmentClear(colliders, approach, slot, 6f) && StaticSegmentClear(original, approach, slot, 6f);
                    Debug.Log("[ExistingShorelineProbe] WEST north=" + north + " firstLandEast=" + firstLand
                        + " slot=" + slot + " approach=" + approach + " terrainAndBaseWidth12mClear=" + clear);
                }
                for (int pad = 0; pad < 3; pad++)
                {
                    float local = -14f + pad * 14f;
                    Vector2 to = new Vector2(-75f + local * Mathf.Cos(.559f), -215f + local * Mathf.Sin(.559f));
                    float maximum = 0f;
                    foreach (float east in new[] { -510f, -360f, -210f })
                        foreach (float north in new[] { -435f, -285f, -135f })
                            maximum = Mathf.Max(maximum, MaximumRouteHeight(colliders, original, new Vector2(east, north), to, 5f));
                    Debug.Log("[ExistingShorelineProbe] UAV pad=" + (pad + 1) + " maximumTerrainAndStructureEnu=" + maximum
                        + " cruise39.65Clearance=" + (39.65f - maximum));
                }
            }
            finally { Object.DestroyImmediate(owner); }
        }

        public static Renderer[] BuildOriginalBases(Transform parent)
        {
            var pads = (Transform[])typeof(SimulationBootstrap).GetMethod("BuildIslandUavBase", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            pads[0].parent.SetParent(parent, true);
            var shore = (GameObject)typeof(SimulationBootstrap).GetMethod("BuildShoreCommandBase", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            shore.transform.SetParent(parent, true);
            var result = new List<Renderer>();
            result.AddRange(pads[0].parent.GetComponentsInChildren<Renderer>());
            result.AddRange(shore.GetComponentsInChildren<Renderer>());
            return result.ToArray();
        }

        public static bool StaticSegmentClear(Renderer[] obstacles, Vector2 from, Vector2 to, float radius)
        {
            int samples = Math.Max(1, Mathf.CeilToInt(Vector2.Distance(from, to)));
            for (int i = 0; i <= samples; i++)
            {
                Vector2 point = Vector2.Lerp(from, to, (float)i / samples) * .18f;
                foreach (Renderer renderer in obstacles)
                {
                    Bounds bounds = renderer.bounds;
                    if (bounds.min.y > .7f || bounds.max.y < -.1f) continue;
                    float x = Mathf.Clamp(point.x, bounds.min.x, bounds.max.x);
                    float y = Mathf.Clamp(point.y, bounds.min.z, bounds.max.z);
                    if (Vector2.Distance(point, new Vector2(x, y)) <= radius * .18f) return false;
                }
            }
            return true;
        }

        public static float MaximumRouteHeight(Collider[] colliders, Renderer[] obstacles, Vector2 from, Vector2 to, float halfWidth)
        {
            Vector2 direction = (to - from).normalized;
            Vector2 side = new Vector2(-direction.y, direction.x) * halfWidth;
            int samples = Math.Max(1, Mathf.CeilToInt(Vector2.Distance(from, to)));
            float maximum = 0f;
            for (int i = 0; i <= samples; i++)
            {
                Vector2 point = Vector2.Lerp(from, to, (float)i / samples);
                foreach (Vector2 sample in new[] { point, point + side, point - side })
                {
                    Ray ray = new Ray(Coordinates.ToPresentation(sample.x, sample.y, 1000f), Vector3.down);
                    foreach (Collider collider in colliders)
                        if (collider.Raycast(ray, out RaycastHit hit, 400f)) maximum = Mathf.Max(maximum, hit.point.y / .18f);
                    Vector2 scaled = sample * .18f;
                    foreach (Renderer renderer in obstacles)
                    {
                        Bounds bounds = renderer.bounds;
                        if (scaled.x >= bounds.min.x && scaled.x <= bounds.max.x && scaled.y >= bounds.min.z && scaled.y <= bounds.max.z)
                            maximum = Mathf.Max(maximum, bounds.max.y / .18f);
                    }
                }
            }
            return maximum;
        }

        public static bool Land(Collider[] colliders, float east, float north)
        {
            Ray ray = new Ray(Coordinates.ToPresentation(east, north, 1000f), Vector3.down);
            foreach (Collider collider in colliders)
                if (collider.Raycast(ray, out RaycastHit hit, 400f) && hit.point.y > .02f) return true;
            return false;
        }

        public static bool SegmentClear(Collider[] colliders, Vector2 from, Vector2 to, float halfWidth)
        {
            Vector2 direction = (to - from).normalized;
            Vector2 side = new Vector2(-direction.y, direction.x) * halfWidth;
            int samples = Math.Max(1, Mathf.CeilToInt(Vector2.Distance(from, to)));
            for (int i = 0; i <= samples; i++)
            {
                Vector2 point = Vector2.Lerp(from, to, (float)i / samples);
                if (Land(colliders, point.x, point.y) || Land(colliders, point.x + side.x, point.y + side.y)
                    || Land(colliders, point.x - side.x, point.y - side.y)) return false;
            }
            return true;
        }
    }
}
#endif
