//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03, pxr/imaging/plugin/hdEmbree/mesh.cpp
// (constructor, dirty-bit mask, _PropagateDirtyBits, _InitRepr, the scene pulls in _PopulateRtMesh and the
// triangulation and smooth-normal steps). Embree, subdivision, primvar samplers, culling and instancing are
// removed. Unlike hdEmbree, smooth normals are kept for subdivisionScheme "none" and "bilinear". See NOTICE.md.
//
#include "mesh.h"

#include "log.h"
#include "renderParam.h"
#include "scene.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/imaging/hd/changeTracker.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/smoothNormals.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/imaging/hd/vertexAdjacency.h"

#include <algorithm>
#include <memory>

PXR_NAMESPACE_OPEN_SCOPE

HdRTMesh::HdRTMesh(SdfPath const& id)
    : HdMesh(id)
{
}

HdDirtyBits
HdRTMesh::GetInitialDirtyBitsMask() const
{
    // Everything Sync() pulls must be dirty the first time, so the first Sync publishes a whole mesh.
    const int mask = HdChangeTracker::Clean
        | HdChangeTracker::InitRepr
        | HdChangeTracker::DirtyPoints
        | HdChangeTracker::DirtyTopology
        | HdChangeTracker::DirtyTransform
        | HdChangeTracker::DirtyVisibility
        | HdChangeTracker::DirtyPrimvar
        | HdChangeTracker::DirtyNormals
        ;
    return (HdDirtyBits)mask;
}

HdDirtyBits
HdRTMesh::_PropagateDirtyBits(HdDirtyBits bits) const
{
    return bits;
}

void
HdRTMesh::_InitRepr(TfToken const &reprToken, HdDirtyBits *dirtyBits)
{
    TF_UNUSED(dirtyBits);

    // An empty repr: the renderer draws from the scene, not from draw items.
    _ReprVector::iterator it = std::find_if(_reprs.begin(), _reprs.end(),
                                            _ReprComparator(reprToken));
    if (it == _reprs.end()) {
        _reprs.emplace_back(reprToken, HdReprSharedPtr());
    }
}

// The constant displayColor, delivered either as one GfVec3f or as an array of them.
static bool
_ReadDisplayColor(VtValue const& value, GfVec3f* out)
{
    if (value.IsHolding<VtVec3fArray>()) {
        VtVec3fArray const& a = value.UncheckedGet<VtVec3fArray>();
        if (!a.empty()) {
            *out = a[0];
            return true;
        }
    } else if (value.IsHolding<GfVec3f>()) {
        *out = value.UncheckedGet<GfVec3f>();
        return true;
    }
    return false;
}

void
HdRTMesh::Sync(HdSceneDelegate *sceneDelegate,
               HdRenderParam   *renderParam,
               HdDirtyBits     *dirtyBits,
               TfToken const   &reprToken)
{
    TF_UNUSED(reprToken);
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);
    SdfPath const& id = GetId();

    // Stop the render before anything it reads changes; the render pass starts a fresh one afterwards.
    param->AcquireSceneForEdit();

    bool geometryDirty = false;

    if (HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->points)) {
        _points = sceneDelegate->Get(id, HdTokens->points).Get<VtVec3fArray>();
        geometryDirty = true;
    }

    if (HdChangeTracker::IsTopologyDirty(*dirtyBits, id)) {
        // The polygons as authored. Refinement is not supported, so the refine level is left at 0.
        _topology = HdMeshTopology(GetMeshTopology(sceneDelegate), 0);
        _adjacencyValid = false;
        geometryDirty = true;

        // Triangulate: a fan, n - 2 triangles per n-sided face. The primitive params are not kept.
        HdMeshUtil meshUtil(&_topology, id);
        VtIntArray primitiveParams;
        _triangles = VtVec3iArray();
        meshUtil.ComputeTriangleIndices(&_triangles, &primitiveParams);
    }

    if (HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
        _transform = sceneDelegate->GetTransform(id);
    }

    if (HdChangeTracker::IsVisibilityDirty(*dirtyBits, id)) {
        _UpdateVisibility(sceneDelegate, dirtyBits);
    }

    if (HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->displayColor)) {
        VtValue const value = GetPrimvar(sceneDelegate, HdTokens->displayColor);
        GfVec3f c;
        if (_ReadDisplayColor(value, &c)) {
            _albedo = c;
        } else {
            _albedo = GfVec3f(0.18f);
        }
        HDRT_DEBUG("mesh %s displayColor arrives as %s -> albedo (%.4f, %.4f, %.4f)",
                   id.GetText(), value.GetTypeName().c_str(), _albedo[0], _albedo[1], _albedo[2]);
    }

    // Smooth vertex normals, always (see the class comment): the adjacency table follows the topology,
    // the normals follow the points.
    if (geometryDirty) {
        if (!_adjacencyValid) {
            _adjacency.BuildAdjacencyTable(&_topology);
            _adjacencyValid = true;
        }
        _normals = Hd_SmoothNormals::ComputeSmoothNormals(
            &_adjacency, static_cast<int>(_points.size()), _points.cdata());
    }

    // Publish a fresh, immutable copy. The render pass picks it up through the scene version.
    auto data = std::make_shared<HdRTMeshData>();
    data->points    = _points;
    data->normals   = _normals;
    data->triangles = _triangles;
    data->transform = _transform;
    data->albedo    = _albedo;
    data->visible   = IsVisible();
    HDRT_DEBUG("mesh %s synced: %zu points, %zu triangles, visible %d",
               id.GetText(), data->points.size(), data->triangles.size(), int(data->visible));
    param->GetScene()->SetMesh(id, std::move(data));

    *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
}

void
HdRTMesh::Finalize(HdRenderParam *renderParam)
{
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);
    param->AcquireSceneForEdit();
    param->GetScene()->RemoveMesh(GetId());
}

PXR_NAMESPACE_CLOSE_SCOPE
