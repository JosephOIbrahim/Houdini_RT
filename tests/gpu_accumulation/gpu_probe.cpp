// Supplementary batch/resolve probe. Artifacts are judged by judge_gpu.py.
#include "vkFurnace.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs = std::filesystem;
void require(bool ok, const std::string& message) { if (!ok) throw std::runtime_error(message); }
void save(const fs::path& path, const float* data, size_t size) {
    FILE* file=nullptr; fopen_s(&file,path.string().c_str(),"wb");
    require(file!=nullptr,"cannot open pixel output");
    const auto written=std::fwrite(data,sizeof(float),size,file);
    std::fclose(file); require(written==size,"cannot write pixels");
}
struct Fixture {
    std::vector<float> positions={-.61f,-.53f,0, .61f,-.53f,0, .61f,.53f,0, -.61f,.53f,0};
    std::vector<float> normals={0,0,1,0,0,1,0,0,1,0,0,1};
    std::vector<uint32_t> indices={0,1,2,0,2,3};
    HdRTVkMesh mesh;
    Fixture() {
        mesh.id="analytic_quad"; mesh.geometryVersion=mesh.topologyVersion=1;
        mesh.positions=positions.data(); mesh.normals=normals.data(); mesh.indices=indices.data();
        mesh.vertexCount=4; mesh.triangleCount=2;
        mesh.albedo[0]=.2f; mesh.albedo[1]=.5f; mesh.albedo[2]=.8f;
    }
};
HdRTVkFrame frame() {
    HdRTVkFrame f; f.width=128; f.height=96; f.orthographic=true;
    f.bounces=1; f.seed=927361; f.dome[0]=.7f; f.dome[1]=1.1f; f.dome[2]=1.4f;
    f.invView[0]=f.invView[5]=f.invView[10]=f.invView[15]=1; f.invView[14]=3;
    f.invProj[0]=1.5f; f.invProj[5]=1.125f; f.invProj[10]=-1; f.invProj[15]=1;
    return f;
}
int main(int argc,char** argv) {
    if (argc!=3) return 15;
    try {
        const fs::path out=argv[2]; fs::create_directories(out);
        HdRTVkOptions options; options.spvPath=argv[1]; options.validation=true;
        HdRTVkFurnace gpu; std::string error; Fixture fixture;
        require(gpu.Init(options,&error),error); require(gpu.ValidationActive(),"validation unavailable");
        std::printf("device=%s validation=active\n",gpu.DeviceName().c_str());
        require(gpu.SetMeshes({fixture.mesh},nullptr,&error),error);
        FILE* csv=nullptr; fopen_s(&csv,(out/"stats.csv").string().c_str(),"w");
        require(csv!=nullptr,"cannot open statistics");
        std::fprintf(csv,"case,width,height,samples,batch,trace_submits,trace_dispatches,traced_samples,resolve_submits,resolve_dispatches,readback_count,readback_bytes,clear_submits,compatibility_readback_submits,accum_bytes,resolve_bytes,readback_allocated,accum_device_local,resolve_device_local,last_batch_seconds,max_batch_seconds,last_resolve_seconds,total_resolve_seconds,errors,warnings\n");
        auto capture=[&](const std::string& name,const HdRTVkFrame& f,uint32_t samples,uint32_t batch) {
            require(gpu.Resolve(samples,&error),error);
            const float* pixels=gpu.ResolvedPixels(); require(pixels!=nullptr,"null resolved pixels");
            save(out/(name+".f32"),pixels,size_t(f.width)*f.height*4);
            const auto s=gpu.FrameStats();
            std::fprintf(csv,"%s,%u,%u,%u,%u,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%d,%d,%.9f,%.9f,%.9f,%.9f,%d,%d\n",
                name.c_str(),f.width,f.height,samples,batch,s.traceSubmits,s.traceDispatches,s.tracedSamples,
                s.resolveSubmits,s.resolveDispatches,s.readbackCount,s.readbackBytes,s.clearSubmits,
                s.compatibilityReadbackSubmits,s.accumulationBytes,s.resolveBytes,s.readbackBytesAllocated,
                int(s.accumulationDeviceLocal),int(s.resolveDeviceLocal),s.lastBatchSeconds,s.maxBatchSeconds,
                s.lastResolveSeconds,s.totalResolveSeconds,gpu.ValidationErrors(),gpu.ValidationWarnings());
            std::fflush(csv); std::printf("wrote %s\n",name.c_str()); std::fflush(stdout);
        };
        auto trace=[&](uint32_t first,uint32_t until,uint32_t batch) {
            while(first<until) {
                const uint32_t count=std::min(batch,until-first);
                require(gpu.RenderBatch(first,count,&error),error); first+=count;
            }
        };
        const auto original=frame();
        for(uint32_t batch: {1u,2u,8u,32u}) {
            require(gpu.BeginFrame(original,&error),error); uint32_t done=0;
            for(uint32_t end: {1u,7u,33u,257u}) {
                trace(done,end,batch); done=end;
                capture("batch"+std::to_string(batch)+"_"+std::to_string(end),original,end,batch);
            }
        }
        require(gpu.BeginFrame(original,&error),error);
        require(!gpu.RenderBatch(0,0,&error),"zero batch accepted");
        require(!gpu.RenderBatch(0,33,&error),"unbounded batch accepted");
        require(!gpu.RenderBatch(1,1,&error),"noncontiguous first sample accepted");
        require(!gpu.Resolve(1,&error),"untraced resolve accepted");
        trace(0,257,8); capture("invalid_input_recovery",original,257,8);
        require(gpu.BeginFrame(original,&error),error); trace(0,7,8);
        require(gpu.BeginFrame(original,&error),error); trace(0,257,8);
        capture("discard_reset",original,257,8);
        auto resized=original; resized.width=96; resized.height=128;
        require(gpu.BeginFrame(resized,&error),error); trace(0,257,8);
        capture("resized",resized,257,8);
        require(gpu.BeginFrame(original,&error),error); trace(0,257,8);
        capture("resize_back",original,257,8);
        fixture.mesh.albedo[0]=.6f; fixture.mesh.albedo[1]=.1f; fixture.mesh.albedo[2]=.3f;
        require(gpu.SetMeshes({fixture.mesh},nullptr,&error),error);
        require(gpu.BeginFrame(original,&error),error); trace(0,257,8);
        capture("color_edit",original,257,8);
        fixture.mesh.albedo[0]=.2f; fixture.mesh.albedo[1]=.5f; fixture.mesh.albedo[2]=.8f;
        require(gpu.SetMeshes({fixture.mesh},nullptr,&error),error);
        auto lit=original; lit.dome[0]*=2; lit.dome[1]*=2; lit.dome[2]*=2;
        require(gpu.BeginFrame(lit,&error),error); trace(0,257,8);
        capture("dome_edit",lit,257,8);
        auto camera=original; camera.invView[12]=.25f;
        require(gpu.BeginFrame(camera,&error),error); trace(0,257,8);
        capture("camera_edit",camera,257,8);
        auto seed=original; seed.seed=563219;
        require(gpu.BeginFrame(seed,&error),error); trace(0,257,8);
        capture("seed_edit",seed,257,8);
        require(gpu.SetMeshes({},nullptr,&error),error);
        require(gpu.BeginFrame(original,&error),error); trace(0,257,8);
        capture("empty",original,257,8);
        require(gpu.SetMeshes({fixture.mesh},nullptr,&error),error);
        require(gpu.BeginFrame(original,&error),error); trace(0,257,8);
        capture("after_empty",original,257,8);
        std::fclose(csv);
        gpu.Destroy();
        FILE* teardown=nullptr; fopen_s(&teardown,(out/"teardown.txt").string().c_str(),"w");
        require(teardown!=nullptr,"cannot write teardown receipt");
        std::fprintf(teardown,"errors=%d warnings=%d\n",gpu.ValidationErrors(),gpu.ValidationWarnings());
        std::fclose(teardown);
        require(gpu.ValidationErrors()==0 && gpu.ValidationWarnings()==0,"Vulkan validation messages");
        return 0;
    } catch(const std::exception& exception) {
        std::fprintf(stderr,"probe failure: %s\n",exception.what()); return 15;
    }
}
