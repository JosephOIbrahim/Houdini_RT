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

#include "aov.h"
#include "light.h"
#include "log.h"
#include "mesh.h"
#include "material.h"
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

// Matte lighting profile: uniform dome plus analytic finite and distant emitters.
const TfTokenVector HdRTRenderDelegate::SUPPORTED_SPRIM_TYPES =
{
    HdPrimTypeTokens->camera,
    HdPrimTypeTokens->material,
    HdPrimTypeTokens->domeLight,
    HdPrimTypeTokens->distantLight,
    HdPrimTypeTokens->rectLight,
    HdPrimTypeTokens->diskLight,
    HdPrimTypeTokens->sphereLight,
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

TfTokenVector HdRTRenderDelegate::GetMaterialRenderContexts() const
{
    return {TfToken("mtlx"), TfToken()};
}

TfTokenVector HdRTRenderDelegate::GetShadingSystems() const
{
    return {TfToken("mtlx")};
}

TfTokenVector HdRTRenderDelegate::GetShaderSourceTypes() const
{
    return GetShadingSystems();
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
    } else if (name == HdRTAlbedoAov()) {
        return HdAovDescriptor(HdFormatFloat32Vec3, false, VtValue(GfVec3f(0.0f)));
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
    stats["hrt_capability_profile_version"] = std::string("textures-hdri-v1");
    // What this render actually used. verify/s3_delegate.sh compares hrt_seed with the seed it chose.
    stats["hrt_seed"] = s.seed;
    stats["hrt_spp"] = s.spp;
    // The bounce budget this render used; verify/s4_firstlight.sh compares it with the count it drew.
    stats["hrt_bounces"] = s.bounces;
    stats["hrt_blas_builds"] = int64_t(s.edits.blasBuilds);
    stats["hrt_blas_refits"] = int64_t(s.edits.blasRefits);
    stats["hrt_tlas_builds"] = int64_t(s.edits.tlasBuilds);
    stats["hrt_tlas_updates"] = int64_t(s.edits.tlasUpdates);
    stats["hrt_geometry_upload_bytes"] = int64_t(s.edits.geometryUploadBytes);
    stats["hrt_table_upload_bytes"] = int64_t(s.edits.tableUploadBytes);
    stats["hrt_uv_upload_bytes"] = int64_t(s.edits.uvUploadBytes);
    stats["hrt_texture_upload_bytes"] = int64_t(s.edits.textureUploadBytes);
    stats["hrt_texture_cache_hits"] = int64_t(s.edits.textureCacheHits);
    stats["hrt_image_decodes"] = int64_t(s.imageDecodes);
    stats["hrt_image_cache_hits"] = int64_t(s.imageCacheHits);
    stats["hrt_texture_diagnostics"] = s.textureDiagnostics;
    stats["hrt_edit_ms"] = s.editMilliseconds;
    stats["hrt_scene_memory"] = s.sceneMemory;
    stats["hrt_scene_generation"] = int64_t(s.sceneGeneration);
    stats["hrt_presented_generation"] = int64_t(s.presentedGeneration);
    stats["hrt_active_generation"] = int64_t(s.activeGeneration);
    stats["hrt_frame_generation"] = int64_t(s.frameGeneration);
    stats["hrt_frame_samples"] = s.frameSamples;
    stats["hrt_presented_samples"] = s.presentedSamples;
    stats["hrt_current_image"] = s.currentImage;
    stats["hrt_first_image_ms"] = s.firstImageMilliseconds;
    stats["hrt_trace_submits"] = int64_t(s.frame.traceSubmits);
    stats["hrt_trace_dispatches"] = int64_t(s.frame.traceDispatches);
    stats["hrt_traced_samples"] = int64_t(s.frame.tracedSamples);
    stats["hrt_resolve_submits"] = int64_t(s.frame.resolveSubmits);
    stats["hrt_resolve_dispatches"] = int64_t(s.frame.resolveDispatches);
    stats["hrt_clear_submits"] = int64_t(s.frame.clearSubmits);
    stats["hrt_readback_bytes"] = int64_t(s.frame.readbackBytes);
    stats["hrt_readback_count"] = int64_t(s.frame.readbackCount);
    stats["hrt_compatibility_readback_submits"] = int64_t(s.frame.compatibilityReadbackSubmits);
    stats["hrt_accumulation_bytes"] = int64_t(s.frame.accumulationBytes);
    stats["hrt_resolve_bytes"] = int64_t(s.frame.resolveBytes);
    stats["hrt_guide_bytes"] = int64_t(s.frame.guideBytes);
    stats["hrt_readback_allocated_bytes"] = int64_t(s.frame.readbackBytesAllocated);
    stats["hrt_accumulation_memory"] = std::string(s.frame.accumulationDeviceLocal ? "device_local" : "unavailable");
    stats["hrt_resolve_memory"] = std::string(s.frame.resolveDeviceLocal ? "device_local" : "unavailable");
    stats["hrt_batch_limit"] = s.batchLimit;
    stats["hrt_batch_target_ms"] = 8.0;
    stats["hrt_last_batch_ms"] = s.frame.lastBatchSeconds * 1000.0;
    stats["hrt_max_batch_ms"] = s.frame.maxBatchSeconds * 1000.0;
    stats["hrt_last_resolve_ms"] = s.frame.lastResolveSeconds * 1000.0;
    stats["hrt_total_resolve_ms"] = s.frame.totalResolveSeconds * 1000.0;
    stats["hrt_pause_epoch"] = int64_t(s.pauseEpoch);
    stats["hrt_pause_ack_epoch"] = int64_t(s.pauseAcknowledgedEpoch);
    stats["hrt_pause_requested"] = s.pauseRequested;
    stats["hrt_pause_acknowledged"] = s.pauseAcknowledged;
    stats["hrt_pause_latency_ms"] = s.pauseLatencyMilliseconds;
    stats["hrt_pause_requested_s"] = s.pauseRequestedSeconds;
    stats["hrt_pause_ack_s"] = s.pauseAcknowledgedSeconds;
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
    // SideFX's documented batch-error protocol; USD diagnostics alone do not
    // fail husk. A superseded request's error must not abort its replacement.
    stats["huskErrorStatus"] = !s.error.empty() && s.activeGeneration == s.sceneGeneration ? 1 : 0;
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
    _renderer.NotifyPause(true);
    _renderThread.PauseRender();
    _paused.store(true);
    return true;
}

bool
HdRTRenderDelegate::Resume()
{
    HDRT_DEBUG("Resume()");
    _renderer.NotifyPause(false);
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
    } else if (typeId == HdPrimTypeTokens->material) {
        return new HdRTMaterial(sprimId);
    } else if (std::find(SUPPORTED_SPRIM_TYPES.begin(), SUPPORTED_SPRIM_TYPES.end(), typeId)
               != SUPPORTED_SPRIM_TYPES.end()) {
        return new HdRTLight(sprimId, typeId);
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
    } else if (typeId == HdPrimTypeTokens->material) {
        return new HdRTMaterial(SdfPath::EmptyPath());
    } else if (std::find(SUPPORTED_SPRIM_TYPES.begin(), SUPPORTED_SPRIM_TYPES.end(), typeId)
               != SUPPORTED_SPRIM_TYPES.end()) {
        return new HdRTLight(SdfPath::EmptyPath(), typeId);
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
