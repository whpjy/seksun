using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using NXOpen;
using NXOpen.Assemblies;
using NXOpen.UF;

public sealed class MeasureVectorV27
{
    public double X;
    public double Y;
    public double Z;

    public MeasureVectorV27() { }

    public MeasureVectorV27(double x, double y, double z)
    {
        X = x;
        Y = y;
        Z = z;
    }
}

public sealed class AxialSegmentV27
{
    public string FaceTag;
    public bool IsInternal;
    public double Diameter;
    public double StartStation;
    public double EndStation;
    public double Length;
    public double Coverage;
}

public sealed class AxialFeatureV27
{
    public string Id;
    public string Type;
    public string BodyTag;
    public readonly List<string> FaceTags = new List<string>();
    public readonly List<AxialSegmentV27> Segments =
        new List<AxialSegmentV27>();
    public readonly List<double> Diameters = new List<double>();
    public MeasureVectorV27 Axis;
    public MeasureVectorV27 Center;
    public MeasureVectorV27 StartPoint;
    public MeasureVectorV27 EndPoint;
    public double TotalLength;
    public double Confidence;
    public string Priority;
    public bool AutoAnnotate;
    public int DisplayDecimals;
}

public sealed class SlotFeatureV27
{
    public string Id;
    public string BodyTag;
    public readonly List<string> FaceTags = new List<string>();
    public MeasureVectorV27 Axis;
    public MeasureVectorV27 Direction;
    public MeasureVectorV27 Center;
    public double Width;
    public double OverallLength;
    public double CenterDistance;
    public double Depth;
    public double Confidence;
    public string Priority;
    public bool AutoAnnotate;
    public int DisplayDecimals;
}

public sealed class RadiusCandidateV27
{
    public string Id;
    public double Radius;
    public int FaceCount;
    public double Confidence;
    public string Priority;
    public bool AutoAnnotate;
}

public sealed class ThicknessCandidateV27
{
    public string Id;
    public double Thickness;
    public int EvidenceCount;
    public MeasureVectorV27 Normal;
    public double Confidence;
    public string Priority;
    public bool AutoAnnotate;
}

public sealed class MeasureResultV27
{
    public string Schema = "nx-measure/27.0";
    public string ModelName;
    public string ModelPath;
    public string Units;
    public int BodyCount;
    public readonly List<AxialFeatureV27> AxialFeatures =
        new List<AxialFeatureV27>();
    public readonly List<SlotFeatureV27> Slots =
        new List<SlotFeatureV27>();
    public readonly List<RadiusCandidateV27> RadiusCandidates =
        new List<RadiusCandidateV27>();
    public readonly List<ThicknessCandidateV27> ThicknessCandidates =
        new List<ThicknessCandidateV27>();
    public readonly List<string> Warnings = new List<string>();
}

public class NxMeasureV27
{
    private const int CylindricalFace = 16;
    private const int ConicalFace = 17;
    private const int ToroidalFace = 19;
    private const int PlanarFace = 22;
    private const double Epsilon = 1.0e-9;
    private const double AxisCosine = 0.999;
    private const double PositionTolerance = 0.05;
    private const double DiameterTolerance = 0.02;

    public static int Main(string[] args)
    {
        Session session = Session.GetSession();
        ListingWindow output = session.ListingWindow;
        output.Open();
        Part workPart = session.Parts.Work;
        if (workPart == null)
        {
            output.WriteLine("NxMeasure V27: no work part is open.");
            return 1;
        }

        try
        {
            MeasureResultV27 result = Analyze(
                workPart,
                UFSession.GetUFSession());
            string path = DefaultJsonPath(workPart);
            WriteJson(result, path);
            output.WriteLine("NxMeasure V27 completed.");
            output.WriteLine("Axial features: " + result.AxialFeatures.Count);
            output.WriteLine("Slots: " + result.Slots.Count);
            output.WriteLine("Radius groups: " + result.RadiusCandidates.Count);
            output.WriteLine("Thickness candidates: " + result.ThicknessCandidates.Count);
            output.WriteLine("JSON: " + path);
            return 0;
        }
        catch (Exception ex)
        {
            output.WriteLine("NxMeasure V27 failed:");
            output.WriteLine(ex.ToString());
            return 99;
        }
    }

    public static MeasureResultV27 Analyze(
        Part workPart,
        UFSession ufSession)
    {
        MeasureResultV27 result = new MeasureResultV27();
        result.ModelName = workPart.Name;
        result.ModelPath = workPart.FullPath;
        result.Units = workPart.PartUnits == BasePart.Units.Millimeters
            ? "mm"
            : "in";

        List<Body> bodies = GetAnalysisBodies(workPart);
        result.BodyCount = bodies.Count;
        List<CylinderPatch> fullPatches = new List<CylinderPatch>();
        List<CylinderPatch> partialPatches = new List<CylinderPatch>();
        List<PlanePatch> planes = new List<PlanePatch>();
        List<double> toroidalRadii = new List<double>();
        Unit areaUnit = workPart.UnitCollection.GetBase("Area");
        Unit lengthUnit = workPart.UnitCollection.GetBase("Length");

        foreach (Body body in bodies)
        {
            foreach (Face face in body.GetFaces())
            {
                FaceData data;
                if (!TryAskFaceData(face, ufSession, out data))
                {
                    continue;
                }

                if (data.Type == CylindricalFace)
                {
                    CylinderPatch patch = BuildCylinderPatch(
                        workPart,
                        areaUnit,
                        lengthUnit,
                        body,
                        face,
                        data,
                        ufSession);
                    if (patch == null)
                    {
                        continue;
                    }
                    if (patch.Coverage >= 0.84)
                    {
                        fullPatches.Add(patch);
                    }
                    else if (patch.IsInternal &&
                        patch.Coverage >= 0.30 &&
                        patch.Coverage <= 0.70)
                    {
                        partialPatches.Add(patch);
                    }
                }
                else if (data.Type == ToroidalFace)
                {
                    double radius = Math.Abs(data.RadiusData);
                    if (radius > Epsilon)
                    {
                        toroidalRadii.Add(radius);
                    }
                }
                else if (data.Type == PlanarFace)
                {
                    double area = MeasureArea(
                        workPart,
                        areaUnit,
                        lengthUnit,
                        face);
                    planes.Add(new PlanePatch
                    {
                        Normal = Canonical(new V(data.Direction[0], data.Direction[1], data.Direction[2])),
                        Offset = Dot(
                            Canonical(new V(data.Direction[0], data.Direction[1], data.Direction[2])),
                            new V(data.Point[0], data.Point[1], data.Point[2])),
                        Area = area
                    });
                }
            }
        }

        BuildAxialFeatures(fullPatches, result);
        BuildSlots(partialPatches, result);
        BuildRadiusCandidates(toroidalRadii, result);
        BuildThicknessCandidates(planes, result);
        return result;
    }

    private static void BuildAxialFeatures(
        List<CylinderPatch> patches,
        MeasureResultV27 result)
    {
        patches.Sort(delegate(CylinderPatch left, CylinderPatch right)
        {
            int body = string.Compare(left.BodyTag, right.BodyTag, StringComparison.Ordinal);
            if (body != 0) return body;
            return left.MinimumStation.CompareTo(right.MinimumStation);
        });

        List<AxialGroup> groups = new List<AxialGroup>();
        foreach (CylinderPatch patch in patches)
        {
            AxialGroup target = null;
            foreach (AxialGroup group in groups)
            {
                if (CanMerge(group, patch))
                {
                    target = group;
                    break;
                }
            }
            if (target == null)
            {
                target = new AxialGroup
                {
                    BodyTag = patch.BodyTag,
                    Axis = patch.Axis,
                    AxisPoint = patch.AxisPoint,
                    MinimumStation = patch.MinimumStation,
                    MaximumStation = patch.MaximumStation
                };
                groups.Add(target);
            }
            target.Patches.Add(patch);
            target.MinimumStation = Math.Min(target.MinimumStation, patch.MinimumStation);
            target.MaximumStation = Math.Max(target.MaximumStation, patch.MaximumStation);
        }

        int holeNumber = 0;
        int externalNumber = 0;
        int mixedNumber = 0;
        foreach (AxialGroup group in groups)
        {
            bool hasInternal = false;
            bool hasExternal = false;
            foreach (CylinderPatch patch in group.Patches)
            {
                hasInternal |= patch.IsInternal;
                hasExternal |= !patch.IsInternal;
            }

            AxialFeatureV27 feature = new AxialFeatureV27();
            if (hasInternal && !hasExternal)
            {
                holeNumber++;
                feature.Id = "HF" + holeNumber.ToString("000", CultureInfo.InvariantCulture);
                feature.Type = group.Patches.Count > 1 ? "COMPOSITE_HOLE" : "ROUND_HOLE";
            }
            else if (!hasInternal && hasExternal)
            {
                externalNumber++;
                feature.Id = "EF" + externalNumber.ToString("000", CultureInfo.InvariantCulture);
                feature.Type = group.Patches.Count > 1
                    ? "STEPPED_EXTERNAL_CYLINDER"
                    : "EXTERNAL_CYLINDER";
            }
            else
            {
                mixedNumber++;
                feature.Id = "CF" + mixedNumber.ToString("000", CultureInfo.InvariantCulture);
                feature.Type = "COAXIAL_COMPOSITE";
            }

            feature.BodyTag = group.BodyTag;
            feature.Axis = ToPublic(group.Axis);
            V center = group.AxisPoint + group.Axis *
                ((group.MinimumStation + group.MaximumStation) * 0.5 -
                 Dot(group.AxisPoint, group.Axis));
            V start = group.AxisPoint + group.Axis *
                (group.MinimumStation - Dot(group.AxisPoint, group.Axis));
            V end = group.AxisPoint + group.Axis *
                (group.MaximumStation - Dot(group.AxisPoint, group.Axis));
            feature.Center = ToPublic(center);
            feature.StartPoint = ToPublic(start);
            feature.EndPoint = ToPublic(end);
            feature.TotalLength = group.MaximumStation - group.MinimumStation;
            feature.DisplayDecimals = 3;

            foreach (CylinderPatch patch in group.Patches)
            {
                feature.FaceTags.Add(patch.FaceTag);
                AddUniqueDiameter(feature.Diameters, patch.Diameter);
                feature.Segments.Add(new AxialSegmentV27
                {
                    FaceTag = patch.FaceTag,
                    IsInternal = patch.IsInternal,
                    Diameter = patch.Diameter,
                    StartStation = patch.MinimumStation,
                    EndStation = patch.MaximumStation,
                    Length = patch.MaximumStation - patch.MinimumStation,
                    Coverage = patch.Coverage
                });
            }
            feature.Diameters.Sort();
            double aspect = feature.Diameters.Count == 0
                ? 0.0
                : feature.TotalLength / feature.Diameters[feature.Diameters.Count - 1];
            feature.Confidence = group.Patches.Count <= 6 ? 0.98 : 0.90;
            feature.Priority = feature.Type == "ROUND_HOLE" ||
                feature.Type == "COMPOSITE_HOLE" ||
                aspect >= 1.2
                    ? "A"
                    : group.Patches.Count <= 3 ? "B" : "C";
            feature.AutoAnnotate = feature.Confidence >= 0.94 &&
                feature.Priority != "C";
            result.AxialFeatures.Add(feature);
        }
    }

    private static bool CanMerge(AxialGroup group, CylinderPatch patch)
    {
        if (group.BodyTag != patch.BodyTag ||
            Math.Abs(Dot(group.Axis, patch.Axis)) < AxisCosine)
        {
            return false;
        }
        V delta = patch.AxisPoint - group.AxisPoint;
        V radial = delta - group.Axis * Dot(delta, group.Axis);
        if (radial.Length() > PositionTolerance)
        {
            return false;
        }
        double gap = IntervalGap(
            group.MinimumStation,
            group.MaximumStation,
            patch.MinimumStation,
            patch.MaximumStation);
        return gap <= Math.Max(0.20, patch.Diameter * 0.05);
    }

    private static void BuildSlots(
        List<CylinderPatch> partials,
        MeasureResultV27 result)
    {
        HashSet<int> used = new HashSet<int>();
        int slotNumber = 0;
        for (int i = 0; i < partials.Count; i++)
        {
            if (used.Contains(i)) continue;
            int bestIndex = -1;
            double bestDistance = double.MaxValue;
            for (int j = i + 1; j < partials.Count; j++)
            {
                if (used.Contains(j) ||
                    partials[i].BodyTag != partials[j].BodyTag ||
                    Math.Abs(Dot(partials[i].Axis, partials[j].Axis)) < AxisCosine ||
                    Math.Abs(partials[i].Diameter - partials[j].Diameter) > DiameterTolerance)
                {
                    continue;
                }
                if (IntervalGap(
                        partials[i].MinimumStation,
                        partials[i].MaximumStation,
                        partials[j].MinimumStation,
                        partials[j].MaximumStation) > 0.10)
                {
                    continue;
                }
                V centerDelta = partials[j].AxisPoint - partials[i].AxisPoint;
                V inPlane = centerDelta - partials[i].Axis *
                    Dot(centerDelta, partials[i].Axis);
                double distance = inPlane.Length();
                if (distance > partials[i].Diameter * 0.5 && distance < bestDistance)
                {
                    bestDistance = distance;
                    bestIndex = j;
                }
            }
            if (bestIndex < 0) continue;

            CylinderPatch first = partials[i];
            CylinderPatch second = partials[bestIndex];
            used.Add(i);
            used.Add(bestIndex);
            slotNumber++;
            V delta = second.AxisPoint - first.AxisPoint;
            V direction = (delta - first.Axis * Dot(delta, first.Axis)).Normalize();
            V center = first.AxisPoint + direction * (bestDistance * 0.5);
            SlotFeatureV27 slot = new SlotFeatureV27();
            slot.Id = "SF" + slotNumber.ToString("000", CultureInfo.InvariantCulture);
            slot.BodyTag = first.BodyTag;
            slot.FaceTags.Add(first.FaceTag);
            slot.FaceTags.Add(second.FaceTag);
            slot.Axis = ToPublic(first.Axis);
            slot.Direction = ToPublic(direction);
            slot.Center = ToPublic(center);
            slot.Width = first.Diameter;
            slot.CenterDistance = bestDistance;
            slot.OverallLength = bestDistance + first.Diameter;
            slot.Depth = Math.Min(
                first.MaximumStation - first.MinimumStation,
                second.MaximumStation - second.MinimumStation);
            slot.Confidence = 0.88;
            slot.Priority = "B";
            slot.AutoAnnotate = false;
            slot.DisplayDecimals = 3;
            result.Slots.Add(slot);
        }
    }

    private static void BuildRadiusCandidates(
        List<double> radii,
        MeasureResultV27 result)
    {
        radii.Sort();
        foreach (double radius in radii)
        {
            RadiusCandidateV27 group = null;
            foreach (RadiusCandidateV27 existing in result.RadiusCandidates)
            {
                if (Math.Abs(existing.Radius - radius) <= 0.01)
                {
                    group = existing;
                    break;
                }
            }
            if (group == null)
            {
                group = new RadiusCandidateV27
                {
                    Id = "RG" + (result.RadiusCandidates.Count + 1).ToString("000"),
                    Radius = radius
                };
                result.RadiusCandidates.Add(group);
            }
            group.FaceCount++;
        }
        foreach (RadiusCandidateV27 group in result.RadiusCandidates)
        {
            group.Confidence = group.FaceCount >= 4 ? 0.92 : 0.75;
            group.Priority = group.FaceCount >= 4 ? "B" : "C";
            group.AutoAnnotate = false;
        }
    }

    private static void BuildThicknessCandidates(
        List<PlanePatch> planes,
        MeasureResultV27 result)
    {
        Dictionary<string, ThicknessAccumulator> groups =
            new Dictionary<string, ThicknessAccumulator>();
        for (int i = 0; i < planes.Count; i++)
        {
            for (int j = i + 1; j < planes.Count; j++)
            {
                if (Math.Abs(Dot(planes[i].Normal, planes[j].Normal)) < AxisCosine)
                {
                    continue;
                }
                double areaRatio = Math.Min(planes[i].Area, planes[j].Area) /
                    Math.Max(Math.Max(planes[i].Area, planes[j].Area), Epsilon);
                if (areaRatio < 0.70)
                {
                    continue;
                }
                double distance = Math.Abs(planes[j].Offset - planes[i].Offset);
                if (distance <= 0.02)
                {
                    continue;
                }
                double rounded = Math.Round(distance, 3);
                string key = rounded.ToString("0.000", CultureInfo.InvariantCulture);
                ThicknessAccumulator accumulator;
                if (!groups.TryGetValue(key, out accumulator))
                {
                    accumulator = new ThicknessAccumulator
                    {
                        Thickness = rounded,
                        Normal = planes[i].Normal
                    };
                    groups[key] = accumulator;
                }
                accumulator.Count++;
            }
        }

        List<ThicknessAccumulator> ordered = new List<ThicknessAccumulator>(groups.Values);
        ordered.Sort(delegate(ThicknessAccumulator left, ThicknessAccumulator right)
        {
            int count = right.Count.CompareTo(left.Count);
            return count != 0 ? count : left.Thickness.CompareTo(right.Thickness);
        });
        int maximum = Math.Min(10, ordered.Count);
        for (int i = 0; i < maximum; i++)
        {
            ThicknessAccumulator item = ordered[i];
            result.ThicknessCandidates.Add(new ThicknessCandidateV27
            {
                Id = "TH" + (i + 1).ToString("000"),
                Thickness = item.Thickness,
                EvidenceCount = item.Count,
                Normal = ToPublic(item.Normal),
                Confidence = item.Count >= 3 ? 0.95 : 0.72,
                Priority = item.Count >= 3 ? "A" : "C",
                AutoAnnotate = false
            });
        }
    }

    private static CylinderPatch BuildCylinderPatch(
        Part workPart,
        Unit areaUnit,
        Unit lengthUnit,
        Body body,
        Face face,
        FaceData data,
        UFSession ufSession)
    {
        V axis = Canonical(new V(
            data.Direction[0],
            data.Direction[1],
            data.Direction[2]));
        V axisPoint = new V(data.Point[0], data.Point[1], data.Point[2]);
        double minimum = double.PositiveInfinity;
        double maximum = double.NegativeInfinity;
        foreach (Edge edge in face.GetEdges())
        {
            foreach (V point in SampleEdge(edge, ufSession))
            {
                double station = Dot(point, axis);
                minimum = Math.Min(minimum, station);
                maximum = Math.Max(maximum, station);
            }
        }
        double length = maximum - minimum;
        double radius = Math.Abs(data.Radius);
        if (double.IsInfinity(minimum) || length <= Epsilon || radius <= Epsilon)
        {
            return null;
        }
        double area = MeasureArea(workPart, areaUnit, lengthUnit, face);
        double coverage = area / (2.0 * Math.PI * radius * length);
        return new CylinderPatch
        {
            BodyTag = body.Tag.ToString(),
            FaceTag = face.Tag.ToString(),
            Axis = axis,
            AxisPoint = axisPoint,
            Diameter = radius * 2.0,
            MinimumStation = minimum,
            MaximumStation = maximum,
            Coverage = Math.Max(0.0, Math.Min(1.0, coverage)),
            IsInternal = data.NormalDirection < 0
        };
    }

    private static double MeasureArea(
        Part workPart,
        Unit areaUnit,
        Unit lengthUnit,
        Face face)
    {
        using (MeasureFaces measure = workPart.MeasureManager.NewFaceProperties(
            areaUnit,
            lengthUnit,
            0.999,
            new IParameterizedSurface[] { face }))
        {
            return measure.Area;
        }
    }

    private static bool TryAskFaceData(
        Face face,
        UFSession ufSession,
        out FaceData result)
    {
        result = new FaceData();
        result.Point = new double[3];
        result.Direction = new double[3];
        double[] box = new double[6];
        try
        {
            ufSession.Modl.AskFaceData(
                face.Tag,
                out result.Type,
                result.Point,
                result.Direction,
                box,
                out result.Radius,
                out result.RadiusData,
                out result.NormalDirection);
            return true;
        }
        catch
        {
            return false;
        }
    }

    private static List<V> SampleEdge(Edge edge, UFSession ufSession)
    {
        List<V> result = new List<V>();
        try
        {
            double[] limits = new double[2];
            int periodic;
            ufSession.Curve.AskParameterization(edge.Tag, limits, out periodic);
            int count = edge.SolidEdgeType == Edge.EdgeType.Linear ? 1 : 32;
            for (int i = 0; i <= count; i++)
            {
                double parameter = limits[0] +
                    (limits[1] - limits[0]) * i / (double)count;
                double[] point = new double[3];
                ufSession.Curve.EvaluateCurve(edge.Tag, parameter, 0, point);
                result.Add(new V(point[0], point[1], point[2]));
            }
        }
        catch
        {
            Point3d first;
            Point3d second;
            edge.GetVertices(out first, out second);
            result.Add(new V(first.X, first.Y, first.Z));
            result.Add(new V(second.X, second.Y, second.Z));
        }
        return result;
    }

    private static List<Body> GetAnalysisBodies(Part workPart)
    {
        List<Body> result = new List<Body>();
        HashSet<Tag> tags = new HashSet<Tag>();
        foreach (Body body in workPart.Bodies)
        {
            AddBody(result, tags, body);
        }
        Component root = workPart.ComponentAssembly.RootComponent;
        if (root != null)
        {
            foreach (Component child in root.GetChildren())
            {
                CollectBodies(child, result, tags);
            }
        }
        return result;
    }

    private static void CollectBodies(
        Component component,
        List<Body> result,
        HashSet<Tag> tags)
    {
        Part prototype = component.Prototype as Part;
        if (prototype != null)
        {
            foreach (Body prototypeBody in prototype.Bodies)
            {
                AddBody(result, tags, component.FindOccurrence(prototypeBody) as Body);
            }
        }
        foreach (Component child in component.GetChildren())
        {
            CollectBodies(child, result, tags);
        }
    }

    private static void AddBody(
        List<Body> result,
        HashSet<Tag> tags,
        Body body)
    {
        if (body == null || !body.IsSolidBody || tags.Contains(body.Tag)) return;
        tags.Add(body.Tag);
        result.Add(body);
    }

    public static string DefaultJsonPath(Part part)
    {
        string directory = string.IsNullOrEmpty(part.FullPath)
            ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
            : Path.GetDirectoryName(part.FullPath);
        return Path.Combine(
            directory,
            SafeFileName(part.Name) + "_自动测量报告V27.json");
    }

    public static void WriteJson(MeasureResultV27 result, string path)
    {
        using (StreamWriter writer = new StreamWriter(
            path,
            false,
            new UTF8Encoding(false)))
        {
            writer.WriteLine("{");
            writer.WriteLine("  \"schema\": \"" + J(result.Schema) + "\",");
            writer.WriteLine("  \"model\": \"" + J(result.ModelName) + "\",");
            writer.WriteLine("  \"units\": \"" + J(result.Units) + "\",");
            writer.WriteLine("  \"body_count\": " + result.BodyCount + ",");
            writer.WriteLine("  \"axial_features\": [");
            for (int i = 0; i < result.AxialFeatures.Count; i++)
            {
                AxialFeatureV27 feature = result.AxialFeatures[i];
                writer.WriteLine("    {");
                writer.WriteLine("      \"id\": \"" + J(feature.Id) + "\",");
                writer.WriteLine("      \"type\": \"" + J(feature.Type) + "\",");
                writer.WriteLine("      \"body_tag\": \"" + J(feature.BodyTag) + "\",");
                writer.Write("      \"face_tags\": "); WriteStrings(writer, feature.FaceTags); writer.WriteLine(",");
                writer.WriteLine("      \"axis\": " + VectorJson(feature.Axis) + ",");
                writer.WriteLine("      \"center\": " + VectorJson(feature.Center) + ",");
                writer.WriteLine("      \"start\": " + VectorJson(feature.StartPoint) + ",");
                writer.WriteLine("      \"end\": " + VectorJson(feature.EndPoint) + ",");
                writer.Write("      \"diameters\": [");
                for (int d = 0; d < feature.Diameters.Count; d++)
                {
                    if (d > 0) writer.Write(",");
                    writer.Write(N(feature.Diameters[d]));
                }
                writer.WriteLine("],");
                writer.WriteLine("      \"total_length\": " + N(feature.TotalLength) + ",");
                writer.WriteLine("      \"confidence\": " + N(feature.Confidence) + ",");
                writer.WriteLine("      \"priority\": \"" + J(feature.Priority) + "\",");
                writer.WriteLine("      \"auto_annotate\": " + (feature.AutoAnnotate ? "true" : "false"));
                writer.WriteLine("    }" + (i + 1 < result.AxialFeatures.Count ? "," : ""));
            }
            writer.WriteLine("  ],");
            writer.WriteLine("  \"slots\": [");
            for (int i = 0; i < result.Slots.Count; i++)
            {
                SlotFeatureV27 slot = result.Slots[i];
                writer.WriteLine("    {\"id\":\"" + J(slot.Id) + "\",\"center\":" +
                    VectorJson(slot.Center) + ",\"axis\":" + VectorJson(slot.Axis) +
                    ",\"direction\":" + VectorJson(slot.Direction) +
                    ",\"width\":" + N(slot.Width) + ",\"overall_length\":" +
                    N(slot.OverallLength) + ",\"depth\":" + N(slot.Depth) +
                    ",\"confidence\":" + N(slot.Confidence) + "}" +
                    (i + 1 < result.Slots.Count ? "," : ""));
            }
            writer.WriteLine("  ],");
            writer.WriteLine("  \"radius_candidates\": [");
            for (int i = 0; i < result.RadiusCandidates.Count; i++)
            {
                RadiusCandidateV27 item = result.RadiusCandidates[i];
                writer.WriteLine("    {\"id\":\"" + J(item.Id) + "\",\"radius\":" +
                    N(item.Radius) + ",\"face_count\":" + item.FaceCount +
                    ",\"confidence\":" + N(item.Confidence) + "}" +
                    (i + 1 < result.RadiusCandidates.Count ? "," : ""));
            }
            writer.WriteLine("  ],");
            writer.WriteLine("  \"thickness_candidates\": [");
            for (int i = 0; i < result.ThicknessCandidates.Count; i++)
            {
                ThicknessCandidateV27 item = result.ThicknessCandidates[i];
                writer.WriteLine("    {\"id\":\"" + J(item.Id) + "\",\"thickness\":" +
                    N(item.Thickness) + ",\"evidence_count\":" + item.EvidenceCount +
                    ",\"normal\":" + VectorJson(item.Normal) +
                    ",\"confidence\":" + N(item.Confidence) + "}" +
                    (i + 1 < result.ThicknessCandidates.Count ? "," : ""));
            }
            writer.WriteLine("  ]");
            writer.WriteLine("}");
        }
    }

    private static string VectorJson(MeasureVectorV27 value)
    {
        return "[" + N(value.X) + "," + N(value.Y) + "," + N(value.Z) + "]";
    }

    private static void WriteStrings(StreamWriter writer, List<string> values)
    {
        writer.Write("[");
        for (int i = 0; i < values.Count; i++)
        {
            if (i > 0) writer.Write(",");
            writer.Write("\"" + J(values[i]) + "\"");
        }
        writer.Write("]");
    }

    private static string N(double value)
    {
        return value.ToString("0.########", CultureInfo.InvariantCulture);
    }

    private static string J(string value)
    {
        if (value == null) return string.Empty;
        return value.Replace("\\", "\\\\").Replace("\"", "\\\"")
            .Replace("\r", "\\r").Replace("\n", "\\n");
    }

    private static string SafeFileName(string value)
    {
        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(invalid, '_');
        }
        return value;
    }

    private static void AddUniqueDiameter(List<double> values, double value)
    {
        foreach (double existing in values)
        {
            if (Math.Abs(existing - value) <= DiameterTolerance) return;
        }
        values.Add(value);
    }

    private static double IntervalGap(
        double firstMin,
        double firstMax,
        double secondMin,
        double secondMax)
    {
        if (firstMax < secondMin) return secondMin - firstMax;
        if (secondMax < firstMin) return firstMin - secondMax;
        return 0.0;
    }

    private static MeasureVectorV27 ToPublic(V value)
    {
        return new MeasureVectorV27(value.X, value.Y, value.Z);
    }

    private static V Canonical(V value)
    {
        V normalized = value.Normalize();
        double dominant = Math.Abs(normalized.X) >= Math.Abs(normalized.Y) &&
            Math.Abs(normalized.X) >= Math.Abs(normalized.Z)
                ? normalized.X
                : Math.Abs(normalized.Y) >= Math.Abs(normalized.Z)
                    ? normalized.Y
                    : normalized.Z;
        return dominant < 0.0 ? normalized * -1.0 : normalized;
    }

    private static double Dot(V left, V right)
    {
        return left.X * right.X + left.Y * right.Y + left.Z * right.Z;
    }

    public static int GetUnloadOption(string dummy)
    {
        return (int)Session.LibraryUnloadOption.Immediately;
    }

    private sealed class FaceData
    {
        public int Type;
        public double[] Point;
        public double[] Direction;
        public double Radius;
        public double RadiusData;
        public int NormalDirection;
    }

    private sealed class CylinderPatch
    {
        public string BodyTag;
        public string FaceTag;
        public V Axis;
        public V AxisPoint;
        public double Diameter;
        public double MinimumStation;
        public double MaximumStation;
        public double Coverage;
        public bool IsInternal;
    }

    private sealed class AxialGroup
    {
        public string BodyTag;
        public V Axis;
        public V AxisPoint;
        public double MinimumStation;
        public double MaximumStation;
        public readonly List<CylinderPatch> Patches = new List<CylinderPatch>();
    }

    private sealed class PlanePatch
    {
        public V Normal;
        public double Offset;
        public double Area;
    }

    private sealed class ThicknessAccumulator
    {
        public double Thickness;
        public int Count;
        public V Normal;
    }

    private struct V
    {
        public double X;
        public double Y;
        public double Z;

        public V(double x, double y, double z)
        {
            X = x;
            Y = y;
            Z = z;
        }

        public double Length()
        {
            return Math.Sqrt(X * X + Y * Y + Z * Z);
        }

        public V Normalize()
        {
            double length = Length();
            return length <= Epsilon ? new V(0, 0, 0) : this * (1.0 / length);
        }

        public static V operator +(V left, V right)
        {
            return new V(left.X + right.X, left.Y + right.Y, left.Z + right.Z);
        }

        public static V operator -(V left, V right)
        {
            return new V(left.X - right.X, left.Y - right.Y, left.Z - right.Z);
        }

        public static V operator *(V value, double scale)
        {
            return new V(value.X * scale, value.Y * scale, value.Z * scale);
        }
    }
}

