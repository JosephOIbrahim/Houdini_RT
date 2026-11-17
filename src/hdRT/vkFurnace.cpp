// vkFurnace.cpp - see vkFurnace.h.
//
// The acceleration-structure call sequence is adapted from Sascha Willems' Vulkan examples
// (examples/rayquery, base/VulkanRaytracingSample), MIT License, Copyright (c) 2016 Sascha Willems,
// by way of this project's step 2 host (src/furnace/rayq_host.cpp).
// See third_party/sascha_willems_vulkan/LICENSE.md and NOTICE.md in this directory.
#include "vkFurnace.h"

#include "log.h"

#include <vulkan/vulkan.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <vector>

namespace {

const char* _ResultName(VkResult r)
{
    switch (r)
    {
    case VK_SUCCESS: return "VK_SUCCESS";
    case VK_NOT_READY: return "VK_NOT_READY";
    case VK_TIMEOUT: return "VK_TIMEOUT";
    case VK_INCOMPLETE: return "VK_INCOMPLETE";
    case VK_ERROR_OUT_OF_HOST_MEMORY: return "VK_ERROR_OUT_OF_HOST_MEMORY";
    case VK_ERROR_OUT_OF_DEVICE_MEMORY: return "VK_ERROR_OUT_OF_DEVICE_MEMORY";
    case VK_ERROR_INITIALIZATION_FAILED: return "VK_ERROR_INITIALIZATION_FAILED";
    case VK_ERROR_DEVICE_LOST: return "VK_ERROR_DEVICE_LOST";
    case VK_ERROR_MEMORY_MAP_FAILED: return "VK_ERROR_MEMORY_MAP_FAILED";
    case VK_ERROR_LAYER_NOT_PRESENT: return "VK_ERROR_LAYER_NOT_PRESENT";
    case VK_ERROR_EXTENSION_NOT_PRESENT: return "VK_ERROR_EXTENSION_NOT_PRESENT";
    case VK_ERROR_FEATURE_NOT_PRESENT: return "VK_ERROR_FEATURE_NOT_PRESENT";
    case VK_ERROR_INCOMPATIBLE_DRIVER: return "VK_ERROR_INCOMPATIBLE_DRIVER";
    case VK_ERROR_TOO_MANY_OBJECTS: return "VK_ERROR_TOO_MANY_OBJECTS";
    case VK_ERROR_FORMAT_NOT_SUPPORTED: return "VK_ERROR_FORMAT_NOT_SUPPORTED";
    case VK_ERROR_UNKNOWN: return "VK_ERROR_UNKNOWN";
    default: return "VkResult";
    }
}

bool _Fail(std::string* error, const std::string& what)
{
    if (error)
        *error = what;
    return false;
}

bool _FailVk(std::string* error, VkResult r, const char* call)
{
    char text[512];
    std::snprintf(text, sizeof(text), "Vulkan call failed (%s, %d): %s", _ResultName(r), int(r), call);
    return _Fail(error, text);
}

// Runs a Vulkan call that returns VkResult; on failure fills *error and returns false from the caller.
#define HDRT_VK(call)                                       \
    do                                                      \
    {                                                       \
        const VkResult hdrt_vk_result = (call);             \
        if (hdrt_vk_result != VK_SUCCESS)                   \
            return _FailVk(error, hdrt_vk_result, #call);   \
    } while (0)

struct Buffer
{
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void* mapped = nullptr;
    VkDeviceSize size = 0;
    VkDeviceSize allocationSize = 0;
    VkMemoryPropertyFlags memoryProperties = 0;
};

// The KHR acceleration-structure entry points are not exported by the loader library; fetch them.
struct AsApi
{
    PFN_vkCreateAccelerationStructureKHR create = nullptr;
    PFN_vkDestroyAccelerationStructureKHR destroy = nullptr;
    PFN_vkGetAccelerationStructureBuildSizesKHR build_sizes = nullptr;
    PFN_vkCmdBuildAccelerationStructuresKHR cmd_build = nullptr;
    PFN_vkGetAccelerationStructureDeviceAddressKHR address = nullptr;
};

struct Accel
{
    VkAccelerationStructureKHR handle = VK_NULL_HANDLE;
    Buffer storage;
    VkDeviceAddress address = 0;
};

// Four uint64 addresses/padding, then one vec4: exactly the shader's std430 table row.
struct MeshRow
{
    uint64_t positions = 0, normals = 0, indices = 0, padding = 0;
    float albedo[4] = {};
};
static_assert(sizeof(MeshRow) == 48, "MeshRow must match the shader");

struct MeshGpu
{
    Buffer positions, normals, indices;
    Accel blas;
    uint64_t geometryVersion = 0, topologyVersion = 0;
    size_t vertexCount = 0, triangleCount = 0;
    uint32_t slot = 0;
    float transform[12] = {};
    bool visible = false;
    bool initialized = false;
};

bool _RenderableTransform(const float (&m)[12])
{
    for (float v : m)
        if (!std::isfinite(v))
            return false;
    // Vulkan requires an invertible linear transform, even for a masked-out instance
    // (VUID-VkTransformMatrixKHR-matrix-03799). Use double so tiny valid scales do not
    // underflow the determinant. A zero-scale mesh keeps its BLAS for later recovery.
    const double determinant = double(m[0]) * (double(m[5]) * m[10] - double(m[6]) * m[9])
        - double(m[1]) * (double(m[4]) * m[10] - double(m[6]) * m[8])
        + double(m[2]) * (double(m[4]) * m[9] - double(m[5]) * m[8]);
    return determinant != 0.0;
}

// Mirrors `buffer Frame` in shaders/furnace.comp (std430: two mat4, then 16-byte vectors).
struct FrameGpu
{
    float invView[16];
    float invProj[16];
    float dome[4];          // rgb dome radiance, a unused
    uint32_t size[4];       // width, height, bounces, seed
    uint32_t pass[4];       // pass index, orthographic flag, unused, unused
};
static_assert(sizeof(FrameGpu) == 176, "FrameGpu must match the std430 layout in furnace.comp");

bool _HasExtension(const std::vector<VkExtensionProperties>& list, const char* name)
{
    for (const VkExtensionProperties& e : list)
        if (!std::strcmp(e.extensionName, name))
            return true;
    return false;
}

} // namespace

// ------------------------------------------------------------------------------------------------

struct HdRTVkFurnace::Impl
{
    VkInstance instance = VK_NULL_HANDLE;
    VkDebugUtilsMessengerEXT messenger = VK_NULL_HANDLE;
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    VkFence fence = VK_NULL_HANDLE;
    bool submissionPending = false;
    AsApi as;
    VkDeviceSize scratchAlignment = 1;
    uint64_t maxPrimitiveCount = 0;        // VkPhysicalDeviceAccelerationStructurePropertiesKHR
    uint64_t maxInstanceCount = 0;
    uint32_t maxStorageBufferRange = 0;    // VkPhysicalDeviceLimits
    uint32_t maxDispatchX = 0, maxDispatchY = 0;

    std::map<std::string, std::unique_ptr<MeshGpu>> meshes;
    std::vector<MeshGpu*> slots;
    std::vector<MeshRow> rows;
    Buffer meshTable, instanceBuffer, stagingBuffer, scratchBuffer;
    Accel tlas;
    uint64_t legacyVersion = 0;
    bool hasScene = false;

    VkDescriptorSetLayout setLayout = VK_NULL_HANDLE;
    VkPipelineLayout pipelineLayout = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkDescriptorPool descriptorPool = VK_NULL_HANDLE;
    VkDescriptorSet descriptorSet = VK_NULL_HANDLE;
    VkDescriptorSetLayout resolveSetLayout = VK_NULL_HANDLE;
    VkPipelineLayout resolvePipelineLayout = VK_NULL_HANDLE;
    VkShaderModule resolveShader = VK_NULL_HANDLE;
    VkPipeline resolvePipeline = VK_NULL_HANDLE;
    VkDescriptorPool resolveDescriptorPool = VK_NULL_HANDLE;
    VkDescriptorSet resolveDescriptorSet = VK_NULL_HANDLE;

    Buffer frameBuffer;   // one FrameGpu, host visible
    Buffer accumBuffer, resolvedBuffer;   // device-local float4 images
    Buffer readbackBuffer, legacyReadbackBuffer;   // host-visible, cached when supported
    uint32_t width = 0;
    uint32_t height = 0;
    uint32_t completedSamples = 0;
    bool frameValid = false, resolvedValid = false, legacyValid = false;
    HdRTVkFrameStats frameStats;

    std::string deviceName;
    bool validationActive = false;
    bool ready = false;
    double lastPassSubmitSeconds = 0.0;
    double lastSubmitWallSeconds = 0.0;
    std::atomic<int> validationErrors{ 0 };
    std::atomic<int> validationWarnings{ 0 };

    static VKAPI_ATTR VkBool32 VKAPI_CALL OnDebug(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                                 const VkDebugUtilsMessengerCallbackDataEXT* data, void* user);

    uint32_t FindMemoryType(uint32_t typeBits, VkMemoryPropertyFlags wanted) const;
    // gpuReads: the kernel reads it on every hit; prefer device-local memory the host can write when its
    // heap is large (resizable BAR), else host memory.
    bool CreateBuffer(VkDeviceSize size, VkBufferUsageFlags usage, bool hostVisible, Buffer* out, std::string* error, bool cpuReads = false,
                      bool gpuReads = false);
    void DestroyBuffer(Buffer& b);
    bool EnsureBuffer(VkDeviceSize size, VkBufferUsageFlags usage, bool hostVisible,
                      Buffer* out, std::string* error, bool cpuReads = false);
    bool Upload(Buffer& out, const void* data, VkDeviceSize bytes, VkDeviceSize offset, std::string* error);
    VkDeviceAddress BufferAddress(VkBuffer buffer) const;
    bool Submit(std::string* error, bool renderPass = false);
    bool BeginCommands(std::string* error);
    bool ResolveToHost(uint32_t samples, bool legacy, std::string* error);
    void RefreshFrameAllocations();
    bool BuildAccel(VkAccelerationStructureTypeKHR type, const VkAccelerationStructureGeometryKHR& geometry, uint32_t primitiveCount,
                    Accel* out, std::string* error);
    void DestroyAccel(Accel& a);

    bool CreateInstance(bool wantValidation, std::string* error);
    bool CreateDevice(bool allowSoftwareDevice, std::string* error);
    bool SetMeshes(const std::vector<HdRTVkMesh>& input, HdRTVkEditStats& stats, std::string* error);
    bool UpdateMesh(MeshGpu& mesh, const HdRTVkMesh& view, HdRTVkEditStats& stats,
                    bool* tlasDirty, std::string* error);
    bool BuildTlas(HdRTVkEditStats& stats, std::string* error);
    void DestroyMesh(MeshGpu& mesh);
    void DestroyScene();
    bool CreatePipeline(const std::vector<uint32_t>& spirv, std::string* error);
    bool CreateResolvePipeline(const std::vector<uint32_t>& spirv, std::string* error);
    void Destroy();
};

// Everything the validation layer or the loader says at warning level or above is printed in full, with
// its message id. "Validation Error" is what verify/s3_delegate.sh greps for, so an error can never pass
// silently; warnings are printed in lower case as in step 2. A warning that carries a VUID in its id still
// puts the letters VUID in the log, which the exam also refuses.
VKAPI_ATTR VkBool32 VKAPI_CALL HdRTVkFurnace::Impl::OnDebug(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                                            const VkDebugUtilsMessengerCallbackDataEXT* data, void* user)
{
    Impl* self = static_cast<Impl*>(user);
    const char* message = (data && data->pMessage) ? data->pMessage : "(no message)";
    const char* id = (data && data->pMessageIdName) ? data->pMessageIdName : "no id";
    if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT)
    {
        if (self)
            ++self->validationErrors;
        HdRTLog("Validation Error: [ %s ] %s", id, message);
    }
    else if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT)
    {
        if (self)
            ++self->validationWarnings;
        HdRTLog("validation warning: [ %s ] %s", id, message);
    }
    return VK_FALSE;
}

uint32_t HdRTVkFurnace::Impl::FindMemoryType(uint32_t typeBits, VkMemoryPropertyFlags wanted) const
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i)
        if ((typeBits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & wanted) == wanted)
            return i;
    return UINT32_MAX;
}

// cpuReads: the CPU reads this buffer back, so ask for cached host memory. Plain host-visible memory on a
// discrete GPU is usually write-combined, and reading that is tens of times slower than normal memory.
bool HdRTVkFurnace::Impl::CreateBuffer(VkDeviceSize size, VkBufferUsageFlags usage, bool hostVisible, Buffer* out, std::string* error, bool cpuReads,
                                       bool gpuReads)
{
    Buffer b;
    b.size = size;
    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = size;
    bi.usage = usage;
    bi.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    HDRT_VK(vkCreateBuffer(device, &bi, nullptr, &b.buffer));
    *out = b;   // from here on the caller's Buffer owns the handle, so Destroy() frees it on any later failure

    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, b.buffer, &req);
    VkMemoryAllocateFlagsInfo flags = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO };
    flags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    const VkMemoryPropertyFlags hostFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    if (usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT)
        ai.pNext = &flags;

    // gpuReads: try device-local memory the host can write, but only on a heap large enough that this
    // buffer is a small share of it. Without resizable BAR that heap is about 256 MB and shared with the
    // driver and Houdini's viewport, so it is skipped; and if the allocation fails anyway, fall back.
    if (hostVisible && gpuReads)
    {
        const uint32_t t = FindMemoryType(req.memoryTypeBits, hostFlags | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
        if (t != UINT32_MAX)
        {
            VkPhysicalDeviceMemoryProperties mp;
            vkGetPhysicalDeviceMemoryProperties(phys, &mp);
            const VkDeviceSize heap = mp.memoryHeaps[mp.memoryTypes[t].heapIndex].size;
            if (heap > (VkDeviceSize(1) << 30) && req.size * 8 <= heap)
            {
                ai.memoryTypeIndex = t;
                if (vkAllocateMemory(device, &ai, nullptr, &out->memory) != VK_SUCCESS)
                    out->memory = VK_NULL_HANDLE;   // full or refused: host memory below
            }
        }
    }
    if (!out->memory)
    {
        ai.memoryTypeIndex = UINT32_MAX;
        if (hostVisible && cpuReads)
            ai.memoryTypeIndex = FindMemoryType(req.memoryTypeBits, hostFlags | VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
        if (ai.memoryTypeIndex == UINT32_MAX)
            ai.memoryTypeIndex = FindMemoryType(req.memoryTypeBits, hostVisible ? hostFlags : VkMemoryPropertyFlags(VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT));
        if (ai.memoryTypeIndex == UINT32_MAX)
            return _Fail(error, "no suitable memory type for a buffer");
        HDRT_VK(vkAllocateMemory(device, &ai, nullptr, &out->memory));
    }
    HDRT_VK(vkBindBufferMemory(device, out->buffer, out->memory, 0));
    VkPhysicalDeviceMemoryProperties selectedMemory;
    vkGetPhysicalDeviceMemoryProperties(phys, &selectedMemory);
    out->allocationSize = req.size;
    out->memoryProperties = selectedMemory.memoryTypes[ai.memoryTypeIndex].propertyFlags;
    if (hostVisible)
        HDRT_VK(vkMapMemory(device, out->memory, 0, VK_WHOLE_SIZE, 0, &out->mapped));
    return true;
}

void HdRTVkFurnace::Impl::DestroyBuffer(Buffer& b)
{
    if (!device)
        return;
    if (b.mapped)
        vkUnmapMemory(device, b.memory);
    if (b.buffer)
        vkDestroyBuffer(device, b.buffer, nullptr);
    if (b.memory)
        vkFreeMemory(device, b.memory, nullptr);
    b = Buffer();
}

VkDeviceAddress HdRTVkFurnace::Impl::BufferAddress(VkBuffer buffer) const
{
    VkBufferDeviceAddressInfo info = { VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO };
    info.buffer = buffer;
    return vkGetBufferDeviceAddress(device, &info);
}

bool HdRTVkFurnace::Impl::EnsureBuffer(VkDeviceSize size, VkBufferUsageFlags usage, bool hostVisible,
                                       Buffer* out, std::string* error, bool cpuReads)
{
    if (out->buffer && out->size >= size)
        return true;
    Buffer next;
    const VkDeviceSize capacity = std::max(size, out->size);
    if (!CreateBuffer(capacity, usage, hostVisible, &next, error, cpuReads))
    {
        DestroyBuffer(next);
        return false;
    }
    DestroyBuffer(*out);
    *out = next;
    return true;
}

bool HdRTVkFurnace::Impl::Upload(Buffer& out, const void* data, VkDeviceSize bytes,
                                 VkDeviceSize offset, std::string* error)
{
    if (!bytes)
        return true;
    if (!data || offset > out.size || bytes > out.size - offset)
        return _Fail(error, "invalid scene upload range");
    if (!EnsureBuffer(bytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, true, &stagingBuffer, error))
        return false;
    std::memcpy(stagingBuffer.mapped, data, size_t(bytes));
    HDRT_VK(vkResetCommandBuffer(cmd, 0));
    VkCommandBufferBeginInfo begin = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    HDRT_VK(vkBeginCommandBuffer(cmd, &begin));
    VkBufferCopy copy = {0, offset, bytes};
    vkCmdCopyBuffer(cmd, stagingBuffer.buffer, out.buffer, 1, &copy);
    VkBufferMemoryBarrier barrier = { VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER };
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.buffer = out.buffer;
    barrier.offset = offset;
    barrier.size = bytes;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT,
                        VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
                        0, 0, nullptr, 1, &barrier, 0, nullptr);
    return Submit(error);
}

bool HdRTVkFurnace::Impl::BeginCommands(std::string* error)
{
    if (submissionPending)
        return _Fail(error, "cannot reuse a command buffer while its submission is pending");
    HDRT_VK(vkResetCommandBuffer(cmd, 0));
    VkCommandBufferBeginInfo begin = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    begin.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    HDRT_VK(vkBeginCommandBuffer(cmd, &begin));
    return true;
}

// One in-flight submission, owned by the render thread. Cancellation is at batch boundaries:
// Vulkan cannot cancel submitted work, so never reset a command buffer or release memory early.
bool HdRTVkFurnace::Impl::Submit(std::string* error, bool renderPass)
{
    HDRT_VK(vkEndCommandBuffer(cmd));
    HDRT_VK(vkResetFences(device, 1, &fence));
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    const auto start = std::chrono::steady_clock::now();
    if (renderPass)
        lastPassSubmitSeconds = std::chrono::duration<double>(start.time_since_epoch()).count();
    const VkResult submitted = vkQueueSubmit(queue, 1, &si, fence);
    if (submitted != VK_SUCCESS)
    {
        ready = frameValid = false;
        return _FailVk(error, submitted, "vkQueueSubmit");
    }
    submissionPending = true;
    const VkResult waited = vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX);
    lastSubmitWallSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - start).count();
    if (waited != VK_SUCCESS)
    {
        ready = frameValid = false;
        return _FailVk(error, waited, "vkWaitForFences");
    }
    submissionPending = false;
    return true;
}

// Builds one acceleration structure from one geometry and waits for it. `type` selects BLAS or TLAS.
bool HdRTVkFurnace::Impl::BuildAccel(VkAccelerationStructureTypeKHR type, const VkAccelerationStructureGeometryKHR& geometry,
                                     uint32_t primitiveCount, Accel* out, std::string* error)
{
    VkAccelerationStructureBuildGeometryInfoKHR build = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR };
    build.type = type;
    build.flags = VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_TRACE_BIT_KHR;
    build.mode = VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR;
    build.geometryCount = 1;
    build.pGeometries = &geometry;

    VkAccelerationStructureBuildSizesInfoKHR sizes = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR };
    as.build_sizes(device, VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &build, &primitiveCount, &sizes);

    if (!out->handle || out->storage.size < sizes.accelerationStructureSize)
    {
        DestroyAccel(*out);
        if (!CreateBuffer(sizes.accelerationStructureSize,
                          VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
                          false, &out->storage, error))
            return false;
        VkAccelerationStructureCreateInfoKHR ci = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR };
        ci.buffer = out->storage.buffer;
        ci.size = sizes.accelerationStructureSize;
        ci.type = type;
        HDRT_VK(as.create(device, &ci, nullptr, &out->handle));
    }

    // Scratch memory must start at a multiple of the device's scratch alignment. Over-allocate
    // and round the address up so the requirement holds whatever the allocator returns.
    const VkDeviceSize align = scratchAlignment ? scratchAlignment : 1;
    if (!EnsureBuffer(sizes.buildScratchSize + align,
                      VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT,
                      false, &scratchBuffer, error))
        return false;
    const VkDeviceAddress scratchBase = BufferAddress(scratchBuffer.buffer);
    const VkDeviceAddress scratchAligned = (scratchBase + align - 1) / align * align;

    build.dstAccelerationStructure = out->handle;
    build.scratchData.deviceAddress = scratchAligned;

    VkAccelerationStructureBuildRangeInfoKHR range = {};
    range.primitiveCount = primitiveCount;
    const VkAccelerationStructureBuildRangeInfoKHR* ranges = &range;

    bool ok = true;
    HDRT_VK(vkResetCommandBuffer(cmd, 0));
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    const VkResult begun = vkBeginCommandBuffer(cmd, &bi);
    if (begun != VK_SUCCESS)
        ok = _FailVk(error, begun, "vkBeginCommandBuffer(cmd, &bi)");
    if (ok)
    {
        VkMemoryBarrier before = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
        before.srcAccessMask = VK_ACCESS_MEMORY_WRITE_BIT;
        before.dstAccessMask = VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR | VK_ACCESS_ACCELERATION_STRUCTURE_WRITE_BIT_KHR;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ALL_COMMANDS_BIT,
                            VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR, 0, 1, &before, 0, nullptr, 0, nullptr);
        as.cmd_build(cmd, 1, &build, &ranges);
        VkMemoryBarrier after = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
        after.srcAccessMask = VK_ACCESS_ACCELERATION_STRUCTURE_WRITE_BIT_KHR;
        after.dstAccessMask = VK_ACCESS_ACCELERATION_STRUCTURE_READ_BIT_KHR;
        vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR,
                            VK_PIPELINE_STAGE_ACCELERATION_STRUCTURE_BUILD_BIT_KHR | VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                            0, 1, &after, 0, nullptr, 0, nullptr);
        ok = Submit(error);
    }
    if (!ok)
        return false;

    VkAccelerationStructureDeviceAddressInfoKHR ai = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR };
    ai.accelerationStructure = out->handle;
    out->address = as.address(device, &ai);
    return true;
}

void HdRTVkFurnace::Impl::DestroyAccel(Accel& a)
{
    if (device && a.handle && as.destroy)
        as.destroy(device, a.handle, nullptr);
    DestroyBuffer(a.storage);
    a = Accel();
}

// ---- instance: the validation layer is on only when asked for AND installed ------------------------

bool HdRTVkFurnace::Impl::CreateInstance(bool wantValidation, std::string* error)
{
    uint32_t layerCount = 0;
    vkEnumerateInstanceLayerProperties(&layerCount, nullptr);
    std::vector<VkLayerProperties> layers(layerCount);
    if (layerCount)
        vkEnumerateInstanceLayerProperties(&layerCount, layers.data());
    bool haveValidation = false;
    for (const VkLayerProperties& l : layers)
        if (!std::strcmp(l.layerName, "VK_LAYER_KHRONOS_validation"))
            haveValidation = true;
    const bool useValidation = wantValidation && haveValidation;
    if (wantValidation && !haveValidation)
        HdRTLog("validation layer requested but VK_LAYER_KHRONOS_validation is not installed");

    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.pApplicationName = "HoudiniRT";
    app.apiVersion = VK_API_VERSION_1_2;

    const char* validationLayer = "VK_LAYER_KHRONOS_validation";
    const char* debugExtension = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
    VkDebugUtilsMessengerCreateInfoEXT dbg = { VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT };
    dbg.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
    dbg.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT;
    dbg.pfnUserCallback = &Impl::OnDebug;
    dbg.pUserData = this;

    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ici.pApplicationInfo = &app;
    if (useValidation)
    {
        ici.enabledLayerCount = 1;
        ici.ppEnabledLayerNames = &validationLayer;
        ici.enabledExtensionCount = 1;
        ici.ppEnabledExtensionNames = &debugExtension;
        ici.pNext = &dbg;   // also covers instance creation and destruction themselves
    }
    HDRT_VK(vkCreateInstance(&ici, nullptr, &instance));

    if (useValidation)
    {
        const auto createMessenger =
            reinterpret_cast<PFN_vkCreateDebugUtilsMessengerEXT>(vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT"));
        if (!createMessenger)
            return _Fail(error, "the validation layer loaded but vkCreateDebugUtilsMessengerEXT is missing");
        HDRT_VK(createMessenger(instance, &dbg, nullptr, &messenger));
        validationActive = true;
    }
    return true;
}

// ---- device: needs ray queries; prefer a discrete GPU; never hardcode an index, never use a CPU device ----

bool HdRTVkFurnace::Impl::CreateDevice(bool allowSoftwareDevice, std::string* error)
{
    const char* requiredExtensions[] = { VK_KHR_ACCELERATION_STRUCTURE_EXTENSION_NAME, VK_KHR_RAY_QUERY_EXTENSION_NAME,
                                         VK_KHR_DEFERRED_HOST_OPERATIONS_EXTENSION_NAME };
    uint32_t physCount = 0;
    vkEnumeratePhysicalDevices(instance, &physCount, nullptr);
    std::vector<VkPhysicalDevice> physicals(physCount);
    if (physCount)
        vkEnumeratePhysicalDevices(instance, &physCount, physicals.data());
    if (physicals.empty())
        return _Fail(error, "no Vulkan device found");

    VkPhysicalDeviceProperties chosenProps = {};
    bool chosenHasRayQuery = false;
    int bestRank = -1;
    for (VkPhysicalDevice candidate : physicals)
    {
        VkPhysicalDeviceProperties props;
        vkGetPhysicalDeviceProperties(candidate, &props);
        if (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU && !allowSoftwareDevice)
            continue;   // a software rasteriser is not an acceptable answer unless a test asked for one
        uint32_t extCount = 0;
        vkEnumerateDeviceExtensionProperties(candidate, nullptr, &extCount, nullptr);
        std::vector<VkExtensionProperties> exts(extCount);
        if (extCount)
            vkEnumerateDeviceExtensionProperties(candidate, nullptr, &extCount, exts.data());
        bool all = props.apiVersion >= VK_API_VERSION_1_2;
        for (const char* name : requiredExtensions)
            all = all && _HasExtension(exts, name);

        bool rayQuery = false;
        if (all)
        {
            VkPhysicalDeviceRayQueryFeaturesKHR rq = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR };
            VkPhysicalDeviceAccelerationStructureFeaturesKHR asf = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR };
            VkPhysicalDeviceVulkan12Features v12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
            asf.pNext = &rq;
            v12.pNext = &asf;
            VkPhysicalDeviceFeatures2 f2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2 };
            f2.pNext = &v12;
            vkGetPhysicalDeviceFeatures2(candidate, &f2);
            rayQuery = rq.rayQuery && asf.accelerationStructure && v12.bufferDeviceAddress && f2.features.shaderInt64;
        }
        const int rank = (rayQuery ? 2 : 0) + (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 1 : 0);
        if (rank > bestRank)
        {
            bestRank = rank;
            phys = candidate;
            chosenProps = props;
            chosenHasRayQuery = rayQuery;
        }
    }
    if (!phys)
        return _Fail(error, "the only Vulkan devices on this machine are software (CPU) devices, and those are refused");
    deviceName = chosenProps.deviceName;
    if (!chosenHasRayQuery)
        return _Fail(error, "the selected device (" + deviceName + ") does not support acceleration structures and ray queries");

    VkPhysicalDeviceAccelerationStructurePropertiesKHR asProps = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_PROPERTIES_KHR };
    VkPhysicalDeviceProperties2 props2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2 };
    props2.pNext = &asProps;
    vkGetPhysicalDeviceProperties2(phys, &props2);
    scratchAlignment = asProps.minAccelerationStructureScratchOffsetAlignment;
    maxPrimitiveCount = asProps.maxPrimitiveCount;
    maxInstanceCount = asProps.maxInstanceCount;
    maxStorageBufferRange = props2.properties.limits.maxStorageBufferRange;
    maxDispatchX = props2.properties.limits.maxComputeWorkGroupCount[0];
    maxDispatchY = props2.properties.limits.maxComputeWorkGroupCount[1];

    uint32_t familyCount = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &familyCount, nullptr);
    std::vector<VkQueueFamilyProperties> families(familyCount);
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &familyCount, families.data());
    uint32_t family = UINT32_MAX;
    for (uint32_t i = 0; i < familyCount; ++i)
        if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT)
        {
            family = i;
            break;
        }
    if (family == UINT32_MAX)
        return _Fail(error, "no compute queue family");

    VkPhysicalDeviceRayQueryFeaturesKHR enableRq = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR };
    enableRq.rayQuery = VK_TRUE;
    VkPhysicalDeviceAccelerationStructureFeaturesKHR enableAs = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR };
    enableAs.accelerationStructure = VK_TRUE;
    enableAs.pNext = &enableRq;
    VkPhysicalDeviceVulkan12Features enable12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    enable12.bufferDeviceAddress = VK_TRUE;
    enable12.pNext = &enableAs;

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qi = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.queueFamilyIndex = family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qi;
    dci.enabledExtensionCount = uint32_t(sizeof(requiredExtensions) / sizeof(requiredExtensions[0]));
    dci.ppEnabledExtensionNames = requiredExtensions;
    dci.pNext = &enable12;
    VkPhysicalDeviceFeatures coreFeatures = {};
    coreFeatures.shaderInt64 = VK_TRUE;
    dci.pEnabledFeatures = &coreFeatures;
    HDRT_VK(vkCreateDevice(phys, &dci, nullptr, &device));
    vkGetDeviceQueue(device, family, 0, &queue);

    as.create = reinterpret_cast<PFN_vkCreateAccelerationStructureKHR>(vkGetDeviceProcAddr(device, "vkCreateAccelerationStructureKHR"));
    as.destroy = reinterpret_cast<PFN_vkDestroyAccelerationStructureKHR>(vkGetDeviceProcAddr(device, "vkDestroyAccelerationStructureKHR"));
    as.build_sizes =
        reinterpret_cast<PFN_vkGetAccelerationStructureBuildSizesKHR>(vkGetDeviceProcAddr(device, "vkGetAccelerationStructureBuildSizesKHR"));
    as.cmd_build = reinterpret_cast<PFN_vkCmdBuildAccelerationStructuresKHR>(vkGetDeviceProcAddr(device, "vkCmdBuildAccelerationStructuresKHR"));
    as.address = reinterpret_cast<PFN_vkGetAccelerationStructureDeviceAddressKHR>(
        vkGetDeviceProcAddr(device, "vkGetAccelerationStructureDeviceAddressKHR"));
    if (!as.create || !as.destroy || !as.build_sizes || !as.cmd_build || !as.address)
        return _Fail(error, "acceleration-structure entry points are missing from the device");

    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = family;
    cpci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    HDRT_VK(vkCreateCommandPool(device, &cpci, nullptr, &pool));

    VkCommandBufferAllocateInfo cbai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cbai.commandPool = pool;
    cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    HDRT_VK(vkAllocateCommandBuffers(device, &cbai, &cmd));
    VkFenceCreateInfo fenceInfo = { VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    HDRT_VK(vkCreateFence(device, &fenceInfo, nullptr, &fence));
    return true;
}

// ---- persistent per-mesh scene ---------------------------------------------------------------

void HdRTVkFurnace::Impl::DestroyMesh(MeshGpu& mesh)
{
    DestroyAccel(mesh.blas);
    DestroyBuffer(mesh.positions);
    DestroyBuffer(mesh.normals);
    DestroyBuffer(mesh.indices);
}

void HdRTVkFurnace::Impl::DestroyScene()
{
    hasScene = false;
    if (device && queue)
        vkQueueWaitIdle(queue);
    DestroyAccel(tlas);
    for (auto& entry : meshes)
        DestroyMesh(*entry.second);
    meshes.clear();
    slots.clear();
    rows.clear();
    DestroyBuffer(meshTable);
    DestroyBuffer(instanceBuffer);
    DestroyBuffer(stagingBuffer);
    DestroyBuffer(scratchBuffer);
}

bool HdRTVkFurnace::Impl::UpdateMesh(MeshGpu& mesh, const HdRTVkMesh& view,
                                     HdRTVkEditStats& stats, bool* tlasDirty, std::string* error)
{
    const bool topology = !mesh.initialized || mesh.topologyVersion != view.topologyVersion
                          || mesh.triangleCount != view.triangleCount;
    const bool geometry = topology || mesh.geometryVersion != view.geometryVersion
                          || mesh.vertexCount != view.vertexCount;
    if (!mesh.initialized || mesh.visible != view.visible
        || std::memcmp(mesh.transform, view.transform, sizeof(mesh.transform)))
        *tlasDirty = true;
    if (geometry)
    {
        *tlasDirty = true;
        if (!view.vertexCount || !view.triangleCount)
            DestroyMesh(mesh);
        else
        {
            const VkBufferUsageFlags input = VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR
                | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
            const VkBufferUsageFlags storage = input | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
            const VkDeviceSize vbBytes = VkDeviceSize(view.vertexCount) * 3 * sizeof(float);
            const VkDeviceSize ibBytes = VkDeviceSize(view.triangleCount) * 3 * sizeof(uint32_t);
            if (!EnsureBuffer(vbBytes, storage, false, &mesh.positions, error)
                || !EnsureBuffer(vbBytes, storage, false, &mesh.normals, error))
                return false;
            if (!Upload(mesh.positions, view.positions, vbBytes, 0, error))
                return false;
            stats.geometryUploadBytes += vbBytes;
            if (!Upload(mesh.normals, view.normals, vbBytes, 0, error))
                return false;
            stats.geometryUploadBytes += vbBytes;
            if (topology || !mesh.indices.buffer)
            {
                if (!EnsureBuffer(ibBytes, storage, false, &mesh.indices, error)
                    || !Upload(mesh.indices, view.indices, ibBytes, 0, error))
                    return false;
                stats.geometryUploadBytes += ibBytes;
            }
            VkAccelerationStructureGeometryKHR geom = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
            geom.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
            geom.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
            auto& triangles = geom.geometry.triangles;
            triangles.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR;
            triangles.vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
            triangles.vertexData.deviceAddress = BufferAddress(mesh.positions.buffer);
            triangles.vertexStride = 3 * sizeof(float);
            triangles.maxVertex = uint32_t(view.vertexCount - 1);
            triangles.indexType = VK_INDEX_TYPE_UINT32;
            triangles.indexData.deviceAddress = BufferAddress(mesh.indices.buffer);
            if (!BuildAccel(VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR, geom,
                            uint32_t(view.triangleCount), &mesh.blas, error))
                return false;
            ++stats.blasBuilds;
        }
    }
    MeshRow& row = rows[mesh.slot];
    row.positions = mesh.positions.buffer ? BufferAddress(mesh.positions.buffer) : 0;
    row.normals = mesh.normals.buffer ? BufferAddress(mesh.normals.buffer) : 0;
    row.indices = mesh.indices.buffer ? BufferAddress(mesh.indices.buffer) : 0;
    std::memcpy(row.albedo, view.albedo, sizeof(row.albedo));
    std::memcpy(mesh.transform, view.transform, sizeof(mesh.transform));
    mesh.visible = view.visible;
    mesh.geometryVersion = view.geometryVersion;
    mesh.topologyVersion = view.topologyVersion;
    mesh.vertexCount = view.vertexCount;
    mesh.triangleCount = view.triangleCount;
    mesh.initialized = true;
    return true;
}

bool HdRTVkFurnace::Impl::BuildTlas(HdRTVkEditStats& stats, std::string* error)
{
    std::vector<VkAccelerationStructureInstanceKHR> instances;
    hasScene = false;
    for (MeshGpu* mesh : slots)
    {
        if (!mesh || !mesh->blas.handle || !_RenderableTransform(mesh->transform))
            continue;
        VkAccelerationStructureInstanceKHR item = {};
        std::memcpy(item.transform.matrix, mesh->transform, sizeof(mesh->transform));
        item.instanceCustomIndex = mesh->slot;
        item.mask = mesh->visible ? 0xFF : 0;
        item.flags = VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
        item.accelerationStructureReference = mesh->blas.address;
        instances.push_back(item);
        hasScene = hasScene || mesh->visible;
    }
    if (instances.empty())
    {
        DestroyAccel(tlas);
        return true;
    }
    if (maxInstanceCount && instances.size() > maxInstanceCount)
        return _Fail(error, "too many mesh instances for the selected Vulkan device");
    const VkDeviceSize bytes = instances.size() * sizeof(instances[0]);
    const VkBufferUsageFlags usage = VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR
        | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if (!EnsureBuffer(bytes, usage, false, &instanceBuffer, error)
        || !Upload(instanceBuffer, instances.data(), bytes, 0, error))
        return false;
    VkAccelerationStructureGeometryKHR geom = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
    geom.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
    geom.geometry.instances.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR;
    geom.geometry.instances.data.deviceAddress = BufferAddress(instanceBuffer.buffer);
    if (!BuildAccel(VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR, geom,
                    uint32_t(instances.size()), &tlas, error))
        return false;
    ++stats.tlasBuilds;
    VkWriteDescriptorSetAccelerationStructureKHR asWrite = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR };
    asWrite.accelerationStructureCount = 1;
    asWrite.pAccelerationStructures = &tlas.handle;
    VkWriteDescriptorSet write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
    write.dstSet = descriptorSet;
    write.dstBinding = 0;
    write.descriptorCount = 1;
    write.descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    write.pNext = &asWrite;
    vkUpdateDescriptorSets(device, 1, &write, 0, nullptr);
    return true;
}

bool HdRTVkFurnace::Impl::SetMeshes(const std::vector<HdRTVkMesh>& input,
                                    HdRTVkEditStats& stats, std::string* error)
{
    std::set<std::string> current;
    for (const HdRTVkMesh& view : input)
    {
        if (view.id.empty() || !current.insert(view.id).second)
            return _Fail(error, "mesh ids must be nonempty and unique");
        if (view.vertexCount > size_t(UINT32_MAX) / 3 || view.triangleCount > size_t(UINT32_MAX) / 3
            || (maxPrimitiveCount && view.triangleCount > maxPrimitiveCount))
            return _Fail(error, "mesh exceeds the Vulkan or shader indexing limits");
        if (view.vertexCount && view.triangleCount && (!view.positions || !view.normals || !view.indices))
            return _Fail(error, "nonempty mesh has a missing geometry array");
    }
    HDRT_VK(vkQueueWaitIdle(queue));
    bool tlasDirty = false;
    std::vector<uint32_t> changedRows;
    for (auto it = meshes.begin(); it != meshes.end(); )
    {
        if (current.count(it->first))
        {
            ++it;
            continue;
        }
        const uint32_t slot = it->second->slot;
        DestroyMesh(*it->second);
        slots[slot] = nullptr;
        rows[slot] = MeshRow();
        changedRows.push_back(slot);
        it = meshes.erase(it);
        tlasDirty = true;
    }
    for (const HdRTVkMesh& view : input)
    {
        auto found = meshes.find(view.id);
        if (found == meshes.end())
        {
            auto mesh = std::make_unique<MeshGpu>();
            auto free = std::find(slots.begin(), slots.end(), nullptr);
            const size_t slot = size_t(free - slots.begin());
            if (slot >= (1u << 24))
                return _Fail(error, "mesh table exceeds Vulkan instanceCustomIndex range");
            mesh->slot = uint32_t(slot);
            if (free == slots.end())
            {
                slots.push_back(mesh.get());
                rows.emplace_back();
            }
            else
                *free = mesh.get();
            found = meshes.emplace(view.id, std::move(mesh)).first;
        }
        MeshGpu& mesh = *found->second;
        const MeshRow previous = rows[mesh.slot];
        if (!UpdateMesh(mesh, view, stats, &tlasDirty, error))
            return false;
        if (std::memcmp(&previous, &rows[mesh.slot], sizeof(MeshRow)))
            changedRows.push_back(mesh.slot);
    }
    if (!rows.empty())
    {
        const VkDeviceSize bytes = rows.size() * sizeof(MeshRow);
        if (maxStorageBufferRange && bytes > maxStorageBufferRange)
            return _Fail(error, "mesh table exceeds maxStorageBufferRange");
        const bool resized = !meshTable.buffer || meshTable.size < bytes;
        if (!EnsureBuffer(bytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT,
                          false, &meshTable, error))
            return false;
        if (resized)
        {
            if (!Upload(meshTable, rows.data(), bytes, 0, error))
                return false;
            stats.tableUploadBytes += bytes;
        }
        else
        {
            std::sort(changedRows.begin(), changedRows.end());
            changedRows.erase(std::unique(changedRows.begin(), changedRows.end()), changedRows.end());
            for (uint32_t slot : changedRows)
            {
                if (!Upload(meshTable, &rows[slot], sizeof(MeshRow), slot * sizeof(MeshRow), error))
                    return false;
                stats.tableUploadBytes += sizeof(MeshRow);
            }
        }
        VkDescriptorBufferInfo info = {meshTable.buffer, 0, bytes};
        VkWriteDescriptorSet write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
        write.dstSet = descriptorSet;
        write.dstBinding = 3;
        write.descriptorCount = 1;
        write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        write.pBufferInfo = &info;
        vkUpdateDescriptorSets(device, 1, &write, 0, nullptr);
    }
    return !tlasDirty || BuildTlas(stats, error);
}

// ---- pipeline: four bindings (0 TLAS, 1 frame, 2 accumulation, 3 mesh table) ---------------------------------------------------

bool HdRTVkFurnace::Impl::CreatePipeline(const std::vector<uint32_t>& spirv, std::string* error)
{
    VkDescriptorSetLayoutBinding bindings[4] = {};
    for (uint32_t k = 0; k < 4; ++k)
    {
        bindings[k].binding = k;
        bindings[k].descriptorType = (k == 0) ? VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[k].descriptorCount = 1;
        bindings[k].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dslci = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    dslci.bindingCount = 4;
    dslci.pBindings = bindings;
    HDRT_VK(vkCreateDescriptorSetLayout(device, &dslci, nullptr, &setLayout));

    VkPipelineLayoutCreateInfo plci = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    plci.setLayoutCount = 1;
    plci.pSetLayouts = &setLayout;
    const VkPushConstantRange push = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 4 * sizeof(uint32_t) };
    plci.pushConstantRangeCount = 1;
    plci.pPushConstantRanges = &push;
    HDRT_VK(vkCreatePipelineLayout(device, &plci, nullptr, &pipelineLayout));

    VkShaderModuleCreateInfo smci = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smci.codeSize = spirv.size() * sizeof(uint32_t);
    smci.pCode = spirv.data();
    HDRT_VK(vkCreateShaderModule(device, &smci, nullptr, &shader));

    VkComputePipelineCreateInfo pci = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    pci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    pci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    pci.stage.module = shader;
    pci.stage.pName = "main";
    pci.layout = pipelineLayout;
    HDRT_VK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &pci, nullptr, &pipeline));

    VkDescriptorPoolSize poolSizes[2] = { { VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR, 1 }, { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 3 } };
    VkDescriptorPoolCreateInfo dpci = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpci.maxSets = 1;
    dpci.poolSizeCount = 2;
    dpci.pPoolSizes = poolSizes;
    HDRT_VK(vkCreateDescriptorPool(device, &dpci, nullptr, &descriptorPool));

    VkDescriptorSetAllocateInfo dsai = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    dsai.descriptorPool = descriptorPool;
    dsai.descriptorSetCount = 1;
    dsai.pSetLayouts = &setLayout;
    HDRT_VK(vkAllocateDescriptorSets(device, &dsai, &descriptorSet));

    if (!CreateBuffer(sizeof(FrameGpu), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true, &frameBuffer, error))
        return false;
    std::memset(frameBuffer.mapped, 0, sizeof(FrameGpu));

    // Binding 1 now; binding 2 in BeginFrame; bindings 0 and 3 in SetMeshes. No pass is dispatched
    // before all four are written (RenderPass skips the dispatch while there is no scene).
    VkDescriptorBufferInfo frameInfo = { frameBuffer.buffer, 0, VK_WHOLE_SIZE };
    VkWriteDescriptorSet write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
    write.dstSet = descriptorSet;
    write.dstBinding = 1;
    write.descriptorCount = 1;
    write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    write.pBufferInfo = &frameInfo;
    vkUpdateDescriptorSets(device, 1, &write, 0, nullptr);
    return true;
}

// Resolve has no scene descriptors, so it is also valid for a newly empty scene.
bool HdRTVkFurnace::Impl::CreateResolvePipeline(const std::vector<uint32_t>& spirv, std::string* error)
{
    VkDescriptorSetLayoutBinding bindings[2] = {};
    for (uint32_t k = 0; k < 2; ++k)
    {
        bindings[k].binding = k;
        bindings[k].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[k].descriptorCount = 1;
        bindings[k].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo layout = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    layout.bindingCount = 2;
    layout.pBindings = bindings;
    HDRT_VK(vkCreateDescriptorSetLayout(device, &layout, nullptr, &resolveSetLayout));
    const VkPushConstantRange push = { VK_SHADER_STAGE_COMPUTE_BIT, 0, 4 * sizeof(uint32_t) };
    VkPipelineLayoutCreateInfo pipelineInfo = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pipelineInfo.setLayoutCount = 1;
    pipelineInfo.pSetLayouts = &resolveSetLayout;
    pipelineInfo.pushConstantRangeCount = 1;
    pipelineInfo.pPushConstantRanges = &push;
    HDRT_VK(vkCreatePipelineLayout(device, &pipelineInfo, nullptr, &resolvePipelineLayout));
    VkShaderModuleCreateInfo module = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    module.codeSize = spirv.size() * sizeof(uint32_t);
    module.pCode = spirv.data();
    HDRT_VK(vkCreateShaderModule(device, &module, nullptr, &resolveShader));
    VkComputePipelineCreateInfo compute = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    compute.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    compute.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    compute.stage.module = resolveShader;
    compute.stage.pName = "main";
    compute.layout = resolvePipelineLayout;
    HDRT_VK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &compute, nullptr, &resolvePipeline));
    const VkDescriptorPoolSize poolSize = { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2 };
    VkDescriptorPoolCreateInfo poolInfo = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    poolInfo.maxSets = 1;
    poolInfo.poolSizeCount = 1;
    poolInfo.pPoolSizes = &poolSize;
    HDRT_VK(vkCreateDescriptorPool(device, &poolInfo, nullptr, &resolveDescriptorPool));
    VkDescriptorSetAllocateInfo allocation = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    allocation.descriptorPool = resolveDescriptorPool;
    allocation.descriptorSetCount = 1;
    allocation.pSetLayouts = &resolveSetLayout;
    HDRT_VK(vkAllocateDescriptorSets(device, &allocation, &resolveDescriptorSet));
    return true;
}

void HdRTVkFurnace::Impl::RefreshFrameAllocations()
{
    frameStats.accumulationBytes = accumBuffer.allocationSize;
    frameStats.resolveBytes = resolvedBuffer.allocationSize;
    frameStats.readbackBytesAllocated = readbackBuffer.allocationSize + legacyReadbackBuffer.allocationSize;
    frameStats.accumulationDeviceLocal = (accumBuffer.memoryProperties & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) != 0;
    frameStats.resolveDeviceLocal = (resolvedBuffer.memoryProperties & VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT) != 0;
}

// ---- teardown: every object, children before parents -----------------------------------------------

void HdRTVkFurnace::Impl::Destroy()
{
    ready = false;
    if (device)
    {
        vkDeviceWaitIdle(device);
        submissionPending = false;
        if (resolvePipeline)
            vkDestroyPipeline(device, resolvePipeline, nullptr);
        if (resolveShader)
            vkDestroyShaderModule(device, resolveShader, nullptr);
        if (resolvePipelineLayout)
            vkDestroyPipelineLayout(device, resolvePipelineLayout, nullptr);
        if (resolveDescriptorPool)
            vkDestroyDescriptorPool(device, resolveDescriptorPool, nullptr);
        if (resolveSetLayout)
            vkDestroyDescriptorSetLayout(device, resolveSetLayout, nullptr);
        if (pipeline)
            vkDestroyPipeline(device, pipeline, nullptr);
        if (shader)
            vkDestroyShaderModule(device, shader, nullptr);
        if (pipelineLayout)
            vkDestroyPipelineLayout(device, pipelineLayout, nullptr);
        if (descriptorPool)
            vkDestroyDescriptorPool(device, descriptorPool, nullptr);   // frees the descriptor set with it
        if (setLayout)
            vkDestroyDescriptorSetLayout(device, setLayout, nullptr);
        DestroyBuffer(frameBuffer);
        DestroyBuffer(accumBuffer);
        DestroyBuffer(resolvedBuffer);
        DestroyBuffer(readbackBuffer);
        DestroyBuffer(legacyReadbackBuffer);
        DestroyScene();
        if (fence)
            vkDestroyFence(device, fence, nullptr);
        if (pool)
            vkDestroyCommandPool(device, pool, nullptr);                // frees the command buffer with it
        vkDestroyDevice(device, nullptr);
    }
    if (instance)
    {
        if (messenger)
        {
            const auto destroyMessenger =
                reinterpret_cast<PFN_vkDestroyDebugUtilsMessengerEXT>(vkGetInstanceProcAddr(instance, "vkDestroyDebugUtilsMessengerEXT"));
            if (destroyMessenger)
                destroyMessenger(instance, messenger, nullptr);
        }
        vkDestroyInstance(instance, nullptr);
    }
    pipeline = VK_NULL_HANDLE;
    shader = VK_NULL_HANDLE;
    pipelineLayout = VK_NULL_HANDLE;
    descriptorPool = VK_NULL_HANDLE;
    descriptorSet = VK_NULL_HANDLE;
    setLayout = VK_NULL_HANDLE;
    resolvePipeline = VK_NULL_HANDLE;
    resolveShader = VK_NULL_HANDLE;
    resolvePipelineLayout = VK_NULL_HANDLE;
    resolveDescriptorPool = VK_NULL_HANDLE;
    resolveDescriptorSet = VK_NULL_HANDLE;
    resolveSetLayout = VK_NULL_HANDLE;
    fence = VK_NULL_HANDLE;
    submissionPending = false;
    pool = VK_NULL_HANDLE;
    cmd = VK_NULL_HANDLE;
    queue = VK_NULL_HANDLE;
    device = VK_NULL_HANDLE;
    phys = VK_NULL_HANDLE;
    messenger = VK_NULL_HANDLE;
    instance = VK_NULL_HANDLE;
    as = AsApi();
    frameBuffer = Buffer();
    accumBuffer = Buffer();
    resolvedBuffer = Buffer();
    readbackBuffer = Buffer();
    legacyReadbackBuffer = Buffer();
    instanceBuffer = Buffer();
    tlas = Accel();
    hasScene = false;
    width = 0;
    height = 0;
    completedSamples = 0;
    frameValid = resolvedValid = legacyValid = false;
    frameStats = HdRTVkFrameStats();
    validationActive = false;
    lastPassSubmitSeconds = 0.0;
    lastSubmitWallSeconds = 0.0;
}

// ------------------------------------------------------------------------------------------------

HdRTVkFurnace::HdRTVkFurnace() : _impl(new Impl) {}

HdRTVkFurnace::~HdRTVkFurnace()
{
    Destroy();
}

bool HdRTVkFurnace::Init(const HdRTVkOptions& options, std::string* error)
{
    Destroy();
    Impl& d = *_impl;
    bool ok = false;
    try
    {
        const auto read = [&](const std::string& path, std::vector<uint32_t>* words) {
            std::ifstream f(path, std::ios::binary | std::ios::ate);
            if (!f)
                return _Fail(error, "cannot open the SPIR-V file '" + path + "'");
            const std::streamsize bytes = f.tellg();
            if (bytes <= 0 || bytes % 4 != 0)
                return _Fail(error, "the SPIR-V file '" + path + "' is empty or truncated");
            words->resize(size_t(bytes) / 4);
            f.seekg(0);
            if (!f.read(reinterpret_cast<char*>(words->data()), bytes))
                return _Fail(error, "could not read the complete SPIR-V file '" + path + "'");
            return true;
        };
        std::vector<uint32_t> spirv, resolveSpirv;
        const std::string resolvePath = (std::filesystem::path(options.spvPath).parent_path() / "resolve.spv").string();
        ok = read(options.spvPath, &spirv) && read(resolvePath, &resolveSpirv)
            && d.CreateInstance(options.validation, error) && d.CreateDevice(options.allowSoftwareDevice, error)
            && d.CreatePipeline(spirv, error) && d.CreateResolvePipeline(resolveSpirv, error);
    }
    catch (const std::exception& exc)
    {
        _Fail(error, std::string("GPU initialization failed: ") + exc.what());
    }
    if (!ok)
    {
        const std::string name = d.deviceName;
        d.Destroy();
        d.deviceName = name;   // keep the name for the error report
        return false;
    }
    d.ready = true;
    return true;
}

bool HdRTVkFurnace::IsReady() const
{
    return _impl->ready;
}

bool HdRTVkFurnace::SetScene(const float* positions, const float* normals, size_t vertexCount, const uint32_t* indices,
                             const float* albedo, size_t triangleCount, std::string* error)
{
    if (albedo)
        for (size_t triangle = 1; triangle < triangleCount; ++triangle)
            if (std::memcmp(albedo, albedo + 4 * triangle, 4 * sizeof(float)))
                return _Fail(error, "SetScene supports uniform albedo; use SetMeshes for distinct mesh colors");
    HdRTVkMesh mesh;
    mesh.id = "__legacy_scene";
    mesh.geometryVersion = mesh.topologyVersion = ++_impl->legacyVersion;
    mesh.positions = positions;
    mesh.normals = normals;
    mesh.vertexCount = vertexCount;
    mesh.indices = indices;
    mesh.triangleCount = triangleCount;
    if (albedo && triangleCount)
        std::memcpy(mesh.albedo, albedo, sizeof(mesh.albedo));
    return SetMeshes({mesh}, nullptr, error);
}

bool HdRTVkFurnace::SetMeshes(const std::vector<HdRTVkMesh>& meshes, HdRTVkEditStats* stats,
                              std::string* error)
{
    HdRTVkEditStats measured;
    if (stats)
        *stats = measured;
    Impl& d = *_impl;
    if (!d.ready)
        return _Fail(error, "SetMeshes called before a successful Init");
    bool ok = false;
    try
    {
        ok = d.SetMeshes(meshes, measured, error);
    }
    catch (const std::exception& exc)
    {
        _Fail(error, std::string("scene update failed: ") + exc.what());
    }
    if (stats)
        *stats = measured;
    if (!ok)
        d.DestroyScene();   // never leave a partially updated scene available for dispatch
    return ok;
}

bool HdRTVkFurnace::HasScene() const
{
    return _impl->hasScene;
}

bool HdRTVkFurnace::BeginFrame(const HdRTVkFrame& frame, std::string* error)
{
    Impl& d = *_impl;
    d.frameValid = d.resolvedValid = d.legacyValid = false;
    d.completedSamples = 0;
    d.lastPassSubmitSeconds = 0.0;
    d.frameStats = HdRTVkFrameStats();
    if (!d.ready)
        return _Fail(error, "BeginFrame called before a successful Init");
    if (frame.width == 0 || frame.height == 0)
        return _Fail(error, "BeginFrame called with an empty image");

    const VkDeviceSize pixels = VkDeviceSize(frame.width) * frame.height;
    if (pixels > UINT32_MAX || pixels * 4 * sizeof(float) > d.maxStorageBufferRange
        || (uint64_t(frame.width) + 7) / 8 > d.maxDispatchX || (uint64_t(frame.height) + 7) / 8 > d.maxDispatchY)
        return _Fail(error, "the image exceeds Vulkan buffer or dispatch limits");
    const VkDeviceSize bytes = pixels * 4 * sizeof(float);
    const VkBufferUsageFlags usage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT;
    if (!d.EnsureBuffer(bytes, usage, false, &d.accumBuffer, error))
        return false;
    d.width = frame.width;
    d.height = frame.height;
    d.RefreshFrameAllocations();
    VkDescriptorBufferInfo accumInfo = { d.accumBuffer.buffer, 0, bytes };
    VkWriteDescriptorSet write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
    write.dstSet = d.descriptorSet;
    write.dstBinding = 2;
    write.descriptorCount = 1;
    write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    write.pBufferInfo = &accumInfo;
    vkUpdateDescriptorSets(d.device, 1, &write, 0, nullptr);

    FrameGpu gpu = {};
    std::memcpy(gpu.invView, frame.invView, sizeof(gpu.invView));
    std::memcpy(gpu.invProj, frame.invProj, sizeof(gpu.invProj));
    gpu.dome[0] = frame.dome[0];
    gpu.dome[1] = frame.dome[1];
    gpu.dome[2] = frame.dome[2];
    gpu.dome[3] = 0.0f;
    gpu.size[0] = frame.width;
    gpu.size[1] = frame.height;
    gpu.size[2] = frame.bounces;
    gpu.size[3] = frame.seed;
    gpu.pass[0] = 0;
    gpu.pass[1] = frame.orthographic ? 1u : 0u;
    std::memcpy(d.frameBuffer.mapped, &gpu, sizeof(gpu));
    if (!d.BeginCommands(error))
        return false;
    VkMemoryBarrier before = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
    before.srcAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    before.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    vkCmdPipelineBarrier(d.cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 1, &before, 0, nullptr, 0, nullptr);
    vkCmdFillBuffer(d.cmd, d.accumBuffer.buffer, 0, bytes, 0);
    VkMemoryBarrier after = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
    after.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    after.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    vkCmdPipelineBarrier(d.cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         0, 1, &after, 0, nullptr, 0, nullptr);
    if (!d.Submit(error))
        return false;
    ++d.frameStats.clearSubmits;
    d.frameValid = true;
    return true;
}

bool HdRTVkFurnace::Impl::ResolveToHost(uint32_t samples, bool legacy, std::string* error)
{
    if (!ready || !frameValid)
        return _Fail(error, "cannot read back an invalid frame");
    const VkDeviceSize bytes = VkDeviceSize(width) * height * 4 * sizeof(float);
    const VkBufferUsageFlags outputUsage = VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_SRC_BIT;
    Buffer& host = legacy ? legacyReadbackBuffer : readbackBuffer;
    if (!EnsureBuffer(bytes, outputUsage, false, &resolvedBuffer, error)
        || !EnsureBuffer(bytes, VK_BUFFER_USAGE_TRANSFER_DST_BIT, true, &host, error, true))
        return false;
    RefreshFrameAllocations();
    const VkDescriptorBufferInfo buffers[2] = { {accumBuffer.buffer, 0, bytes}, {resolvedBuffer.buffer, 0, bytes} };
    VkWriteDescriptorSet writes[2] = {};
    for (uint32_t k = 0; k < 2; ++k)
    {
        writes[k].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[k].dstSet = resolveDescriptorSet;
        writes[k].dstBinding = k;
        writes[k].descriptorCount = 1;
        writes[k].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[k].pBufferInfo = &buffers[k];
    }
    vkUpdateDescriptorSets(device, 2, writes, 0, nullptr);
    if (!BeginCommands(error))
        return false;
    VkMemoryBarrier before = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
    before.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
    before.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &before, 0, nullptr, 0, nullptr);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, resolvePipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, resolvePipelineLayout, 0, 1, &resolveDescriptorSet, 0, nullptr);
    const uint32_t parameters[4] = { width, height, samples, 0 };
    vkCmdPushConstants(cmd, resolvePipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(parameters), parameters);
    vkCmdDispatch(cmd, (width + 7) / 8, (height + 7) / 8, 1);
    VkBufferMemoryBarrier toCopy = { VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER };
    toCopy.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT;
    toCopy.dstAccessMask = VK_ACCESS_TRANSFER_READ_BIT;
    toCopy.srcQueueFamilyIndex = toCopy.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    toCopy.buffer = resolvedBuffer.buffer;
    toCopy.size = bytes;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, nullptr, 1, &toCopy, 0, nullptr);
    const VkBufferCopy copy = { 0, 0, bytes };
    vkCmdCopyBuffer(cmd, resolvedBuffer.buffer, host.buffer, 1, &copy);
    VkBufferMemoryBarrier toHost = { VK_STRUCTURE_TYPE_BUFFER_MEMORY_BARRIER };
    toHost.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    toHost.dstAccessMask = VK_ACCESS_HOST_READ_BIT;
    toHost.srcQueueFamilyIndex = toHost.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    toHost.buffer = host.buffer;
    toHost.size = bytes;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT,
                         0, 0, nullptr, 1, &toHost, 0, nullptr);
    if (!Submit(error))
        return false;
    frameStats.readbackBytes += bytes;
    if (legacy)
    {
        ++frameStats.compatibilityReadbackSubmits;
        legacyValid = true;
    }
    else
    {
        ++frameStats.resolveSubmits;
        ++frameStats.resolveDispatches;
        ++frameStats.readbackCount;
        frameStats.lastResolveSeconds = lastSubmitWallSeconds;
        frameStats.totalResolveSeconds += lastSubmitWallSeconds;
        resolvedValid = true;
    }
    return true;
}

bool HdRTVkFurnace::RenderBatch(uint32_t firstPass, uint32_t count, std::string* error)
{
    Impl& d = *_impl;
    if (!d.ready || !d.frameValid)
        return _Fail(error, "RenderBatch called before a successful BeginFrame");
    if (count == 0 || count > 32 || firstPass != d.completedSamples || count > UINT32_MAX - firstPass)
        return _Fail(error, "RenderBatch requires 1..32 contiguous samples within uint32 range");
    d.frameStats.lastBatchSeconds = 0.0;
    if (!d.hasScene)
    {
        d.completedSamples += count;
        return true;
    }
    if (!d.BeginCommands(error))
        return false;
    VkMemoryBarrier barrier = { VK_STRUCTURE_TYPE_MEMORY_BARRIER };
    barrier.srcAccessMask = VK_ACCESS_SHADER_WRITE_BIT | VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT | VK_ACCESS_SHADER_WRITE_BIT;
    vkCmdPipelineBarrier(d.cmd, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT | VK_PIPELINE_STAGE_TRANSFER_BIT,
                         VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 1, &barrier, 0, nullptr, 0, nullptr);
    vkCmdBindPipeline(d.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, d.pipeline);
    vkCmdBindDescriptorSets(d.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, d.pipelineLayout, 0, 1, &d.descriptorSet, 0, nullptr);
    const uint32_t parameters[4] = { firstPass, count, 0, 0 };
    vkCmdPushConstants(d.cmd, d.pipelineLayout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(parameters), parameters);
    vkCmdDispatch(d.cmd, (d.width + 7) / 8, (d.height + 7) / 8, 1);
    if (!d.Submit(error, true))
        return false;
    d.completedSamples += count;
    ++d.frameStats.traceSubmits;
    ++d.frameStats.traceDispatches;
    d.frameStats.tracedSamples += count;
    d.frameStats.lastBatchSeconds = d.lastSubmitWallSeconds;
    d.frameStats.maxBatchSeconds = std::max(d.frameStats.maxBatchSeconds, d.lastSubmitWallSeconds);
    return true;
}

bool HdRTVkFurnace::RenderPass(uint32_t passIndex, std::string* error)
{
    _impl->legacyValid = false;
    return RenderBatch(passIndex, 1, error) && _impl->ResolveToHost(0, true, error);
}

bool HdRTVkFurnace::Resolve(uint32_t completedSamples, std::string* error)
{
    Impl& d = *_impl;
    d.resolvedValid = false;
    if (!d.ready || !d.frameValid || completedSamples == 0 || completedSamples != d.completedSamples)
        return _Fail(error, "Resolve requires the current frame's nonzero completed sample count");
    return d.ResolveToHost(completedSamples, false, error);
}

const float* HdRTVkFurnace::ResolvedPixels() const
{
    return _impl->resolvedValid ? static_cast<const float*>(_impl->readbackBuffer.mapped) : nullptr;
}

HdRTVkFrameStats HdRTVkFurnace::FrameStats() const
{
    _impl->RefreshFrameAllocations();
    return _impl->frameStats;
}

double HdRTVkFurnace::LastPassSubmitSeconds() const
{
    return _impl->lastPassSubmitSeconds;
}

const float* HdRTVkFurnace::Accumulation() const
{
    return _impl->legacyValid ? static_cast<const float*>(_impl->legacyReadbackBuffer.mapped) : nullptr;
}

void HdRTVkFurnace::Destroy()
{
    _impl->Destroy();
}

const std::string& HdRTVkFurnace::DeviceName() const
{
    return _impl->deviceName;
}

bool HdRTVkFurnace::ValidationActive() const
{
    return _impl->validationActive;
}

int HdRTVkFurnace::ValidationErrors() const
{
    return _impl->validationErrors.load();
}

int HdRTVkFurnace::ValidationWarnings() const
{
    return _impl->validationWarnings.load();
}
