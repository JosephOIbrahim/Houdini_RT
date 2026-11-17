// Level-zero image sampling; no implicit derivatives or undocumented mip policy.
struct TextureGpu { uint64_t pixels; uint64_t cdf; uvec4 size; };
struct TextureBinding { ivec4 config; vec4 uvU; vec4 uvV; vec4 fallback; };
layout(std430,set=0,binding=8) readonly buffer TextureTable { TextureGpu rows[]; } textures;
layout(std430,set=0,binding=9) readonly buffer BindingTable { TextureBinding rows[]; } textureBindings;
layout(buffer_reference,std430,buffer_reference_align=16) readonly buffer PixelArray { vec4 v[]; };

int addressTexel(int i, int n, int mode, inout bool valid) {
    if (mode==0) { valid=valid && i>=0 && i<n; return clamp(i,0,n-1); }
    if (mode==1) return clamp(i,0,n-1);
    int period=mode==3 ? 2*n : n;
    // GLSL leaves % undefined for negative operands. Keep both operands positive,
    // including the left/top bilinear border of non-power-of-two images.
    uint magnitude=i<0 ? uint(-(i+1)) : uint(i);
    int remainder=int(magnitude%uint(period));
    int p=i<0 ? period-1-remainder : remainder;
    return mode==3 && p>=n ? period-1-p : p;
}
vec4 imageTexel(TextureGpu image, ivec2 xy, ivec2 wrap, vec4 fallback) {
    bool valid=true;
    int x=addressTexel(xy.x,int(image.size.x),wrap.x,valid);
    int y=addressTexel(xy.y,int(image.size.y),wrap.y,valid);
    return valid ? PixelArray(image.pixels).v[uint(y)*image.size.x+uint(x)] : fallback;
}
vec4 imageSample(int index, vec2 topUV, ivec2 wrap, int filtering, vec4 fallback) {
    TextureGpu image=textures.rows[index];
    // Reduce periodic/mirror coordinates before float-to-int conversion, including large finite UVs.
    for (int c=0;c<2;++c) {
        if (wrap[c]>=2) topUV[c]=mod(topUV[c],wrap[c]==3 ? 2.0 : 1.0);
        else if (wrap[c]==1) topUV[c]=clamp(topUV[c],0.0,1.0);
        else if (topUV[c]<-1.0 || topUV[c]>2.0) return fallback;
    }
    vec2 p=topUV*vec2(image.size.xy);
    if (filtering==0) return imageTexel(image,ivec2(floor(p)),wrap,fallback);
    p-=0.5;
    ivec2 lo=ivec2(floor(p)); vec2 a=fract(p);
    return mix(mix(imageTexel(image,lo,wrap,fallback),imageTexel(image,lo+ivec2(1,0),wrap,fallback),a.x),
               mix(imageTexel(image,lo+ivec2(0,1),wrap,fallback),imageTexel(image,lo+ivec2(1,1),wrap,fallback),a.x),a.y);
}
vec2 meshUV(MeshGpu mesh, uint prim, vec2 bary) {
    if (mesh.uvs==uint64_t(0)) return vec2(0);
    FloatArray data=FloatArray(mesh.uvs); uint b=prim*6u;
    return (1.0-bary.x-bary.y)*vec2(data.v[b],data.v[b+1u]) +
        bary.x*vec2(data.v[b+2u],data.v[b+3u]) + bary.y*vec2(data.v[b+4u],data.v[b+5u]);
}
vec4 boundSample(int index, vec2 uv) {
    TextureBinding b=textureBindings.rows[index];
    vec2 st=vec2(dot(b.uvU.xyz,vec3(uv,1)),dot(b.uvV.xyz,vec3(uv,1)));
    if (any(isnan(st)) || any(isinf(st))) return b.fallback;
    return imageSample(b.config.x,vec2(st.x,1.0-st.y),b.config.yz,b.config.w,b.fallback);
}
MeshGpu texturedMaterial(MeshGpu mesh, uint prim, vec2 bary) {
    if (mesh.metadata.z==0) return mesh;
    vec2 uv=meshUV(mesh,prim,bary);
    if (mesh.textures0.x>=0) mesh.baseColorWeight.rgb=boundSample(mesh.textures0.x,uv).rgb;
    if (mesh.textures0.y>=0) mesh.baseColorWeight.a=boundSample(mesh.textures0.y,uv).r;
    if (mesh.textures0.z>=0) mesh.materialProperties.x=boundSample(mesh.textures0.z,uv).r;
    if (mesh.textures0.w>=0) mesh.specularColorWeight.rgb=boundSample(mesh.textures0.w,uv).rgb;
    if (mesh.textures1.x>=0) mesh.specularColorWeight.a=boundSample(mesh.textures1.x,uv).r;
    if (mesh.textures1.y>=0) mesh.materialProperties.y=boundSample(mesh.textures1.y,uv).r;
    if (mesh.textures1.z>=0) mesh.materialProperties.z=boundSample(mesh.textures1.z,uv).r;
    return mesh;
}
