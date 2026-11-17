// Production OpenPBR + actual textured-environment NEE/escape MIS.
#include "vkFurnace.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <memory>
#include <stdexcept>
#include <string>
namespace fs=std::filesystem;
void check(bool ok,const std::string& s){if(!ok)throw std::runtime_error(s);}
int main(int argc,char** argv){
 if(argc!=3)return 15;
 try{
  fs::path out=argv[2];fs::create_directories(out);std::string error;
  HdRTVkFurnace gpu;HdRTVkOptions options;options.spvPath=argv[1];options.validation=true;
  check(gpu.Init(options,&error),error);check(gpu.ValidationActive(),"validation inactive");
  auto image=std::make_shared<HdRTVkTexture>();image->id="independent-white-furnace";image->width=image->height=1;image->rgba={1,1,1,1};
  HdRTVkEnvironment environment;environment.texture=0;
  check(gpu.SetTextures({image},{},{environment},nullptr,&error),error);check(gpu.SetLights({},&error),error);
  float positions[]={-100,-100,0,100,-100,0,100,100,0,-100,100,0};
  float normals[]={0,0,1,0,0,1,0,0,1,0,0,1};uint32_t indices[]={0,1,2,0,2,3};
  HdRTVkMesh mesh;mesh.id="/white-furnace";mesh.positions=positions;mesh.normals=normals;mesh.indices=indices;
  mesh.vertexCount=mesh.normalCount=4;mesh.triangleCount=2;mesh.geometryVersion=mesh.topologyVersion=mesh.normalVersion=1;mesh.useOpenPbr=true;
  std::fill_n(mesh.material.baseColorWeight,4,1.f);std::fill_n(mesh.material.specularColorWeight,4,1.f);
  FILE* csv=nullptr;fopen_s(&csv,(out/"stats.csv").string().c_str(),"w");check(csv,"cannot open stats");
  std::fprintf(csv,"case,metal,roughness,view_cos,seed,samples,blas,tlas,geometry_bytes,errors,warnings\n");
  int caseIndex=0;
  for(float metal:{0.f,1.f})for(float rough:{0.f,.3f,1.f})for(float cosine:{1.f,.1f}){
   mesh.material.properties[0]=metal;mesh.material.properties[1]=rough;HdRTVkEditStats edits;
   check(gpu.SetMeshes({mesh},&edits,&error),error);
   const float sine=std::sqrt(1-cosine*cosine);
   for(uint32_t seed:{101u,107u,113u}){
    HdRTVkFrame frame;frame.width=frame.height=32;frame.orthographic=true;frame.bounces=0;frame.seed=seed;
    frame.invView[0]=cosine;frame.invView[2]=-sine;frame.invView[5]=1;frame.invView[8]=sine;frame.invView[10]=cosine;
    frame.invView[12]=3*sine;frame.invView[14]=3*cosine;frame.invView[15]=1;
    frame.invProj[0]=frame.invProj[5]=.1f;frame.invProj[10]=-4.95f;frame.invProj[14]=-5.05f;frame.invProj[15]=1;
    check(gpu.BeginFrame(frame,&error),error);
    for(uint32_t first=0;first<4096;first+=32)check(gpu.RenderBatch(first,32,&error),error);
    check(gpu.Resolve(4096,&error),error);check(gpu.ResolvedPixels(),"missing resolved pixels");
    const auto file=out/(std::to_string(caseIndex)+"_"+std::to_string(seed)+".rgba32f");FILE* imageFile=nullptr;fopen_s(&imageFile,file.string().c_str(),"wb");check(imageFile,"image open");
    check(std::fwrite(gpu.ResolvedPixels(),16,1024,imageFile)==1024,"image short write");std::fclose(imageFile);
    std::fprintf(csv,"%d,%.9g,%.9g,%.9g,%u,%llu,%llu,%llu,%llu,%u,%u\n",caseIndex,metal,rough,cosine,seed,gpu.FrameStats().tracedSamples,edits.blasBuilds,edits.tlasBuilds,edits.geometryUploadBytes,gpu.ValidationErrors(),gpu.ValidationWarnings());
   }
   std::printf("case %d complete\n",caseIndex++);std::fflush(stdout);
  }
  gpu.Destroy();std::fprintf(csv,"destroy,0,0,0,0,0,0,0,0,%u,%u\n",gpu.ValidationErrors(),gpu.ValidationWarnings());std::fclose(csv);return 0;
 }catch(const std::exception& e){std::fprintf(stderr,"probe: %s\n",e.what());return 15;}
}
