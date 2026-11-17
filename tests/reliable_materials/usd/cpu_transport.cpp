// Independent double-precision transport/quadrature around Adobe CPU/array BSDF.
// Shares the specified BSDF model, never the renderer's sampler or transport.
#include <glm/glm.hpp>
#include "openpbr.h"
#include <array>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <string>
#include <vector>
#include "quadrature.hpp"
struct Point {int row,col;double x,y;};
struct Job {std::string name;float m[12];glm::dvec3 dome,rect,center,distant,wi;double halfx,halfy;};
bool read(std::istream& in,Job& j) {
    if(!(in>>j.name))return false;
    for(auto& x:j.m)in>>x;
    for(auto* v:{&j.dome,&j.rect,&j.center})in>>v->x>>v->y>>v->z;
    in>>j.halfx>>j.halfy;
    for(auto* v:{&j.distant,&j.wi})in>>v->x>>v->y>>v->z;
    return bool(in);
}
OpenPBR_PreparedBsdf prepare(const Job& j) {
    auto p=openpbr_make_default_resolved_inputs();
    p.base_color=vec3(j.m[0],j.m[1],j.m[2]);p.base_weight=j.m[3];
    p.specular_color=vec3(j.m[4],j.m[5],j.m[6]);p.specular_weight=j.m[7];
    p.base_metalness=j.m[8];p.specular_roughness=j.m[9];p.specular_ior=j.m[10];p.base_diffuse_roughness=j.m[11];
    return openpbr_prepare(p,vec3(1),OpenPBR_BaseRgbWavelengths_nm,OpenPBR_VacuumIor,vec3(0,0,1));
}
glm::dvec3 evaluate(const OpenPBR_PreparedBsdf& p,glm::dvec3 wi) {
    return glm::dvec3(openpbr_get_sum_of_diffuse_specular(openpbr_eval(p,vec3(wi))));
}
bool blocked(const Job& j,const Point& p,glm::dvec3 wi) {
    if(j.halfx<=0 || wi.z<=0)return false;
    const double t=j.center.z/wi.z;
    return std::abs(p.x+t*wi.x-j.center.x)<=j.halfx && std::abs(p.y+t*wi.y-j.center.y)<=j.halfy;
}
glm::dvec3 dome_integral(const OpenPBR_PreparedBsdf& bsdf,const Quad* q,size_t count) {
    glm::dvec3 sum(0);
    // Isotropy + normal viewing direction make the azimuth integral exactly2pi.
    for(size_t i=0;i<count;i++) {
        const double mu=(q[i].x+1)*.5;
        sum+=evaluate(bsdf,{std::sqrt(1-mu*mu),0,mu})*(q[i].w*3.14159265358979323846);
    }
    return sum;
}
glm::dvec3 rectangle_integral(const Job& j,const Point& p,const OpenPBR_PreparedBsdf& bsdf,const Quad* q,size_t count) {
    glm::dvec3 sum(0);
    if(j.halfx<=0)return sum;
    for(size_t a=0;a<count;a++)for(size_t b=0;b<count;b++) {
        const glm::dvec3 offset=j.center+glm::dvec3(q[a].x*j.halfx,q[b].x*j.halfy,0)-glm::dvec3(p.x,p.y,0);
        const double r=glm::length(offset);const glm::dvec3 wi=offset/r;
        sum+=evaluate(bsdf,wi)*(offset.z/(r*r*r)*q[a].w*q[b].w*j.halfx*j.halfy);
    }
    return sum;
}
glm::dvec3 reference(const Job& j,const Point& p,const OpenPBR_PreparedBsdf& bsdf,glm::dvec3 furnace,const Quad* q,size_t count) {
    glm::dvec3 value=j.dome*furnace;
    // Opaque finite geometry replaces the uniform dome over its solid angle.
    value+=(j.rect-j.dome)*rectangle_integral(j,p,bsdf,q,count);
    if(!blocked(j,p,j.wi))value+=j.distant*evaluate(bsdf,j.wi);
    return value;
}
int main(int argc,char** argv) {
    if(argc!=4)return 15;
    std::ifstream jobs(argv[1]),points_file(argv[2]);std::ofstream out(argv[3]);if(!jobs||!points_file||!out)return 15;
    std::vector<Point> points;Point p;while(points_file>>p.row>>p.col>>p.x>>p.y)points.push_back(p);
    out<<"name,row,col,r32,g32,b32,r64,g64,b64,se_r,se_g,se_b,samples\n"<<std::setprecision(17);
    Job j;
    while(read(jobs,j)) {
        const auto bsdf=prepare(j);
        const auto furnace32=dome_integral(bsdf,q128,128),furnace64=dome_integral(bsdf,q256,256);
        if(j.m[9]>0) {
            for(const auto& point:points) {
                const auto a=reference(j,point,bsdf,furnace32,q32,32),b=reference(j,point,bsdf,furnace64,q64,64);
                out<<j.name<<','<<point.row<<','<<point.col<<','<<a.x<<','<<a.y<<','<<a.z<<','<<b.x<<','<<b.y<<','<<b.z<<",0,0,0,0\n";
            }
        } else {
            // Narrow roughness-zero lobe: use independent BSDF samples with
            // analytic emitter intersections instead of underresolved quadrature.
            const uint32_t count=524288;std::mt19937 rng(1009);std::uniform_real_distribution<float> uni(0,std::nextafter(1.f,0.f));
            std::vector<glm::dvec3> sums(points.size(),glm::dvec3(0)),squares=sums;
            for(uint32_t k=0;k<count;k++) {
                vec3 wi;OpenPBR_DiffuseSpecular w;float pdf;OpenPBR_BsdfLobeType type;
                const vec3 u(uni(rng),uni(rng),uni(rng));openpbr_sample(bsdf,u,wi,w,pdf,type);if(pdf<=0)continue;
                const glm::dvec3 weight(openpbr_get_sum_of_diffuse_specular(w));
                for(size_t i=0;i<points.size();i++) {
                    const auto value=weight*(blocked(j,points[i],glm::dvec3(wi))?j.rect:j.dome);
                    sums[i]+=value;squares[i]+=value*value;
                }
            }
            for(size_t i=0;i<points.size();i++) {
                const auto mean=sums[i]/double(count),variance=glm::max(glm::dvec3(0),(squares[i]-sums[i]*sums[i]/double(count))/double(count-1));
                const auto se=glm::sqrt(variance/double(count));const auto& point=points[i];
                out<<j.name<<','<<point.row<<','<<point.col<<','<<mean.x<<','<<mean.y<<','<<mean.z<<','<<mean.x<<','<<mean.y<<','<<mean.z<<','<<se.x<<','<<se.y<<','<<se.z<<','<<count<<'\n';
            }
        }
        out.flush();std::cout<<"reference "<<j.name<<std::endl;
    }
    return jobs.eof()?0:15;
}
