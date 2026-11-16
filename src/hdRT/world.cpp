// world.cpp - exact per-mesh bounds cached at publication, no world-space flattening.
#include "world.h"
#include "pxr/base/gf/vec3d.h"
#include <algorithm>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

void HdRTUpdateMeshCensus(HdRTMeshData* mesh)
{
    HdRTCensus result;
    result.meshes = 1;
    result.triangles = int(mesh->triangles.size());
    std::vector<unsigned char> used(mesh->points.size(), 0);
    for (const GfVec3i& triangle : mesh->triangles) {
        for (int k = 0; k < 3; ++k) {
            if (triangle[k] >= 0 && size_t(triangle[k]) < used.size())
                used[size_t(triangle[k])] = 1;
        }
    }
    for (size_t i = 0; i < used.size(); ++i) {
        if (!used[i])
            continue;
        const GfVec3f& p = mesh->points[i];
        const GfVec3d world = mesh->transform.Transform(GfVec3d(p[0], p[1], p[2]));
        for (int k = 0; k < 3; ++k) {
            if (!result.hasBox)
                result.bboxMin[k] = result.bboxMax[k] = world[k];
            else {
                result.bboxMin[k] = std::min(result.bboxMin[k], world[k]);
                result.bboxMax[k] = std::max(result.bboxMax[k], world[k]);
            }
        }
        result.hasBox = true;
    }
    mesh->census = result;
}

std::shared_ptr<const HdRTWorldScene> HdRTBuildScene(HdRTSceneSnapshot const& snapshot)
{
    auto world = std::make_shared<HdRTWorldScene>();
    world->meshes = snapshot.meshes;
    world->contentVersion = snapshot.contentVersion;
    for (const auto& entry : snapshot.domes) {
        if (entry.second.visible)
            for (int k = 0; k < 3; ++k)
                world->dome[k] += entry.second.radiance[k];
    }
    for (const auto& entry : snapshot.meshes) {
        const HdRTMeshData& mesh = *entry.second;
        if (!mesh.visible)
            continue;
        world->vertexCount += mesh.points.size();
        world->triangleCount += mesh.triangles.size();
        world->census.meshes += mesh.census.meshes;
        world->census.triangles += mesh.census.triangles;
        if (!mesh.census.hasBox)
            continue;
        for (int k = 0; k < 3; ++k) {
            world->census.bboxMin[k] = world->census.hasBox
                ? std::min(world->census.bboxMin[k], mesh.census.bboxMin[k]) : mesh.census.bboxMin[k];
            world->census.bboxMax[k] = world->census.hasBox
                ? std::max(world->census.bboxMax[k], mesh.census.bboxMax[k]) : mesh.census.bboxMax[k];
        }
        world->census.hasBox = true;
    }
    return world;
}

PXR_NAMESPACE_CLOSE_SCOPE
