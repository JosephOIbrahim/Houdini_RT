cmake_minimum_required(VERSION 3.20)
project(HoudiniRTMaterialTests LANGUAGES CXX)
find_package(Houdini REQUIRED)
enable_testing()
set(HRT_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../src/hdRT")
add_executable(material_translation material_translation.cpp
    "${HRT_SOURCE}/material.cpp" "${HRT_SOURCE}/mesh.cpp"
    "${HRT_SOURCE}/meshNormals.cpp" "${HRT_SOURCE}/world.cpp")
target_include_directories(material_translation PRIVATE "${HRT_SOURCE}")
target_link_libraries(material_translation PRIVATE Houdini HoudiniThirdParty)
target_compile_features(material_translation PRIVATE cxx_std_20)
if(MSVC)
    target_compile_definitions(material_translation PRIVATE
        _SILENCE_CXX20_OLD_SHARED_PTR_ATOMIC_SUPPORT_DEPRECATION_WARNING)
endif()
add_test(NAME material_translation COMMAND $<TARGET_FILE:material_translation>)
