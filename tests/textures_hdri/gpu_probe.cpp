// Runs the actual production texture/geometry kernel; no expected values here.
#include "vkFurnace.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs=std::filesystem;
struct Environment { int texture;std::array<float,3> tint;float rotation; };
struct Case {
    std::string name;bool environment=false;int texture=0,filter=1,wrapU=2,wrapV=2,slot=0;bool seam=false;
    std::array<float,6> affine={1,0,0,0,1,0};
    std::array<float,3> normal={0,0,1},uniform={0,0,0};
    std::vector<Environment> environments;uint32_t spp=1;std::vector<uint32_t> seeds;
};
#include "fixtures.hpp"
void require(bool okay,const std::string& reason) {if(!okay)throw std::runtime_error(reason);}
void save(const fs::path& path,const void* data,size_t bytes) {
    FILE* f=nullptr;fopen_s(&f,path.string().c_str(),"wb");require(f,"output open failed");
    auto written=std::fwrite(data,1,bytes,f);std::fclose(f);require(written==bytes,"incomplete output");
}
struct Geometry {
    float positions[12]={-1,-1,0,1,-1,0,1,1,0,-1,1,0};
    float normals[12]={0,0,1,0,0,1,0,0,1,0,0,1};
    uint32_t indices[6]={0,1,2,0,2,3};int32_t faces[2]={17,17};
    float uvs[12]={0,0,1,0,1,1,0,0,1,1,0,1};HdRTVkMesh mesh;
    Geometry() {
        mesh.id="/Receiver";mesh.positions=positions;mesh.normals=normals;mesh.indices=indices;mesh.faceIds=faces;
        mesh.vertexCount=mesh.normalCount=4;mesh.triangleCount=2;mesh.geometryVersion=mesh.topologyVersion=mesh.normalVersion=mesh.uvVersion=1;
        mesh.cornerUVs=uvs;mesh.useOpenPbr=true;mesh.primId=31;
        mesh.material.baseColorWeight[0]=.2f;mesh.material.baseColorWeight[1]=.5f;mesh.material.baseColorWeight[2]=.8f;
        mesh.material.specularColorWeight[3]=0;
    }
    void configure(const Case& c) {
        float next[12]={0,0,1,0,1,1,0,0,1,1,0,1};
        if(c.seam)for(int i=6;i<12;i+=2)next[i]+=.375f;
        if(std::memcmp(next,uvs,sizeof(next))) {std::copy_n(next,12,uvs);++mesh.uvVersion;}
        const auto n=c.normal;float e2[3]={0,n[2],-n[1]};
        float matrix[12]={1,e2[0],n[0],0,0,e2[1],n[1],0,0,e2[2],n[2],0};
        std::copy_n(matrix,12,mesh.transform);std::fill_n(mesh.textureBindings,8,-1);
        if(!c.environment)mesh.textureBindings[c.slot]=0;
    }
};
HdRTVkFrame frame(const Case& c,uint32_t seed,bool pdf=false,int mode=0) {
    HdRTVkFrame f;f.width=32;f.height=32;f.seed=seed;f.guides=!pdf;f.orthographic=true;f.bounces=pdf?mode:0;
    if(!c.environment)std::fill_n(f.dome,3,1.f);else std::copy_n(c.uniform.data(),3,f.dome);
    auto n=c.normal;f.invView[0]=1;f.invView[5]=n[2];f.invView[6]=-n[1];
    f.invView[8]=n[0];f.invView[9]=n[1];f.invView[10]=n[2];
    f.invView[12]=.0234375f;f.invView[13]=n[1];f.invView[14]=n[2];f.invView[15]=1;
    f.invProj[0]=f.invProj[5]=.75f;f.invProj[10]=-4.95f;f.invProj[14]=-5.05f;f.invProj[15]=1;
    if(pdf) {f.width=mode==1?512:64;f.height=mode==1?256:32;}
    return f;
}
std::vector<HdRTVkEnvironment> environments(const Case& c) {
    std::vector<HdRTVkEnvironment> result;
    for(const auto& value:c.environments) {
        HdRTVkEnvironment env;env.texture=value.texture;std::copy_n(value.tint.data(),3,env.radiance);
        const float angle=value.rotation*float(3.141592653589793/180.0),cosine=std::cos(angle),sine=std::sin(angle);
        env.worldToDome[0]=cosine;env.worldToDome[8]=sine;env.worldToDome[2]=-sine;env.worldToDome[10]=cosine;result.push_back(env);
    }
    return result;
}
std::vector<HdRTVkTextureBinding> binding(const Case& c) {
    if(c.environment)return {};
    HdRTVkTextureBinding b;b.config[0]=c.texture;b.config[1]=c.wrapU;b.config[2]=c.wrapV;b.config[3]=c.filter;
    std::copy_n(c.affine.data(),3,b.uvU);std::copy_n(c.affine.data()+3,3,b.uvV);return {b};
}
void render(HdRTVkFurnace& gpu,const HdRTVkFrame& f,uint32_t samples,std::string& error) {
    require(gpu.BeginFrame(f,&error),error);
    for(uint32_t first=0;first<samples;first+=32)require(gpu.RenderBatch(first,std::min(32u,samples-first),&error),error);
    require(gpu.Resolve(samples,&error),error);require(gpu.ResolvedPixels(),"no resolved image");
}
void stats(FILE* file,const char* operation,const std::string& name,uint32_t seed,
           const HdRTVkEditStats& mesh,const HdRTVkEditStats& texture,const HdRTVkFurnace& gpu) {
    const auto frame=gpu.FrameStats();
    std::fprintf(file,"%s,%s,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%u,%u\n",
        operation,name.c_str(),seed,mesh.blasBuilds,mesh.blasRefits,mesh.tlasBuilds,mesh.tlasUpdates,
        mesh.geometryUploadBytes,mesh.tableUploadBytes,mesh.uvUploadBytes,texture.textureUploadBytes,
        texture.textureCacheHits,frame.traceSubmits,frame.tracedSamples,frame.readbackBytes,
        gpu.ValidationErrors(),gpu.ValidationWarnings());
}
int main(int argc,char** argv) {
    if(argc!=4)return 15;
    try {
        const fs::path out=argv[3];fs::create_directories(out);std::string error;
        auto images=textures();const auto rows=cases();Geometry geometry;
        HdRTVkOptions options;options.spvPath=argv[1];options.validation=true;
        HdRTVkFurnace gpu;require(gpu.Init(options,&error),error);require(gpu.ValidationActive(),"validation inactive");
        std::printf("device=%s validation=active textures=%zu\n",gpu.DeviceName().c_str(),images.size());
        FILE* csv=nullptr;fopen_s(&csv,(out/"stats.csv").string().c_str(),"w");require(csv,"stats open failed");
        std::fprintf(csv,"operation,case,seed,blas_builds,blas_refits,tlas_builds,tlas_updates,geometry_bytes,table_bytes,uv_bytes,texture_bytes,cache_hits,trace_submits,traced_samples,readback_bytes,errors,warnings\n");
        require(gpu.SetLights({},&error),error);
        for(const auto& c:rows) {
            geometry.configure(c);HdRTVkEditStats ms,ts;
            require(gpu.SetTextures(images,binding(c),environments(c),&ts,&error),error);
            require(gpu.SetMeshes({geometry.mesh},&ms,&error),error);
            for(auto seed:c.seeds) {
                render(gpu,frame(c,seed),c.spp,error);
                save(out/(c.name+"_"+std::to_string(seed)+".rgba32f"),gpu.ResolvedPixels(),32*32*16);
                require(gpu.ResolvedGuides(),"missing guides");
                save(out/(c.name+"_"+std::to_string(seed)+".guides"),gpu.ResolvedGuides(),32*32*sizeof(HdRTVkGuide));
                stats(csv,"render",c.name,seed,ms,ts,gpu);
            }
            HdRTVkEditStats repeatMesh,repeatTextures;
            require(gpu.SetTextures(images,binding(c),environments(c),&repeatTextures,&error),error);
            require(gpu.SetMeshes({geometry.mesh},&repeatMesh,&error),error);
            stats(csv,"unchanged",c.name,0,repeatMesh,repeatTextures,gpu);
            std::printf("rendered %s\n",c.name.c_str());std::fflush(stdout);
        }
        gpu.Destroy();stats(csv,"destroy","production",0,{}, {},gpu);std::fclose(csv);
        options.spvPath=argv[2];HdRTVkFurnace pdf;
        require(pdf.Init(options,&error),error);require(pdf.ValidationActive(),"PDF validation inactive");
        for(const auto& c:rows) {
            if(!c.environment)continue;
            geometry.configure(c);require(pdf.SetTextures(images,{},environments(c),nullptr,&error),error);
            require(pdf.SetMeshes({geometry.mesh},nullptr,&error),error);require(pdf.SetLights({},&error),error);
            for(int mode=1;mode<=2;++mode) {
                auto f=frame(c,197,true,mode);render(pdf,f,1,error);
                save(out/(c.name+(mode==1?".pdf_samples":".pdf_values")),pdf.ResolvedPixels(),size_t(f.width)*f.height*16);
            }
        }
        pdf.Destroy();FILE* validation=nullptr;fopen_s(&validation,(out/"validation.csv").string().c_str(),"w");require(validation,"validation open failed");
        std::fprintf(validation,"pipeline,errors,warnings\nproduction,%u,%u\npdf,%u,%u\n",gpu.ValidationErrors(),gpu.ValidationWarnings(),pdf.ValidationErrors(),pdf.ValidationWarnings());
        std::fclose(validation);return 0;
    } catch(const std::exception& e) {std::fprintf(stderr,"probe: %s\n",e.what());return 15;}
}
