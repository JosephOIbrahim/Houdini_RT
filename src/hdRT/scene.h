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
#include "vkFurnace.h"
#include "material.h"

#include <cstdint>
#include <cstring>
#include <atomic>
#include <map>
#include <memory>
#include <mutex>

PXR_NAMESPACE_OPEN_SCOPE

/// Exact world bounds over points referenced by triangles; combined over visible meshes.
struct HdRTCensus
{
    int meshes = 0;
    int triangles = 0;
    bool hasBox = false;
    double bboxMin[3] = {0.0, 0.0, 0.0};
    double bboxMax[3] = {0.0, 0.0, 0.0};
};

/// A new rprim at a reused SdfPath must never look like its deleted predecessor.
inline uint64_t HdRTNextMeshVersion()
{
    static std::atomic<uint64_t> version{0};
    return version.fetch_add(1, std::memory_order_relaxed) + 1;
}

/// One mesh as Hydra delivered it, triangulated and with smooth normals, still in object space.
struct HdRTMeshData
{
    VtVec3fArray points;        ///< object-space positions, as authored
    VtVec3fArray normals;       ///< object-space smooth vertex normals (Hd_SmoothNormals), one per point
    VtVec3fArray cornerNormals; ///< authored shading normals, three per triangle; empty uses smooth normals
    VtVec3iArray triangles;     ///< HdMeshUtil::ComputeTriangleIndices, indices into points
    VtIntArray faceIds;         ///< authored polygon index for each triangulated primitive
    int primId = -1;            ///< Hydra selection ID, never a transient GPU slot
    GfMatrix4d   transform{1.0}; ///< object to world
    GfVec3f      albedo{0.18f}; ///< constant displayColor; 18% grey when none is authored
    SdfPath materialId;         ///< full-purpose binding, resolved by Hydra
    bool materialSubsetsUnsupported = false;
    bool         visible = true;
    uint64_t geometryVersion = 0; ///< changes only with positions or topology
    uint64_t topologyVersion = 0; ///< changes only with topology (and first publication)
    uint64_t normalVersion = 0;   ///< independently tracks shading data without rebuilding a BLAS
    HdRTCensus census;            ///< cached even while hidden; no per-frame vertex walk
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
    uint64_t contentVersion = 0;   ///< moves on actual mesh, light or material changes
    std::map<SdfPath, std::shared_ptr<const HdRTMeshData>> meshes;
    std::map<SdfPath, HdRTDomeData> domes;
    std::map<SdfPath, HdRTVkLight> lights;
    std::map<SdfPath, std::shared_ptr<const HdRTMaterialData>> materials;
};

class HdRTScene
{
public:
    void SetMaterial(const SdfPath& id, std::shared_ptr<const HdRTMaterialData> material)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        const auto found = _snapshot.materials.find(id);
        if (found != _snapshot.materials.end() && *found->second == *material)
            return;
        _snapshot.materials[id] = std::move(material);
        ++_snapshot.contentVersion;
    }

    void RemoveMaterial(const SdfPath& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_snapshot.materials.erase(id)) ++_snapshot.contentVersion;
    }

    void SetMesh(SdfPath const& id, std::shared_ptr<const HdRTMeshData> mesh)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        auto found = _snapshot.meshes.find(id);
        if (found != _snapshot.meshes.end() && found->second == mesh)
            return;
        _snapshot.meshes[id] = std::move(mesh);
        ++_snapshot.contentVersion;
    }

    void RemoveMesh(SdfPath const& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_snapshot.meshes.erase(id))
            ++_snapshot.contentVersion;
    }

    void SetDome(SdfPath const& id, HdRTDomeData const& dome)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        auto found = _snapshot.domes.find(id);
        if (found != _snapshot.domes.end() && found->second.radiance == dome.radiance &&
            found->second.visible == dome.visible)
            return;
        _snapshot.domes[id] = dome;
        ++_snapshot.contentVersion;
    }

    void RemoveDome(SdfPath const& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_snapshot.domes.erase(id))
            ++_snapshot.contentVersion;
    }

    void SetLight(SdfPath const& id, HdRTVkLight const& light)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        const auto found = _snapshot.lights.find(id);
        if (found != _snapshot.lights.end() &&
            std::memcmp(&found->second, &light, sizeof(light)) == 0)
            return;
        _snapshot.lights[id] = light;
        ++_snapshot.contentVersion;
    }

    void RemoveLight(SdfPath const& id)
    {
        std::lock_guard<std::mutex> lock(_mutex);
        if (_snapshot.lights.erase(id)) ++_snapshot.contentVersion;
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
