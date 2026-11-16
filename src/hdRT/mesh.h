//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03, pxr/imaging/plugin/hdEmbree/mesh.h.
// Embree, subdivision refinement, primvar samplers, culling and instancing are removed. See NOTICE.md.
//
#ifndef HDRT_MESH_H
#define HDRT_MESH_H

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/imaging/hd/mesh.h"
#include "pxr/imaging/hd/meshTopology.h"
#include "pxr/imaging/hd/vertexAdjacency.h"

PXR_NAMESPACE_OPEN_SCOPE

///
/// \class HdRTMesh
///
/// A Hydra mesh rprim. Sync() pulls points, topology, transform, visibility and the constant
/// displayColor; triangulates through HdMeshUtil (a fan: n - 2 triangles per n-sided face); computes
/// smooth vertex normals through Hd_SmoothNormals; and publishes the result to the delegate's
/// HdRTScene. It renders the polygons as authored (no subdivision) and always uses smooth normals,
/// whatever the subdivision scheme says: Karma shades a polygon mesh without authored normals smoothly
/// (s4 notes, probe B). Sync does no GPU work.
///
class HdRTMesh final : public HdMesh
{
public:
    HdRTMesh(SdfPath const& id);
    ~HdRTMesh() override = default;

    HdDirtyBits GetInitialDirtyBitsMask() const override;

    void Sync(HdSceneDelegate *sceneDelegate,
              HdRenderParam   *renderParam,
              HdDirtyBits     *dirtyBits,
              TfToken const   &reprToken) override;

    /// Removes this mesh from the scene.
    void Finalize(HdRenderParam *renderParam) override;

protected:
    HdDirtyBits _PropagateDirtyBits(HdDirtyBits bits) const override;

    void _InitRepr(TfToken const &reprToken, HdDirtyBits *dirtyBits) override;

private:
    HdMeshTopology     _topology;
    VtVec3fArray       _points;
    GfMatrix4d         _transform{1.0};
    GfVec3f            _albedo{0.18f};
    Hd_VertexAdjacency _adjacency;
    bool               _adjacencyValid = false;
    VtVec3iArray       _triangles;
    VtVec3fArray       _normals;
    bool               _warnedInstanced = false;

    HdRTMesh(const HdRTMesh&)             = delete;
    HdRTMesh &operator =(const HdRTMesh&) = delete;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_MESH_H
