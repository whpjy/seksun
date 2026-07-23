#include <BRepAdaptor_Curve.hxx>
#include <BRep_Builder.hxx>
#include <GeomAbs_CurveType.hxx>
#include <HLRAlgo_Projector.hxx>
#include <HLRBRep_Algo.hxx>
#include <HLRBRep_HLRToShape.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <Interface_Static.hxx>
#include <STEPControl_Reader.hxx>
#include <Standard_Handle.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp_Explorer.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Compound.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Shape.hxx>
#include <gp_Ax2.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <sstream>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr double kEpsilon = 1.0e-9;

struct Point2 {
    double x = 0.0;
    double y = 0.0;
};

struct Point3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct Polyline {
    std::vector<Point2> points;
};

struct CircleEvidence {
    Point2 center;
    double radius = 0.0;
    std::vector<int> angularBins;
    bool visible = false;
};

struct CenterMark {
    Point2 center;
    double radius = 0.0;
};

struct HoleCallout {
    std::vector<std::string> groupIds;
    std::vector<std::string> lines;
    Point2 target;
    Point2 label;
    double radius = 0.0;
};

struct HoleLocationTarget {
    std::vector<std::string> groupIds;
    Point2 center;
    double horizontalDatumValue =
        std::numeric_limits<double>::quiet_NaN();
    double verticalDatumValue =
        std::numeric_limits<double>::quiet_NaN();
};

struct EngineeringNote {
    std::string type;
    std::vector<std::string> lines;
    Point2 label;
    Point2 target;
    bool hasLeader = false;
};

struct RadiusLeaderCallout {
    double innerRadius = 0.0;
    double outerRadius = 0.0;
    int totalPairs = 0;
    int visibleMatches = 0;
    Point2 target;
    Point2 label;
};

struct ThicknessDimension {
    Point2 first;
    Point2 second;
    Point2 dimensionFirst;
    Point2 dimensionSecond;
    double value = 0.0;
};

struct Bounds2 {
    double minX = std::numeric_limits<double>::infinity();
    double minY = std::numeric_limits<double>::infinity();
    double maxX = -std::numeric_limits<double>::infinity();
    double maxY = -std::numeric_limits<double>::infinity();

    void add(const Point2& p) {
        minX = std::min(minX, p.x);
        minY = std::min(minY, p.y);
        maxX = std::max(maxX, p.x);
        maxY = std::max(maxY, p.y);
    }

    bool valid() const {
        return std::isfinite(minX) && std::isfinite(minY) &&
               std::isfinite(maxX) && std::isfinite(maxY);
    }

    double width() const { return maxX - minX; }
    double height() const { return maxY - minY; }
};

struct OpeningDimension {
    std::string id;
    Bounds2 bounds;
};

struct ViewDefinition {
    std::string id;
    std::string title;
    gp_Dir direction;
    gp_Dir xDirection;
};

struct ViewResult {
    ViewDefinition definition;
    std::vector<Polyline> visible;
    std::vector<Polyline> hidden;
    std::vector<CircleEvidence> circleEvidence;
    std::vector<CenterMark> centerMarks;
    std::vector<HoleCallout> holeCallouts;
    std::vector<HoleLocationTarget> holeLocationTargets;
    std::vector<EngineeringNote> engineeringNotes;
    std::vector<RadiusLeaderCallout> radiusCallouts;
    std::vector<ThicknessDimension> thicknessDimensions;
    std::vector<OpeningDimension> openingDimensions;
    Bounds2 bounds;
};

struct ViewPlacement {
    const ViewResult* view = nullptr;
    double offsetX = 0.0;
    double offsetY = 0.0;
};

struct AnalysisHoleGroup {
    std::string id;
    std::string type;
    Point3 axis;
    Point3 center;
    std::vector<std::string> bodyIds;
    std::vector<std::string> featureIds;
    std::vector<double> diameters;
};

struct AnalysisHolePattern {
    std::string id;
    std::string type;
    Point3 center;
    Point3 directionA;
    Point3 directionB;
    double spacingA = 0.0;
    double spacingB = 0.0;
    double diagonal = 0.0;
    std::vector<double> diameters;
    std::vector<std::string> groupIds;
};

struct AnalysisRadiusPair {
    double innerRadius = 0.0;
    double outerRadius = 0.0;
    double difference = 0.0;
    int estimatedPairs = 0;
    bool ambiguous = false;
};

struct AnalysisTorusPatch {
    std::string faceId;
    Point3 center;
    Point3 axis;
    Point3 surfacePoint;
    double majorRadius = 0.0;
    double minorRadius = 0.0;
};

struct AnalysisThicknessPair {
    Point3 normal;
    double firstOffset = 0.0;
    double secondOffset = 0.0;
    Point3 firstCentroid;
    Point3 secondCentroid;
    double distance = 0.0;
    double firstArea = 0.0;
    double secondArea = 0.0;
};

struct AnalysisPlanarOpening {
    std::string id;
    Point3 normal;
    Point3 minimum;
    Point3 maximum;
    int sourceWires = 0;
    int edges = 0;
};

struct AnalysisStudFeature {
    struct Segment {
        double diameter = 0.0;
        double length = 0.0;
        double startStation = 0.0;
        double endStation = 0.0;
    };
    std::string id;
    Point3 axis;
    Point3 axisPoint;
    double overallLength = 0.0;
    double nominalShaftDiameter = 0.0;
    double headDiameter = 0.0;
    double tipDiameter = 0.0;
    std::string assessment;
    std::vector<Segment> segments;
};

struct AnalysisDatumDimension {
    std::string id;
    std::string featureId;
    std::string axis;
    std::string datum;
    double coordinate = 0.0;
    double value = 0.0;
};

struct AnalysisData {
    fs::path sourcePath;
    std::string schemaVersion;
    std::string sourceFile;
    std::vector<AnalysisHoleGroup> holeGroups;
    std::vector<AnalysisHolePattern> holePatterns;
    double dominantThickness = 0.0;
    int thicknessEvidence = 0;
    std::string thicknessAssessment;
    std::vector<AnalysisRadiusPair> radiusPairs;
    std::vector<AnalysisTorusPatch> torusPatches;
    std::vector<AnalysisThicknessPair> thicknessPairs;
    std::vector<AnalysisPlanarOpening> planarOpenings;
    std::vector<AnalysisDatumDimension> datumDimensions;
    std::vector<AnalysisStudFeature> studFeatures;
};

Point3 readPoint3(const nlohmann::json& value, const char* fieldName) {
    if (!value.is_array() || value.size() != 3) {
        throw std::runtime_error(
            std::string("Analysis field is not a 3D point: ") + fieldName);
    }
    return {value.at(0).get<double>(),
            value.at(1).get<double>(),
            value.at(2).get<double>()};
}

AnalysisData readAnalysis(const fs::path& path, const fs::path& stepPath) {
    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("Cannot read analyzer JSON: " + path.string());
    }

    nlohmann::json json;
    input >> json;

    AnalysisData result;
    result.sourcePath = path;
    result.schemaVersion = json.at("schema_version").get<std::string>();
    result.sourceFile = json.at("source_file").get<std::string>();
    if (result.sourceFile != stepPath.filename().string()) {
        throw std::runtime_error(
            "Analyzer JSON belongs to a different STEP file: " +
            result.sourceFile);
    }

    for (const nlohmann::json& item : json.at("hole_axis_groups")) {
        AnalysisHoleGroup group;
        group.id = item.at("id").get<std::string>();
        group.type = item.at("type").get<std::string>();
        group.axis = readPoint3(item.at("axis"), "hole_axis_groups.axis");
        group.center = readPoint3(item.at("center"), "hole_axis_groups.center");
        group.bodyIds = item.at("body_ids").get<std::vector<std::string>>();
        group.featureIds = item.at("feature_ids").get<std::vector<std::string>>();
        group.diameters = item.at("diameters").get<std::vector<double>>();
        result.holeGroups.push_back(std::move(group));
    }

    for (const nlohmann::json& item : json.at("hole_patterns")) {
        AnalysisHolePattern pattern;
        pattern.id = item.at("id").get<std::string>();
        pattern.type = item.at("type").get<std::string>();
        pattern.center = readPoint3(item.at("center"), "hole_patterns.center");
        pattern.directionA = readPoint3(
            item.at("direction_a"), "hole_patterns.direction_a");
        pattern.directionB = readPoint3(
            item.at("direction_b"), "hole_patterns.direction_b");
        pattern.spacingA = item.at("spacing_a").get<double>();
        pattern.spacingB = item.at("spacing_b").get<double>();
        pattern.diagonal = item.at("diagonal").get<double>();
        pattern.diameters = item.at("diameters").get<std::vector<double>>();
        pattern.groupIds = item.at("group_ids").get<std::vector<std::string>>();
        result.holePatterns.push_back(std::move(pattern));
    }

    if (json.contains("datum_dimensions")) {
        for (const nlohmann::json& item : json.at("datum_dimensions")) {
            AnalysisDatumDimension dimension;
            dimension.id = item.value("id", std::string());
            dimension.featureId = item.value("feature_id", std::string());
            dimension.axis = item.value("axis", std::string());
            dimension.datum = item.value("datum", std::string());
            dimension.coordinate = item.value("coordinate", 0.0);
            dimension.value = item.value("value", 0.0);
            result.datumDimensions.push_back(std::move(dimension));
        }
    }

    if (json.contains("stud_features")) {
        for (const nlohmann::json& item : json.at("stud_features")) {
            AnalysisStudFeature stud;
            stud.id = item.value("id", std::string());
            stud.axis = readPoint3(item.at("axis"), "stud_features.axis");
            stud.axisPoint = readPoint3(
                item.at("axis_point"), "stud_features.axis_point");
            stud.overallLength = item.value("overall_length", 0.0);
            stud.nominalShaftDiameter = item.value(
                "nominal_shaft_diameter", 0.0);
            stud.headDiameter = item.value("head_diameter", 0.0);
            stud.tipDiameter = item.value("tip_diameter", 0.0);
            stud.assessment = item.value("assessment", std::string());
            if (item.contains("segments")) {
                for (const nlohmann::json& segmentItem : item.at("segments")) {
                    AnalysisStudFeature::Segment segment;
                    segment.diameter = segmentItem.value("diameter", 0.0);
                    segment.length = segmentItem.value("length", 0.0);
                    segment.startStation = segmentItem.value("start_station", 0.0);
                    segment.endStation = segmentItem.value("end_station", 0.0);
                    stud.segments.push_back(segment);
                }
            }
            result.studFeatures.push_back(std::move(stud));
        }
    }

    if (json.contains("planar_openings")) {
        for (const nlohmann::json& item : json.at("planar_openings")) {
            AnalysisPlanarOpening opening;
            opening.id = item.value("id", std::string());
            opening.normal = readPoint3(item.at("normal"),
                                        "planar_openings.normal");
            opening.minimum = readPoint3(item.at("min"),
                                         "planar_openings.min");
            opening.maximum = readPoint3(item.at("max"),
                                         "planar_openings.max");
            opening.sourceWires = item.value("source_wires", 0);
            opening.edges = item.value("edges", 0);
            result.planarOpenings.push_back(std::move(opening));
        }
    }

    if (json.contains("thickness_analysis")) {
        const nlohmann::json& thickness = json.at("thickness_analysis");
        result.dominantThickness =
            thickness.value("dominant_thickness", 0.0);
        result.thicknessEvidence =
            thickness.value("dominant_evidence", 0);
        result.thicknessAssessment =
            thickness.value("assessment", std::string());
        if (thickness.contains("dominant_pairs")) {
            for (const nlohmann::json& item :
                 thickness.at("dominant_pairs")) {
                AnalysisThicknessPair pair;
                pair.normal = readPoint3(item.at("normal"),
                                         "dominant_pairs.normal");
                pair.firstOffset = item.value("first_offset", 0.0);
                pair.secondOffset = item.value("second_offset", 0.0);
                pair.firstCentroid = readPoint3(
                    item.at("first_centroid"),
                    "dominant_pairs.first_centroid");
                pair.secondCentroid = readPoint3(
                    item.at("second_centroid"),
                    "dominant_pairs.second_centroid");
                pair.distance = item.value("distance", 0.0);
                pair.firstArea = item.value("first_area", 0.0);
                pair.secondArea = item.value("second_area", 0.0);
                result.thicknessPairs.push_back(std::move(pair));
            }
        }
    }

    if (json.contains("radius_pair_analysis")) {
        const nlohmann::json& radiusAnalysis =
            json.at("radius_pair_analysis");
        if (radiusAnalysis.contains("torus_pairs")) {
            for (const nlohmann::json& item :
                 radiusAnalysis.at("torus_pairs")) {
                AnalysisRadiusPair pair;
                pair.innerRadius = item.value("inner_radius", 0.0);
                pair.outerRadius = item.value("outer_radius", 0.0);
                pair.difference = item.value("difference", 0.0);
                pair.estimatedPairs = item.value("estimated_pairs", 0);
                pair.ambiguous = item.value("ambiguous", false);
                if (!pair.ambiguous && pair.innerRadius > kEpsilon &&
                    pair.outerRadius > pair.innerRadius) {
                    result.radiusPairs.push_back(pair);
                }
            }
        }
        if (radiusAnalysis.contains("torus_patches")) {
            for (const nlohmann::json& item :
                 radiusAnalysis.at("torus_patches")) {
                AnalysisTorusPatch patch;
                patch.faceId = item.value("face_id", std::string());
                patch.center = readPoint3(item.at("center"),
                                          "torus_patches.center");
                patch.axis = readPoint3(item.at("axis"),
                                        "torus_patches.axis");
                patch.surfacePoint = readPoint3(
                    item.at("surface_point"),
                    "torus_patches.surface_point");
                patch.majorRadius = item.value("major_radius", 0.0);
                patch.minorRadius = item.value("minor_radius", 0.0);
                result.torusPatches.push_back(std::move(patch));
            }
        }
    }
    return result;
}

TopoDS_Shape readStep(const fs::path& path) {
    STEPControl_Reader reader;
    Interface_Static::SetCVal("xstep.cascade.unit", "MM");
    Interface_Static::SetCVal("read.step.unit", "MM");

    const IFSelect_ReturnStatus status = reader.ReadFile(path.string().c_str());
    if (status != IFSelect_RetDone) {
        throw std::runtime_error("Cannot read STEP file: " + path.string());
    }

    const Standard_Integer roots = reader.NbRootsForTransfer();
    if (roots <= 0 || reader.TransferRoots() <= 0) {
        throw std::runtime_error("STEP file contains no transferable shape");
    }

    TopoDS_Shape shape = reader.OneShape();
    if (shape.IsNull()) {
        throw std::runtime_error("STEP transfer produced an empty shape");
    }
    return shape;
}

int sampleCount(const BRepAdaptor_Curve& curve) {
    switch (curve.GetType()) {
        case GeomAbs_Line:
            return 2;
        case GeomAbs_Circle:
        case GeomAbs_Ellipse:
            return 73;
        case GeomAbs_Parabola:
        case GeomAbs_Hyperbola:
            return 49;
        case GeomAbs_BezierCurve:
        case GeomAbs_BSplineCurve:
        case GeomAbs_OffsetCurve:
        case GeomAbs_OtherCurve:
        default:
            return 97;
    }
}

std::vector<Polyline> sampleShape(
    const TopoDS_Shape& shape,
    Bounds2& bounds,
    std::vector<CircleEvidence>* circleEvidence = nullptr,
    bool visibleEvidence = false) {
    std::vector<Polyline> result;
    if (shape.IsNull()) {
        return result;
    }

    for (TopExp_Explorer explorer(shape, TopAbs_EDGE); explorer.More(); explorer.Next()) {
        const TopoDS_Edge edge = TopoDS::Edge(explorer.Current());
        BRepAdaptor_Curve curve(edge);
        const double first = curve.FirstParameter();
        const double last = curve.LastParameter();
        if (!std::isfinite(first) || !std::isfinite(last) || last < first) {
            continue;
        }

        if (circleEvidence != nullptr && curve.GetType() == GeomAbs_Circle) {
            constexpr double fullCircle = 6.283185307179586;
            constexpr int angularBinCount = 720;
            const gp_Circ circle = curve.Circle();
            const gp_Pnt center = circle.Location();
            CircleEvidence evidence;
            evidence.center = {center.X(), center.Y()};
            evidence.radius = circle.Radius();
            evidence.visible = visibleEvidence;

            const double span = std::min(std::abs(last - first), fullCircle);
            const int evidenceSamples = std::max(
                2,
                static_cast<int>(std::ceil(
                    span / fullCircle * angularBinCount * 2.0)));
            evidence.angularBins.reserve(static_cast<std::size_t>(evidenceSamples));
            for (int sample = 0; sample < evidenceSamples; ++sample) {
                const double ratio =
                    (static_cast<double>(sample) + 0.5) /
                    static_cast<double>(evidenceSamples);
                const gp_Pnt point = curve.Value(first + (last - first) * ratio);
                double angle = std::atan2(
                    point.Y() - center.Y(),
                    point.X() - center.X());
                if (angle < 0.0) {
                    angle += fullCircle;
                }
                int bin = static_cast<int>(
                    std::floor(angle / fullCircle * angularBinCount));
                bin = std::clamp(bin, 0, angularBinCount - 1);
                evidence.angularBins.push_back(bin);
            }
            std::sort(evidence.angularBins.begin(), evidence.angularBins.end());
            evidence.angularBins.erase(
                std::unique(evidence.angularBins.begin(), evidence.angularBins.end()),
                evidence.angularBins.end());
            circleEvidence->push_back(std::move(evidence));
        }

        Polyline line;
        const int count = sampleCount(curve);
        line.points.reserve(static_cast<std::size_t>(count));
        for (int i = 0; i < count; ++i) {
            const double ratio = count == 1 ? 0.0 :
                static_cast<double>(i) / static_cast<double>(count - 1);
            const gp_Pnt point = curve.Value(first + (last - first) * ratio);
            Point2 projected{point.X(), point.Y()};
            line.points.push_back(projected);
            bounds.add(projected);
        }
        if (line.points.size() >= 2) {
            result.push_back(std::move(line));
        }
    }
    return result;
}

void appendSampled(std::vector<Polyline>& target,
                   const TopoDS_Shape& shape,
                   Bounds2& bounds,
                   std::vector<CircleEvidence>* circleEvidence = nullptr,
                   bool visibleEvidence = false) {
    std::vector<Polyline> sampled = sampleShape(
        shape, bounds, circleEvidence, visibleEvidence);
    target.insert(
        target.end(),
        std::make_move_iterator(sampled.begin()),
        std::make_move_iterator(sampled.end()));
}

void buildCenterMarks(ViewResult& view) {
    constexpr int angularBinCount = 720;
    constexpr double requiredCoverage = 0.94;
    std::vector<CircleEvidence> circles;

    for (const CircleEvidence& evidence : view.circleEvidence) {
        CircleEvidence* match = nullptr;
        for (CircleEvidence& circle : circles) {
            const double coordinateTolerance = 1.0e-4;
            const double radiusTolerance = std::max(1.0e-4, circle.radius * 1.0e-5);
            if (std::hypot(circle.center.x - evidence.center.x,
                           circle.center.y - evidence.center.y) <= coordinateTolerance &&
                std::abs(circle.radius - evidence.radius) <= radiusTolerance) {
                match = &circle;
                break;
            }
        }
        if (match == nullptr) {
            circles.push_back(evidence);
        } else {
            match->angularBins.insert(
                match->angularBins.end(),
                evidence.angularBins.begin(),
                evidence.angularBins.end());
        }
    }

    for (CircleEvidence& circle : circles) {
        std::sort(circle.angularBins.begin(), circle.angularBins.end());
        circle.angularBins.erase(
            std::unique(circle.angularBins.begin(), circle.angularBins.end()),
            circle.angularBins.end());
        const double coverage =
            static_cast<double>(circle.angularBins.size()) /
            static_cast<double>(angularBinCount);
        if (circle.radius <= kEpsilon ||
            coverage < requiredCoverage) {
            continue;
        }

        CenterMark* concentric = nullptr;
        for (CenterMark& mark : view.centerMarks) {
            if (std::hypot(mark.center.x - circle.center.x,
                           mark.center.y - circle.center.y) <= 1.0e-4) {
                concentric = &mark;
                break;
            }
        }
        if (concentric == nullptr) {
            view.centerMarks.push_back({circle.center, circle.radius});
        } else {
            concentric->radius = std::max(concentric->radius, circle.radius);
        }
    }

    std::sort(
        view.centerMarks.begin(),
        view.centerMarks.end(),
        [](const CenterMark& left, const CenterMark& right) {
            if (std::abs(left.center.x - right.center.x) > 1.0e-7) {
                return left.center.x < right.center.x;
            }
            return left.center.y < right.center.y;
        });
}

ViewResult project(const TopoDS_Shape& shape, const ViewDefinition& view) {
    Handle(HLRBRep_Algo) algorithm = new HLRBRep_Algo();
    algorithm->Add(shape);
    algorithm->Projector(HLRAlgo_Projector(
        gp_Ax2(gp_Pnt(0.0, 0.0, 0.0), view.direction, view.xDirection)));
    algorithm->Update();
    algorithm->Hide();

    HLRBRep_HLRToShape converted(algorithm);
    ViewResult result;
    result.definition = view;
    // A complete technical view combines sharp edges and silhouette edges.
    // Smooth tangent/sewn edges are intentionally omitted in this first version.
    appendSampled(
        result.visible,
        converted.VCompound(),
        result.bounds,
        &result.circleEvidence,
        true);
    appendSampled(result.visible, converted.OutLineVCompound(), result.bounds);
    appendSampled(
        result.hidden,
        converted.HCompound(),
        result.bounds,
        &result.circleEvidence,
        false);
    appendSampled(result.hidden, converted.OutLineHCompound(), result.bounds);
    buildCenterMarks(result);
    return result;
}

std::string number(double value) {
    if (std::abs(value) < 0.0000005) {
        value = 0.0;
    }
    std::ostringstream output;
    output << std::fixed << std::setprecision(6) << value;
    return output.str();
}

std::string dimensionNumber(double value) {
    std::ostringstream output;
    output << std::fixed << std::setprecision(3) << value;
    std::string text = output.str();
    while (!text.empty() && text.back() == '0') {
        text.pop_back();
    }
    if (!text.empty() && text.back() == '.') {
        text.pop_back();
    }
    return text;
}

double dot(const Point3& point, const gp_Dir& direction) {
    return point.x * direction.X() +
           point.y * direction.Y() +
           point.z * direction.Z();
}

Point2 projectPoint(const Point3& point, const ViewDefinition& view) {
    const gp_Dir yDirection = view.direction.Crossed(view.xDirection);
    return {dot(point, view.xDirection), dot(point, yDirection)};
}

std::string diameterText(const std::vector<double>& diameters) {
    std::ostringstream output;
    for (std::size_t index = 0; index < diameters.size(); ++index) {
        if (index != 0) {
            output << " / ";
        }
        output << "&#216;" << dimensionNumber(diameters[index]);
    }
    return output.str();
}

std::string diameterKey(const std::vector<double>& diameters) {
    std::ostringstream output;
    output << std::fixed << std::setprecision(4);
    for (const double diameter : diameters) {
        output << diameter << ';';
    }
    return output.str();
}

const AnalysisHoleGroup* findHoleGroup(
    const AnalysisData& analysis,
    const std::string& id) {
    for (const AnalysisHoleGroup& group : analysis.holeGroups) {
        if (group.id == id) {
            return &group;
        }
    }
    return nullptr;
}

int bestProjectionView(const AnalysisHoleGroup& group,
                       const std::vector<ViewResult>& views) {
    int bestIndex = -1;
    double bestAlignment = 0.0;
    for (std::size_t index = 0; index < views.size(); ++index) {
        const double alignment = std::abs(dot(group.axis, views[index].definition.direction));
        if (alignment > bestAlignment) {
            bestAlignment = alignment;
            bestIndex = static_cast<int>(index);
        }
    }
    return bestAlignment >= 0.98 ? bestIndex : -1;
}

const AnalysisHoleGroup* selectLeaderTarget(
    const std::vector<const AnalysisHoleGroup*>& groups,
    const ViewDefinition& view) {
    const AnalysisHoleGroup* result = nullptr;
    Point2 best;
    for (const AnalysisHoleGroup* group : groups) {
        const Point2 point = projectPoint(group->center, view);
        if (result == nullptr || point.x > best.x + 1.0e-7 ||
            (std::abs(point.x - best.x) <= 1.0e-7 && point.y > best.y)) {
            result = group;
            best = point;
        }
    }
    return result;
}

const AnalysisHoleGroup* selectDatumTarget(
    const std::vector<const AnalysisHoleGroup*>& groups,
    const ViewDefinition& view) {
    const AnalysisHoleGroup* result = nullptr;
    Point2 best;
    for (const AnalysisHoleGroup* group : groups) {
        const Point2 point = projectPoint(group->center, view);
        if (result == nullptr || point.x < best.x - 1.0e-7 ||
            (std::abs(point.x - best.x) <= 1.0e-7 && point.y < best.y)) {
            result = group;
            best = point;
        }
    }
    return result;
}

double datumValueFor(const AnalysisData& analysis,
                     const std::string& featureId,
                     const std::string& axis) {
    const auto found = std::find_if(
        analysis.datumDimensions.begin(),
        analysis.datumDimensions.end(),
        [&featureId, &axis](const AnalysisDatumDimension& dimension) {
            return dimension.featureId == featureId &&
                   dimension.axis == axis;
        });
    return found == analysis.datumDimensions.end()
               ? std::numeric_limits<double>::quiet_NaN()
               : found->value;
}

void addHoleLocationTarget(
    ViewResult& view,
    const AnalysisHoleGroup& target,
    const std::vector<std::string>& groupIds,
    const AnalysisData& analysis) {
    HoleLocationTarget location;
    location.groupIds = groupIds;
    location.center = projectPoint(target.center, view.definition);
    if (view.definition.id == "front") {
        location.horizontalDatumValue =
            datumValueFor(analysis, target.id, "X");
        location.verticalDatumValue =
            datumValueFor(analysis, target.id, "Z");
    }
    view.holeLocationTargets.push_back(std::move(location));
}

void layoutHoleCallouts(ViewResult& view) {
    if (view.holeCallouts.empty()) {
        return;
    }

    std::sort(
        view.holeCallouts.begin(),
        view.holeCallouts.end(),
        [](const HoleCallout& left, const HoleCallout& right) {
            if (std::abs(left.target.y - right.target.y) > 1.0e-7) {
                return left.target.y > right.target.y;
            }
            return left.target.x > right.target.x;
        });

    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double labelX = view.bounds.maxX + std::max(10.0, span * 0.08);
    const double minimumSeparation = std::max(11.0, span * 0.085);
    const double maximumY = view.bounds.maxY - span * 0.12;
    const double minimumY = view.bounds.minY + span * 0.12;
    double previousY = std::numeric_limits<double>::infinity();

    for (HoleCallout& callout : view.holeCallouts) {
        double labelY = std::clamp(callout.target.y, minimumY, maximumY);
        if (std::isfinite(previousY)) {
            labelY = std::min(labelY, previousY - minimumSeparation);
        }
        callout.label = {labelX, std::max(labelY, minimumY)};
        previousY = callout.label.y;
    }
}

void buildHoleCallouts(std::vector<ViewResult>& views,
                       const AnalysisData& analysis) {
    std::set<std::string> consumedGroupIds;

    for (const AnalysisHolePattern& pattern : analysis.holePatterns) {
        if (pattern.type != "RECTANGULAR_HOLE_ARRAY" ||
            pattern.groupIds.empty()) {
            continue;
        }
        std::vector<const AnalysisHoleGroup*> groups;
        for (const std::string& id : pattern.groupIds) {
            const AnalysisHoleGroup* group = findHoleGroup(analysis, id);
            if (group != nullptr) {
                groups.push_back(group);
                consumedGroupIds.insert(id);
            }
        }
        if (groups.empty()) {
            continue;
        }

        const int viewIndex = bestProjectionView(*groups.front(), views);
        if (viewIndex < 0) {
            continue;
        }
        ViewResult& view = views[static_cast<std::size_t>(viewIndex)];
        const AnalysisHoleGroup* target = selectLeaderTarget(groups, view.definition);
        HoleCallout callout;
        callout.groupIds = pattern.groupIds;
        callout.lines.push_back(
            std::to_string(pattern.groupIds.size()) + "X " +
            diameterText(pattern.diameters));
        callout.lines.push_back(
            "PITCH " + dimensionNumber(pattern.spacingA) + " &#215; " +
            dimensionNumber(pattern.spacingB));
        callout.target = projectPoint(target->center, view.definition);
        if (!pattern.diameters.empty()) {
            callout.radius = pattern.diameters.back() / 2.0;
        }
        view.holeCallouts.push_back(std::move(callout));

        // A rectangular array needs one datum location plus its two pitches.
        // Locating every member again would duplicate the pattern definition.
        const AnalysisHoleGroup* datumTarget =
            selectDatumTarget(groups, view.definition);
        addHoleLocationTarget(
            view, *datumTarget, pattern.groupIds, analysis);
    }

    struct GroupBucket {
        int viewIndex = -1;
        std::vector<const AnalysisHoleGroup*> groups;
    };
    std::map<std::string, GroupBucket> buckets;
    for (const AnalysisHoleGroup& group : analysis.holeGroups) {
        if (consumedGroupIds.count(group.id) != 0) {
            continue;
        }
        const int viewIndex = bestProjectionView(group, views);
        if (viewIndex < 0) {
            continue;
        }
        const std::string key =
            std::to_string(viewIndex) + '|' + diameterKey(group.diameters);
        GroupBucket& bucket = buckets[key];
        bucket.viewIndex = viewIndex;
        bucket.groups.push_back(&group);
    }

    for (const auto& item : buckets) {
        const GroupBucket& bucket = item.second;
        ViewResult& view = views[static_cast<std::size_t>(bucket.viewIndex)];
        const AnalysisHoleGroup* target =
            selectLeaderTarget(bucket.groups, view.definition);
        HoleCallout callout;
        for (const AnalysisHoleGroup* group : bucket.groups) {
            callout.groupIds.push_back(group->id);
        }
        const std::string countPrefix = bucket.groups.size() > 1
            ? std::to_string(bucket.groups.size()) + "X "
            : "";
        callout.lines.push_back(countPrefix + diameterText(target->diameters));
        callout.target = projectPoint(target->center, view.definition);
        if (!target->diameters.empty()) {
            callout.radius = target->diameters.back() / 2.0;
        }
        view.holeCallouts.push_back(std::move(callout));


        // Diameter callouts may be aggregated, but each non-pattern hole still
        // needs its own X/Y position to be geometrically defined.
        for (const AnalysisHoleGroup* group : bucket.groups) {
            addHoleLocationTarget(view, *group, {group->id}, analysis);
        }
    }

    for (ViewResult& view : views) {
        layoutHoleCallouts(view);
    }
}

ViewResult* findView(std::vector<ViewResult>& views, const std::string& id) {
    for (ViewResult& view : views) {
        if (view.definition.id == id) {
            return &view;
        }
    }
    return nullptr;
}

std::vector<CircleEvidence> mergedVisibleCircles(const ViewResult& view) {
    std::vector<CircleEvidence> circles;
    for (const CircleEvidence& evidence : view.circleEvidence) {
        if (!evidence.visible || evidence.radius <= kEpsilon) {
            continue;
        }
        CircleEvidence* match = nullptr;
        for (CircleEvidence& circle : circles) {
            const double centerTolerance = 0.01;
            const double radiusTolerance =
                std::max(0.005, circle.radius * 0.001);
            if (std::hypot(circle.center.x - evidence.center.x,
                           circle.center.y - evidence.center.y) <=
                    centerTolerance &&
                std::abs(circle.radius - evidence.radius) <=
                    radiusTolerance) {
                match = &circle;
                break;
            }
        }
        if (match == nullptr) {
            circles.push_back(evidence);
        } else {
            match->angularBins.insert(
                match->angularBins.end(),
                evidence.angularBins.begin(), evidence.angularBins.end());
        }
    }
    for (CircleEvidence& circle : circles) {
        std::sort(circle.angularBins.begin(), circle.angularBins.end());
        circle.angularBins.erase(
            std::unique(circle.angularBins.begin(), circle.angularBins.end()),
            circle.angularBins.end());
    }
    return circles;
}

Point2 rightmostEvidencePoint(const CircleEvidence& circle) {
    constexpr double fullCircle = 6.283185307179586;
    constexpr double angularBinCount = 720.0;
    Point2 result{circle.center.x + circle.radius, circle.center.y};
    double bestX = -std::numeric_limits<double>::infinity();
    for (const int bin : circle.angularBins) {
        const double angle =
            (static_cast<double>(bin) + 0.5) / angularBinCount * fullCircle;
        const Point2 point{
            circle.center.x + circle.radius * std::cos(angle),
            circle.center.y + circle.radius * std::sin(angle)};
        if (point.x > bestX) {
            bestX = point.x;
            result = point;
        }
    }
    return result;
}

double distance3(const Point3& left, const Point3& right) {
    return std::sqrt(
        (left.x - right.x) * (left.x - right.x) +
        (left.y - right.y) * (left.y - right.y) +
        (left.z - right.z) * (left.z - right.z));
}

double axisAlignment(const Point3& left, const Point3& right) {
    return std::abs(left.x * right.x + left.y * right.y + left.z * right.z);
}

bool nearestVisiblePoint(const ViewResult& view,
                         const Point2& reference,
                         double maximumDistance,
                         Point2& result) {
    double bestDistance = std::numeric_limits<double>::infinity();
    for (const Polyline& line : view.visible) {
        for (const Point2& point : line.points) {
            const double distance =
                std::hypot(point.x - reference.x, point.y - reference.y);
            if (distance < bestDistance) {
                bestDistance = distance;
                result = point;
            }
        }
    }
    return bestDistance <= maximumDistance;
}

void buildSpatialRadiusCallouts(ViewResult& view,
                                const AnalysisData& analysis) {
    constexpr int angularBinCount = 720;
    const std::vector<CircleEvidence> circles = mergedVisibleCircles(view);
    const double span = std::max(view.bounds.width(), view.bounds.height());
    std::vector<Point2> usedTargets;

    for (const AnalysisRadiusPair& pair : analysis.radiusPairs) {
        int matches = 0;
        Point2 bestTarget;
        bool hasTarget = false;
        std::vector<Point2> candidates;

        // Preferred path: pair exact 3D torus faces from analyzer schema 0.9+,
        // then snap their projected surface point to the nearest visible edge.
        for (const AnalysisTorusPatch& inner : analysis.torusPatches) {
            if (std::abs(inner.minorRadius - pair.innerRadius) > 0.02) {
                continue;
            }
            for (const AnalysisTorusPatch& outer : analysis.torusPatches) {
                if (std::abs(outer.minorRadius - pair.outerRadius) > 0.02 ||
                    distance3(inner.center, outer.center) > 0.05 ||
                    axisAlignment(inner.axis, outer.axis) < 0.999 ||
                    std::abs(inner.majorRadius - outer.majorRadius) > 0.05) {
                    continue;
                }
                const Point2 projected =
                    projectPoint(outer.surfacePoint, view.definition);
                Point2 target;
                const double snapDistance =
                    std::max(3.0, pair.outerRadius * 1.75);
                if (!nearestVisiblePoint(
                        view, projected, snapDistance, target)) {
                    continue;
                }
                ++matches;
                const bool duplicateCandidate = std::any_of(
                    candidates.begin(), candidates.end(),
                    [&target](const Point2& existing) {
                        return std::hypot(existing.x - target.x,
                                          existing.y - target.y) <= 0.5;
                    });
                if (!duplicateCandidate) {
                    candidates.push_back(target);
                }
                break;
            }
        }

        std::sort(
            candidates.begin(), candidates.end(),
            [](const Point2& left, const Point2& right) {
                if (std::abs(left.x - right.x) > 1.0e-7) {
                    return left.x > right.x;
                }
                return left.y < right.y;
            });
        const double targetSeparation = std::max(4.0, span * 0.04);
        for (const Point2& candidate : candidates) {
            const bool alreadyUsed = std::any_of(
                usedTargets.begin(), usedTargets.end(),
                [&candidate, targetSeparation](const Point2& used) {
                    return std::hypot(candidate.x - used.x,
                                      candidate.y - used.y) <
                           targetSeparation;
                });
            if (!alreadyUsed) {
                bestTarget = candidate;
                hasTarget = true;
                break;
            }
        }

        // Backward-compatible fallback for older analyzer JSON: use exact
        // circular HLR evidence when it happens to survive projection.
        if (!hasTarget && candidates.empty()) {
            for (const CircleEvidence& inner : circles) {
                if (std::abs(inner.radius - pair.innerRadius) > 0.02) {
                    continue;
                }
                const double innerCoverage =
                    static_cast<double>(inner.angularBins.size()) /
                    static_cast<double>(angularBinCount);
                if (innerCoverage < 0.04 || innerCoverage > 0.92) {
                    continue;
                }
                for (const CircleEvidence& outer : circles) {
                    if (std::abs(outer.radius - pair.outerRadius) > 0.02 ||
                        std::hypot(inner.center.x - outer.center.x,
                                   inner.center.y - outer.center.y) > 0.03) {
                        continue;
                    }
                    const double outerCoverage =
                        static_cast<double>(outer.angularBins.size()) /
                        static_cast<double>(angularBinCount);
                    if (outerCoverage < 0.04 || outerCoverage > 0.92) {
                        continue;
                    }
                    ++matches;
                    const Point2 target = rightmostEvidencePoint(outer);
                    const double targetSeparation =
                        std::max(4.0, span * 0.04);
                    const bool alreadyUsed = std::any_of(
                        usedTargets.begin(), usedTargets.end(),
                        [&target, targetSeparation](const Point2& used) {
                            return std::hypot(target.x - used.x,
                                              target.y - used.y) <
                                   targetSeparation;
                        });
                    if (alreadyUsed) {
                        break;
                    }
                    if (!hasTarget || target.x > bestTarget.x) {
                        hasTarget = true;
                        bestTarget = target;
                    }
                    break;
                }
            }
        }
        if (!hasTarget) {
            continue;
        }

        RadiusLeaderCallout callout;
        callout.innerRadius = pair.innerRadius;
        callout.outerRadius = pair.outerRadius;
        callout.totalPairs = pair.estimatedPairs;
        callout.visibleMatches = matches;
        callout.target = bestTarget;
        view.radiusCallouts.push_back(callout);
        usedTargets.push_back(bestTarget);
    }


    std::sort(
        view.radiusCallouts.begin(), view.radiusCallouts.end(),
        [](const RadiusLeaderCallout& left,
           const RadiusLeaderCallout& right) {
            if (std::abs(left.target.y - right.target.y) > 1.0e-7) {
                return left.target.y > right.target.y;
            }
            return left.target.x > right.target.x;
        });

    const double labelX = view.bounds.maxX + std::max(10.0, span * 0.08);
    const double firstY = view.bounds.minY + span * 0.20;
    const double separation = std::max(9.0, span * 0.09);
    for (std::size_t index = 0; index < view.radiusCallouts.size(); ++index) {
        view.radiusCallouts[index].label = {
            labelX,
            firstY - static_cast<double>(index) * separation};
    }
}

bool buildThicknessDimension(ViewResult& view,
                             const AnalysisData& analysis) {
    const AnalysisThicknessPair* bestPair = nullptr;
    Point2 bestFirst;
    Point2 bestSecond;
    double bestArea = -1.0;

    for (const AnalysisThicknessPair& pair : analysis.thicknessPairs) {
        if (std::abs(pair.distance - analysis.dominantThickness) > 0.002) {
            continue;
        }
        const Point3 midpoint{
            (pair.firstCentroid.x + pair.secondCentroid.x) / 2.0,
            (pair.firstCentroid.y + pair.secondCentroid.y) / 2.0,
            (pair.firstCentroid.z + pair.secondCentroid.z) / 2.0};
        const double midpointOffset =
            midpoint.x * pair.normal.x +
            midpoint.y * pair.normal.y +
            midpoint.z * pair.normal.z;
        const auto pointOnPlane = [&midpoint, midpointOffset, &pair](
                                      double planeOffset) {
            const double shift = planeOffset - midpointOffset;
            return Point3{
                midpoint.x + pair.normal.x * shift,
                midpoint.y + pair.normal.y * shift,
                midpoint.z + pair.normal.z * shift};
        };
        const Point2 first = projectPoint(
            pointOnPlane(pair.firstOffset), view.definition);
        const Point2 second = projectPoint(
            pointOnPlane(pair.secondOffset), view.definition);
        const double projectedDistance =
            std::hypot(second.x - first.x, second.y - first.y);
        if (std::abs(projectedDistance - pair.distance) > 0.03) {
            continue;
        }
        const double margin = std::max(5.0, analysis.dominantThickness * 2.0);
        const auto nearView = [&view, margin](const Point2& point) {
            return point.x >= view.bounds.minX - margin &&
                   point.x <= view.bounds.maxX + margin &&
                   point.y >= view.bounds.minY - margin &&
                   point.y <= view.bounds.maxY + margin;
        };
        if (!nearView(first) || !nearView(second)) {
            continue;
        }
        const double area = std::min(pair.firstArea, pair.secondArea);
        if (bestPair == nullptr || area > bestArea) {
            bestPair = &pair;
            bestFirst = first;
            bestSecond = second;
            bestArea = area;
        }
    }

    if (bestPair == nullptr) {
        return false;
    }

    const double measured =
        std::hypot(bestSecond.x - bestFirst.x, bestSecond.y - bestFirst.y);
    const Point2 direction{
        (bestSecond.x - bestFirst.x) / measured,
        (bestSecond.y - bestFirst.y) / measured};
    Point2 perpendicular{-direction.y, direction.x};
    const Point2 viewCenter{
        (view.bounds.minX + view.bounds.maxX) / 2.0,
        (view.bounds.minY + view.bounds.maxY) / 2.0};
    const Point2 pairCenter{
        (bestFirst.x + bestSecond.x) / 2.0,
        (bestFirst.y + bestSecond.y) / 2.0};
    const double outwardDot =
        perpendicular.x * (pairCenter.x - viewCenter.x) +
        perpendicular.y * (pairCenter.y - viewCenter.y);
    if (outwardDot < 0.0) {
        perpendicular.x = -perpendicular.x;
        perpendicular.y = -perpendicular.y;
    }
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double offset = std::max(6.0, span * 0.055);
    ThicknessDimension dimension;
    dimension.first = bestFirst;
    dimension.second = bestSecond;
    dimension.dimensionFirst = {
        bestFirst.x + perpendicular.x * offset,
        bestFirst.y + perpendicular.y * offset};
    dimension.dimensionSecond = {
        bestSecond.x + perpendicular.x * offset,
        bestSecond.y + perpendicular.y * offset};
    dimension.value = analysis.dominantThickness;
    view.thicknessDimensions.push_back(dimension);
    return true;
}

bool buildOpeningDimension(ViewResult& view,
                           const AnalysisData& analysis) {
    const AnalysisPlanarOpening* bestOpening = nullptr;
    Bounds2 bestBounds;
    double bestArea = 0.0;
    for (const AnalysisPlanarOpening& opening : analysis.planarOpenings) {
        if (std::abs(dot(opening.normal, view.definition.direction)) < 0.98) {
            continue;
        }
        Bounds2 projectedBounds;
        for (const double x : {opening.minimum.x, opening.maximum.x}) {
            for (const double y : {opening.minimum.y, opening.maximum.y}) {
                for (const double z : {opening.minimum.z, opening.maximum.z}) {
                    projectedBounds.add(projectPoint({x, y, z}, view.definition));
                }
            }
        }
        if (!projectedBounds.valid() || projectedBounds.width() < 5.0 ||
            projectedBounds.height() < 5.0) {
            continue;
        }
        const double area = projectedBounds.width() * projectedBounds.height();
        const double viewArea = view.bounds.width() * view.bounds.height();
        if (area >= viewArea * 0.80) {
            continue;
        }
        // Large 20-edge loops in this model are press-rib / inner contour
        // boundaries, not the central functional opening. Keep compact,
        // non-circular openings such as the 10-edge central cutout.
        if (opening.edges >= 18 && area > viewArea * 0.10) {
            continue;
        }
        if (projectedBounds.width() > view.bounds.width() * 0.65 &&
            projectedBounds.height() > view.bounds.height() * 0.65) {
            continue;
        }
        if (bestOpening == nullptr || area > bestArea) {
            bestOpening = &opening;
            bestBounds = projectedBounds;
            bestArea = area;
        }
    }
    if (bestOpening == nullptr) {
        return false;
    }
    view.openingDimensions.push_back({bestOpening->id, bestBounds});
    return true;
}

void buildEngineeringNotes(std::vector<ViewResult>& views,
                           const AnalysisData& analysis) {
    if (analysis.dominantThickness > kEpsilon &&
        analysis.thicknessAssessment != "NOT_IDENTIFIED" &&
        analysis.thicknessAssessment.rfind("EXCLUDED", 0) != 0) {
        ViewResult* right = findView(views, "right");
        if (right != nullptr) {
            const bool spatiallyLocated =
                buildThicknessDimension(*right, analysis);
            if (!spatiallyLocated) {
                const double span =
                    std::max(right->bounds.width(), right->bounds.height());
                EngineeringNote note;
                note.type = "sheet-thickness";
                note.lines.push_back(
                    "SHEET THICKNESS " +
                    dimensionNumber(analysis.dominantThickness) + " (REF)");
                note.label = {
                    (right->bounds.minX + right->bounds.maxX) / 2.0,
                    right->bounds.maxY + std::max(8.0, span * 0.10)};
                right->engineeringNotes.push_back(std::move(note));
            }
        }
    }

    if (!analysis.radiusPairs.empty()) {
        ViewResult* front = findView(views, "front");
        if (front != nullptr) {
            buildSpatialRadiusCallouts(*front, analysis);
            const double span =
                std::max(front->bounds.width(), front->bounds.height());
            EngineeringNote note;
            note.type = "bend-radii";
            note.lines.push_back("BEND RADII (REF)");
            for (const AnalysisRadiusPair& pair : analysis.radiusPairs) {
                const bool spatiallyLocated = std::any_of(
                    front->radiusCallouts.begin(),
                    front->radiusCallouts.end(),
                    [&pair](const RadiusLeaderCallout& callout) {
                        return std::abs(
                                   callout.innerRadius - pair.innerRadius) <=
                                   0.02 &&
                               std::abs(
                                   callout.outerRadius - pair.outerRadius) <=
                                   0.02;
                    });
                if (spatiallyLocated) {
                    continue;
                }
                std::string prefix;
                if (pair.estimatedPairs > 1) {
                    prefix = std::to_string(pair.estimatedPairs) + "X ";
                }
                note.lines.push_back(
                    prefix + "R" + dimensionNumber(pair.innerRadius) +
                    " / R" + dimensionNumber(pair.outerRadius));
            }
            note.label = {
                front->bounds.maxX + std::max(10.0, span * 0.08),
                front->radiusCallouts.empty()
                    ? front->bounds.minY + span * 0.18
                    : front->bounds.minY + span * 0.36};
            front->engineeringNotes.push_back(std::move(note));
        }
    }

    if (!analysis.studFeatures.empty()) {
        ViewResult* right = findView(views, "right");
        std::vector<const AnalysisStudFeature*> approvedStuds;
        for (const AnalysisStudFeature& item : analysis.studFeatures) {
            if (item.assessment == "HIGH_CONFIDENCE_STEPPED_STUD") {
                approvedStuds.push_back(&item);
            }
        }
        if (right != nullptr && !approvedStuds.empty()) {
            const AnalysisStudFeature& stud = *approvedStuds.front();
            const double span =
                std::max(right->bounds.width(), right->bounds.height());
            const double targetOffset = stud.overallLength * 0.55;
            const Point3 target3{
                stud.axisPoint.x - stud.axis.x * targetOffset,
                stud.axisPoint.y - stud.axis.y * targetOffset,
                stud.axisPoint.z - stud.axis.z * targetOffset};
            EngineeringNote note;
            note.type = "stud-specification";
            note.hasLeader = true;
            note.target = projectPoint(target3, right->definition);
            note.label = {
                right->bounds.maxX + std::max(10.0, span * 0.10),
                note.target.y + std::max(8.0, span * 0.08)};
            const auto segmentLength = [&stud](double diameter) {
                double result = 0.0;
                for (const AnalysisStudFeature::Segment& segment : stud.segments) {
                    if (std::abs(segment.diameter - diameter) <= 0.02) {
                        result = std::max(result, segment.length);
                    }
                }
                return result;
            };
            note.lines.push_back(
                std::to_string(approvedStuds.size()) +
                "X STUD");
            note.lines.push_back(
                "SHAFT &#216;" +
                dimensionNumber(stud.nominalShaftDiameter) + " X " +
                dimensionNumber(segmentLength(stud.nominalShaftDiameter)));
            note.lines.push_back(
                "HEAD &#216;" + dimensionNumber(stud.headDiameter) +
                " X " + dimensionNumber(segmentLength(stud.headDiameter)));
            note.lines.push_back(
                "TIP &#216;" + dimensionNumber(stud.tipDiameter) +
                " X " + dimensionNumber(segmentLength(stud.tipDiameter)));
            note.lines.push_back(
                "OVERALL " + dimensionNumber(stud.overallLength) +
                " REF");
            right->engineeringNotes.push_back(std::move(note));
        }
    }

    ViewResult* front = findView(views, "front");
    if (front != nullptr) {
        buildOpeningDimension(*front, analysis);
    }
}

struct AxisLocation {
    double coordinate = 0.0;
    double datumValue = std::numeric_limits<double>::quiet_NaN();
    Point2 target;
    std::vector<std::string> groupIds;
};

std::vector<AxisLocation> uniqueAxisLocations(
    const ViewResult& view,
    bool horizontal) {
    std::vector<AxisLocation> result;
    constexpr double mergeTolerance = 0.005;
    for (const HoleLocationTarget& target : view.holeLocationTargets) {
        const double coordinate = horizontal ? target.center.x : target.center.y;
        auto existing = std::find_if(
            result.begin(), result.end(),
            [coordinate](const AxisLocation& item) {
                return std::abs(item.coordinate - coordinate) <= mergeTolerance;
            });
        if (existing == result.end()) {
            const double datumValue = horizontal
                ? target.horizontalDatumValue
                : target.verticalDatumValue;
            result.push_back(
                {coordinate, datumValue, target.center, target.groupIds});
        } else {
            existing->groupIds.insert(
                existing->groupIds.end(),
                target.groupIds.begin(), target.groupIds.end());
        }
    }
    std::sort(
        result.begin(), result.end(),
        [](const AxisLocation& left, const AxisLocation& right) {
            return left.coordinate < right.coordinate;
        });
    return result;
}

int holeLocationDimensionCount(const ViewResult& view) {
    return static_cast<int>(uniqueAxisLocations(view, true).size() +
                            uniqueAxisLocations(view, false).size());
}

double locationLaneGap(double span) {
    return std::max(5.0, span * 0.045);
}

double horizontalLocationDepth(const ViewResult& view) {
    const std::size_t count = uniqueAxisLocations(view, true).size();
    if (count == 0) {
        return 0.0;
    }
    const double span = std::max(view.bounds.width(), view.bounds.height());
    return std::max(8.0, span * 0.10) +
           static_cast<double>(count - 1) * locationLaneGap(span) +
           std::max(4.0, span * 0.04);
}

double verticalLocationDepth(const ViewResult& view) {
    const std::size_t count = uniqueAxisLocations(view, false).size();
    if (count == 0) {
        return 0.0;
    }
    const double span = std::max(view.bounds.width(), view.bounds.height());
    return std::max(8.0, span * 0.10) +
           static_cast<double>(count - 1) * locationLaneGap(span) +
           std::max(4.0, span * 0.04);
}

void writePolylines(std::ofstream& output,
                    const std::vector<Polyline>& lines,
                    const char* cssClass) {
    output << "  <g class=\"" << cssClass << "\">\n";
    for (const Polyline& line : lines) {
        output << "    <polyline points=\"";
        for (std::size_t i = 0; i < line.points.size(); ++i) {
            if (i != 0) {
                output << ' ';
            }
            output << number(line.points[i].x) << ',' << number(-line.points[i].y);
        }
        output << "\"/>\n";
    }
    output << "  </g>\n";
}

void writeCenterMarks(std::ofstream& output,
                      const std::vector<CenterMark>& marks) {
    output << "  <g class=\"centerlines\">\n";
    for (const CenterMark& mark : marks) {
        const double extension = std::max(1.5, mark.radius * 0.20);
        const double halfLength = mark.radius + extension;
        output << "    <line x1=\"" << number(mark.center.x - halfLength)
               << "\" y1=\"" << number(-mark.center.y)
               << "\" x2=\"" << number(mark.center.x + halfLength)
               << "\" y2=\"" << number(-mark.center.y) << "\"/>\n";
        output << "    <line x1=\"" << number(mark.center.x)
               << "\" y1=\"" << number(-(mark.center.y - halfLength))
               << "\" x2=\"" << number(mark.center.x)
               << "\" y2=\"" << number(-(mark.center.y + halfLength))
               << "\"/>\n";
    }
    output << "  </g>\n";
}

void writeHoleCallouts(std::ofstream& output,
                       const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double elbowX = view.bounds.maxX + std::max(5.0, span * 0.04);
    const double fontSize = std::max(3.0, span * 0.024);

    for (const HoleCallout& callout : view.holeCallouts) {
        const double dx = elbowX - callout.target.x;
        const double dy = callout.label.y - callout.target.y;
        const double distance = std::hypot(dx, dy);
        const double startOffset = callout.radius + 0.8;
        const double startX = distance > kEpsilon
            ? callout.target.x + dx / distance * startOffset
            : callout.target.x;
        const double startY = distance > kEpsilon
            ? callout.target.y + dy / distance * startOffset
            : callout.target.y;

        output << "  <g class=\"hole-callout\" data-hole-groups=\"";
        for (std::size_t index = 0; index < callout.groupIds.size(); ++index) {
            if (index != 0) {
                output << ',';
            }
            output << callout.groupIds[index];
        }
        output << "\">\n";
        output << "    <polyline class=\"leader\" points=\""
               << number(startX) << ',' << number(-startY) << ' '
               << number(elbowX) << ',' << number(-callout.label.y) << ' '
               << number(callout.label.x - 1.2) << ','
               << number(-callout.label.y) << "\"/>\n";
        output << "    <circle class=\"leader-dot\" cx=\""
               << number(startX) << "\" cy=\"" << number(-startY)
               << "\" r=\"0.55\"/>\n";
        output << "    <text x=\"" << number(callout.label.x)
               << "\" y=\"" << number(-callout.label.y)
               << "\" font-size=\"" << number(fontSize) << "\">";
        for (std::size_t lineIndex = 0;
             lineIndex < callout.lines.size();
             ++lineIndex) {
            output << "<tspan x=\"" << number(callout.label.x) << "\"";
            if (lineIndex != 0) {
                output << " dy=\"" << number(fontSize * 1.18) << "\"";
            }
            output << '>' << callout.lines[lineIndex] << "</tspan>";
        }
        output << "</text>\n  </g>\n";
    }
}

void writeEngineeringNotes(std::ofstream& output,
                           const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double fontSize = std::max(3.0, span * 0.024);
    for (const EngineeringNote& note : view.engineeringNotes) {
        const bool centered = note.type == "sheet-thickness";
        output << "  <g class=\"engineering-note " << note.type << "\">\n";
        if (note.hasLeader) {
            output << "    <polyline class=\"leader\" points=\""
                   << number(note.target.x) << ',' << number(-note.target.y)
                   << ' ' << number(note.label.x) << ','
                   << number(-note.label.y) << "\"/>\n";
        }
        output << "    <text x=\"" << number(note.label.x)
               << "\" y=\"" << number(-note.label.y)
               << "\" font-size=\"" << number(fontSize)
               << "\" text-anchor=\"" << (centered ? "middle" : "start")
               << "\">";
        for (std::size_t index = 0; index < note.lines.size(); ++index) {
            output << "<tspan x=\"" << number(note.label.x) << "\"";
            if (index != 0) {
                output << " dy=\"" << number(fontSize * 1.20) << "\"";
            }
            output << '>' << note.lines[index] << "</tspan>";
        }
        output << "</text>\n  </g>\n";
    }
}

void writeRadiusCallouts(std::ofstream& output,
                         const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double elbowX = view.bounds.maxX + std::max(5.0, span * 0.04);
    const double fontSize = std::max(3.0, span * 0.024);
    for (const RadiusLeaderCallout& callout : view.radiusCallouts) {
        output << "  <g class=\"radius-callout\" data-visible-matches=\""
               << callout.visibleMatches << "\">\n";
        output << "    <polyline class=\"leader\" points=\""
               << number(callout.target.x) << ',' << number(-callout.target.y)
               << ' ' << number(elbowX) << ',' << number(-callout.label.y)
               << ' ' << number(callout.label.x - 1.2) << ','
               << number(-callout.label.y) << "\"/>\n";
        output << "    <circle class=\"leader-dot\" cx=\""
               << number(callout.target.x) << "\" cy=\""
               << number(-callout.target.y) << "\" r=\"0.55\"/>\n";
        const std::string prefix = callout.totalPairs > 1
            ? std::to_string(callout.totalPairs) + "X "
            : "";
        output << "    <text x=\"" << number(callout.label.x)
               << "\" y=\"" << number(-callout.label.y)
               << "\" font-size=\"" << number(fontSize) << "\">"
               << prefix << 'R' << dimensionNumber(callout.innerRadius)
               << " / R" << dimensionNumber(callout.outerRadius)
               << "</text>\n";
        output << "  </g>\n";
    }
}

void writeThicknessDimensions(std::ofstream& output,
                              const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double arrowLength = std::max(2.2, span * 0.018);
    const double arrowHalfWidth = std::max(0.7, span * 0.0055);
    const double overrun = std::max(1.2, span * 0.01);
    const double fontSize = std::max(3.0, span * 0.026);
    constexpr double radiansToDegrees = 57.29577951308232;

    for (const ThicknessDimension& dimension : view.thicknessDimensions) {
        const double length = std::hypot(
            dimension.dimensionSecond.x - dimension.dimensionFirst.x,
            dimension.dimensionSecond.y - dimension.dimensionFirst.y);
        if (length <= kEpsilon) {
            continue;
        }
        const Point2 direction{
            (dimension.dimensionSecond.x - dimension.dimensionFirst.x) / length,
            (dimension.dimensionSecond.y - dimension.dimensionFirst.y) / length};
        const Point2 perpendicular{-direction.y, direction.x};
        const auto shifted = [](const Point2& point,
                                const Point2& firstDirection,
                                double firstScale,
                                const Point2& secondDirection,
                                double secondScale) {
            return Point2{
                point.x + firstDirection.x * firstScale +
                    secondDirection.x * secondScale,
                point.y + firstDirection.y * firstScale +
                    secondDirection.y * secondScale};
        };
        const Point2 firstExtensionEnd = shifted(
            dimension.dimensionFirst, perpendicular, overrun,
            direction, 0.0);
        const Point2 secondExtensionEnd = shifted(
            dimension.dimensionSecond, perpendicular, overrun,
            direction, 0.0);
        const Point2 lineStart = shifted(
            dimension.dimensionFirst, direction, -(arrowLength + overrun),
            perpendicular, 0.0);
        const Point2 lineEnd = shifted(
            dimension.dimensionSecond, direction, arrowLength + overrun,
            perpendicular, 0.0);
        const Point2 firstBaseA = shifted(
            dimension.dimensionFirst, direction, -arrowLength,
            perpendicular, arrowHalfWidth);
        const Point2 firstBaseB = shifted(
            dimension.dimensionFirst, direction, -arrowLength,
            perpendicular, -arrowHalfWidth);
        const Point2 secondBaseA = shifted(
            dimension.dimensionSecond, direction, arrowLength,
            perpendicular, arrowHalfWidth);
        const Point2 secondBaseB = shifted(
            dimension.dimensionSecond, direction, arrowLength,
            perpendicular, -arrowHalfWidth);
        const Point2 label = shifted(
            {(dimension.dimensionFirst.x + dimension.dimensionSecond.x) / 2.0,
             (dimension.dimensionFirst.y + dimension.dimensionSecond.y) / 2.0},
            perpendicular, fontSize * 0.85,
            direction, 0.0);
        double angle = std::atan2(
            -(dimension.dimensionSecond.y - dimension.dimensionFirst.y),
            dimension.dimensionSecond.x - dimension.dimensionFirst.x) *
            radiansToDegrees;
        if (angle > 90.0) angle -= 180.0;
        if (angle < -90.0) angle += 180.0;

        output << "  <g class=\"dimensions sheet-thickness-dimension\">\n";
        output << "    <line class=\"extension\" x1=\""
               << number(dimension.first.x) << "\" y1=\""
               << number(-dimension.first.y) << "\" x2=\""
               << number(firstExtensionEnd.x) << "\" y2=\""
               << number(-firstExtensionEnd.y) << "\"/>\n";
        output << "    <line class=\"extension\" x1=\""
               << number(dimension.second.x) << "\" y1=\""
               << number(-dimension.second.y) << "\" x2=\""
               << number(secondExtensionEnd.x) << "\" y2=\""
               << number(-secondExtensionEnd.y) << "\"/>\n";
        output << "    <line class=\"dimension-line\" x1=\""
               << number(lineStart.x) << "\" y1=\"" << number(-lineStart.y)
               << "\" x2=\"" << number(lineEnd.x) << "\" y2=\""
               << number(-lineEnd.y) << "\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(dimension.dimensionFirst.x) << ' '
               << number(-dimension.dimensionFirst.y) << " L "
               << number(firstBaseA.x) << ' ' << number(-firstBaseA.y)
               << " L " << number(firstBaseB.x) << ' '
               << number(-firstBaseB.y) << " Z\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(dimension.dimensionSecond.x) << ' '
               << number(-dimension.dimensionSecond.y) << " L "
               << number(secondBaseA.x) << ' ' << number(-secondBaseA.y)
               << " L " << number(secondBaseB.x) << ' '
               << number(-secondBaseB.y) << " Z\"/>\n";
        output << "    <text x=\"" << number(label.x) << "\" y=\""
               << number(-label.y) << "\" font-size=\"" << number(fontSize)
               << "\" transform=\"rotate(" << number(angle) << ' '
               << number(label.x) << ' ' << number(-label.y) << ")\">"
               << dimensionNumber(dimension.value) << " REF</text>\n";
        output << "  </g>\n";
    }
}

void writeOpeningDimensions(std::ofstream& output,
                            const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double horizontalGap = std::max(14.0, span * 0.115);
    const double verticalGap = std::max(5.0, span * 0.045);
    const double overrun = std::max(1.2, span * 0.01);
    const double arrowLength = std::max(2.0, span * 0.016);
    const double arrowHalfWidth = std::max(0.65, span * 0.005);
    const double fontSize = std::max(3.0, span * 0.024);

    for (const OpeningDimension& dimension : view.openingDimensions) {
        const Bounds2& bounds = dimension.bounds;
        const double horizontalY = bounds.maxY + horizontalGap;
        const double verticalX = bounds.minX - verticalGap;
        output << "  <g class=\"dimensions opening-dimensions\" "
               << "data-opening-id=\"" << dimension.id << "\">\n";

        output << "    <line class=\"extension\" x1=\"" << number(bounds.minX)
               << "\" y1=\"" << number(-(bounds.maxY + 0.6))
               << "\" x2=\"" << number(bounds.minX) << "\" y2=\""
               << number(-(horizontalY + overrun)) << "\"/>\n";
        output << "    <line class=\"extension\" x1=\"" << number(bounds.maxX)
               << "\" y1=\"" << number(-(bounds.maxY + 0.6))
               << "\" x2=\"" << number(bounds.maxX) << "\" y2=\""
               << number(-(horizontalY + overrun)) << "\"/>\n";
        output << "    <line class=\"dimension-line\" x1=\""
               << number(bounds.minX) << "\" y1=\"" << number(-horizontalY)
               << "\" x2=\"" << number(bounds.maxX) << "\" y2=\""
               << number(-horizontalY) << "\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(bounds.minX) << ' ' << number(-horizontalY) << " L "
               << number(bounds.minX + arrowLength) << ' '
               << number(-(horizontalY + arrowHalfWidth)) << " L "
               << number(bounds.minX + arrowLength) << ' '
               << number(-(horizontalY - arrowHalfWidth)) << " Z\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(bounds.maxX) << ' ' << number(-horizontalY) << " L "
               << number(bounds.maxX - arrowLength) << ' '
               << number(-(horizontalY + arrowHalfWidth)) << " L "
               << number(bounds.maxX - arrowLength) << ' '
               << number(-(horizontalY - arrowHalfWidth)) << " Z\"/>\n";
        output << "    <text x=\"" << number((bounds.minX + bounds.maxX) / 2.0)
               << "\" y=\"" << number(-(horizontalY + fontSize * 0.55))
               << "\" font-size=\"" << number(fontSize) << "\">"
               << dimensionNumber(bounds.width()) << " REF</text>\n";

        output << "    <line class=\"extension\" x1=\""
               << number(bounds.minX - 0.6) << "\" y1=\""
               << number(-bounds.minY) << "\" x2=\""
               << number(verticalX - overrun) << "\" y2=\""
               << number(-bounds.minY) << "\"/>\n";
        output << "    <line class=\"extension\" x1=\""
               << number(bounds.minX - 0.6) << "\" y1=\""
               << number(-bounds.maxY) << "\" x2=\""
               << number(verticalX - overrun) << "\" y2=\""
               << number(-bounds.maxY) << "\"/>\n";
        output << "    <line class=\"dimension-line\" x1=\""
               << number(verticalX) << "\" y1=\"" << number(-bounds.minY)
               << "\" x2=\"" << number(verticalX) << "\" y2=\""
               << number(-bounds.maxY) << "\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(verticalX) << ' ' << number(-bounds.minY) << " L "
               << number(verticalX - arrowHalfWidth) << ' '
               << number(-(bounds.minY + arrowLength)) << " L "
               << number(verticalX + arrowHalfWidth) << ' '
               << number(-(bounds.minY + arrowLength)) << " Z\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(verticalX) << ' ' << number(-bounds.maxY) << " L "
               << number(verticalX - arrowHalfWidth) << ' '
               << number(-(bounds.maxY - arrowLength)) << " L "
               << number(verticalX + arrowHalfWidth) << ' '
               << number(-(bounds.maxY - arrowLength)) << " Z\"/>\n";
        const double verticalTextX = verticalX - fontSize * 0.55;
        const double verticalTextY = -(bounds.minY + bounds.maxY) / 2.0;
        output << "    <text x=\"" << number(verticalTextX) << "\" y=\""
               << number(verticalTextY) << "\" font-size=\""
               << number(fontSize) << "\" transform=\"rotate(-90 "
               << number(verticalTextX) << ' ' << number(verticalTextY)
               << ")\">" << dimensionNumber(bounds.height())
               << " REF</text>\n";
        output << "  </g>\n";
    }
}

void writeGroupIds(std::ofstream& output,
                   const std::vector<std::string>& groupIds) {
    for (std::size_t index = 0; index < groupIds.size(); ++index) {
        if (index != 0) {
            output << ',';
        }
        output << groupIds[index];
    }
}

void writeHorizontalHoleLocations(std::ofstream& output,
                                  const ViewResult& view) {
    const std::vector<AxisLocation> locations =
        uniqueAxisLocations(view, true);
    if (locations.empty()) {
        return;
    }

    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double baseOffset = std::max(8.0, span * 0.10);
    const double laneGap = locationLaneGap(span);
    const double geometryGap = std::max(0.8, span * 0.006);
    const double overrun = std::max(1.5, span * 0.012);
    const double arrowLength = std::max(2.0, span * 0.016);
    const double arrowHalfWidth = std::max(0.65, span * 0.005);
    const double fontSize = std::max(3.0, span * 0.024);

    for (std::size_t index = 0; index < locations.size(); ++index) {
        const AxisLocation& location = locations[index];
        const double measured = std::isfinite(location.datumValue)
            ? location.datumValue
            : location.coordinate - view.bounds.minX;
        if (measured <= 0.005) {
            continue;
        }
        const double y = view.bounds.minY - baseOffset -
                         static_cast<double>(index) * laneGap;
        output << "  <g class=\"dimensions hole-location horizontal\" "
               << "data-axis=\"X\" data-hole-groups=\"";
        writeGroupIds(output, location.groupIds);
        output << "\">\n";
        output << "    <line class=\"extension\" x1=\""
               << number(view.bounds.minX) << "\" y1=\""
               << number(-(view.bounds.minY - geometryGap)) << "\" x2=\""
               << number(view.bounds.minX) << "\" y2=\""
               << number(-(y - overrun)) << "\"/>\n";
        output << "    <line class=\"extension\" x1=\""
               << number(location.coordinate) << "\" y1=\""
               << number(-(location.target.y - geometryGap)) << "\" x2=\""
               << number(location.coordinate) << "\" y2=\""
               << number(-(y - overrun)) << "\"/>\n";
        output << "    <line class=\"dimension-line\" x1=\""
               << number(view.bounds.minX) << "\" y1=\"" << number(-y)
               << "\" x2=\"" << number(location.coordinate) << "\" y2=\""
               << number(-y) << "\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(view.bounds.minX) << ' ' << number(-y) << " L "
               << number(view.bounds.minX + arrowLength) << ' '
               << number(-(y + arrowHalfWidth)) << " L "
               << number(view.bounds.minX + arrowLength) << ' '
               << number(-(y - arrowHalfWidth)) << " Z\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(location.coordinate) << ' ' << number(-y) << " L "
               << number(location.coordinate - arrowLength) << ' '
               << number(-(y + arrowHalfWidth)) << " L "
               << number(location.coordinate - arrowLength) << ' '
               << number(-(y - arrowHalfWidth)) << " Z\"/>\n";
        output << "    <text x=\""
               << number((view.bounds.minX + location.coordinate) / 2.0)
               << "\" y=\"" << number(-(y + fontSize * 0.55))
               << "\" font-size=\"" << number(fontSize) << "\">"
               << dimensionNumber(measured) << "</text>\n";
        output << "  </g>\n";
    }
}

void writeVerticalHoleLocations(std::ofstream& output,
                                const ViewResult& view) {
    const std::vector<AxisLocation> locations =
        uniqueAxisLocations(view, false);
    if (locations.empty()) {
        return;
    }

    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double baseOffset = std::max(8.0, span * 0.10);
    const double laneGap = locationLaneGap(span);
    const double geometryGap = std::max(0.8, span * 0.006);
    const double overrun = std::max(1.5, span * 0.012);
    const double arrowLength = std::max(2.0, span * 0.016);
    const double arrowHalfWidth = std::max(0.65, span * 0.005);
    const double fontSize = std::max(3.0, span * 0.024);

    for (std::size_t index = 0; index < locations.size(); ++index) {
        const AxisLocation& location = locations[index];
        const double measured = std::isfinite(location.datumValue)
            ? location.datumValue
            : location.coordinate - view.bounds.minY;
        if (measured <= 0.005) {
            continue;
        }
        const double x = view.bounds.minX - baseOffset -
                         static_cast<double>(index) * laneGap;
        const double textX = x - fontSize * 0.55;
        const double textY = (view.bounds.minY + location.coordinate) / 2.0;
        output << "  <g class=\"dimensions hole-location vertical\" "
               << "data-axis=\"Y\" data-hole-groups=\"";
        writeGroupIds(output, location.groupIds);
        output << "\">\n";
        output << "    <line class=\"extension\" x1=\""
               << number(view.bounds.minX - geometryGap) << "\" y1=\""
               << number(-view.bounds.minY) << "\" x2=\""
               << number(x - overrun) << "\" y2=\""
               << number(-view.bounds.minY) << "\"/>\n";
        output << "    <line class=\"extension\" x1=\""
               << number(location.target.x - geometryGap) << "\" y1=\""
               << number(-location.coordinate) << "\" x2=\""
               << number(x - overrun) << "\" y2=\""
               << number(-location.coordinate) << "\"/>\n";
        output << "    <line class=\"dimension-line\" x1=\"" << number(x)
               << "\" y1=\"" << number(-view.bounds.minY) << "\" x2=\""
               << number(x) << "\" y2=\"" << number(-location.coordinate)
               << "\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(x) << ' ' << number(-view.bounds.minY) << " L "
               << number(x - arrowHalfWidth) << ' '
               << number(-(view.bounds.minY + arrowLength)) << " L "
               << number(x + arrowHalfWidth) << ' '
               << number(-(view.bounds.minY + arrowLength)) << " Z\"/>\n";
        output << "    <path class=\"dimension-arrow\" d=\"M "
               << number(x) << ' ' << number(-location.coordinate) << " L "
               << number(x - arrowHalfWidth) << ' '
               << number(-(location.coordinate - arrowLength)) << " L "
               << number(x + arrowHalfWidth) << ' '
               << number(-(location.coordinate - arrowLength)) << " Z\"/>\n";
        output << "    <text x=\"" << number(textX) << "\" y=\""
               << number(-textY) << "\" font-size=\"" << number(fontSize)
               << "\" transform=\"rotate(-90 " << number(textX) << ' '
               << number(-textY) << ")\">" << dimensionNumber(measured)
               << "</text>\n";
        output << "  </g>\n";
    }
}

void writeHoleLocationDimensions(std::ofstream& output,
                                 const ViewResult& view) {
    writeHorizontalHoleLocations(output, view);
    writeVerticalHoleLocations(output, view);
}

void writeHorizontalDimension(std::ofstream& output,
                              const Bounds2& bounds,
                              double span) {
    const double offset = std::max(8.0, span * 0.10);
    const double geometryGap = std::max(0.8, span * 0.006);
    const double overrun = std::max(1.5, span * 0.012);
    const double arrowLength = std::max(2.2, span * 0.018);
    const double arrowHalfWidth = std::max(0.7, span * 0.0055);
    const double fontSize = std::max(3.0, span * 0.026);
    const double y = bounds.maxY + offset;

    output << "  <g class=\"dimensions overall-width\">\n";
    output << "    <line class=\"extension\" x1=\"" << number(bounds.minX)
           << "\" y1=\"" << number(-(bounds.maxY + geometryGap))
           << "\" x2=\"" << number(bounds.minX)
           << "\" y2=\"" << number(-(y + overrun)) << "\"/>\n";
    output << "    <line class=\"extension\" x1=\"" << number(bounds.maxX)
           << "\" y1=\"" << number(-(bounds.maxY + geometryGap))
           << "\" x2=\"" << number(bounds.maxX)
           << "\" y2=\"" << number(-(y + overrun)) << "\"/>\n";
    output << "    <line class=\"dimension-line\" x1=\"" << number(bounds.minX)
           << "\" y1=\"" << number(-y)
           << "\" x2=\"" << number(bounds.maxX)
           << "\" y2=\"" << number(-y) << "\"/>\n";
    output << "    <path class=\"dimension-arrow\" d=\"M "
           << number(bounds.minX) << ' ' << number(-y) << " L "
           << number(bounds.minX + arrowLength) << ' '
           << number(-(y + arrowHalfWidth)) << " L "
           << number(bounds.minX + arrowLength) << ' '
           << number(-(y - arrowHalfWidth)) << " Z\"/>\n";
    output << "    <path class=\"dimension-arrow\" d=\"M "
           << number(bounds.maxX) << ' ' << number(-y) << " L "
           << number(bounds.maxX - arrowLength) << ' '
           << number(-(y + arrowHalfWidth)) << " L "
           << number(bounds.maxX - arrowLength) << ' '
           << number(-(y - arrowHalfWidth)) << " Z\"/>\n";
    output << "    <text x=\"" << number((bounds.minX + bounds.maxX) / 2.0)
           << "\" y=\"" << number(-(y + fontSize * 0.45))
           << "\" font-size=\"" << number(fontSize) << "\">"
           << dimensionNumber(bounds.width()) << "</text>\n";
    output << "  </g>\n";
}

void writeVerticalDimension(std::ofstream& output,
                            const Bounds2& bounds,
                            double span,
                            const char* cssName,
                            double requestedOffset = 0.0) {
    const double offset = std::max({8.0, span * 0.10, requestedOffset});
    const double geometryGap = std::max(0.8, span * 0.006);
    const double overrun = std::max(1.5, span * 0.012);
    const double arrowLength = std::max(2.2, span * 0.018);
    const double arrowHalfWidth = std::max(0.7, span * 0.0055);
    const double fontSize = std::max(3.0, span * 0.026);
    const double x = bounds.maxX + offset;
    const double centerY = (bounds.minY + bounds.maxY) / 2.0;
    const double textX = x + fontSize * 0.55;
    const double textY = -centerY;

    output << "  <g class=\"dimensions " << cssName << "\">\n";
    output << "    <line class=\"extension\" x1=\""
           << number(bounds.maxX + geometryGap) << "\" y1=\""
           << number(-bounds.minY) << "\" x2=\"" << number(x + overrun)
           << "\" y2=\"" << number(-bounds.minY) << "\"/>\n";
    output << "    <line class=\"extension\" x1=\""
           << number(bounds.maxX + geometryGap) << "\" y1=\""
           << number(-bounds.maxY) << "\" x2=\"" << number(x + overrun)
           << "\" y2=\"" << number(-bounds.maxY) << "\"/>\n";
    output << "    <line class=\"dimension-line\" x1=\"" << number(x)
           << "\" y1=\"" << number(-bounds.minY)
           << "\" x2=\"" << number(x)
           << "\" y2=\"" << number(-bounds.maxY) << "\"/>\n";
    output << "    <path class=\"dimension-arrow\" d=\"M "
           << number(x) << ' ' << number(-bounds.minY) << " L "
           << number(x - arrowHalfWidth) << ' '
           << number(-(bounds.minY + arrowLength)) << " L "
           << number(x + arrowHalfWidth) << ' '
           << number(-(bounds.minY + arrowLength)) << " Z\"/>\n";
    output << "    <path class=\"dimension-arrow\" d=\"M "
           << number(x) << ' ' << number(-bounds.maxY) << " L "
           << number(x - arrowHalfWidth) << ' '
           << number(-(bounds.maxY - arrowLength)) << " L "
           << number(x + arrowHalfWidth) << ' '
           << number(-(bounds.maxY - arrowLength)) << " Z\"/>\n";
    output << "    <text x=\"" << number(textX) << "\" y=\""
           << number(textY) << "\" font-size=\"" << number(fontSize)
           << "\" transform=\"rotate(-90 " << number(textX) << ' '
           << number(textY) << ")\">" << dimensionNumber(bounds.height())
           << "</text>\n";
    output << "  </g>\n";
}

int overallDimensionCount(const ViewResult& view) {
    if (view.definition.id == "front") {
        return 2;
    }
    if (view.definition.id == "top") {
        return 1;
    }
    return 0;
}

void writeOverallDimensions(std::ofstream& output, const ViewResult& view) {
    const double span = std::max(view.bounds.width(), view.bounds.height());
    const double calloutClearance = view.holeCallouts.empty()
        ? 0.0
        : span * 0.40;
    if (view.definition.id == "front") {
        writeHorizontalDimension(output, view.bounds, span);
        writeVerticalDimension(
            output,
            view.bounds,
            span,
            "overall-height",
            calloutClearance);
    } else if (view.definition.id == "top") {
        writeVerticalDimension(
            output,
            view.bounds,
            span,
            "overall-depth",
            calloutClearance);
    }
}

void writeDrawingStyle(std::ofstream& output) {
    output << "  <style>\n"
           << "    polyline { fill: none; vector-effect: non-scaling-stroke; "
           << "stroke-linecap: round; stroke-linejoin: round; }\n"
           << "    .hidden polyline { stroke: #94a3b8; stroke-width: 0.75; "
           << "stroke-dasharray: 6 4; opacity: 0.85; }\n"
           << "    .centerlines line { stroke: #475569; stroke-width: 0.70; "
           << "stroke-dasharray: 10 2 2 2; vector-effect: non-scaling-stroke; }\n"
           << "    .visible polyline { stroke: #111827; stroke-width: 1.30; }\n"
           << "    .dimensions line { stroke: #334155; stroke-width: 0.70; "
           << "vector-effect: non-scaling-stroke; }\n"
           << "    .dimension-arrow { fill: #334155; }\n"
           << "    .dimensions text { fill: #111827; font-family: Arial, "
           << "'Microsoft YaHei', sans-serif; text-anchor: middle; "
           << "dominant-baseline: central; paint-order: stroke; stroke: white; "
           << "stroke-width: 3px; stroke-linejoin: round; }\n"
           << "    .hole-location .dimension-line, "
           << ".hole-location .extension { stroke: #475569; }\n"
           << "    .hole-callout .leader { fill: none; stroke: #0f172a; "
           << "stroke-width: 0.75; vector-effect: non-scaling-stroke; }\n"
           << "    .hole-callout .leader-dot { fill: #0f172a; }\n"
           << "    .hole-callout text { fill: #0f172a; font-family: Arial, "
           << "'Microsoft YaHei', sans-serif; text-anchor: start; "
           << "dominant-baseline: central; paint-order: stroke; stroke: white; "
           << "stroke-width: 3px; stroke-linejoin: round; }\n"
           << "    .engineering-note text { fill: #0f172a; font-family: Arial, "
           << "'Microsoft YaHei', sans-serif; dominant-baseline: central; "
           << "paint-order: stroke; stroke: white; stroke-width: 3px; "
           << "stroke-linejoin: round; }\n"
           << "    .engineering-note .leader { fill: none; stroke: #0f172a; "
           << "stroke-width: 0.75; vector-effect: non-scaling-stroke; }\n"
           << "    .radius-callout .leader { fill: none; stroke: #0f172a; "
           << "stroke-width: 0.75; vector-effect: non-scaling-stroke; }\n"
           << "    .radius-callout .leader-dot { fill: #0f172a; }\n"
           << "    .radius-callout text { fill: #0f172a; font-family: Arial, "
           << "'Microsoft YaHei', sans-serif; text-anchor: start; "
           << "dominant-baseline: central; paint-order: stroke; stroke: white; "
           << "stroke-width: 3px; stroke-linejoin: round; }\n"
           << "  </style>\n";
}

void writeSvg(const fs::path& path, const ViewResult& view) {
    if (!view.bounds.valid()) {
        throw std::runtime_error("Projection contains no drawable edges");
    }

    const double modelWidth = std::max(view.bounds.width(), 1.0);
    const double modelHeight = std::max(view.bounds.height(), 1.0);
    const double span = std::max(modelWidth, modelHeight);
    const double leftMargin = std::max(
        span * 0.08, verticalLocationDepth(view));
    const double bottomMargin = std::max(
        span * 0.08, horizontalLocationDepth(view));
    double topMargin = view.definition.id == "front"
        ? span * 0.17
        : span * 0.08;
    if (view.definition.id == "right" && !view.engineeringNotes.empty()) {
        topMargin = std::max(topMargin, span * 0.16);
    }
    double rightMargin = span * 0.08;
    if (!view.holeCallouts.empty()) {
        rightMargin = span * 0.46;
    } else if (!view.engineeringNotes.empty()) {
        rightMargin = span * 0.20;
    } else if (view.definition.id == "front" || view.definition.id == "top") {
        rightMargin = span * 0.17;
    }
    const double viewX = view.bounds.minX - leftMargin;
    const double viewY = -view.bounds.maxY - topMargin;
    const double viewWidth = modelWidth + leftMargin + rightMargin;
    const double viewHeight = modelHeight + topMargin + bottomMargin;

    std::ofstream output(path);
    if (!output) {
        throw std::runtime_error("Cannot create SVG: " + path.string());
    }

    output << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    output << "<svg xmlns=\"http://www.w3.org/2000/svg\" "
           << "viewBox=\"" << number(viewX) << ' ' << number(viewY) << ' '
           << number(viewWidth) << ' ' << number(viewHeight) << "\" "
           << "width=\"1200\" height=\"900\">\n";
    output << "  <title>" << view.definition.title << "</title>\n";
    output << "  <rect x=\"" << number(viewX) << "\" y=\"" << number(viewY)
           << "\" width=\"" << number(viewWidth) << "\" height=\""
           << number(viewHeight) << "\" fill=\"white\"/>\n";
    writeDrawingStyle(output);
    writePolylines(output, view.hidden, "hidden");
    writeCenterMarks(output, view.centerMarks);
    writePolylines(output, view.visible, "visible");
    writeHoleCallouts(output, view);
    writeHoleLocationDimensions(output, view);
    writeOverallDimensions(output, view);
    writeThicknessDimensions(output, view);
    writeOpeningDimensions(output, view);
    writeEngineeringNotes(output, view);
    writeRadiusCallouts(output, view);
    output << "</svg>\n";
}

void includePlacedBounds(Bounds2& sheetBounds, const ViewPlacement& placement) {
    const Bounds2& bounds = placement.view->bounds;
    sheetBounds.add({bounds.minX + placement.offsetX,
                     bounds.minY + placement.offsetY});
    sheetBounds.add({bounds.maxX + placement.offsetX,
                     bounds.maxY + placement.offsetY});
}

void writePlacedView(std::ofstream& output, const ViewPlacement& placement) {
    output << "  <g id=\"view-" << placement.view->definition.id
           << "\" transform=\"translate(" << number(placement.offsetX) << ' '
           << number(-placement.offsetY) << ")\">\n";
    writePolylines(output, placement.view->hidden, "hidden");
    writeCenterMarks(output, placement.view->centerMarks);
    writePolylines(output, placement.view->visible, "visible");
    writeHoleCallouts(output, *placement.view);
    writeHoleLocationDimensions(output, *placement.view);
    writeOverallDimensions(output, *placement.view);
    writeThicknessDimensions(output, *placement.view);
    writeOpeningDimensions(output, *placement.view);
    writeEngineeringNotes(output, *placement.view);
    writeRadiusCallouts(output, *placement.view);
    output << "  </g>\n";
}

void writeFirstAngleSheet(const fs::path& path,
                          const ViewResult& front,
                          const ViewResult& top,
                          const ViewResult& right) {
    // First-angle layout: right view is left of the front view; top view is below.
    // Coordinates remain in millimetres, so all three views share one exact scale.
    const double largestSpan = std::max({
        front.bounds.width(), front.bounds.height(),
        top.bounds.width(), top.bounds.height(),
        right.bounds.width(), right.bounds.height()});
    const double gap = std::max(12.0, largestSpan * 0.12);
    const double horizontalGap = gap + verticalLocationDepth(front);
    const double verticalGap = gap + horizontalLocationDepth(front);

    const ViewPlacement frontPlacement{
        &front,
        right.bounds.width() + horizontalGap - front.bounds.minX,
        0.0};
    const ViewPlacement rightPlacement{
        &right,
        -right.bounds.minX,
        (front.bounds.minY + front.bounds.maxY -
         right.bounds.minY - right.bounds.maxY) / 2.0};
    const ViewPlacement topPlacement{
        &top,
        front.bounds.minX + frontPlacement.offsetX - top.bounds.minX,
        front.bounds.minY - verticalGap - top.bounds.maxY};

    const std::vector<ViewPlacement> placements = {
        rightPlacement, frontPlacement, topPlacement};
    Bounds2 sheetBounds;
    for (const ViewPlacement& placement : placements) {
        includePlacedBounds(sheetBounds, placement);
    }

    bool hasHoleCallouts = false;
    for (const ViewPlacement& placement : placements) {
        hasHoleCallouts = hasHoleCallouts || !placement.view->holeCallouts.empty();
    }
    const double sheetSpan = std::max(sheetBounds.width(), sheetBounds.height());
    const double leftMargin = sheetSpan * 0.06;
    const double rightMargin = sheetSpan * (hasHoleCallouts ? 0.31 : 0.10);
    const double topMargin = sheetSpan * 0.12;
    const double bottomMargin = sheetSpan * 0.06;
    const double viewX = sheetBounds.minX - leftMargin;
    const double viewY = -sheetBounds.maxY - topMargin;
    const double viewWidth = sheetBounds.width() + leftMargin + rightMargin;
    const double viewHeight = sheetBounds.height() + topMargin + bottomMargin;

    std::ofstream output(path);
    if (!output) {
        throw std::runtime_error("Cannot create three-view SVG: " + path.string());
    }

    output << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n";
    output << "<svg xmlns=\"http://www.w3.org/2000/svg\" "
           << "viewBox=\"" << number(viewX) << ' ' << number(viewY) << ' '
           << number(viewWidth) << ' ' << number(viewHeight) << "\" "
           << "width=\"1200\" height=\"900\">\n";
    output << "  <title>First-angle orthographic views</title>\n";
    output << "  <metadata>Right view left of front view; top view below front view; units mm</metadata>\n";
    output << "  <rect x=\"" << number(viewX) << "\" y=\"" << number(viewY)
           << "\" width=\"" << number(viewWidth) << "\" height=\""
           << number(viewHeight) << "\" fill=\"white\"/>\n";
    writeDrawingStyle(output);
    for (const ViewPlacement& placement : placements) {
        writePlacedView(output, placement);
    }
    output << "</svg>\n";
}

void writeManifest(const fs::path& path,
                   const fs::path& source,
                   const std::vector<ViewResult>& views,
                   const AnalysisData* analysis) {
    std::ofstream output(path);
    if (!output) {
        throw std::runtime_error("Cannot create manifest: " + path.string());
    }

    output << "{\n"
           << "  \"schema_version\": \"0.16.0\",\n"
           << "  \"source_file\": \"" << source.filename().string() << "\",\n"
           << "  \"units\": \"mm\",\n"
           << "  \"projection_method\": \"FIRST_ANGLE\",\n"
           << "  \"combined_view\": \"three_views.svg\",\n";
    if (analysis != nullptr) {
        output << "  \"analysis\": {\"file\": \""
               << analysis->sourcePath.filename().string()
               << "\", \"schema_version\": \"" << analysis->schemaVersion
               << "\", \"hole_axis_groups\": " << analysis->holeGroups.size()
               << ", \"hole_patterns\": " << analysis->holePatterns.size()
               << ", \"dominant_thickness\": "
               << number(analysis->dominantThickness)
               << ", \"thickness_assessment\": \""
               << analysis->thicknessAssessment
               << "\", \"radius_pairs\": " << analysis->radiusPairs.size()
               << ", \"torus_patches\": " << analysis->torusPatches.size()
               << ", \"thickness_pairs\": "
               << analysis->thicknessPairs.size()
               << ", \"planar_openings\": "
               << analysis->planarOpenings.size()
               << ", \"datum_dimensions\": "
               << analysis->datumDimensions.size()
               << ", \"stud_features\": "
               << analysis->studFeatures.size()
               << "},\n";
    } else {
        output << "  \"analysis\": null,\n";
    }
    output
           << "  \"views\": [\n";
    for (std::size_t i = 0; i < views.size(); ++i) {
        const ViewResult& view = views[i];
        output << "    {\"id\": \"" << view.definition.id
               << "\", \"file\": \"" << view.definition.id << ".svg\""
               << ", \"visible_edges\": " << view.visible.size()
               << ", \"hidden_edges\": " << view.hidden.size()
               << ", \"center_marks\": " << view.centerMarks.size()
               << ", \"overall_dimensions\": " << overallDimensionCount(view)
               << ", \"hole_location_dimensions\": "
               << holeLocationDimensionCount(view)
               << ", \"hole_location_reference\": {\"horizontal\": "
               << "\"LEFT_PROJECTION_BOUND\", \"vertical\": "
               << "\"BOTTOM_PROJECTION_BOUND\"}"
               << ", \"hole_callouts\": " << view.holeCallouts.size()
               << ", \"engineering_notes\": "
               << view.engineeringNotes.size()
               << ", \"radius_callouts\": " << view.radiusCallouts.size()
               << ", \"thickness_dimensions\": "
               << view.thicknessDimensions.size()
               << ", \"opening_dimensions\": "
               << view.openingDimensions.size()
               << ", \"width\": " << number(view.bounds.width())
               << ", \"height\": " << number(view.bounds.height()) << "}";
        if (i + 1 != views.size()) {
            output << ',';
        }
        output << '\n';
    }
    output << "  ]\n}\n";
}

} // namespace

int main(int argc, char** argv) {
    if (argc != 3 && argc != 4) {
        std::cerr << "Usage: occt-projector <input.step> <output-directory> "
                  << "[analyzer.json]\n";
        return 2;
    }

    try {
        const fs::path inputPath(argv[1]);
        const fs::path outputDirectory(argv[2]);
        fs::create_directories(outputDirectory);

        AnalysisData analysis;
        const AnalysisData* analysisPointer = nullptr;
        if (argc == 4) {
            analysis = readAnalysis(fs::path(argv[3]), inputPath);
            analysisPointer = &analysis;
            std::cout << "Analyzer data: groups=" << analysis.holeGroups.size()
                      << " patterns=" << analysis.holePatterns.size()
                      << " thickness="
                      << dimensionNumber(analysis.dominantThickness)
                      << " radius-pairs=" << analysis.radiusPairs.size()
                      << " torus-patches=" << analysis.torusPatches.size()
                      << " thickness-pairs=" << analysis.thicknessPairs.size()
                      << " planar-openings=" << analysis.planarOpenings.size()
                      << " datum-dimensions=" << analysis.datumDimensions.size()
                      << " stud-features=" << analysis.studFeatures.size()
                      << " schema=" << analysis.schemaVersion << std::endl;
        }

        const TopoDS_Shape shape = readStep(inputPath);
        const std::vector<ViewDefinition> definitions = {
            {"front", "Front view (-Y)", gp_Dir(0.0, -1.0, 0.0), gp_Dir(1.0, 0.0, 0.0)},
            {"top", "Top view (+Z)", gp_Dir(0.0, 0.0, 1.0), gp_Dir(1.0, 0.0, 0.0)},
            {"right", "Right view (-X)", gp_Dir(-1.0, 0.0, 0.0), gp_Dir(0.0, -1.0, 0.0)}
        };

        std::vector<ViewResult> views;
        views.reserve(definitions.size());
        for (const ViewDefinition& definition : definitions) {
            std::cout << "Projecting " << definition.id << " view..." << std::endl;
            views.push_back(project(shape, definition));
        }
        if (analysisPointer != nullptr) {
            buildHoleCallouts(views, *analysisPointer);
            buildEngineeringNotes(views, *analysisPointer);
        }
        for (const ViewResult& view : views) {
            writeSvg(
                outputDirectory / (view.definition.id + ".svg"),
                view);
        }
        writeFirstAngleSheet(
            outputDirectory / "three_views.svg",
            views[0],
            views[1],
            views[2]);
        writeManifest(
            outputDirectory / "views.json",
            inputPath,
            views,
            analysisPointer);

        std::cout << "Completed: " << inputPath.filename().string() << '\n';
        for (const ViewResult& view : views) {
            std::cout << "  " << view.definition.id
                      << ": visible=" << view.visible.size()
                      << " hidden=" << view.hidden.size()
                      << " centers=" << view.centerMarks.size()
                      << " dimensions=" << overallDimensionCount(view)
                      << " hole-locations="
                      << holeLocationDimensionCount(view)
                      << " callouts=" << view.holeCallouts.size()
                      << " notes=" << view.engineeringNotes.size()
                      << " radius-callouts=" << view.radiusCallouts.size()
                      << " thickness-dimensions="
                      << view.thicknessDimensions.size()
                      << " opening-dimensions="
                      << view.openingDimensions.size()
                      << " size=" << number(view.bounds.width())
                      << " x " << number(view.bounds.height()) << " mm\n";
        }
        std::cout << "  combined: three_views.svg (FIRST_ANGLE)\n";
        std::cout << "Output: " << outputDirectory.string() << std::endl;
        return 0;
    } catch (const Standard_Failure& failure) {
        std::cerr << "Open CASCADE error: "
                  << (failure.GetMessageString() ? failure.GetMessageString() : "unknown")
                  << std::endl;
        return 1;
    } catch (const std::exception& exception) {
        std::cerr << "Error: " << exception.what() << std::endl;
        return 1;
    }
}
