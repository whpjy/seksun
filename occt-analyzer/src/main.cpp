#include <Bnd_Box.hxx>
#include <BRepBndLib.hxx>
#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <IFSelect_ReturnStatus.hxx>
#include <Interface_Static.hxx>
#include <STEPControl_Reader.hxx>
#include <TopAbs_ShapeEnum.hxx>
#include <TopExp.hxx>
#include <TopTools_IndexedMapOfShape.hxx>
#include <TopoDS_Shape.hxx>

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>

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

int count_subshapes(const TopoDS_Shape& shape, TopAbs_ShapeEnum kind) {
    TopTools_IndexedMapOfShape unique_shapes;
    TopExp::MapShapes(shape, kind, unique_shapes);
    return unique_shapes.Extent();
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
    double surface_area,
    double volume,
    const Bounds& bounds) {
    std::ostringstream json;
    json << std::fixed << std::setprecision(6);
    json << "{\n"
         << "  \"schema_version\": \"0.1.0\",\n"
         << "  \"source_file\": \"" << json_escape(input.filename().string()) << "\",\n"
         << "  \"units\": {\"length\": \"mm\", \"area\": \"mm^2\", \"volume\": \"mm^3\"},\n"
         << "  \"topology\": {\n"
         << "    \"solids\": " << counts.solids << ",\n"
         << "    \"shells\": " << counts.shells << ",\n"
         << "    \"faces\": " << counts.faces << ",\n"
         << "    \"edges\": " << counts.edges << ",\n"
         << "    \"vertices\": " << counts.vertices << "\n"
         << "  },\n"
         << "  \"measurements\": {\n"
         << "    \"surface_area\": " << surface_area << ",\n"
         << "    \"volume\": " << volume << ",\n"
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

        GProp_GProps surface_properties;
        BRepGProp::SurfaceProperties(shape, surface_properties);

        GProp_GProps volume_properties;
        BRepGProp::VolumeProperties(shape, volume_properties);

        Bnd_Box box;
        BRepBndLib::Add(shape, box, Standard_True);
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

        const std::string result = make_json(
            input,
            counts,
            surface_properties.Mass(),
            volume_properties.Mass(),
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
