#include "materialTexture.h"
#include "pxr/base/gf/vec2d.h"
#include "pxr/base/gf/vec2f.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/usd/sdf/assetPath.h"
#include <cmath>
#include <map>
#include <set>

PXR_NAMESPACE_OPEN_SCOPE
int HdRTTextureInputSlot(const TfToken& name)
{
    static const char* names[] = {"base_color", "base_weight", "base_metalness",
        "specular_color", "specular_weight", "specular_roughness", "specular_ior"};
    for (int i = 0; i < HdRTTextureSlotCount; ++i) if (name == TfToken(names[i])) return i;
    return -1;
}
namespace {
using Affine = std::array<float, 6>;
bool Scalar(const VtValue& v, float* out)
{
    if (v.IsHolding<float>()) *out = v.UncheckedGet<float>();
    else if (v.IsHolding<double>()) *out = float(v.UncheckedGet<double>());
    else if (v.IsHolding<int>()) *out = float(v.UncheckedGet<int>());
    else return false;
    return std::isfinite(*out);
}
bool Vector2(const VtValue& v, GfVec2f* out)
{
    if (v.IsHolding<GfVec2f>()) *out = v.UncheckedGet<GfVec2f>();
    else if (v.IsHolding<GfVec2d>()) *out = GfVec2f(v.UncheckedGet<GfVec2d>());
    else return false;
    return std::isfinite((*out)[0]) && std::isfinite((*out)[1]);
}
bool Text(const VtValue& v, std::string* out)
{
    if (v.IsHolding<std::string>()) *out = v.UncheckedGet<std::string>();
    else if (v.IsHolding<TfToken>()) *out = v.UncheckedGet<TfToken>().GetString();
    else return false;
    return true;
}
bool Metadata(const TfToken& token, const VtValue& value, const HdMaterialNode2& node)
{
    const std::string name = token.GetString();
    if (name.compare(0,9,"typeName:") != 0 || !value.IsHolding<TfToken>()) return false;
    const TfToken input(name.substr(9));
    return node.parameters.count(input) || node.inputConnections.count(input);
}
Affine Compose(const Affine& a, const Affine& b)
{
    return {a[0]*b[0]+a[1]*b[3], a[0]*b[1]+a[1]*b[4], a[0]*b[2]+a[1]*b[5]+a[2],
            a[3]*b[0]+a[4]*b[3], a[3]*b[1]+a[4]*b[4], a[3]*b[2]+a[4]*b[5]+a[5]};
}
class Parser
{
public:
    const HdMaterialNetwork2& network;
    std::string* error;
    std::set<SdfPath> seen, stack;
    bool Fail(const std::string& reason) { *error = reason; return false; }
    const HdMaterialNode2* Node(const HdMaterialConnection2& edge, int depth)
    {
        if (depth > 8 || edge.upstreamOutputName != TfToken("out")) {
            Fail("texture graph exceeds depth 8 or uses a non-out source port"); return nullptr;
        }
        const auto found = network.nodes.find(edge.upstreamNode);
        if (found == network.nodes.end() || edge.upstreamNode.IsEmpty() || stack.count(edge.upstreamNode)) {
            Fail("texture graph has a missing node or cycle"); return nullptr;
        }
        seen.insert(edge.upstreamNode);
        if (seen.size() > 16) { Fail("texture graph exceeds 16 reachable nodes"); return nullptr; }
        return &found->second;
    }
    bool Input(const HdMaterialNode2& node, const char* name, int depth,
               Affine* matrix, bool* usesUV, bool implicitUV)
    {
        const TfToken token(name);
        for (const auto& edge : node.inputConnections)
            if (edge.first != token) return Fail("unsupported connected image/UV input: " + edge.first.GetString());
        const auto edge = node.inputConnections.find(token);
        if (edge != node.inputConnections.end()) {
            if (edge->second.size() != 1) return Fail("image/UV input must have exactly one source");
            return UV(edge->second.front(), depth + 1, matrix, usesUV);
        }
        const auto value = node.parameters.find(token);
        if (value == node.parameters.end() && implicitUV) {
            *matrix = {1,0,0,0,1,0}; *usesUV = true; return true;
        }
        GfVec2f constant(0);
        if (value != node.parameters.end() && !Vector2(value->second, &constant))
            return Fail("constant texture coordinates must be finite float2");
        *matrix = {0,0,constant[0],0,0,constant[1]}; *usesUV = false; return true;
    }
    bool UV(const HdMaterialConnection2& edge, int depth, Affine* matrix, bool* usesUV);
    bool Image(const HdMaterialConnection2& edge, int channels, HdRTTextureBinding* out);
};
bool Parser::UV(const HdMaterialConnection2& edge, int depth, Affine* matrix, bool* usesUV)
{
    const auto* node = Node(edge, depth);
    if (!node) return false;
    stack.insert(edge.upstreamNode);
    struct Pop { std::set<SdfPath>& s; SdfPath p; ~Pop() { s.erase(p); } } pop{stack,edge.upstreamNode};
    const auto type = node->nodeTypeId.GetString();
    if (type == "ND_texcoord_vector2" || type == "ND_geompropvalue_vector2") {
        if (!node->inputConnections.empty()) return Fail("connected UV primvar selector is unsupported");
        std::string primvar;
        for (const auto& p : node->parameters) {
            if (Metadata(p.first,p.second,*node)) continue;
            if (type == "ND_texcoord_vector2" && p.first == TfToken("index")) {
                if (!p.second.IsHolding<int>() || p.second.UncheckedGet<int>() != 0)
                    return Fail("only texture coordinate index 0 (st) is supported");
            } else if (type == "ND_geompropvalue_vector2" && p.first == TfToken("geomprop")) {
                if (!Text(p.second,&primvar)) return Fail("UV primvar name must be a string");
            } else if (type == "ND_geompropvalue_vector2" && p.first == TfToken("default")) {
                GfVec2f v;
                if (!Vector2(p.second,&v) || v != GfVec2f(0)) return Fail("nonzero UV primvar default is unsupported");
            } else return Fail("unsupported UV selector input: " + p.first.GetString());
        }
        if (type == "ND_geompropvalue_vector2" && primvar != "st") return Fail("only the st UV primvar is supported");
        *matrix = {1,0,0,0,1,0}; *usesUV = true; return true;
    }
    const bool place = type == "ND_place2d_vector2";
    if (!place && type != "ND_UsdTransform2d") return Fail("unsupported UV node: " + type);
    GfVec2f scale(1), offset(0), pivot(0);
    float angle = 0;
    int order = 0;
    const TfToken input(place ? "texcoord" : "in");
    for (const auto& p : node->parameters) {
        if (Metadata(p.first,p.second,*node) || p.first == input) continue;
        const auto name = p.first.GetString();
        if (name == "scale") { if (!Vector2(p.second,&scale)) return Fail("invalid UV scale"); }
        else if (name == (place ? "offset" : "translation")) {
            if (!Vector2(p.second,&offset)) return Fail("invalid UV translation");
        } else if (name == (place ? "rotate" : "rotation")) {
            if (!Scalar(p.second,&angle)) return Fail("invalid UV rotation");
        } else if (place && name == "pivot") {
            if (!Vector2(p.second,&pivot)) return Fail("invalid UV pivot");
        } else if (place && name == "operationorder") {
            if (!p.second.IsHolding<int>() || (order=p.second.UncheckedGet<int>()) < 0 || order > 1)
                return Fail("UV operationorder must be SRT (0) or TRS (1)");
        } else return Fail("unsupported UV transform input: " + name);
    }
    Affine source;
    if (!Input(*node,input.GetText(),depth,&source,usesUV,false)) return false;
    if (place && (scale[0] == 0 || scale[1] == 0)) return Fail("place2d scale must be nonzero");
    // MaterialX rotate2d is clockwise; UsdTransform2d explicitly negates it.
    const double radians = (place ? angle : -double(angle)) * 0.017453292519943295;
    const float c = float(std::cos(radians)), s = float(std::sin(radians));
    const Affine rotate{c,s,0,-s,c,0};
    const Affine scaling{place?1/scale[0]:scale[0],0,0,0,place?1/scale[1]:scale[1],0};
    Affine local;
    if (!place) local = Compose(Affine{1,0,offset[0],0,1,offset[1]},Compose(rotate,scaling));
    else {
        const Affine unpivot{1,0,-pivot[0],0,1,-pivot[1]};
        const Affine shift{1,0,-offset[0],0,1,-offset[1]};
        const Affine repivot{1,0,pivot[0],0,1,pivot[1]};
        local = order == 0 ? Compose(repivot,Compose(shift,Compose(rotate,Compose(scaling,unpivot))))
                           : Compose(repivot,Compose(scaling,Compose(rotate,Compose(shift,unpivot))));
    }
    *matrix = Compose(local,source);
    for (float value : *matrix) if (!std::isfinite(value)) return Fail("UV transform is not representable");
    return true;
}
bool Parser::Image(const HdMaterialConnection2& edge, int channels, HdRTTextureBinding* out)
{
    const auto* node = Node(edge,1);
    if (!node) return false;
    if (node->nodeTypeId != TfToken(channels == 1 ? "ND_image_float" : "ND_image_color3"))
        return Fail("OpenPBR input requires a direct, type-matching MaterialX image output");
    stack.insert(edge.upstreamNode);
    out->channels = channels;
    for (const auto& p : node->parameters) {
        if (Metadata(p.first,p.second,*node) || p.first == TfToken("texcoord")) continue;
        const std::string name = p.first.GetString();
        if (name == "file") {
            if (!p.second.IsHolding<SdfAssetPath>()) return Fail("image file must be a USD asset path");
            const auto& asset = p.second.UncheckedGet<SdfAssetPath>();
            out->authoredPath = asset.GetAuthoredPath(); out->resolvedPath = asset.GetResolvedPath();
            if (asset.GetAssetPath().empty()) return Fail("image file is empty");
        } else if (name == "colorSpace:file") {
            if (!Text(p.second,&out->colorSpace)) return Fail("image file colorSpace must be a token or string");
        } else if (name == "default") {
            if (channels == 1) {
                if (!Scalar(p.second,&out->fallback[0])) return Fail("invalid image scalar default");
                out->fallback[1] = out->fallback[2] = out->fallback[0];
            } else {
                GfVec3f v;
                if (p.second.IsHolding<GfVec3f>()) v = p.second.UncheckedGet<GfVec3f>();
                else if (p.second.IsHolding<GfVec3d>()) v = GfVec3f(p.second.UncheckedGet<GfVec3d>());
                else return Fail("image color default must be float3");
                for (int k=0;k<3;++k) {
                    if (!std::isfinite(v[k])) return Fail("image default must be finite");
                    out->fallback[k] = v[k];
                }
            }
        } else if (name == "uaddressmode" || name == "vaddressmode") {
            std::string value;
            if (!Text(p.second,&value)) return Fail("image address mode must be a string");
            const std::map<std::string,int> modes{{"constant",0},{"clamp",1},{"periodic",2},{"mirror",3}};
            const auto found = modes.find(value);
            if (found == modes.end()) return Fail("unsupported image address mode: " + value);
            (name == "uaddressmode" ? out->addressU : out->addressV) = found->second;
        } else if (name == "filtertype") {
            std::string value;
            if (!Text(p.second,&value) || (value != "closest" && value != "linear"))
                return Fail("only closest and linear image filters are supported");
            out->filter = value == "linear" ? 1 : 0;
        } else if (name == "frameoffset") {
            if (!p.second.IsHolding<int>() || p.second.UncheckedGet<int>() != 0)
                return Fail("image frame offsets are unsupported");
        } else if (name == "layer" || name == "framerange" || name == "frameendaction") {
            std::string value;
            if (!Text(p.second,&value) || value != (name == "frameendaction" ? "constant" : ""))
                return Fail("unsupported image layer or frame sequencing");
        } else return Fail("unsupported image input: " + name);
    }
    if (out->authoredPath.empty() && out->resolvedPath.empty()) return Fail("image file is missing");
    if (!Input(*node,"texcoord",1,&out->uvTransform,&out->usesUV,true)) return false;
    out->enabled = true;
    return true;
}
} // namespace
bool HdRTTranslateTexture(const HdMaterialNetwork2& network,
                         const HdMaterialConnection2& source, int channels,
                         HdRTTextureBinding* output, std::string* error)
{
    *output = HdRTTextureBinding();
    error->clear();
    Parser parser{network,error};
    return parser.Image(source,channels,output);
}
PXR_NAMESPACE_CLOSE_SCOPE
