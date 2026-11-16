// rayq_host.cpp - s2-rayquery host: builds a BLAS/TLAS, runs rayq.comp, writes measurements.
//
// What it does
//   1. Reads the exam's config.json (camera, 24 directions, occluder triangle, light, 144 probes,
//      rho, E, bounce budget, samples).
//   2. Builds two bottom-level acceleration structures (unit cube, occluder triangle) and one
//      top-level structure with two instances (masks 0x1 and 0x2).
//   3. Dispatches one compute shader that uses inline ray queries (GL_EXT_ray_query).
//   4. Writes hits.csv, occlusion.csv and furnace.csv. It does not grade itself.
//
// Usage
//   rayq_host --spv rayq.spv --config config.json --seed N --out-dir DIR [--no-validation]
//
// Exit codes: 0 ran and wrote all outputs, 14 validation layer reported an error,
//             15 bad arguments, unreadable input, missing device feature or a failed Vulkan call.
//
// Build (single translation unit, Vulkan SDK only):
//   cl /std:c++17 /O2 /EHsc /I%VULKAN_SDK%\Include rayq_host.cpp /link /LIBPATH:%VULKAN_SDK%\Lib vulkan-1.lib
//   g++ -std=c++17 -O2 rayq_host.cpp -o rayq_host -lvulkan
//
// The acceleration-structure call sequence is adapted from Sascha Willems' Vulkan examples
// (examples/rayquery, base/VulkanRaytracingSample), MIT License, Copyright (c) 2016 Sascha Willems.
// See third_party/sascha_willems_vulkan/LICENSE.md.

#include <vulkan/vulkan.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

// ------------------------------------------------------------------------------------------
// diagnostics

static int g_validation_errors = 0;
static int g_validation_warnings = 0;

static VKAPI_ATTR VkBool32 VKAPI_CALL on_debug(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                               const VkDebugUtilsMessengerCallbackDataEXT* data, void*)
{
    if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT)
    {
        ++g_validation_errors;
        std::fprintf(stdout, "Validation Error: %s\n", data->pMessage);
    }
    else if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT)
    {
        ++g_validation_warnings;
        std::fprintf(stdout, "validation warning: %s\n", data->pMessage);
    }
    return VK_FALSE;
}

[[noreturn]] static void fail(const char* what)
{
    std::fprintf(stdout, "rayq_host: %s\n", what);
    std::fflush(stdout);
    std::exit(15);
}

#define CHECK_VK(call)                                                                        \
    do                                                                                        \
    {                                                                                         \
        const VkResult check_vk_result = (call);                                              \
        if (check_vk_result != VK_SUCCESS)                                                    \
        {                                                                                     \
            std::fprintf(stdout, "rayq_host: Vulkan call failed (%d): %s\n", int(check_vk_result), #call); \
            std::fflush(stdout);                                                              \
            std::exit(15);                                                                    \
        }                                                                                     \
    } while (0)

static double seconds_since(const std::chrono::steady_clock::time_point& t0)
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

// ------------------------------------------------------------------------------------------
// config.json: the shape is fixed by the exam, so a tiny reader is enough. For a key it returns
// every number found in that key's value (a scalar, an array, or an array of arrays).

static std::string read_text_file(const char* path)
{
    std::ifstream f(path, std::ios::binary);
    if (!f)
        return std::string();
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

static std::vector<double> json_numbers(const std::string& text, const char* key)
{
    std::vector<double> out;
    const std::string needle = std::string("\"") + key + "\"";
    size_t pos = text.find(needle);
    if (pos == std::string::npos)
        return out;
    pos = text.find(':', pos + needle.size());
    if (pos == std::string::npos)
        return out;
    ++pos;
    int depth = 0;
    while (pos < text.size())
    {
        const char c = text[pos];
        if (c == '[')
        {
            ++depth;
            ++pos;
        }
        else if (c == ']')
        {
            --depth;
            ++pos;
            if (depth <= 0)
                break;
        }
        else if (c == '-' || c == '+' || (c >= '0' && c <= '9'))
        {
            char* end = nullptr;
            out.push_back(std::strtod(text.c_str() + pos, &end));
            pos = size_t(end - text.c_str());
            if (depth == 0)
                break;
        }
        else if ((c == ',' || c == '}') && depth == 0)
        {
            break;
        }
        else
        {
            ++pos;
        }
    }
    return out;
}

// ------------------------------------------------------------------------------------------
// GPU-side layouts (std430; every member is a 16-byte vector, so C++ and GLSL agree)

static const uint32_t MAX_DIRS = 24;
static const uint32_t MAX_PROBES = 144;
static const uint32_t MAX_FURN = 4;

struct Vec4
{
    float x, y, z, w;
};

struct SceneGpu
{
    Vec4 camera;
    Vec4 light;
    Vec4 params;          // rho, E, unused, unused
    uint32_t counts[4];   // nDirs, nProbes, nFurn, samples
    uint32_t misc[4];     // budget, seed, unused, unused
    Vec4 dirs[MAX_DIRS];
    Vec4 probes[MAX_PROBES];
    Vec4 furn[MAX_FURN];
};

// ------------------------------------------------------------------------------------------
// Vulkan helpers

struct Buffer
{
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void* mapped = nullptr;
    VkDeviceSize size = 0;
};

static uint32_t find_memory_type(VkPhysicalDevice phys, uint32_t type_bits, VkMemoryPropertyFlags wanted)
{
    VkPhysicalDeviceMemoryProperties mp;
    vkGetPhysicalDeviceMemoryProperties(phys, &mp);
    for (uint32_t i = 0; i < mp.memoryTypeCount; ++i)
        if ((type_bits & (1u << i)) && (mp.memoryTypes[i].propertyFlags & wanted) == wanted)
            return i;
    fail("no suitable memory type");
}

static Buffer create_buffer(VkPhysicalDevice phys, VkDevice device, VkDeviceSize size, VkBufferUsageFlags usage, bool host_visible)
{
    Buffer b;
    b.size = size;
    VkBufferCreateInfo bi = { VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = size;
    bi.usage = usage;
    bi.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    CHECK_VK(vkCreateBuffer(device, &bi, nullptr, &b.buffer));

    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, b.buffer, &req);
    VkMemoryAllocateFlagsInfo flags = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_FLAGS_INFO };
    flags.flags = VK_MEMORY_ALLOCATE_DEVICE_ADDRESS_BIT;
    VkMemoryAllocateInfo ai = { VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = find_memory_type(phys, req.memoryTypeBits,
                                          host_visible ? (VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT)
                                                       : VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (usage & VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT)
        ai.pNext = &flags;
    CHECK_VK(vkAllocateMemory(device, &ai, nullptr, &b.memory));
    CHECK_VK(vkBindBufferMemory(device, b.buffer, b.memory, 0));
    if (host_visible)
        CHECK_VK(vkMapMemory(device, b.memory, 0, VK_WHOLE_SIZE, 0, &b.mapped));
    return b;
}

static void destroy_buffer(VkDevice device, Buffer& b)
{
    if (b.mapped)
        vkUnmapMemory(device, b.memory);
    if (b.buffer)
        vkDestroyBuffer(device, b.buffer, nullptr);
    if (b.memory)
        vkFreeMemory(device, b.memory, nullptr);
    b = Buffer();
}

static VkDeviceAddress buffer_address(VkDevice device, VkBuffer buffer)
{
    VkBufferDeviceAddressInfo info = { VK_STRUCTURE_TYPE_BUFFER_DEVICE_ADDRESS_INFO };
    info.buffer = buffer;
    return vkGetBufferDeviceAddress(device, &info);
}

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

struct Context
{
    VkPhysicalDevice phys = VK_NULL_HANDLE;
    VkDevice device = VK_NULL_HANDLE;
    VkQueue queue = VK_NULL_HANDLE;
    VkCommandPool pool = VK_NULL_HANDLE;
    AsApi as;
    VkDeviceSize scratch_alignment = 1;
};

static void run_commands(const Context& ctx, VkCommandBuffer cmd)
{
    CHECK_VK(vkEndCommandBuffer(cmd));
    VkSubmitInfo si = { VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    CHECK_VK(vkQueueSubmit(ctx.queue, 1, &si, VK_NULL_HANDLE));
    CHECK_VK(vkQueueWaitIdle(ctx.queue));
}

static VkCommandBuffer begin_commands(const Context& ctx)
{
    VkCommandBufferAllocateInfo ai = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    ai.commandPool = ctx.pool;
    ai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    ai.commandBufferCount = 1;
    VkCommandBuffer cmd = VK_NULL_HANDLE;
    CHECK_VK(vkAllocateCommandBuffers(ctx.device, &ai, &cmd));
    VkCommandBufferBeginInfo bi = { VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    bi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    CHECK_VK(vkBeginCommandBuffer(cmd, &bi));
    return cmd;
}

// Builds one acceleration structure from one geometry and waits for it. `type` selects BLAS or TLAS.
static Accel build_accel(const Context& ctx, VkAccelerationStructureTypeKHR type, const VkAccelerationStructureGeometryKHR& geometry,
                         uint32_t primitive_count)
{
    VkAccelerationStructureBuildGeometryInfoKHR build = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_GEOMETRY_INFO_KHR };
    build.type = type;
    build.flags = VK_BUILD_ACCELERATION_STRUCTURE_PREFER_FAST_TRACE_BIT_KHR;
    build.mode = VK_BUILD_ACCELERATION_STRUCTURE_MODE_BUILD_KHR;
    build.geometryCount = 1;
    build.pGeometries = &geometry;

    VkAccelerationStructureBuildSizesInfoKHR sizes = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_BUILD_SIZES_INFO_KHR };
    ctx.as.build_sizes(ctx.device, VK_ACCELERATION_STRUCTURE_BUILD_TYPE_DEVICE_KHR, &build, &primitive_count, &sizes);

    Accel accel;
    accel.storage = create_buffer(ctx.phys, ctx.device, sizes.accelerationStructureSize,
                                  VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_STORAGE_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, false);
    VkAccelerationStructureCreateInfoKHR ci = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_CREATE_INFO_KHR };
    ci.buffer = accel.storage.buffer;
    ci.size = sizes.accelerationStructureSize;
    ci.type = type;
    CHECK_VK(ctx.as.create(ctx.device, &ci, nullptr, &accel.handle));

    // Scratch memory must start at a multiple of the device's scratch alignment. Over-allocate
    // and round the address up so the requirement holds whatever the allocator returns.
    const VkDeviceSize align = ctx.scratch_alignment ? ctx.scratch_alignment : 1;
    Buffer scratch = create_buffer(ctx.phys, ctx.device, sizes.buildScratchSize + align,
                                   VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, false);
    const VkDeviceAddress scratch_base = buffer_address(ctx.device, scratch.buffer);
    const VkDeviceAddress scratch_aligned = (scratch_base + align - 1) / align * align;

    build.dstAccelerationStructure = accel.handle;
    build.scratchData.deviceAddress = scratch_aligned;

    VkAccelerationStructureBuildRangeInfoKHR range = {};
    range.primitiveCount = primitive_count;
    const VkAccelerationStructureBuildRangeInfoKHR* ranges = &range;

    VkCommandBuffer cmd = begin_commands(ctx);
    ctx.as.cmd_build(cmd, 1, &build, &ranges);
    run_commands(ctx, cmd);
    vkFreeCommandBuffers(ctx.device, ctx.pool, 1, &cmd);
    destroy_buffer(ctx.device, scratch);

    VkAccelerationStructureDeviceAddressInfoKHR ai = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_DEVICE_ADDRESS_INFO_KHR };
    ai.accelerationStructure = accel.handle;
    accel.address = ctx.as.address(ctx.device, &ai);
    return accel;
}

static Accel build_triangle_blas(const Context& ctx, const std::vector<float>& positions, const std::vector<uint32_t>& indices,
                                 Buffer& vertex_buffer, Buffer& index_buffer)
{
    const VkBufferUsageFlags input_usage =
        VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    vertex_buffer = create_buffer(ctx.phys, ctx.device, positions.size() * sizeof(float), input_usage, true);
    index_buffer = create_buffer(ctx.phys, ctx.device, indices.size() * sizeof(uint32_t), input_usage, true);
    std::memcpy(vertex_buffer.mapped, positions.data(), positions.size() * sizeof(float));
    std::memcpy(index_buffer.mapped, indices.data(), indices.size() * sizeof(uint32_t));

    VkAccelerationStructureGeometryKHR geometry = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
    geometry.geometryType = VK_GEOMETRY_TYPE_TRIANGLES_KHR;
    geometry.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
    geometry.geometry.triangles.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_TRIANGLES_DATA_KHR;
    geometry.geometry.triangles.vertexFormat = VK_FORMAT_R32G32B32_SFLOAT;
    geometry.geometry.triangles.vertexData.deviceAddress = buffer_address(ctx.device, vertex_buffer.buffer);
    geometry.geometry.triangles.vertexStride = 3 * sizeof(float);
    geometry.geometry.triangles.maxVertex = uint32_t(positions.size() / 3) - 1;
    geometry.geometry.triangles.indexType = VK_INDEX_TYPE_UINT32;
    geometry.geometry.triangles.indexData.deviceAddress = buffer_address(ctx.device, index_buffer.buffer);
    return build_accel(ctx, VK_ACCELERATION_STRUCTURE_TYPE_BOTTOM_LEVEL_KHR, geometry, uint32_t(indices.size() / 3));
}

static void destroy_accel(const Context& ctx, Accel& a)
{
    if (a.handle)
        ctx.as.destroy(ctx.device, a.handle, nullptr);
    destroy_buffer(ctx.device, a.storage);
    a = Accel();
}

static bool has_extension(const std::vector<VkExtensionProperties>& list, const char* name)
{
    for (const VkExtensionProperties& e : list)
        if (!std::strcmp(e.extensionName, name))
            return true;
    return false;
}

// ------------------------------------------------------------------------------------------

int main(int argc, char** argv)
{
    const char* spv_path = nullptr;
    const char* config_path = nullptr;
    std::string out_dir = ".";
    uint32_t seed = 1;
    bool want_validation = true;
    for (int i = 1; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--spv") && i + 1 < argc) spv_path = argv[++i];
        else if (!std::strcmp(argv[i], "--config") && i + 1 < argc) config_path = argv[++i];
        else if (!std::strcmp(argv[i], "--out-dir") && i + 1 < argc) out_dir = argv[++i];
        else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) seed = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--no-validation")) want_validation = false;
    }
    if (!spv_path || !config_path)
    {
        std::fprintf(stdout, "usage: rayq_host --spv rayq.spv --config config.json --seed N --out-dir DIR [--no-validation]\n");
        return 15;
    }

    // ---- config ---------------------------------------------------------------------------
    const std::string config = read_text_file(config_path);
    if (config.empty())
        fail("cannot read the config file");
    const std::vector<double> c_camera = json_numbers(config, "camera");
    const std::vector<double> c_dirs = json_numbers(config, "directions");
    const std::vector<double> c_occluder = json_numbers(config, "occluder");
    const std::vector<double> c_light = json_numbers(config, "light");
    const std::vector<double> c_probes = json_numbers(config, "probes");
    const std::vector<double> c_rho = json_numbers(config, "rho");
    const std::vector<double> c_emission = json_numbers(config, "E");
    const std::vector<double> c_budget = json_numbers(config, "budget");
    const std::vector<double> c_samples = json_numbers(config, "samples");
    if (c_camera.size() != 3 || c_light.size() != 3 || c_occluder.size() != 9 || c_dirs.empty() || c_dirs.size() % 3 != 0 ||
        c_probes.empty() || c_probes.size() % 3 != 0 || c_rho.size() != 1 || c_emission.size() != 1 || c_budget.size() != 1 ||
        c_samples.size() != 1)
        fail("config.json does not have the expected shape");
    const uint32_t n_dirs = uint32_t(c_dirs.size() / 3);
    const uint32_t n_probes = uint32_t(c_probes.size() / 3);
    if (n_dirs > MAX_DIRS || n_probes > MAX_PROBES)
        fail("config.json has more directions or probes than the shader supports");
    const double rho = c_rho[0];
    const double emission = c_emission[0];
    const uint32_t budget = uint32_t(c_budget[0]);
    const uint32_t samples = uint32_t(c_samples[0]);

    // Two interior points for the furnace; any interior point must see the same radiance.
    const float furnace_points[2][3] = { { 0.31f, 0.42f, 0.57f }, { 0.68f, 0.59f, 0.36f } };
    const uint32_t n_furn = 2;

    // ---- shader ---------------------------------------------------------------------------
    std::vector<uint32_t> spirv;
    {
        std::ifstream f(spv_path, std::ios::binary | std::ios::ate);
        if (!f)
            fail("cannot open the SPIR-V file");
        const std::streamsize bytes = f.tellg();
        if (bytes <= 0 || bytes % 4 != 0)
            fail("the SPIR-V file is empty or truncated");
        spirv.resize(size_t(bytes) / 4);
        f.seekg(0);
        f.read(reinterpret_cast<char*>(spirv.data()), bytes);
    }

    // ---- instance -------------------------------------------------------------------------
    uint32_t layer_count = 0;
    vkEnumerateInstanceLayerProperties(&layer_count, nullptr);
    std::vector<VkLayerProperties> layers(layer_count);
    vkEnumerateInstanceLayerProperties(&layer_count, layers.data());
    bool have_validation = false;
    for (const VkLayerProperties& l : layers)
        if (!std::strcmp(l.layerName, "VK_LAYER_KHRONOS_validation"))
            have_validation = true;
    const bool use_validation = want_validation && have_validation;

    VkApplicationInfo app = { VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.pApplicationName = "rayq_host";
    app.apiVersion = VK_API_VERSION_1_2;

    const char* validation_layer = "VK_LAYER_KHRONOS_validation";
    const char* debug_extension = VK_EXT_DEBUG_UTILS_EXTENSION_NAME;
    VkDebugUtilsMessengerCreateInfoEXT dbg = { VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT };
    dbg.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT;
    dbg.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT;
    dbg.pfnUserCallback = on_debug;

    VkInstanceCreateInfo ici = { VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ici.pApplicationInfo = &app;
    if (use_validation)
    {
        ici.enabledLayerCount = 1;
        ici.ppEnabledLayerNames = &validation_layer;
        ici.enabledExtensionCount = 1;
        ici.ppEnabledExtensionNames = &debug_extension;
        ici.pNext = &dbg;
    }
    VkInstance instance = VK_NULL_HANDLE;
    CHECK_VK(vkCreateInstance(&ici, nullptr, &instance));

    VkDebugUtilsMessengerEXT messenger = VK_NULL_HANDLE;
    if (use_validation)
    {
        const auto create_messenger =
            reinterpret_cast<PFN_vkCreateDebugUtilsMessengerEXT>(vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT"));
        if (create_messenger)
            CHECK_VK(create_messenger(instance, &dbg, nullptr, &messenger));
    }

    // ---- physical device: needs ray queries; prefer a discrete GPU; never hardcode an index ----
    const char* required_extensions[] = { VK_KHR_ACCELERATION_STRUCTURE_EXTENSION_NAME, VK_KHR_RAY_QUERY_EXTENSION_NAME,
                                          VK_KHR_DEFERRED_HOST_OPERATIONS_EXTENSION_NAME };
    uint32_t phys_count = 0;
    vkEnumeratePhysicalDevices(instance, &phys_count, nullptr);
    std::vector<VkPhysicalDevice> physicals(phys_count);
    vkEnumeratePhysicalDevices(instance, &phys_count, physicals.data());
    if (physicals.empty())
        fail("no Vulkan device found");

    Context ctx;
    VkPhysicalDeviceProperties phys_props = {};
    bool chosen_has_ray_query = false;
    int best_rank = -1;
    for (VkPhysicalDevice candidate : physicals)
    {
        VkPhysicalDeviceProperties props;
        vkGetPhysicalDeviceProperties(candidate, &props);
        uint32_t ext_count = 0;
        vkEnumerateDeviceExtensionProperties(candidate, nullptr, &ext_count, nullptr);
        std::vector<VkExtensionProperties> exts(ext_count);
        vkEnumerateDeviceExtensionProperties(candidate, nullptr, &ext_count, exts.data());
        bool all = props.apiVersion >= VK_API_VERSION_1_2;
        for (const char* name : required_extensions)
            all = all && has_extension(exts, name);

        bool ray_query = false;
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
            ray_query = rq.rayQuery && asf.accelerationStructure && v12.bufferDeviceAddress;
        }
        const int rank = (ray_query ? 2 : 0) + (props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU ? 1 : 0);
        if (rank > best_rank)
        {
            best_rank = rank;
            ctx.phys = candidate;
            phys_props = props;
            chosen_has_ray_query = ray_query;
        }
    }

    std::printf("device: %s (type %d) | validation layer: %s\n", phys_props.deviceName, int(phys_props.deviceType),
                use_validation ? "on" : (have_validation ? "off" : "not installed"));
    if (!chosen_has_ray_query)
    {
        std::printf("rayQuery: not supported\n");
        fail("the selected device does not support acceleration structures and ray queries");
    }
    std::printf("rayQuery: supported\n");

    VkPhysicalDeviceAccelerationStructurePropertiesKHR as_props = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_PROPERTIES_KHR };
    VkPhysicalDeviceProperties2 props2 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2 };
    props2.pNext = &as_props;
    vkGetPhysicalDeviceProperties2(ctx.phys, &props2);
    ctx.scratch_alignment = as_props.minAccelerationStructureScratchOffsetAlignment;

    // ---- queue family with compute ----------------------------------------------------------
    uint32_t family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(ctx.phys, &family_count, nullptr);
    std::vector<VkQueueFamilyProperties> families(family_count);
    vkGetPhysicalDeviceQueueFamilyProperties(ctx.phys, &family_count, families.data());
    uint32_t family = UINT32_MAX;
    for (uint32_t i = 0; i < family_count; ++i)
        if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT)
        {
            family = i;
            break;
        }
    if (family == UINT32_MAX)
        fail("no compute queue family");

    // ---- logical device ---------------------------------------------------------------------
    VkPhysicalDeviceRayQueryFeaturesKHR enable_rq = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAY_QUERY_FEATURES_KHR };
    enable_rq.rayQuery = VK_TRUE;
    VkPhysicalDeviceAccelerationStructureFeaturesKHR enable_as = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_ACCELERATION_STRUCTURE_FEATURES_KHR };
    enable_as.accelerationStructure = VK_TRUE;
    enable_as.pNext = &enable_rq;
    VkPhysicalDeviceVulkan12Features enable_12 = { VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES };
    enable_12.bufferDeviceAddress = VK_TRUE;
    enable_12.pNext = &enable_as;

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qi = { VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.queueFamilyIndex = family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci = { VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qi;
    dci.enabledExtensionCount = uint32_t(sizeof(required_extensions) / sizeof(required_extensions[0]));
    dci.ppEnabledExtensionNames = required_extensions;
    dci.pNext = &enable_12;
    CHECK_VK(vkCreateDevice(ctx.phys, &dci, nullptr, &ctx.device));
    vkGetDeviceQueue(ctx.device, family, 0, &ctx.queue);

    ctx.as.create = reinterpret_cast<PFN_vkCreateAccelerationStructureKHR>(vkGetDeviceProcAddr(ctx.device, "vkCreateAccelerationStructureKHR"));
    ctx.as.destroy = reinterpret_cast<PFN_vkDestroyAccelerationStructureKHR>(vkGetDeviceProcAddr(ctx.device, "vkDestroyAccelerationStructureKHR"));
    ctx.as.build_sizes =
        reinterpret_cast<PFN_vkGetAccelerationStructureBuildSizesKHR>(vkGetDeviceProcAddr(ctx.device, "vkGetAccelerationStructureBuildSizesKHR"));
    ctx.as.cmd_build =
        reinterpret_cast<PFN_vkCmdBuildAccelerationStructuresKHR>(vkGetDeviceProcAddr(ctx.device, "vkCmdBuildAccelerationStructuresKHR"));
    ctx.as.address = reinterpret_cast<PFN_vkGetAccelerationStructureDeviceAddressKHR>(
        vkGetDeviceProcAddr(ctx.device, "vkGetAccelerationStructureDeviceAddressKHR"));
    if (!ctx.as.create || !ctx.as.destroy || !ctx.as.build_sizes || !ctx.as.cmd_build || !ctx.as.address)
        fail("acceleration-structure entry points are missing from the device");

    VkCommandPoolCreateInfo cpci = { VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = family;
    cpci.flags = VK_COMMAND_POOL_CREATE_RESET_COMMAND_BUFFER_BIT;
    CHECK_VK(vkCreateCommandPool(ctx.device, &cpci, nullptr, &ctx.pool));

    // ---- geometry -----------------------------------------------------------------------------
    const auto t_build = std::chrono::steady_clock::now();

    // Unit cube [0,1]^3: 8 corners, 12 triangles. Winding is irrelevant: culling is disabled.
    const std::vector<float> cube_positions = { 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0, 1, 1 };
    const std::vector<uint32_t> cube_indices = { 0, 1, 2, 0, 2, 3,    // z = 0
                                                 4, 6, 5, 4, 7, 6,    // z = 1
                                                 0, 5, 1, 0, 4, 5,    // y = 0
                                                 3, 2, 6, 3, 6, 7,    // y = 1
                                                 0, 3, 7, 0, 7, 4,    // x = 0
                                                 1, 5, 6, 1, 6, 2 };  // x = 1
    std::vector<float> occluder_positions(9);
    for (int i = 0; i < 9; ++i)
        occluder_positions[size_t(i)] = float(c_occluder[size_t(i)]);
    const std::vector<uint32_t> occluder_indices = { 0, 1, 2 };

    Buffer cube_vb, cube_ib, occ_vb, occ_ib;
    Accel cube_blas = build_triangle_blas(ctx, cube_positions, cube_indices, cube_vb, cube_ib);
    Accel occluder_blas = build_triangle_blas(ctx, occluder_positions, occluder_indices, occ_vb, occ_ib);

    // Two instances, identity transforms, distinct visibility masks.
    VkAccelerationStructureInstanceKHR instances[2] = {};
    const Accel* blases[2] = { &cube_blas, &occluder_blas };
    const uint32_t masks[2] = { 0x1u, 0x2u };
    for (int i = 0; i < 2; ++i)
    {
        instances[i].transform.matrix[0][0] = 1.0f;
        instances[i].transform.matrix[1][1] = 1.0f;
        instances[i].transform.matrix[2][2] = 1.0f;
        instances[i].instanceCustomIndex = uint32_t(i);
        instances[i].mask = masks[i];
        instances[i].instanceShaderBindingTableRecordOffset = 0;
        instances[i].flags = VK_GEOMETRY_INSTANCE_TRIANGLE_FACING_CULL_DISABLE_BIT_KHR;
        instances[i].accelerationStructureReference = blases[i]->address;
    }
    Buffer instance_buffer = create_buffer(
        ctx.phys, ctx.device, sizeof(instances),
        VK_BUFFER_USAGE_ACCELERATION_STRUCTURE_BUILD_INPUT_READ_ONLY_BIT_KHR | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT, true);
    std::memcpy(instance_buffer.mapped, instances, sizeof(instances));

    VkAccelerationStructureGeometryKHR tlas_geometry = { VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_KHR };
    tlas_geometry.geometryType = VK_GEOMETRY_TYPE_INSTANCES_KHR;
    tlas_geometry.flags = VK_GEOMETRY_OPAQUE_BIT_KHR;
    tlas_geometry.geometry.instances.sType = VK_STRUCTURE_TYPE_ACCELERATION_STRUCTURE_GEOMETRY_INSTANCES_DATA_KHR;
    tlas_geometry.geometry.instances.arrayOfPointers = VK_FALSE;
    tlas_geometry.geometry.instances.data.deviceAddress = buffer_address(ctx.device, instance_buffer.buffer);
    Accel tlas = build_accel(ctx, VK_ACCELERATION_STRUCTURE_TYPE_TOP_LEVEL_KHR, tlas_geometry, 2);
    const double build_seconds = seconds_since(t_build);

    // ---- scene and result buffers -------------------------------------------------------------
    const uint32_t total = n_dirs + n_probes + n_furn * samples;
    Buffer scene_buffer = create_buffer(ctx.phys, ctx.device, sizeof(SceneGpu), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true);
    Buffer result_buffer = create_buffer(ctx.phys, ctx.device, VkDeviceSize(total) * sizeof(float), VK_BUFFER_USAGE_STORAGE_BUFFER_BIT, true);

    SceneGpu scene = {};
    scene.camera = { float(c_camera[0]), float(c_camera[1]), float(c_camera[2]), 0.0f };
    scene.light = { float(c_light[0]), float(c_light[1]), float(c_light[2]), 0.0f };
    scene.params = { float(rho), float(emission), 0.0f, 0.0f };
    scene.counts[0] = n_dirs;
    scene.counts[1] = n_probes;
    scene.counts[2] = n_furn;
    scene.counts[3] = samples;
    scene.misc[0] = budget;
    scene.misc[1] = seed;
    for (uint32_t i = 0; i < n_dirs; ++i)
        scene.dirs[i] = { float(c_dirs[3 * i]), float(c_dirs[3 * i + 1]), float(c_dirs[3 * i + 2]), 0.0f };
    for (uint32_t i = 0; i < n_probes; ++i)
        scene.probes[i] = { float(c_probes[3 * i]), float(c_probes[3 * i + 1]), float(c_probes[3 * i + 2]), 0.0f };
    for (uint32_t i = 0; i < n_furn; ++i)
        scene.furn[i] = { furnace_points[i][0], furnace_points[i][1], furnace_points[i][2], 0.0f };
    std::memcpy(scene_buffer.mapped, &scene, sizeof(scene));
    std::memset(result_buffer.mapped, 0, size_t(total) * sizeof(float));

    // ---- pipeline -----------------------------------------------------------------------------
    VkDescriptorSetLayoutBinding bindings[3] = {};
    bindings[0].binding = 0;
    bindings[0].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    bindings[0].descriptorCount = 1;
    bindings[0].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    bindings[1].binding = 1;
    bindings[1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    bindings[1].descriptorCount = 1;
    bindings[1].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    bindings[2].binding = 2;
    bindings[2].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    bindings[2].descriptorCount = 1;
    bindings[2].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    VkDescriptorSetLayoutCreateInfo dslci = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    dslci.bindingCount = 3;
    dslci.pBindings = bindings;
    VkDescriptorSetLayout set_layout = VK_NULL_HANDLE;
    CHECK_VK(vkCreateDescriptorSetLayout(ctx.device, &dslci, nullptr, &set_layout));

    VkPipelineLayoutCreateInfo plci = { VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    plci.setLayoutCount = 1;
    plci.pSetLayouts = &set_layout;
    VkPipelineLayout pipeline_layout = VK_NULL_HANDLE;
    CHECK_VK(vkCreatePipelineLayout(ctx.device, &plci, nullptr, &pipeline_layout));

    VkShaderModuleCreateInfo smci = { VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smci.codeSize = spirv.size() * sizeof(uint32_t);
    smci.pCode = spirv.data();
    VkShaderModule shader = VK_NULL_HANDLE;
    CHECK_VK(vkCreateShaderModule(ctx.device, &smci, nullptr, &shader));

    VkComputePipelineCreateInfo pci = { VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    pci.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    pci.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    pci.stage.module = shader;
    pci.stage.pName = "main";
    pci.layout = pipeline_layout;
    VkPipeline pipeline = VK_NULL_HANDLE;
    CHECK_VK(vkCreateComputePipelines(ctx.device, VK_NULL_HANDLE, 1, &pci, nullptr, &pipeline));

    VkDescriptorPoolSize pool_sizes[2] = { { VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR, 1 }, { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2 } };
    VkDescriptorPoolCreateInfo dpci = { VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    dpci.maxSets = 1;
    dpci.poolSizeCount = 2;
    dpci.pPoolSizes = pool_sizes;
    VkDescriptorPool descriptor_pool = VK_NULL_HANDLE;
    CHECK_VK(vkCreateDescriptorPool(ctx.device, &dpci, nullptr, &descriptor_pool));

    VkDescriptorSetAllocateInfo dsai = { VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    dsai.descriptorPool = descriptor_pool;
    dsai.descriptorSetCount = 1;
    dsai.pSetLayouts = &set_layout;
    VkDescriptorSet descriptor_set = VK_NULL_HANDLE;
    CHECK_VK(vkAllocateDescriptorSets(ctx.device, &dsai, &descriptor_set));

    VkWriteDescriptorSetAccelerationStructureKHR as_write = { VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET_ACCELERATION_STRUCTURE_KHR };
    as_write.accelerationStructureCount = 1;
    as_write.pAccelerationStructures = &tlas.handle;
    VkDescriptorBufferInfo scene_info = { scene_buffer.buffer, 0, VK_WHOLE_SIZE };
    VkDescriptorBufferInfo result_info = { result_buffer.buffer, 0, VK_WHOLE_SIZE };
    VkWriteDescriptorSet writes[3] = {};
    writes[0].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[0].dstSet = descriptor_set;
    writes[0].dstBinding = 0;
    writes[0].descriptorCount = 1;
    writes[0].descriptorType = VK_DESCRIPTOR_TYPE_ACCELERATION_STRUCTURE_KHR;
    writes[0].pNext = &as_write;
    writes[1].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[1].dstSet = descriptor_set;
    writes[1].dstBinding = 1;
    writes[1].descriptorCount = 1;
    writes[1].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    writes[1].pBufferInfo = &scene_info;
    writes[2].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
    writes[2].dstSet = descriptor_set;
    writes[2].dstBinding = 2;
    writes[2].descriptorCount = 1;
    writes[2].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
    writes[2].pBufferInfo = &result_info;
    vkUpdateDescriptorSets(ctx.device, 3, writes, 0, nullptr);

    // ---- dispatch -----------------------------------------------------------------------------
    const auto t_dispatch = std::chrono::steady_clock::now();
    VkCommandBuffer cmd = begin_commands(ctx);
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &descriptor_set, 0, nullptr);
    vkCmdDispatch(cmd, (total + 63) / 64, 1, 1);
    run_commands(ctx, cmd);
    vkFreeCommandBuffers(ctx.device, ctx.pool, 1, &cmd);
    const double dispatch_seconds = seconds_since(t_dispatch);

    std::vector<float> values(total);
    std::memcpy(values.data(), result_buffer.mapped, size_t(total) * sizeof(float));

    // ---- outputs --------------------------------------------------------------------------------
    std::printf("seed %u: %u hit rays, %u shadow probes, %u furnace probes x %u paths, budget %u | accel build %.3fs | dispatch %.3fs\n", seed,
                n_dirs, n_probes, n_furn, samples, budget, build_seconds, dispatch_seconds);

    bool wrote_all = true;
    {
        const std::string path = out_dir + "/hits.csv";
        std::FILE* f = std::fopen(path.c_str(), "w");
        if (f)
        {
            std::fprintf(f, "dx,dy,dz,distance\n");
            for (uint32_t i = 0; i < n_dirs; ++i)
                std::fprintf(f, "%.8f,%.8f,%.8f,%.7f\n", c_dirs[3 * i], c_dirs[3 * i + 1], c_dirs[3 * i + 2], double(values[i]));
            std::fclose(f);
        }
        else
            wrote_all = false;
    }
    uint32_t blocked = 0;
    {
        const std::string path = out_dir + "/occlusion.csv";
        std::FILE* f = std::fopen(path.c_str(), "w");
        if (f)
        {
            std::fprintf(f, "px,py,pz,blocked\n");
            for (uint32_t i = 0; i < n_probes; ++i)
            {
                const int b = values[n_dirs + i] > 0.5f ? 1 : 0;
                blocked += uint32_t(b);
                std::fprintf(f, "%.6f,%.6f,%.6f,%d\n", c_probes[3 * i], c_probes[3 * i + 1], c_probes[3 * i + 2], b);
            }
            std::fclose(f);
        }
        else
            wrote_all = false;
    }
    {
        const std::string path = out_dir + "/furnace.csv";
        std::FILE* f = std::fopen(path.c_str(), "w");
        if (f)
        {
            std::fprintf(f, "probe,radiance,std_error,radiance_infinite\n");
            const double tail = 1.0 - std::pow(rho, double(budget) + 1.0);
            for (uint32_t p = 0; p < n_furn; ++p)
            {
                double sum = 0.0, sum_sq = 0.0;
                const float* v = values.data() + n_dirs + n_probes + size_t(p) * samples;
                for (uint32_t s = 0; s < samples; ++s)
                {
                    sum += double(v[s]);
                    sum_sq += double(v[s]) * double(v[s]);
                }
                const double mean = sum / samples;
                const double variance = samples > 1 ? std::fmax(0.0, (sum_sq - samples * mean * mean) / (samples - 1)) : 0.0;
                const double std_error = std::sqrt(variance / samples);
                std::fprintf(f, "%u,%.7f,%.7f,%.7f\n", p, mean, std_error, mean / tail);
                std::printf("furnace probe %u: radiance %.5f +- %.5f | extrapolated %.5f | E/(1-rho) is the judge's business\n", p, mean, std_error,
                            mean / tail);
            }
            std::fclose(f);
        }
        else
            wrote_all = false;
    }
    std::printf("hits: first distance %.6f | occlusion: %u of %u probes blocked\n", n_dirs ? double(values[0]) : -1.0, blocked, n_probes);

    // ---- teardown -------------------------------------------------------------------------------
    vkDestroyPipeline(ctx.device, pipeline, nullptr);
    vkDestroyShaderModule(ctx.device, shader, nullptr);
    vkDestroyPipelineLayout(ctx.device, pipeline_layout, nullptr);
    vkDestroyDescriptorPool(ctx.device, descriptor_pool, nullptr);
    vkDestroyDescriptorSetLayout(ctx.device, set_layout, nullptr);
    destroy_buffer(ctx.device, scene_buffer);
    destroy_buffer(ctx.device, result_buffer);
    destroy_accel(ctx, tlas);
    destroy_buffer(ctx.device, instance_buffer);
    destroy_accel(ctx, cube_blas);
    destroy_accel(ctx, occluder_blas);
    destroy_buffer(ctx.device, cube_vb);
    destroy_buffer(ctx.device, cube_ib);
    destroy_buffer(ctx.device, occ_vb);
    destroy_buffer(ctx.device, occ_ib);
    vkDestroyCommandPool(ctx.device, ctx.pool, nullptr);
    vkDestroyDevice(ctx.device, nullptr);
    if (messenger)
    {
        const auto destroy_messenger =
            reinterpret_cast<PFN_vkDestroyDebugUtilsMessengerEXT>(vkGetInstanceProcAddr(instance, "vkDestroyDebugUtilsMessengerEXT"));
        if (destroy_messenger)
            destroy_messenger(instance, messenger, nullptr);
    }
    vkDestroyInstance(instance, nullptr);

    std::printf("validation: %d errors, %d warnings\n", g_validation_errors, g_validation_warnings);
    if (!wrote_all)
        fail("could not write one of the output files");
    if (g_validation_errors > 0)
        return 14;
    return 0;
}
