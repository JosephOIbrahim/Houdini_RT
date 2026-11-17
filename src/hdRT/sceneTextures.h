#ifndef HDRT_SCENE_TEXTURES_H
#define HDRT_SCENE_TEXTURES_H
#include "imageAssets.h"
#include "world.h"
#include <array>
PXR_NAMESPACE_OPEN_SCOPE
struct HdRTSceneTextures {
    std::vector<std::shared_ptr<const HdRTVkTexture>> images;
    std::vector<HdRTVkTextureBinding> bindings;
    std::vector<HdRTVkEnvironment> environments;
    std::map<const HdRTMaterialData*,std::array<int32_t,8>> materialBindings;
    std::map<const HdRTMaterialData*,std::string> materialErrors;
    std::string diagnostics;
    bool Prepare(const HdRTWorldScene&,HdRTImageAssets&,const std::function<bool()>& wait);
private:
    int AddImage(std::shared_ptr<const HdRTVkTexture>,std::string* error);
};
PXR_NAMESPACE_CLOSE_SCOPE
#endif
