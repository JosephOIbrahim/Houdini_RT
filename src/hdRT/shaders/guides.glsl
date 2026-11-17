struct GuideGpu {
    vec4 normalDepth;
    vec4 eyeNormalDistance;
    ivec4 ids;
    vec4 albedo;
};
layout(std430,set=0,binding=5) writeonly buffer Guides { GuideGpu rows[]; } guides;

void writeGuide(uvec2 pixel, uint index) {
    GuideGpu value;
    value.normalDepth=vec4(-1,-1,-1,1);
    value.eyeNormalDistance=vec4(-1,-1,-1,0);
    value.ids=ivec4(-1,-1,-1,0);
    value.albedo=vec4(0);
    vec2 ndc=2.0*((vec2(pixel)+0.5)/vec2(frame.size.xy))-1.0;
    vec4 nearH=frame.invProj*vec4(ndc,-1,1);
    vec3 nearP=nearH.xyz/nearH.w;
    vec3 origin=(frame.invView*vec4(frame.pass.y != 0u ? nearP : vec3(0),1)).xyz;
    vec3 direction=normalize((frame.invView*vec4(frame.pass.y != 0u ? vec3(0,0,-1) : nearP,0)).xyz);
    uint mesh, prim; vec2 bary; mat4x3 o2w,w2o; float distance;
    bool hit=trace(origin,direction,mesh,prim,bary,o2w,w2o,distance);
    uint lightIndex; float lightDistance; vec3 lightNormal;
    bool emitter=closestLight(origin,direction,distance,lightIndex,lightDistance,lightNormal);
    if (hit || emitter) {
        vec3 p,ng,ns;
        if (emitter) { distance=lightDistance; p=origin+distance*direction; ns=lightNormal; }
        else surface(mesh,prim,bary,direction,o2w,w2o,p,ng,ns);
        mat4 view=inverse(frame.invView);
        vec4 clip=inverse(frame.invProj)*view*vec4(p,1);
        value.normalDepth=vec4(ns,0.5*(clip.z/clip.w)+0.5);
        value.eyeNormalDistance=vec4(normalize(mat3(view)*ns),distance);
        value.ids.w=1;
        if (!emitter) {
            MeshGpu data=meshes.rows[mesh];
            value.ids.x=data.metadata.x;
            value.ids.y=int(IndexArray(data.faceIds).v[prim]);
            value.albedo=vec4(data.metadata.z != 0 ? data.baseColorWeight.rgb*data.baseColorWeight.a : data.albedo.rgb,1);
        }
    }
    guides.rows[index]=value;
}
