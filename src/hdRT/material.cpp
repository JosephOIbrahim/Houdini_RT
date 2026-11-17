#include "material.h"
#include "materialTexture.h"
#include "log.h"
#include "renderParam.h"
#include "scene.h"
#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include <cmath>
#include <cstring>
#include <map>
#include <set>

PXR_NAMESPACE_OPEN_SCOPE

bool HdRTMaterialData::operator==(const HdRTMaterialData& other) const
{
    return supported == other.supported && reason == other.reason && textures == other.textures &&
        std::memcmp(&parameters, &other.parameters, sizeof(parameters)) == 0;
}

namespace {
bool Scalar(const VtValue& value, float* out)
{
    if (value.IsHolding<float>()) *out = value.UncheckedGet<float>();
    else if (value.IsHolding<double>()) *out = float(value.UncheckedGet<double>());
    else if (value.IsHolding<int>()) *out = float(value.UncheckedGet<int>());
    else return false;
    return std::isfinite(*out);
}

bool Color(const VtValue& value, float* out)
{
    if (value.IsHolding<GfVec3f>()) {
        const auto& color = value.UncheckedGet<GfVec3f>();
        for (int k = 0; k < 3; ++k) out[k] = color[k];
    } else if (value.IsHolding<GfVec3d>()) {
        const auto& color = value.UncheckedGet<GfVec3d>();
        for (int k = 0; k < 3; ++k) out[k] = float(color[k]);
    } else return false;
    for (int k = 0; k < 3; ++k)
        if (!std::isfinite(out[k])) return false;
    return true;
}

// Installed Houdini 22.0.429 MaterialX 1.39 OpenPBR nodedef 1.1.1.
// Unsupported lobes remain accepted only at their exact native defaults.
const std::map<std::string, float>& ScalarDefaults()
{
    static const std::map<std::string, float> values = {
        {"base_diffuse_roughness",0.f}, {"specular_roughness_anisotropy",0.f},
        {"transmission_weight",0.f}, {"transmission_depth",0.f},
        {"transmission_scatter_anisotropy",0.f}, {"transmission_dispersion_scale",0.f},
        {"transmission_dispersion_abbe_number",20.f}, {"subsurface_weight",0.f},
        {"subsurface_radius",1.f}, {"subsurface_scatter_anisotropy",0.f},
        {"fuzz_weight",0.f}, {"fuzz_roughness",.5f}, {"coat_weight",0.f},
        {"coat_roughness",0.f}, {"coat_roughness_anisotropy",0.f}, {"coat_ior",1.6f},
        {"coat_darkening",1.f}, {"thin_film_weight",0.f}, {"thin_film_thickness",.5f},
        {"thin_film_ior",1.4f}, {"emission_luminance",0.f}, {"geometry_opacity",1.f}
    };
    return values;
}

const std::map<std::string, GfVec3f>& ColorDefaults()
{
    static const std::map<std::string, GfVec3f> values = {
        {"transmission_color",GfVec3f(1)}, {"transmission_scatter",GfVec3f(0)},
        {"subsurface_color",GfVec3f(.8f)}, {"subsurface_radius_scale",GfVec3f(1,.5f,.25f)},
        {"fuzz_color",GfVec3f(1)}, {"coat_color",GfVec3f(1)}, {"emission_color",GfVec3f(1)}
    };
    return values;
}

bool ReadNetwork(const VtValue& resource, HdMaterialNetwork2* out, std::string* error)
{
    if (resource.IsHolding<HdMaterialNetwork2>()) {
        *out = resource.UncheckedGet<HdMaterialNetwork2>();
        return true;
    }
    if (!resource.IsHolding<HdMaterialNetworkMap>()) {
        *error = "missing or unsupported Hydra material resource";
        return false;
    }
    const auto& old = resource.UncheckedGet<HdMaterialNetworkMap>();
    for (const auto& terminal : old.map) {
        std::set<SdfPath> paths;
        for (const auto& node : terminal.second.nodes) {
            if (node.path.IsEmpty() || !paths.insert(node.path).second) {
                *error = "malformed material network: missing or duplicate node path";
                return false;
            }
        }
        if (paths.empty()) { *error = "empty material terminal network"; return false; }
        for (const auto& edge : terminal.second.relationships) {
            if (!paths.count(edge.inputId) || !paths.count(edge.outputId) ||
                edge.inputName.IsEmpty() || edge.outputName.IsEmpty()) {
                *error = "malformed material network: dangling connection";
                return false;
            }
        }
    }
    *out = HdConvertToHdMaterialNetwork2(old);
    // The legacy representation does not encode a terminal's output port.
    for (auto& terminal : out->terminals)
        if (terminal.second.upstreamOutputName.IsEmpty())
            terminal.second.upstreamOutputName = TfToken("out");
    return true;
}
bool NativeTypeMetadata(const std::pair<const TfToken, VtValue>& entry, const HdMaterialNode2& node)
{
    const std::string name = entry.first.GetString();
    return name.compare(0, 9, "typeName:") == 0 && entry.second.IsHolding<TfToken>() &&
        (node.parameters.count(TfToken(name.substr(9))) || node.inputConnections.count(TfToken(name.substr(9))));
}

bool NeutralDisplacement(const HdMaterialNode2& node)
{
    if (node.nodeTypeId != TfToken("ND_displacement_float") ||
        !node.inputConnections.empty()) return false;
    float displacement = 0, scale = 1;
    for (const auto& entry : node.parameters) {
        if (NativeTypeMetadata(entry, node)) continue;
        if (entry.first == TfToken("displacement")) {
            if (!Scalar(entry.second, &displacement)) return false;
        } else if (entry.first == TfToken("scale")) {
            if (!Scalar(entry.second, &scale)) return false;
        } else return false;
    }
    // A disabled nonzero displacement is deliberately outside this profile.
    return displacement == 0;
}

bool Parameter(const std::string& name, const VtValue& value, HdRTVkMaterial* out)
{
    float* scalar = nullptr;
    float low = 0, high = 1;
    if (name == "base_weight") scalar = &out->baseColorWeight[3];
    else if (name == "base_metalness") scalar = &out->properties[0];
    else if (name == "specular_weight") scalar = &out->specularColorWeight[3];
    else if (name == "specular_roughness") scalar = &out->properties[1];
    else if (name == "specular_ior") { scalar = &out->properties[2]; low = 1; high = 3; }
    if (scalar) return Scalar(value, scalar) && *scalar >= low && *scalar <= high;
    if (name == "base_color" || name == "specular_color") {
        float* color = name == "base_color" ? out->baseColorWeight : out->specularColorWeight;
        if (!Color(value, color)) return false;
        for (int k = 0; k < 3; ++k) if (color[k] < 0 || color[k] > 1) return false;
        return true;
    }
    const auto scalarDefault = ScalarDefaults().find(name);
    if (scalarDefault != ScalarDefaults().end()) {
        float actual;
        return Scalar(value, &actual) && actual == scalarDefault->second;
    }
    const auto colorDefault = ColorDefaults().find(name);
    if (colorDefault != ColorDefaults().end()) {
        float actual[3];
        return Color(value, actual) &&
            GfVec3f(actual[0], actual[1], actual[2]) == colorDefault->second;
    }
    return name == "geometry_thin_walled" && value.IsHolding<bool>() &&
        !value.UncheckedGet<bool>();
}
} // namespace

HdRTMaterialData HdRTTranslateMaterial(const VtValue& resource)
{
    HdRTMaterialData result;
    HdMaterialNetwork2 network;
    if (!ReadNetwork(resource, &network, &result.reason)) return result;
    const auto surface = network.terminals.find(TfToken("surface"));
    if (surface == network.terminals.end()) {
        result.reason = "no surface terminal";
        return result;
    }
    for (const auto& terminal : network.terminals) {
        const auto node = network.nodes.find(terminal.second.upstreamNode);
        if (terminal.second.upstreamNode.IsEmpty() || node == network.nodes.end() ||
            terminal.second.upstreamOutputName != TfToken("out")) {
            result.reason = "malformed or unsupported terminal: " + terminal.first.GetString();
            return result;
        }
        if (terminal.first == TfToken("surface")) continue;
        if (terminal.first != TfToken("displacement") || !NeutralDisplacement(node->second)) {
            result.reason = "unsupported non-neutral terminal: " + terminal.first.GetString();
            return result;
        }
    }
    const auto& node = network.nodes.at(surface->second.upstreamNode);
    if (node.nodeTypeId != TfToken("ND_open_pbr_surface_surfaceshader")) {
        result.reason = "unsupported surface shader: " + node.nodeTypeId.GetString();
        return result;
    }
    for (const auto& input : node.inputConnections) {
        const int slot = HdRTTextureInputSlot(input.first);
        if (slot < 0 || input.second.size() != 1) {
            result.reason = "unsupported or ambiguous connected input: " + input.first.GetString();
            return result;
        }
        const int channels = slot == HdRTBaseColor || slot == HdRTSpecularColor ? 3 : 1;
        if (!HdRTTranslateTexture(network,input.second.front(),channels,&result.textures[slot],&result.reason)) {
            result.reason = "connected input " + input.first.GetString() + ": " + result.reason;
            return result;
        }
        const auto& texture = result.textures[slot];
        if (texture.addressU == 0 || texture.addressV == 0) {
            const float low = slot == HdRTIor ? 1.f : 0.f, high = slot == HdRTIor ? 3.f : 1.f;
            for (int k=0;k<channels;++k) if (texture.fallback[k] < low || texture.fallback[k] > high) {
                result.reason = "image constant-border default exceeds the input range: " + input.first.GetString();
                return result;
            }
        }
    }
    HdRTVkMaterial parameters;
    for (const auto& entry : node.parameters) {
        // USD 26.05 attaches source-attribute type information to authored
        // inputs. It is metadata, not an additional OpenPBR shader input.
        if (NativeTypeMetadata(entry, node)) continue;
        const std::string name = entry.first.GetString();
        if (name.compare(0, 11, "colorSpace:") == 0) {
            if (entry.second.IsHolding<TfToken>() && entry.second.UncheckedGet<TfToken>().IsEmpty() &&
                (node.parameters.count(TfToken(name.substr(11))) || node.inputConnections.count(TfToken(name.substr(11))))) continue;
            result.reason = "explicit color-space conversion is unsupported: " + name;
            return result;
        }
        if (!Parameter(entry.first.GetString(), entry.second, &parameters)) {
            result.reason = "unsupported, invalid or non-default input: " + entry.first.GetString();
            return result;
        }
    }
    result.parameters = parameters;
    result.supported = true;
    return result;
}

void HdRTMaterial::Sync(HdSceneDelegate* delegate, HdRenderParam* renderParam,
                        HdDirtyBits* dirtyBits)
{
    if (!_published || (*dirtyBits & AllDirty)) {
        auto data = std::make_shared<HdRTMaterialData>(
            HdRTTranslateMaterial(delegate->GetMaterialResource(GetId())));
        if (!_published || !(*_published == *data)) {
            if (!data->supported)
                HdRTLog("WARNING: material %s: %s; whole-material displayColor Lambert fallback",
                        GetId().GetText(), data->reason.c_str());
            HDRT_DEBUG("material %s synced: OpenPBR %d", GetId().GetText(), int(data->supported));
            _published = std::move(data);
            static_cast<HdRTRenderParam*>(renderParam)->GetScene()->SetMaterial(GetId(), _published);
        }
    }
    *dirtyBits = Clean;
}

void HdRTMaterial::Finalize(HdRenderParam* renderParam)
{
    static_cast<HdRTRenderParam*>(renderParam)->GetScene()->RemoveMaterial(GetId());
    _published.reset();
}

PXR_NAMESPACE_CLOSE_SCOPE
