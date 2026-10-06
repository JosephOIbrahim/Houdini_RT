// vkFurnace.h - the GPU side of the step 3 delegate: a closed-box furnace traced with Vulkan ray queries.
//
// This file and vkFurnace.cpp use Vulkan and the C++ standard library only (no USD, no Hydra), so the
// kernel can be lifted out and run on its own. The Vulkan sequence is the one from step 2
// (src/furnace/rayq_host.cpp, which passed verify/s2_rayquery.sh), refactored from a run-once main()
// into an object that renders one sample per pixel per call and never exits the host process.
//
// Threading: every method must be called from one thread at a time. The delegate calls all of them
// from the render thread, and Destroy() from the app thread after the render thread has stopped.
#ifndef HDRT_VK_FURNACE_H
#define HDRT_VK_FURNACE_H

#include <cstdint>
#include <memory>
#include <string>

/// One image to render. Matrices are in GfMatrix4d memory order (row-major, row vectors), which a GLSL
/// mat4 reads as the equivalent column-vector matrix, so they are uploaded as they are.
struct HdRTVkFrame
{
    uint32_t width = 0;             ///< pixels across the region to render
    uint32_t height = 0;            ///< pixels up the region to render (row 0 is the bottom)
    float invView[16] = {};         ///< camera space to world space
    float invProj[16] = {};         ///< normalised device coordinates to camera space
    bool orthographic = false;
    float rho = 0.5f;               ///< wall albedo
    float emission[3] = { 1.0f, 1.0f, 1.0f };
    uint32_t bounces = 16;          ///< bounces after the first hit
    uint32_t seed = 1;
};

/// How to bring the GPU up. Two HdRTVkOptions that compare equal need no re-initialisation.
struct HdRTVkOptions
{
    std::string spvPath;                ///< the compiled shaders/furnace.comp
    bool validation = false;            ///< load VK_LAYER_KHRONOS_validation if it is installed
    bool allowSoftwareDevice = false;   ///< accept a CPU rasteriser (tests on machines without a GPU only)
    int openFace = -1;                  ///< 0..5 leaves one wall of the cube out (a debugging aid)

    bool operator==(const HdRTVkOptions& o) const
    {
        return spvPath == o.spvPath && validation == o.validation && allowSoftwareDevice == o.allowSoftwareDevice && openFace == o.openFace;
    }
    bool operator!=(const HdRTVkOptions& o) const { return !(*this == o); }
};

class HdRTVkFurnace
{
public:
    HdRTVkFurnace();
    ~HdRTVkFurnace();

    /// Creates the Vulkan instance (with the validation layer when asked for and installed), picks a
    /// device that supports ray queries (a discrete GPU if there is one; a software device only when
    /// options.allowSoftwareDevice says so), builds the unit-cube acceleration structure and the compute
    /// pipeline. Returns false and fills *error on any failure. Safe to call again after Destroy().
    bool Init(const HdRTVkOptions& options, std::string* error);

    bool IsReady() const;

    /// Sets the image size and parameters for the passes that follow and zeroes the accumulation.
    bool BeginFrame(const HdRTVkFrame& frame, std::string* error);

    /// Adds one sample per pixel to the accumulation and waits for the GPU to finish.
    bool RenderPass(uint32_t passIndex, std::string* error);

    /// width * height * 4 floats: rgb = summed radiance, a = number of samples whose camera ray hit the
    /// box. Row 0 is the bottom of the image. Valid between BeginFrame and the next BeginFrame/Destroy.
    const float* Accumulation() const;

    /// Destroys every Vulkan object this class created, in order. Safe to call twice.
    void Destroy();

    const std::string& DeviceName() const;      ///< VkPhysicalDeviceProperties::deviceName of the device in use
    bool ValidationActive() const;              ///< true only if the layer was loaded and its messenger exists
    int ValidationErrors() const;               ///< messages of severity ERROR seen so far
    int ValidationWarnings() const;

private:
    HdRTVkFurnace(const HdRTVkFurnace&) = delete;
    HdRTVkFurnace& operator=(const HdRTVkFurnace&) = delete;

    struct Impl;
    std::unique_ptr<Impl> _impl;
};

#endif // HDRT_VK_FURNACE_H
