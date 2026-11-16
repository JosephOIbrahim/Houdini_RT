//
// Copyright 2016 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderPass.cpp. Settings are resolved through the delegate, AOV bindings
// given by path are looked up in the render index, and a debug trace is added. See NOTICE.md.
//
#include "renderPass.h"

#include "log.h"
#include "renderDelegate.h"
#include "world.h"

#include "pxr/base/tf/enum.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/imaging/cameraUtil/framing.h"
#include "pxr/imaging/hd/renderIndex.h"
#include "pxr/imaging/hd/renderPassState.h"
#include "pxr/imaging/hd/tokens.h"

PXR_NAMESPACE_OPEN_SCOPE

HdRTRenderPass::HdRTRenderPass(HdRenderIndex *index,
                               HdRprimCollection const &collection,
                               HdRenderThread *renderThread,
                               HdRTRenderer *renderer,
                               std::atomic<int> *sceneVersion)
    : HdRenderPass(index, collection)
    , _renderThread(renderThread)
    , _renderer(renderer)
    , _sceneVersion(sceneVersion)
    , _lastSceneVersion(0)
    , _lastSettingsVersion(0)
    , _viewMatrix(1.0f) // == identity
    , _projMatrix(1.0f) // == identity
    , _aovBindings()
    , _colorBuffer(std::make_shared<HdRTRenderBuffer>(SdfPath::EmptyPath()))
    , _depthBuffer(std::make_shared<HdRTRenderBuffer>(SdfPath::EmptyPath()))
    , _executeCount(0)
{
}

HdRTRenderPass::~HdRTRenderPass()
{
    // Make sure the render thread's not running, in case it's writing
    // to _colorBuffer/_depthBuffer.
    _renderThread->StopRender();
}

bool
HdRTRenderPass::IsConverged() const
{
    return _renderer->IsConverged();
}

static
GfRect2i
_GetDataWindow(HdRenderPassStateSharedPtr const& renderPassState)
{
    const CameraUtilFraming &framing = renderPassState->GetFraming();
    if (framing.IsValid()) {
        return framing.dataWindow;
    } else {
        // For applications that use the old viewport API instead of
        // the new camera framing API. (husk 22 is one of them.)
        const GfVec4f vp = renderPassState->GetViewport();
        return GfRect2i(GfVec2i(0), int(vp[2]), int(vp[3]));
    }
}

void
HdRTRenderPass::_Execute(HdRenderPassStateSharedPtr const& renderPassState,
                         TfTokenVector const& renderTags)
{
    TF_UNUSED(renderTags);
    ++_executeCount;
    const auto observedAt = std::chrono::steady_clock::now();
    auto* delegate = static_cast<HdRTRenderDelegate*>(GetRenderIndex()->GetRenderDelegate());
    const bool first = !_sceneHandedOver;
    const int bufferVersion = _sceneVersion->load();
    const bool buffersChanged = first || bufferVersion != _lastSceneVersion;
    _lastSceneVersion = bufferVersion;
    bool changed = buffersChanged;

    const HdRTSceneSnapshot snapshot = delegate->GetScene().Snapshot();
    if (first || snapshot.contentVersion != _lastContentVersion) {
        _lastContentVersion = snapshot.contentVersion;
        _world = HdRTBuildScene(snapshot);
        _sceneHandedOver = true;
        changed = true;
        const HdRTCensus& c = _world->census;
        HDRT_DEBUG("scene version=%llu meshes=%d triangles=%d box=[%.6f %.6f %.6f .. %.6f %.6f %.6f]",
                   (unsigned long long)snapshot.contentVersion, c.meshes, c.triangles,
                   c.bboxMin[0], c.bboxMin[1], c.bboxMin[2], c.bboxMax[0], c.bboxMax[1], c.bboxMax[2]);
    }
    const int settingsVersion = delegate->GetRenderSettingsVersion();
    if (first || _lastSettingsVersion != settingsVersion) {
        _lastSettingsVersion = settingsVersion;
        const HdRTFurnaceConfig config = delegate->ResolveFurnaceConfig();
        if (first || config != _config) {
            _config = config;
            changed = true;
        }
    }
    const GfMatrix4d view = renderPassState->GetWorldToViewMatrix();
    const GfMatrix4d proj = renderPassState->GetProjectionMatrix();
    if (first || _viewMatrix != view || _projMatrix != proj) {
        _viewMatrix = view;
        _projMatrix = proj;
        changed = true;
    }
    const GfRect2i window = _GetDataWindow(renderPassState);
    const bool resized = first || window != _dataWindow;
    if (resized) {
        _dataWindow = window;
        changed = true;
    }
    const auto aovs = renderPassState->GetAovBindings();
    if (first || buffersChanged || _aovBindings != aovs || (aovs.empty() && resized)) {
        _aovBindings = aovs;
        _resolvedAovs = aovs;
        for (auto& binding : _resolvedAovs) {
            if (!binding.renderBuffer && !binding.renderBufferId.IsEmpty()) {
                binding.renderBuffer = static_cast<HdRenderBuffer*>(GetRenderIndex()->GetBprim(
                    HdPrimTypeTokens->renderBuffer, binding.renderBufferId));
            }
        }
        if (_resolvedAovs.empty()) {
            // Never resize buffers that an old request is writing. Its shared ownership keeps the
            // prior pair alive until the renderer consumes the replacement request.
            _colorBuffer = std::make_shared<HdRTRenderBuffer>(SdfPath::EmptyPath());
            _depthBuffer = std::make_shared<HdRTRenderBuffer>(SdfPath::EmptyPath());
            const GfVec3i dimensions(window.GetWidth(), window.GetHeight(), 1);
            _colorBuffer->Allocate(dimensions, HdFormatFloat32Vec4, false);
            _depthBuffer->Allocate(dimensions, HdFormatFloat32, false);
            HdRenderPassAovBinding color, depth;
            color.aovName = HdAovTokens->color;
            color.renderBuffer = _colorBuffer.get();
            color.clearValue = VtValue(GfVec4f(0.0f));
            depth.aovName = HdAovTokens->depth;
            depth.renderBuffer = _depthBuffer.get();
            depth.clearValue = VtValue(1.0f);
            _resolvedAovs = {color, depth};
        }
        changed = true;
    }

    if (changed) {
        HdRTRenderRequest request;
        request.config = _config;
        request.scene = _world;
        request.view = _viewMatrix;
        request.projection = _projMatrix;
        request.dataWindow = _dataWindow;
        request.aovs = _resolvedAovs;
        request.observedAt = observedAt;
        if (_aovBindings.empty()) {
            request.ownedBuffers = {_colorBuffer, _depthBuffer};
        }
        _renderer->Submit(std::move(request));
        HDRT_DEBUG("Execute #%d publishes a request; data window %dx%d", _executeCount,
                   _dataWindow.GetWidth(), _dataWindow.GetHeight());
    }
    // Safe wakeup when idle; while active the renderer observes the new generation itself.
    // Also restarts after render-buffer lifetime synchronization outside Execute.
    if (!_renderer->IsConverged()) {
        _renderThread->StartRender();
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
