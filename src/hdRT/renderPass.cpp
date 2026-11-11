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
    , _colorBuffer(SdfPath::EmptyPath())
    , _depthBuffer(SdfPath::EmptyPath())
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
    // The renderer's bindings are what the render thread writes to: the application's AOV buffers, or
    // _colorBuffer and _depthBuffer when it gave none. Before the first Execute there are none at all.
    const HdRenderPassAovBindingVector& bound = _renderer->GetAovBindings();
    if (bound.empty()) {
        return false;
    }
    for (size_t i = 0; i < bound.size(); ++i) {
        if (bound[i].renderBuffer &&
            !bound[i].renderBuffer->IsConverged()) {
            return false;
        }
    }
    return true;
}

// The debug trace prints containers that have no stream operator of their own.
static std::string
_Describe(HdAovSettingsMap const& settings)
{
    std::string out = "{";
    for (const auto& kv : settings) {
        out += " " + kv.first.GetString() + "=" + TfStringify(kv.second);
    }
    return out + " }";
}

static std::string
_Describe(TfTokenVector const& tokens)
{
    std::string out = "[";
    for (const TfToken& t : tokens) {
        out += " " + t.GetString();
    }
    return out + " ]";
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
                         TfTokenVector const &renderTags)
{
    ++_executeCount;

    HdRTRenderDelegate *renderDelegate =
        static_cast<HdRTRenderDelegate*>(GetRenderIndex()->GetRenderDelegate());

    // Determine whether the scene has changed since the last time we rendered. If it has (or on the
    // first Execute), flatten what Hydra synced into one world-space mesh and hand it to the renderer.
    // This is CPU work only; the GPU upload happens on the render thread (CLAUDE.md:30).
    bool needStartRender = false;
    int currentSceneVersion = _sceneVersion->load();
    if (_lastSceneVersion != currentSceneVersion || !_sceneHandedOver) {
        needStartRender = true;
        _lastSceneVersion = currentSceneVersion;
        _sceneHandedOver = true;

        std::shared_ptr<const HdRTWorldScene> world =
            HdRTFlattenScene(renderDelegate->GetScene().Snapshot());
        const HdRTCensus& c = world->census;
        HDRT_DEBUG("scene v%d: %d meshes, %d triangles (%zu uploaded), box [%.6f %.6f %.6f .. %.6f %.6f %.6f], dome (%.6f, %.6f, %.6f)",
                   currentSceneVersion, c.meshes, c.triangles, world->TriangleCount(),
                   c.bboxMin[0], c.bboxMin[1], c.bboxMin[2], c.bboxMax[0], c.bboxMax[1], c.bboxMax[2],
                   double(world->dome[0]), double(world->dome[1]), double(world->dome[2]));
        _renderThread->StopRender();
        _renderer->SetScene(std::move(world));
    }

    // Likewise the render settings. The furnace parameters are worked out again whenever the settings
    // version moves, and a new render starts only if they really changed.
    int currentSettingsVersion = renderDelegate->GetRenderSettingsVersion();
    if (_lastSettingsVersion != currentSettingsVersion) {
        const bool first = (_lastSettingsVersion == 0);
        _lastSettingsVersion = currentSettingsVersion;

        const HdRTFurnaceConfig config = renderDelegate->ResolveFurnaceConfig();
        if (first || config != _config) {
            _renderThread->StopRender();
            _config = config;
            _renderer->SetConfig(_config);
            needStartRender = true;
        }
    }

    // Determine whether we need to update the renderer camera.
    const GfMatrix4d view = renderPassState->GetWorldToViewMatrix();
    const GfMatrix4d proj = renderPassState->GetProjectionMatrix();
    if (_viewMatrix != view || _projMatrix != proj) {
        _viewMatrix = view;
        _projMatrix = proj;

        _renderThread->StopRender();
        _renderer->SetCamera(_viewMatrix, _projMatrix);
        needStartRender = true;
    }

    const GfRect2i dataWindow = _GetDataWindow(renderPassState);

    if (_dataWindow != dataWindow) {
        _dataWindow = dataWindow;

        _renderThread->StopRender();
        _renderer->SetDataWindow(dataWindow);

        if (!renderPassState->GetFraming().IsValid()) {
            // Support clients that do not use the new framing API
            // and do not use AOVs.
            //
            // Note that we do not support the case of using the
            // new camera framing API without using AOVs.
            //
            const GfVec3i dimensions(_dataWindow.GetWidth(),
                                     _dataWindow.GetHeight(),
                                     1);

            _colorBuffer.Allocate(
                dimensions,
                HdFormatFloat32Vec4,
                /*multiSampled=*/false);

            _depthBuffer.Allocate(
                dimensions,
                HdFormatFloat32,
                /*multiSampled=*/false);
        }

        needStartRender = true;
    }

    // Determine whether we need to update the renderer AOV bindings.
    //
    // It's possible for the passed in bindings to be empty, but that's
    // never a legal state for the renderer, so if that's the case we add
    // a color and depth aov.
    //
    // If the renderer AOV bindings are empty, force a bindings update so that
    // we always get a chance to add color/depth on the first time through.
    HdRenderPassAovBindingVector aovBindings =
        renderPassState->GetAovBindings();
    if (_aovBindings != aovBindings || _renderer->GetAovBindings().empty()) {
        _aovBindings = aovBindings;

        _renderThread->StopRender();

        // A binding may name its buffer by path instead of by pointer; look those up.
        for (HdRenderPassAovBinding& binding : aovBindings) {
            if (!binding.renderBuffer && !binding.renderBufferId.IsEmpty()) {
                binding.renderBuffer = static_cast<HdRenderBuffer*>(
                    GetRenderIndex()->GetBprim(HdPrimTypeTokens->renderBuffer,
                                               binding.renderBufferId));
            }
        }

        if (aovBindings.empty()) {
            HdRenderPassAovBinding colorAov;
            colorAov.aovName = HdAovTokens->color;
            colorAov.renderBuffer = &_colorBuffer;
            colorAov.clearValue =
                VtValue(GfVec4f(0.0707f, 0.0707f, 0.0707f, 1.0f));
            aovBindings.push_back(colorAov);
            HdRenderPassAovBinding depthAov;
            depthAov.aovName = HdAovTokens->depth;
            depthAov.renderBuffer = &_depthBuffer;
            depthAov.clearValue = VtValue(1.0f);
            aovBindings.push_back(depthAov);
        }
        _renderer->SetAovBindings(aovBindings);
        // In general, the render thread clears aov bindings, but make sure
        // they are cleared initially on this thread.
        _renderer->Clear();
        needStartRender = true;
    }

    // Trace what Hydra handed over, but only when it leads to a new render.
    if (needStartRender && HdRTDebugEnabled()) {
        const GfVec4f vp = renderPassState->GetViewport();
        HDRT_DEBUG("Execute #%d starts a render: framing valid=%d viewport=(%g, %g, %g, %g) data window=(%d, %d) %dx%d",
                   _executeCount, int(renderPassState->GetFraming().IsValid()),
                   double(vp[0]), double(vp[1]), double(vp[2]), double(vp[3]),
                   _dataWindow.GetMinX(), _dataWindow.GetMinY(), _dataWindow.GetWidth(), _dataWindow.GetHeight());
        HDRT_DEBUG("  world to view = %s", TfStringify(view).c_str());
        HDRT_DEBUG("  projection    = %s", TfStringify(proj).c_str());
        for (const HdRenderPassAovBinding& b : _renderer->GetAovBindings()) {
            HDRT_DEBUG("  aov '%s' buffer %s %ux%u format %s multisampled=%d clear=%s settings=%s",
                       b.aovName.GetText(), b.renderBufferId.GetText(),
                       b.renderBuffer ? b.renderBuffer->GetWidth() : 0u,
                       b.renderBuffer ? b.renderBuffer->GetHeight() : 0u,
                       b.renderBuffer ? TfEnum::GetName(b.renderBuffer->GetFormat()).c_str() : "(no buffer)",
                       b.renderBuffer ? int(b.renderBuffer->IsMultiSampled()) : 0,
                       TfStringify(b.clearValue).c_str(), _Describe(b.aovSettings).c_str());
        }
        HDRT_DEBUG("  render tags = %s", _Describe(renderTags).c_str());
    }

    TF_VERIFY(!_renderer->GetAovBindings().empty(), "No aov bindings to render into");

    // Only start a new render if something in the scene has changed.
    if (needStartRender) {
        _renderer->MarkAovBuffersUnconverged();
        _renderThread->StartRender();
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
