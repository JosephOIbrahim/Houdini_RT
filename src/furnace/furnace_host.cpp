// GPU white furnace for Adobe's OpenPBR BSDF: Vulkan compute host. First-light step 1.
//
// Runs furnace.comp over the same 72 material rows as the CPU exam (white_furnace.cpp), judges
// each row with the same tolerances, and, when given the CPU reference CSV, checks that the GPU
// and CPU agree row by row. Raw Vulkan, no helper libraries, no window, no ray queries.
//
// Build (OPENPBR is a checkout of https://github.com/adobe/openpbr-bsdf):
//   glslangValidator -V --target-env vulkan1.2 -S comp -DFURNACE_TEX_LUTS -I%OPENPBR% furnace.comp -o furnace_tex.spv
//   cl /std:c++17 /O2 /EHsc /I%VULKAN_SDK%\Include /I%OPENPBR% furnace_host.cpp /link /LIBPATH:%VULKAN_SDK%\Lib vulkan-1.lib   (MSVC)
//   g++ -std=c++17 -O2 -I$OPENPBR furnace_host.cpp -lvulkan -o furnace_host                                                  (Linux)
//
// Run (texture-LUT mode, the production mode):
//   furnace_host --spv furnace_tex.spv --tex-luts [--csv gpu_rows.csv] [--ref furnace_cpu_reference.csv]
//                [--seed N] [--batches 256] [--samples 256] [--no-validation]
//   python judge_furnace.py gpu_rows.csv furnace_cpu_reference.csv        (the independent judge)
// Array-LUT mode: compile the shader without -DFURNACE_TEX_LUTS and drop --tex-luts. On Mesa's
// software driver that mode ran out of memory (over 6 GB) while compiling the pipeline.
//
// Exit codes follow the FRAGMENT harness stage map:
//   0 pass | 14 validation-layer errors | 15 a Vulkan call failed | 16 a row is out of tolerance

#include <vulkan/vulkan.h>

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

struct Row
{
    float metalness, base_gray, specular_weight, coat, fuzz, roughness, cos_view;
    uint32_t seed;
};
struct Partial
{
    float sums[4];
    float stats[4];
};
static_assert(sizeof(Row) == 32 && sizeof(Partial) == 32, "must match the std430 layout in furnace.comp");

enum Kind { White, Lambert, Control };
struct Config
{
    const char* name;
    Kind kind;
    float metalness, base_gray, specular_weight, coat, fuzz;
};
// Same rows, same order, as white_furnace.cpp.
static const Config kConfigs[] = {
    { "white_dielectric", White, 0.0f, 1.0f, 1.0f, 0.0f, 0.0f },
    { "white_metal", White, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f },
    { "white_dielectric_coat", White, 0.0f, 1.0f, 1.0f, 1.0f, 0.0f },
    { "white_dielectric_fuzz", White, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f },
    { "lambert_grey_no_specular", Lambert, 0.0f, 0.5f, 0.0f, 0.0f, 0.0f },
    { "grey_dielectric_control", Control, 0.0f, 0.5f, 1.0f, 0.0f, 0.0f },
};
static const float kRoughnesses[] = { 0.1f, 0.5f, 1.0f };
static const float kCosViews[] = { 1.0f, 0.7f, 0.3f, 0.1f };

static int g_validation_errors = 0;
static int g_validation_warnings = 0;

static VKAPI_ATTR VkBool32 VKAPI_CALL on_debug(VkDebugUtilsMessageSeverityFlagBitsEXT severity, VkDebugUtilsMessageTypeFlagsEXT,
                                               const VkDebugUtilsMessengerCallbackDataEXT* data, void*)
{
    if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT)
    {
        ++g_validation_errors;
        std::fprintf(stderr, "VALIDATION ERROR: %s\n", data->pMessage);
    }
    else if (severity & VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT)
    {
        ++g_validation_warnings;
        std::fprintf(stderr, "validation warning: %s\n", data->pMessage);
    }
    return VK_FALSE;
}

#define VK_CHECK(call)                                                                       \
    do                                                                                       \
    {                                                                                        \
        const VkResult vk_check_result = (call);                                             \
        if (vk_check_result != VK_SUCCESS)                                                   \
        {                                                                                    \
            std::fprintf(stderr, "Vulkan call failed (%d): %s\n", vk_check_result, #call);   \
            std::exit(15);                                                                   \
        }                                                                                    \
    } while (0)

static double seconds_since(const std::chrono::steady_clock::time_point& t0)
{
    return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
}

struct Buffer
{
    VkBuffer buffer = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    void* mapped = nullptr;
};

static Buffer create_host_buffer(VkPhysicalDevice phys, VkDevice device, VkDeviceSize size, VkBufferUsageFlags usage)
{
    Buffer out;
    VkBufferCreateInfo bi{ VK_STRUCTURE_TYPE_BUFFER_CREATE_INFO };
    bi.size = size;
    bi.usage = usage;
    bi.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    VK_CHECK(vkCreateBuffer(device, &bi, nullptr, &out.buffer));

    VkMemoryRequirements req;
    vkGetBufferMemoryRequirements(device, out.buffer, &req);
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(phys, &props);
    const VkMemoryPropertyFlags want = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
    uint32_t type_index = UINT32_MAX;
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
        if ((req.memoryTypeBits & (1u << i)) && (props.memoryTypes[i].propertyFlags & want) == want)
        {
            type_index = i;
            break;
        }
    if (type_index == UINT32_MAX)
    {
        std::fprintf(stderr, "no host-visible memory type\n");
        std::exit(15);
    }
    VkMemoryAllocateInfo ai{ VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = type_index;
    VK_CHECK(vkAllocateMemory(device, &ai, nullptr, &out.memory));
    VK_CHECK(vkBindBufferMemory(device, out.buffer, out.memory, 0));
    VK_CHECK(vkMapMemory(device, out.memory, 0, size, 0, &out.mapped));
    return out;
}

// ---- lookup-table textures (texture-LUT mode) ---------------------------------------------
// The eight tables ship with the BSDF as flattened C arrays. Uploading each flat array as-is
// matches the BSDF's texture-mode coordinates: it samples 2D tables at (y, x) and 3D tables at
// (z, y, x), and the arrays are stored with the rightmost index contiguous.
static const uint16_t kLutIdealDielectricEnergy[32 * 32 * 32] = {
#include "impl/data/openpbr_ideal_dielectric_energy_complement_data.h"
};
static const uint16_t kLutIdealDielectricAvgEnergy[32 * 32] = {
#include "impl/data/openpbr_ideal_dielectric_avg_energy_complement_data.h"
};
static const uint16_t kLutIdealDielectricReflectionRatio[32 * 32] = {
#include "impl/data/openpbr_ideal_dielectric_reflection_ratio_data.h"
};
static const uint16_t kLutOpaqueDielectricEnergy[32 * 32 * 32] = {
#include "impl/data/openpbr_opaque_dielectric_energy_complement_data.h"
};
static const uint16_t kLutOpaqueDielectricAvgEnergy[32 * 32] = {
#include "impl/data/openpbr_opaque_dielectric_avg_energy_complement_data.h"
};
static const uint16_t kLutIdealMetalEnergy[32 * 32] = {
#include "impl/data/openpbr_ideal_metal_energy_complement_data.h"
};
static const uint16_t kLutIdealMetalAvgEnergy[32] = {
#include "impl/data/openpbr_ideal_metal_avg_energy_complement_data.h"
};
struct vec3
{
    float x, y, z;
    constexpr vec3(float a, float b, float c) : x(a), y(b), z(c) {}
};
static const vec3 kLutLtc[32 * 32] = {
#include "impl/data/openpbr_ltc_data.h"
};

struct Texture
{
    VkImage image = VK_NULL_HANDLE;
    VkDeviceMemory memory = VK_NULL_HANDLE;
    VkImageView view = VK_NULL_HANDLE;
};

static uint32_t find_memory_type(VkPhysicalDevice phys, uint32_t bits, VkMemoryPropertyFlags want)
{
    VkPhysicalDeviceMemoryProperties props;
    vkGetPhysicalDeviceMemoryProperties(phys, &props);
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)
        if ((bits & (1u << i)) && (props.memoryTypes[i].propertyFlags & want) == want)
            return i;
    for (uint32_t i = 0; i < props.memoryTypeCount; ++i)  // fall back to any allowed type
        if (bits & (1u << i))
            return i;
    std::fprintf(stderr, "no usable memory type\n");
    std::exit(15);
}

static bool format_filters_linearly(VkPhysicalDevice phys, VkFormat format)
{
    VkFormatProperties props;
    vkGetPhysicalDeviceFormatProperties(phys, format, &props);
    const VkFormatFeatureFlags want = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT | VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT;
    return (props.optimalTilingFeatures & want) == want;
}

// Creates a sampled 2D or 3D texture and fills it from `data` through a staging buffer.
static Texture create_texture(VkPhysicalDevice phys, VkDevice device, VkCommandPool pool, VkQueue queue, VkFormat format, uint32_t width,
                              uint32_t height, uint32_t depth, const void* data, size_t bytes);

struct RefRow
{
    double albedo, std_error;
};

static std::map<std::string, RefRow> load_reference(const char* path)
{
    std::map<std::string, RefRow> out;
    std::ifstream f(path);
    std::string line;
    std::getline(f, line);  // header
    while (std::getline(f, line))
    {
        std::vector<std::string> c;
        std::stringstream ss(line);
        for (std::string cell; std::getline(ss, cell, ',');)
            c.push_back(cell);
        if (c.size() < 11)
            continue;
        // config,kind,metalness,base_gray,specular_weight,coat,fuzz,roughness,cos_view,albedo,std_error,...
        out[c[0] + "|" + c[7] + "|" + c[8]] = { std::atof(c[9].c_str()), std::atof(c[10].c_str()) };
    }
    return out;
}

static Texture create_texture(VkPhysicalDevice phys, VkDevice device, VkCommandPool pool, VkQueue queue, VkFormat format, uint32_t width,
                              uint32_t height, uint32_t depth, const void* data, size_t bytes)
{
    Buffer staging = create_host_buffer(phys, device, bytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT);
    std::memcpy(staging.mapped, data, bytes);

    Texture out;
    VkImageCreateInfo ii{ VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO };
    ii.imageType = depth > 1 ? VK_IMAGE_TYPE_3D : VK_IMAGE_TYPE_2D;
    ii.format = format;
    ii.extent = { width, height, depth };
    ii.mipLevels = 1;
    ii.arrayLayers = 1;
    ii.samples = VK_SAMPLE_COUNT_1_BIT;
    ii.tiling = VK_IMAGE_TILING_OPTIMAL;
    ii.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    ii.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    ii.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    VK_CHECK(vkCreateImage(device, &ii, nullptr, &out.image));

    VkMemoryRequirements req;
    vkGetImageMemoryRequirements(device, out.image, &req);
    VkMemoryAllocateInfo ai{ VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO };
    ai.allocationSize = req.size;
    ai.memoryTypeIndex = find_memory_type(phys, req.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    VK_CHECK(vkAllocateMemory(device, &ai, nullptr, &out.memory));
    VK_CHECK(vkBindImageMemory(device, out.image, out.memory, 0));

    VkCommandBufferAllocateInfo cbai{ VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cbai.commandPool = pool;
    cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    VK_CHECK(vkAllocateCommandBuffers(device, &cbai, &cmd));
    VkCommandBufferBeginInfo cbi{ VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    cbi.flags = VK_COMMAND_BUFFER_USAGE_ONE_TIME_SUBMIT_BIT;
    VK_CHECK(vkBeginCommandBuffer(cmd, &cbi));

    VkImageMemoryBarrier barrier{ VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER };
    barrier.srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = out.image;
    barrier.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.srcAccessMask = 0;
    barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, 0, nullptr, 0, nullptr, 1, &barrier);

    VkBufferImageCopy region{};
    region.imageSubresource = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1 };
    region.imageExtent = { width, height, depth };
    vkCmdCopyBufferToImage(cmd, staging.buffer, out.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &region);

    barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT, 0, 0, nullptr, 0, nullptr, 1, &barrier);
    VK_CHECK(vkEndCommandBuffer(cmd));

    VkSubmitInfo si{ VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    VK_CHECK(vkQueueSubmit(queue, 1, &si, VK_NULL_HANDLE));
    VK_CHECK(vkQueueWaitIdle(queue));
    vkFreeCommandBuffers(device, pool, 1, &cmd);

    vkUnmapMemory(device, staging.memory);
    vkDestroyBuffer(device, staging.buffer, nullptr);
    vkFreeMemory(device, staging.memory, nullptr);

    VkImageViewCreateInfo vi{ VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO };
    vi.image = out.image;
    vi.viewType = depth > 1 ? VK_IMAGE_VIEW_TYPE_3D : VK_IMAGE_VIEW_TYPE_2D;
    vi.format = format;
    vi.subresourceRange = { VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1 };
    VK_CHECK(vkCreateImageView(device, &vi, nullptr, &out.view));
    return out;
}

// Energy tables are 16-bit normalised integers. Prefer R16_UNORM so the driver does the
// normalisation; fall back to R32_SFLOAT on devices that cannot filter R16_UNORM linearly.
static Texture create_energy_lut(VkPhysicalDevice phys, VkDevice device, VkCommandPool pool, VkQueue queue, const uint16_t* data, uint32_t width,
                                 uint32_t height, uint32_t depth)
{
    const size_t count = size_t(width) * height * depth;
    if (format_filters_linearly(phys, VK_FORMAT_R16_UNORM))
        return create_texture(phys, device, pool, queue, VK_FORMAT_R16_UNORM, width, height, depth, data, count * sizeof(uint16_t));
    std::vector<float> as_float(count);
    for (size_t i = 0; i < count; ++i)
        as_float[i] = float(data[i]) / 65535.0f;
    return create_texture(phys, device, pool, queue, VK_FORMAT_R32_SFLOAT, width, height, depth, as_float.data(), count * sizeof(float));
}

int main(int argc, char** argv)
{
    const char* spv_path = nullptr;
    const char* ref_path = nullptr;
    uint32_t seed_base = 1, batches = 256, samples = 256;
    bool want_validation = true;
    bool tex_luts = false;
    const char* csv_path = nullptr;
    for (int i = 1; i < argc; ++i)
    {
        if (!std::strcmp(argv[i], "--tex-luts")) { tex_luts = true; continue; }
        if (!std::strcmp(argv[i], "--csv") && i + 1 < argc) { csv_path = argv[++i]; continue; }
        if (!std::strcmp(argv[i], "--spv") && i + 1 < argc) spv_path = argv[++i];
        else if (!std::strcmp(argv[i], "--ref") && i + 1 < argc) ref_path = argv[++i];
        else if (!std::strcmp(argv[i], "--seed") && i + 1 < argc) seed_base = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--batches") && i + 1 < argc) batches = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--samples") && i + 1 < argc) samples = uint32_t(std::strtoul(argv[++i], nullptr, 10));
        else if (!std::strcmp(argv[i], "--no-validation")) want_validation = false;
    }
    if (!spv_path)
    {
        std::fprintf(stderr, "usage: furnace_host --spv file.spv [--tex-luts] [--ref cpu.csv] [--seed N] [--batches B] [--samples S] [--no-validation]\n"
                             "       pass --tex-luts when the shader was compiled with -DFURNACE_TEX_LUTS\n");
        return 15;
    }

    // ---- rows ---------------------------------------------------------------------------
    std::vector<Row> rows;
    std::vector<const Config*> row_config;
    for (const Config& c : kConfigs)
        for (const float roughness : kRoughnesses)
            for (const float cos_view : kCosViews)
            {
                const uint32_t i = uint32_t(rows.size());
                rows.push_back({ c.metalness, c.base_gray, c.specular_weight, c.coat, c.fuzz, roughness, cos_view,
                                 seed_base * 2654435761u + i * 40503u + 12345u });
                row_config.push_back(&c);
            }
    const uint32_t row_count = uint32_t(rows.size());
    const uint32_t invocations = row_count * batches;

    // ---- instance -----------------------------------------------------------------------
    uint32_t layer_count = 0;
    vkEnumerateInstanceLayerProperties(&layer_count, nullptr);
    std::vector<VkLayerProperties> layers(layer_count);
    vkEnumerateInstanceLayerProperties(&layer_count, layers.data());
    bool have_validation = false;
    for (const VkLayerProperties& l : layers)
        if (!std::strcmp(l.layerName, "VK_LAYER_KHRONOS_validation"))
            have_validation = true;
    const bool use_validation = want_validation && have_validation;

    VkApplicationInfo app{ VK_STRUCTURE_TYPE_APPLICATION_INFO };
    app.pApplicationName = "openpbr_gpu_furnace";
    app.apiVersion = VK_API_VERSION_1_2;
    const char* layer_names[] = { "VK_LAYER_KHRONOS_validation" };
    const char* instance_exts[] = { VK_EXT_DEBUG_UTILS_EXTENSION_NAME };
    VkInstanceCreateInfo ici{ VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO };
    ici.pApplicationInfo = &app;
    if (use_validation)
    {
        ici.enabledLayerCount = 1;
        ici.ppEnabledLayerNames = layer_names;
        ici.enabledExtensionCount = 1;
        ici.ppEnabledExtensionNames = instance_exts;
    }
    VkInstance instance;
    VK_CHECK(vkCreateInstance(&ici, nullptr, &instance));

    VkDebugUtilsMessengerEXT messenger = VK_NULL_HANDLE;
    if (use_validation)
    {
        auto create = (PFN_vkCreateDebugUtilsMessengerEXT)vkGetInstanceProcAddr(instance, "vkCreateDebugUtilsMessengerEXT");
        VkDebugUtilsMessengerCreateInfoEXT mi{ VK_STRUCTURE_TYPE_DEBUG_UTILS_MESSENGER_CREATE_INFO_EXT };
        mi.messageSeverity = VK_DEBUG_UTILS_MESSAGE_SEVERITY_WARNING_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_SEVERITY_ERROR_BIT_EXT;
        mi.messageType = VK_DEBUG_UTILS_MESSAGE_TYPE_VALIDATION_BIT_EXT | VK_DEBUG_UTILS_MESSAGE_TYPE_GENERAL_BIT_EXT;
        mi.pfnUserCallback = on_debug;
        if (create)
            VK_CHECK(create(instance, &mi, nullptr, &messenger));
    }

    // ---- device: prefer a discrete GPU, never hardcode an index --------------------------
    uint32_t phys_count = 0;
    VK_CHECK(vkEnumeratePhysicalDevices(instance, &phys_count, nullptr));
    std::vector<VkPhysicalDevice> physicals(phys_count);
    VK_CHECK(vkEnumeratePhysicalDevices(instance, &phys_count, physicals.data()));
    if (phys_count == 0)
    {
        std::fprintf(stderr, "no Vulkan device\n");
        return 15;
    }
    VkPhysicalDevice phys = physicals[0];
    VkPhysicalDeviceProperties phys_props;
    for (VkPhysicalDevice p : physicals)
    {
        vkGetPhysicalDeviceProperties(p, &phys_props);
        if (phys_props.deviceType == VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU)
        {
            phys = p;
            break;
        }
    }
    vkGetPhysicalDeviceProperties(phys, &phys_props);

    uint32_t family_count = 0;
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &family_count, nullptr);
    std::vector<VkQueueFamilyProperties> families(family_count);
    vkGetPhysicalDeviceQueueFamilyProperties(phys, &family_count, families.data());
    uint32_t family = UINT32_MAX;
    for (uint32_t i = 0; i < family_count; ++i)
        if (families[i].queueFlags & VK_QUEUE_COMPUTE_BIT)
        {
            family = i;
            break;
        }
    if (family == UINT32_MAX)
    {
        std::fprintf(stderr, "no compute queue\n");
        return 15;
    }

    const float priority = 1.0f;
    VkDeviceQueueCreateInfo qi{ VK_STRUCTURE_TYPE_DEVICE_QUEUE_CREATE_INFO };
    qi.queueFamilyIndex = family;
    qi.queueCount = 1;
    qi.pQueuePriorities = &priority;
    VkDeviceCreateInfo dci{ VK_STRUCTURE_TYPE_DEVICE_CREATE_INFO };
    dci.queueCreateInfoCount = 1;
    dci.pQueueCreateInfos = &qi;
    // Texture-LUT mode indexes an array of samplers with the table ID, which needs this feature.
    VkPhysicalDeviceFeatures supported_features;
    vkGetPhysicalDeviceFeatures(phys, &supported_features);
    VkPhysicalDeviceFeatures enabled_features{};
    if (tex_luts)
    {
        if (!supported_features.shaderSampledImageArrayDynamicIndexing)
        {
            std::fprintf(stderr, "device lacks shaderSampledImageArrayDynamicIndexing\n");
            return 15;
        }
        enabled_features.shaderSampledImageArrayDynamicIndexing = VK_TRUE;
    }
    dci.pEnabledFeatures = &enabled_features;
    VkDevice device;
    VK_CHECK(vkCreateDevice(phys, &dci, nullptr, &device));
    VkQueue queue;
    vkGetDeviceQueue(device, family, 0, &queue);

    VkCommandPoolCreateInfo cpci{ VK_STRUCTURE_TYPE_COMMAND_POOL_CREATE_INFO };
    cpci.queueFamilyIndex = family;
    VkCommandPool command_pool;
    VK_CHECK(vkCreateCommandPool(device, &cpci, nullptr, &command_pool));

    // ---- lookup-table textures ------------------------------------------------------------
    // Slot i of each sampler array is table ID i. IDs 0 and 3 are 3D tables; the rest are 2D.
    // Unused slots get a 1-texel dummy so that every descriptor in both arrays is valid.
    std::vector<Texture> textures;
    VkSampler lut_sampler = VK_NULL_HANDLE;
    VkDescriptorImageInfo lut_2d[8]{}, lut_3d[8]{};
    const char* energy_format = "n/a";
    if (tex_luts)
    {
        VkSamplerCreateInfo sci{ VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO };
        sci.magFilter = VK_FILTER_LINEAR;
        sci.minFilter = VK_FILTER_LINEAR;
        sci.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
        sci.addressModeU = sci.addressModeV = sci.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
        VK_CHECK(vkCreateSampler(device, &sci, nullptr, &lut_sampler));
        energy_format = format_filters_linearly(phys, VK_FORMAT_R16_UNORM) ? "R16_UNORM" : "R32_SFLOAT";

        const uint16_t zero16 = 0;
        const Texture dummy_2d = create_energy_lut(phys, device, command_pool, queue, &zero16, 1, 1, 1);
        // A 3D image needs depth > 1 to be created as 3D here, so the dummy is 1x1x2.
        const uint16_t zero16x2[2] = { 0, 0 };
        const Texture dummy_3d = create_energy_lut(phys, device, command_pool, queue, zero16x2, 1, 1, 2);
        textures.push_back(dummy_2d);
        textures.push_back(dummy_3d);

        Texture table_2d[8], table_3d[8];
        for (int i = 0; i < 8; ++i)
        {
            table_2d[i] = dummy_2d;
            table_3d[i] = dummy_3d;
        }
        table_3d[0] = create_energy_lut(phys, device, command_pool, queue, kLutIdealDielectricEnergy, 32, 32, 32);
        table_2d[1] = create_energy_lut(phys, device, command_pool, queue, kLutIdealDielectricAvgEnergy, 32, 32, 1);
        table_2d[2] = create_energy_lut(phys, device, command_pool, queue, kLutIdealDielectricReflectionRatio, 32, 32, 1);
        table_3d[3] = create_energy_lut(phys, device, command_pool, queue, kLutOpaqueDielectricEnergy, 32, 32, 32);
        table_2d[4] = create_energy_lut(phys, device, command_pool, queue, kLutOpaqueDielectricAvgEnergy, 32, 32, 1);
        table_2d[5] = create_energy_lut(phys, device, command_pool, queue, kLutIdealMetalEnergy, 32, 32, 1);
        table_2d[6] = create_energy_lut(phys, device, command_pool, queue, kLutIdealMetalAvgEnergy, 32, 1, 1);

        std::vector<float> ltc_rgba(32 * 32 * 4);
        for (int i = 0; i < 32 * 32; ++i)
        {
            ltc_rgba[size_t(i) * 4 + 0] = kLutLtc[i].x;
            ltc_rgba[size_t(i) * 4 + 1] = kLutLtc[i].y;
            ltc_rgba[size_t(i) * 4 + 2] = kLutLtc[i].z;
            ltc_rgba[size_t(i) * 4 + 3] = 1.0f;
        }
        if (!format_filters_linearly(phys, VK_FORMAT_R32G32B32A32_SFLOAT))
        {
            std::fprintf(stderr, "device cannot filter RGBA32F linearly; the sheen table needs another format\n");
            return 15;
        }
        table_2d[7] = create_texture(phys, device, command_pool, queue, VK_FORMAT_R32G32B32A32_SFLOAT, 32, 32, 1, ltc_rgba.data(),
                                     ltc_rgba.size() * sizeof(float));

        for (int i : { 1, 2, 4, 5, 6, 7 })
            textures.push_back(table_2d[i]);
        for (int i : { 0, 3 })
            textures.push_back(table_3d[i]);
        for (int i = 0; i < 8; ++i)
        {
            lut_2d[i] = { lut_sampler, table_2d[i].view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL };
            lut_3d[i] = { lut_sampler, table_3d[i].view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL };
        }
    }

    // ---- buffers ------------------------------------------------------------------------
    const VkDeviceSize rows_bytes = sizeof(Row) * row_count;
    const VkDeviceSize partials_bytes = sizeof(Partial) * VkDeviceSize(invocations);
    Buffer rows_buffer = create_host_buffer(phys, device, rows_bytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    Buffer partials_buffer = create_host_buffer(phys, device, partials_bytes, VK_BUFFER_USAGE_STORAGE_BUFFER_BIT);
    std::memcpy(rows_buffer.mapped, rows.data(), size_t(rows_bytes));
    std::memset(partials_buffer.mapped, 0, size_t(partials_bytes));

    // ---- descriptors --------------------------------------------------------------------
    VkDescriptorSetLayoutBinding bindings[4]{};
    for (uint32_t i = 0; i < 4; ++i)
    {
        bindings[i].binding = i;
        bindings[i].descriptorType = i < 2 ? VK_DESCRIPTOR_TYPE_STORAGE_BUFFER : VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        bindings[i].descriptorCount = i < 2 ? 1 : 8;
        bindings[i].stageFlags = VK_SHADER_STAGE_COMPUTE_BIT;
    }
    VkDescriptorSetLayoutCreateInfo li{ VK_STRUCTURE_TYPE_DESCRIPTOR_SET_LAYOUT_CREATE_INFO };
    li.bindingCount = tex_luts ? 4 : 2;
    li.pBindings = bindings;
    VkDescriptorSetLayout set_layout;
    VK_CHECK(vkCreateDescriptorSetLayout(device, &li, nullptr, &set_layout));

    const VkDescriptorPoolSize pool_sizes[2] = { { VK_DESCRIPTOR_TYPE_STORAGE_BUFFER, 2 }, { VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER, 16 } };
    VkDescriptorPoolCreateInfo pi{ VK_STRUCTURE_TYPE_DESCRIPTOR_POOL_CREATE_INFO };
    pi.maxSets = 1;
    pi.poolSizeCount = tex_luts ? 2 : 1;
    pi.pPoolSizes = pool_sizes;
    VkDescriptorPool pool;
    VK_CHECK(vkCreateDescriptorPool(device, &pi, nullptr, &pool));

    VkDescriptorSetAllocateInfo sai{ VK_STRUCTURE_TYPE_DESCRIPTOR_SET_ALLOCATE_INFO };
    sai.descriptorPool = pool;
    sai.descriptorSetCount = 1;
    sai.pSetLayouts = &set_layout;
    VkDescriptorSet set;
    VK_CHECK(vkAllocateDescriptorSets(device, &sai, &set));

    VkDescriptorBufferInfo buffer_infos[2] = { { rows_buffer.buffer, 0, rows_bytes }, { partials_buffer.buffer, 0, partials_bytes } };
    VkWriteDescriptorSet writes[2]{};
    for (uint32_t i = 0; i < 2; ++i)
    {
        writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[i].dstSet = set;
        writes[i].dstBinding = i;
        writes[i].descriptorCount = 1;
        writes[i].descriptorType = VK_DESCRIPTOR_TYPE_STORAGE_BUFFER;
        writes[i].pBufferInfo = &buffer_infos[i];
    }
    vkUpdateDescriptorSets(device, 2, writes, 0, nullptr);
    if (tex_luts)
    {
        VkWriteDescriptorSet lut_writes[2]{};
        for (uint32_t i = 0; i < 2; ++i)
        {
            lut_writes[i].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
            lut_writes[i].dstSet = set;
            lut_writes[i].dstBinding = 2 + i;
            lut_writes[i].descriptorCount = 8;
            lut_writes[i].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
            lut_writes[i].pImageInfo = i == 0 ? lut_2d : lut_3d;
        }
        vkUpdateDescriptorSets(device, 2, lut_writes, 0, nullptr);
    }

    // ---- pipeline -----------------------------------------------------------------------
    std::ifstream spv_file(spv_path, std::ios::binary | std::ios::ate);
    if (!spv_file)
    {
        std::fprintf(stderr, "cannot open %s\n", spv_path);
        return 15;
    }
    const size_t spv_bytes = size_t(spv_file.tellg());
    std::vector<uint32_t> spv(spv_bytes / 4);
    spv_file.seekg(0);
    spv_file.read(reinterpret_cast<char*>(spv.data()), std::streamsize(spv_bytes));

    VkShaderModuleCreateInfo smi{ VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO };
    smi.codeSize = spv_bytes;
    smi.pCode = spv.data();
    VkShaderModule module;
    VK_CHECK(vkCreateShaderModule(device, &smi, nullptr, &module));

    VkPushConstantRange push_range{ VK_SHADER_STAGE_COMPUTE_BIT, 0, 3 * sizeof(uint32_t) };
    VkPipelineLayoutCreateInfo pli{ VK_STRUCTURE_TYPE_PIPELINE_LAYOUT_CREATE_INFO };
    pli.setLayoutCount = 1;
    pli.pSetLayouts = &set_layout;
    pli.pushConstantRangeCount = 1;
    pli.pPushConstantRanges = &push_range;
    VkPipelineLayout pipeline_layout;
    VK_CHECK(vkCreatePipelineLayout(device, &pli, nullptr, &pipeline_layout));

    VkComputePipelineCreateInfo cpi{ VK_STRUCTURE_TYPE_COMPUTE_PIPELINE_CREATE_INFO };
    cpi.stage.sType = VK_STRUCTURE_TYPE_PIPELINE_SHADER_STAGE_CREATE_INFO;
    cpi.stage.stage = VK_SHADER_STAGE_COMPUTE_BIT;
    cpi.stage.module = module;
    cpi.stage.pName = "main";
    cpi.layout = pipeline_layout;
    VkPipeline pipeline;
    const auto t_pipeline = std::chrono::steady_clock::now();
    VK_CHECK(vkCreateComputePipelines(device, VK_NULL_HANDLE, 1, &cpi, nullptr, &pipeline));
    const double pipeline_seconds = seconds_since(t_pipeline);

    // ---- dispatch -----------------------------------------------------------------------
    VkCommandBufferAllocateInfo cbai{ VK_STRUCTURE_TYPE_COMMAND_BUFFER_ALLOCATE_INFO };
    cbai.commandPool = command_pool;
    cbai.level = VK_COMMAND_BUFFER_LEVEL_PRIMARY;
    cbai.commandBufferCount = 1;
    VkCommandBuffer cmd;
    VK_CHECK(vkAllocateCommandBuffers(device, &cbai, &cmd));

    const uint32_t push[3] = { row_count, batches, samples };
    VkCommandBufferBeginInfo cbi{ VK_STRUCTURE_TYPE_COMMAND_BUFFER_BEGIN_INFO };
    VK_CHECK(vkBeginCommandBuffer(cmd, &cbi));
    vkCmdBindPipeline(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline);
    vkCmdBindDescriptorSets(cmd, VK_PIPELINE_BIND_POINT_COMPUTE, pipeline_layout, 0, 1, &set, 0, nullptr);
    vkCmdPushConstants(cmd, pipeline_layout, VK_SHADER_STAGE_COMPUTE_BIT, 0, sizeof(push), push);
    vkCmdDispatch(cmd, (invocations + 63) / 64, 1, 1);
    VK_CHECK(vkEndCommandBuffer(cmd));

    VkFenceCreateInfo fci{ VK_STRUCTURE_TYPE_FENCE_CREATE_INFO };
    VkFence fence;
    VK_CHECK(vkCreateFence(device, &fci, nullptr, &fence));
    VkSubmitInfo si{ VK_STRUCTURE_TYPE_SUBMIT_INFO };
    si.commandBufferCount = 1;
    si.pCommandBuffers = &cmd;
    const auto t_dispatch = std::chrono::steady_clock::now();
    VK_CHECK(vkQueueSubmit(queue, 1, &si, fence));
    VK_CHECK(vkWaitForFences(device, 1, &fence, VK_TRUE, UINT64_MAX));
    const double dispatch_seconds = seconds_since(t_dispatch);

    // ---- judge --------------------------------------------------------------------------
    const std::map<std::string, RefRow> reference = ref_path ? load_reference(ref_path) : std::map<std::string, RefRow>{};
    const Partial* partials = static_cast<const Partial*>(partials_buffer.mapped);
    const double n = double(batches) * double(samples);

    std::printf("device: %s (type %d) | validation layer: %s | luts: %s%s | %u rows x %u samples | pipeline %.2fs | dispatch %.2fs\n",
                phys_props.deviceName, int(phys_props.deviceType), use_validation ? "on" : (have_validation ? "off" : "not installed"),
                tex_luts ? "textures, " : "embedded arrays", tex_luts ? energy_format : "", row_count, uint32_t(n), pipeline_seconds,
                dispatch_seconds);
    std::printf("%-26s %5s %5s  %8s %8s %8s %9s %8s  %s\n", "config", "rough", "cosV", "gpu", "3*SE", "cpu", "w!=f/pdf", "tol", "verdict");

    // Per-row results for an independent judge (judge_furnace.py). The verdicts printed below are
    // a convenience; in a harness, only the judge's exit code should count.
    std::FILE* csv = csv_path ? std::fopen(csv_path, "w") : nullptr;
    if (csv)
        std::fprintf(csv, "config,kind,metalness,base_gray,specular_weight,coat,fuzz,roughness,cos_view,albedo,std_error,weight_vs_eval,pdf_mismatch\n");

    int failures = 0, judged = 0, compared = 0;
    double worst_gpu_vs_cpu = 0.0;
    for (uint32_t r = 0; r < row_count; ++r)
    {
        double sum_rgb = 0.0, sum_sq = 0.0, rel_weight = 0.0, rel_pdf = 0.0, pairs = 0.0;
        for (uint32_t b = 0; b < batches; ++b)
        {
            const Partial& p = partials[size_t(r) * batches + b];
            sum_rgb += double(p.sums[0]) + double(p.sums[1]) + double(p.sums[2]);
            sum_sq += p.sums[3];
            rel_weight += p.stats[1];
            rel_pdf += p.stats[2];
            pairs += p.stats[3];
        }
        const double albedo = sum_rgb / 3.0 / n;
        const double std_error = std::sqrt(std::max(0.0, sum_sq / n - albedo * albedo) / n);
        const double consistency = pairs > 0.0 ? rel_weight / pairs : 0.0;
        const double pdf_consistency = pairs > 0.0 ? rel_pdf / pairs : 0.0;

        const Config& c = *row_config[r];
        const Row& row = rows[r];
        if (csv)
            std::fprintf(csv, "%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.6f,%.6f,%.3e,%.3e\n", c.name,
                         c.kind == White ? "white" : (c.kind == Lambert ? "lambert" : "control"), c.metalness, c.base_gray, c.specular_weight,
                         c.coat, c.fuzz, row.roughness, row.cos_view, albedo, std_error, consistency, pdf_consistency);
        double expected = -1.0, model_tolerance = 0.0;
        if (c.kind == White)
        {
            expected = 1.0;
            model_tolerance = (c.coat > 0.0f && row.cos_view < 0.2f) ? 0.03 : 0.005;
        }
        else if (c.kind == Lambert)
        {
            expected = c.base_gray;
            model_tolerance = 0.001;
        }
        const double tolerance = model_tolerance + 3.0 * std_error;

        bool ok = true;
        if (expected >= 0.0)
        {
            ++judged;
            // Looser consistency bound than the CPU exam: this path is single-precision throughout.
            ok = std::abs(albedo - expected) <= tolerance && consistency < 1e-3 && pdf_consistency < 1e-3;
        }

        char key[96];
        std::snprintf(key, sizeof(key), "%s|%.2f|%.2f", c.name, row.roughness, row.cos_view);
        double cpu = -1.0;
        const auto it = reference.find(key);
        if (it != reference.end())
        {
            ++compared;
            cpu = it->second.albedo;
            const double combined = 3.0 * std::sqrt(std_error * std_error + it->second.std_error * it->second.std_error) + 0.001;
            worst_gpu_vs_cpu = std::max(worst_gpu_vs_cpu, std::abs(albedo - cpu));
            if (std::abs(albedo - cpu) > combined)
                ok = false;
        }
        if (!ok)
            ++failures;
        std::printf("%-26s %5.2f %5.2f  %8.4f %8.4f %8.4f %9.2e %8.4f  %s\n", c.name, row.roughness, row.cos_view, albedo, 3.0 * std_error, cpu,
                    consistency, tolerance, ok ? (expected >= 0.0 || cpu >= 0.0 ? "PASS" : "n/a") : "FAIL");
    }

    if (csv)
        std::fclose(csv);
    std::printf("\nseed %u: %d rows judged on tolerance, %d rows compared with the CPU reference, %d failed.\n", seed_base, judged, compared, failures);
    if (compared > 0)
        std::printf("largest |gpu - cpu| albedo difference: %.4f\n", worst_gpu_vs_cpu);
    std::printf("validation errors: %d, warnings: %d\n", g_validation_errors, g_validation_warnings);

    // ---- teardown -----------------------------------------------------------------------
    vkDestroyFence(device, fence, nullptr);
    vkDestroyCommandPool(device, command_pool, nullptr);
    vkDestroyPipeline(device, pipeline, nullptr);
    vkDestroyPipelineLayout(device, pipeline_layout, nullptr);
    vkDestroyShaderModule(device, module, nullptr);
    vkDestroyDescriptorPool(device, pool, nullptr);
    vkDestroyDescriptorSetLayout(device, set_layout, nullptr);
    for (Buffer* b : { &rows_buffer, &partials_buffer })
    {
        vkUnmapMemory(device, b->memory);
        vkDestroyBuffer(device, b->buffer, nullptr);
        vkFreeMemory(device, b->memory, nullptr);
    }
    // Every object must be gone before the device, or the validation layer reports a leak.
    for (const Texture& t : textures)
    {
        vkDestroyImageView(device, t.view, nullptr);
        vkDestroyImage(device, t.image, nullptr);
        vkFreeMemory(device, t.memory, nullptr);
    }
    if (lut_sampler)
        vkDestroySampler(device, lut_sampler, nullptr);
    vkDestroyDevice(device, nullptr);
    if (messenger)
    {
        auto destroy = (PFN_vkDestroyDebugUtilsMessengerEXT)vkGetInstanceProcAddr(instance, "vkDestroyDebugUtilsMessengerEXT");
        if (destroy)
            destroy(instance, messenger, nullptr);
    }
    vkDestroyInstance(instance, nullptr);

    if (g_validation_errors > 0)
        return 14;
    return failures == 0 ? 0 : 16;
}
