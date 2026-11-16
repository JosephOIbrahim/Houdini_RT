//
// Copyright 2018 Pixar
//
// Licensed under the terms set forth in the LICENSE.txt file available at
// https://openusd.org/license.
//
// Modified for HoudiniRT (2026): adapted from OpenUSD 26.03,
// pxr/imaging/plugin/hdEmbree/renderBuffer.h. The per-pixel sample accumulation is removed: this
// renderer accumulates on the GPU and writes resolved values. See NOTICE.md in this directory.
//
#ifndef HDRT_RENDER_BUFFER_H
#define HDRT_RENDER_BUFFER_H

#include "pxr/pxr.h"
#include "pxr/imaging/hd/renderBuffer.h"

#include <atomic>
#include <cstdint>
#include <vector>

PXR_NAMESPACE_OPEN_SCOPE

///
/// \class HdRTRenderBuffer
///
/// A CPU-side image the application can Map() and read: husk reads it to write the EXR, the Solaris
/// viewport reads it to draw. The render thread writes finished (already averaged) values into it.
///
class HdRTRenderBuffer : public HdRenderBuffer
{
public:
    HdRTRenderBuffer(SdfPath const& id);
    ~HdRTRenderBuffer() override;

    /// Overridden only to stop the render thread before a possible re-allocation.
    void Sync(HdSceneDelegate *sceneDelegate,
              HdRenderParam *renderParam,
              HdDirtyBits *dirtyBits) override;

    /// Overridden only to stop the render thread before deallocation.
    void Finalize(HdRenderParam *renderParam) override;

    /// Allocate a new buffer with the given dimensions and format. Only depth == 1 is supported.
    bool Allocate(GfVec3i const& dimensions,
                  HdFormat format,
                  bool multiSampled) override;

    unsigned int GetWidth() const override { return _width; }
    unsigned int GetHeight() const override { return _height; }
    unsigned int GetDepth() const override { return 1; }
    HdFormat GetFormat() const override { return _format; }

    /// Reports what the application asked for. Storage is one value per pixel either way.
    bool IsMultiSampled() const override { return _multiSampled; }

    void* Map() override {
        _mappers++;
        return _buffer.data();
    }

    void Unmap() override {
        _mappers--;
    }

    bool IsMapped() const override {
        return _mappers.load() != 0;
    }

    /// True once the render thread has written the final value of every pixel.
    bool IsConverged() const override {
        return _converged.load();
    }

    void SetConverged(bool cv) {
        _converged.store(cv);
    }

    /// Nothing to do: values are averaged before they are written.
    void Resolve() override {}

    // ---------------------------------------------------------------------- //
    /// \name I/O helpers
    // ---------------------------------------------------------------------- //

    /// Write a float, vec2f, vec3f or vec4f to one pixel, converting to the buffer's format. Extra
    /// components are dropped; missing ones are written as 0. Row 0 is the bottom of the image.
    void Write(GfVec3i const& pixel, size_t numComponents, float const* value);

    /// Write an int, vec2i, vec3i or vec4i to one pixel.
    void Write(GfVec3i const& pixel, size_t numComponents, int const* value);

    /// Fill the whole buffer with one float-valued vector.
    void Clear(size_t numComponents, float const* value);

    /// Fill the whole buffer with one int-valued vector.
    void Clear(size_t numComponents, int const* value);

private:
    static size_t _GetBufferSize(GfVec2i const& dims, HdFormat format);

    void _Deallocate() override;

    unsigned int _width;
    unsigned int _height;
    HdFormat _format;
    bool _multiSampled;

    std::vector<uint8_t> _buffer;

    std::atomic<int> _mappers;
    std::atomic<bool> _converged;
};

PXR_NAMESPACE_CLOSE_SCOPE

#endif // HDRT_RENDER_BUFFER_H
