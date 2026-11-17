// Runs the production kernel; raw outputs are judged by a separate CPU process.
#include "vkFurnace.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs=std::filesystem;
struct Case {
    std::string name; int mode=0,environment=0;
    uint32_t width=24,height=24,spp=2048;
    std::array<float,12> material;
    std::array<float,3> view,light;
    std::vector<uint32_t> seeds;
};
#include "fixtures.hpp"
void require(bool okay,const std::string& reason) { if(!okay) throw std::runtime_error(reason); }
void save(const fs::path& path,const void* data,size_t bytes) {
    FILE* file=nullptr;fopen_s(&file,path.string().c_str(),"wb");require(file,"output open failed");
    const auto written=std::fwrite(data,1,bytes,file);std::fclose(file);require(written==bytes,"incomplete output");
}
struct Geometry {
    float p[12]={-5,-5,0,5,-5,0,5,5,0,-5,5,0};
    float n[12]={0,0,1,0,0,1,0,0,1,0,0,1};
    uint32_t indices[6]={0,1,2,0,2,3};int32_t faces[2]={31,31};
    HdRTVkMesh mesh;
    Geometry() {
        mesh.id="/Receiver";mesh.geometryVersion=mesh.topologyVersion=mesh.normalVersion=1;
        mesh.positions=p;mesh.normals=n;mesh.indices=indices;mesh.faceIds=faces;
        mesh.vertexCount=mesh.normalCount=4;mesh.triangleCount=2;mesh.primId=17;mesh.useOpenPbr=true;
    }
    void material(const Case& c) {
        std::copy_n(c.material.data(),4,mesh.material.baseColorWeight);
        std::copy_n(c.material.data()+4,4,mesh.material.specularColorWeight);
        std::copy_n(c.material.data()+8,4,mesh.material.properties);
    }
};
std::vector<HdRTVkLight> lights(const Case& c) {
    if(c.mode==0) return {};
    HdRTVkLight l;
    if(c.mode==1) {
        l.positionType[3]=3;l.basisVCos[3]=1;
        for(int k=0;k<3;k++) l.normalRadius[k]=-c.light[k];
        l.radiance[0]=1.3f;l.radiance[1]=.7f;l.radiance[2]=2.1f;
    } else {
        l.positionType[0]=-2*c.view[0];l.positionType[2]=2*c.view[2];l.positionType[3]=float(c.environment-1);
        l.normalRadius[0]=c.view[0];l.normalRadius[2]=-c.view[2];
        if(c.environment==1) {l.basisUArea[0]=c.view[2];l.basisUArea[2]=c.view[0];l.basisVCos[1]=.7f;l.basisUArea[3]=2.8f;}
        if(c.environment==2) {l.basisUArea[0]=.8f*c.view[2];l.basisUArea[2]=.8f*c.view[0];l.basisVCos[1]=.8f;l.basisUArea[3]=float(3.141592653589793*.64);}
        if(c.environment==3) {l.normalRadius[3]=.75f;l.basisUArea[3]=float(4*3.141592653589793*.75*.75);}
        l.radiance[0]=l.radiance[1]=l.radiance[2]=3;
    }
    return {l};
}
HdRTVkFrame frame(const Case& c,uint32_t seed) {
    HdRTVkFrame f;f.width=c.width;f.height=c.height;f.seed=seed;f.bounces=0;f.guides=true;f.orthographic=true;
    if(c.mode==0) std::fill_n(f.dome,3,1.0f);
    const float s=c.view[0],v=c.view[2];
    f.invView[0]=v;f.invView[2]=-s;f.invView[5]=1;f.invView[8]=s;f.invView[10]=v;
    f.invView[12]=s;f.invView[14]=v;f.invView[15]=1;
    f.invProj[0]=f.invProj[5]=.08f;f.invProj[10]=-4.95f;f.invProj[14]=-5.05f;f.invProj[15]=1;
    return f;
}
void render(HdRTVkFurnace& gpu,const Case& c,uint32_t seed,std::string& error) {
    require(gpu.BeginFrame(frame(c,seed),&error),error);
    for(uint32_t first=0;first<c.spp;first+=32)require(gpu.RenderBatch(first,std::min(32u,c.spp-first),&error),error);
    require(gpu.Resolve(c.spp,&error),error);
    require(gpu.ResolvedPixels()&&gpu.ResolvedGuides(),"missing image/guide output");
}
int main(int argc,char** argv) {
    if(argc!=3)return 15;
    try {
        const fs::path out=argv[2];fs::create_directories(out);
        HdRTVkOptions options;options.spvPath=argv[1];options.validation=true;
        HdRTVkFurnace gpu;std::string error;require(gpu.Init(options,&error),error);
        require(gpu.ValidationActive(),"Vulkan validation unavailable");
        std::printf("device=%s validation=active\n",gpu.DeviceName().c_str());
        FILE* csv=nullptr;fopen_s(&csv,(out/"stats.csv").string().c_str(),"w");require(csv,"stats open failed");
        std::fprintf(csv,"case,seed,spp,blas_builds,blas_refits,tlas_builds,tlas_updates,geometry_bytes,table_bytes,trace_submits,traced_samples,resolve_submits,readback_count,readback_bytes,errors,warnings\n");
        Geometry geometry;uint32_t row=0;
        for(const auto& c:cases()) {
            geometry.material(c);HdRTVkEditStats edits;
            require(gpu.SetMeshes({geometry.mesh},&edits,&error),error);require(gpu.SetLights(lights(c),&error),error);
            for(const auto seed:c.seeds) {
                render(gpu,c,seed,error);const auto stem=c.name+"_"+std::to_string(seed);
                save(out/(stem+".rgba32f"),gpu.ResolvedPixels(),size_t(c.width)*c.height*16);
                save(out/(stem+".guides"),gpu.ResolvedGuides(),size_t(c.width)*c.height*sizeof(HdRTVkGuide));
                const auto s=gpu.FrameStats();
                std::fprintf(csv,"%s,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%d,%d\n",c.name.c_str(),seed,c.spp,
                    edits.blasBuilds,edits.blasRefits,edits.tlasBuilds,edits.tlasUpdates,edits.geometryUploadBytes,edits.tableUploadBytes,
                    s.traceSubmits,s.tracedSamples,s.resolveSubmits,s.readbackCount,s.readbackBytes,gpu.ValidationErrors(),gpu.ValidationWarnings());
                std::fflush(csv);std::printf("wrote %s\n",stem.c_str());std::fflush(stdout);
                if(seed==c.seeds.front() && row%17==0) {
                    HdRTVkFurnace fresh;require(fresh.Init(options,&error),error);
                    require(fresh.SetMeshes({geometry.mesh},nullptr,&error),error);require(fresh.SetLights(lights(c),&error),error);
                    render(fresh,c,seed,error);save(out/(stem+".fresh.rgba32f"),fresh.ResolvedPixels(),size_t(c.width)*c.height*16);
                    fresh.Destroy();require(!fresh.ValidationErrors()&&!fresh.ValidationWarnings(),"fresh Vulkan validation");
                }
            }
            ++row;
        }
        std::fclose(csv);gpu.Destroy();
        require(!gpu.ValidationErrors()&&!gpu.ValidationWarnings(),"Vulkan validation including teardown");
        save(out/"teardown.txt","errors=0 warnings=0\n",20);return 0;
    } catch(const std::exception& e) {std::fprintf(stderr,"material probe failure: %s\n",e.what());return 15;}
}
