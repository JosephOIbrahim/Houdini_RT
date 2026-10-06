//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/rendererPlugin.cpp. See NOTICE.md in this directory.
//
#include "rendererPlugin.h"

#include "renderDelegate.h"

#include "pxr/imaging/hd/rendererPluginRegistry.h"

PXR_NAMESPACE_OPEN_SCOPE

// Register the plugin with Hydra's renderer plugin system. The type name registered here,
// HdRTRendererPlugin, is the key in plugInfo.json and the id husk takes after -R.
TF_REGISTRY_FUNCTION(TfType)
{
    HdRendererPluginRegistry::Define<HdRTRendererPlugin>();
}

HdRenderDelegate*
HdRTRendererPlugin::CreateRenderDelegate()
{
    return new HdRTRenderDelegate();
}

HdRenderDelegate*
HdRTRendererPlugin::CreateRenderDelegate(
    HdRenderSettingsMap const& settingsMap)
{
    return new HdRTRenderDelegate(settingsMap);
}

void
HdRTRendererPlugin::DeleteRenderDelegate(HdRenderDelegate *renderDelegate)
{
    delete renderDelegate;
}

bool
HdRTRendererPlugin::IsSupported(
    HdRendererCreateArgs const & /* rendererCreateArgs */,
    std::string * /* reasonWhyNot */) const
{
    return true;
}

PXR_NAMESPACE_CLOSE_SCOPE
