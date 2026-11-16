// vkFurnace.h - the GPU side of the delegate: the scene's acceleration structure and the path-tracing
// kernel (shaders/furnace.comp), traced with Vulkan ray queries. (Named for step 3's furnace; kept to
// keep the diff small.)
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
#include <vector>

/// Borrowed immutable object-space data. Version tokens must change after remove/re-add.
struct HdRTVkMesh
{
    std::string id;
    uint64_t geometryVersion = 0, topologyVersion = 0;
    const float* positions = nullptr;
    const float* normals = nullptr;
    size_t vertexCount = 0;
    const uint32_t* indices = nullptr;
    size_t triangleCount = 0;
    float transform[12] = {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0};
    float albedo[4] = {0.18f, 0.18f, 0.18f, 1.0f};
    bool visible = true;
};

struct HdRTVkEditStats
{
    uint64_t blasBuilds = 0, blasRefits = 0, tlasBuilds = 0, tlasUpdates = 0;
    uint64_t geometryUploadBytes = 0, tableUploadBytes = 0;
};

/// One image to render. Matrices are in GfMatrix4d memory order (row-major, row vectors), which a GLSL
/// mat4 reads as the equivalent column-vector matrix, so they are uploaded as they are.
struct HdRTVkFrame
{
    uint32_t width = 0;             ///< pixels across the region to render
    uint32_t height = 0;            ///< pixels up the region to render (row 0 is the bottom)
    float invView[16] = {};         ///< camera space to world space
    float invProj[16] = {};         ///< normalised device coordinates to camera space
    bool orthographic = false;
    float dome[3] = { 0.0f, 0.0f, 0.0f };   ///< uniform dome radiance collected by paths that leave the scene
    uint32_t bounces = 16;          ///< diffuse bounces after the first hit
    uint32_t seed = 1;
};

/// How to bring the GPU up. Two HdRTVkOptions that compare equal need no re-initialisation.
struct HdRTVkOptions
{
    std::string spvPath;                ///< the compiled shaders/furnace.comp
    bool validation = false;            ///< load VK_LAYER_KHRONOS_validation if it is installed
    bool allowSoftwareDevice = false;   ///< accept a CPU rasteriser (tests on machines without a GPU only)

    bool operator==(const HdRTVkOptions& o) const
    {
        return spvPath == o.spvPath && validation == o.validation && allowSoftwareDevice == o.allowSoftwareDevice;
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
    /// options.allowSoftwareDevice says so) and the compute pipeline. No scene exists until SetScene().
    /// Returns false and fills *error on any failure. Safe to call again after Destroy().
    bool Init(const HdRTVkOptions& options, std::string* error);

    bool IsReady() const;

    /// Apply actual deltas from a complete current mesh view. No pointers are retained.
    /// All persistent scene buffers are device-local; stats count submitted work for this call.
    bool SetMeshes(const std::vector<HdRTVkMesh>& meshes, HdRTVkEditStats* stats, std::string* error);

    /// Compatibility wrapper for one identity mesh with uniform per-triangle albedo.
    /// Nonuniform per-triangle albedo must be supplied as separate SetMeshes entries.
    bool SetScene(const float* positions, const float* normals, size_t vertexCount, const uint32_t* indices,
                  const float* albedo, size_t triangleCount, std::string* error);

    bool HasScene() const;

    /// Sets the image size and parameters for the passes that follow and zeroes the accumulation.
    bool BeginFrame(const HdRTVkFrame& frame, std::string* error);

    /// Adds one sample per pixel to the accumulation and waits for the GPU to finish.
    bool RenderPass(uint32_t passIndex, std::string* error);
    double LastPassSubmitSeconds() const;

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
