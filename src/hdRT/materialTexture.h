#ifndef HDRT_MATERIAL_TEXTURE_H
#define HDRT_MATERIAL_TEXTURE_H
#include "material.h"
PXR_NAMESPACE_OPEN_SCOPE
int HdRTTextureInputSlot(const TfToken& name);
bool HdRTTranslateTexture(const HdMaterialNetwork2& network,
                         const HdMaterialConnection2& source, int channels,
                         HdRTTextureBinding* output, std::string* error);
PXR_NAMESPACE_CLOSE_SCOPE
#endif
