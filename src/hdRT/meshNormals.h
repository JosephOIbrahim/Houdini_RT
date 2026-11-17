// HoudiniRT: validated authored-normal sampling in the authored mesh domains.
#ifndef HDRT_MESH_NORMALS_H
#define HDRT_MESH_NORMALS_H

#include "pxr/pxr.h"
#include "pxr/base/vt/types.h"
#include "pxr/base/vt/value.h"
#include "pxr/imaging/hd/meshTopology.h"
#include "pxr/imaging/hd/types.h"
#include "pxr/usd/sdf/path.h"
#include <string>

PXR_NAMESPACE_OPEN_SCOPE
class HdSceneDelegate;

struct HdRTNormalSource
{
    VtValue values;
    VtIntArray indices;
    HdInterpolation interpolation = HdInterpolationConstant;
    bool present = false;
    bool indexed = false;

    bool operator==(const HdRTNormalSource& rhs) const
    {
        return present == rhs.present && indexed == rhs.indexed
            && interpolation == rhs.interpolation && values == rhs.values && indices == rhs.indices;
    }
};

/// Prefer an explicitly exposed primvars:normals descriptor; otherwise use
/// normals, whose USD primvar/attribute precedence is resolved by the delegate.
HdRTNormalSource HdRTReadNormals(HdSceneDelegate* delegate, const SdfPath& id);

/// Output three object-space normals per retained triangle. No point splitting,
/// normalization, or transform changes. Empty output means use smooth fallback.
/// Malformed authored data returns false and a reason; absence is not an error.
bool HdRTBuildCornerNormals(const HdRTNormalSource& source, const HdMeshTopology& topology,
                           const VtVec3iArray& triangles, const VtIntArray& faceIds,
                           size_t pointCount, const SdfPath& id,
                           VtVec3fArray* output, std::string* error);

PXR_NAMESPACE_CLOSE_SCOPE
#endif
