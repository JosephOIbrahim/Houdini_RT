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

# Passed by the exam. Neither is used yet: the furnace has no BSDF in it. OPENPBR arrives with first light.
set(HRT_REPO_ROOT "" CACHE PATH "Repository root")
set(OPENPBR "" CACHE PATH "Adobe OpenPBR BSDF checkout (unused before step 4)")

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

set(HRT_SHADER_SRC "${CMAKE_CURRENT_SOURCE_DIR}/shaders/furnace.comp")
set(HRT_SHADER_SPV "${CMAKE_CURRENT_BINARY_DIR}/shaders/furnace.spv")
add_custom_command(
    OUTPUT "${HRT_SHADER_SPV}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/shaders"
    COMMAND "${HRT_GLSLANG}" -V --target-env vulkan1.2 -S comp "${HRT_SHADER_SRC}" -o "${HRT_SHADER_SPV}"
    DEPENDS "${HRT_SHADER_SRC}"
    COMMENT "glslangValidator furnace.comp"
    VERBATIM)
add_custom_target(hdRT_shaders ALL DEPENDS "${HRT_SHADER_SPV}")

add_library(hdRT SHARED
    rendererPlugin.cpp
    renderDelegate.cpp
    renderPass.cpp
    renderBuffer.cpp
    renderer.cpp
    settings.cpp
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
install(FILES "${HRT_SHADER_SPV}" DESTINATION shaders)
