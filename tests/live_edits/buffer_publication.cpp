#include "renderBuffer.h"
#include "pxr/base/gf/vec3i.h"
#include <iostream>
#include <thread>
#include <atomic>
#undef CHECK
#define CHECK(test) do { if (!(test)) { std::cerr << "FAILED: " #test << std::endl; return 1; } } while(false)
PXR_NAMESPACE_USING_DIRECTIVE
int main()
{
    HdRTRenderBuffer front(SdfPath("/color"));
    CHECK(front.Allocate(GfVec3i(2,1,1), HdFormatFloat32Vec4, false));
    const float oldColor[4] = {1,2,3,4};
    front.Clear(4, oldColor);
    float* old = static_cast<float*>(front.Map());
    auto first = front.CreateStagingBuffer();
    CHECK(first && first->GetWidth() == 2);
    const float color2[4] = {5,6,7,8};
    first->Clear(4, color2);
    float* firstData = static_cast<float*>(first->Map());
    CHECK(!front.PublishFrom(*first)); // A writable staging mapping must close first.
    first->Unmap();
    CHECK(front.PublishFrom(*first));
    CHECK(first->GetFormat() == HdFormatInvalid);
    first.reset(); // A reader still retains the retired front allocation.
    CHECK(old[0] == 1 && old[7] == 4);
    float* middle = static_cast<float*>(front.Map());
    CHECK(middle == firstData && middle[0] == 5 && middle[7] == 8);
    auto second = front.CreateStagingBuffer();
    const float color3[4] = {9,10,11,12};
    second->Clear(4, color3);
    CHECK(front.PublishFrom(*second));
    second.reset();
    CHECK(old[0] == 1 && middle[0] == 5);
    front.Unmap();
    CHECK(middle[7] == 8 && front.IsMapped());
    front.Unmap();
    CHECK(!front.IsMapped());
    float* latest = static_cast<float*>(front.Map());
    CHECK(latest[0] == 9 && latest[7] == 12);
    front.Unmap();
    CHECK(!front.PublishFrom(front));
    std::atomic<bool> bad{false};
    std::atomic<bool> done{false};
    std::thread reader([&] {
        while (!done.load()) {
            const float* pixels = static_cast<const float*>(front.Map());
            const float value = pixels[0];
            std::this_thread::yield();
            if (pixels[0] != value) bad.store(true);
            front.Unmap();
        }
    });
    for (int i = 0; i < 200; ++i) {
        auto staging = front.CreateStagingBuffer();
        const float color[4] = {float(i),0,0,1};
        staging->Clear(4, color);
        if (!front.PublishFrom(*staging)) bad.store(true);
    }
    done.store(true);
    reader.join();
    CHECK(!bad.load() && !front.IsMapped());
    std::cout << "Buffer publication smoke passed: O(1) pointer swap, mapped retirement, nested readers, concurrent Map/publish\n";
}
