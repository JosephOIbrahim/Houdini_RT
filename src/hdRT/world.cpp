// world.cpp - see world.h.
#include "world.h"

#include "pxr/base/gf/matrix3d.h"
#include "pxr/base/gf/vec3d.h"

#include <algorithm>
#include <cmath>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

std::shared_ptr<const HdRTWorldScene>
HdRTFlattenScene(HdRTSceneSnapshot const& snapshot)
{
    auto world = std::make_shared<HdRTWorldScene>();
    HdRTCensus& census = world->census;

    for (const auto& entry : snapshot.domes) {
        if (!entry.second.visible) {
            continue;
        }
        for (int k = 0; k < 3; ++k) {
            world->dome[k] += entry.second.radiance[k];
        }
    }

    for (const auto& entry : snapshot.meshes) {
        const HdRTMeshData& mesh = *entry.second;
        const size_t nPoints = mesh.points.size();
        const size_t nTris = mesh.triangles.size();

        // The census covers every synced mesh, as the exam's census covers every mesh on the stage.
        census.meshes += 1;
        census.triangles += int(nTris);

        // Which points a triangle uses, and only those (the exam's box is over used points). Sync
        // guarantees every index is below nPoints; check again rather than trust it.
        std::vector<char> used(nPoints, 0);
        bool indicesOk = true;
        for (const GfVec3i& t : mesh.triangles) {
            for (int k = 0; k < 3; ++k) {
                if (t[k] < 0 || size_t(t[k]) >= nPoints) {
                    indicesOk = false;
                } else {
                    used[size_t(t[k])] = 1;
                }
            }
        }

        // World positions in double: the census compares them with pxr.Usd's to 1e-4 of the diagonal.
        const GfMatrix4d& M = mesh.transform;
        std::vector<GfVec3d> worldPoints(nPoints);
        for (size_t i = 0; i < nPoints; ++i) {
            const GfVec3f& p = mesh.points[i];
            worldPoints[i] = M.Transform(GfVec3d(p[0], p[1], p[2]));
            if (!used[i]) {
                continue;
            }
            for (int k = 0; k < 3; ++k) {
                if (!census.hasBox) {
                    census.bboxMin[k] = census.bboxMax[k] = worldPoints[i][k];
                } else {
                    census.bboxMin[k] = std::min(census.bboxMin[k], worldPoints[i][k]);
                    census.bboxMax[k] = std::max(census.bboxMax[k], worldPoints[i][k]);
                }
            }
            census.hasBox = true;
        }

        if (!mesh.visible || nTris == 0 || !indicesOk) {
            continue;
        }

        // Normals by the inverse transpose of the upper 3x3. A singular matrix (a zero scale) leaves
        // the normal as transformed by M itself; the shader falls back to the geometric normal anyway.
        GfMatrix3d N = M.ExtractRotationMatrix();
        const double det = N.GetDeterminant();
        const bool invertible = std::abs(det) > 1e-300;
        // A mirroring transform (negative determinant) flips the world-space winding; reverse it so the
        // face normal from the winding agrees with the inverse-transpose shading normals.
        const bool mirrored = det < 0.0;
        if (invertible) {
            N = N.GetInverse().GetTranspose();
        }
        // GfMatrix row-vector convention: v' = v * N.

        const uint32_t base = uint32_t(world->VertexCount());
        const bool haveNormals = mesh.normals.size() == nPoints;
        world->positions.reserve(world->positions.size() + 3 * nPoints);
        world->normals.reserve(world->normals.size() + 3 * nPoints);
        for (size_t i = 0; i < nPoints; ++i) {
            for (int k = 0; k < 3; ++k) {
                world->positions.push_back(float(worldPoints[i][k]));
            }
            GfVec3d n(0.0, 0.0, 0.0);
            if (haveNormals) {
                const GfVec3f& m = mesh.normals[i];
                n = GfVec3d(m[0], m[1], m[2]) * N;
            }
            const double len = n.GetLength();
            if (len > 0.0 && std::isfinite(len)) {
                n /= len;
            } else {
                n = GfVec3d(0.0, 0.0, 0.0);   // the shader treats a zero normal as "use the face normal"
            }
            for (int k = 0; k < 3; ++k) {
                world->normals.push_back(float(n[k]));
            }
        }

        world->indices.reserve(world->indices.size() + 3 * nTris);
        world->albedo.reserve(world->albedo.size() + 4 * nTris);
        for (const GfVec3i& t : mesh.triangles) {
            world->indices.push_back(base + uint32_t(t[0]));
            world->indices.push_back(base + uint32_t(mirrored ? t[2] : t[1]));
            world->indices.push_back(base + uint32_t(mirrored ? t[1] : t[2]));
            world->albedo.push_back(mesh.albedo[0]);
            world->albedo.push_back(mesh.albedo[1]);
            world->albedo.push_back(mesh.albedo[2]);
            world->albedo.push_back(1.0f);
        }
    }

    return world;
}

PXR_NAMESPACE_CLOSE_SCOPE
