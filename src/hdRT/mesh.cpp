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
#include "world.h"

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
        | HdChangeTracker::DirtyInstancer
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

    // Time-sampled attributes can be dirty without changing their values.
    // Immutable publications let Sync run without touching the render thread.
    const bool first = !_published;
    bool geometryDirty = first;
    bool topologyDirty = first;
    bool transformDirty = first;
    bool changed = first;

    // Instanced rprims (point instancers, native instancing) are not expanded yet: say so once.
    _UpdateInstancer(sceneDelegate, dirtyBits);
    if (!GetInstancerId().IsEmpty() && !_warnedInstanced) {
        HdRTLog("WARNING: mesh %s is instanced by %s; instancing is not supported yet, "
                "it renders once at its own transform", id.GetText(), GetInstancerId().GetText());
        _warnedInstanced = true;
    }

    if (HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->points)) {
        // A value of another type (or none) becomes an empty array, caught by the check below.
        const VtVec3fArray points = sceneDelegate->Get(id, HdTokens->points)
            .GetWithDefault<VtVec3fArray>(VtVec3fArray());
        if (points != _points) {
            _points = points;
            geometryDirty = changed = true;
        }
    }

    if (HdChangeTracker::IsTopologyDirty(*dirtyBits, id)) {
        // The polygons as authored. Refinement is not supported, so the refine level is left at 0.
        const HdMeshTopology topology(GetMeshTopology(sceneDelegate), 0);
        if (!(topology == _topology)) {
            _topology = topology;
            _adjacencyValid = false;
            topologyDirty = geometryDirty = changed = true;
        }
    }

    if (HdChangeTracker::IsTransformDirty(*dirtyBits, id)) {
        const GfMatrix4d transform = sceneDelegate->GetTransform(id);
        if (transform != _transform) {
            _transform = transform;
            transformDirty = changed = true;
        }
    }

    if (HdChangeTracker::IsVisibilityDirty(*dirtyBits, id)) {
        const bool visible = IsVisible();
        _UpdateVisibility(sceneDelegate, dirtyBits);
        changed = changed || visible != IsVisible();
    }

    if (HdChangeTracker::IsPrimvarDirty(*dirtyBits, id, HdTokens->displayColor)) {
        VtValue const value = GetPrimvar(sceneDelegate, HdTokens->displayColor);
        GfVec3f c(0.18f);
        _ReadDisplayColor(value, &c);
        if (c != _albedo) {
            _albedo = c;
            changed = true;
        }
        HDRT_DEBUG("mesh %s displayColor arrives as %s -> albedo (%.4f, %.4f, %.4f)",
                   id.GetText(), value.GetTypeName().c_str(), _albedo[0], _albedo[1], _albedo[2]);
    }

    if (!changed) {
        *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
        return;
    }

    // Validate before triangulation or smooth normals. A points-only edit may
    // invalidate the old topology; a later edit can make that same mesh valid.
    if (geometryDirty) {
        const bool wasValid = _geometryValid;
        _geometryValid = true;
        size_t count = 0;
        const VtIntArray& indices = _topology.GetFaceVertexIndices();
        for (int n : _topology.GetFaceVertexCounts()) {
            if (n < 0 || size_t(n) > indices.size() - count) {
                _geometryValid = false;
                break;
            }
            count += size_t(n);
        }
        if (count != indices.size())
            _geometryValid = false;
        for (int index : indices) {
            if (index < 0 || size_t(index) >= _points.size()) {
                _geometryValid = false;
                break;
            }
        }
        if (_geometryValid) {
            if (topologyDirty || !wasValid) {
                HdMeshUtil meshUtil(&_topology, id);
                VtIntArray primitiveParams;
                _triangles = VtVec3iArray();
                meshUtil.ComputeTriangleIndices(&_triangles, &primitiveParams);
            }
            if (!_adjacencyValid) {
                _adjacency.BuildAdjacencyTable(&_topology);
                _adjacencyValid = true;
            }
            _normals = Hd_SmoothNormals::ComputeSmoothNormals(
                &_adjacency, static_cast<int>(_points.size()), _points.cdata());
        } else {
            HdRTLog("WARNING: mesh %s has invalid point/topology indices; it is not rendered", id.GetText());
        }
    }

    // Vt arrays are shared copy-on-write. Transform/visibility/colour edits
    // publish only a small record; unchanged geometry remains shared.
    auto data = _published ? std::make_shared<HdRTMeshData>(*_published)
                           : std::make_shared<HdRTMeshData>();
    if (geometryDirty) {
        data->points = _geometryValid ? _points : VtVec3fArray();
        data->normals = _geometryValid ? _normals : VtVec3fArray();
        data->triangles = _geometryValid ? _triangles : VtVec3iArray();
        data->geometryVersion = HdRTNextMeshVersion();
        if (topologyDirty)
            data->topologyVersion = HdRTNextMeshVersion();
    }
    data->transform = _transform;
    data->albedo    = _albedo;
    data->visible   = IsVisible() && _geometryValid;
    if (geometryDirty || transformDirty)
        HdRTUpdateMeshCensus(data.get());
    HDRT_DEBUG("mesh %s synced: %zu points, %zu triangles, visible %d",
               id.GetText(), data->points.size(), data->triangles.size(), int(data->visible));
    _published = std::move(data);
    param->GetScene()->SetMesh(id, _published);

    *dirtyBits &= ~HdChangeTracker::AllSceneDirtyBits;
}

void
HdRTMesh::Finalize(HdRenderParam *renderParam)
{
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);
    param->GetScene()->RemoveMesh(GetId());
    _published.reset();
}

PXR_NAMESPACE_CLOSE_SCOPE
