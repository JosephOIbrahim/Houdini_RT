#include "imageAssets.h"
#include <OpenImageIO/imageio.h>
#include <OpenColorIO/OpenColorIO.h>
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <sstream>
#include <thread>
namespace OCIO=OCIO_NAMESPACE;
namespace {
std::string ImageKey(const HdRTImageRequest& request,const OCIO::ConstConfigRcPtr& config) {
    const auto path=std::filesystem::u8path(request.path);
    if (!path.is_absolute()) throw std::runtime_error("USD asset is unresolved; an absolute resolved local path is required");
    const auto utf8=path.lexically_normal().generic_u8string();
    std::ostringstream out;
    out<<std::string(utf8.begin(),utf8.end())<<'|'<<std::filesystem::file_size(path)<<'|'
       <<std::filesystem::last_write_time(path).time_since_epoch().count()<<'|'
       <<request.colorSpace<<'|'<<request.channels<<'|'<<request.minimum<<'|'<<request.maximum<<'|'<<request.environment
       <<'|'<<config->getCacheID();
    return out.str();
}
}

HdRTImageAssets::Image HdRTImageAssets::Load(const HdRTImageRequest& request,
    const std::function<bool()>& wait,std::string* error)
{
    error->clear();
    std::string key;
    const auto config=OCIO::GetCurrentConfig();
    try {
        key=ImageKey(request,config);
        auto found=_cache.find(key);
        if (found!=_cache.end()) if (auto image=found->second.lock()) { ++_hits; return image; }
    } catch (const std::exception& e) { *error=request.path+": "+e.what(); return {}; }
    // Only one decoder is live per renderer. Superseding requests leave its future alive;
    // discarding a std::async future in Execute would accidentally join the decoder.
    while (_pending.valid()) {
        if (!wait()) return {};
        if (_pending.wait_for(std::chrono::milliseconds(2))!=std::future_status::ready) continue;
        auto result=_pending.get();
        if (result.image) _cache[_pendingKey]=result.image;
        if (_pendingKey==key) { *error=result.error; return result.image; }
    }
    _pendingKey=key; ++_decodes;
    _pending=std::async(std::launch::async,[request,key,config] { return Decode(request,key,config); });
    while (_pending.wait_for(std::chrono::milliseconds(2))!=std::future_status::ready)
        if (!wait()) return {};
    auto result=_pending.get();
    if (result.image) _cache[key]=result.image;
    for (auto it=_cache.begin();it!=_cache.end();) {
        if (it->second.expired()) it=_cache.erase(it); else ++it;
    }
    *error=result.error;
    return result.image;
}

HdRTImageAssets::Result HdRTImageAssets::Decode(const HdRTImageRequest& request,const std::string& key,OCIO::ConstConfigRcPtr config)
{
    Result result;
    try {
        if (ImageKey(request,config)!=key) throw std::runtime_error("image changed before decoding; refresh the scene");
        std::string ext=std::filesystem::u8path(request.path).extension().string();
        std::transform(ext.begin(),ext.end(),ext.begin(),[](unsigned char c){return char(std::tolower(c));});
        if (ext!=".png" && ext!=".jpg" && ext!=".jpeg" && ext!=".exr" && ext!=".hdr")
            throw std::runtime_error("supported image formats are PNG, JPEG, EXR and HDR; no UDIM/sequence expansion");
        OIIO::ImageSpec options;
        options.attribute("oiio:UnassociatedAlpha",1);
        auto input=OIIO::ImageInput::open(request.path,&options);
        if (!input) throw std::runtime_error(OIIO::geterror());
        const auto spec=input->spec();
        const uint64_t count=uint64_t(std::max(0,spec.width))*std::max(0,spec.height);
        if (!count || count>16777216 || spec.depth!=1 || spec.deep || spec.nchannels<1 || spec.nchannels>4
            || spec.x!=0 || spec.y!=0 || spec.z!=0 || spec.full_x!=0 || spec.full_y!=0 || spec.full_z!=0
            || spec.full_depth!=1 || spec.full_width!=spec.width || spec.full_height!=spec.height)
            throw std::runtime_error("requires a flat full-window image, 1..4 channels, at most 16M texels");
        if (spec.get_int_attribute("Orientation",1)!=1)
            throw std::runtime_error("image orientation metadata must be top-left (Orientation=1)");
        std::vector<float> source(size_t(count)*spec.nchannels);
        if (!input->read_image(0,0,0,-1,OIIO::TypeDesc::FLOAT,source.data()))
            throw std::runtime_error(input->geterror());
        input->close();
        if (ImageKey(request,config)!=key) throw std::runtime_error("image changed while decoding; refresh the scene");
        auto image=std::make_shared<HdRTVkTexture>();
        image->id=key; image->width=spec.width; image->height=spec.height; image->rgba.resize(size_t(count)*4);
        for (size_t i=0;i<count;++i) {
            for (int c=0;c<3;++c) image->rgba[i*4+c]=source[i*spec.nchannels+(request.channels==1 || spec.nchannels<3 ? 0 : c)];
            image->rgba[i*4+3]=1; // Opaque OpenPBR; image alpha is not an opacity implementation.
        }
        bool converted=false;
        if (request.channels==3) {
            std::string space=request.colorSpace;
            if (space.empty() || space=="auto") space=(ext==".png" || ext==".jpg" || ext==".jpeg") ? "srgb_texture" : "scene_linear";
            if (space!="raw" && space!="Raw") {
                const auto processor=config->getProcessor(space.c_str(),OCIO::ROLE_SCENE_LINEAR)->getDefaultCPUProcessor();
                converted=!processor->isNoOp();
                OCIO::PackedImageDesc pixels(image->rgba.data(),image->width,image->height,4);
                processor->apply(pixels);
            }
        }
        for (size_t i=0;i<count;++i) for (int c=0;c<request.channels;++c) {
            float& value=image->rgba[i*4+c];
            // The installed OCIO optimized sRGB processor maps white to 1.00002408.
            // Accommodate only transform roundoff, never out-of-range raw/data texels.
            const float epsilon=converted ? 3e-5f : 0.f;
            if (!std::isfinite(value) || value<request.minimum-epsilon || value>request.maximum+epsilon)
                throw std::runtime_error("decoded/converted texture values exceed the supported input range ["+
                    std::to_string(request.minimum)+", "+std::to_string(request.maximum)+"]");
            value=std::clamp(value,request.minimum,request.maximum);
        }
        result.image=std::move(image);
    } catch (const std::exception& e) { result.error=request.path+": "+e.what(); }
    catch (...) { result.error=request.path+": unknown image decoding failure"; }
    return result;
}
