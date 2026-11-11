// furnace_standalone.cpp - runs the path-tracing kernel WITHOUT Hydra or Houdini and checks it analytically.
//
// It links only vkFurnace.cpp and Vulkan, so it answers one question on its own: does the kernel trace a
// scene correctly on this machine? The delegate adds nothing to the pixels; it only moves them.
//
// The scene is the unit cube [0,1]^3, flat-shaded (24 vertices, face normals), with one albedo, under a
// uniform dome. The cube is convex, so every path that leaves a face escapes at once: each fully covered
// pixel must read exactly albedo * dome, with zero variance, whatever the bounce count. A camera ray that
// misses must read black with alpha 0 (the dome is invisible to the camera).
//
//   furnace_standalone <furnace.spv> [--size W H] [--spp N] [--albedo r g b] [--dome r g b] [--bounces B]
//                      [--seed S] [--fov DEGREES] [--empty] [--no-validation] [--software] [--raw out.f32]
//
// --empty uploads no scene: every pixel must be black with alpha 0.
// Exit code: 0 if the image is right, 16 if it is not, 14 if the validation layer reported an error,
// 15 if the GPU could not be brought up or a Vulkan call failed.
//
// Build (not part of the plugin; see README.md):
//   cl /std:c++20 /O2 /EHsc /D_CRT_SECURE_NO_WARNINGS /I"%HFS%\toolkit\include" /I.. furnace_standalone.cpp ..\vkFurnace.cpp
//      /link /LIBPATH:"%HFS%\custom\houdini\dsolib" vulkan-1.lib
#include "vkFurnace.h"

#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

namespace {

// The unit cube, one quad (two triangles) per face, each face with its own four vertices and normal.
void BuildCube(const float albedo[3], std::vector<float>* positions, std::vector<float>* normals, std::vector<uint32_t>* indices,
               std::vector<float>* triAlbedo)
{
    struct Face { float n[3]; float c[4][3]; };
    const Face faces[6] = {
        { { 0, 0, -1 }, { { 0, 0, 0 }, { 0, 1, 0 }, { 1, 1, 0 }, { 1, 0, 0 } } },
        { { 0, 0, 1 },  { { 0, 0, 1 }, { 1, 0, 1 }, { 1, 1, 1 }, { 0, 1, 1 } } },
        { { 0, -1, 0 }, { { 0, 0, 0 }, { 1, 0, 0 }, { 1, 0, 1 }, { 0, 0, 1 } } },
        { { 0, 1, 0 },  { { 0, 1, 0 }, { 0, 1, 1 }, { 1, 1, 1 }, { 1, 1, 0 } } },
        { { -1, 0, 0 }, { { 0, 0, 0 }, { 0, 0, 1 }, { 0, 1, 1 }, { 0, 1, 0 } } },
        { { 1, 0, 0 },  { { 1, 0, 0 }, { 1, 1, 0 }, { 1, 1, 1 }, { 1, 0, 1 } } },
    };
    for (const Face& f : faces)
    {
        const uint32_t base = uint32_t(positions->size() / 3);
        for (const auto& c : f.c)
            for (int k = 0; k < 3; ++k)
            {
                positions->push_back(c[k]);
                normals->push_back(f.n[k]);
            }
        const uint32_t quad[6] = { 0, 1, 2, 0, 2, 3 };
        for (uint32_t q : quad)
            indices->push_back(base + q);
        for (int t = 0; t < 2; ++t)
        {
            triAlbedo->insert(triAlbedo->end(), albedo, albedo + 3);
            triAlbedo->push_back(1.0f);
        }
    }
}

} // namespace

int main(int argc, char** argv)
{
    if (argc < 2)
    {
        std::printf("usage: furnace_standalone <furnace.spv> [--size W H] [--spp N] [--albedo r g b] [--dome r g b] [--bounces B] "
                    "[--seed S] [--fov DEGREES] [--empty] [--no-validation] [--software] [--raw out.f32]\n");
        return 15;
    }
    HdRTVkOptions options;
    options.spvPath = argv[1];
    options.validation = true;
    HdRTVkFrame frame;
    frame.width = 256;
    frame.height = 256;
    frame.bounces = 8;
    frame.seed = 1;
    frame.dome[0] = frame.dome[1] = frame.dome[2] = 1.0f;
    float albedo[3] = { 0.5f, 0.5f, 0.5f };
    int spp = 64;
    double fov = 30.0;
    bool empty = false;
    const char* raw = nullptr;
    for (int i = 2; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--size") && i + 2 < argc) { frame.width = uint32_t(std::atoi(argv[++i])); frame.height = uint32_t(std::atoi(argv[++i])); }
        else if (!std::strcmp(argv[i], "--spp") && i + 1 < argc) spp = std::atoi(argv[++i]);
        else if (!std::strcmp(argv[i], "--albedo") && i + 3 < argc) { for (int c = 0; c < 3; ++c) albedo[c] = float(std::atof(argv[++i])); }
        else if (!std::strcmp(argv[i], "--dome") && i + 3 < argc) { for (int c = 0; c < 3; ++c) frame.dome[c] = float(std::atof(argv[++i])); }
        else if (!std::strcmp(argv[i], "--bounces") && i + 1 < argc) frame.bounces = uint32_t(std::atoi(argv[++i]));
        else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) frame.seed = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--fov") && i + 1 < argc) fov = std::atof(argv[++i]);
        else if (!std::strcmp(argv[i], "--empty")) empty = true;
        else if (!std::strcmp(argv[i], "--no-validation")) options.validation = false;
        else if (!std::strcmp(argv[i], "--software")) options.allowSoftwareDevice = true;
        else if (!std::strcmp(argv[i], "--raw") && i + 1 < argc) raw = argv[++i];
    }

    // Camera outside the cube, looking at its centre, so three faces and empty background are in view.
    // Both matrices are written in the layout a GLSL mat4 reads (column-major, column vectors), which is
    // the memory order of the equivalent GfMatrix4d.
    const double aspect = double(frame.width) / double(frame.height);
    const double fl = 1.0 / std::tan(0.5 * fov * 3.14159265358979323846 / 180.0);
    const double zn = 0.01, zf = 100.0;
    const double a = fl / aspect, b = fl, c = (zf + zn) / (zn - zf), d = 2.0 * zf * zn / (zn - zf);
    const float invProj[16] = { float(1.0 / a), 0, 0, 0,   0, float(1.0 / b), 0, 0,   0, 0, 0, float(1.0 / d),   0, 0, -1, float(c / d) };
    const double eye[3] = { 2.6, 2.1, 3.2 }, at[3] = { 0.5, 0.5, 0.5 }, upHint[3] = { 0, 1, 0 };
    double fw[3] = { at[0] - eye[0], at[1] - eye[1], at[2] - eye[2] };
    const auto norm = [](double v[3]) { const double l = std::sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]); for (int k = 0; k < 3; ++k) v[k] /= l; };
    norm(fw);
    double rt[3] = { fw[1] * upHint[2] - fw[2] * upHint[1], fw[2] * upHint[0] - fw[0] * upHint[2], fw[0] * upHint[1] - fw[1] * upHint[0] };
    norm(rt);
    const double up[3] = { rt[1] * fw[2] - rt[2] * fw[1], rt[2] * fw[0] - rt[0] * fw[2], rt[0] * fw[1] - rt[1] * fw[0] };
    const float invView[16] = { float(rt[0]), float(rt[1]), float(rt[2]), 0,   float(up[0]), float(up[1]), float(up[2]), 0,
                                float(-fw[0]), float(-fw[1]), float(-fw[2]), 0,   float(eye[0]), float(eye[1]), float(eye[2]), 1 };
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
    if (!empty)
    {
        std::vector<float> positions, normals, triAlbedo;
        std::vector<uint32_t> indices;
        BuildCube(albedo, &positions, &normals, &indices, &triAlbedo);
        if (!gpu.SetScene(positions.data(), normals.data(), positions.size() / 3, indices.data(), triAlbedo.data(), indices.size() / 3, &error))
        {
            std::printf("furnace_standalone: %s\n", error.c_str());
            return 15;
        }
    }
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

    // Fully covered pixels must read albedo * dome exactly; empty pixels black with alpha 0.
    const float* sums = gpu.Accumulation();
    const size_t pixels = size_t(frame.width) * frame.height;
    size_t full = 0, emptyPx = 0;
    double worstFull = 0.0, worstEmpty = 0.0, mean[3] = { 0, 0, 0 };
    std::vector<float> image(pixels * 4);
    for (size_t p = 0; p < pixels; ++p)
    {
        double v[4];
        for (int ch = 0; ch < 4; ++ch)
            image[4 * p + ch] = float(v[ch] = double(sums[4 * p + ch]) / spp);
        if (v[3] > 0.9999)
        {
            ++full;
            for (int ch = 0; ch < 3; ++ch)
            {
                const double want = double(albedo[ch]) * double(frame.dome[ch]);
                worstFull = std::fmax(worstFull, std::fabs(v[ch] - want) / std::fmax(want, 1e-12));
                mean[ch] += v[ch];
            }
        }
        else if (v[3] < 1e-6)
        {
            ++emptyPx;
            for (int ch = 0; ch < 3; ++ch)
                worstEmpty = std::fmax(worstEmpty, std::fabs(v[ch]));
        }
    }
    std::printf("timing: bring-up %.3f s | %d passes %.3f s (%.2f ms each)\n", initSeconds, spp, passSeconds, 1000.0 * passSeconds / (spp > 0 ? spp : 1));
    std::printf("%ux%u, %d spp, bounces %u, seed %u, scene %s\n", frame.width, frame.height, spp, frame.bounces, frame.seed, empty ? "none" : "unit cube");
    for (int ch = 0; ch < 3 && full; ++ch)
        std::printf("channel %d: covered mean %.7f  analytic %.7f\n", ch, mean[ch] / double(full), double(albedo[ch]) * double(frame.dome[ch]));
    std::printf("pixels: %zu fully covered (worst relative error %.3e), %zu empty (worst |rgb| %.3e)\n", full, worstFull, emptyPx, worstEmpty);
    if (raw)
    {
        if (std::FILE* out = std::fopen(raw, "wb"))
        {
            std::fwrite(image.data(), sizeof(float), image.size(), out);
            std::fclose(out);
            std::printf("wrote %s (%ux%u RGBA float32, bottom row first)\n", raw, frame.width, frame.height);
        }
    }
    const int warnings = gpu.ValidationWarnings();
    gpu.Destroy();
    const int errors = gpu.ValidationErrors();
    std::printf("validation: %d errors, %d warnings (counted through teardown)\n", errors, warnings);
    if (errors > 0)
        return 14;
    const bool ok = empty ? (full == 0 && worstEmpty == 0.0 && emptyPx == pixels) : (full > 0 && emptyPx > 0 && worstFull < 1e-5 && worstEmpty == 0.0);
    std::printf("%s\n", ok ? "PASS" : "FAIL");
    return ok ? 0 : 16;
}
