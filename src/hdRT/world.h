// HoudiniRT: immutable object-space meshes and a cached visible scene census.
// Execute copies shared mesh payloads and combines bounds; it never flattens geometry
// or touches Vulkan. The render thread applies revisions to persistent mesh resources.
#ifndef HDRT_WORLD_H
#define HDRT_WORLD_H

#include "pxr/pxr.h"
#include "scene.h"

#include <cstddef>
#include <memory>

PXR_NAMESPACE_OPEN_SCOPE

struct HdRTWorldScene
{
    std::map<SdfPath, std::shared_ptr<const HdRTMeshData>> meshes;
    std::map<SdfPath, std::shared_ptr<const HdRTMaterialData>> materials;
    std::vector<HdRTVkLight> lights;
    uint64_t contentVersion = 0;
    float dome[3] = {0.0f, 0.0f, 0.0f};
    HdRTCensus census;
    size_t vertexCount = 0;
    size_t triangleCount = 0;

    size_t VertexCount() const { return vertexCount; }
    size_t TriangleCount() const { return triangleCount; }
};

/// Recompute only after this mesh's geometry or transform changes, never per scene edit.
void HdRTUpdateMeshCensus(HdRTMeshData* mesh);

/// O(mesh count), independent of the number of triangles in unchanged meshes.
std::shared_ptr<const HdRTWorldScene> HdRTBuildScene(HdRTSceneSnapshot const& snapshot);

/// Resolve immutable material data without modifying mesh geometry or versions.
/// A false result leaves the legacy displayColor Lambert path in use.
bool HdRTResolveMeshMaterial(const HdRTWorldScene& world, const HdRTMeshData& mesh,
                             HdRTVkMaterial* material, std::string* reason);

/// Compatibility spelling for existing callers; no geometry is flattened.
inline std::shared_ptr<const HdRTWorldScene> HdRTFlattenScene(HdRTSceneSnapshot const& snapshot)
{
    return HdRTBuildScene(snapshot);
}

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_WORLD_H
