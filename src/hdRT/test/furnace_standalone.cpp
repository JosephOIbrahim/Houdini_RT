// furnace_standalone.cpp - runs the step 3 kernel WITHOUT Hydra or Houdini and prints image statistics.
//
// It links only vkFurnace.cpp and Vulkan, so it answers one question on its own: does the kernel trace
// the furnace correctly on this machine? The delegate adds nothing to the pixels; it only moves them.
//
//   furnace_standalone <furnace.spv> [--size W H] [--spp N] [--rho R] [--emission r g b] [--bounces B]
//                      [--seed S] [--fov DEGREES] [--open-face 0..5] [--no-validation] [--software]
//                      [--raw out.f32]
//
// The camera sits at the centre of the unit cube and looks down -Z, like the exam's /cam.
// It prints the mean of each channel next to the analytic value E * (1 - rho^(bounces+1)) / (1 - rho).
// It does not judge: the exit code is 0 if the kernel ran, 14 if the validation layer reported an error,
// 15 if the GPU could not be brought up or a Vulkan call failed.
//
// Build (not part of the plugin; see README.md):
//   cl /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"%HFS%\toolkit\include" /I.. furnace_standalone.cpp ..\vkFurnace.cpp
//      /link /LIBPATH:"%HFS%\custom\houdini\dsolib" vulkan-1.lib
//   g++ -std=c++20 -O2 -I.. furnace_standalone.cpp ../vkFurnace.cpp -lvulkan -o furnace_standalone
#include "vkFurnace.h"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

int main(int argc, char** argv)
{
    if (argc < 2)
    {
        std::printf("usage: furnace_standalone <furnace.spv> [--size W H] [--spp N] [--rho R] [--emission r g b] [--bounces B] "
                    "[--seed S] [--fov DEGREES] [--open-face 0..5] [--no-validation] [--software] [--raw out.f32]\n");
        return 15;
    }
    HdRTVkOptions options;
    options.spvPath = argv[1];
    options.validation = true;
    HdRTVkFrame frame;
    frame.width = 256;
    frame.height = 256;
    frame.rho = 0.5f;
    frame.bounces = 16;
    frame.seed = 1;
    int spp = 64;
    double fov = 30.0;
    const char* raw = nullptr;
    for (int i = 2; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--size") && i + 2 < argc) { frame.width = uint32_t(std::atoi(argv[++i])); frame.height = uint32_t(std::atoi(argv[++i])); }
        else if (!std::strcmp(argv[i], "--spp") && i + 1 < argc) spp = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--rho") && i + 1 < argc) frame.rho = float(std::atof(argv[++i]));
        else if (!std::strcmp(argv[i], "--emission") && i + 3 < argc) { for (int c = 0; c < 3; ++c) frame.emission[c] = float(std::atof(argv[++i])); }
        else if (!std::strcmp(argv[i], "--bounces") && i + 1 < argc) frame.bounces = uint32_t(std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) frame.seed = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--fov") && i + 1 < argc) fov = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--open-face") && i + 1 < argc) options.openFace = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--no-validation")) options.validation = false;
        else if (!std::strcmp(argv[i], "--software")) options.allowSoftwareDevice = true;
        else if (!std::strcmp(argv[i], "--raw") && i + 1 < argc) raw = argv[++i];
    }

    // Camera at the box centre looking down -Z. Both matrices are written in the layout a GLSL mat4
    // reads (column-major, column vectors), which is the memory order of the equivalent GfMatrix4d.
    const double aspect = double(frame.width) / double(frame.height);
    const double f = 1.0 / std::tan(0.5 * fov * 3.14159265358979323846 / 180.0);
    const double zn = 0.01, zf = 100.0;
    const double a = f / aspect, b = f, c = (zf + zn) / (zn - zf), d = 2.0 * zf * zn / (zn - zf);
    const float invProj[16] = { float(1.0 / a), 0, 0, 0,   0, float(1.0 / b), 0, 0,   0, 0, 0, float(1.0 / d),   0, 0, -1, float(c / d) };
    const float invView[16] = { 1, 0, 0, 0,   0, 1, 0, 0,   0, 0, 1, 0,   0.5f, 0.5f, 0.5f, 1 };
    std::memcpy(frame.invProj, invProj, sizeof(invProj));
    std::memcpy(frame.invView, invView, sizeof(invView));

    const auto t0 = std::chrono::steady_clock::now();
    const auto since = [](std::chrono::steady_clock::time_point t) { return std::chrono::duration<double>(std::chrono::steady_clock::now() - t).count(); };
    HdRTVkFurnace gpu;
    std::string error;
    if (!gpu.Init(options, &error))
    {
        std::printf("furnace_standalone: %s\n", error.c_str());
        return 15;
    }
    std::printf("device: %s | validation layer: %s\n", gpu.DeviceName().c_str(), gpu.ValidationActive() ? "active" : "off");
    if (!gpu.BeginFrame(frame, &error))
    {
        std::printf("furnace_standalone: %s\n", error.c_str());
        return 15;
    }
    const double initSeconds = since(t0);
    const auto t1 = std::chrono::steady_clock::now();
    for (int i = 0; i < spp; ++i)
        if (!gpu.RenderPass(uint32_t(i), &error))
        {
            std::printf("furnace_standalone: %s\n", error.c_str());
            return 15;
        }
    const double passSeconds = since(t1);
    const auto t2 = std::chrono::steady_clock::now();

    const float* sums = gpu.Accumulation();
    const size_t pixels = size_t(frame.width) * frame.height;
    double mean[4] = { 0, 0, 0, 0 }, lo[4] = { 1e30, 1e30, 1e30, 1e30 }, hi[4] = { -1e30, -1e30, -1e30, -1e30 };
    std::vector<float> image(pixels * 4);
    for (size_t p = 0; p < pixels; ++p)
        for (int ch = 0; ch < 4; ++ch)
        {
            const double v = double(sums[4 * p + ch]) / spp;
            image[4 * p + ch] = float(v);
            mean[ch] += v;
            lo[ch] = std::fmin(lo[ch], v);
            hi[ch] = std::fmax(hi[ch], v);
        }
    std::printf("timing: bring-up %.3f s | %d passes %.3f s (%.2f ms each) | reading the image back %.3f s\n", initSeconds, spp, passSeconds,
                1000.0 * passSeconds / (spp > 0 ? spp : 1), since(t2));
    const double k = (1.0 - std::pow(double(frame.rho), double(frame.bounces) + 1.0)) / (1.0 - double(frame.rho));
    std::printf("%ux%u, %d spp, rho %.6f, bounces %u, seed %u, open face %d\n", frame.width, frame.height, spp, double(frame.rho), frame.bounces,
                frame.seed, options.openFace);
    for (int ch = 0; ch < 3; ++ch)
    {
        const double want = double(frame.emission[ch]) * k;
        const double got = mean[ch] / double(pixels);
        std::printf("channel %d: mean %.7f  analytic %.7f  rel err %+.3e  | min %.7f max %.7f\n", ch, got, want, (got - want) / want, lo[ch], hi[ch]);
    }
    std::printf("alpha: mean %.7f min %.7f max %.7f\n", mean[3] / double(pixels), lo[3], hi[3]);
    if (raw)
    {
        std::FILE* out = std::fopen(raw, "wb");
        if (out)
        {
            std::fwrite(image.data(), sizeof(float), image.size(), out);
            std::fclose(out);
            std::printf("wrote %s (%ux%u RGBA float32, bottom row first)\n", raw, frame.width, frame.height);
        }
    }
    const int errors = gpu.ValidationErrors();
    const int warnings = gpu.ValidationWarnings();
    gpu.Destroy();
    std::printf("validation: %d errors, %d warnings (%d errors after teardown)\n", errors, warnings, gpu.ValidationErrors());
    return gpu.ValidationErrors() > 0 ? 14 : 0;
}
