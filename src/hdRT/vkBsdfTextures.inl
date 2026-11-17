// Included after HdRTVkFurnace::Impl. Texture LUT mode is the production BSDF path.
#include "bsdfLutData.h"

bool HdRTVkFurnace::Impl::CreateBsdfTexture(BsdfTexture& out, VkFormat format,
    uint32_t width, uint32_t height, uint32_t depth, const void* data,
    size_t bytes, std::string* error)
{
    VkFormatProperties properties;
    vkGetPhysicalDeviceFormatProperties(phys, format, &properties);
    const auto features = VK_FORMAT_FEATURE_SAMPLED_IMAGE_BIT |
        VK_FORMAT_FEATURE_SAMPLED_IMAGE_FILTER_LINEAR_BIT | VK_FORMAT_FEATURE_TRANSFER_DST_BIT;
    if ((properties.optimalTilingFeatures & features) != features)
        return _Fail(error, "OpenPBR LUT format lacks sampled image/linear filtering/transfer support");
    VkImageCreateInfo image = {VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO};
    image.imageType = depth > 1 ? VK_IMAGE_TYPE_3D : VK_IMAGE_TYPE_2D;
    image.format = format;
    image.extent = {width, height, depth};
    image.mipLevels = image.arrayLayers = 1;
    image.samples = VK_SAMPLE_COUNT_1_BIT;
    image.tiling = VK_IMAGE_TILING_OPTIMAL;
    image.usage = VK_IMAGE_USAGE_TRANSFER_DST_BIT | VK_IMAGE_USAGE_SAMPLED_BIT;
    image.sharingMode = VK_SHARING_MODE_EXCLUSIVE;
    HDRT_VK(vkCreateImage(device, &image, nullptr, &out.image));
    VkMemoryRequirements requirements;
    vkGetImageMemoryRequirements(device, out.image, &requirements);
    VkMemoryAllocateInfo allocation = {VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO};
    allocation.allocationSize = requirements.size;
    allocation.memoryTypeIndex = FindMemoryType(requirements.memoryTypeBits, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT);
    if (allocation.memoryTypeIndex == UINT32_MAX)
        return _Fail(error, "no device-local memory for OpenPBR LUT");
    HDRT_VK(vkAllocateMemory(device, &allocation, nullptr, &out.memory));
    HDRT_VK(vkBindImageMemory(device, out.image, out.memory, 0));
    if (!EnsureBuffer(bytes, VK_BUFFER_USAGE_TRANSFER_SRC_BIT, true, &stagingBuffer, error)) return false;
    std::memcpy(stagingBuffer.mapped, data, bytes);
    if (!BeginCommands(error)) return false;
    VkImageMemoryBarrier barrier = {VK_STRUCTURE_TYPE_IMAGE_MEMORY_BARRIER};
    barrier.dstAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_UNDEFINED;
    barrier.newLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.srcQueueFamilyIndex = barrier.dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED;
    barrier.image = out.image;
    barrier.subresourceRange = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 1, 0, 1};
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TOP_OF_PIPE_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT,
                         0, 0, nullptr, 0, nullptr, 1, &barrier);
    VkBufferImageCopy copy = {};
    copy.imageSubresource = {VK_IMAGE_ASPECT_COLOR_BIT, 0, 0, 1};
    copy.imageExtent = image.extent;
    vkCmdCopyBufferToImage(cmd, stagingBuffer.buffer, out.image, VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL, 1, &copy);
    barrier.srcAccessMask = VK_ACCESS_TRANSFER_WRITE_BIT;
    barrier.dstAccessMask = VK_ACCESS_SHADER_READ_BIT;
    barrier.oldLayout = VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL;
    barrier.newLayout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    vkCmdPipelineBarrier(cmd, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_COMPUTE_SHADER_BIT,
                         0, 0, nullptr, 0, nullptr, 1, &barrier);
    if (!Submit(error)) return false;
    VkImageViewCreateInfo view = {VK_STRUCTURE_TYPE_IMAGE_VIEW_CREATE_INFO};
    view.image = out.image;
    view.viewType = depth > 1 ? VK_IMAGE_VIEW_TYPE_3D : VK_IMAGE_VIEW_TYPE_2D;
    view.format = format;
    view.subresourceRange = barrier.subresourceRange;
    HDRT_VK(vkCreateImageView(device, &view, nullptr, &out.view));
    return true;
}

bool HdRTVkFurnace::Impl::CreateBsdfTextures(std::string* error)
{
    const uint16_t* data[] = {kBsdfEnergy0,kBsdfEnergy1,kBsdfEnergy2,kBsdfEnergy3,
                             kBsdfEnergy4,kBsdfEnergy5,kBsdfEnergy6};
    for (int i = 0; i < 7; ++i) {
        const uint32_t height = i == 6 ? 1 : 32;
        const uint32_t depth = i == 0 || i == 3 ? 32 : 1;
        std::vector<float> values(size_t(32)*height*depth);
        for (size_t j = 0; j < values.size(); ++j) values[j] = float(data[i][j])/65535.0f;
        if (!CreateBsdfTexture(bsdfTextures[i], VK_FORMAT_R32_SFLOAT, 32, height,
                               depth, values.data(), values.size()*sizeof(float), error)) return false;
    }
    std::vector<float> ltc(32*32*4);
    for (size_t i = 0; i < 32*32; ++i) {
        ltc[i*4] = kBsdfLtc[i].x;
        ltc[i*4+1] = kBsdfLtc[i].y;
        ltc[i*4+2] = kBsdfLtc[i].z;
        ltc[i*4+3] = 1.0f;
    }
    if (!CreateBsdfTexture(bsdfTextures[7], VK_FORMAT_R32G32B32A32_SFLOAT, 32, 32,
                           1, ltc.data(), ltc.size()*sizeof(float), error)) return false;
    VkSamplerCreateInfo sampler = {VK_STRUCTURE_TYPE_SAMPLER_CREATE_INFO};
    sampler.magFilter = sampler.minFilter = VK_FILTER_LINEAR;
    sampler.mipmapMode = VK_SAMPLER_MIPMAP_MODE_NEAREST;
    sampler.addressModeU = sampler.addressModeV = sampler.addressModeW = VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_EDGE;
    HDRT_VK(vkCreateSampler(device, &sampler, nullptr, &bsdfSampler));
    VkDescriptorImageInfo images[2][8] = {};
    VkWriteDescriptorSet writes[2] = {};
    for (uint32_t dimension = 0; dimension < 2; ++dimension) {
        for (uint32_t i = 0; i < 8; ++i) {
            const bool is3d = i == 0 || i == 3;
            // Unused slots still require a valid view of the declared sampler dimension.
            const uint32_t slot = is3d == bool(dimension) ? i : dimension ? 0 : 1;
            images[dimension][i] = {bsdfSampler, bsdfTextures[slot].view, VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL};
        }
        writes[dimension].sType = VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET;
        writes[dimension].dstSet = descriptorSet;
        writes[dimension].dstBinding = 6+dimension;
        writes[dimension].descriptorCount = 8;
        writes[dimension].descriptorType = VK_DESCRIPTOR_TYPE_COMBINED_IMAGE_SAMPLER;
        writes[dimension].pImageInfo = images[dimension];
    }
    vkUpdateDescriptorSets(device, 2, writes, 0, nullptr);
    return true;
}

void HdRTVkFurnace::Impl::DestroyBsdfTextures()
{
    if (bsdfSampler) vkDestroySampler(device, bsdfSampler, nullptr);
    bsdfSampler = VK_NULL_HANDLE;
    for (BsdfTexture& texture : bsdfTextures) {
        if (texture.view) vkDestroyImageView(device, texture.view, nullptr);
        if (texture.image) vkDestroyImage(device, texture.image, nullptr);
        if (texture.memory) vkFreeMemory(device, texture.memory, nullptr);
        texture = BsdfTexture();
    }
}
