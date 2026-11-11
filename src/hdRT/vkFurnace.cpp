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
#include <cstdio>
#include <cstring>
#include <fstream>
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
    AsApi as;
    VkDeviceSize scratchAlignment = 1;
    uint64_t maxPrimitiveCount = 0;        // VkPhysicalDeviceAccelerationStructurePropertiesKHR
    uint32_t maxStorageBufferRange = 0;    // VkPhysicalDeviceLimits

    // The scene: one world-space mesh (SetScene). Positions double as the BLAS build input.
    Buffer sceneVb, sceneNb, sceneIb, sceneAlbedo, instanceBuffer;
    Accel blas, tlas;
    bool hasScene = false;

    VkDescriptorSetLayout setLayout = VK_NULL_HANDLE;
    VkPipelineLayout pipelineLayout = VK_NULL_HANDLE;
    VkShaderModule shader = VK_NULL_HANDLE;
    VkPipeline pipeline = VK_NULL_HANDLE;
    VkDescriptorPool descriptorPool = VK_NULL_HANDLE;
    VkDescriptorSet descriptorSet = VK_NULL_HANDLE;

    Buffer frameBuffer;   // one FrameGpu, host visible
    Buffer accumBuffer;   // one vec4 per pixel, host visible
    uint32_t width = 0;
    uint32_t height = 0;

    std::string deviceName;
    bool validationActive = false;
    bool ready = false;
    std::atomic<int> validationErrors{ 0 };
    std::atomic<int> validationWarnings{ 0 };

    static VKAPI_ATTR VkBool32 VKAPI_CALL OnDebug(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                                 const VkDebugUtilsMessengerCallbackDataEXT* data, void* user);

    uint32_t FindMemoryType(uint32_t typeBits, VkMemoryPropertyFlags wanted) const;
    // gpuReads: the kernel reads it on every hit; prefer device-local memory the host can write (resizable BAR).
    bool CreateBuffer(VkDeviceSize size, VkBufferUsageFlags usage, bool hostVisible, Buffer* out, std::string* error, bool cpuReads = false,
                      bool gpuReads = false);
    void DestroyBuffer(Buffer& b);
    VkDeviceAddress BufferAddress(VkBuffer buffer) const;
    bool Submit(std::string* error);
    bool BuildAccel(VkAccelerationStructureTypeKHR type, const VkAccelerationStructureGeometryKHR& geometry, uint32_t primitiveCount,
                    Accel* out, std::string* error);
    void DestroyAccel(Accel& a);

    bool CreateInstance(bool wantValidation, std::string* error);
    bool CreateDevice(bool allowSoftwareDevice, std::string* error);
    bool BuildScene(const float* positions, const float* normals, size_t vertexCount, const uint32_t* indices,
                    const float* albedo, size_t triangleCount, std::string* error);
    void DestroyScene();
    bool CreatePipeline(const std::vector<uint32_t>& spirv, std::string* error);
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
    ai.memoryTypeIndex = UINT32_MAX;
    if (hostVisible && cpuReads)
        ai.memoryTypeIndex = FindMemoryType(req.memoryTypeBits, hostFlags | VK_MEMORY_PROPERTY_HOST_CACHED_BIT);
    else if (hostVisible && gpuReads)
        ai.memoryTypeIndex = FindMemoryType(req.memoryTypeBits, hostFlags | VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (ai.memoryTypeIndex == UINT32_MAX)
        ai.memoryTypeIndex = FindMemoryType(req.memoryTypeBits, hostVisible ? hostFlags : VkMemoryPropertyFlags(VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT));
    if (ai.memoryTypeIndex == UINT32_MAX)
        return _Fail(error, "no suitable memory type for a buffer");
    if (usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT)
        ai.pNext = &flags;
    HDRT_VK(vkAllocateMemory(device, &ai, nullptr, &out->memory));
    HDRT_VK(vkBindBufferMemory(device, out->buffer, out->memory, 0));
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

// Ends the one command buffer, submits it and waits for the queue to drain.
bool HdRTVkFurnace::Impl::Submit(std::string* error)
{
    HDRT_VK(vkEndCommandBuffer(cmd));
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    HDRT_VK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE));
    HDRT_VK(vkQueueWaitIdle(queue));
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

    if (!CreateBuffer(sizes.accelerationStructureSize,
                      VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, false, &out->storage, error))
        return false;
    VkAccelerationStructureCreateInfoKHR ci = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR };
    ci.buffer = out->storage.buffer;
    ci.size = sizes.accelerationStructureSize;
    ci.type = type;
    HDRT_VK(as.create(device, &ci, nullptr, &out->handle));

    // Scratch memory must start at a multiple of the device's scratch alignment. Over-allocate
    // and round the address up so the requirement holds whatever the allocator returns.
    const VkDeviceSize align = scratchAlignment ? scratchAlignment : 1;
    Buffer scratch;
    if (!CreateBuffer(sizes.buildScratchSize + align, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, false, &scratch,
                      error))
    {
        DestroyBuffer(scratch);
        return false;
    }
    const VkDeviceAddress scratchBase = BufferAddress(scratch.buffer);
    const VkDeviceAddress scratchAligned = (scratchBase + align - 1) / align * align;

    build.dstAccelerationStructure = out->handle;
    build.scratchData.deviceAddress = scratchAligned;

    VkAccelerationStructureBuildRangeInfoKHR range = {};
    range.primitiveCount = primitiveCount;
    const VkAccelerationStructureBuildRangeInfoKHR* ranges = &range;

    bool ok = true;
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    const VkResult begun = vkBeginCommandBuffer(cmd, &bi);
    if (begun != VK_SUCCESS)
        ok = _FailVk(error, begun, "vkBeginCommandBuffer(cmd, &bi)");
    if (ok)
    {
        as.cmd_build(cmd, 1, &build, &ranges);
        ok = Submit(error);
    }
    DestroyBuffer(scratch);
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
            rayQuery = rq.rayQuery && asf.accelerationStructure && v12.bufferDeviceAddress;
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
    maxStorageBufferRange = props2.properties.limits.maxStorageBufferRange;

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
    return true;
}

// ---- scene: one world-space mesh, one bottom-level structure, one identity instance -----------------

void HdRTVkFurnace::Impl::DestroyScene()
{
    hasScene = false;
    if (device && queue)
        vkQueueWaitIdle(queue);   // nothing may still be reading the old buffers
    DestroyAccel(tlas);
    DestroyBuffer(instanceBuffer);
    DestroyAccel(blas);
    DestroyBuffer(sceneVb);
    DestroyBuffer(sceneNb);
    DestroyBuffer(sceneIb);
    DestroyBuffer(sceneAlbedo);
}

bool HdRTVkFurnace::Impl::BuildScene(const float* positions, const float* normals, size_t vertexCount, const uint32_t* indices,
                                     const float* albedo, size_t triangleCount, std::string* error)
{
    DestroyScene();
    if (triangleCount == 0 || vertexCount == 0)
        return true;   // no geometry: no scene, every pass renders black with alpha 0
    // The shader indexes 3 floats per vertex and 3 indices per triangle in 32-bit arithmetic.
    if (vertexCount > size_t(UINT32_MAX) / 3 || triangleCount > size_t(UINT32_MAX) / 3)
        return _Fail(error, "the scene is too large for 32-bit indexing in the kernel");
    if (maxPrimitiveCount && uint64_t(triangleCount) > maxPrimitiveCount)
        return _Fail(error, "the scene has more triangles than this device allows in one acceleration structure");

    // Positions are a build input and a storage buffer (the shader interpolates the hit point from them);
    // the rest are storage buffers only. Written once per edit from the host; placed in device-local memory
    // the host can write when the device offers it (resizable BAR), else in host memory.
    const VkBufferUsageFlags inputUsage =
        VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    const VkBufferUsageFlags geomUsage = inputUsage | VK_BUFFER_USAGE_STORAGE_BUFFER_BIT;
    const VkDeviceSize vbBytes = VkDeviceSize(vertexCount) * 3 * sizeof(float);
    const VkDeviceSize ibBytes = VkDeviceSize(triangleCount) * 3 * sizeof(uint32_t);
    const VkDeviceSize abBytes = VkDeviceSize(triangleCount) * 4 * sizeof(float);
    if (maxStorageBufferRange && std::max(std::max(vbBytes, ibBytes), abBytes) > VkDeviceSize(maxStorageBufferRange))
        return _Fail(error, "a scene buffer is larger than this device's maxStorageBufferRange");
    if (!CreateBuffer(vbBytes, geomUsage, true, &sceneVb, error, false, true))
        return false;
    if (!CreateBuffer(vbBytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true, &sceneNb, error, false, true))
        return false;
    if (!CreateBuffer(ibBytes, geomUsage, true, &sceneIb, error, false, true))
        return false;
    if (!CreateBuffer(abBytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true, &sceneAlbedo, error, false, true))
        return false;
    std::memcpy(sceneVb.mapped, positions, size_t(vbBytes));
    std::memcpy(sceneNb.mapped, normals, size_t(vbBytes));
    std::memcpy(sceneIb.mapped, indices, size_t(ibBytes));
    std::memcpy(sceneAlbedo.mapped, albedo, size_t(abBytes));

    VkAccelerationStructureGeometryKHR geometry = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
    geometry.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
    geometry.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
    geometry.geometry.triangles.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR;
    geometry.geometry.triangles.vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
    geometry.geometry.triangles.vertexData.deviceAddress = BufferAddress(sceneVb.buffer);
    geometry.geometry.triangles.vertexStride = 3 * sizeof(float);
    geometry.geometry.triangles.maxVertex = uint32_t(vertexCount - 1);
    geometry.geometry.triangles.indexType = VK_INDEX_TYPE_UINT32;
    geometry.geometry.triangles.indexData.deviceAddress = BufferAddress(sceneIb.buffer);
    if (!BuildAccel(VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR, geometry, uint32_t(triangleCount), &blas, error))
        return false;

    // One instance, identity transform: the render pass already applied every mesh transform.
    // Back faces are hit like front faces (Karma shades and shadows them the same).
    VkAccelerationStructureInstanceKHR instanceData = {};
    instanceData.transform.matrix[0][0] = 1.0f;
    instanceData.transform.matrix[1][1] = 1.0f;
    instanceData.transform.matrix[2][2] = 1.0f;
    instanceData.instanceCustomIndex = 0;
    instanceData.mask = 0xFF;
    instanceData.instanceShaderBindingTableRecordOffset = 0;
    instanceData.flags = VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
    instanceData.accelerationStructureReference = blas.address;
    if (!CreateBuffer(sizeof(instanceData), inputUsage, true, &instanceBuffer, error))
        return false;
    std::memcpy(instanceBuffer.mapped, &instanceData, sizeof(instanceData));

    VkAccelerationStructureGeometryKHR tlasGeometry = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
    tlasGeometry.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
    tlasGeometry.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
    tlasGeometry.geometry.instances.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR;
    tlasGeometry.geometry.instances.arrayOfPointers = VK_FALSE;
    tlasGeometry.geometry.instances.data.deviceAddress = BufferAddress(instanceBuffer.buffer);
    if (!BuildAccel(VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR, tlasGeometry, 1, &tlas, error))
        return false;

    // Point bindings 0 and 3..6 at the new scene. The queue is idle (BuildAccel waits), so no pass uses them.
    VkWriteDescriptorSetAccelerationStructureKHR asWrite = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR };
    asWrite.accelerationStructureCount = 1;
    asWrite.pAccelerationStructures = &tlas.handle;
    const VkDescriptorBufferInfo infos[4] = { { sceneVb.buffer, 0, VK_WHOLE_SIZE }, { sceneNb.buffer, 0, VK_WHOLE_SIZE },
                                              { sceneIb.buffer, 0, VK_WHOLE_SIZE }, { sceneAlbedo.buffer, 0, VK_WHOLE_SIZE } };
    VkWriteDescriptorSet writes[5] = {};
    writes[0].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[0].dstSet = descriptorSet;
    writes[0].dstBinding = 0;
    writes[0].descriptorCount = 1;
    writes[0].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    writes[0].pNext = &asWrite;
    for (uint32_t k = 0; k < 4; ++k)
    {
        writes[k + 1].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[k + 1].dstSet = descriptorSet;
        writes[k + 1].dstBinding = 3 + k;
        writes[k + 1].descriptorCount = 1;
        writes[k + 1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[k + 1].pBufferInfo = &infos[k];
    }
    vkUpdateDescriptorSets(device, 5, writes, 0, nullptr);
    hasScene = true;
    return true;
}

// ---- pipeline: one compute shader, seven bindings (0 tlas, 1 frame, 2 accumulation, 3-6 the scene) ---------------------------------------------------

bool HdRTVkFurnace::Impl::CreatePipeline(const std::vector<uint32_t>& spirv, std::string* error)
{
    VkDescriptorSetLayoutBinding bindings[7] = {};
    for (uint32_t k = 0; k < 7; ++k)
    {
        bindings[k].binding = k;
        bindings[k].descriptorType = (k == 0) ? VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR : VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        bindings[k].descriptorCount = 1;
        bindings[k].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo dslci = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    dslci.bindingCount = 7;
    dslci.pBindings = bindings;
    HDRT_VK(vkCreateDescriptorSetLayout(device, &dslci, nullptr, &setLayout));

    VkPipelineLayoutCreateInfo plci = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    plci.setLayoutCount = 1;
    plci.pSetLayouts = &setLayout;
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

    VkDescriptorPoolSize poolSizes[2] = { { VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR, 1 }, { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 6 } };
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

    // Binding 1 now; binding 2 in BeginFrame; bindings 0 and 3..6 in BuildScene. No pass is dispatched
    // before all seven are written (RenderPass skips the dispatch while there is no scene).
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

// ---- teardown: every object, children before parents -----------------------------------------------

void HdRTVkFurnace::Impl::Destroy()
{
    ready = false;
    if (device)
    {
        vkDeviceWaitIdle(device);
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
        DestroyScene();
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
    sceneVb = Buffer();
    sceneNb = Buffer();
    sceneIb = Buffer();
    sceneAlbedo = Buffer();
    instanceBuffer = Buffer();
    blas = Accel();
    tlas = Accel();
    hasScene = false;
    width = 0;
    height = 0;
    validationActive = false;
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

    std::vector<uint32_t> spirv;
    {
        std::ifstream f(options.spvPath, std::ios::binary | std::ios::ate);
        if (!f)
            return _Fail(error, "cannot open the SPIR-V file '" + options.spvPath + "'");
        const std::streamsize bytes = f.tellg();
        if (bytes <= 0 || bytes % 4 != 0)
            return _Fail(error, "the SPIR-V file '" + options.spvPath + "' is empty or truncated");
        spirv.resize(size_t(bytes) / 4);
        f.seekg(0);
        f.read(reinterpret_cast<char*>(spirv.data()), bytes);
    }

    Impl& d = *_impl;
    const bool ok = d.CreateInstance(options.validation, error) && d.CreateDevice(options.allowSoftwareDevice, error) &&
                    d.CreatePipeline(spirv, error);
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
    Impl& d = *_impl;
    if (!d.ready)
        return _Fail(error, "SetScene called before a successful Init");
    return d.BuildScene(positions, normals, vertexCount, indices, albedo, triangleCount, error);
}

bool HdRTVkFurnace::HasScene() const
{
    return _impl->hasScene;
}

bool HdRTVkFurnace::BeginFrame(const HdRTVkFrame& frame, std::string* error)
{
    Impl& d = *_impl;
    if (!d.ready)
        return _Fail(error, "BeginFrame called before a successful Init");
    if (frame.width == 0 || frame.height == 0)
        return _Fail(error, "BeginFrame called with an empty image");

    if (frame.width != d.width || frame.height != d.height || !d.accumBuffer.buffer)
    {
        HDRT_VK(vkQueueWaitIdle(d.queue));
        d.DestroyBuffer(d.accumBuffer);
        d.width = 0;
        d.height = 0;
        const VkDeviceSize bytes = VkDeviceSize(frame.width) * frame.height * 4 * sizeof(float);
        if (!d.CreateBuffer(bytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true, &d.accumBuffer, error, /*cpuReads=*/true))
            return false;
        d.width = frame.width;
        d.height = frame.height;

        VkDescriptorBufferInfo accumInfo = { d.accumBuffer.buffer, 0, VK_WHOLE_SIZE };
        VkWriteDescriptorSet write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET };
        write.dstSet = d.descriptorSet;
        write.dstBinding = 2;
        write.descriptorCount = 1;
        write.descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        write.pBufferInfo = &accumInfo;
        vkUpdateDescriptorSets(d.device, 1, &write, 0, nullptr);
    }
    std::memset(d.accumBuffer.mapped, 0, size_t(d.accumBuffer.size));

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
    return true;
}

bool HdRTVkFurnace::RenderPass(uint32_t passIndex, std::string* error)
{
    Impl& d = *_impl;
    if (!d.ready || !d.accumBuffer.buffer)
        return _Fail(error, "RenderPass called before BeginFrame");

    // No scene: every camera ray misses, so the accumulation (zeroed in BeginFrame) is already the answer.
    if (!d.hasScene)
        return true;

    static_cast<FrameGpu*>(d.frameBuffer.mapped)->pass[0] = passIndex;

    HDRT_VK(vkResetCommandBuffer(d.cmd, 0));
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    HDRT_VK(vkBeginCommandBuffer(d.cmd, &bi));
    vkCmdBindPipeline(d.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, d.pipeline);
    vkCmdBindDescriptorSets(d.cmd, VK_PIPELINE_BIND_POINT_COMPUTE, d.pipelineLayout, 0, 1, &d.descriptorSet, 0, nullptr);
    vkCmdDispatch(d.cmd, (d.width + 7) / 8, (d.height + 7) / 8, 1);   // local size is 8 x 8 in furnace.comp
    return d.Submit(error);
}

const float* HdRTVkFurnace::Accumulation() const
{
    return static_cast<const float*>(_impl->accumBuffer.mapped);
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
