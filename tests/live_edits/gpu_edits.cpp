#include "vkFurnace.h"
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs = std::filesystem;
struct Owned {
    std::string id;
    std::vector<float> p, n;
    std::vector<uint32_t> i;
    uint64_t gv=1, tv=1;
    std::array<float,12> transform={1,0,0,0, 0,1,0,0, 0,0,1,0};
    std::array<float,4> color={.2f,.4f,.6f,1};
    bool visible=true;
    HdRTVkMesh view() const {
        HdRTVkMesh v; v.id=id; v.geometryVersion=gv; v.topologyVersion=tv;
        v.positions=p.data(); v.normals=n.data(); v.indices=i.data();
        v.vertexCount=p.size()/3; v.triangleCount=i.size()/3; v.visible=visible;
        std::memcpy(v.transform,transform.data(),sizeof(v.transform));
        std::memcpy(v.albedo,color.data(),sizeof(v.albedo)); return v;
    }
};
std::vector<HdRTVkMesh> views(const std::vector<Owned>& meshes) {
    std::vector<HdRTVkMesh> result;
    for (auto& m:meshes) result.push_back(m.view()); return result;
}
void require(bool ok, const std::string& error) { if(!ok) throw std::runtime_error(error); }
double now() { return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
std::vector<float> render(HdRTVkFurnace& gpu, const HdRTVkFrame& frame, int spp) {
    std::string error; require(gpu.BeginFrame(frame,&error),error);
    for(int s=0;s<spp;++s) require(gpu.RenderPass(s,&error),error);
    std::vector<float> result(gpu.Accumulation(),gpu.Accumulation()+frame.width*frame.height*4);
    for(auto& v:result) v/=spp; return result;
}
void save(const fs::path& path,const std::vector<float>& pixels) {
    FILE* f=nullptr; fopen_s(&f,path.string().c_str(),"wb");
    require(f!=nullptr,"cannot open image output");
    const auto count=std::fwrite(pixels.data(),sizeof(float),pixels.size(),f);
    std::fclose(f); require(count==pixels.size(),"cannot write image output");
}
Owned quad(std::string id) {
    Owned m; m.id=id;
    m.p={-.6f,-.5f,0, .6f,-.5f,0, .6f,.5f,0, -.6f,.5f,0};
    m.n={0,0,1, 0,0,1, 0,0,1, 0,0,1}; m.i={0,1,2, 0,2,3}; return m;
}
int main(int argc,char** argv) {
    if(argc!=3) return 15;
    try {
        fs::path dir=argv[2]; fs::create_directories(dir);
        HdRTVkOptions options; options.spvPath=argv[1]; options.validation=true;
        HdRTVkFrame frame; frame.width=128; frame.height=96; frame.orthographic=true;
        frame.bounces=1; frame.seed=347891; frame.dome[0]=1; frame.dome[1]=.75f; frame.dome[2]=.5f;
        frame.invView[0]=frame.invView[5]=frame.invView[10]=frame.invView[15]=1;
        frame.invView[14]=3;
        frame.invProj[0]=1.5f; frame.invProj[5]=1.125f; frame.invProj[10]=-1; frame.invProj[15]=1;
        const int spp=32;
        HdRTVkFurnace gpu; std::string error;
        require(gpu.Init(options,&error),error);
        require(gpu.ValidationActive(),"Vulkan validation layer unavailable");
        std::printf("device=%s validation=active\n",gpu.DeviceName().c_str());
        FILE* stats=nullptr; fopen_s(&stats,(dir/"stats.csv").string().c_str(),"w");
        require(stats!=nullptr,"cannot create stats CSV");
        std::fprintf(stats,"case,blas_builds,blas_refits,tlas_builds,tlas_updates,geometry_bytes,table_bytes,has_scene,submit_in_range,errors,warnings\n");
        auto step=[&](const std::string& name,const std::vector<Owned>& meshes) {
            HdRTVkEditStats edit; const auto input=views(meshes);
            require(gpu.SetMeshes(input,&edit,&error),error);
            const double start=now(); const auto pixels=render(gpu,frame,spp); const double end=now();
            const bool timestamp=!gpu.HasScene() || (gpu.LastPassSubmitSeconds()>=start && gpu.LastPassSubmitSeconds()<=end);
            save(dir/(name+".live.f32"),pixels);
            HdRTVkFurnace fresh; require(fresh.Init(options,&error),error);
            require(fresh.SetMeshes(input,nullptr,&error),error);
            save(dir/(name+".fresh.f32"),render(fresh,frame,spp));
            fresh.Destroy();
            std::fprintf(stats,"%s,%llu,%llu,%llu,%llu,%llu,%llu,%d,%d,%d,%d\n",name.c_str(),
                edit.blasBuilds,edit.blasRefits,edit.tlasBuilds,edit.tlasUpdates,
                edit.geometryUploadBytes,edit.tableUploadBytes,int(gpu.HasScene()),int(timestamp),
                gpu.ValidationErrors()+fresh.ValidationErrors(),gpu.ValidationWarnings()+fresh.ValidationWarnings());
            std::fflush(stats); std::printf("wrote %s\n",name.c_str()); std::fflush(stdout);
        };
        std::vector<Owned> scene={quad("quadA")};
        step("initial",scene); step("unchanged",scene);
        scene[0].transform[3]=.35f; step("transform",scene);
        scene[0].color={.7f,.2f,.35f,1}; step("color",scene);
        scene[0].visible=false; step("hidden",scene);
        scene[0].visible=true; step("visible",scene);
        for(size_t i=0;i<scene[0].p.size();i+=3) scene[0].p[i]*=.7f;
        scene[0].gv=2; step("points",scene);
        scene[0].p.insert(scene[0].p.end(),{0,0,0}); scene[0].n.insert(scene[0].n.end(),{0,0,1});
        scene[0].i={0,1,4, 1,2,4, 2,3,4, 3,0,4}; scene[0].gv=3; scene[0].tv=3;
        step("topology",scene);
        scene[0].transform={-1.1f,0,0,.35f, 0,1.2f,0,.1f, .4f,0,.8f,0};
        step("mirror_nonuniform",scene);
        auto baked=scene;
        for(size_t i=0;i<baked[0].p.size();i+=3) {
            const float x=baked[0].p[i], y=baked[0].p[i+1], z=baked[0].p[i+2];
            baked[0].p[i]=-1.1f*x+.35f; baked[0].p[i+1]=1.2f*y+.1f; baked[0].p[i+2]=.4f*x+.8f*z;
            baked[0].n[i]=.4f/1.1f; baked[0].n[i+1]=0; baked[0].n[i+2]=1;
        }
        baked[0].transform={1,0,0,0, 0,1,0,0, 0,0,1,0};
        HdRTVkFurnace reference; require(reference.Init(options,&error),error);
        require(reference.SetMeshes(views(baked),nullptr,&error),error);
        save(dir/"mirror_nonuniform.baked.f32",render(reference,frame,spp)); reference.Destroy();
        require(reference.ValidationErrors()==0,"baked reference validation failure");
        auto second=quad("quadB"); second.gv=7; second.tv=7;
        second.transform={.4f,0,0,-.9f, 0,.5f,0,-.2f, 0,0,1,0}; second.color={.9f,.8f,.1f,1};
        scene.push_back(second); step("insert",scene);
        const auto first=scene.front(); scene.erase(scene.begin()); step("remove",scene);
        scene.push_back(first); scene.back().gv=8; scene.back().tv=8; step("readd",scene);
        scene.clear(); step("empty",scene);
        scene={quad("empty_mesh")}; scene[0].p.clear(); scene[0].n.clear(); scene[0].i.clear();
        step("empty_mesh",scene);
        scene={quad("quadA")}; scene[0].gv=9; scene[0].tv=9; step("after_empty",scene);
        auto duplicate=views(scene); duplicate.push_back(duplicate[0]); HdRTVkEditStats failed;
        require(!gpu.SetMeshes(duplicate,&failed,&error),"duplicate ids unexpectedly accepted");
        require(!gpu.HasScene(),"failed update left a dispatchable partial scene");
        step("after_failure",scene);
        scene[0].transform[0]=0; step("zero_scale",scene);
        step("zero_scale_unchanged",scene);
        scene[0].visible=false; step("zero_scale_hidden",scene);
        scene[0].transform[0]=1; scene[0].visible=true; step("zero_scale_recovery",scene);
        scene[0].transform[3]=std::numeric_limits<float>::infinity(); step("infinite_translation",scene);
        scene[0].transform[3]=std::numeric_limits<float>::quiet_NaN(); scene[0].visible=false;
        step("nan_hidden",scene); step("nan_hidden_unchanged",scene);
        scene[0].transform[3]=0; scene[0].visible=true; step("nonfinite_recovery",scene);
        gpu.Destroy();
        std::fprintf(stats,"teardown,0,0,0,0,0,0,0,1,%d,%d\n",gpu.ValidationErrors(),gpu.ValidationWarnings());
        std::fclose(stats); std::printf("artifacts written; separate judge required\n");
        return gpu.ValidationErrors()?14:0;
    } catch(const std::exception& e) { std::fprintf(stderr,"GPU probe failed: %s\n",e.what()); return 15; }
}
