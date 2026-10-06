//
// Copyright 2016 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderPass.h. See NOTICE.md in this directory.
//
#ifndef HDRT_RENDER_PASS_H
#define HDRT_RENDER_PASS_H

#include "pxr/pxr.h"

#include "pxr/base/gf/matrix4d.h"
#include "pxr/base/gf/rect2i.h"
#include "pxr/imaging/hd/aov.h"
#include "pxr/imaging/hd/renderPass.h"
#include "pxr/imaging/hd/renderThread.h"

#include "renderBuffer.h"
#include "renderer.h"
#include "settings.h"

#include <atomic>

PXR_NAMESPACE_OPEN_SCOPE

/// \class HdRTRenderPass
///
/// Hydra calls _Execute on the app thread every time it wants the image brought up to date. Execute never
/// renders and never waits for a render: it compares what Hydra wants now (camera, window, AOV buffers,
/// settings) with what the background render was started with, and only when something differs does it
/// stop the render thread, hand over the new state and start a fresh render.
///
class HdRTRenderPass final : public HdRenderPass
{
public:
    HdRTRenderPass(HdRenderIndex *index,
                   HdRprimCollection const &collection,
                   HdRenderThread *renderThread,
                   HdRTRenderer *renderer,
                   std::atomic<int> *sceneVersion);

    ~HdRTRenderPass() override;

    // -----------------------------------------------------------------------
    // HdRenderPass API

    /// True once every bound AOV buffer holds its final value.
    bool IsConverged() const override;

protected:

    // -----------------------------------------------------------------------
    // HdRenderPass API

    void _Execute(HdRenderPassStateSharedPtr const& renderPassState,
                  TfTokenVector const &renderTags) override;

    void _MarkCollectionDirty() override {}

private:
    // A handle to the render thread.
    HdRenderThread *_renderThread;

    // A handle to the delegate's renderer.
    HdRTRenderer *_renderer;

    // A reference to the delegate's scene version.
    std::atomic<int> *_sceneVersion;

    // The last scene version we rendered with.
    int _lastSceneVersion;

    // The last settings version we rendered with, and the furnace parameters it resolved to.
    int _lastSettingsVersion;
    HdRTFurnaceConfig _config;

    // The pixels written to. Like viewport in OpenGL,
    // but coordinates are y-Down.
    GfRect2i _dataWindow;

    // The view matrix: world space to camera space
    GfMatrix4d _viewMatrix;
    // The projection matrix: camera space to NDC space (with
    // respect to the data window).
    GfMatrix4d _projMatrix;

    // The list of aov buffers this renderpass should write to.
    HdRenderPassAovBindingVector _aovBindings;

    // If no attachments are provided, provide an anonymous renderbuffer for
    // color and depth output.
    HdRTRenderBuffer _colorBuffer;
    HdRTRenderBuffer _depthBuffer;

    // Counts Execute calls, for the debug trace only.
    int _executeCount;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDER_PASS_H
