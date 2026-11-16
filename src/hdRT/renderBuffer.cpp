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

void*
HdRTRenderBuffer::Map()
{
    std::lock_guard<std::mutex> lock(_storageMutex);
    if (_stagingConsumed)
        return nullptr;
    ++_mappers;
    return _buffer.data();
}

void
HdRTRenderBuffer::Unmap()
{
    std::list<std::vector<uint8_t>> released;
    {
        std::lock_guard<std::mutex> lock(_storageMutex);
        if (!TF_VERIFY(_mappers.load() > 0, "Unmap requires a matching Map"))
            return;
        if (--_mappers == 0)
            released.splice(released.end(), _retired);
    }
    // Potentially large pixel allocations are released without either the
    // storage lock or the renderer's publication lock (Unmap is a reader call).
}

std::unique_ptr<HdRTRenderBuffer>
HdRTRenderBuffer::CreateStagingBuffer() const
{
    GfVec3i dimensions;
    HdFormat format;
    bool multiSampled;
    {
        std::lock_guard<std::mutex> lock(_storageMutex);
        if (_stagingConsumed || _format == HdFormatInvalid)
            return nullptr;
        dimensions = GfVec3i(int(_width), int(_height), 1);
        format = _format;
        multiSampled = _multiSampled;
    }
    auto staging = std::make_unique<HdRTRenderBuffer>(SdfPath::EmptyPath());
    if (!staging->Allocate(dimensions, format, multiSampled))
        return nullptr;
    staging->_retirementSlot.emplace_back();
    return staging;
}

bool
HdRTRenderBuffer::PublishFrom(HdRTRenderBuffer& staging)
{
    if (&staging == this)
        return false;
    std::scoped_lock lock(_storageMutex, staging._storageMutex);
    if (staging._stagingConsumed || staging._retirementSlot.empty() ||
        staging._mappers.load() != 0 || _width != staging._width ||
        _height != staging._height || _format != staging._format ||
        _multiSampled != staging._multiSampled || _buffer.size() != staging._buffer.size())
        return false;
    if (_mappers.load() != 0) {
        // Keep the old front stable for every outstanding reader. The empty
        // retirement node was allocated with staging, before the generation lock.
        staging._retirementSlot.front().swap(_buffer);
        _buffer.swap(staging._buffer);
        _retired.splice(_retired.end(), staging._retirementSlot);
    } else {
        // The caller destroys this staging owner only after its generation lock
        // is released; no pixel allocation is freed inside PublishFrom.
        _buffer.swap(staging._buffer);
    }
    staging._stagingConsumed = true;
    staging._width = staging._height = 0;
    staging._format = HdFormatInvalid;
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
