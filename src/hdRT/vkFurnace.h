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
    size_t normalCount = 0; // zero preserves the legacy one-normal-per-vertex layout
    bool cornerNormals = false;
    uint64_t normalVersion = 0; // zero follows geometryVersion for legacy callers
    const int32_t* faceIds = nullptr;
    int32_t primId = -1;
};

/// World-space analytic emitter. All arrays match std430 vec4 alignment.
/// type: 0 rectangle, 1 disk, 2 sphere, 3 distant. U/V are half extents for
/// rectangles, radius vectors for disks. Sphere radius is normalRadius.w.
/// Distant normal is emission direction; basisVCos.w is cos(angular diameter/2).
struct HdRTVkLight
{
    float positionType[4] = {};
    float basisUArea[4] = {}; // w = world surface area, or distant solid angle (0 derives from cosine)
    float basisVCos[4] = {};
    float normalRadius[4] = {};
    float radiance[4] = {}; // rgb radiance, w computed selection probability
    float selection[4] = {}; // x computed CDF, yzw camera radiance (allzero defaults to radiance.rgb)
};

/// Deterministic pixel-center surface data, independent of beauty sample jitter.
struct HdRTVkGuide
{
    float normalDepth[4]; // world shading normal, projected depth
    float eyeNormalDistance[4]; // eye shading normal, ray distance
    int32_t ids[4]; // primitive, authored polygon, instance (-1), coverage
    float albedo[4];
};

struct HdRTVkEditStats
{
    uint64_t blasBuilds = 0, blasRefits = 0, tlasBuilds = 0, tlasUpdates = 0;
    uint64_t geometryUploadBytes = 0, tableUploadBytes = 0;
};

/// Per-frame actual work. Durations are host submit-to-fence wall times, not GPU timestamps.
struct HdRTVkFrameStats
{
    uint64_t traceSubmits = 0, traceDispatches = 0, tracedSamples = 0;
    uint64_t resolveSubmits = 0, resolveDispatches = 0, clearSubmits = 0;
    uint64_t readbackBytes = 0, readbackCount = 0, compatibilityReadbackSubmits = 0;
    uint64_t accumulationBytes = 0, resolveBytes = 0, readbackBytesAllocated = 0;
    uint64_t guideBytes = 0;
    double lastBatchSeconds = 0.0, maxBatchSeconds = 0.0;
    double lastResolveSeconds = 0.0, totalResolveSeconds = 0.0;
    bool accumulationDeviceLocal = false, resolveDeviceLocal = false;
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
    bool guides = false;
};

/// How to bring the GPU up. Two HdRTVkOptions that compare equal need no re-initialisation.
struct HdRTVkOptions
{
    std::string spvPath;                ///< furnace.spv; resolve.spv must be its sibling
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
    bool SetLights(const std::vector<HdRTVkLight>& lights, std::string* error);

    /// Compatibility wrapper for one identity mesh with uniform per-triangle albedo.
    /// Nonuniform per-triangle albedo must be supplied as separate SetMeshes entries.
    bool SetScene(const float* positions, const float* normals, size_t vertexCount, const uint32_t* indices,
                  const float* albedo, size_t triangleCount, std::string* error);

    bool HasScene() const;

    /// Sets the image size and parameters for the passes that follow and zeroes the accumulation.
    bool BeginFrame(const HdRTVkFrame& frame, std::string* error);

    /// Add 1..32 sequential samples, starting at the frame's next sample. Waits for one fence.
    /// No readback occurs. Empty scenes advance the logical sample count without dispatching.
    bool RenderBatch(uint32_t firstPass, uint32_t count, std::string* error);

    /// Resolve all completed samples to averaged premultiplied RGBA and copy once to host memory.
    /// completedSamples must equal the frame's nonzero logical sample count.
    bool Resolve(uint32_t completedSamples, std::string* error);
    /// No GPU work. Valid after Resolve until BeginFrame, another Resolve, or Destroy.
    const float* ResolvedPixels() const;
    const HdRTVkGuide* ResolvedGuides() const;
    HdRTVkFrameStats FrameStats() const;

    /// Compatibility: one sample, followed by a raw-sum readback. Production uses RenderBatch.
    bool RenderPass(uint32_t passIndex, std::string* error);
    double LastPassSubmitSeconds() const;

    /// width * height * 4 floats: rgb = summed radiance, a = number of samples whose camera ray hit the
    /// scene. Row 0 is the bottom. Valid after RenderPass until BeginFrame/RenderPass/Destroy.
    /// Resolve never changes this compatibility snapshot. No GPU work occurs in this accessor.
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
