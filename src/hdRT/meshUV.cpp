#include "meshUV.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include <cmath>
#include <limits>
PXR_NAMESPACE_OPEN_SCOPE
HdRTUVSource HdRTReadUV(HdSceneDelegate* delegate, const SdfPath& id)
{
    HdRTUVSource result;
    HdPrimvarDescriptor selected;
    int priority = 0;
    for (int i=HdInterpolationConstant; i<HdInterpolationCount; ++i) {
        for (const auto& descriptor : delegate->GetPrimvarDescriptors(id,HdInterpolation(i))) {
            const int rank = descriptor.name == TfToken("st") ? 2
                : descriptor.name == TfToken("primvars:st") ? 1 : 0;
            if (rank > priority) { selected = descriptor; priority = rank; }
        }
    }
    if (!priority) return result;
    result.present = true;
    result.indexed = selected.indexed;
    result.interpolation = selected.interpolation;
    result.values = selected.indexed ? delegate->GetIndexedPrimvar(id,selected.name,&result.indices)
                                     : delegate->Get(id,selected.name);
    return result;
}
bool HdRTBuildCornerUV(const HdRTUVSource& source, const HdMeshTopology& topology,
                      const VtVec3iArray& triangles, const VtIntArray& faceIds, size_t pointCount,
                      const SdfPath& id, VtVec2fArray* output, std::string* error)
{
    output->clear(); error->clear();
    auto fail = [&](const char* reason) { *error = reason; return false; };
    if (!source.present) return fail("st UV primvar is missing");
    size_t expected = 0;
    switch (source.interpolation) {
    case HdInterpolationConstant: expected = 1; break;
    case HdInterpolationUniform: expected = topology.GetFaceVertexCounts().size(); break;
    case HdInterpolationVertex:
    case HdInterpolationVarying: expected = pointCount; break;
    case HdInterpolationFaceVarying: expected = topology.GetFaceVertexIndices().size(); break;
    default: return fail("unsupported st interpolation");
    }
    VtVec2fArray values;
    if (source.values.IsHolding<VtVec2fArray>()) values = source.values.UncheckedGet<VtVec2fArray>();
    else if (source.values.IsHolding<VtVec3fArray>()) {
        const auto& input = source.values.UncheckedGet<VtVec3fArray>();
        values.resize(input.size());
        for (size_t i=0; i<input.size(); ++i) {
            if (!std::isfinite(input[i][2])) return fail("st contains non-finite float3 values");
            values[i] = GfVec2f(input[i][0],input[i][1]);
        }
    } else if (source.interpolation == HdInterpolationConstant && source.values.IsHolding<GfVec2f>())
        values = VtVec2fArray{source.values.UncheckedGet<GfVec2f>()};
    else if (source.interpolation == HdInterpolationConstant && source.values.IsHolding<GfVec3f>()) {
        const auto v = source.values.UncheckedGet<GfVec3f>();
        if (!std::isfinite(v[2])) return fail("st contains non-finite float3 values");
        values = VtVec2fArray{GfVec2f(v[0],v[1])};
    } else return fail("st must contain float2 or float3 values");
    if (source.indexed) {
        if (source.indices.size() != expected) return fail("st index count does not match its interpolation domain");
        VtVec2fArray expanded(expected);
        for (size_t i=0; i<expected; ++i) {
            const int index = source.indices[i];
            if (index < 0 || size_t(index) >= values.size()) return fail("st index is outside its value array");
            expanded[i] = values[size_t(index)];
        }
        values = std::move(expanded);
    } else if (values.size() != expected) return fail("st value count does not match its interpolation domain");
    for (const auto& v : values)
        if (!std::isfinite(v[0]) || !std::isfinite(v[1])) return fail("st contains non-finite values");
    if (triangles.size() > size_t(std::numeric_limits<int>::max())/3)
        return fail("triangulated st count exceeds the supported range");
    VtVec2fArray corners;
    if (source.interpolation == HdInterpolationFaceVarying) {
        if (values.size() > size_t(std::numeric_limits<int>::max())) return fail("st domain is too large");
        HdMeshUtil util(&topology,id);
        VtValue triangulated;
        const auto result = util.ComputeTriangulatedFaceVaryingPrimvar(
            values.cdata(),int(values.size()),HdTypeFloatVec2,&triangulated);
        if (result == HdMeshComputationResult::Unchanged) corners = values;
        else if (result == HdMeshComputationResult::Success && triangulated.IsHolding<VtVec2fArray>())
            corners = triangulated.UncheckedGet<VtVec2fArray>();
        else return fail("face-varying st triangulation failed");
    } else {
        if (source.interpolation == HdInterpolationUniform && faceIds.size() != triangles.size())
            return fail("triangle-to-authored-face mapping is unavailable for st");
        corners.resize(triangles.size()*3);
        for (size_t i=0; i<triangles.size(); ++i) for (int k=0; k<3; ++k) {
            const int index = source.interpolation == HdInterpolationConstant ? 0
                : source.interpolation == HdInterpolationUniform ? faceIds[i] : triangles[i][k];
            if (index < 0 || size_t(index) >= values.size()) return fail("triangulated st lookup is outside its domain");
            corners[3*i+k] = values[size_t(index)];
        }
    }
    if (corners.size() != triangles.size()*3) return fail("triangulated st count does not match retained triangles");
    *output = std::move(corners);
    return true;
}
PXR_NAMESPACE_CLOSE_SCOPE
