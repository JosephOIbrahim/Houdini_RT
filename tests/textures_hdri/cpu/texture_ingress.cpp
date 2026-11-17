#include "material.h"
#include "mesh.h"
#include "meshUV.h"
#include "light.h"
#include "renderParam.h"
#include "world.h"
#include "pxr/base/gf/rotation.h"
#include "pxr/imaging/hd/changeTracker.h"
#include "pxr/imaging/hd/meshUtil.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/usd/sdf/assetPath.h"
#include "pxr/usd/usd/stage.h"
#include "pxr/usd/usdGeom/metrics.h"
#include "pxr/usd/usdLux/domeLight_1.h"
#include "pxr/usd/usdShade/material.h"
#include "pxr/usd/usdShade/shader.h"
#include "pxr/usdImaging/usdImaging/domeLight_1Adapter.h"
#include "pxr/usdImaging/usdImaging/materialParamUtils.h"
#include <cmath>
#include <iostream>
#include <limits>
#include <stdexcept>
PXR_NAMESPACE_USING_DIRECTIVE
#define CHECK(x) do { if (!(x)) throw std::runtime_error("FAILED: " #x); } while(false)
const SdfPath surface("/Mat/surface"), image("/Mat/image"), uv("/Mat/uv"), transform("/Mat/place");
HdMaterialNetwork2 Network(bool scalar=false)
{
    HdMaterialNetwork2 n;
    n.nodes[surface].nodeTypeId = TfToken("ND_open_pbr_surface_surfaceshader");
    n.terminals[TfToken("surface")] = {surface,TfToken("out")};
    n.nodes[surface].inputConnections[TfToken(scalar?"specular_roughness":"base_color")] = {{image,TfToken("out")}};
    n.nodes[image].nodeTypeId = TfToken(scalar?"ND_image_float":"ND_image_color3");
    n.nodes[image].parameters[TfToken("file")] = VtValue(SdfAssetPath("color.png","/resolved/color.png"));
    return n;
}
HdRTMaterialData Translate(const HdMaterialNetwork2& n) { return HdRTTranslateMaterial(VtValue(n)); }
void CheckGraphs()
{
    auto n = Network();
    auto d = Translate(n);
    CHECK(d.supported && d.textures[0].enabled && d.textures[0].usesUV);
    CHECK(d.textures[0].resolvedPath == "/resolved/color.png" && d.textures[0].filter == 1);
    n.nodes[image].parameters[TfToken("colorSpace:file")] = VtValue(TfToken("srgb_texture"));
    CHECK(Translate(n).textures[0].colorSpace == "srgb_texture");
    for (const char* wrap : {"constant","clamp","periodic","mirror"}) {
        n.nodes[image].parameters[TfToken("uaddressmode")] = VtValue(std::string(wrap));
        CHECK(Translate(n).supported);
    }
    n.nodes[image].parameters[TfToken("filtertype")] = VtValue(std::string("cubic"));
    CHECK(!Translate(n).supported);
    n.nodes[image].parameters.erase(TfToken("filtertype"));
    n.nodes[image].parameters[TfToken("texcoord")] = VtValue(GfVec2f(.25f,.75f));
    d = Translate(n); CHECK(d.supported && !d.textures[0].usesUV && d.textures[0].uvTransform[2] == .25f);
    n.nodes[image].parameters.erase(TfToken("texcoord"));
    n.nodes[image].inputConnections[TfToken("texcoord")] = {{uv,TfToken("out")}};
    n.nodes[uv].nodeTypeId = TfToken("ND_texcoord_vector2");
    CHECK(Translate(n).supported);
    n.nodes[uv].parameters[TfToken("index")] = VtValue(1); CHECK(!Translate(n).supported);
    n.nodes[uv].parameters.clear(); n.nodes[uv].nodeTypeId = TfToken("ND_geompropvalue_vector2");
    CHECK(!Translate(n).supported);
    n.nodes[uv].parameters[TfToken("geomprop")] = VtValue(std::string("st")); CHECK(Translate(n).supported);
    n.nodes[uv].parameters[TfToken("geomprop")] = VtValue(std::string("uv2")); CHECK(!Translate(n).supported);
    n = Network(true); CHECK(Translate(n).supported && Translate(n).textures[HdRTRoughness].channels == 1);
    n.nodes[image].nodeTypeId = TfToken("ND_image_color3"); CHECK(!Translate(n).supported);
    n = Network(); n.nodes[image].inputConnections[TfToken("default")] = {{image,TfToken("out")}};
    CHECK(!Translate(n).supported);
    n = Network(); n.nodes[image].parameters[TfToken("frameoffset")] = VtValue(1); CHECK(!Translate(n).supported);
    n = Network(true);
    n.nodes[surface].inputConnections.clear();
    n.nodes[surface].inputConnections[TfToken("specular_ior")] = {{image,TfToken("out")}};
    CHECK(Translate(n).supported); // unused image default0 is permitted for periodic IOR.
    n.nodes[image].parameters[TfToken("uaddressmode")] = VtValue(std::string("constant"));
    CHECK(!Translate(n).supported);
    n.nodes[image].parameters[TfToken("default")] = VtValue(1.5f); CHECK(Translate(n).supported);
    n.nodes[image].parameters[TfToken("default")] = VtValue(3.1f); CHECK(!Translate(n).supported);
    n = Network(); n.nodes[image].parameters[TfToken("default")] = VtValue(GfVec3f(2));
    CHECK(Translate(n).supported);
    n.nodes[image].parameters[TfToken("vaddressmode")] = VtValue(std::string("constant")); CHECK(!Translate(n).supported);
}
GfVec2f Apply(const HdRTMaterialData& data, GfVec2f value)
{
    const auto& a = data.textures[0].uvTransform;
    return GfVec2f(a[0]*value[0]+a[1]*value[1]+a[2],a[3]*value[0]+a[4]*value[1]+a[5]);
}
bool Near(GfVec2f a, GfVec2f b) { return (a-b).GetLength() < 1e-5; }
void CheckTransforms()
{
    auto n = Network();
    n.nodes[image].inputConnections[TfToken("texcoord")] = {{transform,TfToken("out")}};
    n.nodes[uv].nodeTypeId = TfToken("ND_texcoord_vector2");
    auto& t = n.nodes[transform];
    t.nodeTypeId = TfToken("ND_UsdTransform2d");
    t.inputConnections[TfToken("in")] = {{uv,TfToken("out")}};
    t.parameters[TfToken("scale")] = VtValue(GfVec2f(2,3));
    t.parameters[TfToken("rotation")] = VtValue(90.f);
    t.parameters[TfToken("translation")] = VtValue(GfVec2f(4,5));
    CHECK(Translate(n).supported && Near(Apply(Translate(n),GfVec2f(1,2)),GfVec2f(-2,7)));
    t.inputConnections.clear(); CHECK(!Translate(n).textures[0].usesUV);
    CHECK(Near(Apply(Translate(n),GfVec2f(1,2)),GfVec2f(4,5)));
    t.nodeTypeId = TfToken("ND_place2d_vector2"); t.parameters.clear();
    t.inputConnections[TfToken("texcoord")] = {{uv,TfToken("out")}};
    t.parameters[TfToken("scale")] = VtValue(GfVec2f(2,4));
    t.parameters[TfToken("rotate")] = VtValue(90.f);
    t.parameters[TfToken("pivot")] = VtValue(GfVec2f(1,2));
    t.parameters[TfToken("offset")] = VtValue(GfVec2f(3,4));
    // Hand evaluated: ((5,10)-(1,2))/(2,4)=(2,2), clockwise90=(2,-2).
    CHECK(Near(Apply(Translate(n),GfVec2f(5,10)),GfVec2f(0,-4)));
    t.parameters[TfToken("operationorder")] = VtValue(1);
    CHECK(Near(Apply(Translate(n),GfVec2f(5,10)),GfVec2f(3,1.75f)));
    t.inputConnections[TfToken("texcoord")] = {{transform,TfToken("out")}};
    CHECK(!Translate(n).supported);
    t.inputConnections[TfToken("texcoord")] = {{uv,TfToken("wrong")}};
    CHECK(!Translate(n).supported);
}
HdMeshTopology Topology()
{ return HdMeshTopology(TfToken("none"),TfToken("rightHanded"),VtIntArray{4,3},VtIntArray{0,1,2,3,3,2,4}); }
void CheckUV()
{
    const auto topology = Topology();
    VtVec3iArray triangles; VtIntArray params, faces;
    HdMeshUtil util(&topology,SdfPath("/mesh")); util.ComputeTriangleIndices(&triangles,&params);
    for (int p : params) faces.push_back(HdMeshUtil::DecodeFaceIndexFromCoarseFaceParam(p));
    HdRTUVSource source; source.present = true;
    VtVec2fArray corners; std::string error;
    auto build = [&]() { return HdRTBuildCornerUV(source,topology,triangles,faces,5,SdfPath("/mesh"),&corners,&error); };
    source.values = VtValue(VtVec2fArray{GfVec2f(.2f,.4f)});
    CHECK(build() && corners.size() == 9 && corners[8] == GfVec2f(.2f,.4f));
    source.interpolation = HdInterpolationUniform;
    source.values = VtValue(VtVec2fArray{GfVec2f(1,2),GfVec2f(3,4)});
    CHECK(build() && corners[5] == GfVec2f(1,2) && corners[6] == GfVec2f(3,4));
    source.interpolation = HdInterpolationVertex;
    source.values = VtValue(VtVec3fArray{GfVec3f(0,0,2),GfVec3f(1,0,2),GfVec3f(1,1,2),GfVec3f(0,1,2),GfVec3f(2,2,2)});
    CHECK(build() && corners[8] == GfVec2f(2,2));
    source.interpolation = HdInterpolationVarying; CHECK(build());
    source.interpolation = HdInterpolationFaceVarying; source.indexed = true;
    source.indices = VtIntArray{0,1,2,3,4,4,4};
    CHECK(build() && corners[6] == GfVec2f(2,2));
    source.indices[6] = 5; CHECK(!build() && corners.empty());
    source.indices[6] = -1; CHECK(!build());
    source.indices = VtIntArray{0}; CHECK(!build());
    source.indexed = false; CHECK(!build());
    source.interpolation = HdInterpolationConstant;
    source.values = VtValue(GfVec2f(std::numeric_limits<float>::infinity(),0)); CHECK(!build());
    source.present = false; CHECK(!build() && error.find("missing") != std::string::npos);
}
class Delegate : public HdSceneDelegate
{
public:
    Delegate() : HdSceneDelegate(nullptr,SdfPath("/scene")) {}
    VtVec3fArray points{GfVec3f(0),GfVec3f(1,0,0),GfVec3f(0,1,0)};
    VtVec2fArray coordinates{GfVec2f(0),GfVec2f(1,0),GfVec2f(0,1)};
    std::map<TfToken,VtValue> light;
    VtValue lightNetwork;
    GfMatrix4d matrix{1}; bool hasUV = true;
    VtValue Get(const SdfPath&,const TfToken& key) override {
        if (key == HdTokens->points) return VtValue(points);
        if (key == TfToken("st")) return VtValue(coordinates);
        return VtValue();
    }
    HdMeshTopology GetMeshTopology(const SdfPath&) override
    { return HdMeshTopology(TfToken("none"),TfToken("rightHanded"),VtIntArray{3},VtIntArray{0,1,2}); }
    HdPrimvarDescriptorVector GetPrimvarDescriptors(const SdfPath&,HdInterpolation interpolation) override
    { return hasUV && interpolation == HdInterpolationVertex ? HdPrimvarDescriptorVector{
        HdPrimvarDescriptor(TfToken("st"),HdInterpolationVertex,TfToken("textureCoordinate"),false)}
        : HdPrimvarDescriptorVector{}; }
    GfMatrix4d GetTransform(const SdfPath&) override { return matrix; }
    bool GetVisible(const SdfPath&) override { return true; }
    SdfPath GetMaterialId(const SdfPath&) override { return SdfPath("/material"); }
    VtValue GetLightParamValue(const SdfPath&,const TfToken& key) override { return light[key]; }
    VtValue GetMaterialResource(const SdfPath&) override { return lightNetwork; }
};
void CheckPublication()
{
    Delegate delegate;
    HdRTScene scene; std::atomic<int> version{0}; HdRTRenderParam param(nullptr,&version,&scene);
    HdRTMesh mesh(SdfPath("/mesh"));
    auto sync = [&](HdDirtyBits bits) { mesh.Sync(&delegate,&param,&bits,TfToken("smoothHull"));
        return scene.Snapshot().meshes.at(SdfPath("/mesh")); };
    const auto first = sync(HdChangeTracker::AllDirty);
    CHECK(first->cornerUVs.size() == 3 && first->uvReason.empty());
    CHECK(sync(HdChangeTracker::DirtyPrimvar) == first);
    delegate.coordinates[1] = GfVec2f(2,0);
    const auto edited = sync(HdChangeTracker::DirtyPrimvar);
    CHECK(edited->uvVersion != first->uvVersion && edited->geometryVersion == first->geometryVersion);
    CHECK(edited->normalVersion == first->normalVersion && edited->topologyVersion == first->topologyVersion);
    CHECK(edited->points.cdata() == first->points.cdata() && edited->census.bboxMax[0] == first->census.bboxMax[0]);
    scene.SetMaterial(SdfPath("/material"),std::make_shared<HdRTMaterialData>(Translate(Network())));
    auto world = HdRTBuildScene(scene.Snapshot()); HdRTVkMaterial m; std::string reason;
    CHECK(HdRTResolveMeshMaterial(*world,*edited,&m,&reason));
    delegate.hasUV = false; const auto missing = sync(HdChangeTracker::DirtyPrimvar);
    world = HdRTBuildScene(scene.Snapshot()); CHECK(!HdRTResolveMeshMaterial(*world,*missing,&m,&reason));
    auto n = Network(); n.nodes[image].parameters[TfToken("texcoord")] = VtValue(GfVec2f(.5f));
    scene.SetMaterial(SdfPath("/material"),std::make_shared<HdRTMaterialData>(Translate(n)));
    world = HdRTBuildScene(scene.Snapshot()); CHECK(HdRTResolveMeshMaterial(*world,*missing,&m,&reason));
    const auto stable = scene.Snapshot().contentVersion;
    scene.SetMaterial(SdfPath("/material"),std::make_shared<HdRTMaterialData>(Translate(n)));
    CHECK(scene.Snapshot().contentVersion == stable);
    mesh.Finalize(&param); CHECK(scene.Snapshot().meshes.empty());
    HdRTMesh recreated(SdfPath("/mesh")); HdDirtyBits bits=HdChangeTracker::AllDirty;
    delegate.hasUV=true; recreated.Sync(&delegate,&param,&bits,TfToken("smoothHull"));
    CHECK(scene.Snapshot().meshes.at(SdfPath("/mesh"))->uvVersion > edited->uvVersion);
}
void CheckDome()
{
    Delegate delegate; HdRTScene scene; std::atomic<int> version{0}; HdRTRenderParam param(nullptr,&version,&scene);
    HdRTLight light(SdfPath("/dome"),HdPrimTypeTokens->domeLight);
    delegate.light[HdLightTokens->textureFile] = VtValue(SdfAssetPath("env.exr","/env.exr"));
    delegate.light[HdLightTokens->textureFormat] = VtValue(TfToken("latlong"));
    GfMatrix4d offset(1); offset.SetRotate(GfRotation(GfVec3d(1,0,0),90));
    delegate.light[HdLightTokens->domeOffset] = VtValue(offset);
    auto sync = [&]() { HdDirtyBits bits=HdLight::AllDirty; light.Sync(&delegate,&param,&bits);
        return scene.Snapshot().domes.at(SdfPath("/dome")); };
    const auto dome=sync(); CHECK(dome.supported && dome.hasTexture);
    CHECK((dome.worldToDome.TransformDir(GfVec3d(0,0,1))-GfVec3d(0,1,0)).GetLength() < 1e-8);
    const auto stable=scene.Snapshot().contentVersion; sync(); CHECK(scene.Snapshot().contentVersion == stable);
    HdMaterialNetwork2 network; network.terminals[TfToken("light")] = {SdfPath("/dome"),TfToken("out")};
    network.nodes[SdfPath("/dome")].parameters[TfToken("colorSpace:texture:file")] = VtValue(TfToken("ACEScg"));
    delegate.lightNetwork = VtValue(network); CHECK(sync().colorSpace == "ACEScg");
    delegate.matrix.SetScale(GfVec3d(2,3,2)); CHECK(!sync().supported);
    delegate.matrix.SetScale(2.0); CHECK(sync().supported);
    delegate.light[HdLightTokens->textureFormat] = VtValue(TfToken("mirroredBall")); CHECK(!sync().supported);
    delegate.light[HdLightTokens->textureFile] = VtValue(SdfAssetPath()); CHECK(!sync().hasTexture);
    light.Finalize(&param); CHECK(scene.Snapshot().domes.empty());
}
void CheckNative(const std::string& directory)
{
    for (const char* name : {"implicit_uv","texcoord","geomprop_st","usd_transform2d","place2d"}) {
        const auto stage = UsdStage::Open(directory+"/"+name+".usda"); CHECK(stage);
        const auto material = UsdShadeMaterial(stage->GetPrimAtPath(SdfPath("/materials/openpbr")));
        TfToken output;
        const auto shader = material.ComputeSurfaceSource(TfTokenVector{TfToken("mtlx")},&output);
        CHECK(shader && output == TfToken("out"));
        HdMaterialNetworkMap network;
        UsdImagingBuildHdMaterialNetworkFromTerminal(shader.GetPrim(),TfToken("surface"),
            {TfToken("mtlx")},{TfToken("mtlx"),TfToken()},&network,UsdTimeCode::Default());
        const auto data = HdRTTranslateMaterial(VtValue(network));
        std::cout << "Native " << name << " supported=" << data.supported << " reason=" << data.reason << '\n';
        for (const auto& terminal : network.map) for (const auto& node : terminal.second.nodes)
            for (const auto& p : node.parameters)
                std::cout << node.path << " " << p.first << " " << p.second.GetTypeName() << "=" << p.second << '\n';
        CHECK(data.supported && data.textures[HdRTBaseColor].enabled && data.textures[HdRTRoughness].enabled);
        CHECK(data.textures[HdRTBaseColor].colorSpace == "srgb_texture");
        CHECK(data.textures[HdRTRoughness].colorSpace == "Raw");
        CHECK(!data.textures[HdRTBaseColor].resolvedPath.empty());
    }
    const auto stage = UsdStage::CreateInMemory();
    auto dome = UsdLuxDomeLight_1::Define(stage,SdfPath("/Dome"));
    UsdImagingDomeLight_1Adapter adapter;
    auto offset = [&]() { return adapter.GetLightParamValue(dome.GetPrim(),dome.GetPath(),
        HdLightTokens->domeOffset,UsdTimeCode::Default()).Get<GfMatrix4d>(); };
    dome.CreatePoleAxisAttr(VtValue(TfToken("Y"))); CHECK(offset() == GfMatrix4d(1));
    dome.GetPoleAxisAttr().Set(TfToken("Z"));
    CHECK((offset().TransformDir(GfVec3d(0,1,0))-GfVec3d(0,0,1)).GetLength() < 1e-8);
    dome.GetPoleAxisAttr().Set(TfToken("scene")); UsdGeomSetStageUpAxis(stage,TfToken("Z"));
    CHECK((offset().TransformDir(GfVec3d(0,1,0))-GfVec3d(0,0,1)).GetLength() < 1e-8);
    dome.CreateTextureFileAttr(VtValue(SdfAssetPath("env.exr"))).SetColorSpace(TfToken("ACEScg"));
    HdMaterialNetworkMap network;
    UsdImagingBuildHdMaterialNetworkFromTerminal(dome.GetPrim(),TfToken("light"),
        {TfToken("mtlx")},{TfToken("mtlx"),TfToken()},&network,UsdTimeCode::Default());
    for (const auto& terminal : network.map) for (const auto& node : terminal.second.nodes)
        for (const auto& p : node.parameters)
            std::cout << "Dome metadata " << p.first << " " << p.second.GetTypeName() << "=" << p.second << '\n';
    CHECK(network.map.at(TfToken("light")).nodes.back().parameters.at(TfToken("colorSpace:texture:file")) == VtValue(TfToken("ACEScg")));
    Delegate delegate; delegate.lightNetwork = VtValue(network);
    delegate.light[HdLightTokens->textureFile] = VtValue(SdfAssetPath("env.exr","/env.exr"));
    HdRTScene scene; std::atomic<int> version{0}; HdRTRenderParam param(nullptr,&version,&scene);
    HdRTLight light(SdfPath("/dome"),HdPrimTypeTokens->domeLight); HdDirtyBits bits=HdLight::AllDirty;
    light.Sync(&delegate,&param,&bits); CHECK(scene.Snapshot().domes.at(SdfPath("/dome")).colorSpace == "ACEScg");
}
int main(int argc,char** argv)
{
    try {
        CheckGraphs(); CheckTransforms(); CheckUV(); CheckPublication(); CheckDome();
        if (argc > 1) CheckNative(argv[1]);
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "Texture graph, native ingress, UV domains/revisions and dome orientation passed\n";
}
