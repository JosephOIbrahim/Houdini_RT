#include "sceneTextures.h"
#include <algorithm>
#include <set>
#include <limits>
PXR_NAMESPACE_OPEN_SCOPE
int HdRTSceneTextures::AddImage(std::shared_ptr<const HdRTVkTexture> image,std::string* error) {
    for (size_t i=0;i<images.size();++i) if (images[i]->id==image->id) return int(i);
    uint64_t count=uint64_t(image->width)*image->height;
    for (const auto& old:images) count+=uint64_t(old->width)*old->height;
    if (images.size()>=128 || count>33554432) { *error="scene exceeds 128 images or 32M decoded texels"; return -1; }
    images.push_back(std::move(image)); return int(images.size()-1);
}
bool HdRTSceneTextures::Prepare(const HdRTWorldScene& scene,HdRTImageAssets& cache,const std::function<bool()>& wait) {
    std::set<const HdRTMaterialData*> needed;
    for (const auto& entry:scene.meshes) {
        HdRTVkMaterial unused; std::string reason; const HdRTMaterialData* material=nullptr;
        if (HdRTResolveMeshMaterial(scene,*entry.second,&unused,&reason,&material)) needed.insert(material);
    }
    auto diagnostic=[&](const std::string& text) { if (diagnostics.size()<16384) diagnostics+=text+'\n'; };
    for (const auto& entry:scene.materials) {
        const auto* material=entry.second.get();
        if (!needed.count(material)) continue;
        std::array<int32_t,8> indices; indices.fill(-1);
        const size_t imageStart=images.size(), bindingStart=bindings.size();
        std::string error;
        for (int slot=0;slot<HdRTTextureSlotCount;++slot) {
            const auto& source=material->textures[slot];
            if (!source.enabled) continue;
            HdRTImageRequest request;
            request.path=source.resolvedPath.empty()?source.authoredPath:source.resolvedPath;
            request.colorSpace=source.colorSpace; request.channels=source.channels;
            request.minimum=slot==HdRTIor ? 1.f : 0.f; request.maximum=slot==HdRTIor ? 3.f : 1.f;
            auto image=cache.Load(request,wait,&error);
            if (!wait()) return false;
            if (!image) break;
            int index=AddImage(image,&error); if (index<0) break;
            HdRTVkTextureBinding binding;
            binding.config[0]=index; binding.config[1]=source.addressU; binding.config[2]=source.addressV; binding.config[3]=source.filter;
            std::copy_n(source.uvTransform.data(),3,binding.uvU); std::copy_n(source.uvTransform.data()+3,3,binding.uvV);
            std::copy_n(source.fallback.data(),4,binding.fallback);
            if (bindings.size()>=65536) { error="scene exceeds 65536 image bindings"; break; }
            indices[slot]=int32_t(bindings.size()); bindings.push_back(binding);
        }
        if (!error.empty()) {
            images.resize(imageStart); bindings.resize(bindingStart);
            materialErrors[material]=error;
            diagnostic(entry.first.GetString()+": "+error+"; whole-material displayColor Lambert fallback");
        } else materialBindings[material]=indices;
    }
    for (const auto& entry:scene.domes) {
        const auto& source=entry.second;
        if (!source.visible || !source.hasTexture) continue;
        std::string error=source.supported ? "" : source.reason;
        HdRTImageRequest request;
        request.path=source.resolvedPath.empty()?source.authoredPath:source.resolvedPath;
        request.colorSpace=source.colorSpace;
        request.environment=true; request.maximum=1e30f;
        auto image=error.empty()?cache.Load(request,wait,&error):nullptr;
        if (!wait()) return false;
        if (image && source.textureFormat=="automatic" && image->width!=image->height*2)
            error="automatic HDRI format requires a 2:1 latlong image";
        if (image && environments.size()>=8) error="scene exceeds eight textured domes";
        int index=-1;
        if (image && error.empty()) index=AddImage(image,&error);
        if (index<0) {
            diagnostic(entry.first.GetString()+": "+error+"; textured dome excluded"); continue;
        }
        HdRTVkEnvironment environment; environment.texture=index;
        for (int c=0;c<3;++c) environment.radiance[c]=source.radiance[c];
        for (int r=0;r<4;++r) for (int c=0;c<4;++c) environment.worldToDome[r*4+c]=float(source.worldToDome[r][c]);
        environments.push_back(environment);
    }
    return true;
}
PXR_NAMESPACE_CLOSE_SCOPE
