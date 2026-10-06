// settings.cpp - see settings.h.
#include "settings.h"

#include "log.h"

#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec3h.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/base/vt/array.h"

#include <algorithm>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

TF_DEFINE_PUBLIC_TOKENS(HdRTRenderSettingsTokens, HDRT_RENDER_SETTINGS_TOKENS);

namespace {

// ---- render settings: the application may hand a number over as any numeric type ---------------

bool _AsDouble(VtValue const& v, double* out)
{
    if (v.IsEmpty())
        return false;
    if (v.IsHolding<double>()) { *out = v.UncheckedGet<double>(); return true; }
    if (v.IsHolding<float>()) { *out = double(v.UncheckedGet<float>()); return true; }
    if (v.IsHolding<int>()) { *out = double(v.UncheckedGet<int>()); return true; }
    if (v.IsHolding<unsigned int>()) { *out = double(v.UncheckedGet<unsigned int>()); return true; }
    if (v.IsHolding<long>()) { *out = double(v.UncheckedGet<long>()); return true; }
    if (v.IsHolding<int64_t>()) { *out = double(v.UncheckedGet<int64_t>()); return true; }
    if (v.IsHolding<uint64_t>()) { *out = double(v.UncheckedGet<uint64_t>()); return true; }
    if (v.IsHolding<bool>()) { *out = v.UncheckedGet<bool>() ? 1.0 : 0.0; return true; }
    const VtValue cast = VtValue::Cast<double>(v);
    if (!cast.IsEmpty() && cast.IsHolding<double>()) { *out = cast.UncheckedGet<double>(); return true; }
    return false;
}

bool _AsVec3(VtValue const& v, double out[3])
{
    if (v.IsHolding<GfVec3f>()) { const GfVec3f a = v.UncheckedGet<GfVec3f>(); out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return true; }
    if (v.IsHolding<GfVec3d>()) { const GfVec3d a = v.UncheckedGet<GfVec3d>(); out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return true; }
    if (v.IsHolding<GfVec3h>()) { const GfVec3h a = v.UncheckedGet<GfVec3h>(); out[0] = float(a[0]); out[1] = float(a[1]); out[2] = float(a[2]); return true; }
    if (v.IsHolding<GfVec4f>()) { const GfVec4f a = v.UncheckedGet<GfVec4f>(); out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return true; }
    if (v.IsHolding<VtArray<float>>()) {
        const VtArray<float>& a = v.UncheckedGet<VtArray<float>>();
        if (a.size() >= 3) { out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return true; }
    }
    if (v.IsHolding<VtArray<double>>()) {
        const VtArray<double>& a = v.UncheckedGet<VtArray<double>>();
        if (a.size() >= 3) { out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return true; }
    }
    double s = 0.0;
    if (_AsDouble(v, &s)) { out[0] = out[1] = out[2] = s; return true; }   // a single number means grey
    return false;
}

const VtValue* _Find(HdRenderSettingsMap const& map, TfToken const& key)
{
    const auto it = map.find(key);
    return it == map.end() ? nullptr : &it->second;
}

// ---- config file: the shape is fixed by the exam, so a tiny reader is enough -------------------
// For a key it returns every number in that key's value (a scalar or an array). Same reader as
// src/furnace/rayq_host.cpp.

std::string _ReadTextFile(const char* path)
{
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return std::string();
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

std::vector<double> _JsonNumbers(const std::string& text, const char* key)
{
    std::vector<double> out;
    const std::string needle = std::string("\"") + key + "\"";
    size_t pos = text.find(needle);
    if (pos == std::string::npos)
        return out;
    pos = text.find(':', pos + needle.size());
    if (pos == std::string::npos)
        return out;
    ++pos;
    int depth = 0;
    while (pos < text.size())
    {
        const char c = text[pos];
        if (c == '[')
        {
            ++depth;
            ++pos;
        }
        else if (c == ']')
        {
            --depth;
            ++pos;
            if (depth <= 0)
                break;
        }
        else if (c == '-' || c == '+' || (c >= '0' && c <= '9'))
        {
            char* end = nullptr;
            out.push_back(std::strtod(text.c_str() + pos, &end));
            pos = size_t(end - text.c_str());
            if (depth == 0)
                break;
        }
        else if ((c == ',' || c == '}') && depth == 0)
        {
            break;
        }
        else
        {
            ++pos;
        }
    }
    return out;
}

int _ClampInt(double v, int lo, int hi)
{
    if (!(v == v))
        return lo;
    return int(std::min(double(hi), std::max(double(lo), v)));
}

} // namespace

HdRTFurnaceConfig
HdRTResolveFurnaceConfig(HdRenderSettingsMap const& delivered)
{
    HdRTFurnaceConfig cfg;

    // Lowest priority after the defaults: the config file.
    int fromFile = 0;
    if (const char* path = std::getenv("HOUDINIRT_S3_CONFIG"))
    {
        const std::string text = *path ? _ReadTextFile(path) : std::string();
        if (!text.empty())
        {
            std::vector<double> n;
            n = _JsonNumbers(text, "seed");     if (n.size() == 1) { cfg.seed = _ClampInt(n[0], 0, 2147483647); ++fromFile; }
            n = _JsonNumbers(text, "spp");      if (n.size() == 1) { cfg.spp = _ClampInt(n[0], 1, 65536); ++fromFile; }
            n = _JsonNumbers(text, "rho");      if (n.size() == 1) { cfg.rho = float(n[0]); ++fromFile; }
            n = _JsonNumbers(text, "emission"); if (n.size() == 3) { cfg.emission[0] = float(n[0]); cfg.emission[1] = float(n[1]); cfg.emission[2] = float(n[2]); ++fromFile; }
            n = _JsonNumbers(text, "bounces");  if (n.size() == 1) { cfg.bounces = _ClampInt(n[0], 0, 4096); ++fromFile; }
        }
        else if (*path)
        {
            HDRT_DEBUG("HOUDINIRT_S3_CONFIG names '%s' but the file is missing or empty", path);
        }
    }

    // Highest priority: what the application delivered as render settings.
    int fromSettings = 0;
    double d = 0.0;
    double e[3] = { 0.0, 0.0, 0.0 };
    if (const VtValue* v = _Find(delivered, HdRTRenderSettingsTokens->seed))
        if (_AsDouble(*v, &d)) { cfg.seed = _ClampInt(d, 0, 2147483647); ++fromSettings; }
    if (const VtValue* v = _Find(delivered, HdRTRenderSettingsTokens->spp))
        if (_AsDouble(*v, &d)) { cfg.spp = _ClampInt(d, 1, 65536); ++fromSettings; }
    if (const VtValue* v = _Find(delivered, HdRTRenderSettingsTokens->furnaceRho))
        if (_AsDouble(*v, &d)) { cfg.rho = float(d); ++fromSettings; }
    if (const VtValue* v = _Find(delivered, HdRTRenderSettingsTokens->furnaceEmission))
        if (_AsVec3(*v, e)) { cfg.emission[0] = float(e[0]); cfg.emission[1] = float(e[1]); cfg.emission[2] = float(e[2]); ++fromSettings; }
    if (const VtValue* v = _Find(delivered, HdRTRenderSettingsTokens->furnaceBounces))
        if (_AsDouble(*v, &d)) { cfg.bounces = _ClampInt(d, 0, 4096); ++fromSettings; }

    // Keep the physics sane whatever was typed in: a wall cannot reflect more than it receives.
    cfg.rho = std::min(0.999f, std::max(0.0f, cfg.rho));
    for (float& c : cfg.emission)
        c = std::max(0.0f, c);

    if (fromSettings == 5)
        cfg.source = "render settings (5 of 5)";
    else if (fromSettings > 0)
        cfg.source = TfStringPrintf("render settings (%d of 5), %s for the rest", fromSettings,
                                    fromFile > 0 ? "config file" : "defaults");
    else if (fromFile > 0)
        cfg.source = TfStringPrintf("config file (%d of 5)", fromFile);
    else
        cfg.source = "defaults";

    if (const char* v = std::getenv("HOUDINIRT_VALIDATION"))
        cfg.validation = (*v && *v != '0');
    if (const char* v = std::getenv("HOUDINIRT_ALLOW_SOFTWARE_DEVICE"))
        cfg.allowSoftwareDevice = (*v && *v != '0');
    if (const char* v = std::getenv("HOUDINIRT_DEBUG_OPEN_FACE"))
        if (*v >= '0' && *v <= '5' && v[1] == '\0')
            cfg.openFace = *v - '0';

    return cfg;
}

PXR_NAMESPACE_CLOSE_SCOPE
