// HoudiniRT: normal interpolation follows Hydra descriptors and HdMeshUtil's
// own triangulation (including holes and left-handed topology).
#include "meshNormals.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include <cmath>
#include <limits>

PXR_NAMESPACE_OPEN_SCOPE

HdRTNormalSource HdRTReadNormals(HdSceneDelegate* delegate, const SdfPath& id)
{
    const TfToken prefixed("primvars:normals");
    HdPrimvarDescriptor selected;
    int priority = 0;
    for (int i = HdInterpolationConstant; i < HdInterpolationCount; ++i) {
        for (const auto& descriptor : delegate->GetPrimvarDescriptors(id, HdInterpolation(i))) {
            const int rank = descriptor.name == prefixed ? 2 : descriptor.name == HdTokens->normals ? 1 : 0;
            if (rank > priority) {
                selected = descriptor;
                priority = rank;
            }
        }
    }
    HdRTNormalSource result;
    if (!priority)
        return result;
    result.present = true;
    result.indexed = selected.indexed;
    result.interpolation = selected.interpolation;
    result.values = selected.indexed
        ? delegate->GetIndexedPrimvar(id, selected.name, &result.indices)
        : delegate->Get(id, selected.name);
    return result;
}

bool HdRTBuildCornerNormals(const HdRTNormalSource& source, const HdMeshTopology& topology,
                           const VtVec3iArray& triangles, const VtIntArray& faceIds,
                           size_t pointCount, const SdfPath& id,
                           VtVec3fArray* output, std::string* error)
{
    output->clear();
    error->clear();
    if (!source.present)
        return true;
    auto fail = [&](const char* reason) { *error = reason; return false; };
    size_t expected = 0;
    switch (source.interpolation) {
    case HdInterpolationConstant: expected = 1; break;
    case HdInterpolationUniform: expected = topology.GetFaceVertexCounts().size(); break;
    case HdInterpolationVertex:
    case HdInterpolationVarying: expected = pointCount; break;
    case HdInterpolationFaceVarying: expected = topology.GetFaceVertexIndices().size(); break;
    default: return fail("unsupported normal interpolation");
    }
    VtVec3fArray values;
    if (source.values.IsHolding<VtVec3fArray>())
        values = source.values.UncheckedGet<VtVec3fArray>();
    else if (source.values.IsHolding<GfVec3f>() && source.interpolation == HdInterpolationConstant)
        values = VtVec3fArray{source.values.UncheckedGet<GfVec3f>()};
    else
        return fail("normals must be float3 values");
    if (source.indexed) {
        if (source.indices.size() != expected)
            return fail("normal index count does not match its interpolation domain");
        VtVec3fArray expanded(expected);
        for (size_t i = 0; i < expected; ++i) {
            const int index = source.indices[i];
            if (index < 0 || size_t(index) >= values.size())
                return fail("normal index is outside the authored value array");
            expanded[i] = values[size_t(index)];
        }
        values = std::move(expanded);
    } else if (values.size() != expected) {
        return fail("normal value count does not match its interpolation domain");
    }
    for (const GfVec3f& normal : values) {
        const double length2 = double(normal[0]) * normal[0] + double(normal[1]) * normal[1]
                             + double(normal[2]) * normal[2];
        if (!std::isfinite(length2) || length2 <= 1.0e-24 || length2 > std::numeric_limits<float>::max())
            return fail("normals contain non-finite, zero-length or unrepresentable vectors");
    }
    if (triangles.size() > size_t(std::numeric_limits<int>::max()) / 3)
        return fail("triangulated normal count exceeds the supported range");
    VtVec3fArray corners;
    if (source.interpolation == HdInterpolationFaceVarying) {
        if (values.size() > size_t(std::numeric_limits<int>::max()))
            return fail("face-varying normal count exceeds the supported range");
        HdMeshUtil meshUtil(&topology, id);
        VtValue triangulated;
        const auto result = meshUtil.ComputeTriangulatedFaceVaryingPrimvar(
            values.cdata(), int(values.size()), HdTypeFloatVec3, &triangulated);
        if (result == HdMeshComputationResult::Unchanged)
            corners = values;
        else if (result == HdMeshComputationResult::Success && triangulated.IsHolding<VtVec3fArray>())
            corners = triangulated.UncheckedGet<VtVec3fArray>();
        else
            return fail("face-varying normal triangulation failed");
    } else {
        if (source.interpolation == HdInterpolationUniform && faceIds.size() != triangles.size())
            return fail("triangle-to-authored-face mapping is unavailable");
        corners.resize(triangles.size() * 3);
        for (size_t i = 0; i < triangles.size(); ++i) {
            for (int k = 0; k < 3; ++k) {
                const int index = source.interpolation == HdInterpolationConstant ? 0
                    : source.interpolation == HdInterpolationUniform ? faceIds[i] : triangles[i][k];
                if (index < 0 || size_t(index) >= values.size())
                    return fail("triangulated normal lookup is outside its interpolation domain");
                corners[3 * i + k] = values[size_t(index)];
            }
        }
    }
    if (corners.size() != triangles.size() * 3)
        return fail("triangulated normal count does not match the retained triangles");
    *output = std::move(corners);
    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
