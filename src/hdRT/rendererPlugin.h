//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/rendererPlugin.h. See NOTICE.md in this directory.
//
#ifndef HDRT_RENDERER_PLUGIN_H
#define HDRT_RENDERER_PLUGIN_H

#include "pxr/pxr.h"
#include "pxr/imaging/hd/rendererPlugin.h"

PXR_NAMESPACE_OPEN_SCOPE

///
/// \class HdRTRendererPlugin
///
/// The class Hydra loads when an application asks for the renderer whose plugInfo.json displayName is
/// "HoudiniRT". It only creates and destroys HdRTRenderDelegate objects.
///
class HdRTRendererPlugin final : public HdRendererPlugin
{
public:
    HdRTRendererPlugin() = default;

    HdRenderDelegate *CreateRenderDelegate() override;

    HdRenderDelegate *CreateRenderDelegate(
        HdRenderSettingsMap const& settingsMap) override;

    void DeleteRenderDelegate(HdRenderDelegate *renderDelegate) override;

    /// Loading the plugin is cheap and touches no GPU, so it is always supported. Whether the machine can
    /// trace rays is found out on the render thread, where a failure is reported instead of crashing the host.
    bool IsSupported(
        HdRendererCreateArgs const &rendererCreateArgs,
        std::string *reasonWhyNot = nullptr) const override;

private:
    HdRTRendererPlugin(const HdRTRendererPlugin&)             = delete;
    HdRTRendererPlugin &operator =(const HdRTRendererPlugin&) = delete;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDERER_PLUGIN_H
