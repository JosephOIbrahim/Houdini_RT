//
// Copyright 2018 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderer.cpp. Only the structure is kept; see renderer.h and NOTICE.md.
//
#include "renderer.h"

#include "aov.h"
#include "log.h"
#include "renderBuffer.h"

#include "pxr/base/gf/vec3d.h"
#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec3i.h"
#include "pxr/base/gf/vec4d.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/imaging/hd/tokens.h"
#include "pxr/imaging/hd/types.h"

#include <chrono>
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <thread>

PXR_NAMESPACE_OPEN_SCOPE

namespace {

// A clear value as up to four floats. Returns how many components it had (0 if it is not float-like).
size_t _ClearAsFloats(VtValue const& v, float out[4])
{
    out[0] = out[1] = out[2] = 0.0f;
    out[3] = 1.0f;
    if (v.IsHolding<float>()) { out[0] = v.UncheckedGet<float>(); return 1; }
    if (v.IsHolding<double>()) { out[0] = float(v.UncheckedGet<double>()); return 1; }
    if (v.IsHolding<GfVec3f>()) { const GfVec3f a = v.UncheckedGet<GfVec3f>(); out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; return 3; }
    if (v.IsHolding<GfVec4f>()) { const GfVec4f a = v.UncheckedGet<GfVec4f>(); out[0] = a[0]; out[1] = a[1]; out[2] = a[2]; out[3] = a[3]; return 4; }
    if (v.IsHolding<GfVec3d>()) { const GfVec3d a = v.UncheckedGet<GfVec3d>(); out[0] = float(a[0]); out[1] = float(a[1]); out[2] = float(a[2]); return 3; }
    if (v.IsHolding<GfVec4d>()) { const GfVec4d a = v.UncheckedGet<GfVec4d>(); out[0] = float(a[0]); out[1] = float(a[1]); out[2] = float(a[2]); out[3] = float(a[3]); return 4; }
    if (v.IsHolding<int>()) { out[0] = float(v.UncheckedGet<int>()); return 1; }
    return 0;
}

void _CopyMatrix(const GfMatrix4d& m, float out[16])
{
    const double* d = m.GetArray();   // row-major, row vectors; see furnace.comp for why no transpose
    for (int i = 0; i < 16; ++i)
        out[i] = float(d[i]);
}

// Diagnostics can cap batches to compare identical samples; this is not an artist setting.
int _BatchLimit()
{
    const char* text = std::getenv("HOUDINIRT_BATCH_LIMIT");
    if (!text || !*text)
        return 32;
    char* end = nullptr;
    const long value = std::strtol(text, &end, 10);
    return end && *end == '\0' && value >= 1 && value <= 32 ? int(value) : 32;
}

double _MonotonicSeconds(std::chrono::steady_clock::time_point time = std::chrono::steady_clock::now())
{
    return std::chrono::duration<double>(time.time_since_epoch()).count();
}

} // namespace

HdRTRenderer::HdRTRenderer()
    : _viewMatrix(1.0)
    , _projMatrix(1.0)
    , _inverseViewMatrix(1.0)
    , _inverseProjMatrix(1.0)
    , _dataWindow()
    , _aovBindingsNeedValidation(false)
    , _aovBindingsValid(false)
    , _width(0)
    , _height(0)
    , _gpuInitFailed(false)
    , _completedSamples(0)
{
    HDRT_DEBUG("render session=%p created", static_cast<void*>(this));
}

HdRTRenderer::~HdRTRenderer()
{
    Shutdown();
    HDRT_DEBUG("render session=%p destroyed", static_cast<void*>(this));
}

void
HdRTRenderer::SetShaderPath(const std::string& spvPath)
{
    _spvPath = spvPath;
}

void
HdRTRenderer::Submit(HdRTRenderRequest request)
{
    {
        std::lock_guard<std::mutex> lock(_requestMutex);
        request.generation = _requestedGeneration.load() + 1;
        _requested = std::make_shared<const HdRTRenderRequest>(std::move(request));
        _requestedGeneration.store(_requested->generation);
        for (const auto& binding : _requested->aovs) {
            if (binding.renderBuffer) {
                static_cast<HdRTRenderBuffer*>(binding.renderBuffer)->SetConverged(false);
            }
        }
        HDRT_DEBUG("request renderer=%p generation=%llu scene=%llu", static_cast<void*>(this),
                   (unsigned long long)_requested->generation,
                   (unsigned long long)(_requested->scene ? _requested->scene->contentVersion : 0));
    }
    _requestReady.notify_one();
}

bool
HdRTRenderer::IsConverged() const
{
    const uint64_t requested = _requestedGeneration.load();
    return requested != 0 && _completedGeneration.load() == requested;
}

bool
HdRTRenderer::_Superseded() const
{
    return !_activeRequest || _activeRequest->generation != _requestedGeneration.load();
}

void HdRTRenderer::NotifyPause(bool paused)
{
    std::lock_guard<std::mutex> lock(_statsMutex);
    if (paused && !_stats.pauseRequested) {
        ++_stats.pauseEpoch;
        _pauseRequestedAt = std::chrono::steady_clock::now();
        _stats.pauseRequestedSeconds = _MonotonicSeconds(_pauseRequestedAt);
        _stats.pauseAcknowledgedSeconds = 0.0;
        _stats.pauseAcknowledged = false;
        _stats.pauseLatencyMilliseconds = 0.0;
    }
    _stats.pauseRequested = paused;
    if (!paused)
        _stats.pauseAcknowledged = false;
    HDRT_DEBUG("%s renderer=%p epoch=%llu generation=%llu samples=%d time_s=%.9f", paused ? "pause request" : "resume",
               static_cast<void*>(this), (unsigned long long)_stats.pauseEpoch,
               (unsigned long long)_stats.frameGeneration, _stats.frameSamples, _MonotonicSeconds());
}

void HdRTRenderer::_CaptureFrameStats()
{
    const HdRTVkFrameStats frame = _gpu.FrameStats();
    std::lock_guard<std::mutex> lock(_statsMutex);
    _stats.frame = frame;
    _stats.frameGeneration = _frameGeneration;
    _stats.frameSamples = _frameCompletedSamples;
}

void HdRTRenderer::_AcknowledgePause()
{
    // Called only at safe points: all render-thread GPU calls have finished their fences.
    _CaptureFrameStats();
    std::lock_guard<std::mutex> lock(_statsMutex);
    if (!_stats.pauseRequested || _stats.pauseAcknowledged)
        return;
    _stats.pauseAcknowledged = true;
    _stats.pauseAcknowledgedEpoch = _stats.pauseEpoch;
    const auto acknowledgedAt = std::chrono::steady_clock::now();
    _stats.pauseAcknowledgedSeconds = _MonotonicSeconds(acknowledgedAt);
    _stats.pauseLatencyMilliseconds = std::chrono::duration<double, std::milli>(
        acknowledgedAt - _pauseRequestedAt).count();
    HDRT_DEBUG("pause ack renderer=%p epoch=%llu generation=%llu samples=%d trace_submits=%llu resolve_submits=%llu readback_bytes=%llu latency_ms=%.3f time_s=%.9f",
               static_cast<void*>(this), (unsigned long long)_stats.pauseEpoch,
               (unsigned long long)_stats.frameGeneration, _stats.frameSamples,
               (unsigned long long)_stats.frame.traceSubmits, (unsigned long long)_stats.frame.resolveSubmits,
               (unsigned long long)_stats.frame.readbackBytes, _stats.pauseLatencyMilliseconds,
               _stats.pauseAcknowledgedSeconds);
}

void
HdRTRenderer::SetConfig(const HdRTFurnaceConfig& config)
{
    _config = config;
}

void
HdRTRenderer::SetScene(std::shared_ptr<const HdRTWorldScene> scene)
{
    _scene = std::move(scene);
    std::lock_guard<std::mutex> lock(_statsMutex);
    _stats.census = _scene ? _scene->census : HdRTCensus();
}

void
HdRTRenderer::SetCamera(const GfMatrix4d& viewMatrix, const GfMatrix4d& projMatrix)
{
    _viewMatrix = viewMatrix;
    _projMatrix = projMatrix;
    _inverseViewMatrix = viewMatrix.GetInverse();
    _inverseProjMatrix = projMatrix.GetInverse();
}

void
HdRTRenderer::SetDataWindow(const GfRect2i& dataWindow)
{
    _dataWindow = dataWindow;

    // The window and the render buffers have to agree, so look at the bindings again.
    _aovBindingsNeedValidation = true;
}

void
HdRTRenderer::SetAovBindings(HdRenderPassAovBindingVector const& aovBindings)
{
    _aovBindings = aovBindings;
    _aovNames.resize(_aovBindings.size());
    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        _aovNames[i] = HdParsedAovToken(_aovBindings[i].aovName);
    }

    // Re-validate the attachments.
    _aovBindingsNeedValidation = true;
}

bool
HdRTRenderer::_ValidateAovBindings()
{
    if (!_aovBindingsNeedValidation) {
        return _aovBindingsValid;
    }

    _aovBindingsNeedValidation = false;
    _aovBindingsValid = true;
    _width = 0;
    _height = 0;

    auto invalid = [&](const std::string& message) {
        TF_WARN("HoudiniRT: %s", message.c_str());
        if (_aovBindingsValid) {
            std::lock_guard<std::mutex> lock(_statsMutex);
            _stats.error = message;
        }
        _aovBindingsValid = false;
    };

    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        // By the time the attachment gets here, there should be a bound output buffer.
        HdRenderBuffer* rb = _aovBindings[i].renderBuffer;
        if (rb == nullptr) {
            TF_WARN("HoudiniRT: Aov '%s' doesn't have any renderbuffer bound",
                    _aovNames[i].name.GetText());
            _aovBindingsValid = false;
            continue;
        }
        if (rb->GetFormat() == HdFormatInvalid) {
            TF_WARN("HoudiniRT: Aov '%s' is bound to a render buffer that was never allocated",
                    _aovNames[i].name.GetText());
            _aovBindingsValid = false;
            continue;
        }
        const TfToken& name = _aovBindings[i].aovName;
        if (HdRTAovFormat(name) == HdFormatInvalid) {
            invalid(TfStringPrintf("unsupported requested AOV '%s'; no image will be published", name.GetText()));
            continue;
        }
        if (!HdRTAovSupportsFormat(name, rb->GetFormat())) {
            invalid(TfStringPrintf("AOV '%s' has unsupported format %d; no image will be published",
                                   name.GetText(), int(rb->GetFormat())));
            continue;
        }
        if (_width == 0 && _height == 0) {
            _width = rb->GetWidth();
            _height = rb->GetHeight();
        } else if (_width != rb->GetWidth() || _height != rb->GetHeight()) {
            TF_WARN("HoudiniRT: render buffers have inconsistent sizes (%ux%u and %ux%u)",
                    _width, _height, rb->GetWidth(), rb->GetHeight());
            _aovBindingsValid = false;
        }
    }

    return _aovBindingsValid;
}

std::unique_ptr<HdRTRenderBuffer>
HdRTRenderer::_CreateClearedAov(size_t i)
{
    auto staging = static_cast<HdRTRenderBuffer*>(
        _aovBindings[i].renderBuffer)->CreateStagingBuffer();
    if (!staging)
        return nullptr;
    HdRTRenderBuffer* rb = staging.get();
    if (!_aovBindings[i].clearValue.IsEmpty()) {
        if (HdGetComponentFormat(rb->GetFormat()) == HdFormatInt32) {
            int clearValue[4] = { 0, 0, 0, 0 };
            if (_aovBindings[i].clearValue.IsHolding<int>()) {
                clearValue[0] = _aovBindings[i].clearValue.UncheckedGet<int>();
            }
            rb->Clear(1, clearValue);
        } else {
            float clearValue[4];
            size_t n = _ClearAsFloats(_aovBindings[i].clearValue, clearValue);
            if (n == 3 && _aovNames[i].name == HdAovTokens->color) {
                n = 4;   // a colour given without alpha is opaque
            }
            if (n > 0) {
                rb->Clear(n, clearValue);
            }
        }
    }
    return staging;
}

void
HdRTRenderer::MarkAovBuffersUnconverged()
{
    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        if (_aovBindings[i].renderBuffer) {
            static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer)->SetConverged(false);
        }
    }
}

void
HdRTRenderer::_MarkAovBuffersConverged()
{
    std::lock_guard<std::mutex> lock(_requestMutex);
    if (_Superseded()) {
        return;
    }
    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        if (_aovBindings[i].renderBuffer) {
            static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer)->SetConverged(true);
        }
    }
    _completedGeneration.store(_activeRequest->generation);
}

void
HdRTRenderer::_Fail(const std::string& what)
{
    HdRTLog("ERROR: %s", what.c_str());
    {
        std::lock_guard<std::mutex> lock(_statsMutex);
        _stats.error = what;
    }
    // Nothing more will be written, so stop the application waiting for it.
    _MarkAovBuffersConverged();
}

// Publish resolved beauty plus deterministic surface guides from the same generation. The GPU image
// covers the data window, row 0 at the bottom; (x0,y0) locates it in the full render buffer.
bool
HdRTRenderer::_WriteAovs(HdRenderThread* renderThread, int completedSamples, unsigned int x0, unsigned int y0,
                        unsigned int w, unsigned int h, std::string* error)
{
    if (completedSamples <= 0 || _Superseded() || renderThread->IsStopRequested()) {
        return true;
    }
    const bool resolved = _gpu.Resolve(uint32_t(completedSamples), error);
    _CaptureFrameStats();
    if (!resolved)
        return false;
    if (_Superseded() || renderThread->IsStopRequested())
        return true;
    const float* pixelsResolved = _gpu.ResolvedPixels();
    const HdRTVkGuide* guides = _gpu.ResolvedGuides();
    if (!pixelsResolved) {
        *error = "GPU resolve returned no image";
        return false;
    }
    std::vector<std::unique_ptr<HdRTRenderBuffer>> staged;
    staged.reserve(_aovBindings.size());
    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        staged.push_back(_CreateClearedAov(i));
        if (!staged.back()) {
            *error = "could not stage the current render buffer";
            return false;
        }
        if (_aovBindings[i].aovName != HdAovTokens->color) {
            if (!guides) {
                *error = "GPU resolve returned no requested surface guides";
                return false;
            }
            HdRTRenderBuffer* buffer = staged.back().get();
            for (unsigned int y = 0; y < h; ++y)
                for (unsigned int x = 0; x < w; ++x)
                    HdRTWriteGuide(buffer, GfVec3i(int(x0+x), int(y0+y), 1),
                                   _aovBindings[i].aovName, guides[size_t(y)*w+x]);
            continue;
        }
        HdRTRenderBuffer *rb = staged.back().get();
        if (HdGetComponentFormat(rb->GetFormat()) == HdFormatInt32) {
            continue;
        }
        void* mapped = rb->Map();
        if (rb->GetFormat() == HdFormatFloat32Vec4) {
            // GPU resolve already divided RGB and coverage by the sample count.
            float* pixels = static_cast<float*>(mapped);
            for (unsigned int y = 0; y < h; ++y) {
                const float* src = pixelsResolved + size_t(y) * w * 4;
                float* dst = pixels + (size_t(y0 + y) * rb->GetWidth() + x0) * 4;
                std::memcpy(dst, src, size_t(w) * 4 * sizeof(float));
            }
        } else {
            // Any other float format: convert pixel by pixel.
            for (unsigned int y = 0; y < h; ++y) {
                const float* row = pixelsResolved + size_t(y) * w * 4;
                for (unsigned int x = 0; x < w; ++x) {
                    const float value[4] = { row[4 * x], row[4 * x + 1], row[4 * x + 2], row[4 * x + 3] };
                    rb->Write(GfVec3i(int(x0 + x), int(y0 + y), 1), 4, value);
                }
            }
        }
        rb->Unmap();
    }
    std::unique_lock<std::mutex> requestLock(_requestMutex);
    if (!_Superseded() && !renderThread->IsStopRequested()) {
        // Convert outside the handoff lock; only constant-time storage swaps publish pixels.
        // Staging owners outlive this lock, so their retired image storage is freed afterwards.
        for (size_t i = 0; i < staged.size(); ++i) {
            if (!static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer)->PublishFrom(*staged[i])) {
                *error = "render-buffer storage changed before publication";
                return false;
            }
        }
        std::lock_guard<std::mutex> statsLock(_statsMutex);
        const bool first = _stats.presentedGeneration != _activeRequest->generation;
        if (first) {
            _stats.firstImageMilliseconds = std::chrono::duration<double, std::milli>(
                std::chrono::steady_clock::now() - _activeRequest->observedAt).count();
        }
        _stats.presentedGeneration = _activeRequest->generation;
        _stats.presentedSamples = completedSamples;
        _unpresentedEdits = HdRTVkEditStats();
        HDRT_DEBUG("present renderer=%p generation=%llu scene=%llu samples=%d first_image_ms=%.3f trace_submits=%llu resolve_submits=%llu readback_bytes=%llu time_s=%.9f",
                   static_cast<void*>(this), (unsigned long long)_activeRequest->generation,
                   (unsigned long long)(_scene ? _scene->contentVersion : 0), completedSamples,
                   _stats.firstImageMilliseconds, (unsigned long long)_stats.frame.traceSubmits,
                   (unsigned long long)_stats.frame.resolveSubmits, (unsigned long long)_stats.frame.readbackBytes,
                   _MonotonicSeconds());
    }
    return true;
}

void
HdRTRenderer::Render(HdRenderThread *renderThread)
{
    // Keep the callback available for new requests while converged. StartRender is a no-op while
    // rendering; returning between requests could otherwise lose a concurrent app-thread wakeup.
    uint64_t processed = 0;
    while (!renderThread->IsStopRequested()) {
        if (renderThread->IsPauseRequested())
            _AcknowledgePause();
        std::shared_ptr<const HdRTRenderRequest> request;
        {
            std::unique_lock<std::mutex> lock(_requestMutex);
            _requestReady.wait_for(lock, std::chrono::milliseconds(2), [&] {
                return _requested && _requested->generation != processed;
            });
            if (_requested && _requested->generation != processed) {
                request = _requested;
            }
        }
        if (!request || renderThread->IsStopRequested()) {
            continue;
        }
        _activeRequest = std::move(request);
        processed = _activeRequest->generation;
        SetConfig(_activeRequest->config);
        SetScene(_activeRequest->scene);
        SetCamera(_activeRequest->view, _activeRequest->projection);
        SetDataWindow(_activeRequest->dataWindow);
        SetAovBindings(_activeRequest->aovs);
        _RenderFrame(renderThread);
    }
}

void
HdRTRenderer::_RenderFrame(HdRenderThread *renderThread)
{
    const auto t0 = std::chrono::steady_clock::now();
    const int batchLimit = _BatchLimit();
    _completedSamples.store(0);
    {
        std::lock_guard<std::mutex> lock(_statsMutex);
        _stats.seed = _config.seed;
        _stats.activeGeneration = _activeRequest->generation;
        _stats.batchLimit = batchLimit;
        _stats.spp = _config.spp;
        _stats.bounces = _config.bounces;
        _stats.completedSamples = 0;
        _stats.settingsSource = _config.source;
        _stats.error.clear();
        _stats.renderSeconds = 0.0;
        _stats.edits = _unpresentedEdits;
        _stats.editMilliseconds = 0.0;
    }

    while (renderThread->IsPauseRequested() && !renderThread->IsStopRequested() && !_Superseded()) {
        _AcknowledgePause();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    if (renderThread->IsStopRequested() || _Superseded()) {
        return;
    }

    if (!_ValidateAovBindings()) {
        std::string reason;
        {
            std::lock_guard<std::mutex> lock(_statsMutex);
            reason = _stats.error;
        }
        // Terminate this request with a visible failure, without publishing a
        // clear-only image or claiming a current presented generation.
        _Fail(reason.empty() ? "could not validate the AOV bindings; no image was published" : reason);
        return;
    }
    if (_aovBindings.empty() || _width == 0 || _height == 0) {
        _MarkAovBuffersConverged();
        return;
    }

    // The data window is y-down; render buffers are stored bottom row first. Flip it (as hdEmbree does).
    const int dwW = _dataWindow.GetWidth();
    const int dwH = _dataWindow.GetHeight();
    const int dwX0 = _dataWindow.GetMinX();
    const int dwY0 = int(_height) - (_dataWindow.GetMaxY() + 1);
    if (dwW <= 0 || dwH <= 0 || dwX0 < 0 || dwY0 < 0 ||
        dwX0 + dwW > int(_width) || dwY0 + dwH > int(_height)) {
        _Fail(TfStringPrintf("the data window (%d, %d) %dx%d does not fit the %ux%u render buffers",
                             _dataWindow.GetMinX(), _dataWindow.GetMinY(), dwW, dwH, _width, _height));
        return;
    }

    // ---- GPU: created on the first render, and again only if what it was built with has changed ----
    std::string error;
    HdRTVkOptions options;
    options.spvPath = _spvPath;
    options.validation = _config.validation;
    options.allowSoftwareDevice = _config.allowSoftwareDevice;
    if (!_gpu.IsReady() || options != _gpuOptions) {
        if (_gpuInitFailed && options == _gpuOptions) {
            _Fail("the GPU could not be initialised earlier in this session; not retrying");
            return;
        }
        _gpuOptions = options;
        const bool ok = _gpu.Init(options, &error);
        // Init destroys the previous accumulation, even when replacement fails.
        _frameGeneration = 0;
        _frameCompletedSamples = 0;
        _CaptureFrameStats();
        _gpuInitFailed = !ok;
        {
            std::lock_guard<std::mutex> lock(_statsMutex);
            _stats.device = _gpu.DeviceName();
            _stats.validationActive = ok && _gpu.ValidationActive();
        }
        if (!ok) {
            _Fail(error);
            return;
        }
        if (_gpu.ValidationActive()) {
            HdRTLog("validation layer active");
        }
        _uploadedScene.reset();   // a fresh device holds no scene
    }

    // ---- scene: built on the render thread whenever the render pass handed over a new one --------------
    // (Acceleration structures are never built in Sync or Execute: CLAUDE.md:30.)
    // A paused render submits no GPU work: the scene upload waits for Resume (Houdini pauses on edits, and a
    // pause outlives StopRender/StartRender). Hold here exactly as the pass loop does, then honour a stop.
    while (renderThread->IsPauseRequested() && !renderThread->IsStopRequested() && !_Superseded()) {
        _AcknowledgePause();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    if (renderThread->IsStopRequested() || _Superseded()) {
        // Stopped before anything was traced: the buffers keep what they hold, and this render is over.
        return;
    }
    if (_scene != _uploadedScene) {
        const auto s0 = std::chrono::steady_clock::now();
        static const HdRTWorldScene empty;
        const HdRTWorldScene& w = _scene ? *_scene : empty;
        std::vector<HdRTVkMesh> meshes;
        meshes.reserve(w.meshes.size());
        for (const auto& entry : w.meshes) {
            const HdRTMeshData& data = *entry.second;
            HdRTVkMesh mesh;
            mesh.id = entry.first.GetString();
            mesh.geometryVersion = data.geometryVersion;
            mesh.topologyVersion = data.topologyVersion;
            mesh.normalVersion = data.normalVersion;
            mesh.cornerNormals = !data.cornerNormals.empty();
            mesh.vertexCount = data.points.size();
            mesh.triangleCount = data.triangles.size();
            static_assert(sizeof(GfVec3f) == 3 * sizeof(float), "packed GPU xyz input");
            static_assert(sizeof(GfVec3i) == 3 * sizeof(uint32_t), "packed GPU triangle input");
            mesh.positions = reinterpret_cast<const float*>(data.points.cdata());
            const VtVec3fArray& normals = mesh.cornerNormals ? data.cornerNormals : data.normals;
            mesh.normals = reinterpret_cast<const float*>(normals.cdata());
            mesh.normalCount = normals.size();
            mesh.faceIds = data.faceIds.cdata();
            mesh.primId = data.primId;
            mesh.indices = reinterpret_cast<const uint32_t*>(data.triangles.cdata());
            for (int r = 0; r < 3; ++r) {
                for (int c = 0; c < 4; ++c) {
                    mesh.transform[r * 4 + c] = float(data.transform[c][r]);
                }
                mesh.albedo[r] = data.albedo[r];
            }
            mesh.visible = data.visible;
            meshes.push_back(std::move(mesh));
        }
        HdRTVkEditStats edits;
        if (!_gpu.SetMeshes(meshes, &edits, &error)) {
            _uploadedScene.reset(); // SetMeshes failure invalidates the GPU's scene caches.
            _Fail(error);
            return;
        }
        if (!_gpu.SetLights(w.lights, &error)) {
            _uploadedScene.reset();
            _Fail(error);
            return;
        }
        {
            std::lock_guard<std::mutex> lock(_statsMutex);
            _unpresentedEdits.blasBuilds += edits.blasBuilds;
            _unpresentedEdits.blasRefits += edits.blasRefits;
            _unpresentedEdits.tlasBuilds += edits.tlasBuilds;
            _unpresentedEdits.tlasUpdates += edits.tlasUpdates;
            _unpresentedEdits.geometryUploadBytes += edits.geometryUploadBytes;
            _unpresentedEdits.tableUploadBytes += edits.tableUploadBytes;
            _stats.edits = _unpresentedEdits;
            _stats.sceneMemory = "geometry=device_local;index=device_local;normal=device_local;table=device_local;staging=host_visible;readback=host_visible";
        }
        _uploadedScene = _scene;
        HDRT_DEBUG("scene uploaded: %zu vertices, %zu triangles in %.3f s", w.VertexCount(), w.TriangleCount(),
                   std::chrono::duration<double>(std::chrono::steady_clock::now() - s0).count());
    }

    HdRTLog("seed=%d spp=%d device=%s", _config.seed, _config.spp, _gpu.DeviceName().c_str());
    HdRTLog("bounces=%d, %dx%d, values from %s; scene: %zu triangles, dome (%g, %g, %g)",
            _config.bounces, dwW, dwH, _config.source.c_str(), _scene ? _scene->TriangleCount() : size_t(0),
            _scene ? double(_scene->dome[0]) : 0.0, _scene ? double(_scene->dome[1]) : 0.0,
            _scene ? double(_scene->dome[2]) : 0.0);

    HdRTLog("render thread start");

    HdRTVkFrame frame;
    frame.width = uint32_t(dwW);
    frame.height = uint32_t(dwH);
    _CopyMatrix(_inverseViewMatrix, frame.invView);
    _CopyMatrix(_inverseProjMatrix, frame.invProj);
    frame.orthographic = (std::round(_projMatrix[3][3]) == 1.0);
    if (_scene) {
        frame.dome[0] = _scene->dome[0];
        frame.dome[1] = _scene->dome[1];
        frame.dome[2] = _scene->dome[2];
    }
    frame.bounces = uint32_t(_config.bounces);
    frame.seed = uint32_t(_config.seed);
    frame.guides = std::any_of(_aovBindings.begin(), _aovBindings.end(),
        [](const HdRenderPassAovBinding& binding) { return binding.aovName != HdAovTokens->color; });

    bool failed = false;
    int completed = 0;
    int written = 0;
    while (renderThread->IsPauseRequested() && !renderThread->IsStopRequested() && !_Superseded()) {
        _AcknowledgePause();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    if (_Superseded() || renderThread->IsStopRequested()) {
        return;
    }
    const bool began = _gpu.BeginFrame(frame, &error);
    _frameGeneration = began ? _activeRequest->generation : 0;
    _frameCompletedSamples = 0;
    _CaptureFrameStats();
    failed = !began;
    const double setupSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    // One in-flight batch at a time. Eight milliseconds is an adaptive target,
    // not a hard GPU scheduling bound; a single slow sample can exceed it.
    auto lastWrite = std::chrono::steady_clock::now() - std::chrono::seconds(1);
    uint32_t nextBatch = 1; // every request publishes its first sample promptly
    double secondsPerSample = 0.0;
    while (!failed && completed < _config.spp) {
        // Pause point: hold here, without burning the GPU, until resumed or stopped. A paused image
        // shows everything accumulated so far, so bring the buffers up to date before waiting.
        while (renderThread->IsPauseRequested()) {
            if (renderThread->IsStopRequested() || _Superseded()) {
                break;
            }
            if (written < completed) {
                if (!_WriteAovs(renderThread, completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH), &error)) {
                    failed = true;
                    break;
                }
                written = completed;
            }
            _AcknowledgePause();
            std::this_thread::sleep_for(std::chrono::milliseconds(2));
        }
        // Cancellation point.
        if (failed || renderThread->IsStopRequested() || _Superseded()) {
            break;
        }

        const uint32_t first = uint32_t(completed);
        const uint32_t count = std::min(nextBatch, uint32_t(_config.spp - completed));
        if (!_gpu.RenderBatch(first, count, &error)) {
            _CaptureFrameStats();
            failed = true;
            break;
        }
        completed += int(count);
        _frameCompletedSamples = completed;
        _completedSamples.store(completed);
        _CaptureFrameStats();
        const HdRTVkFrameStats batch = _gpu.FrameStats();
        if (first == 0 && _gpu.HasScene()) {
            const double observed = std::chrono::duration<double>(
                _activeRequest->observedAt.time_since_epoch()).count();
            std::lock_guard<std::mutex> lock(_statsMutex);
            _stats.editMilliseconds = std::max(0.0, (_gpu.LastPassSubmitSeconds() - observed) * 1000.0);
        }
        const double cost = batch.lastBatchSeconds / double(count);
        if (std::isfinite(cost) && cost > 0.0) {
            secondsPerSample = secondsPerSample == 0.0 ? cost : 0.75 * secondsPerSample + 0.25 * cost;
            const double desired = std::clamp(0.008 / std::max(cost, secondsPerSample), 1.0, double(batchLimit));
            // Grow cautiously after a cheap sample; shrink immediately after a slow batch.
            nextBatch = std::min(count * 2, uint32_t(desired));
        } else {
            nextBatch = std::min(count * 2, uint32_t(batchLimit));
        }
        HDRT_DEBUG("batch renderer=%p generation=%llu first=%u count=%u completed=%d trace_submits=%llu resolve_submits=%llu readback_bytes=%llu batch_ms=%.3f time_s=%.9f",
                   static_cast<void*>(this), (unsigned long long)_activeRequest->generation, first, count, completed,
                   (unsigned long long)batch.traceSubmits, (unsigned long long)batch.resolveSubmits,
                   (unsigned long long)batch.readbackBytes, batch.lastBatchSeconds * 1000.0, _MonotonicSeconds());

        const auto now = std::chrono::steady_clock::now();
        if (completed == _config.spp || now - lastWrite >= std::chrono::milliseconds(33)) {
            if (!_WriteAovs(renderThread, completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH), &error)) {
                failed = true;
                break;
            }
            written = completed;
            lastWrite = now;
        }
    }
    // Stopped part-way: leave the buffers holding everything that was accumulated.
    if (!failed && !_Superseded() && !renderThread->IsStopRequested() && written < completed) {
        failed = !_WriteAovs(renderThread, completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH), &error);
    }

    const double seconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    {
        std::lock_guard<std::mutex> lock(_statsMutex);
        _stats.completedSamples = completed;
        _stats.renderSeconds = seconds;
        _stats.validationErrors = _gpu.ValidationErrors();
    }

    if (failed) {
        _Fail(error);
    } else if (!_Superseded() && !renderThread->IsStopRequested() && completed == _config.spp) {
        _MarkAovBuffersConverged();
    }
    HDRT_DEBUG("render finished: %d of %d samples per pixel in %.3f s (%.3f s of that before the first pass)",
               completed, _config.spp, seconds, setupSeconds);
    HdRTLog("render thread stop");
}

void
HdRTRenderer::Shutdown()
{
    _gpu.Destroy();
}

HdRTRenderStats
HdRTRenderer::GetStats() const
{
    std::lock_guard<std::mutex> lock(_statsMutex);
    HdRTRenderStats s = _stats;
    s.completedSamples = _completedSamples.load();
    s.sceneGeneration = _requestedGeneration.load();
    s.currentImage = s.presentedGeneration == s.sceneGeneration && s.presentedSamples > 0;
    return s;
}

PXR_NAMESPACE_CLOSE_SCOPE
