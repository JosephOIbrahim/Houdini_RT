// Independent transport reference: Adobe C++/array LUTs, no renderer code.
// Reads whitespace jobs; writes raw observations. A separate Python judge decides.
#include <glm/glm.hpp>
#include "openpbr.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <string>

struct Job {
    std::string name;
    uint32_t samples=0,seed=1;
    int environment=0; // 0 dome, 1 rectangle, 2 disk, 3 sphere
    float material[12]{};
    vec3 normal{},view{},light{};
};
OpenPBR_ResolvedInputs inputs(const Job& j) {
    auto p=openpbr_make_default_resolved_inputs();
    p.base_color=vec3(j.material[0],j.material[1],j.material[2]); p.base_weight=j.material[3];
    p.specular_color=vec3(j.material[4],j.material[5],j.material[6]); p.specular_weight=j.material[7];
    p.base_metalness=j.material[8]; p.specular_roughness=j.material[9];
    p.specular_ior=j.material[10]; p.base_diffuse_roughness=j.material[11];
    p.geometry_basis=openpbr_make_basis(normalize(j.normal)); p.geometry_coat_basis=p.geometry_basis;
    return p;
}
bool read(std::istream& in,Job& j) {
    if(!(in>>j.name>>j.samples>>j.seed>>j.environment)) return false;
    for(float& f:j.material) in>>f;
    for(auto* v:{&j.normal,&j.view,&j.light}) in>>v->x>>v->y>>v->z;
    return bool(in);
}
bool finite(vec3 x) { return std::isfinite(x.x)&&std::isfinite(x.y)&&std::isfinite(x.z); }
double environment(const Job& j,vec3 direction,float x,float y) {
    if(j.environment==0) return 1;
    // Independent analytic intersections. Receiver/camera integration is over
    // the same continuous orthographic raster footprint, not candidate pixels.
    const vec3 origin(x/j.view.z,y,0);
    const vec3 axis(-j.view.x,0,j.view.z),center=2.0f*axis,u(j.view.z,0,j.view.x);
    if(j.environment==3) {
        const vec3 offset=origin-center;
        const double b=dot(offset,direction),c=dot(offset,offset)-.75*.75;
        const double discriminant=b*b-c;
        return discriminant>=0 && -b-std::sqrt(discriminant)>0 ? 3 : 0;
    }
    const float denominator=dot(direction,axis);
    if(denominator<=0) return 0;
    const float distance=dot(center-origin,axis)/denominator;
    if(distance<=0) return 0;
    const vec3 hit=origin+direction*distance-center;
    const float a=dot(hit,u),b=hit.y;
    if(j.environment==1) return std::abs(a)<=1 && std::abs(b)<=.7 ? 3 : 0;
    return a*a+b*b<=.8*.8 ? 3 : 0;
}
int main(int argc,char** argv) {
    if(argc!=3) return 15;
    std::ifstream jobs(argv[1]); std::ofstream out(argv[2]);
    if(!jobs || !out) return 15;
    out<<"name,fcos_r,fcos_g,fcos_b,pdf,reverse_fcos_r,reverse_fcos_g,reverse_fcos_b,"
          "mean_r,mean_g,mean_b,se_r,se_g,se_b,invalid,nonfinite,negative,delta,"
          "compared,weight_mean_rel,pdf_mean_rel,weight_max_rel,pdf_max_rel,samples\n";
    out<<std::setprecision(17);
    Job j;
    while(read(jobs,j)) {
        const auto p=inputs(j); const vec3 wo=normalize(j.view),wi=normalize(j.light);
        const auto prepared=openpbr_prepare(p,vec3(1),OpenPBR_BaseRgbWavelengths_nm,OpenPBR_VacuumIor,wo);
        const auto reverse=openpbr_prepare(p,vec3(1),OpenPBR_BaseRgbWavelengths_nm,OpenPBR_VacuumIor,wi);
        const auto f=openpbr_get_sum_of_diffuse_specular(openpbr_eval(prepared,wi));
        const auto g=openpbr_get_sum_of_diffuse_specular(openpbr_eval(reverse,wo));
        const float pdf=openpbr_pdf(prepared,wi);
        std::mt19937 rng(j.seed); std::uniform_real_distribution<float> uniform(0,std::nextafter(1.0f,0.0f));
        double sums[3]{},squares[3]{},weightRel=0,pdfRel=0,weightMax=0,pdfMax=0;
        uint64_t invalid=0,nonfinite=0,negative=0,delta=0,compared=0;
        for(uint32_t k=0;k<j.samples;k++) {
            vec3 direction; OpenPBR_DiffuseSpecular weight; float probability=0; OpenPBR_BsdfLobeType type;
            const vec3 random(uniform(rng),uniform(rng),uniform(rng));
            openpbr_sample(prepared,random,direction,weight,probability,type);
            if(!std::isfinite(probability) || probability<0) { ++nonfinite; continue; }
            if(probability==0) { ++invalid; continue; }
            const vec3 w=openpbr_get_sum_of_diffuse_specular(weight);
            if(!finite(w)||!finite(direction)) { ++nonfinite; continue; }
            const double le=environment(j,direction,(uniform(rng)*2-1)*.08f,(uniform(rng)*2-1)*.08f);
            for(int c=0;c<3;c++) {
                const double value=w[c]*le; sums[c]+=value; squares[c]+=value*value; if(w[c]<0) ++negative;
            }
            if(type&OpenPBR_BsdfLobeTypeSpecular) { ++delta; continue; }
            // Keep the exact returned direction. At the upstream minimum
            // roughness, another normalization changes an extremely sharp PDF.
            const float evaluatedPdf=openpbr_pdf(prepared,direction);
            if(evaluatedPdf<=1e-12f || probability<=1e-12f) continue;
            const vec3 eval=openpbr_get_sum_of_diffuse_specular(openpbr_eval(prepared,direction))/evaluatedPdf;
            double difference=0;
            for(int c=0;c<3;c++) difference=std::max(difference,double(std::abs(eval[c]-w[c])/std::max(std::abs(w[c]),1e-6f)));
            const double pd=std::abs(double(probability)-evaluatedPdf)/probability;
            weightRel+=difference; pdfRel+=pd; weightMax=std::max(weightMax,difference); pdfMax=std::max(pdfMax,pd); ++compared;
        }
        out<<j.name<<','<<f.x<<','<<f.y<<','<<f.z<<','<<pdf<<','<<g.x<<','<<g.y<<','<<g.z;
        for(int c=0;c<3;c++) out<<','<<(j.samples?sums[c]/j.samples:0);
        for(int c=0;c<3;c++) {
            double variance=j.samples>1?(squares[c]-sums[c]*sums[c]/j.samples)/(j.samples-1):0;
            out<<','<<(j.samples?std::sqrt(std::max(0.0,variance)/j.samples):0);
        }
        out<<','<<invalid<<','<<nonfinite<<','<<negative<<','<<delta<<','<<compared<<','
           <<(compared?weightRel/compared:0)<<','<<(compared?pdfRel/compared:0)<<','
           <<weightMax<<','<<pdfMax<<','<<j.samples<<'\n';
        out.flush(); std::cout<<"reference "<<j.name<<'\n';
    }
    return jobs.eof() ? 0 : 15;
}
