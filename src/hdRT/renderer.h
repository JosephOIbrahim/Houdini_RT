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
#include "world.h"

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

class HdRTRenderBuffer;

/// A complete, immutable handoff. Output lifetimes are synchronized by Hydra buffer Sync/Finalize;
/// anonymous fallback buffers are retained here across viewport resizes.
struct HdRTRenderRequest
{
    HdRTFurnaceConfig config;
    std::shared_ptr<const HdRTWorldScene> scene;
    GfMatrix4d view{1.0}, projection{1.0};
    GfRect2i dataWindow;
    HdRenderPassAovBindingVector aovs;
    std::vector<std::shared_ptr<HdRTRenderBuffer>> ownedBuffers;
    std::chrono::steady_clock::time_point observedAt;
    uint64_t generation = 0;
};

/// A snapshot of what the renderer last did, safe to read from any thread.
struct HdRTRenderStats
{
    int seed = 0;
    int spp = 0;
    int bounces = 0;                ///< the houdinirt:bounces value this render used
    int completedSamples = 0;
    std::string device;             ///< Vulkan deviceName, empty until the GPU has been initialised
    std::string settingsSource;     ///< where the furnace values came from
    bool validationActive = false;
    int validationErrors = 0;
    double renderSeconds = 0.0;
    std::string error;              ///< empty unless the last render failed
    HdRTCensus census;              ///< what Hydra synced, as of the last SetScene
    HdRTVkEditStats edits;
    double editMilliseconds = 0.0;
    uint64_t sceneGeneration = 0;
    uint64_t presentedGeneration = 0;
    std::string sceneMemory;
};

class HdRTRenderer final
{
public:
    HdRTRenderer();
    ~HdRTRenderer();

    /// Where the compute kernel's SPIR-V lives on disk. Set once, before the first render.
    void SetShaderPath(const std::string& spvPath);

    /// Publish without waiting for the rendering/GPU thread. Latest complete request wins.
    void Submit(HdRTRenderRequest request);
    bool IsConverged() const;

    // ---- the render thread's body ---------------------------------------------------------------

    /// Consumes complete requests until stopped, waiting between converged images. GPU work and
    /// image conversion stay on this thread; failures are logged and recorded in the stats.
    void Render(HdRenderThread* renderThread);

    // ---- teardown --------------------------------------------------------------------------------

    /// Destroys every GPU object. The render thread must not be running.
    void Shutdown();

    HdRTRenderStats GetStats() const;

private:
    HdRTRenderer(const HdRTRenderer&) = delete;
    HdRTRenderer& operator=(const HdRTRenderer&) = delete;

    bool _ValidateAovBindings();
    void _RenderFrame(HdRenderThread* renderThread);
    bool _Superseded() const;
    void SetConfig(const HdRTFurnaceConfig& config);
    void SetScene(std::shared_ptr<const HdRTWorldScene> scene);
    void SetCamera(const GfMatrix4d& viewMatrix, const GfMatrix4d& projMatrix);
    void SetDataWindow(const GfRect2i& dataWindow);
    void SetAovBindings(HdRenderPassAovBindingVector const& aovBindings);
    std::unique_ptr<HdRTRenderBuffer> _CreateClearedAov(size_t index);
    void MarkAovBuffersUnconverged();
    void _MarkAovBuffersConverged();
    bool _WriteAovs(int completedSamples, unsigned int x0, unsigned int y0,
                    unsigned int w, unsigned int h, std::string* error);
    void _Fail(const std::string& what);

    std::string _spvPath;
    HdRTFurnaceConfig _config;
    std::shared_ptr<const HdRTWorldScene> _scene;           // active render-thread snapshot
    std::shared_ptr<const HdRTWorldScene> _uploadedScene;   // what the GPU holds; render thread only
    HdRTVkEditStats _unpresentedEdits; // work survives cancelled requests until an image is published

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
    mutable std::mutex _requestMutex;
    std::condition_variable _requestReady;
    std::shared_ptr<const HdRTRenderRequest> _requested;
    std::shared_ptr<const HdRTRenderRequest> _activeRequest;
    std::atomic<uint64_t> _requestedGeneration{0};
    std::atomic<uint64_t> _completedGeneration{0};
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDERER_H
