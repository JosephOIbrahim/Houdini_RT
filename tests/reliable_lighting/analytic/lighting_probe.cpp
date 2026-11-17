// Supplementary native transport probe. The judge is a separate Python process.
#include "vkFurnace.h"
#include <algorithm>
#include <array>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs=std::filesystem;
struct Case {
    std::string name;
    uint32_t width=24,height=24,spp=2048;
    float extent=1.2f;
    std::array<float,3> albedo{},normal{},dome{};
    std::vector<uint32_t> seeds;
    std::vector<HdRTVkLight> lights;
    bool blocker=false;
    std::array<float,4> block{}; // x, halfY, minZ, maxZ
};
#include "frozen/fixtures.hpp"
void require(bool okay,const std::string& text) { if(!okay) throw std::runtime_error(text); }
void save(const fs::path& path,const void* data,size_t bytes) {
    FILE* file=nullptr; fopen_s(&file,path.string().c_str(),"wb"); require(file,"cannot open output: "+path.string());
    const size_t count=std::fwrite(data,1,bytes,file); std::fclose(file); require(count==bytes,"incomplete output");
}
struct Owned {
    std::vector<float> p,n;
    std::vector<uint32_t> indices;
    std::vector<int32_t> faces;
    HdRTVkMesh mesh;
    void bind() {
        mesh.positions=p.data(); mesh.normals=n.data(); mesh.indices=indices.data();
        mesh.vertexCount=p.size()/3; mesh.triangleCount=indices.size()/3; mesh.normalCount=n.size()/3;
        mesh.faceIds=faces.data();
    }
};
std::vector<Owned> geometry(const Case& c,uint64_t normalVersion) {
    Owned receiver;
    receiver.mesh.id="/Receiver"; receiver.mesh.geometryVersion=receiver.mesh.topologyVersion=1;
    receiver.mesh.normalVersion=normalVersion; receiver.mesh.primId=17;
    receiver.p={-2,-2,0, 2,-2,0, 2,2,0, -2,2,0};
    for(int i=0;i<4;i++) receiver.n.insert(receiver.n.end(),c.normal.begin(),c.normal.end());
    receiver.indices={0,1,2,0,2,3}; receiver.faces={41,41};
    std::copy(c.albedo.begin(),c.albedo.end(),receiver.mesh.albedo); receiver.mesh.albedo[3]=1;
    std::vector<Owned> result; result.push_back(receiver);
    if(c.blocker) {
        Owned wall; wall.mesh.id="/Blocker"; wall.mesh.geometryVersion=wall.mesh.topologyVersion=normalVersion+100;
        wall.mesh.normalVersion=normalVersion+100; wall.mesh.primId=29;
        const auto& b=c.block;
        wall.p={b[0],-b[1],b[2], b[0],b[1],b[2], b[0],b[1],b[3], b[0],-b[1],b[3]};
        wall.n={1,0,0,1,0,0,1,0,0,1,0,0}; wall.indices={0,1,2,0,2,3}; wall.faces={93,93};
        std::fill(wall.mesh.albedo,wall.mesh.albedo+3,0); wall.mesh.albedo[3]=1;
        result.push_back(wall);
    }
    for(auto& object:result) object.bind();
    return result;
}
std::vector<HdRTVkMesh> views(const std::vector<Owned>& objects) {
    std::vector<HdRTVkMesh> result;
    for(const auto& object:objects) result.push_back(object.mesh);
    return result;
}
HdRTVkFrame frame(const Case& c,uint32_t seed) {
    HdRTVkFrame f; f.width=c.width; f.height=c.height; f.seed=seed; f.bounces=0; f.guides=true;
    f.orthographic=true; std::copy(c.dome.begin(),c.dome.end(),f.dome);
    f.invView[0]=f.invView[5]=f.invView[10]=f.invView[15]=1; f.invView[14]=1;
    // Orthographic clip near=.1, far=10, eye z=1. Near-plane ray origin z=.9.
    f.invProj[0]=f.invProj[5]=c.extent; f.invProj[10]=-4.95f; f.invProj[14]=-5.05f; f.invProj[15]=1;
    return f;
}
void trace(HdRTVkFurnace& gpu,const Case& c,uint32_t seed,std::string& error) {
    require(gpu.BeginFrame(frame(c,seed),&error),error);
    for(uint32_t first=0;first<c.spp;first+=32) require(gpu.RenderBatch(first,std::min(32u,c.spp-first),&error),error);
    require(gpu.Resolve(c.spp,&error),error);
    require(gpu.ResolvedPixels()!=nullptr && gpu.ResolvedGuides()!=nullptr,"missing resolved beauty or guides");
}
int main(int argc,char** argv) {
    if(argc!=3) return 15;
    try {
        static_assert(sizeof(HdRTVkGuide)==64);
        const fs::path out=argv[2]; fs::create_directories(out);
        HdRTVkOptions options; options.spvPath=argv[1]; options.validation=true;
        HdRTVkFurnace gpu; std::string error;
        require(gpu.Init(options,&error),error); require(gpu.ValidationActive(),"Vulkan validation unavailable");
        std::printf("device=%s validation=active\n",gpu.DeviceName().c_str()); std::fflush(stdout);
        FILE* csv=nullptr; fopen_s(&csv,(out/"stats.csv").string().c_str(),"w"); require(csv,"cannot create stats");
        std::fprintf(csv,"case,seed,spp,width,height,trace_submits,traced_samples,resolve_submits,readback_count,readback_bytes,errors,warnings\n");
        uint64_t version=1;
        for(const auto& c:cases()) {
            auto objects=geometry(c,version++);
            require(gpu.SetMeshes(views(objects),nullptr,&error),error);
            require(gpu.SetLights(c.lights,&error),error);
            for(const auto seed:c.seeds) {
                trace(gpu,c,seed,error);
                const std::string stem=c.name+"_"+std::to_string(seed);
                save(out/(stem+".rgba32f"),gpu.ResolvedPixels(),size_t(c.width)*c.height*16);
                save(out/(stem+".guides"),gpu.ResolvedGuides(),size_t(c.width)*c.height*sizeof(HdRTVkGuide));
                const auto s=gpu.FrameStats();
                std::fprintf(csv,"%s,%u,%u,%u,%u,%llu,%llu,%llu,%llu,%llu,%d,%d\n",c.name.c_str(),seed,c.spp,c.width,c.height,
                    s.traceSubmits,s.tracedSamples,s.resolveSubmits,s.readbackCount,s.readbackBytes,gpu.ValidationErrors(),gpu.ValidationWarnings());
                std::fflush(csv); std::printf("wrote %s\n",stem.c_str()); std::fflush(stdout);
                if(seed==c.seeds.front() && (c.name=="rect" || c.name=="disk_shadow" || c.name=="mixed" || c.name=="distant_tilted_normal" || c.name=="blocker_behind_light")) {
                    HdRTVkFurnace fresh; require(fresh.Init(options,&error),error);
                    require(fresh.SetMeshes(views(objects),nullptr,&error),error); require(fresh.SetLights(c.lights,&error),error);
                    trace(fresh,c,seed,error);
                    save(out/(stem+".fresh.rgba32f"),fresh.ResolvedPixels(),size_t(c.width)*c.height*16);
                    save(out/(stem+".fresh.guides"),fresh.ResolvedGuides(),size_t(c.width)*c.height*sizeof(HdRTVkGuide));
                    fresh.Destroy(); require(fresh.ValidationErrors()==0 && fresh.ValidationWarnings()==0,"fresh validation messages");
                }
            }
        }
        std::fclose(csv); gpu.Destroy();
        FILE* final=nullptr; fopen_s(&final,(out/"teardown.txt").string().c_str(),"w"); require(final,"cannot record teardown");
        std::fprintf(final,"errors=%d warnings=%d\n",gpu.ValidationErrors(),gpu.ValidationWarnings()); std::fclose(final);
        require(gpu.ValidationErrors()==0 && gpu.ValidationWarnings()==0,"Vulkan validation messages");
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"lighting probe failure: %s\n",e.what()); return 15; }
}
