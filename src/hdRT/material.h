// Bounded MaterialX OpenPBR constants, translated on the CPU during Hydra Sync.
#ifndef HDRT_MATERIAL_H
#define HDRT_MATERIAL_H
#include "vkFurnace.h"
#include "pxr/pxr.h"
#include "pxr/imaging/hd/material.h"
#include <memory>
#include <string>
#include <array>

PXR_NAMESPACE_OPEN_SCOPE

enum HdRTTextureSlot { HdRTBaseColor, HdRTBaseWeight, HdRTMetalness,
    HdRTSpecularColor, HdRTSpecularWeight, HdRTRoughness, HdRTIor, HdRTTextureSlotCount };

/// CPU metadata only. Image decoding and uploads belong to the render thread.
struct HdRTTextureBinding
{
    bool enabled = false;
    bool usesUV = true;
    std::string authoredPath, resolvedPath, colorSpace;
    int channels = 3;
    int addressU = 2, addressV = 2; // constant, clamp, periodic, mirror
    int filter = 1;                // closest, linear
    std::array<float, 4> fallback{0, 0, 0, 1};
    // u'=a*u+b*v+c, v'=d*u+e*v+f; constant coordinates have zero a,b,d,e.
    std::array<float, 6> uvTransform{1, 0, 0, 0, 1, 0};
    bool operator==(const HdRTTextureBinding&) const = default;
};

struct HdRTMaterialData
{
    HdRTVkMaterial parameters;
    std::array<HdRTTextureBinding, HdRTTextureSlotCount> textures;
    bool supported = false;
    std::string reason;
    bool operator==(const HdRTMaterialData& other) const;
};

/// No evaluation of arbitrary graphs. Unsupported resources return a diagnosed
/// whole-material fallback; unbound geometry keeps its existing displayColor.
HdRTMaterialData HdRTTranslateMaterial(const VtValue& resource);

class HdRTMaterial final : public HdMaterial
{
public:
    explicit HdRTMaterial(const SdfPath& id) : HdMaterial(id) {}
    HdDirtyBits GetInitialDirtyBitsMask() const override { return AllDirty; }
    void Sync(HdSceneDelegate*, HdRenderParam*, HdDirtyBits*) override;
    void Finalize(HdRenderParam*) override;
private:
    std::shared_ptr<const HdRTMaterialData> _published;
};

PXR_NAMESPACE_CLOSE_SCOPE
#endif
