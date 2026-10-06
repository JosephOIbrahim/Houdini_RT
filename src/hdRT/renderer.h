//
// Copyright 2018 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderer.h. Only the structure is kept: setters called by the render pass,
// AOV binding validation, Clear, and a pass loop with a pause point and a cancellation point. The tracing
// is this project's own (vkFurnace). See NOTICE.md in this directory.
//
// renderer.h - what the render thread runs.
//
// HdRTRenderer sits between Hydra (render pass, render buffers, HdRenderThread) and the GPU kernel
// (HdRTVkFurnace). The render pass hands it the camera, the image window, the AOV bindings and the furnace
// parameters while the render thread is stopped; Render() then runs on the render thread, one sample per
// pixel per pass, and writes the running average into the bound render buffers.
#ifndef HDRT_RENDERER_H
#define HDRT_RENDERER_H

#include "pxr/pxr.h"
#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/rect2i.h"
#include "pxr/imaging/hd/aov.h"
#include "pxr/imaging/hd/renderThread.h"

#include "settings.h"
#include "vkFurnace.h"

#include <atomic>
#include <mutex>
#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

/// A snapshot of what the renderer last did, safe to read from any thread.
struct HdRTRenderStats
{
    int seed = 0;
    int spp = 0;
    int completedSamples = 0;
    std::string device;             ///< Vulkan deviceName, empty until the GPU has been initialised
    std::string settingsSource;     ///< where the furnace values came from
    bool validationActive = false;
    int validationErrors = 0;
    double renderSeconds = 0.0;
    std::string error;              ///< empty unless the last render failed
};

class HdRTRenderer final
{
public:
    HdRTRenderer();
    ~HdRTRenderer();

    /// Where the compute kernel's SPIR-V lives on disk. Set once, before the first render.
    void SetShaderPath(const std::string& spvPath);

    // ---- called by the render pass on the app thread, always with the render thread stopped ---------

    void SetConfig(const HdRTFurnaceConfig& config);
    void SetCamera(const GfMatrix4d& viewMatrix, const GfMatrix4d& projMatrix);

    /// The pixels to render, y-down as Hydra gives it. Must lie inside the render buffers.
    void SetDataWindow(const GfRect2i& dataWindow);

    void SetAovBindings(HdRenderPassAovBindingVector const& aovBindings);
    HdRenderPassAovBindingVector const& GetAovBindings() const { return _aovBindings; }

    /// Fills every bound buffer with its clear value and marks it unconverged.
    void Clear();
    void MarkAovBuffersUnconverged();

    // ---- the render thread's body ---------------------------------------------------------------

    /// Renders until the image has `spp` samples per pixel or a stop is requested. Never blocks the app
    /// thread and never throws: a failure is logged, recorded in the stats, and ends the render.
    void Render(HdRenderThread* renderThread);

    // ---- teardown --------------------------------------------------------------------------------

    /// Destroys every GPU object. The render thread must not be running.
    void Shutdown();

    HdRTRenderStats GetStats() const;

private:
    HdRTRenderer(const HdRTRenderer&) = delete;
    HdRTRenderer& operator=(const HdRTRenderer&) = delete;

    bool _ValidateAovBindings();
    void _MarkAovBuffersConverged();
    void _WriteAovs(int completedSamples, unsigned int x0, unsigned int y0, unsigned int w, unsigned int h);
    void _Fail(const std::string& what);

    std::string _spvPath;
    HdRTFurnaceConfig _config;

    GfMatrix4d _viewMatrix;
    GfMatrix4d _projMatrix;
    GfMatrix4d _inverseViewMatrix;
    GfMatrix4d _inverseProjMatrix;
    GfRect2i _dataWindow;

    HdRenderPassAovBindingVector _aovBindings;
    std::vector<HdParsedAovToken> _aovNames;
    bool _aovBindingsNeedValidation;
    bool _aovBindingsValid;

    // Size of the bound render buffers, set when the bindings are validated.
    unsigned int _width;
    unsigned int _height;

    HdRTVkFurnace _gpu;
    HdRTVkOptions _gpuOptions;  // what the GPU was last brought up with (instance, device, scene, kernel)
    bool _gpuInitFailed;        // do not retry a failed initialisation on every restart

    mutable std::mutex _statsMutex;
    HdRTRenderStats _stats;
    std::atomic<int> _completedSamples;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDERER_H
