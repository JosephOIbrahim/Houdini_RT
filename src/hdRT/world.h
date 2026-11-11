//
// HoudiniRT (2026). The scene flattened to world space, ready for one acceleration structure.
//
// The render pass builds it in Execute(), on the app thread, from the HdRTScene snapshot whenever the
// scene version moves; the renderer takes it while the render thread is stopped and uploads it on the
// render thread (C5). Building it makes no Vulkan call (CLAUDE.md:30).
//
// Every mesh transform is applied here, in double precision: positions by the matrix, normals by its
// inverse transpose. A single flattened mesh needs no instance transforms on the GPU, so the
// row-major GfMatrix4d never has to be transposed into a VkTransformMatrixKHR.
//
#ifndef HDRT_WORLD_H
#define HDRT_WORLD_H

#include "pxr/pxr.h"

#include "scene.h"

#include <cstdint>
#include <memory>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

/// What the delegate synced, measured the way verify/s4_firstlight.sh measures the stage: every mesh
/// counts, triangles are HdMeshUtil's fan triangles, the box covers every point a triangle uses.
struct HdRTCensus
{
    int    meshes = 0;
    int    triangles = 0;
    bool   hasBox = false;
    double bboxMin[3] = { 0.0, 0.0, 0.0 };
    double bboxMax[3] = { 0.0, 0.0, 0.0 };
};

struct HdRTWorldScene
{
    std::vector<float>    positions;   ///< xyz per vertex, world space
    std::vector<float>    normals;     ///< xyz per vertex, world space, unit length
    std::vector<uint32_t> indices;     ///< 3 per triangle, into the vertex arrays above
    std::vector<float>    albedo;      ///< rgba per triangle (a = 1)
    float                 dome[3] = { 0.0f, 0.0f, 0.0f };   ///< summed radiance of the visible domes
    HdRTCensus            census;

    size_t VertexCount() const { return positions.size() / 3; }
    size_t TriangleCount() const { return indices.size() / 3; }
};

/// Flattens the visible meshes into one world-space mesh and takes the census of every synced mesh.
std::shared_ptr<const HdRTWorldScene> HdRTFlattenScene(HdRTSceneSnapshot const& snapshot);

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_WORLD_H
