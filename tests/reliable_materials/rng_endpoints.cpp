// CPU-only observations: IEEE-float endpoints and exact shader PCG paths.
// No Vulkan or production shader is executed. The Python runner judges output.
#include <glm/glm.hpp>
#include "openpbr.h"
#include <cmath>
#include <cstdint>
#include <iomanip>
#include <iostream>

uint32_t pcg(uint32_t& state) {
    state=state*747796405u+2891336453u;
    const uint32_t word=((state>>((state>>28u)+4u))^state)*277803737u;
    return (word>>22u)^word;
}
uint32_t mix(uint32_t h) {
    h^=h>>16u;h*=0x85EBCA6Bu;h^=h>>13u;h*=0xC2B2AE35u;h^=h>>16u;return h;
}
float closed(uint32_t word) { return float(word>>8u)*(1.0f/16777216.0f); }
float midpoint(uint32_t word) { return (float(word>>9u)+0.5f)*(1.0f/8388608.0f); }
struct Sample {float pdf;vec3 direction,weight,evaluatedWeight;};
Sample sample(const OpenPBR_PreparedBsdf& p,vec3 u) {
    Sample s;OpenPBR_DiffuseSpecular weight;OpenPBR_BsdfLobeType type;
    openpbr_sample(p,u,s.direction,weight,s.pdf,type);
    s.weight=openpbr_get_sum_of_diffuse_specular(weight);
    s.evaluatedWeight=s.pdf>0 ? openpbr_get_sum_of_diffuse_specular(openpbr_eval(p,s.direction))/s.pdf : vec3(0);
    return s;
}
void values(const Sample& s) {
    std::cout<<"{\"pdf\":"<<s.pdf<<",\"z\":"<<s.direction.z<<",\"weight\":["
             <<s.weight.x<<','<<s.weight.y<<','<<s.weight.z<<"],\"evaluated_weight\":["
             <<s.evaluatedWeight.x<<','<<s.evaluatedWeight.y<<','<<s.evaluatedWeight.z<<"]}";
}
int main() {
    std::cout<<std::setprecision(17);
    uint64_t invalid=0,nonIncreasing=0,nonMidpoint=0;double sum=0;float previous=0;
    for(uint32_t bin=0;bin<(1u<<23u);++bin) {
        const float u=midpoint(bin<<9u);
        invalid+=!(u>0&&u<1);nonIncreasing+=(u<=previous);
        nonMidpoint+=(double(u)*16777216.0!=double(2*bin+1));sum+=u;previous=u;
    }
    auto inputs=openpbr_make_default_resolved_inputs();inputs.base_color=vec3(.15f,.45f,.8f);inputs.specular_weight=0;
    const auto bsdf=openpbr_prepare(inputs,vec3(1),OpenPBR_BaseRgbWavelengths_nm,OpenPBR_VacuumIor,vec3(0,0,1));
    std::cout<<"{\"bins\":8388608,\"invalid\":"<<invalid<<",\"non_increasing\":"<<nonIncreasing
             <<",\"non_midpoint\":"<<nonMidpoint<<",\"sum\":"<<sum<<",\"min\":"<<midpoint(0)
             <<",\"max\":"<<midpoint(UINT32_MAX)<<",\"endpoint_old\":";
    values(sample(bsdf,vec3(.5f,0,.5f)));
    std::cout<<",\"endpoint_new\":";values(sample(bsdf,vec3(.5f,midpoint(0),.5f)));
    std::cout<<",\"hits\":[";bool comma=false;
    constexpr uint32_t width=128,height=96,spp=4096;
    for(uint32_t seed:{101u,107u,113u})for(uint32_t pass=0;pass<spp;++pass)
        for(uint32_t index=0;index<width*height;++index) {
            uint32_t state=mix(seed+mix(pass*(width*height)+index+0x9E3779B9u));
            pcg(state);pcg(state); // two unchanged camera jitter draws
            const uint32_t a=pcg(state),b=pcg(state),c=pcg(state);
            if(b>=256u)continue;
            if(comma)std::cout<<',';comma=true;
            std::cout<<"{\"seed\":"<<seed<<",\"pass\":"<<pass<<",\"x\":"<<index%width
                     <<",\"y_bottom\":"<<index/width<<",\"row_exr\":"<<height-1-index/width
                     <<",\"words\":["<<a<<','<<b<<','<<c<<"],\"old\":";
            values(sample(bsdf,vec3(closed(a),closed(b),closed(c))));
            std::cout<<",\"new\":";values(sample(bsdf,vec3(midpoint(a),midpoint(b),midpoint(c))));std::cout<<'}';
        }
    std::cout<<"]}\n";return 0;
}
