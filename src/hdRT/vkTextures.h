// Data-only texture interface shared by the delegate and standalone GPU tests.
#ifndef HDRT_VK_TEXTURES_H
#define HDRT_VK_TEXTURES_H
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

struct HdRTVkTexture {
    std::string id; // cache identity, including file revision and color transform
    uint32_t width = 0, height = 0;
    // Top-left origin, scene-linear RGB; scalar images contain raw R in RGB.
    std::vector<float> rgba;
};

struct HdRTVkTextureBinding {
    int32_t config[4] = {-1, 2, 2, 1}; // texture, U/V address, closest0/linear1
    float uvU[4] = {1,0,0,0}, uvV[4] = {0,1,0,0};
    float fallback[4] = {};
};
static_assert(sizeof(HdRTVkTextureBinding) == 64, "texture binding std430");

struct HdRTVkEnvironment {
    int32_t texture = -1;
    float radiance[3] = {1,1,1};
    // Column-major world-to-dome orthonormal direction matrix, including poleAxis.
    float worldToDome[16] = {1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1};
};
#endif
