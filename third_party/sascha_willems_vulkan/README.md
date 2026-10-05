# Reference only: Sascha Willems' Vulkan ray-query sample (MIT)

Source: https://github.com/SaschaWillems/Vulkan (examples/rayquery, base/VulkanRaytracingSample, shaders/glsl/rayquery). Licence: MIT, see LICENSE.md.

These files do NOT build here. They depend on his base framework (VulkanExampleBase, vks::Buffer, glTF loader). Do not include or link them.

Use them as a worked example and adapt the calls into this project's own standalone host (`src/furnace/furnace_host.cpp` shows this project's style: raw Vulkan, no framework, validation layer on, explicit teardown).

What to take:
- `rayquery.cpp` `createBottomLevelAccelerationStructure()` and `createTopLevelAccelerationStructure()`: the full sequence of VkAccelerationStructureGeometryKHR, vkGetAccelerationStructureBuildSizesKHR, buffer + vkCreateAccelerationStructureKHR, scratch buffer, vkCmdBuildAccelerationStructuresKHR, instance buffer for the TLAS.
- `VulkanRaytracingSample.cpp`: the device extensions and feature structs to enable (acceleration structure, ray query, buffer device address, deferred host operations), scratch buffer creation, and how a device address is fetched.
- `scene.frag`: `rayQueryInitializeEXT` / `rayQueryProceedEXT` / committed-intersection usage in GLSL. This project uses a compute shader, so the same calls go in a `.comp` with `layout(binding = N) uniform accelerationStructureEXT name;`.

Keep a copy of the MIT notice with any substantial portion that is adapted.
