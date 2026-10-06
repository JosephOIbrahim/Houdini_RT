//
// Copyright 2018 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderBuffer.cpp. The per-pixel sample accumulation is removed and
// normalised 8-bit writes are clamped. See NOTICE.md in this directory.
//
#include "renderBuffer.h"

#include "renderParam.h"

#include "pxr/base/gf/half.h"
#include "pxr/base/gf/vec2i.h"
#include "pxr/base/gf/vec3i.h"
#include "pxr/base/tf/diagnostic.h"
#include "pxr/base/tf/enum.h"

#include <algorithm>

PXR_NAMESPACE_OPEN_SCOPE

HdRTRenderBuffer::HdRTRenderBuffer(SdfPath const& id)
    : HdRenderBuffer(id)
    , _width(0)
    , _height(0)
    , _format(HdFormatInvalid)
    , _multiSampled(false)
    , _buffer()
    , _mappers(0)
    , _converged(false)
{
}

HdRTRenderBuffer::~HdRTRenderBuffer() = default;

/*virtual*/
void
HdRTRenderBuffer::Sync(HdSceneDelegate *sceneDelegate,
                       HdRenderParam *renderParam,
                       HdDirtyBits *dirtyBits)
{
    if (*dirtyBits & DirtyDescription) {
        // The render thread writes straight into render buffers, so it has to be idle before one
        // is reallocated.
        static_cast<HdRTRenderParam*>(renderParam)->AcquireSceneForEdit();
    }

    HdRenderBuffer::Sync(sceneDelegate, renderParam, dirtyBits);
}

/*virtual*/
void
HdRTRenderBuffer::Finalize(HdRenderParam *renderParam)
{
    // Same reason as in Sync: the render thread must be idle before a buffer goes away.
    static_cast<HdRTRenderParam*>(renderParam)->AcquireSceneForEdit();

    HdRenderBuffer::Finalize(renderParam);
}

/*virtual*/
void
HdRTRenderBuffer::_Deallocate()
{
    // If the buffer is mapped while we're doing this, there's not a great
    // recovery path...
    TF_VERIFY(!IsMapped());

    _width = 0;
    _height = 0;
    _format = HdFormatInvalid;
    _multiSampled = false;
    _buffer.resize(0);

    _mappers.store(0);
    _converged.store(false);
}

/*static*/
size_t
HdRTRenderBuffer::_GetBufferSize(GfVec2i const &dims, HdFormat format)
{
    return size_t(dims[0]) * size_t(dims[1]) * HdDataSizeOfFormat(format);
}

/*virtual*/
bool
HdRTRenderBuffer::Allocate(GfVec3i const& dimensions,
                           HdFormat format,
                           bool multiSampled)
{
    _Deallocate();

    if (dimensions[2] != 1) {
        TF_WARN("Render buffer allocated with dims <%d, %d, %d> and"
                " format %s; depth must be 1!",
                dimensions[0], dimensions[1], dimensions[2],
                TfEnum::GetName(format).c_str());
        return false;
    }
    if (dimensions[0] < 0 || dimensions[1] < 0) {
        TF_WARN("Render buffer allocated with negative dims <%d, %d>",
                dimensions[0], dimensions[1]);
        return false;
    }

    _width = dimensions[0];
    _height = dimensions[1];
    _format = format;
    _multiSampled = multiSampled;
    _buffer.assign(_GetBufferSize(GfVec2i(_width, _height), format), 0);

    return true;
}

template<typename T>
static void _WriteOutput(HdFormat format, uint8_t *dst,
                         size_t valueComponents, T const* value)
{
    HdFormat componentFormat = HdGetComponentFormat(format);
    size_t componentCount = HdGetComponentCount(format);

    for (size_t c = 0; c < componentCount; ++c) {
        if (componentFormat == HdFormatInt32) {
            ((int32_t*)dst)[c] =
                (c < valueComponents) ? (int32_t)(value[c]) : 0;
        } else if (componentFormat == HdFormatFloat16) {
            ((uint16_t*)dst)[c] =
                (c < valueComponents) ? GfHalf(float(value[c])).bits() : 0;
        } else if (componentFormat == HdFormatFloat32) {
            ((float*)dst)[c] =
                (c < valueComponents) ? (float)(value[c]) : 0.0f;
        } else if (componentFormat == HdFormatUNorm8) {
            const float v = (c < valueComponents) ? float(value[c]) : 0.0f;
            ((uint8_t*)dst)[c] =
                (uint8_t)(std::min(1.0f, std::max(0.0f, v)) * 255.0f + 0.5f);
        } else if (componentFormat == HdFormatSNorm8) {
            const float v = (c < valueComponents) ? float(value[c]) : 0.0f;
            ((int8_t*)dst)[c] =
                (int8_t)(std::min(1.0f, std::max(-1.0f, v)) * 127.0f);
        }
    }
}

void
HdRTRenderBuffer::Write(
    GfVec3i const& pixel, size_t numComponents, float const* value)
{
    const size_t idx = size_t(pixel[1]) * _width + size_t(pixel[0]);
    const size_t formatSize = HdDataSizeOfFormat(_format);
    _WriteOutput(_format, &_buffer[idx * formatSize], numComponents, value);
}

void
HdRTRenderBuffer::Write(
    GfVec3i const& pixel, size_t numComponents, int const* value)
{
    const size_t idx = size_t(pixel[1]) * _width + size_t(pixel[0]);
    const size_t formatSize = HdDataSizeOfFormat(_format);
    _WriteOutput(_format, &_buffer[idx * formatSize], numComponents, value);
}

void
HdRTRenderBuffer::Clear(size_t numComponents, float const* value)
{
    const size_t formatSize = HdDataSizeOfFormat(_format);
    for (size_t i = 0; i < size_t(_width) * _height; ++i) {
        _WriteOutput(_format, &_buffer[i * formatSize], numComponents, value);
    }
}

void
HdRTRenderBuffer::Clear(size_t numComponents, int const* value)
{
    const size_t formatSize = HdDataSizeOfFormat(_format);
    for (size_t i = 0; i < size_t(_width) * _height; ++i) {
        _WriteOutput(_format, &_buffer[i * formatSize], numComponents, value);
    }
}

PXR_NAMESPACE_CLOSE_SCOPE
