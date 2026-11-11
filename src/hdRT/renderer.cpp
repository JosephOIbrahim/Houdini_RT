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
#include <cmath>
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
}

HdRTRenderer::~HdRTRenderer()
{
    Shutdown();
}

void
HdRTRenderer::SetShaderPath(const std::string& spvPath)
{
    _spvPath = spvPath;
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

void
HdRTRenderer::Clear()
{
    if (!_ValidateAovBindings()) {
        return;
    }

    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        HdRTRenderBuffer *rb =
            static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer);

        if (!_aovBindings[i].clearValue.IsEmpty()) {
            rb->Map();
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
            rb->Unmap();
        }
        rb->SetConverged(false);
    }
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
    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        if (_aovBindings[i].renderBuffer) {
            static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer)->SetConverged(true);
        }
    }
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

// Writes the running average into the bound colour buffer(s). The GPU image covers the data window, row 0
// at the bottom; (x0, y0) is where that window starts inside the render buffer, whose row 0 is also the
// bottom. Other AOVs keep their clear value: depth, ids and normals arrive with real geometry in step 4.
void
HdRTRenderer::_WriteAovs(int completedSamples, unsigned int x0, unsigned int y0, unsigned int w, unsigned int h)
{
    const float* sums = _gpu.Accumulation();
    if (!sums || completedSamples <= 0) {
        return;
    }
    const float inv = 1.0f / float(completedSamples);

    for (size_t i = 0; i < _aovBindings.size(); ++i) {
        if (_aovNames[i].name != HdAovTokens->color) {
            continue;
        }
        HdRTRenderBuffer *rb =
            static_cast<HdRTRenderBuffer*>(_aovBindings[i].renderBuffer);
        if (HdGetComponentFormat(rb->GetFormat()) == HdFormatInt32) {
            continue;
        }
        void* mapped = rb->Map();
        if (rb->GetFormat() == HdFormatFloat32Vec4) {
            // The format this delegate asks for: scale whole rows straight into the buffer.
            float* pixels = static_cast<float*>(mapped);
            for (unsigned int y = 0; y < h; ++y) {
                const float* src = sums + size_t(y) * w * 4;
                float* dst = pixels + (size_t(y0 + y) * rb->GetWidth() + x0) * 4;
                for (size_t c = 0; c < size_t(w) * 4; ++c) {
                    dst[c] = src[c] * inv;
                }
            }
        } else {
            // Any other float format: convert pixel by pixel.
            for (unsigned int y = 0; y < h; ++y) {
                const float* row = sums + size_t(y) * w * 4;
                for (unsigned int x = 0; x < w; ++x) {
                    const float value[4] = { row[4 * x] * inv, row[4 * x + 1] * inv,
                                             row[4 * x + 2] * inv, row[4 * x + 3] * inv };
                    rb->Write(GfVec3i(int(x0 + x), int(y0 + y), 1), 4, value);
                }
            }
        }
        rb->Unmap();
    }
}

void
HdRTRenderer::Render(HdRenderThread *renderThread)
{
    const auto t0 = std::chrono::steady_clock::now();
    _completedSamples.store(0);
    {
        std::lock_guard<std::mutex> lock(_statsMutex);
        _stats.seed = _config.seed;
        _stats.spp = _config.spp;
        _stats.bounces = _config.bounces;
        _stats.completedSamples = 0;
        _stats.settingsSource = _config.source;
        _stats.error.clear();
        _stats.renderSeconds = 0.0;
    }

    if (!_ValidateAovBindings()) {
        // Nothing can be rendered. Mark the AOVs converged so that the application stops waiting.
        _Fail("could not validate the AOV bindings; nothing will be rendered");
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
    options.openFace = _config.openFace;
    if (!_gpu.IsReady() || options != _gpuOptions) {
        if (_gpuInitFailed && options == _gpuOptions) {
            _Fail("the GPU could not be initialised earlier in this session; not retrying");
            return;
        }
        _gpuOptions = options;
        const bool ok = _gpu.Init(options, &error);
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
        if (_config.openFace >= 0) {
            HdRTLog("DEBUG BUILD OF THE SCENE: wall %d of the box is left out (HOUDINIRT_DEBUG_OPEN_FACE)", _config.openFace);
        }
    }

    HdRTLog("seed=%d spp=%d device=%s", _config.seed, _config.spp, _gpu.DeviceName().c_str());
    HdRTLog("bounces=%d, %dx%d, values from %s; test scene: furnace rho=%g emission=(%g, %g, %g)",
            _config.bounces, dwW, dwH, _config.source.c_str(),
            double(_config.rho), double(_config.emission[0]), double(_config.emission[1]), double(_config.emission[2]));

    HdRTLog("render thread start");

    HdRTVkFrame frame;
    frame.width = uint32_t(dwW);
    frame.height = uint32_t(dwH);
    _CopyMatrix(_inverseViewMatrix, frame.invView);
    _CopyMatrix(_inverseProjMatrix, frame.invProj);
    frame.orthographic = (std::round(_projMatrix[3][3]) == 1.0);
    frame.rho = _config.rho;
    frame.emission[0] = _config.emission[0];
    frame.emission[1] = _config.emission[1];
    frame.emission[2] = _config.emission[2];
    frame.bounces = uint32_t(_config.bounces);
    frame.seed = uint32_t(_config.seed);

    bool failed = false;
    int completed = 0;
    int written = 0;
    if (!_gpu.BeginFrame(frame, &error)) {
        failed = true;
    }
    const double setupSeconds = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    // Each pass adds one sample per pixel; the image is converged after `spp` passes. The running average
    // is copied to the render buffers about thirty times a second, and always after the last pass.
    auto lastWrite = std::chrono::steady_clock::now() - std::chrono::seconds(1);
    for (int i = 0; !failed && i < _config.spp; ++i) {
        // Pause point: hold here, without burning the GPU, until resumed or stopped. A paused image
        // shows everything accumulated so far, so bring the buffers up to date before waiting.
        while (renderThread->IsPauseRequested()) {
            if (renderThread->IsStopRequested()) {
                break;
            }
            if (written < completed) {
                _WriteAovs(completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH));
                written = completed;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        // Cancellation point.
        if (renderThread->IsStopRequested()) {
            break;
        }

        if (!_gpu.RenderPass(uint32_t(i), &error)) {
            failed = true;
            break;
        }
        completed = i + 1;
        _completedSamples.store(completed);

        const auto now = std::chrono::steady_clock::now();
        if (completed == _config.spp || now - lastWrite >= std::chrono::milliseconds(33)) {
            _WriteAovs(completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH));
            written = completed;
            lastWrite = now;
        }
    }
    // Stopped part-way: leave the buffers holding everything that was accumulated.
    if (!failed && written < completed) {
        _WriteAovs(completed, unsigned(dwX0), unsigned(dwY0), unsigned(dwW), unsigned(dwH));
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
    } else {
        // Converged, or stopped: either way this render writes nothing more.
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
    return s;
}

PXR_NAMESPACE_CLOSE_SCOPE
