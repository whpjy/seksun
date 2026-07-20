using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using NXOpen;
using NXOpen.Assemblies;
using NXOpen.UF;

/// <summary>
/// NX 12 Open DLL: exports traceable orthographic projections of the current
/// work part. Every 2D polyline retains its originating NX body/edge tag.
/// The output is intended for a later PDF drawing matching service.
/// </summary>
public class Nx3d2dProjectionV8
{
    private const int PlanarFace = 22;
    private const int CylindricalFace = 16;
    private const int MaximumCandidateViews = 24;
    private const double DirectionMergeCosine = 0.9993908270190958; // 2 degrees
    private const double Epsilon = 1.0e-9;
    private const int SvgWidth = 1200;
    private const int SvgHeight = 900;
    private const double SvgMargin = 35.0;

    public static int Main(string[] args)
    {
        Session session = Session.GetSession();
        UFSession ufSession = UFSession.GetUFSession();
        ListingWindow listing = session.ListingWindow;
        listing.Open();

        Part workPart = session.Parts.Work;
        if (workPart == null)
        {
            listing.WriteLine("3D-2D Projection: no work part is open.");
            return 1;
        }

        try
        {
            List<Body> bodies = GetAnalysisBodies(workPart);
            if (bodies.Count == 0)
            {
                listing.WriteLine("3D-2D Projection: no solid body was found.");
                return 2;
            }

            List<ViewDefinition> views = BuildCandidateViews(bodies, ufSession);
            for (int viewIndex = 0; viewIndex < views.Count; viewIndex++)
            {
                views[viewIndex].Id = "VIEW-" +
                    (viewIndex + 1).ToString("000", CultureInfo.InvariantCulture);
            }
            Dictionary<Tag, string> bodyIds = BuildBodyIds(bodies);
            List<EdgeSample> edges = SampleAllEdges(bodies, bodyIds, ufSession);
            List<CylinderSurfaceSample> cylinders = SampleCylinderSurfaces(
                bodies,
                bodyIds,
                ufSession);
            MeasureResultV27 measureResult = NxMeasureV27.Analyze(
                workPart,
                ufSession);

            if (edges.Count == 0)
            {
                listing.WriteLine("3D-2D Projection: no body edge was found.");
                return 3;
            }

            string outputDirectory = CreateOutputDirectory(workPart);
            Bounds3 modelBounds = GetModelBounds(edges);
            WriteManifest(
                outputDirectory,
                workPart,
                bodies,
                edges,
                cylinders,
                measureResult,
                views,
                modelBounds);

            List<ProjectionResult> projections = new List<ProjectionResult>();
            string unitLabel = UnitLabel(workPart);
            for (int i = 0; i < views.Count; i++)
            {
                string baseName = "view_" + (i + 1).ToString("000", CultureInfo.InvariantCulture);
                ProjectionResult projection = ProjectEdges(
                    edges,
                    cylinders,
                    views[i],
                    unitLabel,
                    measureResult);
                projections.Add(projection);
                WriteViewJson(
                    Path.Combine(outputDirectory, baseName + ".json"),
                    workPart,
                    views[i],
                    projection);
                WriteViewSvg(
                    Path.Combine(outputDirectory, baseName + "_plain.svg"),
                    views[i],
                    projection,
                    false);
                WriteViewSvg(
                    Path.Combine(outputDirectory, baseName + ".svg"),
                    views[i],
                    projection,
                    true);
            }

            WriteMeasurementsJson(
                Path.Combine(outputDirectory, "measurements.json"),
                workPart,
                views,
                projections);
            NxMeasureV27.WriteJson(
                measureResult,
                Path.Combine(outputDirectory, "nxmeasure_v27.json"));
            WriteIndexHtml(outputDirectory, workPart, views);

            listing.WriteLine("3D-2D Projection export completed.");
            listing.WriteLine("Bodies: " + bodies.Count);
            listing.WriteLine("Unique edges: " + edges.Count);
            listing.WriteLine("Full cylindrical faces: " + cylinders.Count);
            listing.WriteLine("Candidate views: " + views.Count);
            int measurementCount = 0;
            foreach (ProjectionResult projection in projections)
            {
                measurementCount += projection.Measurements.Count;
            }
            listing.WriteLine("View measurements: " + measurementCount);
            listing.WriteLine("NxMeasure V27 axial features: " +
                measureResult.AxialFeatures.Count);
            listing.WriteLine("NxMeasure V27 slots: " + measureResult.Slots.Count);
            listing.WriteLine("Output: " + outputDirectory);
            return 0;
        }
        catch (Exception ex)
        {
            listing.WriteLine("3D-2D Projection failed:");
            listing.WriteLine(ex.ToString());
            return 99;
        }
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
                CollectComponentBodies(child, result, tags);
            }
        }
        return result;
    }

    private static void CollectComponentBodies(
        Component component,
        List<Body> result,
        HashSet<Tag> tags)
    {
        Part prototype = component.Prototype as Part;
        if (prototype != null)
        {
            foreach (Body prototypeBody in prototype.Bodies)
            {
                Body occurrence = component.FindOccurrence(prototypeBody) as Body;
                AddBody(result, tags, occurrence);
            }
        }

        foreach (Component child in component.GetChildren())
        {
            CollectComponentBodies(child, result, tags);
        }
    }

    private static void AddBody(
        List<Body> result,
        HashSet<Tag> tags,
        Body body)
    {
        if (body == null || !body.IsSolidBody || tags.Contains(body.Tag))
        {
            return;
        }
        tags.Add(body.Tag);
        result.Add(body);
    }

    private static Dictionary<Tag, string> BuildBodyIds(List<Body> bodies)
    {
        Dictionary<Tag, string> result = new Dictionary<Tag, string>();
        for (int i = 0; i < bodies.Count; i++)
        {
            result[bodies[i].Tag] = "BODY-" +
                (i + 1).ToString("000", CultureInfo.InvariantCulture);
        }
        return result;
    }

    private static List<ViewDefinition> BuildCandidateViews(
        List<Body> bodies,
        UFSession ufSession)
    {
        List<ViewDefinition> result = new List<ViewDefinition>();

        AddCandidate(result, new V3(1, 0, 0), "+X", "standard");
        AddCandidate(result, new V3(-1, 0, 0), "-X", "standard");
        AddCandidate(result, new V3(0, 1, 0), "+Y", "standard");
        AddCandidate(result, new V3(0, -1, 0), "-Y", "standard");
        AddCandidate(result, new V3(0, 0, 1), "+Z", "standard");
        AddCandidate(result, new V3(0, 0, -1), "-Z", "standard");

        foreach (Body body in bodies)
        {
            foreach (Face face in body.GetFaces())
            {
                if (result.Count >= MaximumCandidateViews)
                {
                    break;
                }

                int type;
                double[] point = new double[3];
                double[] direction = new double[3];
                double[] box = new double[6];
                double radius;
                double radiusData;
                int normalDirection;
                try
                {
                    ufSession.Modl.AskFaceData(
                        face.Tag,
                        out type,
                        point,
                        direction,
                        box,
                        out radius,
                        out radiusData,
                        out normalDirection);
                }
                catch
                {
                    continue;
                }

                if (type != PlanarFace && type != CylindricalFace)
                {
                    continue;
                }

                V3 axis = new V3(direction[0], direction[1], direction[2]);
                if (axis.Length() <= Epsilon)
                {
                    continue;
                }

                string prefix = type == PlanarFace ? "PLANE" : "CYLINDER";
                AddCandidate(result, axis, prefix + "+", prefix.ToLowerInvariant());
                AddCandidate(result, axis.Negate(), prefix + "-", prefix.ToLowerInvariant());
            }
        }
        return result;
    }

    private static void AddCandidate(
        List<ViewDefinition> views,
        V3 direction,
        string suggestedName,
        string source)
    {
        if (views.Count >= MaximumCandidateViews)
        {
            return;
        }

        V3 n = direction.Normalize();
        foreach (ViewDefinition existing in views)
        {
            if (V3.Dot(n, existing.N) >= DirectionMergeCosine)
            {
                return;
            }
        }

        V3 reference = Math.Abs(V3.Dot(n, new V3(0, 0, 1))) < 0.92
            ? new V3(0, 0, 1)
            : new V3(0, 1, 0);
        V3 u = V3.Cross(reference, n).Normalize();
        V3 v = V3.Cross(n, u).Normalize();

        views.Add(new ViewDefinition
        {
            Name = suggestedName,
            Source = source,
            N = n,
            U = u,
            V = v
        });
    }

    private static List<EdgeSample> SampleAllEdges(
        List<Body> bodies,
        Dictionary<Tag, string> bodyIds,
        UFSession ufSession)
    {
        List<EdgeSample> result = new List<EdgeSample>();
        HashSet<Tag> edgeTags = new HashSet<Tag>();
        int edgeNumber = 0;

        foreach (Body body in bodies)
        {
            foreach (Edge edge in body.GetEdges())
            {
                if (edgeTags.Contains(edge.Tag))
                {
                    continue;
                }
                edgeTags.Add(edge.Tag);
                edgeNumber++;

                EdgeSample sample = new EdgeSample();
                sample.EdgeId = "EDGE-" +
                    edgeNumber.ToString("000000", CultureInfo.InvariantCulture);
                sample.EdgeTag = edge.Tag.ToString();
                sample.BodyId = bodyIds.ContainsKey(body.Tag)
                    ? bodyIds[body.Tag]
                    : "BODY-UNKNOWN";
                sample.BodyTag = body.Tag.ToString();
                sample.EdgeType = edge.SolidEdgeType.ToString();
                sample.Points = SampleEdge(edge, ufSession);

                if (sample.Points.Count >= 2)
                {
                    result.Add(sample);
                }
            }
        }
        return result;
    }

    /// <summary>
    /// Finds full cylindrical face patches whose side silhouettes are not
    /// represented by topological edges. Imported cylinders normally have
    /// two circular boundary edges and no longitudinal boundary edge.
    /// Partial cylinders already have longitudinal B-Rep edges and are not
    /// supplemented here, avoiding false silhouettes outside a trimmed patch.
    /// </summary>
    private static List<CylinderSurfaceSample> SampleCylinderSurfaces(
        List<Body> bodies,
        Dictionary<Tag, string> bodyIds,
        UFSession ufSession)
    {
        List<CylinderSurfaceSample> result = new List<CylinderSurfaceSample>();
        int faceNumber = 0;

        foreach (Body body in bodies)
        {
            foreach (Face face in body.GetFaces())
            {
                int type;
                double[] point = new double[3];
                double[] direction = new double[3];
                double[] box = new double[6];
                double radius;
                double radiusData;
                int normalDirection;
                try
                {
                    ufSession.Modl.AskFaceData(
                        face.Tag,
                        out type,
                        point,
                        direction,
                        box,
                        out radius,
                        out radiusData,
                        out normalDirection);
                }
                catch
                {
                    continue;
                }

                if (type != CylindricalFace || Math.Abs(radius) <= Epsilon)
                {
                    continue;
                }

                V3 axis = new V3(
                    direction[0],
                    direction[1],
                    direction[2]).Normalize();
                if (axis.Length() <= Epsilon)
                {
                    continue;
                }

                int circularEdges = 0;
                int longitudinalEdges = 0;
                double minimum = double.PositiveInfinity;
                double maximum = double.NegativeInfinity;
                V3 axisPoint = new V3(point[0], point[1], point[2]);

                foreach (Edge edge in face.GetEdges())
                {
                    if (edge.SolidEdgeType == Edge.EdgeType.Circular ||
                        edge.SolidEdgeType == Edge.EdgeType.Elliptical)
                    {
                        circularEdges++;
                    }
                    else
                    {
                        longitudinalEdges++;
                    }

                    foreach (V3 boundaryPoint in SampleEdge(edge, ufSession))
                    {
                        double station = V3.Dot(boundaryPoint - axisPoint, axis);
                        minimum = Math.Min(minimum, station);
                        maximum = Math.Max(maximum, station);
                    }
                }

                // A full cylindrical wall needs generated view silhouettes.
                // A partial cylindrical patch normally exposes two or more
                // longitudinal trimming edges which are already exported.
                if (circularEdges == 0 ||
                    longitudinalEdges > 1 ||
                    double.IsInfinity(minimum) ||
                    maximum - minimum <= Epsilon)
                {
                    continue;
                }

                faceNumber++;
                result.Add(new CylinderSurfaceSample
                {
                    FaceId = "CYL-FACE-" +
                        faceNumber.ToString("000000", CultureInfo.InvariantCulture),
                    FaceTag = face.Tag.ToString(),
                    BodyId = bodyIds.ContainsKey(body.Tag)
                        ? bodyIds[body.Tag]
                        : "BODY-UNKNOWN",
                    BodyTag = body.Tag.ToString(),
                    AxisPoint = axisPoint,
                    Axis = axis,
                    Radius = Math.Abs(radius),
                    MinimumStation = minimum,
                    MaximumStation = maximum,
                    IsInternal = normalDirection < 0
                });
            }
        }
        return result;
    }

    private static List<V3> SampleEdge(Edge edge, UFSession ufSession)
    {
        List<V3> points = new List<V3>();
        int count = SampleCount(edge.SolidEdgeType);

        try
        {
            double[] limits = new double[2];
            int periodic;
            ufSession.Curve.AskParameterization(edge.Tag, limits, out periodic);

            if (Math.Abs(limits[1] - limits[0]) > Epsilon)
            {
                for (int i = 0; i <= count; i++)
                {
                    double t = limits[0] +
                        (limits[1] - limits[0]) * i / (double)count;
                    double[] evaluated = new double[3];
                    ufSession.Curve.EvaluateCurve(edge.Tag, t, 0, evaluated);
                    AddPointIfDifferent(
                        points,
                        new V3(evaluated[0], evaluated[1], evaluated[2]));
                }
            }
        }
        catch
        {
            // Some imported/convergent edges cannot be evaluated as UF curves.
        }

        if (points.Count < 2)
        {
            Point3d first;
            Point3d second;
            edge.GetVertices(out first, out second);
            AddPointIfDifferent(points, V3.From(first));
            AddPointIfDifferent(points, V3.From(second));
        }
        return points;
    }

    private static int SampleCount(Edge.EdgeType type)
    {
        switch (type)
        {
            case Edge.EdgeType.Linear:
                return 1;
            case Edge.EdgeType.Circular:
                return 48;
            case Edge.EdgeType.Elliptical:
                return 56;
            case Edge.EdgeType.Spline:
            case Edge.EdgeType.SpCurve:
            case Edge.EdgeType.Intersection:
                return 64;
            default:
                return 24;
        }
    }

    private static void AddPointIfDifferent(List<V3> points, V3 point)
    {
        if (points.Count == 0 || (points[points.Count - 1] - point).Length() > Epsilon)
        {
            points.Add(point);
        }
    }

    private static Bounds3 GetModelBounds(List<EdgeSample> edges)
    {
        Bounds3 bounds = new Bounds3();
        foreach (EdgeSample edge in edges)
        {
            foreach (V3 point in edge.Points)
            {
                bounds.Include(point);
            }
        }
        return bounds;
    }

    private static ProjectionResult ProjectEdges(
        List<EdgeSample> edges,
        List<CylinderSurfaceSample> cylinders,
        ViewDefinition view,
        string unitLabel,
        MeasureResultV27 measureResult)
    {
        ProjectionResult result = new ProjectionResult();
        foreach (EdgeSample edge in edges)
        {
            ProjectedEdge projected = new ProjectedEdge();
            projected.Source = edge;
            foreach (V3 point in edge.Points)
            {
                P2 p = new P2(V3.Dot(point, view.U), V3.Dot(point, view.V));
                projected.Points.Add(p);
                result.Bounds.Include(p);
            }
            result.Edges.Add(projected);
        }

        foreach (CylinderSurfaceSample cylinder in cylinders)
        {
            V3 silhouetteOffsetDirection = V3.Cross(
                cylinder.Axis,
                view.N);
            if (silhouetteOffsetDirection.Length() <= 1.0e-7)
            {
                // Looking along the cylinder axis: circular boundary edges
                // already describe the correct projection.
                continue;
            }

            V3 offset = silhouetteOffsetDirection.Normalize() * cylinder.Radius;
            V3 axisStart = cylinder.AxisPoint +
                cylinder.Axis * cylinder.MinimumStation;
            V3 axisEnd = cylinder.AxisPoint +
                cylinder.Axis * cylinder.MaximumStation;

            AddCylinderSilhouette(
                result,
                cylinder,
                view,
                axisStart + offset,
                axisEnd + offset,
                1);
            AddCylinderSilhouette(
                result,
                cylinder,
                view,
                axisStart - offset,
                axisEnd - offset,
                2);
        }
        AddViewMeasurements(
            result,
            cylinders,
            view,
            unitLabel,
            measureResult);
        return result;
    }

    private static void AddViewMeasurements(
        ProjectionResult result,
        List<CylinderSurfaceSample> cylinders,
        ViewDefinition view,
        string unitLabel,
        MeasureResultV27 measureResult)
    {
        double width = result.Bounds.Width();
        double height = result.Bounds.Height();
        if (width > Epsilon)
        {
            double y = result.Bounds.MinY + Math.Max(height * 0.035, width * 0.002);
            AddMeasurement(
                result,
                view.Id + "-OVERALL-WIDTH",
                "overall_width",
                "W=" + DisplayNumber(width, 3) + " " + unitLabel,
                width,
                unitLabel,
                new P2(result.Bounds.MinX, y),
                new P2(result.Bounds.MaxX, y),
                null,
                null,
                null);
        }
        if (height > Epsilon)
        {
            double x = result.Bounds.MinX + Math.Max(width * 0.035, height * 0.002);
            AddMeasurement(
                result,
                view.Id + "-OVERALL-HEIGHT",
                "overall_height",
                "H=" + DisplayNumber(height, 3) + " " + unitLabel,
                height,
                unitLabel,
                new P2(x, result.Bounds.MinY),
                new P2(x, result.Bounds.MaxY),
                null,
                null,
                null);
        }

        foreach (CylinderSurfaceSample cylinder in cylinders)
        {
            double alignment = Math.Abs(V3.Dot(cylinder.Axis, view.N));
            V3 center3d = cylinder.AxisPoint + cylinder.Axis *
                ((cylinder.MinimumStation + cylinder.MaximumStation) * 0.5);

            // Internal holes are dimensioned in an axial view. External pins
            // and legs also receive a side-view diameter measurement.
            bool v27FeatureOwnsMeasurement = HasV27Face(
                measureResult,
                cylinder.FaceTag);
            if ((alignment >= 0.985 || !cylinder.IsInternal) &&
                !v27FeatureOwnsMeasurement)
            {
                V3 diameterDirection;
                if (alignment >= 0.985)
                {
                    diameterDirection = view.U;
                }
                else
                {
                    diameterDirection = V3.Cross(cylinder.Axis, view.N).Normalize();
                }

                if (diameterDirection.Length() > Epsilon)
                {
                    P2 first = ProjectPoint(
                        center3d - diameterDirection * cylinder.Radius,
                        view);
                    P2 second = ProjectPoint(
                        center3d + diameterDirection * cylinder.Radius,
                        view);
                    double diameter = cylinder.Radius * 2.0;
                    AddMeasurement(
                        result,
                        view.Id + "-" + cylinder.FaceId + "-D",
                        cylinder.IsInternal
                            ? "internal_diameter"
                            : "external_diameter",
                        "Ø" + Number(diameter) + " " + unitLabel,
                        diameter,
                        unitLabel,
                        first,
                        second,
                        cylinder.FaceId,
                        cylinder.FaceTag,
                        cylinder.BodyTag);
                }
            }

            // Length is meaningful when the cylinder axis lies nearly in the
            // view plane. Restrict it to external cylinders to avoid labeling
            // every hidden hole wall in side views.
            if (!cylinder.IsInternal && alignment <= 0.20 &&
                !v27FeatureOwnsMeasurement)
            {
                V3 axisStart = cylinder.AxisPoint +
                    cylinder.Axis * cylinder.MinimumStation;
                V3 axisEnd = cylinder.AxisPoint +
                    cylinder.Axis * cylinder.MaximumStation;
                double length = cylinder.MaximumStation - cylinder.MinimumStation;
                AddMeasurement(
                    result,
                    view.Id + "-" + cylinder.FaceId + "-L",
                    "external_cylinder_length",
                    "L=" + DisplayNumber(length, 3) + " " + unitLabel,
                    length,
                    unitLabel,
                    ProjectPoint(axisStart, view),
                    ProjectPoint(axisEnd, view),
                    cylinder.FaceId,
                    cylinder.FaceTag,
                    cylinder.BodyTag);
            }
        }

        AddV27ViewMeasurements(
            result,
            view,
            unitLabel,
            measureResult);
        AddProjectedArcRadiusMeasurements(result, view, unitLabel);
        AddInclinedEdgeMeasurements(result, view, unitLabel);
    }

    private static bool HasV27Face(
        MeasureResultV27 result,
        string faceTag)
    {
        if (faceTag == null)
        {
            return false;
        }
        foreach (AxialFeatureV27 feature in result.AxialFeatures)
        {
            if (feature.FaceTags.Contains(faceTag))
            {
                return true;
            }
        }
        return false;
    }

    private static void AddV27ViewMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        MeasureResultV27 data)
    {
        List<AxialFeatureV27> axialHoles = new List<AxialFeatureV27>();
        HashSet<string> patternFeatureIds = AddV27HolePatternMeasurements(
            result,
            view,
            unitLabel,
            data);
        foreach (AxialFeatureV27 feature in data.AxialFeatures)
        {
            if (!feature.AutoAnnotate || feature.Axis == null ||
                feature.Center == null || feature.Diameters.Count == 0)
            {
                continue;
            }

            V3 axis = PublicVector(feature.Axis).Normalize();
            V3 center = PublicVector(feature.Center);
            double alignment = Math.Abs(V3.Dot(axis, view.N));
            bool isHole = feature.Type == "ROUND_HOLE" ||
                feature.Type == "COMPOSITE_HOLE";

            if (alignment >= 0.985 && isHole)
            {
                if (!patternFeatureIds.Contains(feature.Id))
                {
                    axialHoles.Add(feature);
                    double diameter = feature.Diameters[feature.Diameters.Count - 1];
                    string diameterText = JoinDiameters(
                        feature.Diameters,
                        feature.DisplayDecimals);
                    AddMeasurement(
                        result,
                        view.Id + "-V27-" + feature.Id + "-D",
                        "v27_axial_feature_diameters",
                        feature.Id + " " + diameterText + " " + unitLabel,
                        diameter,
                        unitLabel,
                        ProjectPoint(center - view.U * (diameter * 0.5), view),
                        ProjectPoint(center + view.U * (diameter * 0.5), view),
                        feature.Id,
                        feature.FaceTags.Count > 0 ? feature.FaceTags[0] : null,
                        feature.BodyTag);
                }
            }

            if (!isHole && alignment <= 0.20 &&
                feature.StartPoint != null && feature.EndPoint != null)
            {
                AddMeasurement(
                    result,
                    view.Id + "-V27-" + feature.Id + "-L",
                    "v27_axial_feature_length",
                    feature.Id + " L=" +
                        DisplayNumber(feature.TotalLength, feature.DisplayDecimals) +
                        " " + unitLabel,
                    feature.TotalLength,
                    unitLabel,
                    ProjectPoint(PublicVector(feature.StartPoint), view),
                    ProjectPoint(PublicVector(feature.EndPoint), view),
                    feature.Id,
                    feature.FaceTags.Count > 0 ? feature.FaceTags[0] : null,
                    feature.BodyTag);
            }
        }

        AddNearestAlignedHolePitches(
            result,
            view,
            unitLabel,
            axialHoles);

        foreach (SlotFeatureV27 slot in data.Slots)
        {
            if (!slot.AutoAnnotate || slot.Axis == null ||
                slot.Center == null || slot.Direction == null ||
                Math.Abs(V3.Dot(PublicVector(slot.Axis).Normalize(), view.N)) < 0.985)
            {
                continue;
            }
            V3 center = PublicVector(slot.Center);
            V3 longAxis = PublicVector(slot.Direction).Normalize();
            V3 widthAxis = V3.Cross(PublicVector(slot.Axis), longAxis).Normalize();
            AddMeasurement(
                result,
                view.Id + "-V27-" + slot.Id + "-L",
                "v27_slot_overall_length",
                slot.Id + " L=" + DisplayNumber(slot.OverallLength, slot.DisplayDecimals) +
                    " " + unitLabel,
                slot.OverallLength,
                unitLabel,
                ProjectPoint(center - longAxis * (slot.OverallLength * 0.5), view),
                ProjectPoint(center + longAxis * (slot.OverallLength * 0.5), view),
                slot.Id,
                slot.FaceTags.Count > 0 ? slot.FaceTags[0] : null,
                slot.BodyTag);
            AddMeasurement(
                result,
                view.Id + "-V27-" + slot.Id + "-W",
                "v27_slot_width",
                slot.Id + " W=" + DisplayNumber(slot.Width, slot.DisplayDecimals) +
                    " " + unitLabel,
                slot.Width,
                unitLabel,
                ProjectPoint(center - widthAxis * (slot.Width * 0.5), view),
                ProjectPoint(center + widthAxis * (slot.Width * 0.5), view),
                slot.Id,
                slot.FaceTags.Count > 0 ? slot.FaceTags[0] : null,
                slot.BodyTag);
        }
    }

    private static HashSet<string> AddV27HolePatternMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        MeasureResultV27 data)
    {
        Dictionary<string, List<AxialFeatureV27>> groups =
            new Dictionary<string, List<AxialFeatureV27>>();
        foreach (AxialFeatureV27 feature in data.AxialFeatures)
        {
            bool isHole = feature.Type == "ROUND_HOLE" ||
                feature.Type == "COMPOSITE_HOLE";
            if (!isHole || !feature.AutoAnnotate || feature.Axis == null ||
                feature.Center == null || feature.Diameters.Count == 0 ||
                Math.Abs(V3.Dot(PublicVector(feature.Axis).Normalize(), view.N)) < 0.985)
            {
                continue;
            }
            string key = JoinDiameters(feature.Diameters, feature.DisplayDecimals);
            if (!groups.ContainsKey(key))
            {
                groups[key] = new List<AxialFeatureV27>();
            }
            groups[key].Add(feature);
        }

        HashSet<string> groupedFeatureIds = new HashSet<string>();
        int patternNumber = 0;
        foreach (KeyValuePair<string, List<AxialFeatureV27>> entry in groups)
        {
            List<PatternPoint> points = new List<PatternPoint>();
            foreach (AxialFeatureV27 feature in entry.Value)
            {
                P2 projected = ProjectPoint(PublicVector(feature.Center), view);
                PatternPoint existing = FindPatternPoint(points, projected);
                if (existing == null)
                {
                    existing = new PatternPoint { Point = projected, Feature = feature };
                    points.Add(existing);
                }
                existing.FeatureIds.Add(feature.Id);
            }
            if (points.Count < 2)
            {
                continue;
            }

            patternNumber++;
            string patternId = "HP" +
                patternNumber.ToString("000", CultureInfo.InvariantCulture);
            foreach (AxialFeatureV27 feature in entry.Value)
            {
                groupedFeatureIds.Add(feature.Id);
            }

            AxialFeatureV27 representative = points[0].Feature;
            double diameter = representative.Diameters[
                representative.Diameters.Count - 1];
            P2 center = points[0].Point;
            AddMeasurement(
                result,
                view.Id + "-V28-" + patternId + "-D",
                "hole_pattern_diameter",
                points.Count.ToString(CultureInfo.InvariantCulture) + "× " +
                    entry.Key + " " + unitLabel,
                diameter,
                unitLabel,
                new P2(center.X - diameter * 0.5, center.Y),
                new P2(center.X + diameter * 0.5, center.Y),
                patternId,
                representative.FaceTags.Count > 0
                    ? representative.FaceTags[0]
                    : null,
                null);

            AddPatternSpanMeasurements(
                result,
                view,
                unitLabel,
                patternId,
                points);
        }
        return groupedFeatureIds;
    }

    private static PatternPoint FindPatternPoint(
        List<PatternPoint> points,
        P2 target)
    {
        foreach (PatternPoint point in points)
        {
            if (Math.Abs(point.Point.X - target.X) <= 0.05 &&
                Math.Abs(point.Point.Y - target.Y) <= 0.05)
            {
                return point;
            }
        }
        return null;
    }

    private static void AddPatternSpanMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        string patternId,
        List<PatternPoint> points)
    {
        PatternPoint minimumX = points[0];
        PatternPoint maximumX = points[0];
        PatternPoint minimumY = points[0];
        PatternPoint maximumY = points[0];
        foreach (PatternPoint point in points)
        {
            if (point.Point.X < minimumX.Point.X) minimumX = point;
            if (point.Point.X > maximumX.Point.X) maximumX = point;
            if (point.Point.Y < minimumY.Point.Y) minimumY = point;
            if (point.Point.Y > maximumY.Point.Y) maximumY = point;
        }
        double spanX = maximumX.Point.X - minimumX.Point.X;
        double spanY = maximumY.Point.Y - minimumY.Point.Y;
        if (spanX > 0.05)
        {
            double dimensionY = Math.Min(minimumX.Point.Y, maximumX.Point.Y);
            AddMeasurement(
                result,
                view.Id + "-V28-" + patternId + "-PX",
                "hole_pattern_span_x",
                patternId + " X=" + DisplayNumber(spanX, 3) + " " + unitLabel,
                spanX,
                unitLabel,
                new P2(minimumX.Point.X, dimensionY),
                new P2(maximumX.Point.X, dimensionY),
                patternId,
                null,
                null);
        }
        if (spanY > 0.05)
        {
            double dimensionX = Math.Min(minimumY.Point.X, maximumY.Point.X);
            AddMeasurement(
                result,
                view.Id + "-V28-" + patternId + "-PY",
                "hole_pattern_span_y",
                patternId + " Y=" + DisplayNumber(spanY, 3) + " " + unitLabel,
                spanY,
                unitLabel,
                new P2(dimensionX, minimumY.Point.Y),
                new P2(dimensionX, maximumY.Point.Y),
                patternId,
                null,
                null);
        }
    }

    private static void AddNearestAlignedHolePitches(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        List<AxialFeatureV27> holes)
    {
        HashSet<string> emitted = new HashSet<string>();
        for (int i = 0; i < holes.Count; i++)
        {
            P2 first = ProjectPoint(PublicVector(holes[i].Center), view);
            int nearestU = -1;
            int nearestV = -1;
            double nearestUDistance = double.MaxValue;
            double nearestVDistance = double.MaxValue;
            for (int j = 0; j < holes.Count; j++)
            {
                if (i == j) continue;
                P2 second = ProjectPoint(PublicVector(holes[j].Center), view);
                if (Math.Abs(second.Y - first.Y) <= 0.05 &&
                    Math.Abs(second.X - first.X) > 0.05 &&
                    Math.Abs(second.X - first.X) < nearestUDistance)
                {
                    nearestU = j;
                    nearestUDistance = Math.Abs(second.X - first.X);
                }
                if (Math.Abs(second.X - first.X) <= 0.05 &&
                    Math.Abs(second.Y - first.Y) > 0.05 &&
                    Math.Abs(second.Y - first.Y) < nearestVDistance)
                {
                    nearestV = j;
                    nearestVDistance = Math.Abs(second.Y - first.Y);
                }
            }
            AddV27Pitch(result, view, unitLabel, holes, i, nearestU, emitted);
            AddV27Pitch(result, view, unitLabel, holes, i, nearestV, emitted);
        }
    }

    private static void AddV27Pitch(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        List<AxialFeatureV27> holes,
        int firstIndex,
        int secondIndex,
        HashSet<string> emitted)
    {
        if (secondIndex < 0) return;
        string firstId = holes[firstIndex].Id;
        string secondId = holes[secondIndex].Id;
        string key = string.Compare(firstId, secondId, StringComparison.Ordinal) < 0
            ? firstId + ":" + secondId
            : secondId + ":" + firstId;
        if (!emitted.Add(key)) return;
        V3 first = PublicVector(holes[firstIndex].Center);
        V3 second = PublicVector(holes[secondIndex].Center);
        P2 first2 = ProjectPoint(first, view);
        P2 second2 = ProjectPoint(second, view);
        double distance = Math.Sqrt(
            (second2.X - first2.X) * (second2.X - first2.X) +
            (second2.Y - first2.Y) * (second2.Y - first2.Y));
        if (distance <= Epsilon) return;
        AddMeasurement(
            result,
            view.Id + "-V27-" + key.Replace(':', '-') + "-P",
            "v27_nearest_aligned_hole_pitch",
            key.Replace(':', '-') + " P=" + DisplayNumber(distance, 3) +
                " " + unitLabel,
            distance,
            unitLabel,
            first2,
            second2,
            key,
            null,
            null);
    }

    private static V3 PublicVector(MeasureVectorV27 value)
    {
        return new V3(value.X, value.Y, value.Z);
    }

    private static string JoinDiameters(List<double> diameters, int decimals)
    {
        StringBuilder builder = new StringBuilder();
        for (int i = 0; i < diameters.Count; i++)
        {
            if (i > 0) builder.Append('/');
            builder.Append('Ø');
            builder.Append(DisplayNumber(diameters[i], decimals));
        }
        return builder.ToString();
    }

    private static void AddProjectedArcRadiusMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel)
    {
        Dictionary<string, ArcRadiusGroup> groups =
            new Dictionary<string, ArcRadiusGroup>();
        foreach (ProjectedEdge edge in result.Edges)
        {
            if (edge.Source.EdgeType.IndexOf("Circular", StringComparison.OrdinalIgnoreCase) < 0 ||
                edge.Points.Count < 4)
            {
                continue;
            }
            double chord = Distance2(edge.Points[0], edge.Points[edge.Points.Count - 1]);
            P2 center;
            double radius;
            if (!TryFitProjectedCircle(edge.Points, out center, out radius) ||
                chord <= Math.Max(0.02, radius * 0.02))
            {
                continue;
            }
            string key = DisplayNumber(radius, 2);
            ArcRadiusGroup group;
            if (!groups.TryGetValue(key, out group))
            {
                group = new ArcRadiusGroup { Radius = radius };
                groups.Add(key, group);
            }
            if (!ContainsArcCenter(group.Centers, center))
            {
                group.Centers.Add(center);
                group.Edges.Add(edge);
            }
        }

        List<ArcRadiusGroup> ordered = new List<ArcRadiusGroup>(groups.Values);
        ordered.Sort(delegate(ArcRadiusGroup left, ArcRadiusGroup right)
        {
            int count = right.Centers.Count.CompareTo(left.Centers.Count);
            return count != 0 ? count : right.Radius.CompareTo(left.Radius);
        });
        int outputCount = Math.Min(12, ordered.Count);
        for (int i = 0; i < outputCount; i++)
        {
            ArcRadiusGroup group = ordered[i];
            if (group.Edges.Count == 0) continue;
            ProjectedEdge representative = group.Edges[0];
            P2 center = group.Centers[0];
            P2 arcPoint = representative.Points[representative.Points.Count / 2];
            string multiplier = group.Centers.Count > 1
                ? group.Centers.Count.ToString(CultureInfo.InvariantCulture) + "× "
                : string.Empty;
            AddMeasurement(
                result,
                view.Id + "-ARC-R" + (i + 1).ToString("000", CultureInfo.InvariantCulture),
                "projected_arc_radius",
                multiplier + "R" + DisplayNumber(group.Radius, 3) + " " + unitLabel,
                group.Radius,
                unitLabel,
                center,
                arcPoint,
                representative.Source.EdgeId,
                null,
                representative.Source.BodyTag);
        }
    }

    private static bool TryFitProjectedCircle(
        List<P2> points,
        out P2 center,
        out double radius)
    {
        center = new P2(0, 0);
        radius = 0.0;
        P2 first = points[0];
        P2 second = points[points.Count / 3];
        P2 third = points[(points.Count * 2) / 3];
        double determinant = 2.0 * (
            first.X * (second.Y - third.Y) +
            second.X * (third.Y - first.Y) +
            third.X * (first.Y - second.Y));
        if (Math.Abs(determinant) <= 1.0e-8)
        {
            return false;
        }
        double firstSquare = first.X * first.X + first.Y * first.Y;
        double secondSquare = second.X * second.X + second.Y * second.Y;
        double thirdSquare = third.X * third.X + third.Y * third.Y;
        center = new P2(
            (firstSquare * (second.Y - third.Y) +
             secondSquare * (third.Y - first.Y) +
             thirdSquare * (first.Y - second.Y)) / determinant,
            (firstSquare * (third.X - second.X) +
             secondSquare * (first.X - third.X) +
             thirdSquare * (second.X - first.X)) / determinant);
        radius = Distance2(center, first);
        if (radius <= 0.01 || double.IsNaN(radius) || double.IsInfinity(radius))
        {
            return false;
        }
        double maximumError = 0.0;
        foreach (P2 point in points)
        {
            maximumError = Math.Max(
                maximumError,
                Math.Abs(Distance2(center, point) - radius));
        }
        return maximumError <= Math.Max(0.02, radius * 0.003);
    }

    private static bool ContainsArcCenter(List<P2> centers, P2 target)
    {
        foreach (P2 center in centers)
        {
            if (Distance2(center, target) <= 0.05)
            {
                return true;
            }
        }
        return false;
    }

    private static void AddInclinedEdgeMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel)
    {
        double diagonal = Math.Sqrt(
            result.Bounds.Width() * result.Bounds.Width() +
            result.Bounds.Height() * result.Bounds.Height());
        Dictionary<string, InclinedEdgeGroup> groups =
            new Dictionary<string, InclinedEdgeGroup>();
        foreach (ProjectedEdge edge in result.Edges)
        {
            if (edge.Source.EdgeType.IndexOf("Linear", StringComparison.OrdinalIgnoreCase) < 0 ||
                edge.Points.Count < 2 || edge.Source.Points.Count < 2)
            {
                continue;
            }
            V3 vector3 = edge.Source.Points[edge.Source.Points.Count - 1] -
                edge.Source.Points[0];
            double length3 = vector3.Length();
            P2 first = edge.Points[0];
            P2 second = edge.Points[edge.Points.Count - 1];
            double length2 = Distance2(first, second);
            if (length3 <= Epsilon || length2 < diagonal * 0.025 ||
                Math.Abs(V3.Dot(vector3.Normalize(), view.N)) > 0.02 ||
                Math.Abs(length2 - length3) > Math.Max(0.02, length3 * 0.002))
            {
                continue;
            }
            double angle = Math.Atan2(
                Math.Abs(second.Y - first.Y),
                Math.Abs(second.X - first.X)) * 180.0 / Math.PI;
            if (angle < 2.0 || angle > 88.0)
            {
                continue;
            }
            string key = DisplayNumber(angle, 1) + ":" + DisplayNumber(length2, 2);
            InclinedEdgeGroup group;
            if (!groups.TryGetValue(key, out group))
            {
                group = new InclinedEdgeGroup
                {
                    Angle = angle,
                    Length = length2,
                    Edge = edge
                };
                groups.Add(key, group);
            }
            group.Count++;
        }

        List<InclinedEdgeGroup> ordered = new List<InclinedEdgeGroup>(groups.Values);
        ordered.Sort(delegate(InclinedEdgeGroup left, InclinedEdgeGroup right)
        {
            return right.Length.CompareTo(left.Length);
        });
        int outputCount = Math.Min(8, ordered.Count);
        for (int i = 0; i < outputCount; i++)
        {
            InclinedEdgeGroup group = ordered[i];
            P2 first = group.Edge.Points[0];
            P2 second = group.Edge.Points[group.Edge.Points.Count - 1];
            string multiplier = group.Count > 1
                ? group.Count.ToString(CultureInfo.InvariantCulture) + "× "
                : string.Empty;
            AddMeasurement(
                result,
                view.Id + "-INCLINED-" +
                    (i + 1).ToString("000", CultureInfo.InvariantCulture),
                "inclined_edge_length_angle",
                multiplier + "L=" + DisplayNumber(group.Length, 3) +
                    " " + unitLabel + " / ∠" + DisplayNumber(group.Angle, 2) + "°",
                group.Length,
                unitLabel,
                first,
                second,
                group.Edge.Source.EdgeId,
                null,
                group.Edge.Source.BodyTag);
        }
    }

    private static double Distance2(P2 first, P2 second)
    {
        double dx = second.X - first.X;
        double dy = second.Y - first.Y;
        return Math.Sqrt(dx * dx + dy * dy);
    }

    private static bool HasMatchedNxMeasureHole(
        NxMeasureData data,
        string faceTag)
    {
        if (faceTag == null)
        {
            return false;
        }
        foreach (NxMeasureHole hole in data.Holes)
        {
            if (hole.FaceTag == faceTag)
            {
                return true;
            }
        }
        return false;
    }

    private static void AddNxMeasureViewMeasurements(
        ProjectionResult result,
        ViewDefinition view,
        string unitLabel,
        NxMeasureData data)
    {
        V3 yAxis = new V3(0, 1, 0);
        if (Math.Abs(V3.Dot(view.N, yAxis)) < 0.985)
        {
            return;
        }

        foreach (NxMeasureHole hole in data.Holes)
        {
            V3 center = new V3(hole.CenterX, hole.CenterY, hole.CenterZ);
            P2 first = ProjectPoint(center - view.U * (hole.Diameter * 0.5), view);
            P2 second = ProjectPoint(center + view.U * (hole.Diameter * 0.5), view);
            AddMeasurement(
                result,
                view.Id + "-V26-" + hole.Id + "-D",
                "v26_hole_diameter",
                hole.Id + " Ø" + Number(hole.Diameter) + " " + unitLabel,
                hole.Diameter,
                unitLabel,
                first,
                second,
                hole.Id,
                hole.FaceTag,
                hole.BodyTag);
        }

        foreach (NxMeasureSlot slot in data.Slots)
        {
            V3 center = new V3(slot.CenterX, slot.CenterY, slot.CenterZ);
            V3 longAxis = slot.Direction.IndexOf("X", StringComparison.OrdinalIgnoreCase) >= 0
                ? new V3(1, 0, 0)
                : new V3(0, 0, 1);
            V3 widthAxis = Math.Abs(V3.Dot(longAxis, new V3(1, 0, 0))) > 0.9
                ? new V3(0, 0, 1)
                : new V3(1, 0, 0);
            AddMeasurement(
                result,
                view.Id + "-V26-" + slot.Id + "-L",
                "v26_slot_overall_length",
                slot.Id + " L=" + Number(slot.OverallLength) + " " + unitLabel,
                slot.OverallLength,
                unitLabel,
                ProjectPoint(center - longAxis * (slot.OverallLength * 0.5), view),
                ProjectPoint(center + longAxis * (slot.OverallLength * 0.5), view),
                slot.Id,
                null,
                null);
            AddMeasurement(
                result,
                view.Id + "-V26-" + slot.Id + "-W",
                "v26_slot_width",
                slot.Id + " W=" + Number(slot.Width) + " " + unitLabel,
                slot.Width,
                unitLabel,
                ProjectPoint(center - widthAxis * (slot.Width * 0.5), view),
                ProjectPoint(center + widthAxis * (slot.Width * 0.5), view),
                slot.Id,
                null,
                null);
        }

        // Only aligned hole pairs are dimensioned automatically. This avoids
        // the unreadable n*(n-1)/2 distance explosion from the full V26 report.
        for (int i = 0; i < data.Holes.Count; i++)
        {
            for (int j = i + 1; j < data.Holes.Count; j++)
            {
                NxMeasureHole firstHole = data.Holes[i];
                NxMeasureHole secondHole = data.Holes[j];
                double deltaX = secondHole.CenterX - firstHole.CenterX;
                double deltaZ = secondHole.CenterZ - firstHole.CenterZ;
                if (Math.Abs(deltaX) > 0.05 && Math.Abs(deltaZ) > 0.05)
                {
                    continue;
                }
                double distance = Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ);
                if (distance <= Epsilon)
                {
                    continue;
                }
                AddMeasurement(
                    result,
                    view.Id + "-V26-" + firstHole.Id + "-" + secondHole.Id + "-P",
                    "v26_aligned_hole_pitch",
                    firstHole.Id + "-" + secondHole.Id + " P=" +
                        Number(distance) + " " + unitLabel,
                    distance,
                    unitLabel,
                    ProjectPoint(
                        new V3(firstHole.CenterX, firstHole.CenterY, firstHole.CenterZ),
                        view),
                    ProjectPoint(
                        new V3(secondHole.CenterX, secondHole.CenterY, secondHole.CenterZ),
                        view),
                    firstHole.Id + ":" + secondHole.Id,
                    null,
                    null);
            }
        }
    }

    private static NxMeasureData RunNxMeasureV26(
        Part workPart,
        ListingWindow listing)
    {
        NxMeasureData result = new NxMeasureData();
        try
        {
            int code = NxMeasureV26.Main(new string[0]);
            result.ExitCode = code;
            result.ReportPath = FindNewestNxMeasureReport(workPart);
            if (result.ReportPath != null)
            {
                ParseNxMeasureReport(result.ReportPath, result);
            }
            else
            {
                result.Warning = "NxMeasureV26 completed but its CSV report was not found.";
            }
        }
        catch (Exception ex)
        {
            result.ExitCode = -1;
            result.Warning = ex.Message;
            listing.WriteLine("NxMeasureV26 integration warning: " + ex.Message);
        }
        return result;
    }

    private static string FindNewestNxMeasureReport(Part workPart)
    {
        string directory = string.IsNullOrEmpty(workPart.FullPath)
            ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
            : Path.GetDirectoryName(workPart.FullPath);
        if (string.IsNullOrEmpty(directory) || !Directory.Exists(directory))
        {
            return null;
        }

        string[] candidates = Directory.GetFiles(
            directory,
            SafeFileName(workPart.Leaf) + "*_自动测量报告.csv");
        if (candidates.Length == 0)
        {
            candidates = Directory.GetFiles(directory, "*_自动测量报告.csv");
        }

        string best = null;
        DateTime bestTime = DateTime.MinValue;
        foreach (string file in candidates)
        {
            DateTime time = File.GetLastWriteTimeUtc(file);
            if (time > bestTime)
            {
                best = file;
                bestTime = time;
            }
        }
        return best;
    }

    private static void ParseNxMeasureReport(
        string path,
        NxMeasureData result)
    {
        NxMeasureSection current = null;
        bool expectingHeader = false;
        foreach (string rawLine in File.ReadAllLines(path, Encoding.UTF8))
        {
            string line = rawLine.Trim();
            if (line.Length == 0)
            {
                continue;
            }
            if (line.StartsWith("[") && line.EndsWith("]"))
            {
                current = new NxMeasureSection();
                current.Name = line.Substring(1, line.Length - 2);
                result.Sections.Add(current);
                expectingHeader = true;
                continue;
            }
            if (current == null)
            {
                continue;
            }

            List<string> fields = ParseCsvLine(line);
            if (expectingHeader)
            {
                current.Header.AddRange(fields);
                expectingHeader = false;
                continue;
            }
            current.Rows.Add(fields);

            if (current.Name == "圆孔" && fields.Count >= 9)
            {
                NxMeasureHole hole = new NxMeasureHole();
                hole.Id = fields[0];
                hole.Diameter = ParseDouble(fields[1]);
                hole.CenterX = ParseDouble(fields[2]);
                hole.CenterY = ParseDouble(fields[3]);
                hole.CenterZ = ParseDouble(fields[4]);
                hole.MinimumY = ParseDouble(fields[5]);
                hole.MaximumY = ParseDouble(fields[6]);
                hole.Length = ParseDouble(fields[7]);
                result.Holes.Add(hole);
            }
            else if (current.Name == "腰形孔" && fields.Count >= 10)
            {
                NxMeasureSlot slot = new NxMeasureSlot();
                slot.Id = fields[0];
                slot.Width = ParseDouble(fields[1]);
                slot.OverallLength = ParseDouble(fields[2]);
                slot.CenterDistance = ParseDouble(fields[3]);
                slot.CenterX = ParseDouble(fields[4]);
                slot.CenterY = ParseDouble(fields[5]);
                slot.CenterZ = ParseDouble(fields[6]);
                slot.Direction = fields[7];
                result.Slots.Add(slot);
            }
        }
    }

    private static List<string> ParseCsvLine(string line)
    {
        List<string> result = new List<string>();
        StringBuilder field = new StringBuilder();
        bool quoted = false;
        for (int i = 0; i < line.Length; i++)
        {
            char character = line[i];
            if (character == '"')
            {
                if (quoted && i + 1 < line.Length && line[i + 1] == '"')
                {
                    field.Append('"');
                    i++;
                }
                else
                {
                    quoted = !quoted;
                }
            }
            else if (character == ',' && !quoted)
            {
                result.Add(field.ToString());
                field.Length = 0;
            }
            else
            {
                field.Append(character);
            }
        }
        result.Add(field.ToString());
        return result;
    }

    private static double ParseDouble(string value)
    {
        double result;
        return double.TryParse(
            value.Replace("%", string.Empty),
            NumberStyles.Float,
            CultureInfo.InvariantCulture,
            out result)
                ? result
                : 0.0;
    }

    private static void MatchNxMeasureHolesToFaces(
        NxMeasureData data,
        List<CylinderSurfaceSample> cylinders)
    {
        foreach (NxMeasureHole hole in data.Holes)
        {
            CylinderSurfaceSample best = null;
            double bestScore = double.MaxValue;
            foreach (CylinderSurfaceSample cylinder in cylinders)
            {
                if (!cylinder.IsInternal ||
                    Math.Abs(V3.Dot(cylinder.Axis, new V3(0, 1, 0))) < 0.985)
                {
                    continue;
                }
                V3 center = cylinder.AxisPoint + cylinder.Axis *
                    ((cylinder.MinimumStation + cylinder.MaximumStation) * 0.5);
                double diameterDifference = Math.Abs(
                    cylinder.Radius * 2.0 - hole.Diameter);
                double positionDifference = Math.Sqrt(
                    (center.X - hole.CenterX) * (center.X - hole.CenterX) +
                    (center.Z - hole.CenterZ) * (center.Z - hole.CenterZ));
                double score = diameterDifference * 10.0 + positionDifference;
                if (diameterDifference <= 0.02 &&
                    positionDifference <= 0.05 &&
                    score < bestScore)
                {
                    best = cylinder;
                    bestScore = score;
                }
            }
            if (best != null)
            {
                hole.FaceId = best.FaceId;
                hole.FaceTag = best.FaceTag;
                hole.BodyTag = best.BodyTag;
            }
        }
    }

    private static P2 ProjectPoint(V3 point, ViewDefinition view)
    {
        return new P2(V3.Dot(point, view.U), V3.Dot(point, view.V));
    }

    private static void AddMeasurement(
        ProjectionResult result,
        string id,
        string type,
        string label,
        double value,
        string unit,
        P2 first,
        P2 second,
        string featureId,
        string faceTag,
        string bodyTag)
    {
        ProjectedMeasurement measurement = new ProjectedMeasurement();
        measurement.Id = id;
        measurement.Type = type;
        measurement.Label = label;
        measurement.Value = value;
        measurement.Unit = unit;
        measurement.First = first;
        measurement.Second = second;
        measurement.FeatureId = featureId;
        measurement.FaceTag = faceTag;
        measurement.BodyTag = bodyTag;
        result.Measurements.Add(measurement);
    }

    private static void AddCylinderSilhouette(
        ProjectionResult result,
        CylinderSurfaceSample cylinder,
        ViewDefinition view,
        V3 first,
        V3 second,
        int side)
    {
        ProjectedSilhouette silhouette = new ProjectedSilhouette();
        silhouette.Source = cylinder;
        silhouette.Side = side;
        P2 first2d = new P2(
            V3.Dot(first, view.U),
            V3.Dot(first, view.V));
        P2 second2d = new P2(
            V3.Dot(second, view.U),
            V3.Dot(second, view.V));
        silhouette.Points.Add(first2d);
        silhouette.Points.Add(second2d);
        result.Bounds.Include(first2d);
        result.Bounds.Include(second2d);
        result.Silhouettes.Add(silhouette);
    }

    private static string CreateOutputDirectory(Part workPart)
    {
        string parent = null;
        if (!string.IsNullOrEmpty(workPart.FullPath))
        {
            parent = Path.GetDirectoryName(workPart.FullPath);
        }
        if (string.IsNullOrEmpty(parent) || !Directory.Exists(parent))
        {
            parent = Path.GetTempPath();
        }

        string modelName = SafeFileName(
            string.IsNullOrEmpty(workPart.Name) ? "nx_model" : workPart.Name);
        string directory = Path.Combine(
            parent,
            modelName + "_3d2d_projection_" +
            DateTime.Now.ToString("yyyyMMdd_HHmmss", CultureInfo.InvariantCulture));
        Directory.CreateDirectory(directory);
        return directory;
    }

    private static string SafeFileName(string value)
    {
        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(invalid, '_');
        }
        return value;
    }

    private static void WriteManifest(
        string directory,
        Part part,
        List<Body> bodies,
        List<EdgeSample> edges,
        List<CylinderSurfaceSample> cylinders,
        MeasureResultV27 measureResult,
        List<ViewDefinition> views,
        Bounds3 bounds)
    {
        string path = Path.Combine(directory, "manifest.json");
        using (StreamWriter writer = Utf8Writer(path))
        {
            writer.WriteLine("{");
            writer.WriteLine("  \"schema\": \"nx-3d2d-projection/1.3\",");
            writer.WriteLine("  \"generator\": \"Nx3d2dProjectionV8\",");
            writer.WriteLine("  \"generated_at\": \"" +
                Json(DateTime.Now.ToString("o", CultureInfo.InvariantCulture)) + "\",");
            writer.WriteLine("  \"model\": {");
            writer.WriteLine("    \"name\": \"" + Json(part.Name) + "\",");
            writer.WriteLine("    \"path\": \"" + Json(part.FullPath) + "\",");
            writer.WriteLine("    \"units\": \"" + Json(part.PartUnits.ToString()) + "\",");
            writer.WriteLine("    \"body_count\": " + bodies.Count + ",");
            writer.WriteLine("    \"edge_count\": " + edges.Count + ",");
            writer.WriteLine("    \"full_cylindrical_face_count\": " + cylinders.Count + ",");
            writer.WriteLine("    \"nxmeasure_v27_axial_feature_count\": " +
                measureResult.AxialFeatures.Count + ",");
            writer.WriteLine("    \"nxmeasure_v27_slot_count\": " +
                measureResult.Slots.Count + ",");
            writer.WriteLine("    \"nxmeasure_v27_radius_group_count\": " +
                measureResult.RadiusCandidates.Count + ",");
            writer.WriteLine("    \"nxmeasure_v27_thickness_candidate_count\": " +
                measureResult.ThicknessCandidates.Count + ",");
            writer.WriteLine("    \"bounds\": " + Bounds3Json(bounds));
            writer.WriteLine("  },");
            writer.WriteLine("  \"nxmeasure_v27_json\": \"nxmeasure_v27.json\",");
            writer.WriteLine("  \"views\": [");
            for (int i = 0; i < views.Count; i++)
            {
                ViewDefinition view = views[i];
                string comma = i + 1 < views.Count ? "," : string.Empty;
                writer.WriteLine("    {");
                writer.WriteLine("      \"id\": \"" + Json(view.Id) + "\",");
                writer.WriteLine("      \"name\": \"" + Json(view.Name) + "\",");
                writer.WriteLine("      \"source\": \"" + Json(view.Source) + "\",");
                writer.WriteLine("      \"direction_n\": " + V3Json(view.N) + ",");
                writer.WriteLine("      \"axis_u\": " + V3Json(view.U) + ",");
                writer.WriteLine("      \"axis_v\": " + V3Json(view.V) + ",");
                writer.WriteLine("      \"json\": \"view_" +
                    (i + 1).ToString("000", CultureInfo.InvariantCulture) + ".json\",");
                writer.WriteLine("      \"svg\": \"view_" +
                    (i + 1).ToString("000", CultureInfo.InvariantCulture) + ".svg\",");
                writer.WriteLine("      \"plain_svg\": \"view_" +
                    (i + 1).ToString("000", CultureInfo.InvariantCulture) + "_plain.svg\",");
                writer.WriteLine("      \"center_section_candidates\": [");
                writer.WriteLine("        {\"plane_normal\": " + V3Json(view.U) +
                    ", \"plane_origin\": " + V3Json(bounds.Center()) + "},");
                writer.WriteLine("        {\"plane_normal\": " + V3Json(view.V) +
                    ", \"plane_origin\": " + V3Json(bounds.Center()) + "}");
                writer.WriteLine("      ]");
                writer.WriteLine("    }" + comma);
            }
            writer.WriteLine("  ]");
            writer.WriteLine("}");
        }
    }

    private static void WriteViewJson(
        string path,
        Part part,
        ViewDefinition view,
        ProjectionResult projection)
    {
        using (StreamWriter writer = Utf8Writer(path))
        {
            writer.WriteLine("{");
            writer.WriteLine("  \"schema\": \"nx-3d2d-view/1.1\",");
            writer.WriteLine("  \"view_id\": \"" + Json(view.Id) + "\",");
            writer.WriteLine("  \"model\": \"" + Json(part.Name) + "\",");
            writer.WriteLine("  \"units\": \"" + Json(part.PartUnits.ToString()) + "\",");
            writer.WriteLine("  \"name\": \"" + Json(view.Name) + "\",");
            writer.WriteLine("  \"source\": \"" + Json(view.Source) + "\",");
            writer.WriteLine("  \"direction_n\": " + V3Json(view.N) + ",");
            writer.WriteLine("  \"axis_u\": " + V3Json(view.U) + ",");
            writer.WriteLine("  \"axis_v\": " + V3Json(view.V) + ",");
            writer.WriteLine("  \"bounds_2d\": " + Bounds2Json(projection.Bounds) + ",");
            writer.WriteLine("  \"projection_mode\": \"brep_edges_plus_analytic_cylinder_silhouettes_no_hidden_line_removal\",");
            writer.WriteLine("  \"edges\": [");
            for (int i = 0; i < projection.Edges.Count; i++)
            {
                ProjectedEdge edge = projection.Edges[i];
                string comma = i + 1 < projection.Edges.Count ? "," : string.Empty;
                writer.WriteLine("    {");
                writer.WriteLine("      \"edge_id\": \"" + Json(edge.Source.EdgeId) + "\",");
                writer.WriteLine("      \"edge_tag\": \"" + Json(edge.Source.EdgeTag) + "\",");
                writer.WriteLine("      \"body_id\": \"" + Json(edge.Source.BodyId) + "\",");
                writer.WriteLine("      \"body_tag\": \"" + Json(edge.Source.BodyTag) + "\",");
                writer.WriteLine("      \"edge_type\": \"" + Json(edge.Source.EdgeType) + "\",");
                writer.Write("      \"points\": [");
                for (int p = 0; p < edge.Points.Count; p++)
                {
                    if (p > 0)
                    {
                        writer.Write(",");
                    }
                    writer.Write(P2Json(edge.Points[p]));
                }
                writer.WriteLine("]");
                writer.WriteLine("    }" + comma);
            }
            writer.WriteLine("  ],");
            writer.WriteLine("  \"silhouettes\": [");
            for (int i = 0; i < projection.Silhouettes.Count; i++)
            {
                ProjectedSilhouette silhouette = projection.Silhouettes[i];
                string comma = i + 1 < projection.Silhouettes.Count ? "," : string.Empty;
                writer.WriteLine("    {");
                writer.WriteLine("      \"curve_id\": \"" +
                    Json(silhouette.Source.FaceId + "-S" + silhouette.Side) + "\",");
                writer.WriteLine("      \"kind\": \"silhouette_curve\",");
                writer.WriteLine("      \"surface_type\": \"cylinder\",");
                writer.WriteLine("      \"face_id\": \"" + Json(silhouette.Source.FaceId) + "\",");
                writer.WriteLine("      \"face_tag\": \"" + Json(silhouette.Source.FaceTag) + "\",");
                writer.WriteLine("      \"body_id\": \"" + Json(silhouette.Source.BodyId) + "\",");
                writer.WriteLine("      \"body_tag\": \"" + Json(silhouette.Source.BodyTag) + "\",");
                writer.WriteLine("      \"radius\": " + Number(silhouette.Source.Radius) + ",");
                writer.WriteLine("      \"is_internal\": " +
                    (silhouette.Source.IsInternal ? "true" : "false") + ",");
                writer.Write("      \"points\": [");
                for (int p = 0; p < silhouette.Points.Count; p++)
                {
                    if (p > 0)
                    {
                        writer.Write(",");
                    }
                    writer.Write(P2Json(silhouette.Points[p]));
                }
                writer.WriteLine("]");
                writer.WriteLine("    }" + comma);
            }
            writer.WriteLine("  ],");
            writer.WriteLine("  \"measurements\": [");
            WriteMeasurementArray(writer, projection.Measurements, "    ");
            writer.WriteLine("  ]");
            writer.WriteLine("}");
        }
    }

    private static void WriteMeasurementsJson(
        string path,
        Part part,
        List<ViewDefinition> views,
        List<ProjectionResult> projections)
    {
        using (StreamWriter writer = Utf8Writer(path))
        {
            writer.WriteLine("{");
            writer.WriteLine("  \"schema\": \"nx-3d2d-measurements/1.0\",");
            writer.WriteLine("  \"model\": \"" + Json(part.Name) + "\",");
            writer.WriteLine("  \"units\": \"" + Json(UnitLabel(part)) + "\",");
            writer.WriteLine("  \"views\": [");
            for (int i = 0; i < views.Count; i++)
            {
                string comma = i + 1 < views.Count ? "," : string.Empty;
                writer.WriteLine("    {");
                writer.WriteLine("      \"view_id\": \"" + Json(views[i].Id) + "\",");
                writer.WriteLine("      \"name\": \"" + Json(views[i].Name) + "\",");
                writer.WriteLine("      \"measurements\": [");
                WriteMeasurementArray(writer, projections[i].Measurements, "        ");
                writer.WriteLine("      ]");
                writer.WriteLine("    }" + comma);
            }
            writer.WriteLine("  ]");
            writer.WriteLine("}");
        }
    }

    private static void WriteNxMeasureJson(
        string path,
        Part part,
        NxMeasureData data)
    {
        using (StreamWriter writer = Utf8Writer(path))
        {
            writer.WriteLine("{");
            writer.WriteLine("  \"schema\": \"nxmeasure-v26-import/1.0\",");
            writer.WriteLine("  \"model\": \"" + Json(part.Name) + "\",");
            writer.WriteLine("  \"coordinate_assumption\": \"V26 hole and slot axes use global Y\",");
            writer.WriteLine("  \"exit_code\": " + data.ExitCode + ",");
            writer.WriteLine("  \"warning\": " + JsonOrNull(data.Warning) + ",");
            writer.WriteLine("  \"source_report\": " + JsonOrNull(data.ReportPath) + ",");
            writer.WriteLine("  \"sections\": [");
            for (int i = 0; i < data.Sections.Count; i++)
            {
                NxMeasureSection section = data.Sections[i];
                string comma = i + 1 < data.Sections.Count ? "," : string.Empty;
                writer.WriteLine("    {");
                writer.WriteLine("      \"name\": \"" + Json(section.Name) + "\",");
                writer.Write("      \"header\": ");
                WriteStringArray(writer, section.Header);
                writer.WriteLine(",");
                writer.WriteLine("      \"rows\": [");
                for (int rowIndex = 0; rowIndex < section.Rows.Count; rowIndex++)
                {
                    writer.Write("        ");
                    WriteStringArray(writer, section.Rows[rowIndex]);
                    writer.WriteLine(rowIndex + 1 < section.Rows.Count ? "," : string.Empty);
                }
                writer.WriteLine("      ]");
                writer.WriteLine("    }" + comma);
            }
            writer.WriteLine("  ]");
            writer.WriteLine("}");
        }
    }

    private static void WriteStringArray(
        StreamWriter writer,
        List<string> values)
    {
        writer.Write("[");
        for (int i = 0; i < values.Count; i++)
        {
            if (i > 0)
            {
                writer.Write(",");
            }
            writer.Write("\"");
            writer.Write(Json(values[i]));
            writer.Write("\"");
        }
        writer.Write("]");
    }

    private static void CopyNxMeasureReport(
        string outputDirectory,
        NxMeasureData data)
    {
        if (data.ReportPath == null || !File.Exists(data.ReportPath))
        {
            return;
        }
        File.Copy(
            data.ReportPath,
            Path.Combine(outputDirectory, "nxmeasure_v26_report.csv"),
            true);
    }

    private static void WriteMeasurementArray(
        StreamWriter writer,
        List<ProjectedMeasurement> measurements,
        string indent)
    {
        for (int i = 0; i < measurements.Count; i++)
        {
            ProjectedMeasurement measurement = measurements[i];
            string comma = i + 1 < measurements.Count ? "," : string.Empty;
            writer.WriteLine(indent + "{");
            writer.WriteLine(indent + "  \"measurement_id\": \"" + Json(measurement.Id) + "\",");
            writer.WriteLine(indent + "  \"type\": \"" + Json(measurement.Type) + "\",");
            writer.WriteLine(indent + "  \"label\": \"" + Json(measurement.Label) + "\",");
            writer.WriteLine(indent + "  \"value\": " + Number(measurement.Value) + ",");
            writer.WriteLine(indent + "  \"unit\": \"" + Json(measurement.Unit) + "\",");
            writer.WriteLine(indent + "  \"points\": [" +
                P2Json(measurement.First) + "," + P2Json(measurement.Second) + "],");
            writer.WriteLine(indent + "  \"feature_id\": " + JsonOrNull(measurement.FeatureId) + ",");
            writer.WriteLine(indent + "  \"face_tag\": " + JsonOrNull(measurement.FaceTag) + ",");
            writer.WriteLine(indent + "  \"body_tag\": " + JsonOrNull(measurement.BodyTag));
            writer.WriteLine(indent + "}" + comma);
        }
    }

    private static void WriteViewSvg(
        string path,
        ViewDefinition view,
        ProjectionResult projection,
        bool includeMeasurements)
    {
        double width = Math.Max(projection.Bounds.Width(), Epsilon);
        double height = Math.Max(projection.Bounds.Height(), Epsilon);
        double scale = Math.Min(
            (SvgWidth - 2.0 * SvgMargin) / width,
            (SvgHeight - 2.0 * SvgMargin) / height);

        using (StreamWriter writer = Utf8Writer(path))
        {
            writer.WriteLine("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
            writer.WriteLine("<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"" +
                SvgWidth + "\" height=\"" + SvgHeight + "\" viewBox=\"0 0 " +
                SvgWidth + " " + SvgHeight + "\">");
            writer.WriteLine("  <rect width=\"100%\" height=\"100%\" fill=\"white\"/>");
            writer.WriteLine("  <g fill=\"none\" stroke=\"black\" stroke-width=\"1.15\" " +
                "stroke-linecap=\"round\" stroke-linejoin=\"round\">");

            foreach (ProjectedEdge edge in projection.Edges)
            {
                writer.Write("    <polyline data-edge-id=\"");
                writer.Write(Xml(edge.Source.EdgeId));
                writer.Write("\" data-edge-tag=\"");
                writer.Write(Xml(edge.Source.EdgeTag));
                writer.Write("\" data-body-id=\"");
                writer.Write(Xml(edge.Source.BodyId));
                writer.Write("\" points=\"");

                foreach (P2 point in edge.Points)
                {
                    double x = SvgMargin +
                        (point.X - projection.Bounds.MinX) * scale;
                    double y = SvgHeight - SvgMargin -
                        (point.Y - projection.Bounds.MinY) * scale;
                    writer.Write(Number(x));
                    writer.Write(",");
                    writer.Write(Number(y));
                    writer.Write(" ");
                }
                writer.WriteLine("\"/>");
            }

            writer.WriteLine("  </g>");
            writer.WriteLine("  <g fill=\"none\" stroke=\"black\" stroke-width=\"1.6\" " +
                "stroke-linecap=\"round\">");
            foreach (ProjectedSilhouette silhouette in projection.Silhouettes)
            {
                writer.Write("    <polyline data-kind=\"silhouette_curve\" data-face-id=\"");
                writer.Write(Xml(silhouette.Source.FaceId));
                writer.Write("\" data-face-tag=\"");
                writer.Write(Xml(silhouette.Source.FaceTag));
                writer.Write("\" data-body-id=\"");
                writer.Write(Xml(silhouette.Source.BodyId));
                writer.Write("\" points=\"");
                foreach (P2 point in silhouette.Points)
                {
                    double x = SvgMargin +
                        (point.X - projection.Bounds.MinX) * scale;
                    double y = SvgHeight - SvgMargin -
                        (point.Y - projection.Bounds.MinY) * scale;
                    writer.Write(Number(x));
                    writer.Write(",");
                    writer.Write(Number(y));
                    writer.Write(" ");
                }
                writer.WriteLine("\"/>");
            }
            writer.WriteLine("  </g>");
            if (includeMeasurements)
            {
                WriteSvgMeasurements(writer, projection, scale);
            }
            writer.WriteLine("  <text x=\"15\" y=\"24\" font-family=\"Arial\" font-size=\"16\" fill=\"#555\">" +
                Xml(view.Name + " | source=" + view.Source +
                    (includeMeasurements ? " | measured" : string.Empty)) + "</text>");
            writer.WriteLine("</svg>");
        }
    }

    private static void WriteSvgMeasurements(
        StreamWriter writer,
        ProjectionResult projection,
        double scale)
    {
        writer.WriteLine("  <g class=\"measurement-layer\" fill=\"none\" stroke=\"#000000\" " +
            "stroke-width=\"1.3\" font-family=\"Arial\">");
        List<LabelBox> occupiedLabels = new List<LabelBox>();
        foreach (ProjectedMeasurement measurement in projection.Measurements)
        {
            double x1 = SvgMargin +
                (measurement.First.X - projection.Bounds.MinX) * scale;
            double y1 = SvgHeight - SvgMargin -
                (measurement.First.Y - projection.Bounds.MinY) * scale;
            double x2 = SvgMargin +
                (measurement.Second.X - projection.Bounds.MinX) * scale;
            double y2 = SvgHeight - SvgMargin -
                (measurement.Second.Y - projection.Bounds.MinY) * scale;
            double dimensionMidX = (x1 + x2) * 0.5;
            double dimensionMidY = (y1 + y2) * 0.5;
            double dx = x2 - x1;
            double dy = y2 - y1;
            double length = Math.Sqrt(dx * dx + dy * dy);
            double tx = length > Epsilon ? -dy / length * 5.0 : 0.0;
            double ty = length > Epsilon ? dx / length * 5.0 : 5.0;
            List<string> labelLines = SplitMeasurementLabel(measurement.Label);
            LabelBox labelBox = PlaceMeasurementLabel(
                dimensionMidX,
                dimensionMidY,
                labelLines,
                occupiedLabels);
            occupiedLabels.Add(labelBox);

            writer.WriteLine("    <g data-measurement-id=\"" + Xml(measurement.Id) +
                "\" data-type=\"" + Xml(measurement.Type) + "\">");
            writer.WriteLine("      <line x1=\"" + Number(x1) + "\" y1=\"" + Number(y1) +
                "\" x2=\"" + Number(x2) + "\" y2=\"" + Number(y2) + "\"/>");
            writer.WriteLine("      <line x1=\"" + Number(x1 - tx) + "\" y1=\"" +
                Number(y1 - ty) + "\" x2=\"" + Number(x1 + tx) + "\" y2=\"" +
                Number(y1 + ty) + "\"/>");
            writer.WriteLine("      <line x1=\"" + Number(x2 - tx) + "\" y1=\"" +
                Number(y2 - ty) + "\" x2=\"" + Number(x2 + tx) + "\" y2=\"" +
                Number(y2 + ty) + "\"/>");
            double labelCenterX = labelBox.X + labelBox.Width * 0.5;
            double labelCenterY = labelBox.Y + labelBox.Height * 0.5;
            if (Math.Abs(labelCenterX - dimensionMidX) > 8.0 ||
                Math.Abs(labelCenterY - dimensionMidY) > 8.0)
            {
                writer.WriteLine("      <line class=\"measurement-leader\" x1=\"" +
                    Number(dimensionMidX) + "\" y1=\"" + Number(dimensionMidY) +
                    "\" x2=\"" + Number(labelCenterX) + "\" y2=\"" +
                    Number(labelCenterY) + "\" stroke-width=\"0.9\"/>");
            }
            writer.WriteLine("      <rect x=\"" + Number(labelBox.X) + "\" y=\"" +
                Number(labelBox.Y) + "\" width=\"" + Number(labelBox.Width) +
                "\" height=\"" + Number(labelBox.Height) +
                "\" rx=\"2\" fill=\"white\" fill-opacity=\"0.92\" stroke=\"none\"/>");
            writer.WriteLine("      <text x=\"" + Number(labelCenterX) + "\" y=\"" +
                Number(labelBox.Y + 14.0) +
                "\" text-anchor=\"middle\" font-size=\"13\" fill=\"#000000\" stroke=\"none\">");
            for (int lineIndex = 0; lineIndex < labelLines.Count; lineIndex++)
            {
                writer.WriteLine("        <tspan x=\"" + Number(labelCenterX) +
                    "\" dy=\"" + (lineIndex == 0 ? "0" : "15") + "\">" +
                    Xml(labelLines[lineIndex]) + "</tspan>");
            }
            writer.WriteLine("      </text>");
            writer.WriteLine("    </g>");
        }
        writer.WriteLine("  </g>");
    }

    private static List<string> SplitMeasurementLabel(string label)
    {
        List<string> result = new List<string>();
        if (string.IsNullOrEmpty(label))
        {
            result.Add(string.Empty);
            return result;
        }

        int pitchIndex = label.IndexOf(" P=", StringComparison.Ordinal);
        if (pitchIndex > 0)
        {
            result.Add(label.Substring(0, pitchIndex));
            result.Add(label.Substring(pitchIndex + 1));
            return result;
        }

        int slashIndex = label.IndexOf(" / ", StringComparison.Ordinal);
        if (slashIndex > 0)
        {
            result.Add(label.Substring(0, slashIndex));
            result.Add(label.Substring(slashIndex + 3));
            return result;
        }

        if ((label.StartsWith("H", StringComparison.Ordinal) ||
             label.StartsWith("E", StringComparison.Ordinal) ||
             label.StartsWith("C", StringComparison.Ordinal) ||
             label.StartsWith("S", StringComparison.Ordinal)) &&
            label.IndexOf(' ') > 0)
        {
            int split = label.IndexOf(' ');
            result.Add(label.Substring(0, split));
            result.Add(label.Substring(split + 1));
            return result;
        }

        result.Add(label);
        return result;
    }

    private static LabelBox PlaceMeasurementLabel(
        double centerX,
        double centerY,
        List<string> lines,
        List<LabelBox> occupied)
    {
        int longest = 1;
        foreach (string line in lines)
        {
            longest = Math.Max(longest, line == null ? 0 : line.Length);
        }
        double width = Math.Max(48.0, longest * 7.4 + 12.0);
        double height = lines.Count * 15.0 + 7.0;
        double[,] offsets = new double[,]
        {
            { 0, -28 }, { 0, 28 }, { 48, -20 }, { -48, -20 },
            { 48, 22 }, { -48, 22 }, { 0, -52 }, { 0, 52 },
            { 92, -20 }, { -92, -20 }, { 92, 22 }, { -92, 22 },
            { 48, -55 }, { -48, -55 }, { 48, 55 }, { -48, 55 }
        };

        for (int i = 0; i < offsets.GetLength(0); i++)
        {
            LabelBox candidate = ClampLabelBox(
                centerX + offsets[i, 0] - width * 0.5,
                centerY + offsets[i, 1] - height * 0.5,
                width,
                height);
            if (!OverlapsAny(candidate, occupied))
            {
                return candidate;
            }
        }

        // Dense drawings fall back to deterministic right-side lanes.
        int maximumRows = Math.Max(
            1,
            (int)((SvgHeight - 2.0 * SvgMargin) / (height + 5.0)));
        for (int lane = 0; lane < maximumRows * 2; lane++)
        {
            int column = lane / maximumRows;
            int row = lane % maximumRows;
            double laneX = column == 0
                ? SvgWidth - SvgMargin - width
                : SvgMargin;
            double laneY = SvgMargin + row * (height + 5.0);
            LabelBox candidate = ClampLabelBox(laneX, laneY, width, height);
            if (!OverlapsAny(candidate, occupied))
            {
                return candidate;
            }
        }

        return ClampLabelBox(
            centerX - width * 0.5,
            centerY - height * 0.5,
            width,
            height);
    }

    private static LabelBox ClampLabelBox(
        double x,
        double y,
        double width,
        double height)
    {
        return new LabelBox
        {
            X = Math.Max(4.0, Math.Min(SvgWidth - width - 4.0, x)),
            Y = Math.Max(28.0, Math.Min(SvgHeight - height - 4.0, y)),
            Width = width,
            Height = height
        };
    }

    private static bool OverlapsAny(
        LabelBox candidate,
        List<LabelBox> occupied)
    {
        foreach (LabelBox existing in occupied)
        {
            if (candidate.X < existing.X + existing.Width + 5.0 &&
                candidate.X + candidate.Width + 5.0 > existing.X &&
                candidate.Y < existing.Y + existing.Height + 5.0 &&
                candidate.Y + candidate.Height + 5.0 > existing.Y)
            {
                return true;
            }
        }
        return false;
    }

    private static void WriteIndexHtml(
        string directory,
        Part part,
        List<ViewDefinition> views)
    {
        using (StreamWriter writer = Utf8Writer(Path.Combine(directory, "index.html")))
        {
            writer.WriteLine("<!doctype html><html><head><meta charset=\"utf-8\">");
            writer.WriteLine("<title>NX 3D-2D Projections V8</title>");
            writer.WriteLine("<style>body{font-family:Arial;margin:24px;background:#f4f6f8;color:#222}" +
                ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px}" +
                ".card{background:white;border:1px solid #d8dde3;border-radius:8px;padding:12px}" +
                "img{width:100%;border:1px solid #eee}code{color:#555}</style></head><body>");
            writer.WriteLine("<h1>NX 3D-2D Projections V8</h1>");
            writer.WriteLine("<p>Model: <code>" + Xml(part.Name) + "</code> &nbsp; " +
                "<a href=\"measurements.json\">measurements JSON</a> &nbsp; " +
                "<a href=\"nxmeasure_v27.json\">NxMeasure V27 JSON</a></p>");
            writer.WriteLine("<p><label><input id=\"measurement-toggle\" type=\"checkbox\" checked " +
                "onchange=\"toggleMeasurements(this.checked)\"> Show measurement layer</label></p>");
            writer.WriteLine("<div class=\"grid\">");
            for (int i = 0; i < views.Count; i++)
            {
                string id = (i + 1).ToString("000", CultureInfo.InvariantCulture);
                writer.WriteLine("<div class=\"card\"><h3>" + Xml(views[i].Name) +
                    "</h3><img class=\"projection-image\" src=\"view_" + id +
                    ".svg\" data-plain=\"view_" + id + "_plain.svg\" data-measured=\"view_" +
                    id + ".svg\"><p><a href=\"view_" + id +
                    ".svg\">annotated SVG</a> &nbsp; <a href=\"view_" + id +
                    "_plain.svg\">plain SVG</a> &nbsp; <a href=\"view_" +
                    id + ".json\">projection JSON</a></p></div>");
            }
            writer.WriteLine("</div><script>function toggleMeasurements(on){" +
                "document.querySelectorAll('.projection-image').forEach(function(img){" +
                "img.src=on?img.dataset.measured:img.dataset.plain;});}</script></body></html>");
        }
    }

    private static string UnitLabel(Part part)
    {
        return part.PartUnits == BasePart.Units.Millimeters ? "mm" : "in";
    }

    private static StreamWriter Utf8Writer(string path)
    {
        return new StreamWriter(path, false, new UTF8Encoding(false));
    }

    private static string Number(double value)
    {
        return value.ToString("0.########", CultureInfo.InvariantCulture);
    }

    private static string DisplayNumber(double value, int decimals)
    {
        int safeDecimals = Math.Max(0, Math.Min(6, decimals));
        string format = safeDecimals == 0
            ? "0"
            : "0." + new string('#', safeDecimals);
        return value.ToString(format, CultureInfo.InvariantCulture);
    }

    private static string V3Json(V3 value)
    {
        return "[" + Number(value.X) + "," + Number(value.Y) + "," + Number(value.Z) + "]";
    }

    private static string P2Json(P2 value)
    {
        return "[" + Number(value.X) + "," + Number(value.Y) + "]";
    }

    private static string Bounds2Json(Bounds2 value)
    {
        return "[" + Number(value.MinX) + "," + Number(value.MinY) + "," +
            Number(value.MaxX) + "," + Number(value.MaxY) + "]";
    }

    private static string Bounds3Json(Bounds3 value)
    {
        return "[" + Number(value.MinX) + "," + Number(value.MinY) + "," +
            Number(value.MinZ) + "," + Number(value.MaxX) + "," +
            Number(value.MaxY) + "," + Number(value.MaxZ) + "]";
    }

    private static string Json(string value)
    {
        if (value == null)
        {
            return string.Empty;
        }
        return value.Replace("\\", "\\\\")
            .Replace("\"", "\\\"")
            .Replace("\r", "\\r")
            .Replace("\n", "\\n");
    }

    private static string JsonOrNull(string value)
    {
        return value == null ? "null" : "\"" + Json(value) + "\"";
    }

    private static string Xml(string value)
    {
        if (value == null)
        {
            return string.Empty;
        }
        return value.Replace("&", "&amp;")
            .Replace("<", "&lt;")
            .Replace(">", "&gt;")
            .Replace("\"", "&quot;")
            .Replace("'", "&apos;");
    }

    public static int GetUnloadOption(string dummy)
    {
        return (int)Session.LibraryUnloadOption.Immediately;
    }

    private sealed class NxMeasureData
    {
        public int ExitCode;
        public string ReportPath;
        public string Warning;
        public readonly List<NxMeasureHole> Holes =
            new List<NxMeasureHole>();
        public readonly List<NxMeasureSlot> Slots =
            new List<NxMeasureSlot>();
        public readonly List<NxMeasureSection> Sections =
            new List<NxMeasureSection>();
    }

    private sealed class NxMeasureHole
    {
        public string Id;
        public double Diameter;
        public double CenterX;
        public double CenterY;
        public double CenterZ;
        public double MinimumY;
        public double MaximumY;
        public double Length;
        public string FaceId;
        public string FaceTag;
        public string BodyTag;
    }

    private sealed class NxMeasureSlot
    {
        public string Id;
        public double Width;
        public double OverallLength;
        public double CenterDistance;
        public double CenterX;
        public double CenterY;
        public double CenterZ;
        public string Direction;
    }

    private sealed class NxMeasureSection
    {
        public string Name;
        public readonly List<string> Header = new List<string>();
        public readonly List<List<string>> Rows =
            new List<List<string>>();
    }

    private sealed class ViewDefinition
    {
        public string Id;
        public string Name;
        public string Source;
        public V3 N;
        public V3 U;
        public V3 V;
    }

    private sealed class EdgeSample
    {
        public string EdgeId;
        public string EdgeTag;
        public string BodyId;
        public string BodyTag;
        public string EdgeType;
        public List<V3> Points;
    }

    private sealed class ProjectedEdge
    {
        public EdgeSample Source;
        public readonly List<P2> Points = new List<P2>();
    }

    private sealed class CylinderSurfaceSample
    {
        public string FaceId;
        public string FaceTag;
        public string BodyId;
        public string BodyTag;
        public V3 AxisPoint;
        public V3 Axis;
        public double Radius;
        public double MinimumStation;
        public double MaximumStation;
        public bool IsInternal;
    }

    private sealed class ProjectedSilhouette
    {
        public CylinderSurfaceSample Source;
        public int Side;
        public readonly List<P2> Points = new List<P2>();
    }

    private sealed class ProjectedMeasurement
    {
        public string Id;
        public string Type;
        public string Label;
        public double Value;
        public string Unit;
        public P2 First;
        public P2 Second;
        public string FeatureId;
        public string FaceTag;
        public string BodyTag;
    }

    private sealed class PatternPoint
    {
        public P2 Point;
        public AxialFeatureV27 Feature;
        public readonly List<string> FeatureIds = new List<string>();
    }

    private sealed class ArcRadiusGroup
    {
        public double Radius;
        public readonly List<P2> Centers = new List<P2>();
        public readonly List<ProjectedEdge> Edges = new List<ProjectedEdge>();
    }

    private sealed class InclinedEdgeGroup
    {
        public double Angle;
        public double Length;
        public int Count;
        public ProjectedEdge Edge;
    }

    private sealed class LabelBox
    {
        public double X;
        public double Y;
        public double Width;
        public double Height;
    }

    private sealed class ProjectionResult
    {
        public readonly List<ProjectedEdge> Edges = new List<ProjectedEdge>();
        public readonly List<ProjectedSilhouette> Silhouettes =
            new List<ProjectedSilhouette>();
        public readonly List<ProjectedMeasurement> Measurements =
            new List<ProjectedMeasurement>();
        public readonly Bounds2 Bounds = new Bounds2();
    }

    private struct P2
    {
        public readonly double X;
        public readonly double Y;

        public P2(double x, double y)
        {
            X = x;
            Y = y;
        }
    }

    private struct V3
    {
        public readonly double X;
        public readonly double Y;
        public readonly double Z;

        public V3(double x, double y, double z)
        {
            X = x;
            Y = y;
            Z = z;
        }

        public static V3 From(Point3d value)
        {
            return new V3(value.X, value.Y, value.Z);
        }

        public double Length()
        {
            return Math.Sqrt(X * X + Y * Y + Z * Z);
        }

        public V3 Normalize()
        {
            double length = Length();
            if (length <= Epsilon)
            {
                return new V3(0, 0, 0);
            }
            return new V3(X / length, Y / length, Z / length);
        }

        public V3 Negate()
        {
            return new V3(-X, -Y, -Z);
        }

        public static V3 operator -(V3 left, V3 right)
        {
            return new V3(left.X - right.X, left.Y - right.Y, left.Z - right.Z);
        }

        public static V3 operator +(V3 left, V3 right)
        {
            return new V3(left.X + right.X, left.Y + right.Y, left.Z + right.Z);
        }

        public static V3 operator *(V3 value, double scale)
        {
            return new V3(value.X * scale, value.Y * scale, value.Z * scale);
        }

        public static double Dot(V3 left, V3 right)
        {
            return left.X * right.X + left.Y * right.Y + left.Z * right.Z;
        }

        public static V3 Cross(V3 left, V3 right)
        {
            return new V3(
                left.Y * right.Z - left.Z * right.Y,
                left.Z * right.X - left.X * right.Z,
                left.X * right.Y - left.Y * right.X);
        }
    }

    private sealed class Bounds2
    {
        public double MinX = double.PositiveInfinity;
        public double MinY = double.PositiveInfinity;
        public double MaxX = double.NegativeInfinity;
        public double MaxY = double.NegativeInfinity;

        public void Include(P2 point)
        {
            MinX = Math.Min(MinX, point.X);
            MinY = Math.Min(MinY, point.Y);
            MaxX = Math.Max(MaxX, point.X);
            MaxY = Math.Max(MaxY, point.Y);
        }

        public double Width()
        {
            return MaxX - MinX;
        }

        public double Height()
        {
            return MaxY - MinY;
        }
    }

    private sealed class Bounds3
    {
        public double MinX = double.PositiveInfinity;
        public double MinY = double.PositiveInfinity;
        public double MinZ = double.PositiveInfinity;
        public double MaxX = double.NegativeInfinity;
        public double MaxY = double.NegativeInfinity;
        public double MaxZ = double.NegativeInfinity;

        public void Include(V3 point)
        {
            MinX = Math.Min(MinX, point.X);
            MinY = Math.Min(MinY, point.Y);
            MinZ = Math.Min(MinZ, point.Z);
            MaxX = Math.Max(MaxX, point.X);
            MaxY = Math.Max(MaxY, point.Y);
            MaxZ = Math.Max(MaxZ, point.Z);
        }

        public V3 Center()
        {
            return new V3(
                (MinX + MaxX) * 0.5,
                (MinY + MaxY) * 0.5,
                (MinZ + MaxZ) * 0.5);
        }
    }
}
