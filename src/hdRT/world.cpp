// world.cpp - exact per-mesh bounds cached at publication, no world-space flattening.
#include "world.h"
#include "pxr/base/gf/vec3d.h"
#include <algorithm>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

bool HdRTResolveMeshMaterial(const HdRTWorldScene& world, const HdRTMeshData& mesh,
                             HdRTVkMaterial* material, std::string* reason,
                             const HdRTMaterialData** data)
{
    if (data) *data = nullptr;
    *material = HdRTVkMaterial();
    reason->clear();
    if (mesh.materialSubsetsUnsupported) {
        *reason = "per-face material bindings are outside the whole-mesh material profile";
        return false;
    }
    if (mesh.materialId.IsEmpty()) return false;
    const auto found = world.materials.find(mesh.materialId);
    if (found == world.materials.end()) {
        *reason = "bound material is unavailable: " + mesh.materialId.GetString();
        return false;
    }
    if (!found->second->supported) {
        *reason = mesh.materialId.GetString() + ": " + found->second->reason;
        return false;
    }
    for (const auto& texture : found->second->textures) {
        if (texture.enabled && texture.usesUV && (!mesh.uvReason.empty() ||
            mesh.cornerUVs.size() != mesh.triangles.size()*3)) {
            *reason = mesh.materialId.GetString() + ": " +
                (mesh.uvReason.empty() ? "st UV data is unavailable" : mesh.uvReason);
            return false;
        }
    }
    *material = found->second->parameters;
    if (data) *data = found->second.get();
    return true;
}

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
    world->materials = snapshot.materials;
    world->domes = snapshot.domes;
    world->contentVersion = snapshot.contentVersion;
    for (const auto& entry : snapshot.lights) world->lights.push_back(entry.second);
    for (const auto& entry : snapshot.domes) {
        if (entry.second.visible && entry.second.supported && !entry.second.hasTexture)
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
