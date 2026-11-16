#include "world.h"
#include "pxr/base/gf/rotation.h"
#include <cassert>
#include <cmath>
#include <iostream>
#undef assert
#define assert(test) do { if (!(test)) { std::cerr << "FAILED: " #test << std::endl; return 1; } } while (false)
PXR_NAMESPACE_USING_DIRECTIVE
int main()
{
    auto mesh = std::make_shared<HdRTMeshData>();
    mesh->points = VtVec3fArray{GfVec3f(0,0,0), GfVec3f(2,0,0), GfVec3f(0,1,0), GfVec3f(100,100,100)};
    mesh->triangles = VtVec3iArray{GfVec3i(0,1,2)};
    mesh->geometryVersion = HdRTNextMeshVersion();
    mesh->topologyVersion = HdRTNextMeshVersion();
    mesh->transform.SetRotate(GfRotation(GfVec3d(0,0,1), 90));
    HdRTUpdateMeshCensus(mesh.get());
    assert(mesh->census.meshes == 1 && mesh->census.triangles == 1);
    assert(std::abs(mesh->census.bboxMin[0] + 1) < 1e-10);
    assert(std::abs(mesh->census.bboxMax[0]) < 1e-10);
    assert(std::abs(mesh->census.bboxMax[1] - 2) < 1e-10);
    assert(std::abs(mesh->census.bboxMax[2]) < 1e-10);
    HdRTScene scene;
    const SdfPath id("/mesh");
    scene.SetMesh(id, mesh);
    auto original = scene.Snapshot();
    auto world = HdRTBuildScene(original);
    assert(world->census.meshes == 1 && world->TriangleCount() == 1);
    assert(world->meshes.at(id).get() == mesh.get());
    scene.SetMesh(id, mesh);
    assert(scene.Snapshot().contentVersion == original.contentVersion);
    auto hidden = std::make_shared<HdRTMeshData>(*mesh);
    hidden->visible = false;
    scene.SetMesh(id, hidden);
    auto hiddenWorld = HdRTBuildScene(scene.Snapshot());
    assert(hiddenWorld->census.meshes == 0 && !hiddenWorld->census.hasBox);
    assert(hiddenWorld->TriangleCount() == 0 && hiddenWorld->VertexCount() == 0);
    assert(original.meshes.at(id)->visible);
    assert(hidden->points.cdata() == mesh->points.cdata());
    assert(hidden->geometryVersion == mesh->geometryVersion);
    HdRTDomeData dome;
    dome.radiance = GfVec3f(1,2,3);
    scene.SetDome(SdfPath("/dome"), dome);
    const auto domeVersion = scene.Snapshot().contentVersion;
    scene.SetDome(SdfPath("/dome"), dome);
    assert(scene.Snapshot().contentVersion == domeVersion);
    assert(HdRTBuildScene(scene.Snapshot())->dome[1] == 2);
    scene.RemoveMesh(id);
    const auto removedVersion = scene.Snapshot().contentVersion;
    scene.RemoveMesh(id);
    scene.RemoveDome(SdfPath("/absent"));
    assert(scene.Snapshot().contentVersion == removedVersion);
    auto replacement = std::make_shared<HdRTMeshData>(*mesh);
    replacement->geometryVersion = HdRTNextMeshVersion();
    replacement->topologyVersion = HdRTNextMeshVersion();
    scene.SetMesh(id, replacement);
    assert(replacement->geometryVersion > mesh->topologyVersion);
    assert(HdRTBuildScene(scene.Snapshot())->census.meshes == 1);
    assert(original.meshes.at(id)->geometryVersion == mesh->geometryVersion);
    std::cout << "CPU scene smoke passed: exact bounds, visibility, immutable snapshots, no-op publication, fresh versions\n";
}
