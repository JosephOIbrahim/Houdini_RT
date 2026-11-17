#include "meshNormals.h"
#include "mesh.h"
#include "renderParam.h"
#include "scene.h"
#include "pxr/imaging/hd/changeTracker.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include <iostream>
#include <limits>
#include <stdexcept>

PXR_NAMESPACE_USING_DIRECTIVE
#define CHECK(value) do { if (!(value)) throw std::runtime_error("FAILED: " #value); } while (false)

const SdfPath meshId("/scene/mesh");
const VtVec3fArray values = {GfVec3f(1,0,1), GfVec3f(0,1,1), GfVec3f(-1,0,1), GfVec3f(0,-1,1)};

struct Triangles {
    VtVec3iArray indices;
    VtIntArray faces;
    explicit Triangles(const HdMeshTopology& topology) {
        VtIntArray params;
        HdMeshUtil(&topology, meshId).ComputeTriangleIndices(&indices, &params);
        for (int param : params)
            faces.push_back(HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(param));
    }
};

HdMeshTopology Quad(const TfToken& orientation = HdTokens->rightHanded) {
    return HdMeshTopology(TfToken("none"), orientation, VtIntArray{4}, VtIntArray{0,1,2,3});
}

HdRTNormalSource Source(HdInterpolation interpolation, const VtVec3fArray& data) {
    HdRTNormalSource result;
    result.present = true;
    result.interpolation = interpolation;
    result.values = VtValue(data);
    return result;
}

VtVec3fArray Build(const HdRTNormalSource& source, const HdMeshTopology& topology, size_t points = 4) {
    const Triangles triangles(topology);
    VtVec3fArray out;
    std::string error;
    CHECK(HdRTBuildCornerNormals(source, topology, triangles.indices, triangles.faces,
                                points, meshId, &out, &error));
    CHECK(error.empty());
    return out;
}

void CheckInterpolation() {
    const HdMeshTopology topology = Quad();
    const Triangles triangles(topology);
    CHECK(triangles.faces == VtIntArray({0,0}));
    auto constant = Build(Source(HdInterpolationConstant, VtVec3fArray{values[0]}), topology);
    CHECK(constant.size() == 6);
    for (const auto& n : constant) CHECK(n == values[0]);
    for (HdInterpolation interpolation : {HdInterpolationVertex, HdInterpolationVarying, HdInterpolationFaceVarying}) {
        for (const auto& orientation : {HdTokens->rightHanded, HdTokens->leftHanded}) {
            const auto oriented = Quad(orientation);
            const Triangles t(oriented);
            const auto out = Build(Source(interpolation, values), oriented);
            CHECK(out.size() == 6);
            for (size_t i=0; i<t.indices.size(); ++i)
                for (int k=0; k<3; ++k) CHECK(out[3*i+k] == values[t.indices[i][k]]);
        }
    }
    auto indexed = Source(HdInterpolationFaceVarying, VtVec3fArray{values[0], values[1]});
    indexed.indexed = true;
    indexed.indices = VtIntArray{0,1,0,1};
    CHECK(Build(indexed, topology) == VtVec3fArray({values[0],values[1],values[0],values[0],values[0],values[1]}));
    const HdMeshTopology triangle(TfToken("none"), HdTokens->rightHanded, VtIntArray{3}, VtIntArray{0,1,2});
    CHECK(Build(Source(HdInterpolationFaceVarying, VtVec3fArray{values[0],values[1],values[2]}), triangle, 3)
          == VtVec3fArray({values[0],values[1],values[2]}));
}

void CheckHolesAndValidation() {
    HdMeshTopology topology(TfToken("none"), HdTokens->rightHanded,
        VtIntArray{4,3,3}, VtIntArray{0,1,2,3,0,2,4,1,4,2}, VtIntArray{1});
    const Triangles triangles(topology);
    CHECK(triangles.faces == VtIntArray({0,0,2}));
    const auto uniform = Build(Source(HdInterpolationUniform, VtVec3fArray{values[0],values[1],values[2]}), topology, 5);
    CHECK(uniform.size() == 9);
    for (size_t i=0; i<9; ++i) CHECK(uniform[i] == values[i < 6 ? 0 : 2]);
    const auto fv = Build(Source(HdInterpolationFaceVarying, VtVec3fArray{
        values[0],values[1],values[2],values[3], values[1],values[1],values[1], values[3],values[2],values[0]}), topology, 5);
    CHECK(fv == VtVec3fArray({values[0],values[1],values[2],values[0],values[2],values[3],values[3],values[2],values[0]}));
    auto rejects = [&](HdRTNormalSource source) {
        VtVec3fArray output{values[0]};
        std::string error;
        CHECK(!HdRTBuildCornerNormals(source, topology, triangles.indices, triangles.faces, 5, meshId, &output, &error));
        CHECK(output.empty() && !error.empty());
    };
    rejects(Source(HdInterpolationVertex, values));
    rejects(Source(HdInterpolationInstance, VtVec3fArray{values[0]}));
    rejects(Source(HdInterpolationConstant, VtVec3fArray{GfVec3f(0)}));
    rejects(Source(HdInterpolationConstant, VtVec3fArray{GfVec3f(std::numeric_limits<float>::quiet_NaN())}));
    auto indexed = Source(HdInterpolationConstant, VtVec3fArray{values[0]});
    indexed.indexed = true;
    indexed.indices = VtIntArray{-1}; rejects(indexed);
    indexed.indices = VtIntArray{1}; rejects(indexed);
    indexed.indices.clear(); rejects(indexed);
    indexed = Source(HdInterpolationConstant, VtVec3fArray{values[0]});
    indexed.values = VtValue(1.0f); rejects(indexed);
    CHECK(Build(HdRTNormalSource(), topology, 5).empty());
}

class Delegate : public HdSceneDelegate {
public:
    Delegate() : HdSceneDelegate(nullptr, SdfPath("/scene")) {}
    bool authored = true, prefixed = false, indexed = false;
    VtVec3fArray normals{GfVec3f(0,0,1)};
    VtVec3fArray points{GfVec3f(0,0,0),GfVec3f(1,0,0),GfVec3f(1,1,0),GfVec3f(0,1,0)};
    HdMeshTopology GetMeshTopology(const SdfPath&) override { return Quad(); }
    GfMatrix4d GetTransform(const SdfPath&) override { return GfMatrix4d(1); }
    bool GetVisible(const SdfPath&) override { return true; }
    SdfPath GetInstancerId(const SdfPath&) override { return SdfPath(); }
    VtValue Get(const SdfPath&, const TfToken& key) override {
        if (key == HdTokens->points) return VtValue(points);
        if (key == TfToken("primvars:normals")) return VtValue(VtVec3fArray{values[0]});
        if (key == HdTokens->normals) return VtValue(normals);
        return VtValue();
    }
    VtValue GetIndexedPrimvar(const SdfPath& id, const TfToken& key, VtIntArray* indices) override {
        *indices = VtIntArray{0};
        return Get(id, key);
    }
    HdPrimvarDescriptorVector GetPrimvarDescriptors(const SdfPath&, HdInterpolation interpolation) override {
        HdPrimvarDescriptorVector result;
        if (authored && interpolation == HdInterpolationConstant) {
            result.emplace_back(HdTokens->normals, interpolation, HdPrimvarRoleTokens->normal, indexed);
            if (prefixed) result.emplace_back(TfToken("primvars:normals"), interpolation, HdPrimvarRoleTokens->normal, false);
        }
        return result;
    }
};

void CheckSync() {
    Delegate delegate;
    delegate.prefixed = true;
    CHECK(Build(HdRTReadNormals(&delegate, meshId), Quad())[0] == values[0]);
    delegate.prefixed = false;
    delegate.indexed = true;
    CHECK(Build(HdRTReadNormals(&delegate, meshId), Quad())[0] == GfVec3f(0,0,1));
    HdRTMesh mesh(meshId);
    mesh.SetPrimId(17);
    HdRTScene scene;
    HdRTRenderParam param(nullptr, nullptr, &scene);
    auto sync = [&](HdDirtyBits bits) {
        mesh.Sync(&delegate, &param, &bits, HdReprTokens->smoothHull);
        return scene.Snapshot().meshes.at(meshId);
    };
    const auto first = sync(mesh.GetInitialDirtyBitsMask());
    CHECK(first->primId == 17 && first->cornerNormals.size() == 6 && first->faceIds == VtIntArray({0,0}));
    delegate.normals = VtVec3fArray{values[0]};
    const auto edited = sync(HdChangeTracker::DirtyNormals);
    CHECK(edited->normalVersion > first->normalVersion && edited->geometryVersion == first->geometryVersion);
    CHECK(edited->topologyVersion == first->topologyVersion && edited->points.cdata() == first->points.cdata());
    CHECK(edited->census.bboxMax[0] == first->census.bboxMax[0] && edited->cornerNormals[0] == values[0]);
    CHECK(sync(HdChangeTracker::DirtyNormals) == edited);
    CHECK(sync(HdChangeTracker::DirtyPoints) == edited);
    mesh.SetPrimId(18);
    const auto idEdit = sync(HdChangeTracker::DirtyPrimID);
    CHECK(idEdit->primId == 18 && idEdit->normalVersion == edited->normalVersion);
    CHECK(idEdit->geometryVersion == edited->geometryVersion);
    delegate.authored = false;
    const auto fallback = sync(HdChangeTracker::DirtyPrimvar);
    CHECK(fallback->cornerNormals.empty() && fallback->normalVersion > idEdit->normalVersion);
    CHECK(fallback->geometryVersion == idEdit->geometryVersion && fallback->normals == first->normals);
    mesh.Finalize(&param);
    CHECK(scene.Snapshot().meshes.empty());
}

int main() {
    try { CheckInterpolation(); CheckHolesAndValidation(); CheckSync(); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "Authored normal domains, indices, holes, orientation, fallback, IDs and Sync revisions passed\n";
}
