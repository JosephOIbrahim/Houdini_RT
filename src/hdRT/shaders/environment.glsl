// OpenEXR/UsdLux latlong: +Y pole, +Z center, longitude grows toward image left.
struct EnvironmentGpu { mat4 worldToDome; vec4 radiance; vec4 info; };
layout(std430,set=0,binding=10) readonly buffer EnvironmentTable { EnvironmentGpu rows[]; } environments;
float luminance(vec3 c) { return dot(c,vec3(.2126,.7152,.0722)); }
vec2 environmentUV(vec3 d) {
    return vec2(fract(.5-atan(d.x,d.z)/(2.0*PI)),acos(clamp(d.y,-1.0,1.0))/PI);
}
vec3 environmentDirection(vec2 uv) {
    float phi=(.5-uv.x)*2.0*PI, theta=uv.y*PI;
    return vec3(sin(phi)*sin(theta),cos(theta),cos(phi)*sin(theta));
}
float environmentTotal() {
    float total=max(0.0,luminance(frame.dome.rgb))*4.0*PI;
    for (int i=0;i<int(frame.dome.a);++i) total+=environments.rows[i].info.y;
    return total;
}
vec3 environmentValue(vec3 direction) {
    vec3 value=frame.dome.rgb;
    for (int i=0;i<int(frame.dome.a);++i) {
        EnvironmentGpu e=environments.rows[i];
        vec2 uv=environmentUV(normalize(mat3(e.worldToDome)*direction));
        value+=e.radiance.rgb*imageSample(int(e.info.x),uv,ivec2(2,1),1,vec4(0)).rgb;
    }
    return value;
}
float imageDirectionPdf(int imageIndex, vec3 local) {
    TextureGpu image=textures.rows[imageIndex];
    vec2 uv=environmentUV(local);
    uvec2 xy=min(uvec2(uv*vec2(image.size.xy)),image.size.xy-1u);
    uint index=xy.y*image.size.x+xy.x;
    FloatArray cdf=FloatArray(image.cdf);
    float p=cdf.v[index]-(index>0u ? cdf.v[index-1u] : 0.0);
    // Stable solid angle at the poles: cos(a)-cos(b)=2sin((a+b)/2)sin((b-a)/2).
    float omega=4.0*PI/float(image.size.x)*sin(PI*(float(xy.y)+.5)/float(image.size.y))*sin(.5*PI/float(image.size.y));
    return max(0.0,p)/omega;
}
float environmentPdf(vec3 direction) {
    float total=environmentTotal();
    if (total<=0.0) return 1.0/(4.0*PI);
    float weighted=max(0.0,luminance(frame.dome.rgb));
    for (int i=0;i<int(frame.dome.a);++i) {
        EnvironmentGpu e=environments.rows[i];
        weighted+=e.info.y*imageDirectionPdf(int(e.info.x),normalize(mat3(e.worldToDome)*direction));
    }
    return .05/(4.0*PI)+.95*weighted/total;
}
vec3 uniformDirection(inout uint rng) {
    float y=1.0-2.0*randOpen01(rng), phi=2.0*PI*randOpen01(rng);
    float r=sqrt(max(0.0,1.0-y*y));
    return vec3(r*sin(phi),y,r*cos(phi));
}
vec3 sampleEnvironment(inout uint rng) {
    float total=environmentTotal();
    if (randOpen01(rng)<.05 || total<=0.0) return uniformDirection(rng);
    float choice=randOpen01(rng)*total;
    float prefix=max(0.0,luminance(frame.dome.rgb))*4.0*PI;
    if (choice<prefix) return uniformDirection(rng);
    int selected=int(frame.dome.a)-1;
    for (int i=0;i<int(frame.dome.a);++i) {
        prefix+=environments.rows[i].info.y;
        if (choice<prefix) { selected=i; break; }
    }
    EnvironmentGpu e=environments.rows[selected];
    TextureGpu image=textures.rows[int(e.info.x)];
    FloatArray cdf=FloatArray(image.cdf);
    float xi=randOpen01(rng); uint lo=0u,hi=image.size.x*image.size.y-1u;
    while (lo<hi) { uint mid=lo+(hi-lo)/2u; if (xi<cdf.v[mid]) hi=mid; else lo=mid+1u; }
    uint x=lo%image.size.x,y=lo/image.size.x;
    float u=(float(x)+randOpen01(rng))/float(image.size.x);
    float cosTheta=mix(cos(PI*float(y)/float(image.size.y)),cos(PI*float(y+1u)/float(image.size.y)),randOpen01(rng));
    float phi=(.5-u)*2.0*PI, r=sqrt(max(0.0,1.0-cosTheta*cosTheta));
    vec3 local=vec3(r*sin(phi),cosTheta,r*cos(phi));
    return normalize(transpose(mat3(e.worldToDome))*local);
}
vec3 directEnvironment(vec3 origin,vec3 ng,vec3 ns,inout uint rng) {
    vec3 direction=sampleEnvironment(rng);
    float cosine=dot(ns,direction),pdf=environmentPdf(direction);
    if (cosine<=0.0 || dot(ng,direction)<=0.0 || occluded(origin,direction,T_MAX)) return vec3(0);
    return environmentValue(direction)*(cosine/PI)*powerWeight(pdf,cosine/PI)/pdf;
}
vec3 directMaterialEnvironment(vec3 origin,vec3 ng,vec3 ns,OpenPBR_PreparedBsdf bsdf,inout uint rng) {
    vec3 direction=sampleEnvironment(rng);
    float pdf=environmentPdf(direction);
    if (dot(ns,direction)<=0.0 || dot(ng,direction)<=0.0 || occluded(origin,direction,T_MAX)) return vec3(0);
    return environmentValue(direction)*materialEval(bsdf,direction)*powerWeight(pdf,openpbr_pdf(bsdf,direction))/pdf;
}
