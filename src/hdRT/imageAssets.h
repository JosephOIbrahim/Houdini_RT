// Bounded native image decoding. File work never runs in Sync, Execute or the UI.
#ifndef HDRT_IMAGE_ASSETS_H
#define HDRT_IMAGE_ASSETS_H
#include "vkTextures.h"
#include <functional>
#include <future>
#include <map>
#include <OpenColorIO/OpenColorIO.h>

struct HdRTImageRequest {
    std::string path, colorSpace;
    int channels=3;
    float minimum=0, maximum=1;
    bool environment=false;
};
class HdRTImageAssets {
public:
    using Image=std::shared_ptr<const HdRTVkTexture>;
    // wait() returns false on cancellation; may acknowledge pause while the CPU decoder runs.
    Image Load(const HdRTImageRequest&, const std::function<bool()>& wait, std::string* error);
    uint64_t Decodes() const { return _decodes; }
    uint64_t CacheHits() const { return _hits; }
private:
    struct Result { Image image; std::string error; };
    static Result Decode(const HdRTImageRequest&, const std::string& key, OCIO_NAMESPACE::ConstConfigRcPtr config);
    std::future<Result> _pending;
    std::string _pendingKey;
    std::map<std::string,std::weak_ptr<const HdRTVkTexture>> _cache;
    uint64_t _decodes=0,_hits=0;
};
#endif
