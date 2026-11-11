// settings.h - the parameters of the step 3 furnace and where they come from.
//
// The exam hands the delegate five values two ways at once: as attributes on the RenderSettings prim
// (they reach the delegate as render settings) and in a JSON file named by HOUDINIRT_S3_CONFIG.
// See the header of verify/s3_delegate.sh for the contract.
#ifndef HDRT_SETTINGS_H
#define HDRT_SETTINGS_H

#include "pxr/pxr.h"
#include "pxr/base/tf/staticTokens.h"
#include "pxr/base/tf/token.h"
#include "pxr/base/vt/value.h"
#include "pxr/imaging/hd/renderDelegate.h"

#include <string>

PXR_NAMESPACE_OPEN_SCOPE

#define HDRT_RENDER_SETTINGS_TOKENS                         \
    ((seed,            "houdinirt:seed"))                   \
    ((spp,             "houdinirt:spp"))                    \
    ((furnaceRho,      "houdinirt:furnace:rho"))            \
    ((furnaceEmission, "houdinirt:furnace:emission"))       \
    ((bounces,         "houdinirt:bounces"))

TF_DECLARE_PUBLIC_TOKENS(HdRTRenderSettingsTokens, HDRT_RENDER_SETTINGS_TOKENS);

/// Everything one render of the furnace depends on, besides the camera and the image size.
struct HdRTFurnaceConfig
{
    int seed = 1;                               ///< decorrelates the per-pixel random streams
    int spp = 64;                               ///< passes to convergence, one sample per pixel each
    float rho = 0.5f;                           ///< wall albedo
    float emission[3] = { 1.0f, 1.0f, 1.0f };   ///< wall emission, RGB
    int bounces = 16;                           ///< houdinirt:bounces: diffuse bounces after the first hit. The default
                                                ///< stays outside the s4 exam's drawn set (6, 7, 9..12): an injected
                                                ///< value must never equal a default (DECISIONS.md 2026-10-06)
    int openFace = -1;                          ///< debug: 0..5 leaves one wall out (HOUDINIRT_DEBUG_OPEN_FACE)
    bool validation = false;                    ///< HOUDINIRT_VALIDATION=1 asks for the Vulkan validation layer
    bool allowSoftwareDevice = false;           ///< HOUDINIRT_ALLOW_SOFTWARE_DEVICE=1: tests on machines without a GPU
    std::string source = "defaults";            ///< where the five furnace values came from (log, stats)

    bool operator==(const HdRTFurnaceConfig& o) const
    {
        return seed == o.seed && spp == o.spp && rho == o.rho && emission[0] == o.emission[0] &&
               emission[1] == o.emission[1] && emission[2] == o.emission[2] && bounces == o.bounces &&
               openFace == o.openFace && validation == o.validation && allowSoftwareDevice == o.allowSoftwareDevice;
    }
    bool operator!=(const HdRTFurnaceConfig& o) const { return !(*this == o); }
};

/// Works out the furnace parameters. Each of the five values is taken, in this order, from: a render
/// setting the application delivered, else the JSON file named by HOUDINIRT_S3_CONFIG, else the default
/// above. `delivered` holds only settings the application set, never descriptor defaults.
HdRTFurnaceConfig HdRTResolveFurnaceConfig(HdRenderSettingsMap const& delivered);

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_SETTINGS_H
