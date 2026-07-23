using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using NXOpen;
using NXOpen.Assemblies;
using NXOpen.UF;

public class NxMeasureV26
{
    private const int CylindricalFace = 16;
    private const int ToroidalFace = 19;
    private const int PlanarFace = 22;
    private const double AxisTolerance = 0.999;
    private const double PositionTolerance = 0.02;
    private const double DiameterTolerance = 0.01;
    private const double RangeTolerance = 0.05;
    private const double RoundHoleCoverage = 0.85;
    private const double SlotEndMinCoverage = 0.35;
    private const double SlotEndMaxCoverage = 0.65;

    public static int Main(string[] args)
    {
        Session session = Session.GetSession();
        UFSession ufSession = UFSession.GetUFSession();
        ListingWindow output = session.ListingWindow;
        output.Open();

        Part workPart = session.Parts.Work;
        if (workPart == null)
        {
            output.WriteLine("错误：当前没有打开工作零件。");
            return 1;
        }

        Unit areaUnit = workPart.UnitCollection.GetBase("Area");
        Unit lengthUnit = workPart.UnitCollection.GetBase("Length");
        List<CylinderLayer> internalLayers = new List<CylinderLayer>();
        List<CylinderLayer> externalLayers = new List<CylinderLayer>();
        List<PlaneGroup> planeGroups = new List<PlaneGroup>();
        List<RadiusGroup> toroidalRadiusGroups = new List<RadiusGroup>();
        List<BendRadiusGroup> bendRadiusGroups =
            new List<BendRadiusGroup>();
        List<Body> analysisBodies = GetAnalysisBodies(workPart);

        foreach (Body body in analysisBodies)
        {
            if (!body.IsSolidBody)
            {
                continue;
            }

            foreach (Face face in body.GetFaces())
            {
                int faceType;
                double[] point = new double[3];
                double[] direction = new double[3];
                double[] box = new double[6];
                double radius;
                double radiusData;
                int normalDirection;

                ufSession.Modl.AskFaceData(
                    face.Tag,
                    out faceType,
                    point,
                    direction,
                    box,
                    out radius,
                    out radiusData,
                    out normalDirection);

                if (faceType == PlanarFace)
                {
                    double planeArea;
                    using (MeasureFaces measure =
                        workPart.MeasureManager.NewFaceProperties(
                            areaUnit,
                            lengthUnit,
                            0.999,
                            new IParameterizedSurface[] { face }))
                    {
                        planeArea = measure.Area;
                    }

                    AddOrMergePlaneGroup(
                        planeGroups,
                        point,
                        direction,
                        planeArea);
                }

                if (faceType == ToroidalFace)
                {
                    AddOrMergeRadiusGroup(
                        toroidalRadiusGroups,
                        Math.Abs(radiusData),
                        Math.Abs(radius));
                }

                if (faceType == CylindricalFace &&
                    Math.Abs(direction[1]) < AxisTolerance)
                {
                    AddOrMergeBendRadiusGroup(
                        bendRadiusGroups,
                        Math.Abs(radius),
                        GetGeneralAxisCategory(direction),
                        normalDirection);
                }

                if (faceType != CylindricalFace ||
                    Math.Abs(direction[1]) < AxisTolerance)
                {
                    continue;
                }

                double faceArea;
                using (MeasureFaces measure = workPart.MeasureManager.NewFaceProperties(
                    areaUnit,
                    lengthUnit,
                    0.999,
                    new IParameterizedSurface[] { face }))
                {
                    faceArea = measure.Area;
                }

                Dictionary<Tag, Face> adjacentPlanarFaces =
                    GetAdjacentPlanarFaces(face, ufSession);

                List<CylinderLayer> targetLayers =
                    normalDirection < 0
                        ? internalLayers
                        : externalLayers;

                AddOrMergeLayer(
                    targetLayers,
                    body.Tag,
                    radius * 2.0,
                    point[0],
                    point[2],
                    box[1],
                    box[4],
                    faceArea,
                    face,
                    adjacentPlanarFaces);
            }
        }

        List<CylinderLayer> roundHoles = new List<CylinderLayer>();
        List<CylinderLayer> partialEnds = new List<CylinderLayer>();
        List<CylinderLayer> externalCylinders = new List<CylinderLayer>();

        foreach (CylinderLayer layer in internalLayers)
        {
            double coverage = layer.AreaCoverage();
            if (coverage >= RoundHoleCoverage)
            {
                roundHoles.Add(layer);
            }
            else if (coverage >= SlotEndMinCoverage &&
                     coverage <= SlotEndMaxCoverage)
            {
                partialEnds.Add(layer);
            }
        }

        foreach (CylinderLayer layer in externalLayers)
        {
            if (layer.AreaCoverage() >= 0.10)
            {
                externalCylinders.Add(layer);
            }
        }

        List<SlotFeature> slots = PairSlotEnds(partialEnds, ufSession);
        roundHoles.Sort(CompareLayers);
        slots.Sort(CompareSlots);
        externalCylinders.Sort(CompareLayers);
        List<BossFeature> bosses =
            BuildBossFeatures(externalCylinders, roundHoles);
        List<HoleAxisGroup> holeAxisGroups =
            BuildHoleAxisGroups(roundHoles);
        List<EntityAnalysis> entityAnalyses = BuildEntityAnalyses(
            workPart,
            ufSession,
            analysisBodies,
            roundHoles);

        PartSummary summary = AnalyzePartSummary(
            workPart,
            ufSession,
            analysisBodies);
        List<PlaneGroup> majorPlanes = GetMajorPlanes(planeGroups);
        string reportPath = ExportCsv(
            workPart,
            summary,
            roundHoles,
            slots,
            majorPlanes,
            toroidalRadiusGroups,
            externalCylinders,
            bosses,
            holeAxisGroups,
            bendRadiusGroups,
            entityAnalyses);

        output.WriteLine("========================================");
        output.WriteLine("NX 自动测量 V26：实体厚度可信度修正");
        output.WriteLine("零件名称：" + workPart.Leaf);
        output.WriteLine("圆孔数量：" + roundHoles.Count);
        output.WriteLine("腰形孔数量：" + slots.Count);
        output.WriteLine("主要平面组数量：" + majorPlanes.Count);
        output.WriteLine("----------------------------------------");
        output.WriteLine("圆孔：");

        for (int i = 0; i < roundHoles.Count; i++)
        {
            CylinderLayer hole = roundHoles[i];
            output.WriteLine(
                "H" + (i + 1).ToString("000") +
                " / Ø" + Format(hole.Diameter) + " mm" +
                " / 中心 X=" + Format(hole.AxisX) +
                ", Z=" + Format(hole.AxisZ) +
                " / Y=" + Format(hole.MinY) + "～" + Format(hole.MaxY) +
                " / 圆柱段长=" + Format(hole.Length()) + " mm");
        }

        output.WriteLine("----------------------------------------");
        output.WriteLine("腰形孔：");

        for (int i = 0; i < slots.Count; i++)
        {
            SlotFeature slot = slots[i];
            output.WriteLine(
                "S" + (i + 1).ToString("000") +
                " / 宽=" + Format(slot.Width) + " mm" +
                " / 总长=" + Format(slot.OverallLength) + " mm" +
                " / 两端中心距=" + Format(slot.CenterDistance) + " mm" +
                " / 中心 X=" + Format(slot.CenterX) +
                ", Z=" + Format(slot.CenterZ) +
                " / 方向=" + slot.Direction +
                " / 共享直壁=" + slot.WallFaces.Count);
        }

        output.WriteLine("----------------------------------------");
        output.WriteLine("CSV报告已导出：");
        output.WriteLine(reportPath);
        output.WriteLine("本版本不修改模型颜色。");
        output.WriteLine("========================================");
        return 0;
    }

    private static PartSummary AnalyzePartSummary(
        Part workPart,
        UFSession ufSession,
        List<Body> analysisBodies)
    {
        PartSummary summary = new PartSummary();
        List<Tag> solidTags = new List<Tag>();
        bool hasBox = false;

        foreach (Body body in analysisBodies)
        {
            if (!body.IsSolidBody)
            {
                continue;
            }

            solidTags.Add(body.Tag);
            summary.FaceCount += body.GetFaces().Length;

            double[] bodyBox = new double[6];
            ufSession.Modl.AskBoundingBox(body.Tag, bodyBox);
            if (!hasBox)
            {
                Array.Copy(bodyBox, summary.Box, 6);
                hasBox = true;
            }
            else
            {
                summary.Box[0] = Math.Min(summary.Box[0], bodyBox[0]);
                summary.Box[1] = Math.Min(summary.Box[1], bodyBox[1]);
                summary.Box[2] = Math.Min(summary.Box[2], bodyBox[2]);
                summary.Box[3] = Math.Max(summary.Box[3], bodyBox[3]);
                summary.Box[4] = Math.Max(summary.Box[4], bodyBox[4]);
                summary.Box[5] = Math.Max(summary.Box[5], bodyBox[5]);
            }
        }

        summary.BodyCount = solidTags.Count;
        if (solidTags.Count == 0)
        {
            return summary;
        }

        double[] accuracyValues = new double[11];
        accuracyValues[0] = 0.999;
        double[] massProperties = new double[47];
        double[] statistics = new double[13];

        ufSession.Modl.AskMassProps3d(
            solidTags.ToArray(),
            solidTags.Count,
            1,
            4,
            1.0,
            1,
            accuracyValues,
            massProperties,
            statistics);

        bool millimeterPart = workPart.PartUnits != BasePart.Units.Inches;
        double lengthFactor = millimeterPart ? 1000.0 : 39.3700787401575;
        double areaFactor = lengthFactor * lengthFactor;
        double volumeFactor = areaFactor * lengthFactor;

        summary.UnitName = millimeterPart ? "mm" : "in";
        summary.SurfaceArea = massProperties[0] * areaFactor;
        summary.Volume = massProperties[1] * volumeFactor;
        summary.CentroidX = massProperties[3] * lengthFactor;
        summary.CentroidY = massProperties[4] * lengthFactor;
        summary.CentroidZ = massProperties[5] * lengthFactor;
        return summary;
    }

    private static List<Body> GetAnalysisBodies(Part workPart)
    {
        List<Body> bodies = new List<Body>();
        HashSet<Tag> bodyTags = new HashSet<Tag>();

        foreach (Body body in workPart.Bodies)
        {
            AddAnalysisBody(bodies, bodyTags, body);
        }

        Component root = workPart.ComponentAssembly.RootComponent;
        if (root != null)
        {
            foreach (Component child in root.GetChildren())
            {
                CollectComponentBodies(child, bodies, bodyTags);
            }
        }

        return bodies;
    }

    private static void CollectComponentBodies(
        Component component,
        List<Body> bodies,
        HashSet<Tag> bodyTags)
    {
        Part prototypePart = component.Prototype as Part;
        if (prototypePart != null)
        {
            foreach (Body prototypeBody in prototypePart.Bodies)
            {
                Body occurrenceBody =
                    component.FindOccurrence(prototypeBody) as Body;
                if (occurrenceBody != null)
                {
                    AddAnalysisBody(bodies, bodyTags, occurrenceBody);
                }
            }
        }

        foreach (Component child in component.GetChildren())
        {
            CollectComponentBodies(child, bodies, bodyTags);
        }
    }

    private static void AddAnalysisBody(
        List<Body> bodies,
        HashSet<Tag> bodyTags,
        Body body)
    {
        if (body == null ||
            !body.IsSolidBody ||
            bodyTags.Contains(body.Tag))
        {
            return;
        }

        bodyTags.Add(body.Tag);
        bodies.Add(body);
    }

    private static List<EntityAnalysis> BuildEntityAnalyses(
        Part workPart,
        UFSession ufSession,
        List<Body> bodies,
        List<CylinderLayer> allHoleLayers)
    {
        List<EntityAnalysis> result = new List<EntityAnalysis>();
        Unit areaUnit = workPart.UnitCollection.GetBase("Area");
        Unit lengthUnit = workPart.UnitCollection.GetBase("Length");

        foreach (Body body in bodies)
        {
            EntityAnalysis entity = new EntityAnalysis
            {
                BodyTag = body.Tag,
                Summary = AnalyzePartSummary(
                    workPart,
                    ufSession,
                    new List<Body> { body })
            };

            foreach (CylinderLayer hole in allHoleLayers)
            {
                if (hole.BodyTag == body.Tag)
                {
                    entity.HoleLayers.Add(hole);
                }
            }
            entity.HoleAxisGroups =
                BuildHoleAxisGroups(entity.HoleLayers);

            List<PlaneGroup> planeGroups = new List<PlaneGroup>();
            foreach (Face face in body.GetFaces())
            {
                int faceType;
                double[] point = new double[3];
                double[] direction = new double[3];
                double[] box = new double[6];
                double radius;
                double radiusData;
                int normalDirection;

                ufSession.Modl.AskFaceData(
                    face.Tag,
                    out faceType,
                    point,
                    direction,
                    box,
                    out radius,
                    out radiusData,
                    out normalDirection);

                if (faceType == PlanarFace)
                {
                    double area;
                    using (MeasureFaces measure =
                        workPart.MeasureManager.NewFaceProperties(
                            areaUnit,
                            lengthUnit,
                            0.999,
                            new IParameterizedSurface[] { face }))
                    {
                        area = measure.Area;
                    }
                    AddOrMergePlaneGroup(
                        planeGroups,
                        point,
                        direction,
                        area);
                }
                else if (faceType == ToroidalFace)
                {
                    AddOrMergeRadiusGroup(
                        entity.ToroidalRadii,
                        Math.Abs(radiusData),
                        Math.Abs(radius));
                }

                if (faceType == CylindricalFace &&
                    Math.Abs(direction[1]) < AxisTolerance)
                {
                    AddOrMergeBendRadiusGroup(
                        entity.BendRadii,
                        Math.Abs(radius),
                        GetGeneralAxisCategory(direction),
                        normalDirection);
                }
            }

            List<PlanePair> pairs = FindMatchingPlanePairs(
                GetMajorPlanes(planeGroups));
            entity.ThicknessSummaries =
                SummarizeThicknesses(pairs);
            entity.DominantThickness =
                GetDominantThickness(entity.ThicknessSummaries);
            foreach (ThicknessSummary thickness in
                entity.ThicknessSummaries)
            {
                if (Math.Abs(
                        thickness.Thickness -
                        entity.DominantThickness) <= 0.001)
                {
                    entity.DominantThicknessEvidence =
                        thickness.TotalCount;
                    break;
                }
            }

            if (entity.DominantThickness > 0.0)
            {
                foreach (RadiusPair pair in FindRadiusPairs(
                    entity.ToroidalRadii,
                    entity.DominantThickness))
                {
                    entity.RadiusPairEvidence += Math.Min(
                        pair.Inner.FaceCount,
                        pair.Outer.FaceCount);
                }

                foreach (BendRadiusPair pair in FindBendRadiusPairs(
                    entity.BendRadii,
                    entity.DominantThickness))
                {
                    entity.RadiusPairEvidence += Math.Min(
                        pair.Inner.FaceCount,
                        pair.Outer.FaceCount);
                }
            }

            entity.ToroidalRadii.Sort(delegate(
                RadiusGroup left,
                RadiusGroup right)
            {
                return left.MinorRadius.CompareTo(right.MinorRadius);
            });
            entity.BendRadii.Sort(delegate(
                BendRadiusGroup left,
                BendRadiusGroup right)
            {
                int radiusCompare =
                    left.Radius.CompareTo(right.Radius);
                if (radiusCompare != 0) return radiusCompare;
                return string.Compare(
                    left.AxisCategory,
                    right.AxisCategory,
                    StringComparison.Ordinal);
            });

            result.Add(entity);
        }

        return result;
    }

    private static string ExportCsv(
        Part workPart,
        PartSummary summary,
        List<CylinderLayer> roundHoles,
        List<SlotFeature> slots,
        List<PlaneGroup> majorPlanes,
        List<RadiusGroup> toroidalRadiusGroups,
        List<CylinderLayer> externalCylinders,
        List<BossFeature> bosses,
        List<HoleAxisGroup> holeAxisGroups,
        List<BendRadiusGroup> bendRadiusGroups,
        List<EntityAnalysis> entityAnalyses)
    {
        string partPath = workPart.FullPath;
        string directory =
            string.IsNullOrEmpty(partPath)
                ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
                : Path.GetDirectoryName(partPath);

        string fileName = SanitizeFileName(workPart.Leaf) +
            "_自动测量报告.csv";
        string reportPath = Path.Combine(directory, fileName);

        using (StreamWriter writer = new StreamWriter(
            reportPath,
            false,
            new UTF8Encoding(true)))
        {
            writer.WriteLine("NX自动测量报告");
            writer.WriteLine("零件名称," + Csv(workPart.Leaf));
            writer.WriteLine("导出时间," +
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
            writer.WriteLine();

            writer.WriteLine("[零件概览]");
            writer.WriteLine("项目,数值,单位");
            WriteValue(writer, "实体数量", summary.BodyCount, "");
            WriteValue(writer, "面数量", summary.FaceCount, "");
            WriteValue(writer, "X方向尺寸", summary.Box[3] - summary.Box[0], summary.UnitName);
            WriteValue(writer, "Y方向尺寸", summary.Box[4] - summary.Box[1], summary.UnitName);
            WriteValue(writer, "Z方向尺寸", summary.Box[5] - summary.Box[2], summary.UnitName);
            WriteValue(writer, "表面积", summary.SurfaceArea, summary.UnitName + "²");
            WriteValue(writer, "体积", summary.Volume, summary.UnitName + "³");
            WriteValue(writer, "重心X", summary.CentroidX, summary.UnitName);
            WriteValue(writer, "重心Y", summary.CentroidY, summary.UnitName);
            WriteValue(writer, "重心Z", summary.CentroidZ, summary.UnitName);
            writer.WriteLine();

            writer.WriteLine("[实体级概览]");
            writer.WriteLine(
                "实体编号,面数量,X尺寸,Y尺寸,Z尺寸,最小外形尺寸,表面积,体积,重心X,重心Y,重心Z,孔层数量,独立孔轴线数,主厚度候选,厚度证据数,半径配对证据数,厚度可信度,单位");
            for (int i = 0; i < entityAnalyses.Count; i++)
            {
                EntityAnalysis entity = entityAnalyses[i];
                writer.WriteLine(
                    "E" + (i + 1).ToString("000") + "," +
                    entity.Summary.FaceCount + "," +
                    Number(entity.Summary.Box[3] - entity.Summary.Box[0]) + "," +
                    Number(entity.Summary.Box[4] - entity.Summary.Box[1]) + "," +
                    Number(entity.Summary.Box[5] - entity.Summary.Box[2]) + "," +
                    Number(entity.MinimumBoundingSize()) + "," +
                    Number(entity.Summary.SurfaceArea) + "," +
                    Number(entity.Summary.Volume) + "," +
                    Number(entity.Summary.CentroidX) + "," +
                    Number(entity.Summary.CentroidY) + "," +
                    Number(entity.Summary.CentroidZ) + "," +
                    entity.HoleLayers.Count + "," +
                    entity.HoleAxisGroups.Count + "," +
                    (entity.DominantThickness > 0.0
                        ? Number(entity.DominantThickness)
                        : "") + "," +
                    entity.DominantThicknessEvidence + "," +
                    entity.RadiusPairEvidence + "," +
                    Csv(entity.ThicknessConfidence()) + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[实体级孔特征]");
            writer.WriteLine(
                "实体编号,孔轴线组,中心X,中心Z,直径层,圆柱层数量,判断,单位");
            for (int i = 0; i < entityAnalyses.Count; i++)
            {
                EntityAnalysis entity = entityAnalyses[i];
                for (int j = 0; j < entity.HoleAxisGroups.Count; j++)
                {
                    HoleAxisGroup group = entity.HoleAxisGroups[j];
                    writer.WriteLine(
                        "E" + (i + 1).ToString("000") + "," +
                        "EH" + (j + 1).ToString("000") + "," +
                        Number(group.AxisX) + "," +
                        Number(group.AxisZ) + "," +
                        Csv(group.DiameterSummary()) + "," +
                        group.Layers.Count + "," +
                        Csv(group.Layers.Count > 1
                            ? "单实体复合孔"
                            : "单层圆孔") + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[实体级厚度统计]");
            writer.WriteLine(
                "实体编号,厚度,配对数量,X法向数量,Y法向数量,Z法向数量,斜向数量,与最小外形尺寸比较,判断,单位");
            for (int i = 0; i < entityAnalyses.Count; i++)
            {
                foreach (ThicknessSummary thickness in
                    entityAnalyses[i].ThicknessSummaries)
                {
                    writer.WriteLine(
                        "E" + (i + 1).ToString("000") + "," +
                        Number(thickness.Thickness) + "," +
                        thickness.TotalCount + "," +
                        thickness.XCount + "," +
                        thickness.YCount + "," +
                        thickness.ZCount + "," +
                        thickness.ObliqueCount + "," +
                        Csv(thickness.Thickness <=
                            entityAnalyses[i].MinimumBoundingSize() + 0.02
                                ? "不大于最小外形尺寸"
                                : "大于最小外形尺寸") + "," +
                        Csv(entityAnalyses[i].ThicknessAssessment(
                            thickness)) + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[实体级圆环半径]");
            writer.WriteLine(
                "实体编号,半径,曲面数量,主半径最小,主半径最大,单位");
            for (int i = 0; i < entityAnalyses.Count; i++)
            {
                foreach (RadiusGroup radius in
                    entityAnalyses[i].ToroidalRadii)
                {
                    writer.WriteLine(
                        "E" + (i + 1).ToString("000") + "," +
                        Number(radius.MinorRadius) + "," +
                        radius.FaceCount + "," +
                        Number(radius.MinMajorRadius) + "," +
                        Number(radius.MaxMajorRadius) + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[实体级折弯圆柱半径]");
            writer.WriteLine(
                "实体编号,半径,轴向类别,法向类别,曲面数量,单位");
            for (int i = 0; i < entityAnalyses.Count; i++)
            {
                foreach (BendRadiusGroup bend in
                    entityAnalyses[i].BendRadii)
                {
                    writer.WriteLine(
                        "E" + (i + 1).ToString("000") + "," +
                        Number(bend.Radius) + "," +
                        Csv(bend.AxisCategory) + "," +
                        Csv(bend.NormalDirection < 0
                            ? "内法向候选"
                            : "外法向候选") + "," +
                        bend.FaceCount + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[圆孔]");
            writer.WriteLine("编号,直径,中心X,中心Y,中心Z,Y最小,Y最大,圆柱段长,单位");
            for (int i = 0; i < roundHoles.Count; i++)
            {
                CylinderLayer hole = roundHoles[i];
                writer.WriteLine(
                    "H" + (i + 1).ToString("000") + "," +
                    Number(hole.Diameter) + "," +
                    Number(hole.AxisX) + "," +
                    Number((hole.MinY + hole.MaxY) / 2.0) + "," +
                    Number(hole.AxisZ) + "," +
                    Number(hole.MinY) + "," +
                    Number(hole.MaxY) + "," +
                    Number(hole.Length()) + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[孔轴线组]");
            writer.WriteLine(
                "组编号,中心X,中心Z,圆柱层数量,直径层,实体数量,分实体明细,层编号,判断,单位");
            for (int i = 0; i < holeAxisGroups.Count; i++)
            {
                HoleAxisGroup group = holeAxisGroups[i];
                writer.WriteLine(
                    "HG" + (i + 1).ToString("000") + "," +
                    Number(group.AxisX) + "," +
                    Number(group.AxisZ) + "," +
                    group.Layers.Count + "," +
                    Csv(group.DiameterSummary()) + "," +
                    group.BodyCount() + "," +
                    Csv(group.PerBodySummary()) + "," +
                    Csv(group.LayerIdSummary(roundHoles)) + "," +
                    Csv(group.Classification()) + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[腰形孔]");
            writer.WriteLine(
                "编号,宽度,总长,两端中心距,中心X,中心Y,中心Z,方向,共享直壁数,单位");
            for (int i = 0; i < slots.Count; i++)
            {
                SlotFeature slot = slots[i];
                writer.WriteLine(
                    "S" + (i + 1).ToString("000") + "," +
                    Number(slot.Width) + "," +
                    Number(slot.OverallLength) + "," +
                    Number(slot.CenterDistance) + "," +
                    Number(slot.CenterX) + "," +
                    Number((slot.FirstEnd.MinY + slot.FirstEnd.MaxY) / 2.0) + "," +
                    Number(slot.CenterZ) + "," +
                    Csv(slot.Direction) + "," +
                    slot.WallFaces.Count + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[高可信环形凸台规格汇总]");
            writer.WriteLine(
                "规格编号,数量,外径,内径,径向宽度,完整凸台数,局部凸台数,关联孔,判断,单位");
            List<BossSpecification> bossSpecifications =
                SummarizeBosses(bosses, true);
            for (int i = 0; i < bossSpecifications.Count; i++)
            {
                BossSpecification specification = bossSpecifications[i];
                writer.WriteLine(
                    "BS" + (i + 1).ToString("000") + "," +
                    specification.Count + "," +
                    Number(specification.OuterDiameter) + "," +
                    Number(specification.InnerDiameter) + "," +
                    Number((specification.OuterDiameter -
                        specification.InnerDiameter) / 2.0) + "," +
                    specification.FullCount + "," +
                    specification.PartialCount + "," +
                    Csv(string.Join(" ", specification.HoleIds.ToArray())) + "," +
                    Csv(specification.FullCount == specification.Count
                        ? "完整环形凸台规格"
                        : "局部环形凸台/翻边规格") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[外圆柱原始候选附录]");
            writer.WriteLine(
                "编号,外径,中心X,中心Y,中心Z,Y最小,Y最大,圆柱段高,圆周覆盖率,完整度,同轴圆孔,单位");
            for (int i = 0; i < externalCylinders.Count; i++)
            {
                CylinderLayer cylinder = externalCylinders[i];
                int holeIndex = FindCoaxialHoleIndex(
                    cylinder,
                    roundHoles);
                writer.WriteLine(
                    "C" + (i + 1).ToString("000") + "," +
                    Number(cylinder.Diameter) + "," +
                    Number(cylinder.AxisX) + "," +
                    Number((cylinder.MinY + cylinder.MaxY) / 2.0) + "," +
                    Number(cylinder.AxisZ) + "," +
                    Number(cylinder.MinY) + "," +
                    Number(cylinder.MaxY) + "," +
                    Number(cylinder.Length()) + "," +
                    Number(cylinder.AreaCoverage() * 100.0) + "%," +
                    Csv(cylinder.AreaCoverage() >= RoundHoleCoverage
                        ? "完整圆柱"
                        : "局部圆柱") + "," +
                    (holeIndex >= 0
                        ? "H" + (holeIndex + 1).ToString("000")
                        : "") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[同轴外圆柱关系明细]");
            writer.WriteLine(
                "编号,外圆柱,同轴圆孔,外径,内径,径向宽度,中心X,中心Y,中心Z,外圆柱段高,圆周覆盖率,判断,单位");
            for (int i = 0; i < bosses.Count; i++)
            {
                BossFeature boss = bosses[i];
                writer.WriteLine(
                    "B" + (i + 1).ToString("000") + "," +
                    "C" + (boss.ExternalIndex + 1).ToString("000") + "," +
                    "H" + (boss.HoleIndex + 1).ToString("000") + "," +
                    Number(boss.External.Diameter) + "," +
                    Number(boss.Hole.Diameter) + "," +
                    Number((boss.External.Diameter - boss.Hole.Diameter) / 2.0) + "," +
                    Number(boss.External.AxisX) + "," +
                    Number((boss.External.MinY + boss.External.MaxY) / 2.0) + "," +
                    Number(boss.External.AxisZ) + "," +
                    Number(boss.External.Length()) + "," +
                    Number(boss.External.AreaCoverage() * 100.0) + "%," +
                    Csv(boss.External.AreaCoverage() >= RoundHoleCoverage
                        ? "高可信完整环形凸台"
                        : "低覆盖率同轴外轮廓/翻边候选") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[主要平面]");
            writer.WriteLine(
                "编号,方向类别,法向X,法向Y,法向Z,平面位置,合计面积,面数量,单位");
            for (int i = 0; i < majorPlanes.Count; i++)
            {
                PlaneGroup plane = majorPlanes[i];
                writer.WriteLine(
                    "PL" + (i + 1).ToString("000") + "," +
                    Csv(plane.AxisCategory()) + "," +
                    Number(plane.NormalX) + "," +
                    Number(plane.NormalY) + "," +
                    Number(plane.NormalZ) + "," +
                    Number(plane.Offset) + "," +
                    Number(plane.Area) + "," +
                    plane.FaceCount + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[圆环与过渡半径统计]");
            writer.WriteLine(
                "编号,次半径候选,曲面数量,主半径最小,主半径最大,可信度,说明,单位");
            toroidalRadiusGroups.Sort(delegate(RadiusGroup left, RadiusGroup right)
            {
                return left.MinorRadius.CompareTo(right.MinorRadius);
            });
            for (int i = 0; i < toroidalRadiusGroups.Count; i++)
            {
                RadiusGroup radiusGroup = toroidalRadiusGroups[i];
                writer.WriteLine(
                    "R" + (i + 1).ToString("000") + "," +
                    Number(radiusGroup.MinorRadius) + "," +
                    radiusGroup.FaceCount + "," +
                    Number(radiusGroup.MinMajorRadius) + "," +
                    Number(radiusGroup.MaxMajorRadius) + "," +
                    Csv(radiusGroup.FaceCount >= 4
                        ? "高频"
                        : radiusGroup.FaceCount >= 2
                            ? "重复"
                            : "单处") + "," +
                    Csv("圆环面次半径；可能对应圆角、翻边或筋条转角") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            List<PlanePair> thicknessPairs =
                FindMatchingPlanePairs(majorPlanes);
            List<ThicknessSummary> thicknessSummaries =
                SummarizeThicknesses(thicknessPairs);
            double dominantThickness =
                GetDominantThickness(thicknessSummaries);

            writer.WriteLine("[折弯圆柱半径统计]");
            writer.WriteLine(
                "编号,半径,轴向类别,法向类别,曲面数量,判断,单位");
            bendRadiusGroups.Sort(delegate(
                BendRadiusGroup left,
                BendRadiusGroup right)
            {
                int radiusCompare =
                    left.Radius.CompareTo(right.Radius);
                if (radiusCompare != 0) return radiusCompare;
                return string.Compare(
                    left.AxisCategory,
                    right.AxisCategory,
                    StringComparison.Ordinal);
            });
            for (int i = 0; i < bendRadiusGroups.Count; i++)
            {
                BendRadiusGroup group = bendRadiusGroups[i];
                writer.WriteLine(
                    "BR" + (i + 1).ToString("000") + "," +
                    Number(group.Radius) + "," +
                    Csv(group.AxisCategory) + "," +
                    Csv(group.NormalDirection < 0
                        ? "内法向候选"
                        : "外法向候选") + "," +
                    group.FaceCount + "," +
                    Csv("非孔方向圆柱面；可能对应折弯、卷边或轮廓圆弧") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[折弯内外半径配对]");
            writer.WriteLine(
                "配对编号,内侧半径,外侧半径,半径差,轴向类别,估计配对数,判断,单位");
            List<BendRadiusPair> bendPairs = FindBendRadiusPairs(
                bendRadiusGroups,
                dominantThickness);
            for (int i = 0; i < bendPairs.Count; i++)
            {
                BendRadiusPair pair = bendPairs[i];
                writer.WriteLine(
                    "BP" + (i + 1).ToString("000") + "," +
                    Number(pair.Inner.Radius) + "," +
                    Number(pair.Outer.Radius) + "," +
                    Number(pair.Outer.Radius - pair.Inner.Radius) + "," +
                    Csv(pair.Inner.AxisCategory) + "," +
                    Math.Min(
                        pair.Inner.FaceCount,
                        pair.Outer.FaceCount) + "," +
                    Csv("半径差等于主板厚") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[内外过渡半径配对]");
            writer.WriteLine(
                "配对编号,内侧半径,外侧半径,半径差,内侧曲面数,外侧曲面数,估计配对数,判断,单位");
            List<RadiusPair> radiusPairs = FindRadiusPairs(
                toroidalRadiusGroups,
                dominantThickness);
            for (int i = 0; i < radiusPairs.Count; i++)
            {
                RadiusPair pair = radiusPairs[i];
                writer.WriteLine(
                    "RP" + (i + 1).ToString("000") + "," +
                    Number(pair.Inner.MinorRadius) + "," +
                    Number(pair.Outer.MinorRadius) + "," +
                    Number(pair.Outer.MinorRadius - pair.Inner.MinorRadius) + "," +
                    pair.Inner.FaceCount + "," +
                    pair.Outer.FaceCount + "," +
                    Math.Min(pair.Inner.FaceCount, pair.Outer.FaceCount) + "," +
                    Csv(pair.IsAmbiguous
                        ? "候选关系；半径参与多组配对"
                        : "高可信内外侧关系") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[平面厚度配对]");
            writer.WriteLine(
                "配对编号,平面1,平面2,方向类别,面积1,面积2,厚度,面积差比例,单位");
            for (int i = 0; i < thicknessPairs.Count; i++)
            {
                PlanePair pair = thicknessPairs[i];
                writer.WriteLine(
                    "T" + (i + 1).ToString("000") + "," +
                    "PL" + (pair.FirstIndex + 1).ToString("000") + "," +
                    "PL" + (pair.SecondIndex + 1).ToString("000") + "," +
                    Csv(pair.First.AxisCategory()) + "," +
                    Number(pair.First.Area) + "," +
                    Number(pair.Second.Area) + "," +
                    Number(pair.Distance) + "," +
                    Number(pair.AreaDifferenceRatio * 100.0) + "%," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[厚度统计]");
            writer.WriteLine(
                "厚度,配对数量,X法向数量,Y法向数量,Z法向数量,斜向数量,判断,单位");
            foreach (ThicknessSummary item in thicknessSummaries)
            {
                writer.WriteLine(
                    Number(item.Thickness) + "," +
                    item.TotalCount + "," +
                    item.XCount + "," +
                    item.YCount + "," +
                    item.ZCount + "," +
                    item.ObliqueCount + "," +
                    Csv(item.TotalCount >= 3 ? "高可信常用厚度" : "局部厚度") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[Y向平面层级]");
            writer.WriteLine(
                "平面编号,Y位置,合计面积,面数量,与上一层距离,单位");
            PlaneGroup previousYPlane = null;
            for (int i = 0; i < majorPlanes.Count; i++)
            {
                PlaneGroup plane = majorPlanes[i];
                if (plane.AxisCategory() != "Y法向")
                {
                    continue;
                }

                writer.WriteLine(
                    "PL" + (i + 1).ToString("000") + "," +
                    Number(plane.Offset) + "," +
                    Number(plane.Area) + "," +
                    plane.FaceCount + "," +
                    (previousYPlane == null
                        ? ""
                        : Number(plane.Offset - previousYPlane.Offset)) + "," +
                    summary.UnitName);
                previousYPlane = plane;
            }
            writer.WriteLine();

            writer.WriteLine("[独立孔位矩形阵列]");
            writer.WriteLine(
                "阵列编号,孔位数量,X向孔距,Z向孔距,对角距,孔轴线组,判断,单位");
            List<AxisRectangularPattern> axisPatterns =
                FindAxisRectangularPatterns(holeAxisGroups);
            for (int i = 0; i < axisPatterns.Count; i++)
            {
                AxisRectangularPattern pattern = axisPatterns[i];
                writer.WriteLine(
                    "AP" + (i + 1).ToString("000") + "," +
                    "4," +
                    Number(pattern.PitchX) + "," +
                    Number(pattern.PitchZ) + "," +
                    Number(Math.Sqrt(
                        pattern.PitchX * pattern.PitchX +
                        pattern.PitchZ * pattern.PitchZ)) + "," +
                    Csv(pattern.GroupIds) + "," +
                    Csv("按独立空间轴线识别") + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[圆孔层矩形阵列附录]");
            writer.WriteLine(
                "阵列编号,孔径,孔数量,X向孔距,Z向孔距,对角距,孔编号,单位");
            List<RectangularPattern> patterns =
                FindRectangularPatterns(roundHoles);
            for (int i = 0; i < patterns.Count; i++)
            {
                RectangularPattern pattern = patterns[i];
                writer.WriteLine(
                    "P" + (i + 1).ToString("000") + "," +
                    Number(pattern.Diameter) + "," +
                    "4," +
                    Number(pattern.PitchX) + "," +
                    Number(pattern.PitchZ) + "," +
                    Number(Math.Sqrt(
                        pattern.PitchX * pattern.PitchX +
                        pattern.PitchZ * pattern.PitchZ)) + "," +
                    Csv(pattern.HoleIds) + "," +
                    summary.UnitName);
            }
            writer.WriteLine();

            writer.WriteLine("[关键孔距]");
            writer.WriteLine(
                "起点特征,终点特征,ΔX,ΔZ,中心距,关系,说明,单位");
            WriteKeyAxisDistances(
                writer,
                holeAxisGroups,
                slots,
                summary.UnitName);
            writer.WriteLine();

            writer.WriteLine("[孔轴线组中心距]");
            writer.WriteLine(
                "起点组,终点组,ΔX,ΔZ,中心距,关系,单位");
            for (int i = 0; i < holeAxisGroups.Count; i++)
            {
                for (int j = i + 1; j < holeAxisGroups.Count; j++)
                {
                    double deltaX =
                        holeAxisGroups[j].AxisX -
                        holeAxisGroups[i].AxisX;
                    double deltaZ =
                        holeAxisGroups[j].AxisZ -
                        holeAxisGroups[i].AxisZ;
                    writer.WriteLine(
                        "HG" + (i + 1).ToString("000") + "," +
                        "HG" + (j + 1).ToString("000") + "," +
                        Number(deltaX) + "," +
                        Number(deltaZ) + "," +
                        Number(Math.Sqrt(
                            deltaX * deltaX +
                            deltaZ * deltaZ)) + "," +
                        Csv(GetAlignment(deltaX, deltaZ)) + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[圆孔层中心距附录]");
            writer.WriteLine(
                "起点孔,终点孔,起点直径,终点直径,ΔX,ΔZ,中心距,关系,单位");
            for (int i = 0; i < roundHoles.Count; i++)
            {
                for (int j = i + 1; j < roundHoles.Count; j++)
                {
                    CylinderLayer first = roundHoles[i];
                    CylinderLayer second = roundHoles[j];
                    double deltaX = second.AxisX - first.AxisX;
                    double deltaZ = second.AxisZ - first.AxisZ;

                    writer.WriteLine(
                        "H" + (i + 1).ToString("000") + "," +
                        "H" + (j + 1).ToString("000") + "," +
                        Number(first.Diameter) + "," +
                        Number(second.Diameter) + "," +
                        Number(deltaX) + "," +
                        Number(deltaZ) + "," +
                        Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                        Csv(GetAlignment(deltaX, deltaZ)) + "," +
                        summary.UnitName);
                }
            }
            writer.WriteLine();

            writer.WriteLine("[腰形孔中心到圆孔中心距离]");
            writer.WriteLine(
                "腰形孔,圆孔,ΔX,ΔZ,中心距,关系,单位");
            for (int i = 0; i < slots.Count; i++)
            {
                SlotFeature slot = slots[i];
                for (int j = 0; j < roundHoles.Count; j++)
                {
                    CylinderLayer hole = roundHoles[j];
                    double deltaX = hole.AxisX - slot.CenterX;
                    double deltaZ = hole.AxisZ - slot.CenterZ;

                    writer.WriteLine(
                        "S" + (i + 1).ToString("000") + "," +
                        "H" + (j + 1).ToString("000") + "," +
                        Number(deltaX) + "," +
                        Number(deltaZ) + "," +
                        Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                        Csv(GetAlignment(deltaX, deltaZ)) + "," +
                        summary.UnitName);
                }
            }
        }

        return reportPath;
    }

    private static double GetDominantThickness(
        List<ThicknessSummary> summaries)
    {
        double thickness = 0.0;
        int bestCount = 0;

        foreach (ThicknessSummary summary in summaries)
        {
            if (summary.TotalCount > bestCount)
            {
                bestCount = summary.TotalCount;
                thickness = summary.Thickness;
            }
        }

        return thickness;
    }

    private static List<RadiusPair> FindRadiusPairs(
        List<RadiusGroup> groups,
        double dominantThickness)
    {
        const double radiusTolerance = 0.002;
        List<RadiusPair> result = new List<RadiusPair>();
        Dictionary<RadiusGroup, int> participation =
            new Dictionary<RadiusGroup, int>();

        if (dominantThickness <= 0.0)
        {
            return result;
        }

        for (int i = 0; i < groups.Count; i++)
        {
            for (int j = 0; j < groups.Count; j++)
            {
                if (groups[j].MinorRadius <= groups[i].MinorRadius)
                {
                    continue;
                }

                double difference =
                    groups[j].MinorRadius - groups[i].MinorRadius;
                if (Math.Abs(difference - dominantThickness) >
                    radiusTolerance)
                {
                    continue;
                }

                result.Add(new RadiusPair
                {
                    Inner = groups[i],
                    Outer = groups[j]
                });

                IncrementParticipation(participation, groups[i]);
                IncrementParticipation(participation, groups[j]);
            }
        }

        foreach (RadiusPair pair in result)
        {
            pair.IsAmbiguous =
                participation[pair.Inner] > 1 ||
                participation[pair.Outer] > 1;
        }

        result.Sort(delegate(RadiusPair left, RadiusPair right)
        {
            return left.Inner.MinorRadius.CompareTo(
                right.Inner.MinorRadius);
        });
        return result;
    }

    private static void IncrementParticipation(
        Dictionary<RadiusGroup, int> participation,
        RadiusGroup group)
    {
        int count;
        participation.TryGetValue(group, out count);
        participation[group] = count + 1;
    }

    private static List<BossFeature> BuildBossFeatures(
        List<CylinderLayer> externalCylinders,
        List<CylinderLayer> holes)
    {
        List<BossFeature> bosses = new List<BossFeature>();

        for (int i = 0; i < externalCylinders.Count; i++)
        {
            CylinderLayer external = externalCylinders[i];
            int holeIndex = FindCoaxialHoleIndex(external, holes);
            if (holeIndex < 0 ||
                external.Diameter <= holes[holeIndex].Diameter)
            {
                continue;
            }

            bosses.Add(new BossFeature
            {
                ExternalIndex = i,
                HoleIndex = holeIndex,
                External = external,
                Hole = holes[holeIndex]
            });
        }

        return bosses;
    }

    private static List<BossSpecification> SummarizeBosses(
        List<BossFeature> bosses,
        bool fullOnly)
    {
        const double specificationTolerance = 0.01;
        List<BossSpecification> specifications =
            new List<BossSpecification>();

        foreach (BossFeature boss in bosses)
        {
            if (fullOnly &&
                boss.External.AreaCoverage() < RoundHoleCoverage)
            {
                continue;
            }

            BossSpecification matching = null;
            foreach (BossSpecification specification in specifications)
            {
                if (Math.Abs(
                        specification.OuterDiameter -
                        boss.External.Diameter) <= specificationTolerance &&
                    Math.Abs(
                        specification.InnerDiameter -
                        boss.Hole.Diameter) <= specificationTolerance)
                {
                    matching = specification;
                    break;
                }
            }

            if (matching == null)
            {
                matching = new BossSpecification
                {
                    OuterDiameter = boss.External.Diameter,
                    InnerDiameter = boss.Hole.Diameter
                };
                specifications.Add(matching);
            }

            matching.Count++;
            if (boss.External.AreaCoverage() >= RoundHoleCoverage)
            {
                matching.FullCount++;
            }
            else
            {
                matching.PartialCount++;
            }
            matching.HoleIds.Add(
                "H" + (boss.HoleIndex + 1).ToString("000"));
        }

        specifications.Sort(delegate(
            BossSpecification left,
            BossSpecification right)
        {
            int outerCompare =
                left.OuterDiameter.CompareTo(right.OuterDiameter);
            if (outerCompare != 0) return outerCompare;
            return left.InnerDiameter.CompareTo(right.InnerDiameter);
        });
        return specifications;
    }

    private static int FindCoaxialHoleIndex(
        CylinderLayer external,
        List<CylinderLayer> holes)
    {
        int bestIndex = -1;
        double bestDistance = double.MaxValue;

        for (int i = 0; i < holes.Count; i++)
        {
            if (external.BodyTag != holes[i].BodyTag)
            {
                continue;
            }

            double distance = Distance2d(
                external.AxisX,
                external.AxisZ,
                holes[i].AxisX,
                holes[i].AxisZ);
            if (distance <= PositionTolerance &&
                distance < bestDistance)
            {
                bestIndex = i;
                bestDistance = distance;
            }
        }

        return bestIndex;
    }

    private static void AddOrMergeRadiusGroup(
        List<RadiusGroup> groups,
        double minorRadius,
        double majorRadius)
    {
        const double radiusTolerance = 0.001;
        if (minorRadius <= 0.0)
        {
            return;
        }

        foreach (RadiusGroup group in groups)
        {
            if (Math.Abs(group.MinorRadius - minorRadius) <= radiusTolerance)
            {
                group.FaceCount++;
                group.MinMajorRadius = Math.Min(
                    group.MinMajorRadius,
                    majorRadius);
                group.MaxMajorRadius = Math.Max(
                    group.MaxMajorRadius,
                    majorRadius);
                return;
            }
        }

        groups.Add(new RadiusGroup
        {
            MinorRadius = minorRadius,
            FaceCount = 1,
            MinMajorRadius = majorRadius,
            MaxMajorRadius = majorRadius
        });
    }

    private static void AddOrMergeBendRadiusGroup(
        List<BendRadiusGroup> groups,
        double radius,
        string axisCategory,
        int normalDirection)
    {
        const double radiusTolerance = 0.001;
        if (radius <= 0.0)
        {
            return;
        }

        foreach (BendRadiusGroup group in groups)
        {
            if (Math.Abs(group.Radius - radius) <= radiusTolerance &&
                group.AxisCategory == axisCategory &&
                group.NormalDirection == normalDirection)
            {
                group.FaceCount++;
                return;
            }
        }

        groups.Add(new BendRadiusGroup
        {
            Radius = radius,
            AxisCategory = axisCategory,
            NormalDirection = normalDirection,
            FaceCount = 1
        });
    }

    private static string GetGeneralAxisCategory(double[] direction)
    {
        double x = Math.Abs(direction[0]);
        double y = Math.Abs(direction[1]);
        double z = Math.Abs(direction[2]);

        if (x >= AxisTolerance) return "X轴";
        if (y >= AxisTolerance) return "Y轴";
        if (z >= AxisTolerance) return "Z轴";
        return "斜轴";
    }

    private static List<BendRadiusPair> FindBendRadiusPairs(
        List<BendRadiusGroup> groups,
        double dominantThickness)
    {
        const double radiusTolerance = 0.002;
        List<BendRadiusPair> result = new List<BendRadiusPair>();

        if (dominantThickness <= 0.0)
        {
            return result;
        }

        for (int i = 0; i < groups.Count; i++)
        {
            for (int j = 0; j < groups.Count; j++)
            {
                BendRadiusGroup inner = groups[i];
                BendRadiusGroup outer = groups[j];
                if (inner.AxisCategory != outer.AxisCategory ||
                    inner.NormalDirection >= 0 ||
                    outer.NormalDirection < 0 ||
                    outer.Radius <= inner.Radius)
                {
                    continue;
                }

                if (Math.Abs(
                        outer.Radius -
                        inner.Radius -
                        dominantThickness) <= radiusTolerance)
                {
                    result.Add(new BendRadiusPair
                    {
                        Inner = inner,
                        Outer = outer
                    });
                }
            }
        }

        result.Sort(delegate(BendRadiusPair left, BendRadiusPair right)
        {
            int axisCompare = string.Compare(
                left.Inner.AxisCategory,
                right.Inner.AxisCategory,
                StringComparison.Ordinal);
            if (axisCompare != 0) return axisCompare;
            return left.Inner.Radius.CompareTo(right.Inner.Radius);
        });
        return result;
    }

    private static List<PlanePair> FindMatchingPlanePairs(
        List<PlaneGroup> planes)
    {
        const double maximumPairDistance = 10.0;
        const double maximumAreaDifferenceRatio = 0.01;
        List<PlanePair> candidates = new List<PlanePair>();

        for (int i = 0; i < planes.Count; i++)
        {
            for (int j = i + 1; j < planes.Count; j++)
            {
                PlaneGroup first = planes[i];
                PlaneGroup second = planes[j];
                if (!first.IsParallelTo(second) ||
                    first.AxisCategory() != second.AxisCategory())
                {
                    continue;
                }

                double distance = Math.Abs(second.Offset - first.Offset);
                if (distance <= 0.001 || distance > maximumPairDistance)
                {
                    continue;
                }

                double maximumArea = Math.Max(first.Area, second.Area);
                double areaDifferenceRatio =
                    maximumArea <= 0.0
                        ? 1.0
                        : Math.Abs(first.Area - second.Area) / maximumArea;
                if (areaDifferenceRatio > maximumAreaDifferenceRatio)
                {
                    continue;
                }

                candidates.Add(new PlanePair
                {
                    FirstIndex = i,
                    SecondIndex = j,
                    First = first,
                    Second = second,
                    Distance = distance,
                    AreaDifferenceRatio = areaDifferenceRatio
                });
            }
        }

        candidates.Sort(delegate(PlanePair left, PlanePair right)
        {
            int distanceCompare = left.Distance.CompareTo(right.Distance);
            if (distanceCompare != 0) return distanceCompare;
            return left.FirstIndex.CompareTo(right.FirstIndex);
        });

        bool[] used = new bool[planes.Count];
        List<PlanePair> result = new List<PlanePair>();
        foreach (PlanePair candidate in candidates)
        {
            if (used[candidate.FirstIndex] || used[candidate.SecondIndex])
            {
                continue;
            }

            used[candidate.FirstIndex] = true;
            used[candidate.SecondIndex] = true;
            result.Add(candidate);
        }

        result.Sort(delegate(PlanePair left, PlanePair right)
        {
            int categoryCompare = string.Compare(
                left.First.AxisCategory(),
                right.First.AxisCategory(),
                StringComparison.Ordinal);
            if (categoryCompare != 0) return categoryCompare;
            return left.First.Offset.CompareTo(right.First.Offset);
        });
        return result;
    }

    private static List<ThicknessSummary> SummarizeThicknesses(
        List<PlanePair> pairs)
    {
        SortedDictionary<double, ThicknessSummary> summaries =
            new SortedDictionary<double, ThicknessSummary>();

        foreach (PlanePair pair in pairs)
        {
            double roundedThickness = Math.Round(pair.Distance, 3);
            ThicknessSummary summary;
            if (!summaries.TryGetValue(roundedThickness, out summary))
            {
                summary = new ThicknessSummary
                {
                    Thickness = roundedThickness
                };
                summaries.Add(roundedThickness, summary);
            }

            summary.TotalCount++;
            string category = pair.First.AxisCategory();
            if (category == "X法向") summary.XCount++;
            else if (category == "Y法向") summary.YCount++;
            else if (category == "Z法向") summary.ZCount++;
            else summary.ObliqueCount++;
        }

        return new List<ThicknessSummary>(summaries.Values);
    }

    private static void AddOrMergePlaneGroup(
        List<PlaneGroup> groups,
        double[] point,
        double[] direction,
        double area)
    {
        double length = Math.Sqrt(
            direction[0] * direction[0] +
            direction[1] * direction[1] +
            direction[2] * direction[2]);
        if (length <= 0.0)
        {
            return;
        }

        double nx = direction[0] / length;
        double ny = direction[1] / length;
        double nz = direction[2] / length;

        // 固定法向符号，保证同一平面不会因法向相反而分成两组。
        if (nx < -0.000001 ||
            (Math.Abs(nx) <= 0.000001 && ny < -0.000001) ||
            (Math.Abs(nx) <= 0.000001 &&
             Math.Abs(ny) <= 0.000001 &&
             nz < 0.0))
        {
            nx = -nx;
            ny = -ny;
            nz = -nz;
        }

        double offset =
            nx * point[0] +
            ny * point[1] +
            nz * point[2];

        foreach (PlaneGroup group in groups)
        {
            double dot =
                group.NormalX * nx +
                group.NormalY * ny +
                group.NormalZ * nz;
            if (dot >= 0.99999 &&
                Math.Abs(group.Offset - offset) <= 0.02)
            {
                group.Area += area;
                group.FaceCount++;
                return;
            }
        }

        groups.Add(new PlaneGroup
        {
            NormalX = nx,
            NormalY = ny,
            NormalZ = nz,
            Offset = offset,
            Area = area,
            FaceCount = 1
        });
    }

    private static List<PlaneGroup> GetMajorPlanes(
        List<PlaneGroup> groups)
    {
        const double minimumArea = 20.0;
        List<PlaneGroup> result = new List<PlaneGroup>();

        foreach (PlaneGroup group in groups)
        {
            if (group.Area >= minimumArea)
            {
                result.Add(group);
            }
        }

        result.Sort(delegate(PlaneGroup left, PlaneGroup right)
        {
            int categoryCompare = string.Compare(
                left.AxisCategory(),
                right.AxisCategory(),
                StringComparison.Ordinal);
            if (categoryCompare != 0)
            {
                return categoryCompare;
            }
            return left.Offset.CompareTo(right.Offset);
        });

        return result;
    }

    private static void WriteKeyDistances(
        StreamWriter writer,
        List<CylinderLayer> holes,
        List<SlotFeature> slots,
        string unitName)
    {
        const double alignmentTolerance = 0.02;

        for (int i = 0; i < holes.Count; i++)
        {
            for (int j = i + 1; j < holes.Count; j++)
            {
                double deltaX = holes[j].AxisX - holes[i].AxisX;
                double deltaZ = holes[j].AxisZ - holes[i].AxisZ;
                bool aligned =
                    Math.Abs(deltaX) <= alignmentTolerance ||
                    Math.Abs(deltaZ) <= alignmentTolerance;
                if (!aligned)
                {
                    continue;
                }

                writer.WriteLine(
                    "H" + (i + 1).ToString("000") + "," +
                    "H" + (j + 1).ToString("000") + "," +
                    Number(deltaX) + "," +
                    Number(deltaZ) + "," +
                    Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                    Csv(GetAlignment(deltaX, deltaZ)) + "," +
                    Csv("同轴向对齐孔距") + "," +
                    unitName);
            }
        }

        for (int i = 0; i < slots.Count; i++)
        {
            for (int j = 0; j < holes.Count; j++)
            {
                double deltaX = holes[j].AxisX - slots[i].CenterX;
                double deltaZ = holes[j].AxisZ - slots[i].CenterZ;
                bool aligned =
                    Math.Abs(deltaX) <= alignmentTolerance ||
                    Math.Abs(deltaZ) <= alignmentTolerance;
                if (!aligned)
                {
                    continue;
                }

                writer.WriteLine(
                    "S" + (i + 1).ToString("000") + "," +
                    "H" + (j + 1).ToString("000") + "," +
                    Number(deltaX) + "," +
                    Number(deltaZ) + "," +
                    Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                    Csv(GetAlignment(deltaX, deltaZ)) + "," +
                    Csv("腰形孔中心到圆孔中心") + "," +
                    unitName);
            }
        }
    }

    private static void WriteKeyAxisDistances(
        StreamWriter writer,
        List<HoleAxisGroup> groups,
        List<SlotFeature> slots,
        string unitName)
    {
        const double alignmentTolerance = 0.02;

        for (int i = 0; i < groups.Count; i++)
        {
            for (int j = i + 1; j < groups.Count; j++)
            {
                double deltaX = groups[j].AxisX - groups[i].AxisX;
                double deltaZ = groups[j].AxisZ - groups[i].AxisZ;
                if (Math.Abs(deltaX) > alignmentTolerance &&
                    Math.Abs(deltaZ) > alignmentTolerance)
                {
                    continue;
                }

                writer.WriteLine(
                    "HG" + (i + 1).ToString("000") + "," +
                    "HG" + (j + 1).ToString("000") + "," +
                    Number(deltaX) + "," +
                    Number(deltaZ) + "," +
                    Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                    Csv(GetAlignment(deltaX, deltaZ)) + "," +
                    Csv("独立孔轴线中心距") + "," +
                    unitName);
            }
        }

        for (int i = 0; i < slots.Count; i++)
        {
            for (int j = 0; j < groups.Count; j++)
            {
                double deltaX = groups[j].AxisX - slots[i].CenterX;
                double deltaZ = groups[j].AxisZ - slots[i].CenterZ;
                if (Math.Abs(deltaX) > alignmentTolerance &&
                    Math.Abs(deltaZ) > alignmentTolerance)
                {
                    continue;
                }

                writer.WriteLine(
                    "S" + (i + 1).ToString("000") + "," +
                    "HG" + (j + 1).ToString("000") + "," +
                    Number(deltaX) + "," +
                    Number(deltaZ) + "," +
                    Number(Math.Sqrt(deltaX * deltaX + deltaZ * deltaZ)) + "," +
                    Csv(GetAlignment(deltaX, deltaZ)) + "," +
                    Csv("腰形孔中心到孔轴线中心") + "," +
                    unitName);
            }
        }
    }

    private static List<HoleAxisGroup> BuildHoleAxisGroups(
        List<CylinderLayer> holes)
    {
        List<HoleAxisGroup> groups = new List<HoleAxisGroup>();

        foreach (CylinderLayer hole in holes)
        {
            HoleAxisGroup matching = null;
            foreach (HoleAxisGroup group in groups)
            {
                if (Distance2d(
                        group.AxisX,
                        group.AxisZ,
                        hole.AxisX,
                        hole.AxisZ) <= PositionTolerance)
                {
                    matching = group;
                    break;
                }
            }

            if (matching == null)
            {
                matching = new HoleAxisGroup
                {
                    AxisX = hole.AxisX,
                    AxisZ = hole.AxisZ
                };
                groups.Add(matching);
            }

            matching.Layers.Add(hole);
        }

        groups.Sort(delegate(HoleAxisGroup left, HoleAxisGroup right)
        {
            int xCompare = left.AxisX.CompareTo(right.AxisX);
            return xCompare != 0
                ? xCompare
                : left.AxisZ.CompareTo(right.AxisZ);
        });
        return groups;
    }

    private static List<RectangularPattern> FindRectangularPatterns(
        List<CylinderLayer> holes)
    {
        const double coordinateTolerance = 0.02;
        List<RectangularPattern> patterns = new List<RectangularPattern>();

        for (int a = 0; a < holes.Count; a++)
        {
            for (int b = a + 1; b < holes.Count; b++)
            {
                for (int c = b + 1; c < holes.Count; c++)
                {
                    for (int d = c + 1; d < holes.Count; d++)
                    {
                        int[] indices = new int[] { a, b, c, d };
                        if (!SameDiameter(holes, indices))
                        {
                            continue;
                        }

                        List<double> xValues = UniqueCoordinates(
                            holes,
                            indices,
                            true,
                            coordinateTolerance);
                        List<double> zValues = UniqueCoordinates(
                            holes,
                            indices,
                            false,
                            coordinateTolerance);

                        if (xValues.Count != 2 || zValues.Count != 2 ||
                            !ContainsEveryCorner(
                                holes,
                                indices,
                                xValues,
                                zValues,
                                coordinateTolerance))
                        {
                            continue;
                        }

                        patterns.Add(new RectangularPattern
                        {
                            Diameter = holes[a].Diameter,
                            PitchX = Math.Abs(xValues[1] - xValues[0]),
                            PitchZ = Math.Abs(zValues[1] - zValues[0]),
                            HoleIds =
                                "H" + (a + 1).ToString("000") + " " +
                                "H" + (b + 1).ToString("000") + " " +
                                "H" + (c + 1).ToString("000") + " " +
                                "H" + (d + 1).ToString("000")
                        });
                    }
                }
            }
        }

        return patterns;
    }

    private static List<AxisRectangularPattern> FindAxisRectangularPatterns(
        List<HoleAxisGroup> groups)
    {
        const double coordinateTolerance = 0.02;
        List<AxisRectangularPattern> patterns =
            new List<AxisRectangularPattern>();

        for (int a = 0; a < groups.Count; a++)
        {
            for (int b = a + 1; b < groups.Count; b++)
            {
                for (int c = b + 1; c < groups.Count; c++)
                {
                    for (int d = c + 1; d < groups.Count; d++)
                    {
                        int[] indices = new int[] { a, b, c, d };
                        List<double> xValues = UniqueAxisCoordinates(
                            groups,
                            indices,
                            true,
                            coordinateTolerance);
                        List<double> zValues = UniqueAxisCoordinates(
                            groups,
                            indices,
                            false,
                            coordinateTolerance);

                        if (xValues.Count != 2 ||
                            zValues.Count != 2 ||
                            !ContainsEveryAxisCorner(
                                groups,
                                indices,
                                xValues,
                                zValues,
                                coordinateTolerance))
                        {
                            continue;
                        }

                        patterns.Add(new AxisRectangularPattern
                        {
                            PitchX = Math.Abs(xValues[1] - xValues[0]),
                            PitchZ = Math.Abs(zValues[1] - zValues[0]),
                            GroupIds =
                                "HG" + (a + 1).ToString("000") + " " +
                                "HG" + (b + 1).ToString("000") + " " +
                                "HG" + (c + 1).ToString("000") + " " +
                                "HG" + (d + 1).ToString("000")
                        });
                    }
                }
            }
        }

        return patterns;
    }

    private static List<double> UniqueAxisCoordinates(
        List<HoleAxisGroup> groups,
        int[] indices,
        bool useX,
        double tolerance)
    {
        List<double> values = new List<double>();
        foreach (int index in indices)
        {
            double value =
                useX ? groups[index].AxisX : groups[index].AxisZ;
            bool found = false;
            foreach (double existing in values)
            {
                if (Math.Abs(existing - value) <= tolerance)
                {
                    found = true;
                    break;
                }
            }
            if (!found)
            {
                values.Add(value);
            }
        }
        values.Sort();
        return values;
    }

    private static bool ContainsEveryAxisCorner(
        List<HoleAxisGroup> groups,
        int[] indices,
        List<double> xValues,
        List<double> zValues,
        double tolerance)
    {
        foreach (double x in xValues)
        {
            foreach (double z in zValues)
            {
                bool found = false;
                foreach (int index in indices)
                {
                    if (Math.Abs(groups[index].AxisX - x) <= tolerance &&
                        Math.Abs(groups[index].AxisZ - z) <= tolerance)
                    {
                        found = true;
                        break;
                    }
                }
                if (!found)
                {
                    return false;
                }
            }
        }
        return true;
    }

    private static bool SameDiameter(
        List<CylinderLayer> holes,
        int[] indices)
    {
        double diameter = holes[indices[0]].Diameter;
        foreach (int index in indices)
        {
            if (Math.Abs(holes[index].Diameter - diameter) > DiameterTolerance)
            {
                return false;
            }
        }
        return true;
    }

    private static List<double> UniqueCoordinates(
        List<CylinderLayer> holes,
        int[] indices,
        bool useX,
        double tolerance)
    {
        List<double> values = new List<double>();
        foreach (int index in indices)
        {
            double value = useX ? holes[index].AxisX : holes[index].AxisZ;
            bool found = false;
            foreach (double existing in values)
            {
                if (Math.Abs(existing - value) <= tolerance)
                {
                    found = true;
                    break;
                }
            }
            if (!found)
            {
                values.Add(value);
            }
        }
        values.Sort();
        return values;
    }

    private static bool ContainsEveryCorner(
        List<CylinderLayer> holes,
        int[] indices,
        List<double> xValues,
        List<double> zValues,
        double tolerance)
    {
        foreach (double x in xValues)
        {
            foreach (double z in zValues)
            {
                bool found = false;
                foreach (int index in indices)
                {
                    if (Math.Abs(holes[index].AxisX - x) <= tolerance &&
                        Math.Abs(holes[index].AxisZ - z) <= tolerance)
                    {
                        found = true;
                        break;
                    }
                }
                if (!found)
                {
                    return false;
                }
            }
        }
        return true;
    }

    private static string GetAlignment(double deltaX, double deltaZ)
    {
        const double alignmentTolerance = 0.02;
        if (Math.Abs(deltaZ) <= alignmentTolerance)
        {
            return "X向对齐";
        }
        if (Math.Abs(deltaX) <= alignmentTolerance)
        {
            return "Z向对齐";
        }
        return "斜向";
    }

    private static void WriteValue(
        StreamWriter writer,
        string name,
        double value,
        string unit)
    {
        writer.WriteLine(Csv(name) + "," + Number(value) + "," + Csv(unit));
    }

    private static string Number(double value)
    {
        return value.ToString("0.###", CultureInfo.InvariantCulture);
    }

    private static string Csv(string value)
    {
        if (value == null)
        {
            return "";
        }

        return "\"" + value.Replace("\"", "\"\"") + "\"";
    }

    private static string SanitizeFileName(string value)
    {
        foreach (char invalid in Path.GetInvalidFileNameChars())
        {
            value = value.Replace(invalid, '_');
        }
        return value;
    }

    private static Dictionary<Tag, Face> GetAdjacentPlanarFaces(
        Face sourceFace,
        UFSession ufSession)
    {
        Dictionary<Tag, Face> result = new Dictionary<Tag, Face>();

        foreach (Edge edge in sourceFace.GetEdges())
        {
            foreach (Face adjacentFace in edge.GetFaces())
            {
                if (adjacentFace.Tag == sourceFace.Tag)
                {
                    continue;
                }

                int adjacentType;
                ufSession.Modl.AskFaceType(adjacentFace.Tag, out adjacentType);
                if (adjacentType == PlanarFace)
                {
                    result[adjacentFace.Tag] = adjacentFace;
                }
            }
        }

        return result;
    }

    private static void AddOrMergeLayer(
        List<CylinderLayer> layers,
        Tag bodyTag,
        double diameter,
        double axisX,
        double axisZ,
        double minY,
        double maxY,
        double area,
        Face sourceFace,
        Dictionary<Tag, Face> adjacentPlanarFaces)
    {
        foreach (CylinderLayer layer in layers)
        {
            if (layer.BodyTag == bodyTag &&
                Math.Abs(layer.Diameter - diameter) <= DiameterTolerance &&
                Distance2d(layer.AxisX, layer.AxisZ, axisX, axisZ) <=
                PositionTolerance)
            {
                layer.MinY = Math.Min(layer.MinY, minY);
                layer.MaxY = Math.Max(layer.MaxY, maxY);
                layer.Area += area;
                layer.FaceCount++;
                layer.CylinderFaces.Add(sourceFace);
                AddFaces(layer.AdjacentPlanarFaces, adjacentPlanarFaces);
                return;
            }
        }

        CylinderLayer newLayer = new CylinderLayer
        {
            BodyTag = bodyTag,
            Diameter = diameter,
            AxisX = axisX,
            AxisZ = axisZ,
            MinY = minY,
            MaxY = maxY,
            Area = area,
            FaceCount = 1
        };
        newLayer.CylinderFaces.Add(sourceFace);
        AddFaces(newLayer.AdjacentPlanarFaces, adjacentPlanarFaces);
        layers.Add(newLayer);
    }

    private static void AddFaces(
        Dictionary<Tag, Face> target,
        Dictionary<Tag, Face> source)
    {
        foreach (KeyValuePair<Tag, Face> item in source)
        {
            target[item.Key] = item.Value;
        }
    }

    private static List<SlotFeature> PairSlotEnds(
        List<CylinderLayer> ends,
        UFSession ufSession)
    {
        List<SlotFeature> slots = new List<SlotFeature>();
        bool[] used = new bool[ends.Count];

        for (int i = 0; i < ends.Count; i++)
        {
            if (used[i])
            {
                continue;
            }

            CylinderLayer first = ends[i];
            int bestIndex = -1;
            List<Face> bestWallFaces = null;
            double bestDistance = double.MaxValue;

            for (int j = i + 1; j < ends.Count; j++)
            {
                if (used[j])
                {
                    continue;
                }

                CylinderLayer second = ends[j];
                if (Math.Abs(first.Diameter - second.Diameter) > DiameterTolerance ||
                    Math.Abs(first.MinY - second.MinY) > RangeTolerance ||
                    Math.Abs(first.MaxY - second.MaxY) > RangeTolerance)
                {
                    continue;
                }

                double dx = Math.Abs(first.AxisX - second.AxisX);
                double dz = Math.Abs(first.AxisZ - second.AxisZ);
                bool alignedX = dx <= PositionTolerance && dz > PositionTolerance;
                bool alignedZ = dz <= PositionTolerance && dx > PositionTolerance;
                if (!alignedX && !alignedZ)
                {
                    continue;
                }

                string slotDirection = alignedX ? "Z向" : "X向";
                List<Face> wallFaces = GetSharedSlotWallFaces(
                    first,
                    second,
                    slotDirection,
                    ufSession);
                if (wallFaces.Count < 2)
                {
                    continue;
                }

                double distance = Math.Sqrt(dx * dx + dz * dz);
                if (bestWallFaces == null ||
                    wallFaces.Count > bestWallFaces.Count ||
                    (wallFaces.Count == bestWallFaces.Count &&
                     distance < bestDistance))
                {
                    bestIndex = j;
                    bestWallFaces = wallFaces;
                    bestDistance = distance;
                }
            }

            if (bestIndex < 0)
            {
                continue;
            }

            CylinderLayer paired = ends[bestIndex];
            double centerDistance = Distance2d(
                first.AxisX,
                first.AxisZ,
                paired.AxisX,
                paired.AxisZ);

            slots.Add(new SlotFeature
            {
                Width = (first.Diameter + paired.Diameter) / 2.0,
                CenterDistance = centerDistance,
                OverallLength =
                    (first.Diameter + paired.Diameter) / 2.0 + centerDistance,
                CenterX = (first.AxisX + paired.AxisX) / 2.0,
                CenterZ = (first.AxisZ + paired.AxisZ) / 2.0,
                Direction =
                    Math.Abs(first.AxisX - paired.AxisX) >
                    Math.Abs(first.AxisZ - paired.AxisZ)
                        ? "X向"
                        : "Z向",
                FirstEnd = first,
                SecondEnd = paired
            });
            slots[slots.Count - 1].WallFaces.AddRange(bestWallFaces);

            used[i] = true;
            used[bestIndex] = true;
        }

        return slots;
    }

    private static List<Face> GetSharedSlotWallFaces(
        CylinderLayer first,
        CylinderLayer second,
        string slotDirection,
        UFSession ufSession)
    {
        List<Face> result = new List<Face>();

        foreach (KeyValuePair<Tag, Face> item in first.AdjacentPlanarFaces)
        {
            if (!second.AdjacentPlanarFaces.ContainsKey(item.Key))
            {
                continue;
            }

            int faceType;
            double[] point = new double[3];
            double[] direction = new double[3];
            double[] box = new double[6];
            double radius;
            double radiusData;
            int normalDirection;
            ufSession.Modl.AskFaceData(
                item.Key,
                out faceType,
                point,
                direction,
                box,
                out radius,
                out radiusData,
                out normalDirection);

            // 槽轴线沿X时，直壁法向沿Z；槽轴线沿Z时，直壁法向沿X。
            bool isWall =
                slotDirection == "X向"
                    ? Math.Abs(direction[2]) >= AxisTolerance
                    : Math.Abs(direction[0]) >= AxisTolerance;

            if (isWall && Math.Abs(direction[1]) < 0.1)
            {
                result.Add(item.Value);
            }
        }

        return result;
    }

    private static int CompareLayers(CylinderLayer left, CylinderLayer right)
    {
        int xCompare = left.AxisX.CompareTo(right.AxisX);
        return xCompare != 0 ? xCompare : left.AxisZ.CompareTo(right.AxisZ);
    }

    private static int CompareSlots(SlotFeature left, SlotFeature right)
    {
        int xCompare = left.CenterX.CompareTo(right.CenterX);
        return xCompare != 0 ? xCompare : left.CenterZ.CompareTo(right.CenterZ);
    }

    private static double Distance2d(double x1, double z1, double x2, double z2)
    {
        double dx = x1 - x2;
        double dz = z1 - z2;
        return Math.Sqrt(dx * dx + dz * dz);
    }

    private static string Format(double value)
    {
        return value.ToString("0.###");
    }

    private class CylinderLayer
    {
        public Tag BodyTag;
        public double Diameter;
        public double AxisX;
        public double AxisZ;
        public double MinY;
        public double MaxY;
        public double Area;
        public int FaceCount;
        public readonly List<Face> CylinderFaces = new List<Face>();
        public readonly Dictionary<Tag, Face> AdjacentPlanarFaces =
            new Dictionary<Tag, Face>();

        public double Length()
        {
            return MaxY - MinY;
        }

        public double AreaCoverage()
        {
            double theoreticalArea = Math.PI * Diameter * Length();
            return theoreticalArea <= 0.0 ? 0.0 : Area / theoreticalArea;
        }
    }

    private class SlotFeature
    {
        public double Width;
        public double CenterDistance;
        public double OverallLength;
        public double CenterX;
        public double CenterZ;
        public string Direction;
        public CylinderLayer FirstEnd;
        public CylinderLayer SecondEnd;
        public readonly List<Face> WallFaces = new List<Face>();
    }

    private class PartSummary
    {
        public int BodyCount;
        public int FaceCount;
        public readonly double[] Box = new double[6];
        public string UnitName = "mm";
        public double SurfaceArea;
        public double Volume;
        public double CentroidX;
        public double CentroidY;
        public double CentroidZ;
    }

    private class RectangularPattern
    {
        public double Diameter;
        public double PitchX;
        public double PitchZ;
        public string HoleIds;
    }

    private class AxisRectangularPattern
    {
        public double PitchX;
        public double PitchZ;
        public string GroupIds;
    }

    private class PlaneGroup
    {
        public double NormalX;
        public double NormalY;
        public double NormalZ;
        public double Offset;
        public double Area;
        public int FaceCount;

        public string AxisCategory()
        {
            if (Math.Abs(NormalX) >= AxisTolerance) return "X法向";
            if (Math.Abs(NormalY) >= AxisTolerance) return "Y法向";
            if (Math.Abs(NormalZ) >= AxisTolerance) return "Z法向";
            return "斜向";
        }

        public bool IsParallelTo(PlaneGroup other)
        {
            double dot =
                NormalX * other.NormalX +
                NormalY * other.NormalY +
                NormalZ * other.NormalZ;
            return Math.Abs(dot) >= 0.99999;
        }
    }

    private class PlanePair
    {
        public int FirstIndex;
        public int SecondIndex;
        public PlaneGroup First;
        public PlaneGroup Second;
        public double Distance;
        public double AreaDifferenceRatio;
    }

    private class ThicknessSummary
    {
        public double Thickness;
        public int TotalCount;
        public int XCount;
        public int YCount;
        public int ZCount;
        public int ObliqueCount;
    }

    private class RadiusGroup
    {
        public double MinorRadius;
        public int FaceCount;
        public double MinMajorRadius;
        public double MaxMajorRadius;
    }

    private class RadiusPair
    {
        public RadiusGroup Inner;
        public RadiusGroup Outer;
        public bool IsAmbiguous;
    }

    private class BendRadiusGroup
    {
        public double Radius;
        public string AxisCategory;
        public int NormalDirection;
        public int FaceCount;
    }

    private class BendRadiusPair
    {
        public BendRadiusGroup Inner;
        public BendRadiusGroup Outer;
    }

    private class BossFeature
    {
        public int ExternalIndex;
        public int HoleIndex;
        public CylinderLayer External;
        public CylinderLayer Hole;
    }

    private class BossSpecification
    {
        public double OuterDiameter;
        public double InnerDiameter;
        public int Count;
        public int FullCount;
        public int PartialCount;
        public readonly List<string> HoleIds = new List<string>();
    }

    private class HoleAxisGroup
    {
        public double AxisX;
        public double AxisZ;
        public readonly List<CylinderLayer> Layers =
            new List<CylinderLayer>();

        public string DiameterSummary()
        {
            List<double> diameters = new List<double>();
            foreach (CylinderLayer layer in Layers)
            {
                bool found = false;
                foreach (double diameter in diameters)
                {
                    if (Math.Abs(diameter - layer.Diameter) <=
                        DiameterTolerance)
                    {
                        found = true;
                        break;
                    }
                }
                if (!found)
                {
                    diameters.Add(layer.Diameter);
                }
            }

            diameters.Sort();
            List<string> parts = new List<string>();
            foreach (double diameter in diameters)
            {
                parts.Add("Ø" + Number(diameter));
            }
            return string.Join(" + ", parts.ToArray());
        }

        public int BodyCount()
        {
            HashSet<Tag> bodies = new HashSet<Tag>();
            foreach (CylinderLayer layer in Layers)
            {
                bodies.Add(layer.BodyTag);
            }
            return bodies.Count;
        }

        public string PerBodySummary()
        {
            Dictionary<Tag, List<double>> byBody =
                new Dictionary<Tag, List<double>>();
            foreach (CylinderLayer layer in Layers)
            {
                List<double> diameters;
                if (!byBody.TryGetValue(layer.BodyTag, out diameters))
                {
                    diameters = new List<double>();
                    byBody.Add(layer.BodyTag, diameters);
                }
                diameters.Add(layer.Diameter);
            }

            List<string> parts = new List<string>();
            int bodyIndex = 1;
            foreach (KeyValuePair<Tag, List<double>> item in byBody)
            {
                item.Value.Sort();
                List<string> diameterParts = new List<string>();
                foreach (double diameter in item.Value)
                {
                    diameterParts.Add("Ø" + Number(diameter));
                }
                parts.Add(
                    "实体" + bodyIndex + ":" +
                    string.Join("+", diameterParts.ToArray()));
                bodyIndex++;
            }
            return string.Join("; ", parts.ToArray());
        }

        public string Classification()
        {
            if (BodyCount() > 1)
            {
                return "跨实体同轴开孔";
            }
            if (Layers.Count > 1)
            {
                return "单实体复合孔";
            }
            return "单层圆孔";
        }

        public string LayerIdSummary(List<CylinderLayer> allHoles)
        {
            List<string> ids = new List<string>();
            foreach (CylinderLayer layer in Layers)
            {
                int index = allHoles.IndexOf(layer);
                if (index >= 0)
                {
                    ids.Add("H" + (index + 1).ToString("000"));
                }
            }
            return string.Join(" ", ids.ToArray());
        }
    }

    private class EntityAnalysis
    {
        public Tag BodyTag;
        public PartSummary Summary;
        public readonly List<CylinderLayer> HoleLayers =
            new List<CylinderLayer>();
        public List<HoleAxisGroup> HoleAxisGroups =
            new List<HoleAxisGroup>();
        public List<ThicknessSummary> ThicknessSummaries =
            new List<ThicknessSummary>();
        public double DominantThickness;
        public int DominantThicknessEvidence;
        public int RadiusPairEvidence;
        public readonly List<RadiusGroup> ToroidalRadii =
            new List<RadiusGroup>();
        public readonly List<BendRadiusGroup> BendRadii =
            new List<BendRadiusGroup>();

        public double MinimumBoundingSize()
        {
            return Math.Min(
                Summary.Box[3] - Summary.Box[0],
                Math.Min(
                    Summary.Box[4] - Summary.Box[1],
                    Summary.Box[5] - Summary.Box[2]));
        }

        public string ThicknessConfidence()
        {
            if (DominantThickness <= 0.0)
            {
                return "未识别";
            }
            if (DominantThickness > MinimumBoundingSize() + 0.02)
            {
                return "排除为整体厚度";
            }
            if (DominantThicknessEvidence >= 3 ||
                (DominantThicknessEvidence >= 2 &&
                 RadiusPairEvidence > 0))
            {
                return "高可信主厚度";
            }
            if (DominantThicknessEvidence >= 2 ||
                RadiusPairEvidence > 0)
            {
                return "中等可信厚度";
            }
            return "低可信局部尺寸";
        }

        public string ThicknessAssessment(ThicknessSummary thickness)
        {
            if (thickness.Thickness > MinimumBoundingSize() + 0.02)
            {
                return "排除：不可能是整体材料厚度";
            }
            if (Math.Abs(
                    thickness.Thickness -
                    DominantThickness) <= 0.001)
            {
                return ThicknessConfidence();
            }
            return thickness.TotalCount >= 3
                ? "重复局部厚度"
                : "局部厚度候选";
        }
    }

    public static int GetUnloadOption(string dummy)
    {
        return (int)Session.LibraryUnloadOption.Immediately;
    }
}
