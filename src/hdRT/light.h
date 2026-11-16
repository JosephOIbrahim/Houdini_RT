//
// Copyright 2024 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03, pxr/imaging/plugin/hdEmbree/light.h.
// Only the untextured dome light is kept; transforms, shaping, textures and light samplers are removed.
// See NOTICE.md.
//
#ifndef HDRT_LIGHT_H
#define HDRT_LIGHT_H

#include "pxr/pxr.h"
#include "pxr/imaging/hd/light.h"

PXR_NAMESPACE_OPEN_SCOPE

///
/// \class HdRTLight
///
/// A Hydra dome light sprim. Sync() reads intensity, exposure and color and publishes one uniform
/// radiance, intensity * 2^exposure * color, to the delegate's HdRTScene. The dome lights paths that
/// leave the scene; it is never visible to camera rays (a camera miss is black with alpha 0), whatever
/// domeLightCameraVisibility says (Joe's decision of 2026-11-11). Textures are not supported yet.
///
class HdRTLight final : public HdLight
{
public:
    HdRTLight(SdfPath const& id);
    ~HdRTLight() override = default;

    void Sync(HdSceneDelegate *sceneDelegate,
              HdRenderParam   *renderParam,
              HdDirtyBits     *dirtyBits) override;

    HdDirtyBits GetInitialDirtyBitsMask() const override;

    /// Removes this light from the scene.
    void Finalize(HdRenderParam *renderParam) override;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_LIGHT_H
