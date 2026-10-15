#if UNITY_EDITOR
using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

namespace UavUsv.Editor.Tools
{
    /// <summary>Export conservative USV water cells from original scene geometry; never save a scene or build a player.</summary>
    public static class NativeBerthWaterExporter
    {
        private const double EastMin = -700, NorthMin = -600, Resolution = 2, HullRadius = 6;
        private const int Width = 330, Height = 350;
        private const double NumericMargin = .05;

        [Serializable] public class Corridor
        {
            public string deviceCode;
            public double fromEastM, fromNorthM, toEastM, toNorthM, clearanceM;
        }
        [Serializable] public class Provenance
        {
            public string generator = "UavUsv.Editor.Tools.NativeBerthWaterExporter.Run";
            public string terrainPrefabAssetPath, terrainDependencyHash;
            public string sceneGeometry = "CatalinaIslandRuntime.CreateVisualTerrain(ENU(0,0,-0.8),0.18) + SimulationBootstrap original bases";
            public string cellContract = "safeRows[row][column]=1 certifies the complete closed 2m square for a 6m-radius USV; rows south-to-north, columns west-to-east; center=min+(index+0.5)*resolution; outside bounds is unavailable";
            public string corridorContract = "Only exact centerline points/segments are supplementary water; clearanceM is hull clearance, NOT lateral center freedom; every complete capsule is checked against the same geometry";
            public string geometryContract = "All world-space terrain triangles clipped at ENU up=0 and conservatively projected to ENU; waterline-intersecting original structure renderer AABBs are obstacles; no finite ray sampling";
            public int sourceTriangleCount, projectedTriangleCount, structureCount, safeCellCount;
            public double terrainLandThresholdUpM = 0;
            public double structureMinimumUpM, structureMaximumUpM;
        }
        [Serializable] public class Data
        {
            public string version = "native-berth-water-v1", coordinateFrame = "GLOBAL_ENU";
            public double resolutionM = Resolution, eastMinM = EastMin, northMinM = NorthMin;
            public int width = Width, height = Height;
            public double hullRadiusM = HullRadius, cellCoverRadiusM = HullRadius + Math.Sqrt(2) + NumericMargin;
            public string[] safeRows;
            public Corridor[] corridors;
            public Provenance metadata = new Provenance();
        }
        private struct Point
        {
            public double x, y;
            public Point(double east, double north) { x = east; y = north; }
        }
        private struct Triangle
        {
            public Point a, b, c;
            public double minX, maxX, minY, maxY;
            public Triangle(Point p, Point q, Point r)
            {
                a = p; b = q; c = r;
                minX = Math.Min(p.x, Math.Min(q.x, r.x)); maxX = Math.Max(p.x, Math.Max(q.x, r.x));
                minY = Math.Min(p.y, Math.Min(q.y, r.y)); maxY = Math.Max(p.y, Math.Max(q.y, r.y));
            }
        }

        [MenuItem("UAV-USV/Navigation/Export Original Berth Water")]
        public static void Run()
        {
            if (EditorApplication.isPlayingOrWillChangePlaymode) throw new InvalidOperationException("Exit Play Mode before exporting water.");
            string output = null;
            string[] args = Environment.GetCommandLineArgs();
            for (int i = 0; i + 1 < args.Length; i++) if (args[i] == "-nativeWaterOutput") output = args[i + 1];
            if (string.IsNullOrEmpty(output)) throw new InvalidOperationException("Missing -nativeWaterOutput JSON path.");
            ValidateGeometryMath();
            Scene previous = SceneManager.GetActiveScene();
            bool owns = !(Application.isBatchMode && previous.IsValid() && previous.isLoaded && string.IsNullOrEmpty(previous.path));
            Scene scene = owns ? EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive) : previous;
            var root = new GameObject("NativeBerthWaterExportTemporaryGeometry");
            try
            {
                SceneManager.SetActiveScene(scene);
                SceneManager.MoveGameObjectToScene(root, scene);
                GameObject terrain = CatalinaIslandRuntime.CreateVisualTerrain(Coordinates.ToPresentationEnvironment(0, 0, -.8f), .18f);
                if (!terrain) throw new InvalidOperationException("Original Catalina terrain resource missing.");
                terrain.transform.SetParent(root.transform, true);
                var data = new Data();
                var prefab = Resources.Load<GameObject>("CatalinaIslandGltf/scene");
                data.metadata.terrainPrefabAssetPath = AssetDatabase.GetAssetPath(prefab);
                data.metadata.terrainDependencyHash = AssetDatabase.GetAssetDependencyHash(data.metadata.terrainPrefabAssetPath).ToString();
                var triangles = new List<Triangle>();
                foreach (MeshFilter filter in terrain.GetComponentsInChildren<MeshFilter>(true))
                {
                    Mesh mesh = filter.sharedMesh;
                    if (!mesh) continue;
                    Vector3[] vertices = mesh.vertices;
                    int[] indices = mesh.triangles;
                    data.metadata.sourceTriangleCount += indices.Length / 3;
                    for (int i = 0; i < indices.Length; i += 3)
                    {
                        var polygon = new List<Vector3>(3) {
                            filter.transform.TransformPoint(vertices[indices[i]]),
                            filter.transform.TransformPoint(vertices[indices[i + 1]]),
                            filter.transform.TransformPoint(vertices[indices[i + 2]]) };
                        polygon = ClipAboveSea(polygon);
                        for (int j = 1; j + 1 < polygon.Count; j++)
                            triangles.Add(new Triangle(Project(polygon[0]), Project(polygon[j]), Project(polygon[j + 1])));
                    }
                }
                if (data.metadata.sourceTriangleCount == 0 || triangles.Count == 0)
                    throw new InvalidOperationException("Cannot certify an empty terrain geometry.");
                data.metadata.projectedTriangleCount = triangles.Count;
                Renderer[] structures = ExistingShorelineProbe.BuildOriginalBases(root.transform);
                foreach (Renderer renderer in structures)
                {
                    Bounds bounds = renderer.bounds;
                    // Match the original mesh probe's waterline structure band.
                    if (bounds.min.y > .7f || bounds.max.y < -.1f) continue;
                    Point a = Project(new Vector3(bounds.min.x, 0, bounds.min.z));
                    Point b = Project(new Vector3(bounds.max.x, 0, bounds.min.z));
                    Point c = Project(new Vector3(bounds.max.x, 0, bounds.max.z));
                    Point d = Project(new Vector3(bounds.min.x, 0, bounds.max.z));
                    triangles.Add(new Triangle(a, b, c)); triangles.Add(new Triangle(a, c, d));
                    data.metadata.structureCount++;
                }
                data.metadata.structureMinimumUpM = -.1 / Coordinates.PresentationCoordinateScale;
                data.metadata.structureMaximumUpM = .7 / Coordinates.PresentationCoordinateScale;
                data.corridors = new[] {
                    MakeCorridor("USV-001", -240, -210, -92, -180),
                    MakeCorridor("USV-002", -240, -190, -108, -160),
                    MakeCorridor("USV-003", -240, -170, -108, -140) };
                foreach (Corridor corridor in data.corridors)
                {
                    var from = new Point(corridor.fromEastM, corridor.fromNorthM);
                    var to = new Point(corridor.toEastM, corridor.toNorthM);
                    double minimum = double.PositiveInfinity;
                    foreach (Triangle triangle in triangles)
                        minimum = Math.Min(minimum, SegmentTriangleDistanceSquared(from, to, triangle));
                    double clearance = Math.Sqrt(minimum);
                    if (clearance < HullRadius + NumericMargin)
                        throw new InvalidOperationException("Original berth capsule is not clear: " + corridor.deviceCode + " clearance=" + clearance);
                    Debug.Log("[NativeBerthWater] " + corridor.deviceCode + " full capsule clearance=" + clearance + "m");
                }
                bool[,] blocked = new bool[Height, Width];
                double cover = data.cellCoverRadiusM, coverSquared = cover * cover;
                foreach (Triangle triangle in triangles)
                {
                    int minColumn = Math.Max(0, (int)Math.Floor((triangle.minX - cover - EastMin) / Resolution));
                    int maxColumn = Math.Min(Width - 1, (int)Math.Floor((triangle.maxX + cover - EastMin) / Resolution));
                    int minRow = Math.Max(0, (int)Math.Floor((triangle.minY - cover - NorthMin) / Resolution));
                    int maxRow = Math.Min(Height - 1, (int)Math.Floor((triangle.maxY + cover - NorthMin) / Resolution));
                    for (int row = minRow; row <= maxRow; row++)
                        for (int column = minColumn; column <= maxColumn; column++)
                            if (!blocked[row, column] && PointTriangleDistanceSquared(
                                new Point(EastMin + (column + .5) * Resolution, NorthMin + (row + .5) * Resolution), triangle) <= coverSquared)
                                blocked[row, column] = true;
                }
                data.safeRows = new string[Height];
                for (int row = 0; row < Height; row++)
                {
                    var characters = new char[Width];
                    for (int column = 0; column < Width; column++)
                    {
                        characters[column] = blocked[row, column] ? '0' : '1';
                        if (!blocked[row, column]) data.metadata.safeCellCount++;
                    }
                    data.safeRows[row] = new string(characters);
                }
                if (data.metadata.safeCellCount == 0 || data.metadata.safeCellCount == Width * Height)
                    throw new InvalidOperationException("Expected both original land and navigable water in the export.");
                Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(output)));
                File.WriteAllText(output, JsonUtility.ToJson(data, true));
                Debug.Log("[NativeBerthWater] PASS " + data.metadata.safeCellCount + "/" + (Width * Height)
                    + " whole safe cells; " + data.metadata.sourceTriangleCount + " source triangles; output=" + output
                    + "; original scene/assets unchanged; no player build.");
            }
            finally
            {
                Object.DestroyImmediate(root);
                if (owns) EditorSceneManager.CloseScene(scene, true);
                if (previous.IsValid() && previous.isLoaded) SceneManager.SetActiveScene(previous);
            }
        }

        private static Corridor MakeCorridor(string code, double x, double y, double endX, double endY)
        { return new Corridor { deviceCode = code, fromEastM = x, fromNorthM = y, toEastM = endX, toNorthM = endY, clearanceM = HullRadius }; }
        private static Point Project(Vector3 world)
        { return new Point(world.x / Coordinates.PresentationCoordinateScale, world.z / Coordinates.PresentationCoordinateScale); }
        private static List<Vector3> ClipAboveSea(List<Vector3> input)
        {
            var output = new List<Vector3>(4);
            Vector3 previous = input[input.Count - 1];
            foreach (Vector3 current in input)
            {
                bool inside = current.y >= 0, wasInside = previous.y >= 0;
                if (inside != wasInside) output.Add(Vector3.LerpUnclamped(previous, current, -previous.y / (current.y - previous.y)));
                if (inside) output.Add(current);
                previous = current;
            }
            return output;
        }
        private static double Cross(Point a, Point b, Point c)
        { return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x); }
        private static double PointSegmentDistanceSquared(Point p, Point a, Point b)
        {
            double dx = b.x - a.x, dy = b.y - a.y, denominator = dx * dx + dy * dy;
            double t = denominator < 1e-20 ? 0 : Math.Max(0, Math.Min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / denominator));
            dx = p.x - a.x - t * dx; dy = p.y - a.y - t * dy;
            return dx * dx + dy * dy;
        }
        private static double PointTriangleDistanceSquared(Point p, Triangle t)
        {
            double ab = Cross(t.a, t.b, p), bc = Cross(t.b, t.c, p), ca = Cross(t.c, t.a, p);
            if (Math.Abs(Cross(t.a, t.b, t.c)) > 1e-15 && ((ab >= 0 && bc >= 0 && ca >= 0) || (ab <= 0 && bc <= 0 && ca <= 0))) return 0;
            return Math.Min(PointSegmentDistanceSquared(p, t.a, t.b), Math.Min(PointSegmentDistanceSquared(p, t.b, t.c), PointSegmentDistanceSquared(p, t.c, t.a)));
        }
        private static double SegmentDistanceSquared(Point a, Point b, Point c, Point d)
        {
            double abC = Cross(a, b, c), abD = Cross(a, b, d), cdA = Cross(c, d, a), cdB = Cross(c, d, b);
            if (((abC > 0 && abD < 0) || (abC < 0 && abD > 0)) && ((cdA > 0 && cdB < 0) || (cdA < 0 && cdB > 0))) return 0;
            return Math.Min(Math.Min(PointSegmentDistanceSquared(a, c, d), PointSegmentDistanceSquared(b, c, d)),
                Math.Min(PointSegmentDistanceSquared(c, a, b), PointSegmentDistanceSquared(d, a, b)));
        }
        private static double SegmentTriangleDistanceSquared(Point a, Point b, Triangle t)
        {
            return Math.Min(Math.Min(PointTriangleDistanceSquared(a, t), PointTriangleDistanceSquared(b, t)),
                Math.Min(SegmentDistanceSquared(a, b, t.a, t.b), Math.Min(SegmentDistanceSquared(a, b, t.b, t.c), SegmentDistanceSquared(a, b, t.c, t.a))));
        }
        private static void ValidateGeometryMath()
        {
            var triangle = new Triangle(new Point(0, 0), new Point(10, 0), new Point(0, 10));
            if (PointTriangleDistanceSquared(new Point(2, 2), triangle) != 0
                || Math.Abs(PointTriangleDistanceSquared(new Point(-3, 0), triangle) - 9) > 1e-9
                || SegmentTriangleDistanceSquared(new Point(-1, 5), new Point(6, 5), triangle) != 0
                || Math.Abs(SegmentTriangleDistanceSquared(new Point(-3, -4), new Point(-3, 0), triangle) - 9) > 1e-9
                || Math.Abs(SegmentDistanceSquared(new Point(0, 0), new Point(2, 0), new Point(1, 0), new Point(3, 0))) > 1e-9)
                throw new InvalidOperationException("Conservative water geometry self-test failed.");
            var clipped = ClipAboveSea(new List<Vector3> { new Vector3(0, -1, 0), new Vector3(2, 1, 0), new Vector3(0, 1, 2) });
            if (clipped.Count != 4 || clipped.Exists(point => point.y < -1e-6)) throw new InvalidOperationException("Sea clipping self-test failed.");
        }
    }
}
#endif
