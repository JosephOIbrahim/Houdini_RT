#include "aov.h"
#include <iostream>
#include <stdexcept>
#include <vector>
PXR_NAMESPACE_USING_DIRECTIVE
#define CHECK(value) do { if (!(value)) throw std::runtime_error("FAILED: " #value); } while (false)

int main()
{
    try {
        const std::vector<TfToken> names = {HdAovTokens->normal, HdAovTokens->Neye,
            HdAovTokens->depth, HdAovTokens->cameraDepth, HdAovTokens->primId,
            HdAovTokens->elementId, HdAovTokens->instanceId, HdRTAlbedoAov()};
        HdRTVkGuide hit{};
        hit.normalDepth[0] = 1; hit.normalDepth[3] = 0.25f;
        hit.eyeNormalDistance[1] = 1; hit.eyeNormalDistance[3] = 5.75f;
        hit.ids[0] = 20000001; hit.ids[1] = 41; hit.ids[2] = -1; hit.ids[3] = 1;
        hit.albedo[0] = 0.2f; hit.albedo[1] = 0.3f; hit.albedo[2] = 0.4f;
        for (const auto& name : names) {
            const HdFormat format = HdRTAovFormat(name);
            CHECK(format != HdFormatInvalid && HdRTAovSupportsFormat(name, format));
            HdRTRenderBuffer buffer(SdfPath::EmptyPath());
            CHECK(buffer.Allocate(GfVec3i(3,2,1), format, false));
            const bool integer = format == HdFormatInt32;
            const int clearInt = -7;
            const float clearFloat[3] = {-2,-2,-2};
            if (integer) buffer.Clear(1, &clearInt);
            else buffer.Clear(3, clearFloat);
            // An offset pixel tests the same full-buffer addressing used for crops.
            HdRTWriteGuide(&buffer, GfVec3i(1,1,1), name, hit);
            HdRTVkGuide miss = hit;
            miss.ids[3] = 0;
            HdRTWriteGuide(&buffer, GfVec3i(0,0,1), name, miss);
            void* mapped = buffer.Map();
            if (integer) {
                const int* data = static_cast<const int*>(mapped);
                const int index = name == HdAovTokens->primId ? 0 : name == HdAovTokens->elementId ? 1 : 2;
                CHECK(data[4] == hit.ids[index] && data[0] == clearInt && data[5] == clearInt);
            } else {
                const float* data = static_cast<const float*>(mapped);
                const size_t count = HdGetComponentCount(format);
                const float* expected = name == HdAovTokens->normal ? hit.normalDepth
                    : name == HdAovTokens->Neye ? hit.eyeNormalDistance
                    : name == HdAovTokens->depth ? hit.normalDepth + 3
                    : name == HdAovTokens->cameraDepth ? hit.eyeNormalDistance + 3 : hit.albedo;
                for (size_t c=0; c<count; ++c) CHECK(data[4*count+c] == expected[c]);
                CHECK(data[0] == -2 && data[5*count] == -2);
            }
            buffer.Unmap();
        }
        CHECK(HdRTAovFormat(TfToken("primvars:normal")) == HdFormatInvalid);
        CHECK(HdRTAovFormat(TfToken("unimplemented")) == HdFormatInvalid);
        CHECK(!HdRTAovSupportsFormat(HdAovTokens->normal, HdFormatFloat32Vec4));
        CHECK(!HdRTAovSupportsFormat(HdAovTokens->primId, HdFormatFloat32));
        CHECK(!HdRTAovSupportsFormat(HdAovTokens->depth, HdFormatFloat16));
        CHECK(!HdRTAovSupportsFormat(HdAovTokens->color, HdFormatInt32Vec4));
        CHECK(HdRTAovSupportsFormat(HdAovTokens->color, HdFormatFloat16Vec4));
        CHECK(HdRTAovSupportsFormat(HdAovTokens->color, HdFormatUNorm8Vec4));
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    std::cout << "AOV classification, typed guide outputs, integer precision, offsets and misses passed\n";
}
