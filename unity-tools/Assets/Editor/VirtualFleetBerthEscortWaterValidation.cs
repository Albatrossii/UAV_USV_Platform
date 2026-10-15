#if UNITY_EDITOR
using System;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace UavUsv.Editor.Tools
{
    /// <summary>Validate exported authoritative trajectories against the original meshes. Never saves a scene or builds a player.</summary>
    public static class VirtualFleetBerthEscortWaterValidation
    {
        [Serializable] public class Point { public float eastM; public float northM; public float radiusM; public string code; }
        [Serializable] public class Segment { public float fromEastM; public float fromNorthM; public float toEastM; public float toNorthM; public float radiusM; public string code; }
        [Serializable] public class Samples { public Point[] points; public Segment[] segments; }

        public static void Run()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new InvalidOperationException("Exit Play Mode before validation.");
            string path = null;
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i + 1 < args.Length; i++) if (args[i] == "-escortWaterSamples") path = args[i + 1];
            if (string.IsNullOrEmpty(path)) throw new InvalidOperationException("Missing -escortWaterSamples JSON path.");
            Samples samples = JsonUtility.FromJson<Samples>(File.ReadAllText(path));
            if (samples == null || samples.points == null || samples.points.Length == 0)
                throw new InvalidOperationException("No authoritative water samples supplied.");
            Scene previous = SceneManager.GetActiveScene();
            bool owns = !(Application.isBatchMode && previous.IsValid() && previous.isLoaded && string.IsNullOrEmpty(previous.path));
            Scene scene = owns ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive) : previous;
            var root = new GameObject("BerthEscortWaterValidation");
            int passed = 0;
            try
            {
                SceneManager.SetActiveScene(scene);
                SceneManager.MoveGameObjectToScene(root, scene);
                var terrain = CatalinaIslandRuntime.CreateVisualTerrain(Coordinates.ToPresentationEnvironment(0f, 0f, -.8f), .18f);
                if (!terrain) throw new InvalidOperationException("Original Catalina terrain missing.");
                terrain.transform.SetParent(root.transform, true);
                foreach (MeshFilter filter in terrain.GetComponentsInChildren<MeshFilter>(true))
                {
                    if (!filter.sharedMesh) continue;
                    var collider = filter.GetComponent<MeshCollider>();
                    if (!collider) collider = filter.gameObject.AddComponent<MeshCollider>();
                    collider.sharedMesh = filter.sharedMesh;
                }
                Renderer[] structures = ExistingShorelineProbe.BuildOriginalBases(root.transform);
                Physics.SyncTransforms();
                Collider[] colliders = terrain.GetComponentsInChildren<Collider>();
                foreach (Point point in samples.points)
                {
                    CheckFootprint(colliders, structures, new Vector2(point.eastM, point.northM), point.radiusM, point.code);
                    passed++;
                }
                foreach (Segment segment in samples.segments ?? new Segment[0])
                {
                    var from = new Vector2(segment.fromEastM, segment.fromNorthM);
                    var to = new Vector2(segment.toEastM, segment.toNorthM);
                    int steps = Math.Max(1, Mathf.CeilToInt(Vector2.Distance(from, to)));
                    for (int i = 0; i <= steps; i++)
                        CheckFootprint(colliders, structures, Vector2.Lerp(from, to, (float)i / steps), segment.radiusM, segment.code);
                    passed++;
                }
                Debug.Log("[BerthEscortWater] PASS " + passed + " authoritative hull/trajectory checks; original terrain and bases unchanged.");
            }
            finally
            {
                Object.DestroyImmediate(root);
                if (owns) EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded) SceneManager.SetActiveScene(previous);
            }
        }

        private static void CheckFootprint(Collider[] colliders, Renderer[] structures, Vector2 center, float radius, string code)
        {
            if (float.IsNaN(center.x) || float.IsNaN(center.y) || float.IsInfinity(center.x) || float.IsInfinity(center.y)
                || float.IsNaN(radius) || float.IsInfinity(radius) || radius <= 0f)
                throw new InvalidOperationException("Invalid water footprint: " + code);
            int rings = Math.Max(1, Mathf.CeilToInt(radius / 2f));
            for (int ring = 0; ring <= rings; ring++)
            {
                float distance = radius * ring / rings;
                int count = ring == 0 ? 1 : Math.Max(8, Mathf.CeilToInt(Mathf.PI * 2f * distance / 2f));
                for (int i = 0; i < count; i++)
                {
                    float angle = Mathf.PI * 2f * i / count;
                    Vector2 point = center + new Vector2(Mathf.Cos(angle), Mathf.Sin(angle)) * distance;
                    if (ExistingShorelineProbe.Land(colliders, point.x, point.y))
                        throw new InvalidOperationException("[BerthEscortWater] Land collision: " + code + " center=" + center + " sample=" + point);
                }
            }
            if (!ExistingShorelineProbe.StaticSegmentClear(structures, center, center, radius))
                throw new InvalidOperationException("[BerthEscortWater] Original base collision: " + code + " center=" + center);
        }
    }
}
#endif
