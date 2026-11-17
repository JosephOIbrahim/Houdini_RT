// Supplementary deterministic guide/caching edges. Expectations live in judge.py.
#include "vkFurnace.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>
namespace fs = std::filesystem;
constexpr uint32_t Width=32, Height=32, Samples=32;
void require(bool okay,const std::string& message) {
    if (!okay) throw std::runtime_error(message);
}
void save(const fs::path& path,const void* data,size_t bytes) {
    FILE* f=nullptr; fopen_s(&f,path.string().c_str(),"wb"); require(f,"open "+path.string());
    const size_t count=std::fwrite(data,1,bytes,f); std::fclose(f); require(count==bytes,"short output");
}
struct Plane {
    std::vector<float> positions={-4,-4,0, 4,-4,0, 4,4,0, -4,4,0};
    std::vector<float> normals;
    std::vector<uint32_t> indices={0,1,2,0,2,3};
    std::vector<int32_t> faces={101,303};
    HdRTVkMesh mesh;
    Plane() {
        mesh.id="/AuthoredPlane"; mesh.geometryVersion=mesh.topologyVersion=1;
        mesh.normalVersion=1; mesh.primId=20000001; mesh.cornerNormals=true;
        mesh.albedo[0]=.2f; mesh.albedo[1]=.4f; mesh.albedo[2]=.6f;
        seam(false);
    }
    void seam(bool updated) {
        normals.clear();
        for(int face=0;face<2;++face) for(int corner=0;corner<3;++corner) {
            const float tilt=face ? -.6f : .6f;
            normals.insert(normals.end(),{updated ? 0.f : tilt,updated ? tilt : 0.f,.8f});
        }
    }
    void constant(float x,float y,float z) {
        normals.clear(); for(int i=0;i<6;++i) normals.insert(normals.end(),{x,y,z});
        if(faces[0]!=71) { ++mesh.geometryVersion; ++mesh.topologyVersion; }
        faces={71,71};
    }
    HdRTVkMesh view() {
        mesh.positions=positions.data(); mesh.vertexCount=4;
        mesh.normals=normals.data(); mesh.normalCount=normals.size()/3;
        mesh.indices=indices.data(); mesh.triangleCount=2; mesh.faceIds=faces.data();
        return mesh;
    }
};
HdRTVkFrame frame(bool perspective=false,bool rotated=false,float extent=1) {
    HdRTVkFrame f; f.width=Width; f.height=Height; f.seed=193; f.bounces=0; f.guides=true;
    f.orthographic=!perspective;
    f.invView[0]=f.invView[5]=f.invView[10]=f.invView[15]=1; f.invView[14]=3;
    if(rotated) {
        const float view[16]={0,0,-1,0, 0,1,0,0, 1,0,0,0, 3,0,0,1};
        std::copy(view,view+16,f.invView);
    }
    // OpenGL clip convention, near=.1, far=10; 90-degree perspective when selected.
    f.invProj[0]=f.invProj[5]=extent;
    if(perspective) { f.invProj[11]=-4.95f; f.invProj[14]=-1; f.invProj[15]=5.05f; }
    else { f.invProj[10]=-4.95f; f.invProj[14]=-5.05f; f.invProj[15]=1; }
    return f;
}
HdRTVkLight emitter(int type,bool zero=false,bool diffuseZero=false) {
    HdRTVkLight light; light.positionType[3]=float(type);
    const float radius=.75f;
    light.basisUArea[0]=radius; light.basisVCos[1]=type==0 ? .5f : radius;
    light.basisUArea[3]=type==0 ? 1.5f : (type==1 ? 1.f : 4.f)*3.14159265358979323846f*radius*radius;
    light.normalRadius[2]=1; light.normalRadius[3]=radius;
    const float energy[3]={.25f,.5f,.75f};
    for(int k=0;k<3;++k) {
        light.radiance[k]=zero || diffuseZero ? 0 : energy[k];
        light.selection[k+1]=zero ? 0 : energy[k];
    }
    return light;
}
class Probe {
public:
    HdRTVkFurnace gpu;
    std::string error;
    fs::path output;
    FILE* stats=nullptr;
    explicit Probe(const fs::path& out):output(out) {
        fs::create_directories(out);
        fopen_s(&stats,(out/"stats.csv").string().c_str(),"w"); require(stats,"stats open");
        std::fprintf(stats,"case,blas_builds,blas_refits,tlas_builds,tlas_updates,geometry_upload,table_upload,trace_submits,samples,readback_count,readback_bytes,errors,warnings\n");
    }
    ~Probe() { if(stats) std::fclose(stats); }
    HdRTVkEditStats meshes(const std::vector<HdRTVkMesh>& input) {
        HdRTVkEditStats edit; require(gpu.SetMeshes(input,&edit,&error),error); return edit;
    }
    void lights(const std::vector<HdRTVkLight>& input) { require(gpu.SetLights(input,&error),error); }
    void render(const std::string& name,const HdRTVkFrame& f,const HdRTVkEditStats& edit={}) {
        require(gpu.BeginFrame(f,&error),error);
        require(gpu.RenderBatch(0,Samples,&error),error); require(gpu.Resolve(Samples,&error),error);
        require(gpu.ResolvedPixels() && gpu.ResolvedGuides(),"resolved outputs missing");
        save(output/(name+".rgba32f"),gpu.ResolvedPixels(),Width*Height*16);
        save(output/(name+".guides"),gpu.ResolvedGuides(),Width*Height*sizeof(HdRTVkGuide));
        const auto s=gpu.FrameStats();
        std::fprintf(stats,"%s,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%llu,%d,%d\n",name.c_str(),
            edit.blasBuilds,edit.blasRefits,edit.tlasBuilds,edit.tlasUpdates,edit.geometryUploadBytes,
            edit.tableUploadBytes,s.traceSubmits,s.tracedSamples,s.readbackCount,s.readbackBytes,
            gpu.ValidationErrors(),gpu.ValidationWarnings());
        std::fflush(stats); std::printf("wrote %s\n",name.c_str()); std::fflush(stdout);
    }
};
int main(int argc,char** argv) {
    if(argc!=3) return 15;
    try {
        static_assert(sizeof(HdRTVkGuide)==64);
        Probe p(argv[2]); HdRTVkOptions options; options.spvPath=argv[1]; options.validation=true;
        require(p.gpu.Init(options,&p.error),p.error);
        require(p.gpu.ValidationActive(),"Vulkan validation unavailable");
        std::printf("device=%s validation=active\n",p.gpu.DeviceName().c_str());
        Plane plane; auto edit=p.meshes({plane.view()}); p.lights({});
        p.render("hard_seam",frame(),edit);
        plane.seam(true); ++plane.mesh.normalVersion;
        edit=p.meshes({plane.view()}); p.render("normal_update",frame(),edit);
        // Normals vary independently of position/topology. A transform only affects TLAS.
        plane.constant(1,1,1); ++plane.mesh.normalVersion;
        plane.mesh.transform[0]=2; plane.mesh.transform[5]=.5f;
        edit=p.meshes({plane.view()}); p.render("nonuniform",frame(),edit);
        plane.mesh.transform[0]=-2;
        edit=p.meshes({plane.view()}); p.render("reflected",frame(),edit);
        plane.constant(.6f,0,.8f); ++plane.mesh.normalVersion;
        plane.mesh.transform[0]=plane.mesh.transform[5]=1;
        edit=p.meshes({plane.view()}); p.render("perspective",frame(true),edit);
        const float rotation[12]={0,0,1,0, 0,1,0,0, -1,0,0,0};
        std::copy(rotation,rotation+12,plane.mesh.transform);
        edit=p.meshes({plane.view()});
        p.render("rotated_perspective",frame(true,true),edit);
        p.render("rotated_ortho",frame(false,true));
        // A rejected update invalidates the partial scene. A subsequent valid snapshot recovers.
        HdRTVkMesh malformed=plane.view(); malformed.normalCount=3;
        HdRTVkEditStats rejected; const bool accepted=p.gpu.SetMeshes({malformed},&rejected,&p.error);
        FILE* bad=nullptr; fopen_s(&bad,(p.output/"malformed.txt").string().c_str(),"w"); require(bad,"malformed open");
        std::fprintf(bad,"accepted=%d has_scene=%d error=%s\n",accepted,p.gpu.HasScene(),p.error.c_str()); std::fclose(bad);
        require(!accepted,"undersized corner normals accepted"); require(!p.gpu.HasScene(),"rejected scene retained");
        edit=p.meshes({plane.view()}); p.lights({});
        p.render("recovered",frame(false,true),edit);
        edit=p.meshes({});
        p.lights({emitter(0)}); require(p.gpu.HasScene(),"finite light missing scene coverage");
        p.render("rect_only",frame(false,false,2),edit);
        p.lights({emitter(1)}); p.render("disk_only",frame(false,false,2));
        p.lights({emitter(2)}); p.render("sphere_only",frame(false,false,2));
        p.lights({emitter(0,true)}); p.render("zero_emitter",frame(false,false,2));
        p.lights({emitter(0,false,true)}); p.render("diffuse_zero",frame(false,false,2));
        p.lights({}); p.render("empty",frame(false,false,2));
        p.gpu.Destroy();
        FILE* teardown=nullptr; fopen_s(&teardown,(p.output/"teardown.txt").string().c_str(),"w"); require(teardown,"teardown open");
        std::fprintf(teardown,"errors=%d warnings=%d\n",p.gpu.ValidationErrors(),p.gpu.ValidationWarnings()); std::fclose(teardown);
        require(p.gpu.ValidationErrors()==0 && p.gpu.ValidationWarnings()==0,"Vulkan validation messages");
        return 0;
    } catch(const std::exception& e) { std::fprintf(stderr,"edge probe failure: %s\n",e.what()); return 15; }
}
