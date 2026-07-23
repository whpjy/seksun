#include <Bnd_Box.hxx>
#include <BRepAdaptor_Curve.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <BRepBndLib.hxx>
#include <BRepGProp.hxx>
#include <BRepTools.hxx>
#include <GeomAbs_SurfaceType.hxx>
#include <GProp_GProps.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <Interface_Static.hxx>
#include <STEPControl_Reader.hxx>
#include <TopAbs_Orientation.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp.hxx>
#include <TopExp_Explorer.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Edge.hxx>
#include <TopoDS_Face.hxx>
#include <TopoDS_Shape.hxx>
#include <TopoDS_Wire.hxx>
#include <gp_Cylinder.hxx>
#include <gp_Circ.hxx>
#include <gp_Dir.hxx>
#include <gp_Pnt.hxx>
#include <gp_Pln.hxx>
#include <gp_Torus.hxx>

#include <algorithm>
#include <cmath>
#include <filesystem>
#include <functional>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

struct Counts {
    int solids = 0;
    int shells = 0;
    int faces = 0;
    int edges = 0;
    int vertices = 0;
};

struct Bounds {
    double x_min = 0.0;
    double y_min = 0.0;
    double z_min = 0.0;
    double x_max = 0.0;
    double y_max = 0.0;
    double z_max = 0.0;
};

struct Point3 {
    double x = 0.0;
    double y = 0.0;
    double z = 0.0;
};

struct SurfaceCounts {
    int plane = 0;
    int cylinder = 0;
    int cone = 0;
    int sphere = 0;
    int torus = 0;
    int bezier = 0;
    int bspline = 0;
    int revolution = 0;
    int extrusion = 0;
    int offset = 0;
    int other = 0;
};

struct CylinderRadiusGroup {
    double radius = 0.0;
    int faces = 0;
};

struct TorusRadiusGroup {
    double minor_radius = 0.0;
    double major_radius_min = std::numeric_limits<double>::max();
    double major_radius_max = std::numeric_limits<double>::lowest();
    int faces = 0;
};

struct CylinderPatch {
    std::string body_id;
    std::string face_id;
    Point3 axis;
    Point3 axis_point;
    double diameter = 0.0;
    double minimum_station = 0.0;
    double maximum_station = 0.0;
    double area = 0.0;
    double coverage = 0.0;
    bool is_internal = false;
    int source_face_count = 1;
};

struct AxialFeature {
    std::string id;
    std::string type;
    std::string body_id;
    Point3 axis;
    Point3 center;
    Point3 start;
    Point3 end;
    double total_length = 0.0;
    std::vector<double> diameters;
    std::vector<CylinderPatch> segments;
};

struct StudFeature {
    std::string id;
    std::string type;
    Point3 axis;
    Point3 axis_point;
    std::vector<Point3> centers;
    std::vector<std::string> feature_ids;
    std::vector<std::string> body_ids;
    double overall_length = 0.0;
    double nominal_shaft_diameter = 0.0;
    double head_diameter = 0.0;
    double tip_diameter = 0.0;
    std::string assessment = "NOT_IDENTIFIED";
    std::vector<double> diameters;
    std::vector<CylinderPatch> segments;
};

struct ThreadFeature {
    std::string id;
    std::string type;
    std::string source_stud_id;
    Point3 axis;
    Point3 axis_point;
    double nominal_diameter = 0.0;
    double pitch = 0.0;
    double threaded_length = 0.0;
    std::string assessment;
};

struct ChamferFeature {
    std::string id;
    std::string type;
    std::string source_stud_id;
    Point3 axis;
    Point3 axis_point;
    double station = 0.0;
    double axial_length = 0.0;
    double radial_depth = 0.0;
    double angle_degrees = 0.0;
    double first_diameter = 0.0;
    double second_diameter = 0.0;
    std::string assessment;
};

struct HoleAxisGroup {
    std::string id;
    std::string type;
    Point3 axis;
    Point3 center;
    std::vector<std::string> feature_ids;
    std::vector<std::string> body_ids;
    std::vector<double> diameters;
};

struct HolePattern {
    std::string id;
    std::string type;
    Point3 center;
    Point3 direction_a;
    Point3 direction_b;
    double spacing_a = 0.0;
    double spacing_b = 0.0;
    double diagonal = 0.0;
    std::vector<double> diameters;
    std::vector<std::string> group_ids;
};

struct DatumDimension {
    std::string id;
    std::string feature_id;
    std::string axis;
    std::string datum;
    double coordinate = 0.0;
    double value = 0.0;
};

struct PlanarOpening {
    std::string id;
    Point3 normal;
    Bounds bounds;
    int source_wires = 1;
    int edges = 0;
};

struct PlaneGroup {
    Point3 normal;
    double offset = 0.0;
    Point3 centroid;
    double area = 0.0;
    int faces = 0;
};

struct PlanePair {
    int first_index = 0;
    int second_index = 0;
    PlaneGroup first;
    PlaneGroup second;
    double distance = 0.0;
    double area_difference_ratio = 0.0;
};

struct ThicknessCandidate {
    double thickness = 0.0;
    int evidence = 0;
    int x_count = 0;
    int y_count = 0;
    int z_count = 0;
    int oblique_count = 0;
};

struct ThicknessAnalysis {
    int plane_groups = 0;
    int major_planes = 0;
    int matched_pairs = 0;
    double dominant_thickness = 0.0;
    int dominant_evidence = 0;
    std::string assessment = "NOT_IDENTIFIED";
    std::vector<ThicknessCandidate> candidates;
    std::vector<PlanePair> dominant_pairs;
};

struct TorusRadiusPair {
    double inner_radius = 0.0;
    double outer_radius = 0.0;
    double difference = 0.0;
    int inner_faces = 0;
    int outer_faces = 0;
    int estimated_pairs = 0;
    bool ambiguous = false;
};

struct TorusPatch {
    std::string face_id;
    Point3 center;
    Point3 axis;
    Point3 surface_point;
    double major_radius = 0.0;
    double minor_radius = 0.0;
};

struct BendRadiusGroup {
    double radius = 0.0;
    std::string axis_category;
    int normal_direction = 0;
    int faces = 0;
};

struct BendRadiusPair {
    BendRadiusGroup inner;
    BendRadiusGroup outer;
    double difference = 0.0;
    int estimated_pairs = 0;
};

struct RadiusPairAnalysis {
    std::vector<TorusRadiusPair> torus_pairs;
    std::vector<TorusPatch> torus_patches;
    std::vector<BendRadiusGroup> bend_groups;
    std::vector<BendRadiusPair> bend_pairs;
};

struct BodySurfaceSummary {
    std::string id;
    int faces = 0;
    SurfaceCounts surface_types;
    std::vector<CylinderRadiusGroup> cylinder_radius_groups;
    std::vector<TorusRadiusGroup> torus_radius_groups;
    ThicknessAnalysis thickness_analysis;
    RadiusPairAnalysis radius_pair_analysis;
};

constexpr double RadiusGroupTolerance = 1.0e-6;
constexpr double Epsilon = 1.0e-9;
constexpr double AxisCosine = 0.999;
constexpr double PositionTolerance = 0.05;
constexpr double DiameterTolerance = 0.02;
constexpr double Pi = 3.14159265358979323846;

std::vector<DatumDimension> build_datum_dimensions(
    const std::vector<HoleAxisGroup>& groups,
    const Bounds& bounds) {
    std::vector<DatumDimension> dimensions;
    int index = 1;
    for (const HoleAxisGroup& group : groups) {
        if (std::abs(group.axis.y) < 0.98) continue;
        DatumDimension x;
        x.id = "DX" + std::to_string(index++);
        x.feature_id = group.id;
        x.axis = "X";
        x.datum = "LEFT_EDGE";
        x.coordinate = group.center.x;
        x.value = group.center.x - bounds.x_min;
        dimensions.push_back(x);

        DatumDimension z;
        z.id = "DZ" + std::to_string(index++);
        z.feature_id = group.id;
        z.axis = "Z";
        z.datum = "BOTTOM_EDGE";
        z.coordinate = group.center.z;
        z.value = group.center.z - bounds.z_min;
        dimensions.push_back(z);
    }
    return dimensions;
}

std::string json_escape(const std::string& value);
double interval_gap(
    double first_min,
    double first_max,
    double second_min,
    double second_max);

Point3 operator+(const Point3& left, const Point3& right) {
    return {left.x + right.x, left.y + right.y, left.z + right.z};
}

Point3 operator-(const Point3& left, const Point3& right) {
    return {left.x - right.x, left.y - right.y, left.z - right.z};
}

Point3 operator*(const Point3& value, double scale) {
    return {value.x * scale, value.y * scale, value.z * scale};
}

double dot(const Point3& left, const Point3& right) {
    return left.x * right.x + left.y * right.y + left.z * right.z;
}

double length(const Point3& value) {
    return std::sqrt(dot(value, value));
}

Point3 canonical(const gp_Dir& direction) {
    Point3 value{direction.X(), direction.Y(), direction.Z()};
    const double dominant =
        std::abs(value.x) >= std::abs(value.y) &&
        std::abs(value.x) >= std::abs(value.z)
            ? value.x
            : std::abs(value.y) >= std::abs(value.z) ? value.y : value.z;
    return dominant < 0.0 ? value * -1.0 : value;
}

Point3 canonical_unit(const Point3& input) {
    const double magnitude = length(input);
    if (magnitude <= Epsilon) return {};
    Point3 value = input * (1.0 / magnitude);
    const double dominant =
        std::abs(value.x) >= std::abs(value.y) &&
        std::abs(value.x) >= std::abs(value.z)
            ? value.x
            : std::abs(value.y) >= std::abs(value.z) ? value.y : value.z;
    return dominant < 0.0 ? value * -1.0 : value;
}

int dominant_axis(const Point3& direction) {
    if (std::abs(direction.x) >= std::abs(direction.y) &&
        std::abs(direction.x) >= std::abs(direction.z)) return 0;
    return std::abs(direction.y) >= std::abs(direction.z) ? 1 : 2;
}

int count_subshapes(const TopoDS_Shape& shape, TopAbs_ShapeEnum kind) {
    TopTools_IndexedMapOfShape unique_shapes;
    TopExp::MapShapes(shape, kind, unique_shapes);
    return unique_shapes.Extent();
}

SurfaceCounts classify_surfaces(const TopoDS_Shape& shape) {
    SurfaceCounts counts;
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);

    for (Standard_Integer index = 1; index <= faces.Extent(); ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        const BRepAdaptor_Surface surface(face, Standard_True);

        switch (surface.GetType()) {
            case GeomAbs_Plane: ++counts.plane; break;
            case GeomAbs_Cylinder: ++counts.cylinder; break;
            case GeomAbs_Cone: ++counts.cone; break;
            case GeomAbs_Sphere: ++counts.sphere; break;
            case GeomAbs_Torus: ++counts.torus; break;
            case GeomAbs_BezierSurface: ++counts.bezier; break;
            case GeomAbs_BSplineSurface: ++counts.bspline; break;
            case GeomAbs_SurfaceOfRevolution: ++counts.revolution; break;
            case GeomAbs_SurfaceOfExtrusion: ++counts.extrusion; break;
            case GeomAbs_OffsetSurface: ++counts.offset; break;
            case GeomAbs_OtherSurface: ++counts.other; break;
        }
    }
    return counts;
}

long long radius_key(double radius) {
    return std::llround(radius / RadiusGroupTolerance);
}

void analyze_radius_groups(
    const TopoDS_Shape& shape,
    std::vector<CylinderRadiusGroup>& cylinder_groups,
    std::vector<TorusRadiusGroup>& torus_groups) {
    std::map<long long, CylinderRadiusGroup> cylinders;
    std::map<long long, TorusRadiusGroup> tori;

    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    for (Standard_Integer index = 1; index <= faces.Extent(); ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        const BRepAdaptor_Surface surface(face, Standard_True);

        if (surface.GetType() == GeomAbs_Cylinder) {
            const double radius = surface.Cylinder().Radius();
            CylinderRadiusGroup& group = cylinders[radius_key(radius)];
            group.radius = radius;
            ++group.faces;
        } else if (surface.GetType() == GeomAbs_Torus) {
            const gp_Torus torus = surface.Torus();
            const double minor_radius = torus.MinorRadius();
            const double major_radius = torus.MajorRadius();
            TorusRadiusGroup& group = tori[radius_key(minor_radius)];
            group.minor_radius = minor_radius;
            group.major_radius_min = std::min(group.major_radius_min, major_radius);
            group.major_radius_max = std::max(group.major_radius_max, major_radius);
            ++group.faces;
        }
    }

    for (const auto& item : cylinders) {
        cylinder_groups.push_back(item.second);
    }
    for (const auto& item : tori) {
        torus_groups.push_back(item.second);
    }
}

bool is_circular_wire(const TopoDS_Wire& wire) {
    bool has_circle = false;
    gp_Pnt reference_center;
    double reference_radius = 0.0;
    for (TopExp_Explorer explorer(wire, TopAbs_EDGE);
         explorer.More(); explorer.Next()) {
        const TopoDS_Edge edge = TopoDS::Edge(explorer.Current());
        const BRepAdaptor_Curve curve(edge);
        if (curve.GetType() != GeomAbs_Circle) {
            return false;
        }
        const gp_Circ circle = curve.Circle();
        if (!has_circle) {
            has_circle = true;
            reference_center = circle.Location();
            reference_radius = circle.Radius();
        } else if (reference_center.Distance(circle.Location()) > 0.02 ||
                   std::abs(reference_radius - circle.Radius()) > 0.02) {
            return false;
        }
    }
    return has_circle;
}

bool same_opening(const PlanarOpening& left, const PlanarOpening& right) {
    const double tolerance = 0.05;
    return std::abs(dot(left.normal, right.normal)) >= 0.99999 &&
           std::abs(left.bounds.x_min - right.bounds.x_min) <= tolerance &&
           std::abs(left.bounds.y_min - right.bounds.y_min) <= tolerance &&
           std::abs(left.bounds.z_min - right.bounds.z_min) <= tolerance &&
           std::abs(left.bounds.x_max - right.bounds.x_max) <= tolerance &&
           std::abs(left.bounds.y_max - right.bounds.y_max) <= tolerance &&
           std::abs(left.bounds.z_max - right.bounds.z_max) <= tolerance;
}

std::vector<PlanarOpening> collect_planar_openings(
    const TopoDS_Shape& shape) {
    std::vector<PlanarOpening> openings;
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    for (Standard_Integer face_index = 1;
         face_index <= faces.Extent(); ++face_index) {
        const TopoDS_Face face = TopoDS::Face(faces(face_index));
        const BRepAdaptor_Surface surface(face, Standard_True);
        if (surface.GetType() != GeomAbs_Plane) {
            continue;
        }
        const Point3 normal = canonical(surface.Plane().Axis().Direction());
        const TopoDS_Wire outer = BRepTools::OuterWire(face);
        for (TopExp_Explorer explorer(face, TopAbs_WIRE);
             explorer.More(); explorer.Next()) {
            const TopoDS_Wire wire = TopoDS::Wire(explorer.Current());
            if ((!outer.IsNull() && wire.IsSame(outer)) ||
                is_circular_wire(wire)) {
                continue;
            }

            int edge_count = 0;
            for (TopExp_Explorer edge_explorer(wire, TopAbs_EDGE);
                 edge_explorer.More(); edge_explorer.Next()) {
                ++edge_count;
            }
            if (edge_count < 2) {
                continue;
            }

            Bnd_Box box;
            BRepBndLib::AddOptimal(
                wire, box, Standard_False, Standard_False);
            if (box.IsVoid()) {
                continue;
            }
            PlanarOpening opening;
            opening.normal = normal;
            opening.edges = edge_count;
            box.Get(
                opening.bounds.x_min,
                opening.bounds.y_min,
                opening.bounds.z_min,
                opening.bounds.x_max,
                opening.bounds.y_max,
                opening.bounds.z_max);

            PlanarOpening* duplicate = nullptr;
            for (PlanarOpening& existing : openings) {
                if (same_opening(existing, opening)) {
                    duplicate = &existing;
                    break;
                }
            }
            if (duplicate == nullptr) {
                openings.push_back(opening);
            } else {
                ++duplicate->source_wires;
                duplicate->edges = std::max(duplicate->edges, edge_count);
            }
        }
    }

    std::sort(
        openings.begin(), openings.end(),
        [](const PlanarOpening& left, const PlanarOpening& right) {
            const std::vector<double> left_spans = {
                left.bounds.x_max - left.bounds.x_min,
                left.bounds.y_max - left.bounds.y_min,
                left.bounds.z_max - left.bounds.z_min};
            const std::vector<double> right_spans = {
                right.bounds.x_max - right.bounds.x_min,
                right.bounds.y_max - right.bounds.y_min,
                right.bounds.z_max - right.bounds.z_min};
            std::vector<double> left_sorted = left_spans;
            std::vector<double> right_sorted = right_spans;
            std::sort(left_sorted.begin(), left_sorted.end(), std::greater<double>());
            std::sort(right_sorted.begin(), right_sorted.end(), std::greater<double>());
            return left_sorted[0] * left_sorted[1] >
                   right_sorted[0] * right_sorted[1];
        });
    for (std::size_t index = 0; index < openings.size(); ++index) {
        std::ostringstream id;
        id << "OP" << std::setw(3) << std::setfill('0') << index + 1;
        openings[index].id = id.str();
    }
    return openings;
}

std::string axis_category(const Point3& normal) {
    if (std::abs(normal.x) >= 0.999) return "X";
    if (std::abs(normal.y) >= 0.999) return "Y";
    if (std::abs(normal.z) >= 0.999) return "Z";
    return "OBLIQUE";
}

std::vector<PlaneGroup> collect_plane_groups(const TopoDS_Shape& shape) {
    std::vector<PlaneGroup> groups;
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);

    for (Standard_Integer index = 1; index <= faces.Extent(); ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        const BRepAdaptor_Surface surface(face, Standard_True);
        if (surface.GetType() != GeomAbs_Plane) continue;

        const gp_Pln plane = surface.Plane();
        const Point3 normal = canonical(plane.Axis().Direction());
        const gp_Pnt location = plane.Location();
        const Point3 point{location.X(), location.Y(), location.Z()};

        GProp_GProps properties;
        BRepGProp::SurfaceProperties(face, properties);
        const double area = properties.Mass();
        const gp_Pnt center = properties.CentreOfMass();
        const Point3 centroid{center.X(), center.Y(), center.Z()};
        const double offset = dot(normal, point);

        PlaneGroup* target = nullptr;
        for (PlaneGroup& group : groups) {
            if (dot(group.normal, normal) >= 0.99999 &&
                std::abs(group.offset - offset) <= 0.02) {
                target = &group;
                break;
            }
        }
        if (target == nullptr) {
            groups.push_back(PlaneGroup{normal, offset, {}, 0.0, 0});
            target = &groups.back();
        }
        const double combinedArea = target->area + area;
        if (combinedArea > Epsilon) {
            target->centroid =
                (target->centroid * target->area + centroid * area) *
                (1.0 / combinedArea);
        }
        target->area += area;
        ++target->faces;
    }
    return groups;
}

std::vector<PlaneGroup> get_major_planes(
    const std::vector<PlaneGroup>& groups) {
    std::vector<PlaneGroup> result;
    for (const PlaneGroup& group : groups) {
        if (group.area >= 20.0) result.push_back(group);
    }
    std::sort(
        result.begin(),
        result.end(),
        [](const PlaneGroup& left, const PlaneGroup& right) {
            const std::string left_category = axis_category(left.normal);
            const std::string right_category = axis_category(right.normal);
            if (left_category != right_category) {
                return left_category < right_category;
            }
            return left.offset < right.offset;
        });
    return result;
}

std::vector<PlanePair> find_matching_plane_pairs(
    const std::vector<PlaneGroup>& planes) {
    std::vector<PlanePair> candidates;
    for (std::size_t first_index = 0;
         first_index < planes.size();
         ++first_index) {
        for (std::size_t second_index = first_index + 1;
             second_index < planes.size();
             ++second_index) {
            const PlaneGroup& first = planes[first_index];
            const PlaneGroup& second = planes[second_index];
            if (std::abs(dot(first.normal, second.normal)) < 0.99999 ||
                axis_category(first.normal) != axis_category(second.normal)) {
                continue;
            }

            const double distance = std::abs(second.offset - first.offset);
            if (distance <= 0.001 || distance > 10.0) continue;

            const double maximum_area = std::max(first.area, second.area);
            const double area_difference_ratio = maximum_area <= 0.0
                ? 1.0
                : std::abs(first.area - second.area) / maximum_area;
            if (area_difference_ratio > 0.01) continue;

            candidates.push_back(PlanePair{
                static_cast<int>(first_index),
                static_cast<int>(second_index),
                first,
                second,
                distance,
                area_difference_ratio});
        }
    }

    std::sort(
        candidates.begin(),
        candidates.end(),
        [](const PlanePair& left, const PlanePair& right) {
            if (std::abs(left.distance - right.distance) > Epsilon) {
                return left.distance < right.distance;
            }
            return left.first_index < right.first_index;
        });

    std::vector<bool> used(planes.size(), false);
    std::vector<PlanePair> result;
    for (const PlanePair& candidate : candidates) {
        if (used[candidate.first_index] || used[candidate.second_index]) continue;
        used[candidate.first_index] = true;
        used[candidate.second_index] = true;
        result.push_back(candidate);
    }
    return result;
}

double minimum_bounding_size(const TopoDS_Shape& shape) {
    Bnd_Box box;
    BRepBndLib::AddOptimal(shape, box, Standard_False, Standard_False);
    if (box.IsVoid()) return 0.0;
    double x_min, y_min, z_min, x_max, y_max, z_max;
    box.Get(x_min, y_min, z_min, x_max, y_max, z_max);
    return std::min({x_max - x_min, y_max - y_min, z_max - z_min});
}

ThicknessAnalysis analyze_thickness(const TopoDS_Shape& shape) {
    ThicknessAnalysis analysis;
    const std::vector<PlaneGroup> groups = collect_plane_groups(shape);
    const std::vector<PlaneGroup> major_planes = get_major_planes(groups);
    const std::vector<PlanePair> pairs =
        find_matching_plane_pairs(major_planes);
    analysis.plane_groups = static_cast<int>(groups.size());
    analysis.major_planes = static_cast<int>(major_planes.size());
    analysis.matched_pairs = static_cast<int>(pairs.size());

    std::map<long long, ThicknessCandidate> summaries;
    for (const PlanePair& pair : pairs) {
        const double rounded = std::round(pair.distance * 1000.0) / 1000.0;
        const long long key = std::llround(rounded * 1000.0);
        ThicknessCandidate& candidate = summaries[key];
        candidate.thickness = rounded;
        ++candidate.evidence;
        const std::string category = axis_category(pair.first.normal);
        if (category == "X") ++candidate.x_count;
        else if (category == "Y") ++candidate.y_count;
        else if (category == "Z") ++candidate.z_count;
        else ++candidate.oblique_count;
    }
    for (const auto& item : summaries) {
        analysis.candidates.push_back(item.second);
    }

    for (const ThicknessCandidate& candidate : analysis.candidates) {
        if (candidate.evidence > analysis.dominant_evidence) {
            analysis.dominant_evidence = candidate.evidence;
            analysis.dominant_thickness = candidate.thickness;
        }
    }

    for (const PlanePair& pair : pairs) {
        if (std::abs(pair.distance - analysis.dominant_thickness) <= 0.002) {
            analysis.dominant_pairs.push_back(pair);
        }
    }

    if (analysis.dominant_thickness <= Epsilon) {
        analysis.assessment = "NOT_IDENTIFIED";
    } else if (analysis.dominant_thickness >
               minimum_bounding_size(shape) + 0.02) {
        analysis.assessment = "EXCLUDED_GREATER_THAN_MIN_BOUND";
    } else if (analysis.dominant_evidence >= 3) {
        analysis.assessment = "HIGH_CONFIDENCE_MAIN_THICKNESS";
    } else {
        analysis.assessment = "LOCAL_THICKNESS_CANDIDATE";
    }
    return analysis;
}

std::vector<TorusRadiusPair> find_torus_radius_pairs(
    const std::vector<TorusRadiusGroup>& groups,
    double dominant_thickness) {
    std::vector<TorusRadiusPair> pairs;
    if (dominant_thickness <= Epsilon) return pairs;

    std::vector<int> participation(groups.size(), 0);
    for (std::size_t inner_index = 0;
         inner_index < groups.size();
         ++inner_index) {
        for (std::size_t outer_index = 0;
             outer_index < groups.size();
             ++outer_index) {
            if (groups[outer_index].minor_radius <=
                groups[inner_index].minor_radius) {
                continue;
            }
            const double difference = groups[outer_index].minor_radius -
                groups[inner_index].minor_radius;
            if (std::abs(difference - dominant_thickness) > 0.002) continue;

            pairs.push_back(TorusRadiusPair{
                groups[inner_index].minor_radius,
                groups[outer_index].minor_radius,
                difference,
                groups[inner_index].faces,
                groups[outer_index].faces,
                std::min(
                    groups[inner_index].faces,
                    groups[outer_index].faces),
                false});
            ++participation[inner_index];
            ++participation[outer_index];
        }
    }

    for (TorusRadiusPair& pair : pairs) {
        for (std::size_t index = 0; index < groups.size(); ++index) {
            if ((std::abs(groups[index].minor_radius - pair.inner_radius) <=
                    RadiusGroupTolerance ||
                 std::abs(groups[index].minor_radius - pair.outer_radius) <=
                    RadiusGroupTolerance) &&
                participation[index] > 1) {
                pair.ambiguous = true;
            }
        }
    }
    std::sort(
        pairs.begin(),
        pairs.end(),
        [](const TorusRadiusPair& left, const TorusRadiusPair& right) {
            return left.inner_radius < right.inner_radius;
        });
    return pairs;
}

std::vector<TorusPatch> collect_torus_patches(const TopoDS_Shape& shape) {
    std::vector<TorusPatch> patches;
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);
    for (Standard_Integer index = 1; index <= faces.Extent(); ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        const BRepAdaptor_Surface surface(face, Standard_True);
        if (surface.GetType() != GeomAbs_Torus) {
            continue;
        }

        const gp_Torus torus = surface.Torus();
        const gp_Pnt center = torus.Location();
        const gp_Dir axis = torus.Axis().Direction();
        GProp_GProps properties;
        BRepGProp::SurfaceProperties(face, properties);
        const gp_Pnt surfacePoint = properties.CentreOfMass();

        std::ostringstream faceId;
        faceId << 'F' << std::setw(4) << std::setfill('0') << index;
        patches.push_back(TorusPatch{
            faceId.str(),
            {center.X(), center.Y(), center.Z()},
            canonical(axis),
            {surfacePoint.X(), surfacePoint.Y(), surfacePoint.Z()},
            torus.MajorRadius(),
            torus.MinorRadius()});
    }
    return patches;
}

std::vector<BendRadiusGroup> collect_bend_radius_groups(
    const TopoDS_Shape& shape) {
    std::vector<BendRadiusGroup> groups;
    TopTools_IndexedMapOfShape faces;
    TopExp::MapShapes(shape, TopAbs_FACE, faces);

    for (Standard_Integer index = 1; index <= faces.Extent(); ++index) {
        const TopoDS_Face face = TopoDS::Face(faces(index));
        const BRepAdaptor_Surface surface(face, Standard_True);
        if (surface.GetType() != GeomAbs_Cylinder) continue;

        const gp_Cylinder cylinder = surface.Cylinder();
        const Point3 axis = canonical(cylinder.Axis().Direction());
        const std::string category = axis_category(axis);
        if (category == "Y") continue;

        const double radius = cylinder.Radius();
        const int normal_direction =
            face.Orientation() == TopAbs_REVERSED ? -1 : 1;
        BendRadiusGroup* target = nullptr;
        for (BendRadiusGroup& group : groups) {
            if (std::abs(group.radius - radius) <= 0.001 &&
                group.axis_category == category &&
                group.normal_direction == normal_direction) {
                target = &group;
                break;
            }
        }
        if (target == nullptr) {
            groups.push_back(BendRadiusGroup{
                radius, category, normal_direction, 0});
            target = &groups.back();
        }
        ++target->faces;
    }

    std::sort(
        groups.begin(),
        groups.end(),
        [](const BendRadiusGroup& left, const BendRadiusGroup& right) {
            if (left.axis_category != right.axis_category) {
                return left.axis_category < right.axis_category;
            }
            if (std::abs(left.radius - right.radius) > RadiusGroupTolerance) {
                return left.radius < right.radius;
            }
            return left.normal_direction < right.normal_direction;
        });
    return groups;
}

std::vector<BendRadiusPair> find_bend_radius_pairs(
    const std::vector<BendRadiusGroup>& groups,
    double dominant_thickness) {
    std::vector<BendRadiusPair> pairs;
    if (dominant_thickness <= Epsilon) return pairs;

    for (const BendRadiusGroup& inner : groups) {
        for (const BendRadiusGroup& outer : groups) {
            if (inner.axis_category != outer.axis_category ||
                inner.normal_direction >= 0 ||
                outer.normal_direction < 0 ||
                outer.radius <= inner.radius) {
                continue;
            }
            const double difference = outer.radius - inner.radius;
            if (std::abs(difference - dominant_thickness) > 0.002) continue;
            pairs.push_back(BendRadiusPair{
                inner,
                outer,
                difference,
                std::min(inner.faces, outer.faces)});
        }
    }
    std::sort(
        pairs.begin(),
        pairs.end(),
        [](const BendRadiusPair& left, const BendRadiusPair& right) {
            if (left.inner.axis_category != right.inner.axis_category) {
                return left.inner.axis_category < right.inner.axis_category;
            }
            return left.inner.radius < right.inner.radius;
        });
    return pairs;
}

RadiusPairAnalysis analyze_radius_pairs(
    const TopoDS_Shape& shape,
    const std::vector<TorusRadiusGroup>& torus_groups,
    const ThicknessAnalysis& thickness) {
    RadiusPairAnalysis analysis;
    analysis.torus_pairs = find_torus_radius_pairs(
        torus_groups,
        thickness.dominant_thickness);
    analysis.torus_patches = collect_torus_patches(shape);
    analysis.bend_groups = collect_bend_radius_groups(shape);
    analysis.bend_pairs = find_bend_radius_pairs(
        analysis.bend_groups,
        thickness.dominant_thickness);
    return analysis;
}

std::vector<BodySurfaceSummary> analyze_bodies(const TopoDS_Shape& shape) {
    TopTools_IndexedMapOfShape solids;
    TopExp::MapShapes(shape, TopAbs_SOLID, solids);

    std::vector<BodySurfaceSummary> bodies;
    bodies.reserve(static_cast<std::size_t>(solids.Extent()));
    for (Standard_Integer index = 1; index <= solids.Extent(); ++index) {
        const TopoDS_Shape& solid = solids(index);
        BodySurfaceSummary body;
        std::ostringstream id;
        id << 'B' << std::setw(3) << std::setfill('0') << index;
        body.id = id.str();
        body.faces = count_subshapes(solid, TopAbs_FACE);
        body.surface_types = classify_surfaces(solid);
        analyze_radius_groups(
            solid,
            body.cylinder_radius_groups,
            body.torus_radius_groups);
        body.thickness_analysis = analyze_thickness(solid);
        body.radius_pair_analysis = analyze_radius_pairs(
            solid,
            body.torus_radius_groups,
            body.thickness_analysis);
        bodies.push_back(body);
    }
    return bodies;
}

bool build_cylinder_patch(
    const std::string& body_id,
    const std::string& face_id,
    const TopoDS_Face& face,
    CylinderPatch& patch) {
    const BRepAdaptor_Surface surface(face, Standard_True);
    if (surface.GetType() != GeomAbs_Cylinder) return false;

    const gp_Cylinder cylinder = surface.Cylinder();
    const Point3 axis = canonical(cylinder.Axis().Direction());
    const gp_Pnt location = cylinder.Axis().Location();
    const Point3 axis_point{location.X(), location.Y(), location.Z()};

    double minimum = std::numeric_limits<double>::max();
    double maximum = std::numeric_limits<double>::lowest();
    bool has_sample = false;

    TopTools_IndexedMapOfShape edges;
    TopExp::MapShapes(face, TopAbs_EDGE, edges);
    for (Standard_Integer edge_index = 1;
         edge_index <= edges.Extent();
         ++edge_index) {
        const TopoDS_Edge edge = TopoDS::Edge(edges(edge_index));
        try {
            const BRepAdaptor_Curve curve(edge);
            const double first = curve.FirstParameter();
            const double last = curve.LastParameter();
            if (!std::isfinite(first) || !std::isfinite(last)) continue;

            constexpr int SampleCount = 32;
            for (int sample = 0; sample <= SampleCount; ++sample) {
                const double parameter = first +
                    (last - first) * sample / static_cast<double>(SampleCount);
                const gp_Pnt point = curve.Value(parameter);
                const Point3 value{point.X(), point.Y(), point.Z()};
                const double station = dot(value, axis);
                minimum = std::min(minimum, station);
                maximum = std::max(maximum, station);
                has_sample = true;
            }
        } catch (...) {
            // A malformed or degenerated edge is ignored. Other boundary
            // edges on the same face normally still define the axial range.
        }
    }

    const double radius = cylinder.Radius();
    const double axial_length = maximum - minimum;
    if (!has_sample || axial_length <= Epsilon || radius <= Epsilon) {
        return false;
    }

    GProp_GProps properties;
    BRepGProp::SurfaceProperties(face, properties);
    const double coverage = properties.Mass() / (2.0 * Pi * radius * axial_length);

    patch.body_id = body_id;
    patch.face_id = face_id;
    patch.axis = axis;
    patch.axis_point = axis_point;
    patch.diameter = radius * 2.0;
    patch.minimum_station = minimum;
    patch.maximum_station = maximum;
    patch.area = properties.Mass();
    patch.coverage = std::clamp(coverage, 0.0, 1.0);
    patch.is_internal = face.Orientation() == TopAbs_REVERSED;
    return true;
}

std::vector<CylinderPatch> collect_full_cylinder_patches(
    const TopoDS_Shape& shape) {
    std::vector<CylinderPatch> fragments;
    TopTools_IndexedMapOfShape solids;
    TopExp::MapShapes(shape, TopAbs_SOLID, solids);

    for (Standard_Integer solid_index = 1;
         solid_index <= solids.Extent();
         ++solid_index) {
        std::ostringstream body_id;
        body_id << 'B' << std::setw(3) << std::setfill('0') << solid_index;

        TopTools_IndexedMapOfShape faces;
        TopExp::MapShapes(solids(solid_index), TopAbs_FACE, faces);
        for (Standard_Integer face_index = 1;
             face_index <= faces.Extent();
             ++face_index) {
            std::ostringstream face_id;
            face_id << body_id.str() << "-F"
                    << std::setw(3) << std::setfill('0') << face_index;
            CylinderPatch patch;
            if (build_cylinder_patch(
                    body_id.str(),
                    face_id.str(),
                    TopoDS::Face(faces(face_index)),
                    patch)) {
                fragments.push_back(patch);
            }
        }
    }

    std::vector<CylinderPatch> merged;
    for (const CylinderPatch& fragment : fragments) {
        CylinderPatch* target = nullptr;
        for (CylinderPatch& existing : merged) {
            if (existing.body_id != fragment.body_id ||
                existing.is_internal != fragment.is_internal ||
                std::abs(existing.diameter - fragment.diameter) >
                    DiameterTolerance ||
                std::abs(dot(existing.axis, fragment.axis)) < AxisCosine) {
                continue;
            }
            const Point3 delta = fragment.axis_point - existing.axis_point;
            const Point3 radial = delta - existing.axis * dot(delta, existing.axis);
            if (length(radial) > PositionTolerance) continue;
            const double gap = interval_gap(
                existing.minimum_station,
                existing.maximum_station,
                fragment.minimum_station,
                fragment.maximum_station);
            if (gap <= std::max(0.20, fragment.diameter * 0.05)) {
                target = &existing;
                break;
            }
        }

        if (target == nullptr) {
            merged.push_back(fragment);
            continue;
        }

        target->minimum_station =
            std::min(target->minimum_station, fragment.minimum_station);
        target->maximum_station =
            std::max(target->maximum_station, fragment.maximum_station);
        target->area += fragment.area;
        target->source_face_count += fragment.source_face_count;
        const double axial_length =
            target->maximum_station - target->minimum_station;
        const double radius = target->diameter * 0.5;
        target->coverage = axial_length <= Epsilon || radius <= Epsilon
            ? 0.0
            : std::clamp(
                target->area / (2.0 * Pi * radius * axial_length),
                0.0,
                1.0);
    }

    std::vector<CylinderPatch> full_patches;
    for (const CylinderPatch& patch : merged) {
        if (patch.coverage >= 0.84) full_patches.push_back(patch);
    }
    return full_patches;
}

double interval_gap(
    double first_min,
    double first_max,
    double second_min,
    double second_max) {
    if (first_max < second_min) return second_min - first_max;
    if (second_max < first_min) return first_min - second_max;
    return 0.0;
}

void add_unique_diameter(std::vector<double>& values, double value) {
    for (const double existing : values) {
        if (std::abs(existing - value) <= DiameterTolerance) return;
    }
    values.push_back(value);
}

std::vector<AxialFeature> build_axial_features(
    std::vector<CylinderPatch> patches) {
    struct Group {
        std::string body_id;
        Point3 axis;
        Point3 axis_point;
        double minimum_station = 0.0;
        double maximum_station = 0.0;
        std::vector<CylinderPatch> patches;
    };

    std::sort(
        patches.begin(),
        patches.end(),
        [](const CylinderPatch& left, const CylinderPatch& right) {
            if (left.body_id != right.body_id) {
                return left.body_id < right.body_id;
            }
            return left.minimum_station < right.minimum_station;
        });

    std::vector<Group> groups;
    for (const CylinderPatch& patch : patches) {
        Group* target = nullptr;
        for (Group& group : groups) {
            if (group.body_id != patch.body_id ||
                std::abs(dot(group.axis, patch.axis)) < AxisCosine) {
                continue;
            }
            const Point3 delta = patch.axis_point - group.axis_point;
            const Point3 radial = delta - group.axis * dot(delta, group.axis);
            if (length(radial) > PositionTolerance) continue;

            const double gap = interval_gap(
                group.minimum_station,
                group.maximum_station,
                patch.minimum_station,
                patch.maximum_station);
            if (gap <= std::max(0.20, patch.diameter * 0.05)) {
                target = &group;
                break;
            }
        }

        if (target == nullptr) {
            groups.push_back(Group{
                patch.body_id,
                patch.axis,
                patch.axis_point,
                patch.minimum_station,
                patch.maximum_station,
                {}});
            target = &groups.back();
        }
        target->patches.push_back(patch);
        target->minimum_station =
            std::min(target->minimum_station, patch.minimum_station);
        target->maximum_station =
            std::max(target->maximum_station, patch.maximum_station);
    }

    int hole_number = 0;
    int external_number = 0;
    int composite_number = 0;
    std::vector<AxialFeature> features;
    for (const Group& group : groups) {
        bool has_internal = false;
        bool has_external = false;
        for (const CylinderPatch& patch : group.patches) {
            has_internal = has_internal || patch.is_internal;
            has_external = has_external || !patch.is_internal;
        }

        AxialFeature feature;
        std::ostringstream id;
        if (has_internal && !has_external) {
            id << "HF" << std::setw(3) << std::setfill('0') << ++hole_number;
            feature.type = group.patches.size() > 1
                ? "COMPOSITE_HOLE"
                : "ROUND_HOLE";
        } else if (!has_internal && has_external) {
            id << "EF" << std::setw(3) << std::setfill('0') << ++external_number;
            feature.type = group.patches.size() > 1
                ? "STEPPED_EXTERNAL_CYLINDER"
                : "EXTERNAL_CYLINDER";
        } else {
            id << "CF" << std::setw(3) << std::setfill('0') << ++composite_number;
            feature.type = "COAXIAL_COMPOSITE";
        }
        feature.id = id.str();
        feature.body_id = group.body_id;
        feature.axis = group.axis;
        feature.total_length = group.maximum_station - group.minimum_station;

        const double origin_station = dot(group.axis_point, group.axis);
        feature.start = group.axis_point +
            group.axis * (group.minimum_station - origin_station);
        feature.end = group.axis_point +
            group.axis * (group.maximum_station - origin_station);
        feature.center = group.axis_point + group.axis *
            ((group.minimum_station + group.maximum_station) * 0.5 -
             origin_station);

        feature.segments = group.patches;
        for (const CylinderPatch& patch : group.patches) {
            add_unique_diameter(feature.diameters, patch.diameter);
        }
        std::sort(feature.diameters.begin(), feature.diameters.end());
        features.push_back(feature);
    }
    return features;
}

std::vector<StudFeature> build_stud_features(
    const std::vector<AxialFeature>& features) {
    struct Bucket { Point3 axis; Point3 center; std::vector<const AxialFeature*> items; };
    std::vector<Bucket> buckets;
    for (const AxialFeature& feature : features) {
        if (feature.type.find("EXTERNAL_CYLINDER") == std::string::npos ||
            std::abs(feature.axis.y) < 0.98) continue;
        if (feature.diameters.empty()) continue;
        auto found = std::find_if(
            buckets.begin(), buckets.end(),
            [&feature](const Bucket& bucket) {
                if (std::abs(dot(bucket.axis, feature.axis)) < AxisCosine) return false;
                const Point3 delta = feature.center - bucket.center;
                const Point3 radial = delta - bucket.axis * dot(delta, bucket.axis);
                return length(radial) <= PositionTolerance;
            });
        if (found == buckets.end()) {
            buckets.push_back({feature.axis, feature.center, {&feature}});
        } else {
            found->items.push_back(&feature);
        }
    }

    std::vector<StudFeature> result;
    int number = 0;
    for (const Bucket& bucket : buckets) {
        if (bucket.items.size() < 2) continue;
        StudFeature stud;
        std::ostringstream id;
        id << "SF" << std::setw(3) << std::setfill('0') << ++number;
        stud.id = id.str();
        stud.type = "STEPPED_STUD";
        stud.axis = bucket.axis;
        stud.axis_point = bucket.center -
            bucket.axis * dot(bucket.center, bucket.axis);
        double minimum = std::numeric_limits<double>::infinity();
        double maximum = -std::numeric_limits<double>::infinity();
        for (const AxialFeature* feature : bucket.items) {
            stud.feature_ids.push_back(feature->id);
            if (std::find(stud.body_ids.begin(), stud.body_ids.end(), feature->body_id) == stud.body_ids.end()) {
                stud.body_ids.push_back(feature->body_id);
            }
            stud.centers.push_back(feature->center);
            minimum = std::min(minimum, dot(feature->start, stud.axis));
            minimum = std::min(minimum, dot(feature->end, stud.axis));
            maximum = std::max(maximum, dot(feature->start, stud.axis));
            maximum = std::max(maximum, dot(feature->end, stud.axis));
            for (double diameter : feature->diameters) add_unique_diameter(stud.diameters, diameter);
            stud.segments.insert(stud.segments.end(), feature->segments.begin(), feature->segments.end());
        }
        stud.overall_length = maximum - minimum;
        std::sort(stud.diameters.begin(), stud.diameters.end());
        if (!stud.diameters.empty()) {
            stud.tip_diameter = stud.diameters.front();
            stud.head_diameter = stud.diameters.back();
        }
        const auto longest = std::max_element(
            stud.segments.begin(), stud.segments.end(),
            [](const CylinderPatch& left, const CylinderPatch& right) {
                return left.maximum_station - left.minimum_station <
                       right.maximum_station - right.minimum_station;
            });
        if (longest != stud.segments.end()) {
            stud.nominal_shaft_diameter = longest->diameter;
        }
        const bool has_diameter_step =
            stud.diameters.size() >= 2 &&
            stud.nominal_shaft_diameter > Epsilon &&
            stud.head_diameter >= stud.nominal_shaft_diameter * 1.15;
        if (!has_diameter_step) {
            continue;
        }
        std::sort(
            stud.segments.begin(), stud.segments.end(),
            [](const CylinderPatch& left, const CylinderPatch& right) {
                return left.minimum_station < right.minimum_station;
            });
        stud.assessment =
            bucket.items.size() >= 4 && stud.diameters.size() >= 4
                ? "HIGH_CONFIDENCE_STEPPED_STUD"
                : "STEPPED_STUD_CANDIDATE";
        result.push_back(std::move(stud));
    }
    return result;
}

double iso_metric_coarse_pitch(double nominal_diameter) {
    const std::vector<std::pair<double, double>> sizes = {
        {3.0, 0.5}, {4.0, 0.7}, {5.0, 0.8}, {6.0, 1.0},
        {8.0, 1.25}, {10.0, 1.5}, {12.0, 1.75},
        {14.0, 2.0}, {16.0, 2.0}, {18.0, 2.5}, {20.0, 2.5}};
    for (const auto& size : sizes) {
        if (std::abs(nominal_diameter - size.first) <= 0.15) {
            return size.second;
        }
    }
    return 0.0;
}

std::vector<ThreadFeature> build_thread_features(
    const std::vector<StudFeature>& studs) {
    std::vector<ThreadFeature> result;
    int number = 0;
    for (const StudFeature& stud : studs) {
        if (stud.assessment != "HIGH_CONFIDENCE_STEPPED_STUD") {
            continue;
        }
        const double pitch =
            iso_metric_coarse_pitch(stud.nominal_shaft_diameter);
        if (pitch <= Epsilon) {
            continue;
        }
        double threaded_length = 0.0;
        for (const CylinderPatch& segment : stud.segments) {
            if (std::abs(segment.diameter -
                         stud.nominal_shaft_diameter) <= 0.15) {
                threaded_length = std::max(
                    threaded_length,
                    segment.maximum_station - segment.minimum_station);
            }
        }
        if (threaded_length <= Epsilon) {
            continue;
        }
        ThreadFeature thread;
        std::ostringstream id;
        id << "TF" << std::setw(3) << std::setfill('0') << ++number;
        thread.id = id.str();
        thread.type = "EXTERNAL_METRIC_THREAD_INFERRED";
        thread.source_stud_id = stud.id;
        thread.axis = stud.axis;
        thread.axis_point = stud.axis_point;
        thread.nominal_diameter =
            std::round(stud.nominal_shaft_diameter);
        thread.pitch = pitch;
        thread.threaded_length = threaded_length;
        thread.assessment = "INFERRED_FROM_STUD_SHAFT_GEOMETRY";
        result.push_back(std::move(thread));
    }
    return result;
}

std::vector<ChamferFeature> build_chamfer_features(
    const std::vector<StudFeature>& studs) {
    constexpr double radians_to_degrees = 57.29577951308232;
    std::vector<ChamferFeature> result;
    int number = 0;
    for (const StudFeature& stud : studs) {
        if (stud.assessment != "HIGH_CONFIDENCE_STEPPED_STUD") {
            continue;
        }
        const CylinderPatch* tip = nullptr;
        const CylinderPatch* shaft = nullptr;
        for (const CylinderPatch& segment : stud.segments) {
            if (std::abs(segment.diameter - stud.tip_diameter) <= 0.02 &&
                (tip == nullptr ||
                 segment.maximum_station - segment.minimum_station >
                     tip->maximum_station - tip->minimum_station)) {
                tip = &segment;
            }
            if (std::abs(segment.diameter -
                         stud.nominal_shaft_diameter) <= 0.02 &&
                (shaft == nullptr ||
                 segment.maximum_station - segment.minimum_station >
                     shaft->maximum_station - shaft->minimum_station)) {
                shaft = &segment;
            }
        }
        if (tip == nullptr || shaft == nullptr) {
            continue;
        }

        double axial_length = 0.0;
        double station = 0.0;
        if (tip->maximum_station <= shaft->minimum_station) {
            axial_length = shaft->minimum_station - tip->maximum_station;
            station = (shaft->minimum_station + tip->maximum_station) / 2.0;
        } else if (shaft->maximum_station <= tip->minimum_station) {
            axial_length = tip->minimum_station - shaft->maximum_station;
            station = (tip->minimum_station + shaft->maximum_station) / 2.0;
        } else {
            continue;
        }
        const double radial_depth =
            std::abs(stud.nominal_shaft_diameter - stud.tip_diameter) / 2.0;
        if (axial_length < 0.02 || axial_length > 5.0 ||
            radial_depth < 0.02 || radial_depth > 5.0) {
            continue;
        }
        const double angle =
            std::atan2(radial_depth, axial_length) * radians_to_degrees;
        if (angle < 20.0 || angle > 70.0) {
            continue;
        }

        ChamferFeature chamfer;
        std::ostringstream id;
        id << "CH" << std::setw(3) << std::setfill('0') << ++number;
        chamfer.id = id.str();
        chamfer.type = "STUD_TIP_CHAMFER_INFERRED";
        chamfer.source_stud_id = stud.id;
        chamfer.axis = stud.axis;
        chamfer.axis_point = stud.axis_point;
        chamfer.station = station;
        chamfer.axial_length = axial_length;
        chamfer.radial_depth = radial_depth;
        chamfer.angle_degrees = angle;
        chamfer.first_diameter = stud.nominal_shaft_diameter;
        chamfer.second_diameter = stud.tip_diameter;
        chamfer.assessment =
            "HIGH_CONFIDENCE_COAXIAL_STUD_TIP_CHAMFER";
        result.push_back(std::move(chamfer));
    }
    return result;
}

std::vector<HoleAxisGroup> build_hole_axis_groups(
    const std::vector<AxialFeature>& features) {
    std::vector<HoleAxisGroup> groups;

    for (const AxialFeature& feature : features) {
        if (feature.type.find("HOLE") == std::string::npos) continue;

        HoleAxisGroup* target = nullptr;
        for (HoleAxisGroup& group : groups) {
            if (std::abs(dot(group.axis, feature.axis)) < AxisCosine) continue;
            const Point3 delta = feature.center - group.center;
            const Point3 radial = delta - group.axis * dot(delta, group.axis);
            if (length(radial) <= PositionTolerance) {
                target = &group;
                break;
            }
        }

        if (target == nullptr) {
            HoleAxisGroup group;
            group.axis = feature.axis;
            group.center = feature.center -
                feature.axis * dot(feature.center, feature.axis);
            groups.push_back(group);
            target = &groups.back();
        }

        target->feature_ids.push_back(feature.id);
        if (std::find(
                target->body_ids.begin(),
                target->body_ids.end(),
                feature.body_id) == target->body_ids.end()) {
            target->body_ids.push_back(feature.body_id);
        }
        for (const double diameter : feature.diameters) {
            add_unique_diameter(target->diameters, diameter);
        }
    }

    std::sort(
        groups.begin(),
        groups.end(),
        [](const HoleAxisGroup& left, const HoleAxisGroup& right) {
            if (std::abs(left.center.x - right.center.x) > PositionTolerance) {
                return left.center.x < right.center.x;
            }
            if (std::abs(left.center.y - right.center.y) > PositionTolerance) {
                return left.center.y < right.center.y;
            }
            return left.center.z < right.center.z;
        });

    int number = 0;
    for (HoleAxisGroup& group : groups) {
        std::ostringstream id;
        id << "HG" << std::setw(3) << std::setfill('0') << ++number;
        group.id = id.str();
        group.type = group.body_ids.size() > 1
            ? "COAXIAL_ACROSS_BODIES"
            : "SINGLE_BODY_HOLE";
        std::sort(group.diameters.begin(), group.diameters.end());
    }
    return groups;
}

bool same_diameter_layers(
    const HoleAxisGroup& left,
    const HoleAxisGroup& right) {
    if (left.diameters.size() != right.diameters.size()) return false;
    for (std::size_t index = 0; index < left.diameters.size(); ++index) {
        if (std::abs(left.diameters[index] - right.diameters[index]) >
            DiameterTolerance) {
            return false;
        }
    }
    return true;
}

std::vector<HolePattern> build_hole_patterns(
    const std::vector<HoleAxisGroup>& groups) {
    std::vector<HolePattern> patterns;
    const int diagonal_pairings[3][4] = {
        {0, 1, 2, 3},
        {0, 2, 1, 3},
        {0, 3, 1, 2}
    };

    for (std::size_t a = 0; a < groups.size(); ++a) {
        for (std::size_t b = a + 1; b < groups.size(); ++b) {
            for (std::size_t c = b + 1; c < groups.size(); ++c) {
                for (std::size_t d = c + 1; d < groups.size(); ++d) {
                    const HoleAxisGroup* candidate[4] = {
                        &groups[a], &groups[b], &groups[c], &groups[d]
                    };
                    bool compatible = true;
                    for (int index = 1; index < 4; ++index) {
                        compatible = compatible &&
                            same_diameter_layers(*candidate[0], *candidate[index]) &&
                            std::abs(dot(candidate[0]->axis, candidate[index]->axis)) >=
                                AxisCosine;
                    }
                    if (!compatible) continue;

                    bool found = false;
                    for (const auto& pairing : diagonal_pairings) {
                        const Point3& p0 = candidate[pairing[0]]->center;
                        const Point3& p1 = candidate[pairing[1]]->center;
                        const Point3& p2 = candidate[pairing[2]]->center;
                        const Point3& p3 = candidate[pairing[3]]->center;
                        const Point3 midpoint_1 = (p0 + p1) * 0.5;
                        const Point3 midpoint_2 = (p2 + p3) * 0.5;
                        const double diagonal_1 = length(p1 - p0);
                        const double diagonal_2 = length(p3 - p2);
                        if (length(midpoint_1 - midpoint_2) > PositionTolerance ||
                            std::abs(diagonal_1 - diagonal_2) > PositionTolerance) {
                            continue;
                        }

                        const Point3 side_a = p2 - p0;
                        const Point3 side_b = p3 - p0;
                        const double spacing_a = length(side_a);
                        const double spacing_b = length(side_b);
                        if (spacing_a <= Epsilon || spacing_b <= Epsilon ||
                            std::abs(dot(side_a, side_b)) /
                                (spacing_a * spacing_b) > 1.0e-4) {
                            continue;
                        }

                        HolePattern pattern;
                        pattern.type = "RECTANGULAR_HOLE_ARRAY";
                        pattern.center = (midpoint_1 + midpoint_2) * 0.5;
                        pattern.direction_a = canonical_unit(side_a);
                        pattern.direction_b = canonical_unit(side_b);
                        pattern.spacing_a = spacing_a;
                        pattern.spacing_b = spacing_b;
                        pattern.diagonal = (diagonal_1 + diagonal_2) * 0.5;
                        pattern.diameters = candidate[0]->diameters;

                        if (dominant_axis(pattern.direction_a) >
                            dominant_axis(pattern.direction_b)) {
                            std::swap(pattern.direction_a, pattern.direction_b);
                            std::swap(pattern.spacing_a, pattern.spacing_b);
                        }
                        for (const HoleAxisGroup* item : candidate) {
                            pattern.group_ids.push_back(item->id);
                        }
                        std::sort(pattern.group_ids.begin(), pattern.group_ids.end());
                        patterns.push_back(pattern);
                        found = true;
                        break;
                    }
                    (void)found;
                }
            }
        }
    }

    for (std::size_t index = 0; index < patterns.size(); ++index) {
        std::ostringstream id;
        id << "AP" << std::setw(3) << std::setfill('0') << index + 1;
        patterns[index].id = id.str();
    }
    return patterns;
}

void append_surface_counts(
    std::ostringstream& json,
    const SurfaceCounts& counts,
    const std::string& indent) {
    json << indent << "{\n"
         << indent << "  \"plane\": " << counts.plane << ",\n"
         << indent << "  \"cylinder\": " << counts.cylinder << ",\n"
         << indent << "  \"cone\": " << counts.cone << ",\n"
         << indent << "  \"sphere\": " << counts.sphere << ",\n"
         << indent << "  \"torus\": " << counts.torus << ",\n"
         << indent << "  \"bezier\": " << counts.bezier << ",\n"
         << indent << "  \"bspline\": " << counts.bspline << ",\n"
         << indent << "  \"revolution\": " << counts.revolution << ",\n"
         << indent << "  \"extrusion\": " << counts.extrusion << ",\n"
         << indent << "  \"offset\": " << counts.offset << ",\n"
         << indent << "  \"other\": " << counts.other << "\n"
         << indent << '}';
}

void append_cylinder_radius_groups(
    std::ostringstream& json,
    const std::vector<CylinderRadiusGroup>& groups,
    const std::string& indent) {
    json << "[";
    if (!groups.empty()) json << '\n';
    for (std::size_t index = 0; index < groups.size(); ++index) {
        const CylinderRadiusGroup& group = groups[index];
        json << indent << "  {\"radius\": " << group.radius
             << ", \"diameter\": " << group.radius * 2.0
             << ", \"faces\": " << group.faces << '}'
             << (index + 1 < groups.size() ? "," : "") << '\n';
    }
    if (!groups.empty()) json << indent;
    json << ']';
}

void append_torus_radius_groups(
    std::ostringstream& json,
    const std::vector<TorusRadiusGroup>& groups,
    const std::string& indent) {
    json << "[";
    if (!groups.empty()) json << '\n';
    for (std::size_t index = 0; index < groups.size(); ++index) {
        const TorusRadiusGroup& group = groups[index];
        json << indent << "  {\"minor_radius\": " << group.minor_radius
             << ", \"major_radius_min\": " << group.major_radius_min
             << ", \"major_radius_max\": " << group.major_radius_max
             << ", \"faces\": " << group.faces << '}'
             << (index + 1 < groups.size() ? "," : "") << '\n';
    }
    if (!groups.empty()) json << indent;
    json << ']';
}

void append_point(std::ostringstream& json, const Point3& point) {
    json << '[' << point.x << ", " << point.y << ", " << point.z << ']';
}

void append_axial_features(
    std::ostringstream& json,
    const std::vector<AxialFeature>& features) {
    json << "[";
    if (!features.empty()) json << '\n';
    for (std::size_t index = 0; index < features.size(); ++index) {
        const AxialFeature& feature = features[index];
        json << "    {\n"
             << "      \"id\": \"" << feature.id << "\",\n"
             << "      \"type\": \"" << feature.type << "\",\n"
             << "      \"body_id\": \"" << feature.body_id << "\",\n"
             << "      \"axis\": ";
        append_point(json, feature.axis);
        json << ",\n      \"center\": ";
        append_point(json, feature.center);
        json << ",\n      \"start\": ";
        append_point(json, feature.start);
        json << ",\n      \"end\": ";
        append_point(json, feature.end);
        json << ",\n"
             << "      \"total_length\": " << feature.total_length << ",\n"
             << "      \"diameters\": [";
        for (std::size_t diameter_index = 0;
             diameter_index < feature.diameters.size();
             ++diameter_index) {
            if (diameter_index > 0) json << ", ";
            json << feature.diameters[diameter_index];
        }
        json << "],\n"
             << "      \"segments\": [\n";
        for (std::size_t segment_index = 0;
             segment_index < feature.segments.size();
             ++segment_index) {
            const CylinderPatch& segment = feature.segments[segment_index];
            json << "        {\"face_id\": \"" << segment.face_id
                 << "\", \"internal\": "
                 << (segment.is_internal ? "true" : "false")
                 << ", \"source_face_count\": "
                 << segment.source_face_count
                 << ", \"diameter\": " << segment.diameter
                 << ", \"length\": "
                 << segment.maximum_station - segment.minimum_station
                 << ", \"coverage\": " << segment.coverage << '}'
                 << (segment_index + 1 < feature.segments.size() ? "," : "")
                 << '\n';
        }
        json << "      ]\n"
             << "    }" << (index + 1 < features.size() ? "," : "") << '\n';
    }
    json << "  ]";
}

void append_string_array(
    std::ostringstream& json,
    const std::vector<std::string>& values);

void append_stud_features(
    std::ostringstream& json,
    const std::vector<StudFeature>& studs) {
    json << "[";
    if (!studs.empty()) json << '\n';
    for (std::size_t index = 0; index < studs.size(); ++index) {
        const StudFeature& stud = studs[index];
        json << "    {\"id\": \"" << stud.id
             << "\", \"type\": \"" << stud.type
             << "\", \"axis\": ";
        append_point(json, stud.axis);
        json << ", \"axis_point\": ";
        append_point(json, stud.axis_point);
        json << ", \"count\": " << stud.centers.size()
             << ", \"centers\": [";
        for (std::size_t center = 0; center < stud.centers.size(); ++center) {
            if (center > 0) json << ", ";
            append_point(json, stud.centers[center]);
        }
        json << "], \"feature_ids\": ";
        append_string_array(json, stud.feature_ids);
        json << ", \"body_ids\": ";
        append_string_array(json, stud.body_ids);
        json << ", \"overall_length\": " << stud.overall_length
             << ", \"nominal_shaft_diameter\": "
             << stud.nominal_shaft_diameter
             << ", \"head_diameter\": " << stud.head_diameter
             << ", \"tip_diameter\": " << stud.tip_diameter
             << ", \"assessment\": \"" << stud.assessment << "\""
             << ", \"diameters\": [";
        for (std::size_t diameter = 0; diameter < stud.diameters.size(); ++diameter) {
            if (diameter > 0) json << ", ";
            json << stud.diameters[diameter];
        }
        json << "], \"segments\": [";
        for (std::size_t segment = 0; segment < stud.segments.size(); ++segment) {
            const CylinderPatch& patch = stud.segments[segment];
            if (segment > 0) json << ", ";
            json << "{\"face_id\": \"" << patch.face_id
                 << "\", \"diameter\": " << patch.diameter
                 << ", \"length\": "
                 << patch.maximum_station - patch.minimum_station
                 << ", \"start_station\": " << patch.minimum_station
                 << ", \"end_station\": " << patch.maximum_station
                 << "}";
        }
        json << "]}" << (index + 1 < studs.size() ? "," : "") << '\n';
    }
    if (!studs.empty()) json << "  ";
    json << "]";
}

void append_thread_features(
    std::ostringstream& json,
    const std::vector<ThreadFeature>& threads) {
    json << "[";
    if (!threads.empty()) json << '\n';
    for (std::size_t index = 0; index < threads.size(); ++index) {
        const ThreadFeature& thread = threads[index];
        json << "    {\"id\": \"" << thread.id
             << "\", \"type\": \"" << thread.type
             << "\", \"source_stud_id\": \"" << thread.source_stud_id
             << "\", \"axis\": ";
        append_point(json, thread.axis);
        json << ", \"axis_point\": ";
        append_point(json, thread.axis_point);
        json << ", \"nominal_diameter\": " << thread.nominal_diameter
             << ", \"pitch\": " << thread.pitch
             << ", \"threaded_length\": " << thread.threaded_length
             << ", \"assessment\": \"" << thread.assessment << "\"}"
             << (index + 1 < threads.size() ? "," : "") << '\n';
    }
    if (!threads.empty()) json << "  ";
    json << "]";
}

void append_chamfer_features(
    std::ostringstream& json,
    const std::vector<ChamferFeature>& chamfers) {
    json << "[";
    if (!chamfers.empty()) json << '\n';
    for (std::size_t index = 0; index < chamfers.size(); ++index) {
        const ChamferFeature& chamfer = chamfers[index];
        json << "    {\"id\": \"" << chamfer.id
             << "\", \"type\": \"" << chamfer.type
             << "\", \"source_stud_id\": \"" << chamfer.source_stud_id
             << "\", \"axis\": ";
        append_point(json, chamfer.axis);
        json << ", \"axis_point\": ";
        append_point(json, chamfer.axis_point);
        json << ", \"station\": " << chamfer.station
             << ", \"axial_length\": " << chamfer.axial_length
             << ", \"radial_depth\": " << chamfer.radial_depth
             << ", \"angle_degrees\": " << chamfer.angle_degrees
             << ", \"first_diameter\": " << chamfer.first_diameter
             << ", \"second_diameter\": " << chamfer.second_diameter
             << ", \"assessment\": \"" << chamfer.assessment << "\"}"
             << (index + 1 < chamfers.size() ? "," : "") << '\n';
    }
    if (!chamfers.empty()) json << "  ";
    json << "]";
}

void append_string_array(
    std::ostringstream& json,
    const std::vector<std::string>& values) {
    json << '[';
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) json << ", ";
        json << '\"' << json_escape(values[index]) << '\"';
    }
    json << ']';
}

void append_hole_axis_groups(
    std::ostringstream& json,
    const std::vector<HoleAxisGroup>& groups) {
    json << "[";
    if (!groups.empty()) json << '\n';
    for (std::size_t index = 0; index < groups.size(); ++index) {
        const HoleAxisGroup& group = groups[index];
        json << "    {\n"
             << "      \"id\": \"" << group.id << "\",\n"
             << "      \"type\": \"" << group.type << "\",\n"
             << "      \"axis\": ";
        append_point(json, group.axis);
        json << ",\n      \"center\": ";
        append_point(json, group.center);
        json << ",\n      \"body_ids\": ";
        append_string_array(json, group.body_ids);
        json << ",\n      \"feature_ids\": ";
        append_string_array(json, group.feature_ids);
        json << ",\n      \"diameters\": [";
        for (std::size_t diameter_index = 0;
             diameter_index < group.diameters.size();
             ++diameter_index) {
            if (diameter_index > 0) json << ", ";
            json << group.diameters[diameter_index];
        }
        json << "]\n"
             << "    }" << (index + 1 < groups.size() ? "," : "") << '\n';
    }
    json << "  ]";
}

void append_hole_patterns(
    std::ostringstream& json,
    const std::vector<HolePattern>& patterns) {
    json << "[";
    if (!patterns.empty()) json << '\n';
    for (std::size_t index = 0; index < patterns.size(); ++index) {
        const HolePattern& pattern = patterns[index];
        json << "    {\n"
             << "      \"id\": \"" << pattern.id << "\",\n"
             << "      \"type\": \"" << pattern.type << "\",\n"
             << "      \"count\": " << pattern.group_ids.size() << ",\n"
             << "      \"center\": ";
        append_point(json, pattern.center);
        json << ",\n      \"direction_a\": ";
        append_point(json, pattern.direction_a);
        json << ",\n      \"direction_b\": ";
        append_point(json, pattern.direction_b);
        json << ",\n"
             << "      \"spacing_a\": " << pattern.spacing_a << ",\n"
             << "      \"spacing_b\": " << pattern.spacing_b << ",\n"
             << "      \"diagonal\": " << pattern.diagonal << ",\n"
             << "      \"diameters\": [";
        for (std::size_t diameter_index = 0;
             diameter_index < pattern.diameters.size();
             ++diameter_index) {
            if (diameter_index > 0) json << ", ";
            json << pattern.diameters[diameter_index];
        }
        json << "],\n      \"group_ids\": ";
        append_string_array(json, pattern.group_ids);
        json << "\n    }"
             << (index + 1 < patterns.size() ? "," : "") << '\n';
    }
    json << "  ]";
}

void append_datum_dimensions(
    std::ostringstream& json,
    const std::vector<DatumDimension>& dimensions) {
    json << "[";
    if (!dimensions.empty()) json << '\n';
    for (std::size_t index = 0; index < dimensions.size(); ++index) {
        const DatumDimension& dimension = dimensions[index];
        json << "    {\"id\": \"" << dimension.id
             << "\", \"feature_id\": \"" << dimension.feature_id
             << "\", \"axis\": \"" << dimension.axis
             << "\", \"datum\": \"" << dimension.datum
             << "\", \"coordinate\": " << dimension.coordinate
             << ", \"value\": " << dimension.value << "}"
             << (index + 1 < dimensions.size() ? "," : "") << '\n';
    }
    if (!dimensions.empty()) json << "  ";
    json << "]";
}

void append_planar_openings(
    std::ostringstream& json,
    const std::vector<PlanarOpening>& openings) {
    json << "[";
    if (!openings.empty()) json << '\n';
    for (std::size_t index = 0; index < openings.size(); ++index) {
        const PlanarOpening& opening = openings[index];
        json << "    {\"id\": \"" << opening.id
             << "\", \"normal\": [" << opening.normal.x << ", "
             << opening.normal.y << ", " << opening.normal.z
             << "], \"min\": [" << opening.bounds.x_min << ", "
             << opening.bounds.y_min << ", " << opening.bounds.z_min
             << "], \"max\": [" << opening.bounds.x_max << ", "
             << opening.bounds.y_max << ", " << opening.bounds.z_max
             << "], \"source_wires\": " << opening.source_wires
             << ", \"edges\": " << opening.edges << '}'
             << (index + 1 < openings.size() ? "," : "") << '\n';
    }
    if (!openings.empty()) json << "  ";
    json << ']';
}

void append_thickness_analysis(
    std::ostringstream& json,
    const ThicknessAnalysis& analysis,
    const std::string& indent) {
    json << indent << "{\n"
         << indent << "  \"plane_groups\": " << analysis.plane_groups << ",\n"
         << indent << "  \"major_planes\": " << analysis.major_planes << ",\n"
         << indent << "  \"matched_pairs\": " << analysis.matched_pairs << ",\n"
         << indent << "  \"dominant_thickness\": "
         << analysis.dominant_thickness << ",\n"
         << indent << "  \"dominant_evidence\": "
         << analysis.dominant_evidence << ",\n"
         << indent << "  \"assessment\": \""
         << analysis.assessment << "\",\n"
         << indent << "  \"candidates\": [";
    if (!analysis.candidates.empty()) json << '\n';
    for (std::size_t index = 0; index < analysis.candidates.size(); ++index) {
        const ThicknessCandidate& candidate = analysis.candidates[index];
        json << indent << "    {\"thickness\": " << candidate.thickness
             << ", \"evidence\": " << candidate.evidence
             << ", \"x\": " << candidate.x_count
             << ", \"y\": " << candidate.y_count
             << ", \"z\": " << candidate.z_count
             << ", \"oblique\": " << candidate.oblique_count << '}'
             << (index + 1 < analysis.candidates.size() ? "," : "")
             << '\n';
    }
    if (!analysis.candidates.empty()) json << indent << "  ";
    json << "],\n"
         << indent << "  \"dominant_pairs\": [";
    if (!analysis.dominant_pairs.empty()) json << '\n';
    for (std::size_t index = 0;
         index < analysis.dominant_pairs.size();
         ++index) {
        const PlanePair& pair = analysis.dominant_pairs[index];
        json << indent << "    {\"normal\": ["
             << pair.first.normal.x << ", " << pair.first.normal.y << ", "
             << pair.first.normal.z << "], \"first_offset\": "
             << pair.first.offset << ", \"second_offset\": "
             << pair.second.offset << ", \"first_centroid\": ["
             << pair.first.centroid.x << ", " << pair.first.centroid.y << ", "
             << pair.first.centroid.z << "], \"second_centroid\": ["
             << pair.second.centroid.x << ", " << pair.second.centroid.y << ", "
             << pair.second.centroid.z << "], \"distance\": "
             << pair.distance << ", \"first_area\": " << pair.first.area
             << ", \"second_area\": " << pair.second.area << '}'
             << (index + 1 < analysis.dominant_pairs.size() ? "," : "")
             << '\n';
    }
    if (!analysis.dominant_pairs.empty()) json << indent << "  ";
    json << "]\n" << indent << '}';
}

void append_radius_pair_analysis(
    std::ostringstream& json,
    const RadiusPairAnalysis& analysis,
    const std::string& indent) {
    json << indent << "{\n"
         << indent << "  \"torus_pairs\": [";
    if (!analysis.torus_pairs.empty()) json << '\n';
    for (std::size_t index = 0; index < analysis.torus_pairs.size(); ++index) {
        const TorusRadiusPair& pair = analysis.torus_pairs[index];
        json << indent << "    {\"inner_radius\": " << pair.inner_radius
             << ", \"outer_radius\": " << pair.outer_radius
             << ", \"difference\": " << pair.difference
             << ", \"inner_faces\": " << pair.inner_faces
             << ", \"outer_faces\": " << pair.outer_faces
             << ", \"estimated_pairs\": " << pair.estimated_pairs
             << ", \"ambiguous\": " << (pair.ambiguous ? "true" : "false")
             << '}'
             << (index + 1 < analysis.torus_pairs.size() ? "," : "")
             << '\n';
    }
    if (!analysis.torus_pairs.empty()) json << indent << "  ";
    json << "],\n"
         << indent << "  \"torus_patches\": [";
    if (!analysis.torus_patches.empty()) json << '\n';
    for (std::size_t index = 0;
         index < analysis.torus_patches.size();
         ++index) {
        const TorusPatch& patch = analysis.torus_patches[index];
        json << indent << "    {\"face_id\": \"" << patch.face_id
             << "\", \"center\": [" << patch.center.x << ", "
             << patch.center.y << ", " << patch.center.z
             << "], \"axis\": [" << patch.axis.x << ", "
             << patch.axis.y << ", " << patch.axis.z
             << "], \"surface_point\": [" << patch.surface_point.x << ", "
             << patch.surface_point.y << ", " << patch.surface_point.z
             << "], \"major_radius\": " << patch.major_radius
             << ", \"minor_radius\": " << patch.minor_radius << '}'
             << (index + 1 < analysis.torus_patches.size() ? "," : "")
             << '\n';
    }
    if (!analysis.torus_patches.empty()) json << indent << "  ";
    json << "],\n"
         << indent << "  \"bend_groups\": [";
    if (!analysis.bend_groups.empty()) json << '\n';
    for (std::size_t index = 0; index < analysis.bend_groups.size(); ++index) {
        const BendRadiusGroup& group = analysis.bend_groups[index];
        json << indent << "    {\"radius\": " << group.radius
             << ", \"axis\": \"" << group.axis_category
             << "\", \"side\": \""
             << (group.normal_direction < 0 ? "INNER" : "OUTER")
             << "\", \"faces\": " << group.faces << '}'
             << (index + 1 < analysis.bend_groups.size() ? "," : "")
             << '\n';
    }
    if (!analysis.bend_groups.empty()) json << indent << "  ";
    json << "],\n"
         << indent << "  \"bend_pairs\": [";
    if (!analysis.bend_pairs.empty()) json << '\n';
    for (std::size_t index = 0; index < analysis.bend_pairs.size(); ++index) {
        const BendRadiusPair& pair = analysis.bend_pairs[index];
        json << indent << "    {\"axis\": \""
             << pair.inner.axis_category
             << "\", \"inner_radius\": " << pair.inner.radius
             << ", \"outer_radius\": " << pair.outer.radius
             << ", \"difference\": " << pair.difference
             << ", \"inner_faces\": " << pair.inner.faces
             << ", \"outer_faces\": " << pair.outer.faces
             << ", \"estimated_pairs\": " << pair.estimated_pairs << '}'
             << (index + 1 < analysis.bend_pairs.size() ? "," : "")
             << '\n';
    }
    if (!analysis.bend_pairs.empty()) json << indent << "  ";
    json << "]\n" << indent << '}';
}

std::string json_escape(const std::string& value) {
    std::ostringstream escaped;
    for (const unsigned char ch : value) {
        switch (ch) {
            case '\"': escaped << "\\\""; break;
            case '\\': escaped << "\\\\"; break;
            case '\b': escaped << "\\b"; break;
            case '\f': escaped << "\\f"; break;
            case '\n': escaped << "\\n"; break;
            case '\r': escaped << "\\r"; break;
            case '\t': escaped << "\\t"; break;
            default:
                if (ch < 0x20) {
                    escaped << "\\u"
                            << std::hex << std::setw(4) << std::setfill('0')
                            << static_cast<int>(ch)
                            << std::dec << std::setfill(' ');
                } else {
                    escaped << ch;
                }
        }
    }
    return escaped.str();
}

std::string make_json(
    const fs::path& input,
    const Counts& counts,
    const SurfaceCounts& surface_counts,
    const std::vector<BodySurfaceSummary>& bodies,
    const std::vector<AxialFeature>& axial_features,
    const std::vector<StudFeature>& stud_features,
    const std::vector<ThreadFeature>& thread_features,
    const std::vector<ChamferFeature>& chamfer_features,
    const std::vector<HoleAxisGroup>& hole_axis_groups,
    const std::vector<HolePattern>& hole_patterns,
    const std::vector<DatumDimension>& datum_dimensions,
    const std::vector<PlanarOpening>& planar_openings,
    const ThicknessAnalysis& thickness_analysis,
    const RadiusPairAnalysis& radius_pair_analysis,
    double surface_area,
    double volume,
    const Point3& center_of_mass,
    const Bounds& bounds) {
    std::ostringstream json;
    json << std::fixed << std::setprecision(6);
    json << "{\n"
             << "  \"schema_version\": \"0.18.0\",\n"
         << "  \"source_file\": \"" << json_escape(input.filename().string()) << "\",\n"
         << "  \"units\": {\"length\": \"mm\", \"area\": \"mm^2\", \"volume\": \"mm^3\"},\n"
         << "  \"topology\": {\n"
         << "    \"solids\": " << counts.solids << ",\n"
         << "    \"shells\": " << counts.shells << ",\n"
         << "    \"faces\": " << counts.faces << ",\n"
         << "    \"edges\": " << counts.edges << ",\n"
         << "    \"vertices\": " << counts.vertices << "\n"
         << "  },\n"
         << "  \"surface_types\": ";
    append_surface_counts(json, surface_counts, "  ");
    json << ",\n"
         << "  \"bodies\": [\n";
    for (std::size_t index = 0; index < bodies.size(); ++index) {
        const BodySurfaceSummary& body = bodies[index];
        json << "    {\n"
             << "      \"id\": \"" << body.id << "\",\n"
             << "      \"faces\": " << body.faces << ",\n"
             << "      \"surface_types\": ";
        append_surface_counts(json, body.surface_types, "      ");
        json << ",\n"
             << "      \"cylinder_radius_groups\": ";
        append_cylinder_radius_groups(json, body.cylinder_radius_groups, "      ");
        json << ",\n"
             << "      \"torus_radius_groups\": ";
        append_torus_radius_groups(json, body.torus_radius_groups, "      ");
        json << ",\n"
             << "      \"thickness_analysis\": ";
        append_thickness_analysis(json, body.thickness_analysis, "      ");
        json << ",\n"
             << "      \"radius_pair_analysis\": ";
        append_radius_pair_analysis(json, body.radius_pair_analysis, "      ");
        json << "\n    }" << (index + 1 < bodies.size() ? "," : "") << "\n";
    }
    json << "  ],\n"
         << "  \"axial_features\": ";
    append_axial_features(json, axial_features);
    json << ",\n"
         << "  \"stud_features\": ";
    append_stud_features(json, stud_features);
    json << ",\n"
         << "  \"thread_features\": ";
    append_thread_features(json, thread_features);
    json << ",\n"
         << "  \"chamfer_features\": ";
    append_chamfer_features(json, chamfer_features);
    json << ",\n"
         << "  \"hole_axis_groups\": ";
    append_hole_axis_groups(json, hole_axis_groups);
    json << ",\n"
         << "  \"hole_patterns\": ";
    append_hole_patterns(json, hole_patterns);
    json << ",\n"
         << "  \"datum_dimensions\": ";
    append_datum_dimensions(json, datum_dimensions);
    json << ",\n"
         << "  \"planar_openings\": ";
    append_planar_openings(json, planar_openings);
    json << ",\n"
         << "  \"thickness_analysis\": ";
    append_thickness_analysis(json, thickness_analysis, "  ");
    json << ",\n"
         << "  \"radius_pair_analysis\": ";
    append_radius_pair_analysis(json, radius_pair_analysis, "  ");
    json << ",\n"
         << "  \"measurements\": {\n"
         << "    \"surface_area\": " << surface_area << ",\n"
         << "    \"volume\": " << volume << ",\n"
         << "    \"center_of_mass\": ["
         << center_of_mass.x << ", "
         << center_of_mass.y << ", "
         << center_of_mass.z << "],\n"
         << "    \"bounding_box\": {\n"
         << "      \"min\": [" << bounds.x_min << ", " << bounds.y_min << ", " << bounds.z_min << "],\n"
         << "      \"max\": [" << bounds.x_max << ", " << bounds.y_max << ", " << bounds.z_max << "],\n"
         << "      \"size\": ["
         << bounds.x_max - bounds.x_min << ", "
         << bounds.y_max - bounds.y_min << ", "
         << bounds.z_max - bounds.z_min << "]\n"
         << "    }\n"
         << "  }\n"
         << "}\n";
    return json.str();
}

void write_text(const fs::path& output, const std::string& content) {
    const fs::path parent = output.parent_path();
    if (!parent.empty()) {
        fs::create_directories(parent);
    }

    std::ofstream stream(output, std::ios::binary);
    if (!stream) {
        throw std::runtime_error("Cannot open output file: " + output.string());
    }
    stream << content;
    if (!stream) {
        throw std::runtime_error("Cannot write output file: " + output.string());
    }
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 2 || argc > 3) {
        std::cerr << "Usage: occt-analyzer <input.step> [output.json]\n";
        return 2;
    }

    try {
        const fs::path input = argv[1];
        const fs::path output = argc == 3 ? fs::path(argv[2]) : fs::path();

        if (!fs::is_regular_file(input)) {
            throw std::runtime_error("Input STEP file does not exist: " + input.string());
        }

        Interface_Static::SetCVal("xstep.cascade.unit", "MM");

        STEPControl_Reader reader;
        const IFSelect_ReturnStatus status = reader.ReadFile(input.string().c_str());
        if (status != IFSelect_RetDone) {
            throw std::runtime_error("OCCT could not read the STEP file");
        }

        const Standard_Integer transferred = reader.TransferRoots();
        if (transferred <= 0) {
            throw std::runtime_error("STEP file contains no transferable roots");
        }

        const TopoDS_Shape shape = reader.OneShape();
        if (shape.IsNull()) {
            throw std::runtime_error("STEP transfer produced an empty shape");
        }

        Counts counts;
        counts.solids = count_subshapes(shape, TopAbs_SOLID);
        counts.shells = count_subshapes(shape, TopAbs_SHELL);
        counts.faces = count_subshapes(shape, TopAbs_FACE);
        counts.edges = count_subshapes(shape, TopAbs_EDGE);
        counts.vertices = count_subshapes(shape, TopAbs_VERTEX);

        const SurfaceCounts surface_counts = classify_surfaces(shape);
        const std::vector<BodySurfaceSummary> bodies = analyze_bodies(shape);
        const std::vector<CylinderPatch> full_cylinder_patches =
            collect_full_cylinder_patches(shape);
        const std::vector<AxialFeature> axial_features =
            build_axial_features(full_cylinder_patches);
        const std::vector<StudFeature> stud_features =
            build_stud_features(axial_features);
        const std::vector<ThreadFeature> thread_features =
            build_thread_features(stud_features);
        const std::vector<ChamferFeature> chamfer_features =
            build_chamfer_features(stud_features);
        const std::vector<HoleAxisGroup> hole_axis_groups =
            build_hole_axis_groups(axial_features);
        const std::vector<HolePattern> hole_patterns =
            build_hole_patterns(hole_axis_groups);
        const std::vector<PlanarOpening> planar_openings =
            collect_planar_openings(shape);
        const ThicknessAnalysis thickness_analysis = analyze_thickness(shape);
        std::vector<CylinderRadiusGroup> global_cylinder_groups;
        std::vector<TorusRadiusGroup> global_torus_groups;
        analyze_radius_groups(
            shape,
            global_cylinder_groups,
            global_torus_groups);
        const RadiusPairAnalysis radius_pair_analysis = analyze_radius_pairs(
            shape,
            global_torus_groups,
            thickness_analysis);

        GProp_GProps surface_properties;
        BRepGProp::SurfaceProperties(shape, surface_properties);

        GProp_GProps volume_properties;
        BRepGProp::VolumeProperties(shape, volume_properties);

        const gp_Pnt center = volume_properties.CentreOfMass();
        const Point3 center_of_mass{center.X(), center.Y(), center.Z()};

        Bnd_Box box;
        BRepBndLib::AddOptimal(
            shape,
            box,
            Standard_False,
            Standard_False);
        if (box.IsVoid()) {
            throw std::runtime_error("Cannot calculate the model bounding box");
        }

        Bounds bounds;
        box.Get(
            bounds.x_min,
            bounds.y_min,
            bounds.z_min,
            bounds.x_max,
            bounds.y_max,
            bounds.z_max);

        const std::vector<DatumDimension> datum_dimensions =
            build_datum_dimensions(hole_axis_groups, bounds);

        const std::string result = make_json(
            input,
            counts,
            surface_counts,
            bodies,
            axial_features,
            stud_features,
            thread_features,
            chamfer_features,
            hole_axis_groups,
            hole_patterns,
            datum_dimensions,
            planar_openings,
            thickness_analysis,
            radius_pair_analysis,
            surface_properties.Mass(),
            volume_properties.Mass(),
            center_of_mass,
            bounds);

        if (!output.empty()) {
            write_text(output, result);
        }
        std::cout << result;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "ERROR: " << error.what() << '\n';
        return 1;
    }
}
