//
// Copyright 2024 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03, pxr/imaging/plugin/hdEmbree/light.cpp
// (Sync's parameter pulls, GetInitialDirtyBitsMask, Finalize). Only the untextured dome is kept. See NOTICE.md.
//
#include "light.h"

#include "log.h"
#include "renderParam.h"
#include "scene.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/imaging/hd/sceneDelegate.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/usd/usdLux/blackbody.h"

#include <cmath>

PXR_NAMESPACE_OPEN_SCOPE

HdRTLight::HdRTLight(SdfPath const& id, TfToken const& type)
    : HdLight(id), _type(type)
{
}

HdDirtyBits
HdRTLight::GetInitialDirtyBitsMask() const
{
    return HdLight::AllDirty;
}

void
HdRTLight::Sync(HdSceneDelegate *sceneDelegate,
                HdRenderParam   *renderParam,
                HdDirtyBits     *dirtyBits)
{
    SdfPath const& id = GetId();
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);

    // A fallback light (empty path) has no scene data and never lights anything.
    if (id.IsEmpty() || !sceneDelegate || !param) {
        *dirtyBits = HdLight::Clean;
        return;
    }

    const float intensity = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->intensity).GetWithDefault(1.0f);
    const float exposure = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->exposure).GetWithDefault(0.0f);
    GfVec3f color = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->color).GetWithDefault(GfVec3f(1.0f, 1.0f, 1.0f));
    auto value = [&](const char* name) { return sceneDelegate->GetLightParamValue(id, TfToken(name)); };
    if (value("enableColorTemperature").GetWithDefault(false))
        color = GfCompMult(color, UsdLuxBlackbodyTemperatureAsRgb(
            value("colorTemperature").GetWithDefault(6500.0f)));

    // Computed in double, stored as float: radiance = intensity * 2^exposure * color.
    const double scale = double(intensity) * std::exp2(double(exposure));
    HdRTDomeData dome;
    dome.radiance = GfVec3f(float(scale * color[0]), float(scale * color[1]), float(scale * color[2]));
    dome.visible = sceneDelegate->GetVisible(id);
    const float diffuse = value("diffuse").GetWithDefault(1.0f);

    HDRT_DEBUG("dome %s: intensity %.6g exposure %.6g color (%.6g, %.6g, %.6g) -> radiance (%.6f, %.6f, %.6f), visible %d",
               id.GetText(), intensity, exposure, color[0], color[1], color[2],
               dome.radiance[0], dome.radiance[1], dome.radiance[2], int(dome.visible));
    if (_type == HdPrimTypeTokens->domeLight) {
        dome.radiance *= diffuse;
        bool valid = true;
        for (int k=0; k<3; ++k) valid = valid && std::isfinite(dome.radiance[k]) && dome.radiance[k] >= 0;
        if (valid) param->GetScene()->SetDome(id, dome);
        else {
            param->GetScene()->RemoveDome(id);
            HdRTLog("unsupported dome %s: radiance must be finite and nonnegative", id.GetText());
        }
    }
    else {
        HdRTVkLight light;
        const GfMatrix4d transform = sceneDelegate->GetTransform(id);
        const GfVec3d p = transform.Transform(GfVec3d(0));
        GfVec3d u = transform.TransformDir(GfVec3d(1, 0, 0));
        GfVec3d v = transform.TransformDir(GfVec3d(0, 1, 0));
        GfVec3d z = transform.TransformDir(GfVec3d(0, 0, 1));
        GfVec3d n = -GfCross(u, v).GetNormalized();
        if (GfDot(n, z) > 0) n = -n; // reflection keeps authored local -Z emission
        constexpr double pi = 3.141592653589793;
        double area = 0, normalization = 1, radius = 0, cosine = 0;
        int type = 0;
        bool valid = dome.visible;
        if (_type == HdPrimTypeTokens->distantLight) {
            type = 3;
            const double angle = value("angle").GetWithDefault(0.53f);
            valid = valid && angle >= 0 && angle <= 360 && z.GetLength() > 0;
            cosine = std::cos(angle * pi / 360.0);
            const double halfSin = std::sin(angle * pi / 720.0);
            area = 4*pi*halfSin*halfSin; // solid angle, stable for sub-float cosine changes
            n = -z.GetNormalized();
            const double sine = std::sin(angle * pi / 360.0);
            normalization = angle == 0 ? 1 : pi * (angle <= 180 ? sine*sine : 2-sine*sine);
        } else if (_type == HdPrimTypeTokens->sphereLight) {
            type = 2;
            const double sx = u.GetLength(), sy = v.GetLength(), sz = z.GetLength();
            radius = value("radius").GetWithDefault(0.5f) * sx;
            valid = valid && sx > 0 && std::abs(sx-sy) < 1e-5*sx && std::abs(sx-sz) < 1e-5*sx
                && std::abs(GfDot(u,v)) < 1e-5*sx*sx && std::abs(GfDot(u,z)) < 1e-5*sx*sx
                && std::abs(GfDot(v,z)) < 1e-5*sx*sx && radius > 0;
            area = 4*pi*radius*radius;
        } else if (_type == HdPrimTypeTokens->diskLight) {
            type = 1;
            radius = value("radius").GetWithDefault(0.5f);
            valid = valid && radius > 0;
            u *= radius; v *= radius;
            area = pi * GfCross(u,v).GetLength();
        } else {
            const double w = value("width").GetWithDefault(1.0f);
            const double h = value("height").GetWithDefault(1.0f);
            valid = valid && w > 0 && h > 0;
            u *= w * 0.5; v *= h * 0.5;
            area = 4 * GfCross(u,v).GetLength();
        }
        if (type != 3) { valid = valid && area > 0; normalization = area; }
        const double divisor = value("normalize").GetWithDefault(false) ? normalization : 1;
        for (int k=0; k<3; ++k) {
            light.positionType[k] = float(p[k]); light.basisUArea[k] = float(u[k]);
            light.basisVCos[k] = float(v[k]); light.normalRadius[k] = float(n[k]);
            light.radiance[k] = float(dome.radiance[k] * diffuse / divisor);
            light.selection[k+1] = float(dome.radiance[k] / divisor);
        }
        light.positionType[3] = float(type); light.basisUArea[3] = float(area);
        light.basisVCos[3] = float(cosine); light.normalRadius[3] = float(radius);
        for (float f : light.positionType) valid = valid && std::isfinite(f);
        for (float f : light.basisUArea) valid = valid && std::isfinite(f);
        for (float f : light.basisVCos) valid = valid && std::isfinite(f);
        for (float f : light.normalRadius) valid = valid && std::isfinite(f);
        for (float f : light.radiance) valid = valid && std::isfinite(f) && f >= 0;
        for (float f : light.selection) valid = valid && std::isfinite(f) && f >= 0;
        if (valid) param->GetScene()->SetLight(id, light);
        else {
            param->GetScene()->RemoveLight(id);
            if (dome.visible) HdRTLog("unsupported light %s: invalid dimensions/energy or nonuniform sphere transform", id.GetText());
        }
    }

    *dirtyBits = HdLight::Clean;
}

void
HdRTLight::Finalize(HdRenderParam *renderParam)
{
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);
    if (param && !GetId().IsEmpty()) {
        param->GetScene()->RemoveDome(GetId());
        param->GetScene()->RemoveLight(GetId());
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
