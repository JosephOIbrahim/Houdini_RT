#include "material.h"
#include "mesh.h"
#include "renderParam.h"
#include "scene.h"
#include "world.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/imaging/hd/changeTracker.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdShade/shader.h"
#include "pxr/usd/usdShade/material.h"
#include "pxr/usdImaging/usdImaging/materialParamUtils.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>

PXR_NAMESPACE_USING_DIRECTIVE
#define CHECK(value) do { if (!(value)) throw std::runtime_error("FAILED: " #value); } while (false)
const SdfPath materialId("/scene/material"), shaderId("/scene/material/surface");
const SdfPath meshId("/scene/mesh"), displacementId("/scene/material/displacement");

HdMaterialNetwork2 Network() {
    HdMaterialNetwork2 n;
    n.nodes[shaderId].nodeTypeId = TfToken("ND_open_pbr_surface_surfaceshader");
    n.terminals[TfToken("surface")] = {shaderId, TfToken("out")};
    return n;
}
HdRTMaterialData Translate(const HdMaterialNetwork2& n) { return HdRTTranslateMaterial(VtValue(n)); }
void Reject(const HdMaterialNetwork2& n, const std::string& reason) {
    const auto data = Translate(n);
    CHECK(!data.supported && data.reason.find(reason) != std::string::npos);
    CHECK(data.parameters.baseColorWeight[0] == .8f); // never partially accepted
}

void CheckDefaultsAndValues() {
    auto n = Network();
    const auto d = Translate(n);
    CHECK(d.supported && d.reason.empty());
    CHECK(d.parameters.baseColorWeight[0] == .8f && d.parameters.baseColorWeight[3] == 1);
    CHECK(d.parameters.specularColorWeight[0] == 1 && d.parameters.specularColorWeight[3] == 1);
    CHECK(d.parameters.properties[0] == 0 && d.parameters.properties[1] == .3f);
    CHECK(d.parameters.properties[2] == 1.5f && d.parameters.properties[3] == 0);
    auto& p = n.nodes[shaderId].parameters;
    p[TfToken("base_color")] = VtValue(GfVec3f(.1f,.4f,.9f));
    p[TfToken("base_weight")] = VtValue(.7f);
    p[TfToken("base_metalness")] = VtValue(.2);
    p[TfToken("specular_color")] = VtValue(GfVec3d(.9,.7,.5));
    p[TfToken("specular_weight")] = VtValue(.4f);
    p[TfToken("specular_roughness")] = VtValue(.6f);
    p[TfToken("specular_ior")] = VtValue(2);
    const auto edited = Translate(n);
    CHECK(edited.supported && edited.parameters.baseColorWeight[0] == .1f);
    CHECK(edited.parameters.baseColorWeight[3] == .7f && edited.parameters.properties[0] == .2f);
    CHECK(edited.parameters.specularColorWeight[1] == .7f && edited.parameters.specularColorWeight[3] == .4f);
    CHECK(edited.parameters.properties[1] == .6f && edited.parameters.properties[2] == 2);
    for (const auto& name : {"base_weight","base_metalness","specular_weight","specular_roughness"}) {
        for (float boundary : {0.f, 1.f}) {
            auto b = Network(); b.nodes[shaderId].parameters[TfToken(name)] = VtValue(boundary);
            CHECK(Translate(b).supported);
        }
        for (float invalid : {-0.01f, 1.01f, std::numeric_limits<float>::infinity(),
                               std::numeric_limits<float>::quiet_NaN()}) {
            auto b = n; b.nodes[shaderId].parameters[TfToken(name)] = VtValue(invalid);
            Reject(b, name);
        }
    }
    for (float boundary : {1.f,3.f}) { p[TfToken("specular_ior")] = VtValue(boundary); CHECK(Translate(n).supported); }
    for (float invalid : {.99f,3.01f}) { p[TfToken("specular_ior")] = VtValue(invalid); Reject(n,"specular_ior"); }
}

void CheckUnsupportedInputs() {
    const std::map<std::string,float> defaults = {
        {"base_diffuse_roughness",0.f},{"specular_roughness_anisotropy",0.f},
        {"transmission_weight",0.f},{"transmission_depth",0.f},
        {"transmission_scatter_anisotropy",0.f},{"transmission_dispersion_scale",0.f},
        {"transmission_dispersion_abbe_number",20.f},{"subsurface_weight",0.f},
        {"subsurface_radius",1.f},{"subsurface_scatter_anisotropy",0.f},
        {"fuzz_weight",0.f},{"fuzz_roughness",.5f},{"coat_weight",0.f},
        {"coat_roughness",0.f},{"coat_roughness_anisotropy",0.f},{"coat_ior",1.6f},
        {"coat_darkening",1.f},{"thin_film_weight",0.f},{"thin_film_thickness",.5f},
        {"thin_film_ior",1.4f},{"emission_luminance",0.f},{"geometry_opacity",1.f}};
    auto allDefaults = Network();
    for (const auto& entry : defaults) {
        allDefaults.nodes[shaderId].parameters[TfToken(entry.first)] = VtValue(entry.second);
        auto bad = Network();
        bad.nodes[shaderId].parameters[TfToken(entry.first)] = VtValue(entry.second+.125f);
        Reject(bad,entry.first);
    }
    const std::map<std::string,GfVec3f> colors = {
        {"transmission_color",GfVec3f(1)}, {"transmission_scatter",GfVec3f(0)},
        {"subsurface_color",GfVec3f(.8f)}, {"subsurface_radius_scale",GfVec3f(1,.5f,.25f)},
        {"fuzz_color",GfVec3f(1)}, {"coat_color",GfVec3f(1)}, {"emission_color",GfVec3f(1)}};
    for (const auto& entry : colors) {
        allDefaults.nodes[shaderId].parameters[TfToken(entry.first)] = VtValue(entry.second);
        auto bad = Network();
        bad.nodes[shaderId].parameters[TfToken(entry.first)] = VtValue(GfVec3f(.15f));
        Reject(bad,entry.first);
    }
    allDefaults.nodes[shaderId].parameters[TfToken("geometry_thin_walled")] = VtValue(false);
    CHECK(Translate(allDefaults).supported);
    for (const auto& name : {"geometry_normal","geometry_tangent","geometry_coat_normal",
                            "geometry_coat_tangent","unknown_input"}) {
        auto n = Network(); n.nodes[shaderId].parameters[TfToken(name)] = VtValue(GfVec3f(0,0,1));
        Reject(n,name);
    }
    auto n = Network(); n.nodes[shaderId].parameters[TfToken("geometry_thin_walled")] = VtValue(true);
    Reject(n,"geometry_thin_walled");
    for (const auto& value : {VtValue(true),VtValue(std::string("0.5")),VtValue(GfVec3f(.5f))}) {
        n = Network(); n.nodes[shaderId].parameters[TfToken("base_weight")] = value; Reject(n,"base_weight");
    }
    for (const auto& value : {VtValue(1.f),VtValue(GfVec3f(-.1f,0,0)),VtValue(GfVec3f(0,0,1.1f)),
                             VtValue(GfVec3f(std::numeric_limits<float>::quiet_NaN()))}) {
        n = Network(); n.nodes[shaderId].parameters[TfToken("base_color")] = value; Reject(n,"base_color");
    }
}

void CheckGraphs() {
    CHECK(!HdRTTranslateMaterial(VtValue()).supported);
    CHECK(!HdRTTranslateMaterial(VtValue(4)).supported);
    auto n = Network(); n.terminals.clear(); Reject(n,"no surface");
    n = Network(); n.nodes.clear(); Reject(n,"terminal");
    n = Network(); n.terminals[TfToken("surface")].upstreamOutputName = TfToken("wrong"); Reject(n,"terminal");
    n = Network(); n.nodes[shaderId].nodeTypeId = TfToken("UsdPreviewSurface"); Reject(n,"surface shader");
    n = Network(); n.nodes[shaderId].inputConnections[TfToken("base_color")] = {{shaderId,TfToken("out")}};
    Reject(n,"connected input"); // a cycle never recurses
    n.nodes[shaderId].inputConnections[TfToken("base_color")][0].upstreamNode = SdfPath("/missing");
    Reject(n,"connected input");
    n = Network(); n.nodes[shaderId].inputConnections[TfToken("base_weight")] = {};
    Reject(n,"connected input");
    n = Network(); n.terminals[TfToken("displacement")] = {displacementId,TfToken("out")};
    n.nodes[displacementId].nodeTypeId = TfToken("ND_displacement_float");
    CHECK(Translate(n).supported); // exact unmodified native MaterialX builder
    n.nodes[displacementId].parameters[TfToken("scale")] = VtValue(2.f); CHECK(Translate(n).supported);
    n.nodes[displacementId].parameters[TfToken("displacement")] = VtValue(.1f); Reject(n,"displacement");
    n.nodes[displacementId].parameters[TfToken("scale")] = VtValue(0.f); Reject(n,"displacement");
    n.nodes[displacementId].parameters.clear();
    n.nodes[displacementId].inputConnections[TfToken("displacement")] = {{shaderId,TfToken("out")}};
    Reject(n,"displacement");
    n = Network(); n.terminals[TfToken("volume")] = {shaderId,TfToken("out")}; Reject(n,"volume");
    n = Network(); n.nodes[SdfPath("/unused")].nodeTypeId = TfToken("ND_image_color3");
    CHECK(Translate(n).supported); // only reachable shader graph affects the result
}

void CheckLegacyNetwork() {
    HdMaterialNetworkMap old;
    old.map[TfToken("surface")].nodes.push_back({shaderId,TfToken("ND_open_pbr_surface_surfaceshader"),{}});
    old.map[TfToken("displacement")].nodes.push_back({displacementId,TfToken("ND_displacement_float"),{}});
    old.terminals = {shaderId,displacementId};
    CHECK(HdRTTranslateMaterial(VtValue(old)).supported);
    auto duplicate = old;
    duplicate.map[TfToken("surface")].nodes.push_back(old.map[TfToken("surface")].nodes.front());
    CHECK(!HdRTTranslateMaterial(VtValue(duplicate)).supported);
    old.map[TfToken("surface")].relationships.push_back({SdfPath("/missing"),TfToken("out"),shaderId,TfToken("base_color")});
    CHECK(!HdRTTranslateMaterial(VtValue(old)).supported);
}

class Delegate : public HdSceneDelegate {
public:
    Delegate() : HdSceneDelegate(nullptr, SdfPath("/scene")) {}
    HdMaterialNetwork2 network = Network();
    SdfPath binding;
    HdGeomSubsets subsets;
    VtValue GetMaterialResource(const SdfPath&) override { return VtValue(network); }
    SdfPath GetMaterialId(const SdfPath&) override { return binding; }
    HdMeshTopology GetMeshTopology(const SdfPath&) override {
        HdMeshTopology t(TfToken("none"), HdTokens->rightHanded, VtIntArray{3}, VtIntArray{0,1,2});
        t.SetGeomSubsets(subsets); return t;
    }
    GfMatrix4d GetTransform(const SdfPath&) override { return GfMatrix4d(1); }
    bool GetVisible(const SdfPath&) override { return true; }
    SdfPath GetInstancerId(const SdfPath&) override { return SdfPath(); }
    VtValue Get(const SdfPath&, const TfToken& key) override {
        if (key == HdTokens->points)
            return VtValue(VtVec3fArray{GfVec3f(0,0,0),GfVec3f(1,0,0),GfVec3f(0,1,0)});
        return VtValue();
    }
    HdPrimvarDescriptorVector GetPrimvarDescriptors(const SdfPath&, HdInterpolation) override { return {}; }
};

void CheckSyncAndBinding() {
    Delegate delegate;
    HdRTScene scene;
    HdRTRenderParam param(nullptr, nullptr, &scene); // any StopRender call would crash
    HdRTMesh mesh(meshId);
    auto syncMesh = [&](HdDirtyBits bits) {
        mesh.Sync(&delegate, &param, &bits, HdReprTokens->smoothHull);
        return scene.Snapshot().meshes.at(meshId);
    };
    const auto initial = syncMesh(mesh.GetInitialDirtyBitsMask());
    CHECK(initial->materialId.IsEmpty());
    delegate.binding = materialId;
    const auto bound = syncMesh(HdChangeTracker::DirtyMaterialId);
    CHECK(bound->materialId == materialId && bound->geometryVersion == initial->geometryVersion);
    CHECK(bound->topologyVersion == initial->topologyVersion && bound->normalVersion == initial->normalVersion);
    CHECK(bound->points.cdata() == initial->points.cdata());
    CHECK(bound->census.bboxMax[0] == initial->census.bboxMax[0]);
    CHECK(syncMesh(HdChangeTracker::DirtyMaterialId) == bound);
    HdRTVkMaterial output;
    std::string reason;
    auto resolve = [&]() { return HdRTResolveMeshMaterial(*HdRTBuildScene(scene.Snapshot()),
        *scene.Snapshot().meshes.at(meshId), &output, &reason); };
    CHECK(!resolve() && reason.find("unavailable") != std::string::npos);
    HdRTMaterial material(materialId);
    auto syncMaterial = [&]() {
        HdDirtyBits bits = HdMaterial::AllDirty;
        material.Sync(&delegate, &param, &bits);
        CHECK(bits == HdMaterial::Clean);
    };
    syncMaterial();
    CHECK(resolve() && reason.empty());
    const auto oldSnapshot = scene.Snapshot();
    const auto oldWorld = HdRTBuildScene(oldSnapshot);
    syncMaterial();
    CHECK(scene.Snapshot().contentVersion == oldSnapshot.contentVersion);
    delegate.network.nodes[shaderId].parameters[TfToken("base_metalness")] = VtValue(1.f);
    syncMaterial();
    CHECK(scene.Snapshot().contentVersion == oldSnapshot.contentVersion + 1);
    CHECK(scene.Snapshot().meshes.at(meshId) == bound); // shared by all bound meshes, never rewritten
    CHECK(resolve() && output.properties[0] == 1);
    CHECK(oldWorld->materials.at(materialId)->parameters.properties[0] == 0); // immutable old request
    delegate.network.nodes[shaderId].parameters[TfToken("coat_weight")] = VtValue(.5f);
    syncMaterial(); CHECK(!resolve() && reason.find("coat_weight") != std::string::npos);
    material.Finalize(&param);
    CHECK(!resolve() && reason.find("unavailable") != std::string::npos);
    delegate.network = Network(); syncMaterial(); CHECK(resolve()); // delete/re-add at same path
    delegate.subsets = {{HdGeomSubset::TypeFaceSet, SdfPath("/scene/mesh/faces"), materialId, VtIntArray{0}}};
    const auto subset = syncMesh(HdChangeTracker::DirtyTopology);
    CHECK(subset->materialSubsetsUnsupported && !resolve());
    CHECK(subset->geometryVersion == bound->geometryVersion && subset->normalVersion == bound->normalVersion);
    CHECK(subset->topologyVersion == bound->topologyVersion && subset->points.cdata() == bound->points.cdata());
    delegate.subsets.clear(); syncMesh(HdChangeTracker::DirtyTopology); CHECK(resolve());
    delegate.binding = SdfPath();
    const auto unbound = syncMesh(HdChangeTracker::DirtyMaterialId);
    CHECK(!resolve() && reason.empty() && unbound->geometryVersion == initial->geometryVersion);
    CHECK(unbound->albedo == initial->albedo);
    mesh.Finalize(&param); material.Finalize(&param);
    CHECK(scene.Snapshot().meshes.empty() && scene.Snapshot().materials.empty());
}

void CheckUsdIngress() {
    const auto stage = UsdStage::CreateInMemory();
    auto shader = UsdShadeShader::Define(stage, shaderId);
    shader.CreateIdAttr(VtValue(TfToken("ND_open_pbr_surface_surfaceshader")));
    const auto color = shader.CreateInput(TfToken("base_color"), SdfValueTypeNames->Color3f);
    color.Set(GfVec3f(.25f,.5f,.75f));
    auto build = [&]() {
        HdMaterialNetworkMap network;
        UsdImagingBuildHdMaterialNetworkFromTerminal(shader.GetPrim(), TfToken("surface"),
            {TfToken("mtlx")}, {TfToken("mtlx"),TfToken()}, &network, UsdTimeCode::Default());
        return network;
    };
    const auto plain = build();
    const auto data = HdRTTranslateMaterial(VtValue(plain));
    std::cout << "Plain native ingress: " << data.supported << " " << data.reason << '\n';
    for (const auto& terminal : plain.map) {
        std::cout << "terminal " << terminal.first << '\n';
        for (const auto& node : terminal.second.nodes) {
            std::cout << "node " << node.path << " " << node.identifier << '\n';
            for (const auto& p : node.parameters)
                std::cout << "  " << p.first << " type " << p.second.GetTypeName() << '\n';
        }
    }
    color.GetAttr().SetColorSpace(TfToken("srgb_texture"));
    const auto tagged = build();
    const auto taggedData = HdRTTranslateMaterial(VtValue(tagged));
    std::cout << "Native USD->Hydra colorSpace probe: supported=" << taggedData.supported
              << " reason=" << taggedData.reason << '\n';
    for (const auto& terminal : tagged.map)
        for (const auto& node : terminal.second.nodes)
            for (const auto& p : node.parameters)
                std::cout << "  parameter " << p.first << " type " << p.second.GetTypeName() << '\n';
    CHECK(data.supported && data.parameters.baseColorWeight[1] == .5f);
    CHECK(!taggedData.supported && taggedData.reason.find("color-space") != std::string::npos);
    auto displacement = UsdShadeShader::Define(stage, displacementId);
    displacement.CreateIdAttr(VtValue(TfToken("ND_displacement_float")));
    displacement.CreateInput(TfToken("displacement"), SdfValueTypeNames->Float).Set(0.f);
    displacement.CreateInput(TfToken("scale"), SdfValueTypeNames->Float).Set(2.f);
    auto withDisplacement = plain;
    UsdImagingBuildHdMaterialNetworkFromTerminal(displacement.GetPrim(), TfToken("displacement"),
        {TfToken("mtlx")}, {TfToken("mtlx"),TfToken()}, &withDisplacement, UsdTimeCode::Default());
    CHECK(HdRTTranslateMaterial(VtValue(withDisplacement)).supported);
    displacement.GetInput(TfToken("displacement")).Set(.1f);
    withDisplacement = plain;
    UsdImagingBuildHdMaterialNetworkFromTerminal(displacement.GetPrim(), TfToken("displacement"),
        {TfToken("mtlx")}, {TfToken("mtlx"),TfToken()}, &withDisplacement, UsdTimeCode::Default());
    CHECK(!HdRTTranslateMaterial(VtValue(withDisplacement)).supported);
}

void CheckInterfaceIngress() {
    const auto stage = UsdStage::CreateInMemory();
    auto material = UsdShadeMaterial::Define(stage, materialId);
    auto interfaceInput = material.CreateInput(TfToken("tint"), SdfValueTypeNames->Color3f);
    interfaceInput.Set(GfVec3f(.2f,.4f,.6f));
    auto shader = UsdShadeShader::Define(stage, shaderId);
    shader.CreateIdAttr(VtValue(TfToken("ND_open_pbr_surface_surfaceshader")));
    auto color = shader.CreateInput(TfToken("base_color"), SdfValueTypeNames->Color3f);
    CHECK(color.ConnectToSource(interfaceInput));
    HdMaterialNetworkMap network;
    UsdImagingBuildHdMaterialNetworkFromTerminal(shader.GetPrim(), TfToken("surface"),
        {TfToken("mtlx")}, {TfToken("mtlx"),TfToken()}, &network, UsdTimeCode::Default());
    const auto data = HdRTTranslateMaterial(VtValue(network));
    std::cout << "Native constant interface ingress: supported=" << data.supported
              << " base_color=" << data.parameters.baseColorWeight[0] << ','
              << data.parameters.baseColorWeight[1] << ',' << data.parameters.baseColorWeight[2]
              << " reason=" << data.reason << '\n';
    for (const auto& terminal : network.map)
        std::cout << "  terminal " << terminal.first << " nodes=" << terminal.second.nodes.size()
                  << " relationships=" << terminal.second.relationships.size() << '\n';
    CHECK(data.supported && data.parameters.baseColorWeight[0] == .2f &&
          data.parameters.baseColorWeight[1] == .4f && data.parameters.baseColorWeight[2] == .6f);
    CHECK(network.map.at(TfToken("surface")).relationships.empty());
    interfaceInput.GetAttr().SetColorSpace(TfToken("srgb_texture"));
    HdMaterialNetworkMap tagged;
    UsdImagingBuildHdMaterialNetworkFromTerminal(shader.GetPrim(), TfToken("surface"),
        {TfToken("mtlx")}, {TfToken("mtlx"),TfToken()}, &tagged, UsdTimeCode::Default());
    const auto taggedData = HdRTTranslateMaterial(VtValue(tagged));
    std::cout << "Native tagged interface ingress: supported=" << taggedData.supported
              << " reason=" << taggedData.reason << '\n';
}

int main() {
    try { CheckDefaultsAndValues(); CheckUnsupportedInputs(); CheckGraphs();
          CheckLegacyNetwork(); CheckSyncAndBinding(); CheckUsdIngress(); CheckInterfaceIngress(); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "Native defaults, bounded inputs, graph fallback, neutral displacement, immutable material edits, bindings and subsets passed\n";
}
