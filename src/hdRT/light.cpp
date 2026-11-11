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

#include <cmath>

PXR_NAMESPACE_OPEN_SCOPE

HdRTLight::HdRTLight(SdfPath const& id)
    : HdLight(id)
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

    // Stop the render before the lighting changes; the render pass starts a fresh one afterwards.
    param->AcquireSceneForEdit();

    const float intensity = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->intensity).GetWithDefault(1.0f);
    const float exposure = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->exposure).GetWithDefault(0.0f);
    const GfVec3f color = sceneDelegate->GetLightParamValue(
        id, HdLightTokens->color).GetWithDefault(GfVec3f(1.0f, 1.0f, 1.0f));

    // Computed in double, stored as float: radiance = intensity * 2^exposure * color.
    const double scale = double(intensity) * std::exp2(double(exposure));
    HdRTDomeData dome;
    dome.radiance = GfVec3f(float(scale * color[0]), float(scale * color[1]), float(scale * color[2]));
    dome.visible = sceneDelegate->GetVisible(id);

    HDRT_DEBUG("dome %s: intensity %.6g exposure %.6g color (%.6g, %.6g, %.6g) -> radiance (%.6f, %.6f, %.6f), visible %d",
               id.GetText(), intensity, exposure, color[0], color[1], color[2],
               dome.radiance[0], dome.radiance[1], dome.radiance[2], int(dome.visible));
    param->GetScene()->SetDome(id, dome);

    *dirtyBits = HdLight::Clean;
}

void
HdRTLight::Finalize(HdRenderParam *renderParam)
{
    HdRTRenderParam *param = static_cast<HdRTRenderParam*>(renderParam);
    if (param && !GetId().IsEmpty()) {
        param->AcquireSceneForEdit();
        param->GetScene()->RemoveDome(GetId());
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
