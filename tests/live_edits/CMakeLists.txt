cmake_minimum_required(VERSION 3.20)
project(HoudiniRTLiveEditTests LANGUAGES CXX)

find_package(Houdini REQUIRED)
enable_testing()

set(HRT_SOURCE "${CMAKE_CURRENT_LIST_DIR}/../../src/hdRT")
add_executable(scene_snapshots scene_snapshots.cpp "${HRT_SOURCE}/world.cpp")
add_executable(buffer_publication buffer_publication.cpp "${HRT_SOURCE}/renderBuffer.cpp")

foreach(test_name IN ITEMS scene_snapshots buffer_publication)
    target_include_directories(${test_name} PRIVATE "${HRT_SOURCE}")
    target_link_libraries(${test_name} PRIVATE Houdini HoudiniThirdParty)
    target_compile_features(${test_name} PRIVATE cxx_std_20)
    if(MSVC)
        target_compile_definitions(${test_name} PRIVATE
            _SILENCE_CXX20_OLD_SHARED_PTR_ATOMIC_SUPPORT_DEPRECATION_WARNING)
    endif()
    add_test(NAME ${test_name} COMMAND $<TARGET_FILE:${test_name}>)
endforeach()
