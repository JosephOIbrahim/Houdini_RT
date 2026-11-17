#ifndef HDRT_MESH_UV_H
#define HDRT_MESH_UV_H
#include "pxr/pxr.h"
#include "pxr/base/gf/vec2f.h"
#include "pxr/base/vt/types.h"
#include "pxr/base/vt/value.h"
#include "pxr/imaging/hd/meshTopology.h"
#include "pxr/imaging/hd/types.h"
#include "pxr/usd/sdf/path.h"
#include <string>
PXR_NAMESPACE_OPEN_SCOPE
class HdSceneDelegate;
struct HdRTUVSource
{
    VtValue values;
    VtIntArray indices;
    HdInterpolation interpolation = HdInterpolationConstant;
    bool present = false, indexed = false;
    bool operator==(const HdRTUVSource& rhs) const
    { return present == rhs.present && indexed == rhs.indexed && interpolation == rhs.interpolation
          && values == rhs.values && indices == rhs.indices; }
};
HdRTUVSource HdRTReadUV(HdSceneDelegate*, const SdfPath&);
/// Convert st float2 or float3.xy to three values per retained triangle.
/// Empty output is missing/invalid UVs; it never changes geometry or normals.
bool HdRTBuildCornerUV(const HdRTUVSource&, const HdMeshTopology&,
                      const VtVec3iArray&, const VtIntArray& faceIds, size_t pointCount,
                      const SdfPath&, VtVec2fArray*, std::string* error);
PXR_NAMESPACE_CLOSE_SCOPE
#endif
