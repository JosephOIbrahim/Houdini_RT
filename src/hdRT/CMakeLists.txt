# src/hdRT - the HoudiniRT Hydra render delegate (step 3: the delegate shell).
#
# The exam (verify/s3_delegate.sh) drives this file exactly like so:
#   cmake -S src/hdRT -B build/s3/cmake -G "Visual Studio 17 2022" -A x64
#         -DCMAKE_PREFIX_PATH=<HFS>/toolkit/cmake -DHRT_REPO_ROOT=<repo> -DOPENPBR=<openpbr or "">
#   cmake --build build/s3/cmake --config Release
#   cmake --install build/s3/cmake --config Release --prefix build/plugin_stage
#
# What lands in the install prefix (a flat USD plugin directory; point PXR_PLUGINPATH_NAME at it):
#   plugInfo.json          one HdRendererPlugin type, displayName "HoudiniRT"
#   hdRT.dll               the delegate
#   shaders/furnace.spv    the compute kernel, loaded at run time from the plugin's resource path
cmake_minimum_required(VERSION 3.20)
project(hdRT LANGUAGES CXX)

# Retain the exam arguments. Production always uses the hash-pinned vendored OpenPBR snapshot.
set(HRT_REPO_ROOT "" CACHE PATH "Repository root")
set(OPENPBR "" CACHE PATH "Legacy exam dependency argument; production uses third_party/openpbr-bsdf")

# Houdini's HDK: headers for USD/Hydra and Vulkan, import libraries for both (HoudiniThirdParty carries
# libpxr_* and vulkan-1.lib). "Houdini" alone leaves libpxr_hd and python313 unresolved (LNK2019).
# houdini_configure_target() is deliberately NOT called: it sends the DLL to the user's houdini22.0/dso.
find_package(Houdini REQUIRED)

# The Vulkan SDK is needed for one tool only: the GLSL compiler.
# VULKAN_SDK usually arrives with backslashes on Windows; normalise it before building paths from it.
file(TO_CMAKE_PATH "$ENV{VULKAN_SDK}" HRT_VULKAN_SDK)
find_program(HRT_GLSLANG glslangValidator HINTS "${HRT_VULKAN_SDK}/Bin" "${HRT_VULKAN_SDK}/bin")
if(NOT HRT_GLSLANG)
    message(FATAL_ERROR "glslangValidator was not found. Install the Vulkan SDK and set VULKAN_SDK "
                        "(found VULKAN_SDK='$ENV{VULKAN_SDK}').")
endif()
message(STATUS "HoudiniRT: GLSL compiler ${HRT_GLSLANG}")

file(GLOB_RECURSE HRT_BSDF_HEADERS CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/../../third_party/openpbr-bsdf/*.h")
set(HRT_SHADER_OUTPUTS)
foreach(kernel IN ITEMS furnace resolve)
    set(shader_src "${CMAKE_CURRENT_SOURCE_DIR}/shaders/${kernel}.comp")
    set(shader_spv "${CMAKE_CURRENT_BINARY_DIR}/shaders/${kernel}.spv")
    add_custom_command(
        OUTPUT "${shader_spv}"
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/shaders"
        COMMAND "${HRT_GLSLANG}" -V --target-env vulkan1.2 -S comp "${shader_src}" -o "${shader_spv}"
        DEPENDS "${shader_src}" shaders/lighting.glsl shaders/guides.glsl shaders/material.glsl ${HRT_BSDF_HEADERS}
        COMMENT "glslangValidator ${kernel}.comp"
        VERBATIM)
    list(APPEND HRT_SHADER_OUTPUTS "${shader_spv}")
endforeach()
add_custom_target(hdRT_shaders ALL DEPENDS ${HRT_SHADER_OUTPUTS})

add_library(hdRT SHARED
    rendererPlugin.cpp
    renderDelegate.cpp
    renderPass.cpp
    renderBuffer.cpp
    renderer.cpp
    settings.cpp
    mesh.cpp
    meshNormals.cpp
    material.cpp
    light.cpp
    world.cpp
    vkFurnace.cpp)
add_dependencies(hdRT hdRT_shaders)
target_link_libraries(hdRT PRIVATE Houdini HoudiniThirdParty)
# MFB_* name this library to USD's registry and to PLUG_THIS_PLUGIN; they must match "Name" in plugInfo.json.
target_compile_definitions(hdRT PRIVATE
    MFB_PACKAGE_NAME=hdRT
    MFB_ALT_PACKAGE_NAME=hdRT
    MFB_PACKAGE_MODULE=HdRT)
if(MSVC)
    # Compile the translation units in parallel, and silence one deprecation notice that comes from USD's own
    # headers under C++20 (std::atomic_* on shared_ptr) and would otherwise fill the build log.
    target_compile_options(hdRT PRIVATE /MP)
    target_compile_definitions(hdRT PRIVATE _SILENCE_CXX20_OLD_SHARED_PTR_ATOMIC_SUPPORT_DEPRECATION_WARNING)
endif()
set_target_properties(hdRT PROPERTIES PREFIX "" OUTPUT_NAME "hdRT")

# Install the DLL as a plain file: the plugin exports no symbols, so there is no import library to install.
install(FILES "$<TARGET_FILE:hdRT>" DESTINATION .)
install(FILES plugInfo.json DESTINATION .)
install(FILES capabilities.json DESTINATION .)
install(FILES ${HRT_SHADER_OUTPUTS} DESTINATION shaders)

install(FILES ../../third_party/openpbr-bsdf/LICENSE DESTINATION licenses RENAME OpenPBR-LICENSE.txt)
