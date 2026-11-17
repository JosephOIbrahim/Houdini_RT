// HoudiniRT: one exact contract for advertised outputs, validation and guide writes.
#ifndef HDRT_AOV_H
#define HDRT_AOV_H
#include "renderBuffer.h"
#include "vkFurnace.h"
#include "pxr/base/gf/vec3i.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/imaging/hd/types.h"

PXR_NAMESPACE_OPEN_SCOPE
inline const TfToken& HdRTAlbedoAov() { static const TfToken token("albedo"); return token; }

inline HdFormat HdRTAovFormat(const TfToken& name)
{
    if (name == HdAovTokens->color) return HdFormatFloat32Vec4;
    if (name == HdAovTokens->normal || name == HdAovTokens->Neye || name == HdRTAlbedoAov())
        return HdFormatFloat32Vec3;
    if (name == HdAovTokens->depth || name == HdAovTokens->cameraDepth) return HdFormatFloat32;
    if (name == HdAovTokens->primId || name == HdAovTokens->elementId || name == HdAovTokens->instanceId)
        return HdFormatInt32;
    return HdFormatInvalid;
}

inline bool HdRTAovSupportsFormat(const TfToken& name, HdFormat format)
{
    if (format == HdFormatInvalid) return false;
    if (name != HdAovTokens->color)
        return format != HdFormatInvalid && format == HdRTAovFormat(name);
    const size_t count = HdGetComponentCount(format);
    const HdFormat component = HdGetComponentFormat(format);
    return (count == 3 || count == 4) && (component == HdFormatFloat32 || component == HdFormatFloat16
        || component == HdFormatUNorm8 || component == HdFormatSNorm8);
}

/// Misses retain the binding's clear value. Guides are a coherent, unfiltered
/// pixel-center hit, not a sum or average of IDs/normals from different surfaces.
inline void HdRTWriteGuide(HdRTRenderBuffer* buffer, const GfVec3i& pixel,
                          const TfToken& name, const HdRTVkGuide& guide)
{
    if (!guide.ids[3]) return;
    if (name == HdAovTokens->normal) buffer->Write(pixel, 3, guide.normalDepth);
    else if (name == HdAovTokens->Neye) buffer->Write(pixel, 3, guide.eyeNormalDistance);
    else if (name == HdAovTokens->depth) buffer->Write(pixel, 1, guide.normalDepth + 3);
    else if (name == HdAovTokens->cameraDepth) buffer->Write(pixel, 1, guide.eyeNormalDistance + 3);
    else if (name == HdRTAlbedoAov()) buffer->Write(pixel, 3, guide.albedo);
    else if (name == HdAovTokens->primId) buffer->Write(pixel, 1, guide.ids);
    else if (name == HdAovTokens->elementId) buffer->Write(pixel, 1, guide.ids + 1);
    else if (name == HdAovTokens->instanceId) buffer->Write(pixel, 1, guide.ids + 2);
}
PXR_NAMESPACE_CLOSE_SCOPE
#endif
