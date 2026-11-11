//
// HoudiniRT (2026). The CPU-side scene that Hydra prims publish into during Sync().
//
// Rprims and sprims write here on the app thread (possibly several at once: Hydra may sync rprims in
// parallel), and the render pass reads a consistent copy of it in Execute(). Nothing here touches Vulkan:
// acceleration structures are built on the render thread from the snapshot (CLAUDE.md:30).
//
#ifndef HDRT_SCENE_H
#define HDRT_SCENE_H

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec3i.h"
#include "pxr/base/vt/array.h"
#include "pxr/base/vt/types.h"
#include "pxr/usd/sdf/path.h"

#include <map>
#include <memory>
#include <mutex>

PXR_NAMESPACE_OPEN_SCOPE

/// One mesh as Hydra delivered it, triangulated and with smooth normals, still in object space.
struct HdRTMeshData
{
    VtVec3fArray points;        ///< object-space positions, as authored
    VtVec3fArray normals;       ///< object-space smooth vertex normals (Hd_SmoothNormals), one per point
    VtVec3iArray triangles;     ///< HdMeshUtil::ComputeTriangleIndices, indices into points
    GfMatrix4d   transform;     ///< object to world
    GfVec3f      albedo{0.18f}; ///< constant displayColor; 18% grey when none is authored
    bool         visible = true;
};

/// One uniform (untextured) dome light: radiance = intensity * 2^exposure * color.
struct HdRTDomeData
{
    GfVec3f radiance{0.0f};
    bool    visible = true;
};

/// Everything the renderer needs from Hydra, copied out under the lock.
struct HdRTSceneSnapshot
{
    std::map<SdfPath, std::shared_ptr<const HdRTMeshData>> meshes;
    std::map<SdfPath, HdRTDomeData> domes;
};

class HdRTScene
{
public:
    void SetMesh(SdfPath const& id, std::shared_ptr<const HdRTMeshData> mesh)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _snapshot.meshes[id] = std::move(mesh);
    }

    void RemoveMesh(SdfPath const& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _snapshot.meshes.erase(id);
    }

    void SetDome(SdfPath const& id, HdRTDomeData const& dome)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _snapshot.domes[id] = dome;
    }

    void RemoveDome(SdfPath const& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _snapshot.domes.erase(id);
    }

    /// A consistent copy. Mesh payloads are shared, immutable and cheap to copy.
    HdRTSceneSnapshot Snapshot() const
    {
        std::lock_guard<std::mutex> lock(_mutex);
        return _snapshot;
    }

private:
    mutable std::mutex _mutex;
    HdRTSceneSnapshot  _snapshot;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_SCENE_H
