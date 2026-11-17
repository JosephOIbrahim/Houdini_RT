// Adobe OpenPBR, texture LUT mode. The material graph is resolved on the CPU.
layout(set=0, binding=6) uniform sampler2D openpbr_luts_2d[8];
layout(set=0, binding=7) uniform sampler3D openpbr_luts_3d[8];
#define OPENPBR_USE_TEXTURE_LUTS 1
#define OPENPBR_SAMPLE_2D_TEXTURE(id, uv) textureLod(openpbr_luts_2d[id], uv, 0.0)
#define OPENPBR_SAMPLE_3D_TEXTURE(id, uvw) textureLod(openpbr_luts_3d[id], uvw, 0.0)
#include "../../../third_party/openpbr-bsdf/openpbr.h"

OpenPBR_PreparedBsdf prepareMaterial(MeshGpu mesh, vec3 ns, vec3 viewDirection) {
    OpenPBR_ResolvedInputs inputs = openpbr_make_default_resolved_inputs();
    inputs.base_color = mesh.baseColorWeight.rgb;
    inputs.base_weight = mesh.baseColorWeight.a;
    inputs.base_metalness = mesh.materialProperties.x;
    inputs.base_diffuse_roughness = 0.0;
    inputs.specular_color = mesh.specularColorWeight.rgb;
    inputs.specular_weight = mesh.specularColorWeight.a;
    inputs.specular_roughness = mesh.materialProperties.y;
    inputs.specular_ior = mesh.materialProperties.z;
    inputs.geometry_basis = openpbr_make_basis(ns);
    inputs.geometry_coat_basis = inputs.geometry_basis;
    return openpbr_prepare(inputs, vec3(1.0), OpenPBR_BaseRgbWavelengths_nm,
                           OpenPBR_VacuumIor, viewDirection);
}

vec3 materialEval(OpenPBR_PreparedBsdf bsdf, vec3 direction) {
    return openpbr_get_sum_of_diffuse_specular(openpbr_eval(bsdf, direction));
}
