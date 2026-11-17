// Analytic UsdLux emitters. Power-weighted one-light NEE and diffuse MIS.
struct LightGpu {
    vec4 positionType;
    vec4 basisUArea;
    vec4 basisVCos;
    vec4 normalRadius;
    vec4 radiance;
    vec4 selection;
};
layout(std430, set=0, binding=4) readonly buffer LightTable { LightGpu rows[]; } lights;

float powerWeight(float a, float b) {
    float m = max(a,b);
    if (m <= 0.0) return 0.0;
    a /= m; b /= m;
    return a*a / (a*a+b*b);
}

float solidAngle(LightGpu light) {
    return light.basisUArea.w > 0.0 ? light.basisUArea.w : 2.0*PI*(1.0-light.basisVCos.w);
}

vec3 around(vec3 axis, float oneMinusCosine, float phi) {
    vec3 helper = abs(axis.x) > 0.5 ? vec3(0,1,0) : vec3(1,0,0);
    vec3 tangent = normalize(cross(helper,axis));
    return (1.0-oneMinusCosine)*axis + sqrt(max(0.0,oneMinusCosine*(2.0-oneMinusCosine))) *
        (cos(phi)*tangent + sin(phi)*cross(axis,tangent));
}

bool intersectLight(LightGpu light, vec3 origin, vec3 direction, out float t, out vec3 normal) {
    int type = int(light.positionType.w);
    if (type == 3) return false;
    vec3 delta = origin - light.positionType.xyz;
    if (type == 2) {
        float b = dot(delta,direction), r = light.normalRadius.w;
        float d = b*b - dot(delta,delta) + r*r;
        if (d < 0.0) return false;
        float root = sqrt(d);
        t = -b-root;
        if (t <= 1e-6) t = -b+root;
        if (t <= 1e-6) return false;
        normal = normalize(delta+t*direction);
        return true;
    }
    normal = light.normalRadius.xyz;
    float denominator = dot(normal,direction);
    if (abs(denominator) < 1e-8) return false;
    t = -dot(normal,delta)/denominator;
    if (t <= 1e-6) return false;
    vec3 point = delta+t*direction;
    vec3 u = light.basisUArea.xyz, v = light.basisVCos.xyz;
    float uu=dot(u,u), vv=dot(v,v), uv=dot(u,v);
    float determinant=uu*vv-uv*uv;
    if (determinant <= 0.0) return false;
    vec2 ab = vec2(dot(point,u)*vv-dot(point,v)*uv,
                   dot(point,v)*uu-dot(point,u)*uv) / determinant;
    return type == 0 ? max(abs(ab.x),abs(ab.y)) <= 1.0 : dot(ab,ab) <= 1.0;
}

bool closestLight(vec3 origin, vec3 direction, float limit,
                  out uint index, out float t, out vec3 normal) {
    bool hit=false; t=limit;
    for (uint i=0u; i<frame.pass.z; ++i) {
        float candidate; vec3 n;
        if (intersectLight(lights.rows[i],origin,direction,candidate,n) && candidate < t) {
            hit=true; index=i; t=candidate; normal=n;
        }
    }
    return hit;
}

bool occluded(vec3 origin, vec3 direction, float limit) {
    rayQueryEXT query;
    rayQueryInitializeEXT(query,tlas,gl_RayFlagsOpaqueEXT | gl_RayFlagsTerminateOnFirstHitEXT,
                         0xFFu,origin,0.0,direction,limit);
    while (rayQueryProceedEXT(query)) {}
    if (rayQueryGetIntersectionTypeEXT(query,true) == gl_RayQueryCommittedIntersectionTriangleEXT)
        return true;
    uint index; float distance; vec3 normal;
    return closestLight(origin,direction,limit,index,distance,normal);
}

vec3 distantEmission(vec3 direction, float bsdfPdf) {
    vec3 value=vec3(0);
    for (uint i=0u; i<frame.pass.z; ++i) {
        LightGpu light=lights.rows[i];
        if (int(light.positionType.w) != 3 || solidAngle(light) <= 0.0) continue;
        vec3 difference=direction+light.normalRadius.xyz;
        if (dot(difference,difference) > solidAngle(light)/PI) continue;
        float pdf=light.radiance.w / solidAngle(light);
        value += light.radiance.rgb * powerWeight(bsdfPdf,pdf);
    }
    return value;
}

vec3 directLight(vec3 origin, vec3 ng, vec3 ns, inout uint rng) {
    if (lights.rows[frame.pass.z-1u].selection.x <= 0.0) return vec3(0);
    float choice=rand01(rng);
    uint index=frame.pass.z-1u;
    for (uint i=0u; i<frame.pass.z; ++i)
        if (choice < lights.rows[i].selection.x) { index=i; break; }
    LightGpu light=lights.rows[index];
    int type=int(light.positionType.w);
    bool delta=type == 3 && solidAngle(light) <= 0.0;
    vec3 direction;
    float pdf=light.radiance.w, distance=T_MAX;
    if (type == 3) {
        direction=-light.normalRadius.xyz;
        if (!delta) {
            float oneMinusCosine=rand01(rng)*solidAngle(light)/(2.0*PI);
            direction=around(direction,oneMinusCosine,2.0*PI*rand01(rng));
            pdf /= solidAngle(light);
        }
    } else {
        vec3 point, normal=light.normalRadius.xyz;
        float a=rand01(rng), b=rand01(rng);
        if (type == 2) {
            float z=1.0-2.0*a, r=sqrt(max(0.0,1.0-z*z));
            normal=vec3(r*cos(2.0*PI*b),r*sin(2.0*PI*b),z);
            point=light.positionType.xyz + light.normalRadius.w*normal;
        } else {
            vec2 uv = type == 0 ? 2.0*vec2(a,b)-1.0 : sqrt(a)*vec2(cos(2.0*PI*b),sin(2.0*PI*b));
            point=light.positionType.xyz + uv.x*light.basisUArea.xyz + uv.y*light.basisVCos.xyz;
        }
        vec3 offset=point-origin;
        distance=length(offset);
        if (distance <= 1e-6) return vec3(0);
        direction=offset/distance;
        float cosine=dot(normal,-direction);
        if (cosine <= 0.0) return vec3(0);
        pdf *= distance*distance / (light.basisUArea.w*cosine);
    }
    float cosine=dot(ns,direction);
    if (cosine <= 0.0 || dot(ng,direction) <= 0.0 || pdf <= 0.0) return vec3(0);
    // Stop before the sampled emitter; a blocker beyond it cannot shadow it.
    float limit=type == 3 ? T_MAX : max(0.0,distance - max(1e-5, distance*2e-5));
    if (occluded(origin,direction,limit)) return vec3(0);
    float bsdfPdf=cosine/PI;
    float weight=delta ? 1.0 : powerWeight(pdf,bsdfPdf);
    return light.radiance.rgb * (cosine*weight/(PI*pdf));
}
