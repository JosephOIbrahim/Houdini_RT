//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderDelegate.cpp. Embree, instancers and every light but the dome are
// removed; meshes and the dome are this project's HdRTMesh and HdRTLight; the renderer is its own Vulkan tracer. See NOTICE.md in this directory.
//
#include "renderDelegate.h"

#include "light.h"
#include "log.h"
#include "mesh.h"
#include "renderBuffer.h"
#include "renderParam.h"
#include "renderPass.h"

#include "pxr/base/gf/vec3f.h"
#include "pxr/base/gf/vec4f.h"
#include "pxr/base/plug/plugin.h"
#include "pxr/base/plug/thisPlugin.h"
#include "pxr/base/tf/stringUtils.h"
#include "pxr/imaging/hd/bprim.h"
#include "pxr/imaging/hd/camera.h"
#include "pxr/imaging/hd/instancer.h"
#include "pxr/imaging/hd/resourceRegistry.h"
#include "pxr/imaging/hd/rprim.h"
#include "pxr/imaging/hd/tokens.h"

#include <algorithm>
#include <functional>

PXR_NAMESPACE_OPEN_SCOPE

// Step 4: polygon meshes.
const TfTokenVector HdRTRenderDelegate::SUPPORTED_RPRIM_TYPES =
{
    HdPrimTypeTokens->mesh,
};

// Step 4: the camera and an untextured dome light.
const TfTokenVector HdRTRenderDelegate::SUPPORTED_SPRIM_TYPES =
{
    HdPrimTypeTokens->camera,
    HdPrimTypeTokens->domeLight,
};

const TfTokenVector HdRTRenderDelegate::SUPPORTED_BPRIM_TYPES =
{
    HdPrimTypeTokens->renderBuffer,
};

std::mutex HdRTRenderDelegate::_mutexResourceRegistry;
std::atomic_int HdRTRenderDelegate::_counterResourceRegistry;
HdResourceRegistrySharedPtr HdRTRenderDelegate::_resourceRegistry;

static void _RenderCallback(HdRTRenderer *renderer,
                            HdRenderThread *renderThread)
{
    renderer->Clear();
    renderer->Render(renderThread);
}

static void _DebugSetting(const char* where, TfToken const& key, VtValue const& value)
{
    HDRT_DEBUG("%s %s (%s) = %s", where, key.GetText(), value.GetTypeName().c_str(), TfStringify(value).c_str());
}

HdRTRenderDelegate::HdRTRenderDelegate()
    : HdRenderDelegate()
{
    _Initialize();
}

HdRTRenderDelegate::HdRTRenderDelegate(
    HdRenderSettingsMap const& settingsMap)
    : HdRenderDelegate(settingsMap)
{
    _delivered = settingsMap;
    if (HdRTDebugEnabled()) {
        HDRT_DEBUG("delegate created with %zu initial settings", settingsMap.size());
        for (const auto& kv : settingsMap) {
            _DebugSetting("  initial setting", kv.first, kv.second);
        }
    }
    _Initialize();
}

void
HdRTRenderDelegate::_Initialize()
{
    // What a settings panel can show. The defaults are deliberately not written into the settings map:
    // ResolveFurnaceConfig() needs to know which values the application really set.
    _settingDescriptors.resize(3);
    _settingDescriptors[0] = { "Samples Per Pixel",
        HdRTRenderSettingsTokens->spp, VtValue(int(HdRTFurnaceConfig().spp)) };
    _settingDescriptors[1] = { "Random Number Seed",
        HdRTRenderSettingsTokens->seed, VtValue(int(HdRTFurnaceConfig().seed)) };
    _settingDescriptors[2] = { "Diffuse Bounces",
        HdRTRenderSettingsTokens->bounces, VtValue(int(HdRTFurnaceConfig().bounces)) };

    // std::atomic does not default-initialize; do so here.
    _sceneVersion.store(0);
    _paused.store(false);

    // The compute kernel is a resource of this plugin: <plugin dir>/shaders/furnace.spv.
    std::string spvPath;
    if (const PlugPluginPtr plugin = PLUG_THIS_PLUGIN) {
        spvPath = PlugFindPluginResource(plugin, "shaders/furnace.spv");
        if (spvPath.empty()) {
            HdRTLog("ERROR: shaders/furnace.spv was not found under the plugin's resource path '%s'",
                    plugin->GetResourcePath().c_str());
        }
    } else {
        HdRTLog("ERROR: the plugin registry does not know a plugin named hdRT; the kernel cannot be located");
    }
    HDRT_DEBUG("kernel: %s", spvPath.c_str());
    _renderer.SetShaderPath(spvPath);

    // Passed to prims during Sync(); it gives them a handle on the render thread.
    _renderParam = std::make_shared<HdRTRenderParam>(
        &_renderThread, &_sceneVersion, &_scene);

    // Set the background render thread's rendering entrypoint to HdRTRenderer::Render.
    _renderThread.SetRenderCallback(
        std::bind(_RenderCallback, &_renderer, &_renderThread));
    // Start the background render thread. It idles until a render pass asks for a render;
    // no GPU object exists before that.
    _renderThread.StartThread();

    // Initialize one resource registry for all HoudiniRT delegates.
    std::lock_guard<std::mutex> guard(_mutexResourceRegistry);

    if (_counterResourceRegistry.fetch_add(1) == 0) {
        _resourceRegistry = std::make_shared<HdResourceRegistry>();
    }
}

HdRTRenderDelegate::~HdRTRenderDelegate()
{
    HDRT_DEBUG("delegate being destroyed");

    // Clean the resource registry only when it is the last HoudiniRT delegate.
    {
        std::lock_guard<std::mutex> guard(_mutexResourceRegistry);
        if (_counterResourceRegistry.fetch_sub(1) == 1) {
            _resourceRegistry.reset();
        }
    }

    // Stop the render thread first: nothing may be rendering while GPU objects are destroyed.
    _renderThread.StopThread();
    _renderer.Shutdown();
    _renderParam.reset();

    HDRT_DEBUG("delegate destroyed, GPU objects released");
}

void
HdRTRenderDelegate::SetRenderSetting(TfToken const& key, VtValue const& value)
{
    _DebugSetting("SetRenderSetting", key, value);
    {
        std::lock_guard<std::mutex> lock(_deliveredMutex);
        _delivered[key] = value;
    }
    HdRenderDelegate::SetRenderSetting(key, value);
}

HdRenderSettingDescriptorList
HdRTRenderDelegate::GetRenderSettingDescriptors() const
{
    return _settingDescriptors;
}

TfTokenVector
HdRTRenderDelegate::GetRenderSettingsNamespaces() const
{
    static const TfTokenVector namespaces = { TfToken("houdinirt") };
    return namespaces;
}

HdRTFurnaceConfig
HdRTRenderDelegate::ResolveFurnaceConfig() const
{
    HdRenderSettingsMap delivered;
    {
        std::lock_guard<std::mutex> lock(_deliveredMutex);
        delivered = _delivered;
    }
    return HdRTResolveFurnaceConfig(delivered);
}

HdRenderParam*
HdRTRenderDelegate::GetRenderParam() const
{
    return _renderParam.get();
}

void
HdRTRenderDelegate::CommitResources(HdChangeTracker *tracker)
{
}

TfTokenVector const&
HdRTRenderDelegate::GetSupportedRprimTypes() const
{
    return SUPPORTED_RPRIM_TYPES;
}

TfTokenVector const&
HdRTRenderDelegate::GetSupportedSprimTypes() const
{
    return SUPPORTED_SPRIM_TYPES;
}

TfTokenVector const&
HdRTRenderDelegate::GetSupportedBprimTypes() const
{
    return SUPPORTED_BPRIM_TYPES;
}

HdResourceRegistrySharedPtr
HdRTRenderDelegate::GetResourceRegistry() const
{
    return _resourceRegistry;
}

HdAovDescriptor
HdRTRenderDelegate::GetDefaultAovDescriptor(TfToken const& name) const
{
    if (name == HdAovTokens->color) {
        // Averaged on the GPU and written resolved, so not "multisampled" in Hydra's sense.
        return HdAovDescriptor(HdFormatFloat32Vec4, false,
                               VtValue(GfVec4f(0.0f)));
    } else if (name == HdAovTokens->normal || name == HdAovTokens->Neye) {
        return HdAovDescriptor(HdFormatFloat32Vec3, false,
                               VtValue(GfVec3f(-1.0f)));
    } else if (name == HdAovTokens->depth) {
        return HdAovDescriptor(HdFormatFloat32, false, VtValue(1.0f));
    } else if (name == HdAovTokens->cameraDepth) {
        return HdAovDescriptor(HdFormatFloat32, false, VtValue(0.0f));
    } else if (name == HdAovTokens->primId ||
               name == HdAovTokens->instanceId ||
               name == HdAovTokens->elementId) {
        return HdAovDescriptor(HdFormatInt32, false, VtValue(-1));
    }

    return HdAovDescriptor();
}

VtDictionary
HdRTRenderDelegate::GetRenderStats() const
{
    const HdRTRenderStats s = _renderer.GetStats();

    VtDictionary stats;
    stats[HdPerfTokens->numCompletedSamples.GetString()] = s.completedSamples;
    stats["percentDone"] = 100.0 * double(s.completedSamples) / double(std::max(1, s.spp));
    stats["totalClockTime"] = s.renderSeconds;
    stats["rendererName"] = std::string("HoudiniRT");
    // What this render actually used. verify/s3_delegate.sh compares hrt_seed with the seed it chose.
    stats["hrt_seed"] = s.seed;
    stats["hrt_spp"] = s.spp;
    // The bounce budget this render used; verify/s4_firstlight.sh compares it with the count it drew.
    stats["hrt_bounces"] = s.bounces;
    // The translation census of what Hydra synced; verify/s4_firstlight.sh compares it with its own census
    // of the stage. Counts are ints, the box is double (husk keeps both types in the EXR header).
    stats["hrt_mesh_count"] = s.census.meshes;
    stats["hrt_triangle_count"] = s.census.triangles;
    // No box (no geometry yet) is reported as no keys, never as a box collapsed at the origin.
    static const char* axes[3] = { "x", "y", "z" };
    for (int k = 0; k < 3 && s.census.hasBox; ++k) {
        stats[std::string("hrt_bbox_min_") + axes[k]] = s.census.bboxMin[k];
        stats[std::string("hrt_bbox_max_") + axes[k]] = s.census.bboxMax[k];
    }
    stats["hrt_device"] = s.device;
    stats["hrt_settings_source"] = s.settingsSource;
    stats["hrt_validation_active"] = s.validationActive;
    stats["hrt_validation_errors"] = s.validationErrors;
    if (!s.error.empty()) {
        stats["hrt_error"] = s.error;
    }
    return stats;
}

bool
HdRTRenderDelegate::IsPauseSupported() const
{
    return true;
}

bool
HdRTRenderDelegate::IsPaused() const
{
    return _paused.load();
}

bool
HdRTRenderDelegate::Pause()
{
    HDRT_DEBUG("Pause()");
    _renderThread.PauseRender();
    _paused.store(true);
    return true;
}

bool
HdRTRenderDelegate::Resume()
{
    HDRT_DEBUG("Resume()");
    _renderThread.ResumeRender();
    _paused.store(false);
    return true;
}

HdRenderPassSharedPtr
HdRTRenderDelegate::CreateRenderPass(HdRenderIndex *index,
                            HdRprimCollection const& collection)
{
    return HdRenderPassSharedPtr(new HdRTRenderPass(
        index, collection, &_renderThread, &_renderer, &_sceneVersion));
}

HdInstancer *
HdRTRenderDelegate::CreateInstancer(HdSceneDelegate *delegate,
                                    SdfPath const& id)
{
    // Instancing is not expanded yet: an instanced mesh renders once at its own transform (HdRTMesh warns).
    // A plain instancer keeps Hydra content.
    return new HdInstancer(delegate, id);
}

void
HdRTRenderDelegate::DestroyInstancer(HdInstancer *instancer)
{
    delete instancer;
}

HdRprim *
HdRTRenderDelegate::CreateRprim(TfToken const& typeId,
                                SdfPath const& rprimId)
{
    HDRT_DEBUG("CreateRprim %s %s", typeId.GetText(), rprimId.GetText());
    if (typeId == HdPrimTypeTokens->mesh) {
        return new HdRTMesh(rprimId);
    }
    TF_CODING_ERROR("HoudiniRT: unknown Rprim type %s (%s)", typeId.GetText(), rprimId.GetText());
    return nullptr;
}

void
HdRTRenderDelegate::DestroyRprim(HdRprim *rPrim)
{
    delete rPrim;
}

HdSprim *
HdRTRenderDelegate::CreateSprim(TfToken const& typeId,
                                SdfPath const& sprimId)
{
    HDRT_DEBUG("CreateSprim %s %s", typeId.GetText(), sprimId.GetText());
    if (typeId == HdPrimTypeTokens->camera) {
        return new HdCamera(sprimId);
    } else if (typeId == HdPrimTypeTokens->domeLight) {
        return new HdRTLight(sprimId);
    } else {
        TF_CODING_ERROR("HoudiniRT: unknown Sprim type %s", typeId.GetText());
    }

    return nullptr;
}

HdSprim *
HdRTRenderDelegate::CreateFallbackSprim(TfToken const& typeId)
{
    // For fallback sprims, create objects with an empty scene path.
    // They'll use default values and won't be updated by a scene delegate.
    if (typeId == HdPrimTypeTokens->camera) {
        return new HdCamera(SdfPath::EmptyPath());
    } else if (typeId == HdPrimTypeTokens->domeLight) {
        return new HdRTLight(SdfPath::EmptyPath());
    } else {
        TF_CODING_ERROR("HoudiniRT: unknown Sprim type %s", typeId.GetText());
    }

    return nullptr;
}

void
HdRTRenderDelegate::DestroySprim(HdSprim *sPrim)
{
    delete sPrim;
}

HdBprim *
HdRTRenderDelegate::CreateBprim(TfToken const& typeId,
                                SdfPath const& bprimId)
{
    HDRT_DEBUG("CreateBprim %s %s", typeId.GetText(), bprimId.GetText());
    if (typeId == HdPrimTypeTokens->renderBuffer) {
        return new HdRTRenderBuffer(bprimId);
    } else {
        TF_CODING_ERROR("HoudiniRT: unknown Bprim type %s", typeId.GetText());
    }
    return nullptr;
}

HdBprim *
HdRTRenderDelegate::CreateFallbackBprim(TfToken const& typeId)
{
    if (typeId == HdPrimTypeTokens->renderBuffer) {
        return new HdRTRenderBuffer(SdfPath::EmptyPath());
    } else {
        TF_CODING_ERROR("HoudiniRT: unknown Bprim type %s", typeId.GetText());
    }
    return nullptr;
}

void
HdRTRenderDelegate::DestroyBprim(HdBprim *bPrim)
{
    delete bPrim;
}

PXR_NAMESPACE_CLOSE_SCOPE
