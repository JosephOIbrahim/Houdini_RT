//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderParam.h. See NOTICE.md in this directory.
//
#ifndef HDRT_RENDER_PARAM_H
#define HDRT_RENDER_PARAM_H

#include "pxr/pxr.h"
#include "pxr/imaging/hd/renderDelegate.h"
#include "pxr/imaging/hd/renderThread.h"

#include <atomic>

PXR_NAMESPACE_OPEN_SCOPE

///
/// \class HdRTRenderParam
///
/// Passed to every prim during Sync(). Anything the render thread reads or writes (render buffers now,
/// meshes from step 4) must call AcquireSceneForEdit() before it changes, so an edit never races a render.
///
class HdRTRenderParam final : public HdRenderParam
{
public:
    HdRTRenderParam(HdRenderThread *renderThread, std::atomic<int> *sceneVersion)
        : _renderThread(renderThread), _sceneVersion(sceneVersion)
    {}

    /// Stop the current render (it blocks until the render thread is idle) and mark the scene as edited.
    /// The render pass sees the new version on its next Execute and starts a fresh render.
    void AcquireSceneForEdit()
    {
        _renderThread->StopRender();
        (*_sceneVersion)++;
    }

private:
    HdRenderThread *_renderThread;
    std::atomic<int> *_sceneVersion;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDER_PARAM_H
