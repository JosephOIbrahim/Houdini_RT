// Bounded MaterialX OpenPBR constants, translated on the CPU during Hydra Sync.
#ifndef HDRT_MATERIAL_H
#define HDRT_MATERIAL_H
#include "vkFurnace.h"
#include "pxr/pxr.h"
#include "pxr/imaging/hd/material.h"
#include <memory>
#include <string>

PXR_NAMESPACE_OPEN_SCOPE

struct HdRTMaterialData
{
    HdRTVkMaterial parameters;
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
