//
// Copyright 2017 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderDelegate.h. Embree, instancers and every light but the dome are
// removed; meshes and the dome are this project's HdRTMesh and HdRTLight; the renderer is its own Vulkan tracer. See NOTICE.md in this directory.
//
#ifndef HDRT_RENDER_DELEGATE_H
#define HDRT_RENDER_DELEGATE_H

#include "pxr/pxr.h"
#include "pxr/imaging/hd/renderDelegate.h"
#include "pxr/imaging/hd/renderThread.h"

#include "renderer.h"
#include "scene.h"
#include "settings.h"

#include <atomic>
#include <memory>
#include <mutex>

PXR_NAMESPACE_OPEN_SCOPE

class HdRTRenderParam;

///
/// \class HdRTRenderDelegate
///
/// Hydra's entry point into HoudiniRT. It tells Hydra which prim types exist (a camera and render
/// buffers for now; meshes arrive with step 4), creates the render pass, owns the background render
/// thread and the renderer that runs on it, receives render settings, and reports render statistics.
///
/// Step 3 draws a built-in test scene (the closed-box furnace), so no scene geometry is read yet.
///
class HdRTRenderDelegate final : public HdRenderDelegate
{
public:
    HdRTRenderDelegate();
    /// Also receives the render settings the application knows at creation time.
    HdRTRenderDelegate(HdRenderSettingsMap const& settingsMap);
    ~HdRTRenderDelegate() override;

    HdRenderParam *GetRenderParam() const override;

    const TfTokenVector &GetSupportedRprimTypes() const override;
    const TfTokenVector &GetSupportedSprimTypes() const override;
    const TfTokenVector &GetSupportedBprimTypes() const override;

    HdResourceRegistrySharedPtr GetResourceRegistry() const override;

    // ---- render settings ---------------------------------------------------------------------

    /// Records what the application set, then hands it to the base class (which bumps the settings
    /// version the render pass watches).
    void SetRenderSetting(TfToken const& key, VtValue const& value) override;

    /// The settings a user interface can offer. Defaults are listed here but not stored, so the
    /// delegate can tell a value the application set from a default.
    HdRenderSettingDescriptorList GetRenderSettingDescriptors() const override;

    /// Attributes on the RenderSettings prim that start with "houdinirt:" are meant for this delegate.
    TfTokenVector GetRenderSettingsNamespaces() const override;

    /// The furnace parameters as of now: delivered render settings first, then the config file named by
    /// HOUDINIRT_S3_CONFIG, then defaults. Called by the render pass on the app thread.
    HdRTFurnaceConfig ResolveFurnaceConfig() const;

    VtDictionary GetRenderStats() const override;

    // ---- pause and resume ----------------------------------------------------------------------

    /// Return true to indicate that pausing and resuming are supported.
    bool IsPauseSupported() const override;

    bool IsPaused() const override;

    /// Pause the background render thread. The image stops changing and the GPU goes idle after the
    /// pass in flight (one sample per pixel) has finished.
    bool Pause() override;

    /// Resume the background render thread where it paused.
    bool Resume() override;

    // ---- prims and passes ----------------------------------------------------------------------

    HdRenderPassSharedPtr CreateRenderPass(HdRenderIndex *index,
                HdRprimCollection const& collection) override;

    HdInstancer *CreateInstancer(HdSceneDelegate *delegate,
                                 SdfPath const& id) override;
    void DestroyInstancer(HdInstancer *instancer) override;

    HdRprim *CreateRprim(TfToken const& typeId,
                         SdfPath const& rprimId) override;
    void DestroyRprim(HdRprim *rPrim) override;

    HdSprim *CreateSprim(TfToken const& typeId,
                         SdfPath const& sprimId) override;
    HdSprim *CreateFallbackSprim(TfToken const& typeId) override;
    void DestroySprim(HdSprim *sPrim) override;

    HdBprim *CreateBprim(TfToken const& typeId,
                         SdfPath const& bprimId) override;
    HdBprim *CreateFallbackBprim(TfToken const& typeId) override;
    void DestroyBprim(HdBprim *bPrim) override;

    void CommitResources(HdChangeTracker *tracker) override;

    /// Raytraced back ends ask for the "full" material binding.
    TfToken GetMaterialBindingPurpose() const override {
        return HdTokens->full;
    }

    /// What format each named AOV is rendered in.
    HdAovDescriptor
        GetDefaultAovDescriptor(TfToken const& name) const override;

private:
    static const TfTokenVector SUPPORTED_RPRIM_TYPES;
    static const TfTokenVector SUPPORTED_SPRIM_TYPES;
    static const TfTokenVector SUPPORTED_BPRIM_TYPES;

    /// One resource registry shared by every HoudiniRT delegate in the process.
    static std::mutex _mutexResourceRegistry;
    static std::atomic_int _counterResourceRegistry;
    static HdResourceRegistrySharedPtr _resourceRegistry;

    HdRTRenderDelegate(const HdRTRenderDelegate &)             = delete;
    HdRTRenderDelegate &operator =(const HdRTRenderDelegate &) = delete;

    void _Initialize();

    // A version counter for edits to anything the render thread reads or writes.
    std::atomic<int> _sceneVersion;

    // Meshes and lights as Hydra delivered them (CPU side). Declared before the render param, which
    // points at it, and before the render thread, which is stopped before anything here is destroyed.
    HdRTScene _scene;

    // Passed to prims during Sync(); stops the render thread before an edit.
    std::shared_ptr<HdRTRenderParam> _renderParam;

    // The background render thread. It manages the synchronisation between Hydra's edits on the app
    // thread and the renderer running in the background.
    HdRenderThread _renderThread;

    // What the render thread runs.
    HdRTRenderer _renderer;

    HdRenderSettingDescriptorList _settingDescriptors;

    // Settings the application actually set (constructor map and SetRenderSetting), never defaults.
    mutable std::mutex _deliveredMutex;
    HdRenderSettingsMap _delivered;

    std::atomic<bool> _paused;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDER_DELEGATE_H
