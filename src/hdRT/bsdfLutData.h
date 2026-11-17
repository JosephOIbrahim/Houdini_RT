// Adobe OpenPBR tables, Apache-2.0; unmodified data lives in third_party.
#ifndef HDRT_BSDF_LUT_DATA_H
#define HDRT_BSDF_LUT_DATA_H
namespace {
const uint16_t kBsdfEnergy0[32*32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ideal_dielectric_energy_complement_data.h"
};
const uint16_t kBsdfEnergy1[32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ideal_dielectric_avg_energy_complement_data.h"
};
const uint16_t kBsdfEnergy2[32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ideal_dielectric_reflection_ratio_data.h"
};
const uint16_t kBsdfEnergy3[32*32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_opaque_dielectric_energy_complement_data.h"
};
const uint16_t kBsdfEnergy4[32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_opaque_dielectric_avg_energy_complement_data.h"
};
const uint16_t kBsdfEnergy5[32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ideal_metal_energy_complement_data.h"
};
const uint16_t kBsdfEnergy6[32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ideal_metal_avg_energy_complement_data.h"
};
struct BsdfTableVector { float x,y,z; constexpr BsdfTableVector(float a,float b,float c):x(a),y(b),z(c){} };
#define vec3 BsdfTableVector
const BsdfTableVector kBsdfLtc[32*32] = {
#include "../../third_party/openpbr-bsdf/impl/data/openpbr_ltc_data.h"
};
#undef vec3
}
#endif
