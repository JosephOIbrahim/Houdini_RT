"""Independent CPU equations; imports no renderer, shader, or production helper."""
import math

def srgb_to_linear(value):
    return value/12.92 if value<=.04045 else ((value+.055)/1.055)**2.4

def address(index,size,mode):
    if mode=='periodic':return index%size
    if mode=='mirror':
        p=index%(2*size);return p if p<size else 2*size-1-p
    if mode=='clamp':return max(0,min(size-1,index))
    if mode=='constant':return index if 0<=index<size else None
    raise ValueError(mode)

def sample_texture(rows,u,v,filtertype='linear',wrap_u='periodic',wrap_v='periodic',default=(0.,0.,0.)):
    """Rows are top-to-bottom; material UV (0,0) is lower-left.

    Linear filtering samples texel centers and applies addressing to each tap.
    This explicitly declares the numerical oracle rather than calling a GPU API.
    """
    h=len(rows);w=len(rows[0]);assert h and w and all(len(r)==w for r in rows)
    def texel(x,y):
        x=address(x,w,wrap_u);y=address(y,h,wrap_v)
        return default if x is None or y is None else rows[y][x]
    if filtertype=='closest':return tuple(texel(math.floor(u*w),math.floor((1-v)*h)))
    if filtertype!='linear':raise ValueError(filtertype)
    x=u*w-.5;y=(1-v)*h-.5;ix=math.floor(x);iy=math.floor(y);tx=x-ix;ty=y-iy
    taps=[(texel(ix,iy),(1-tx)*(1-ty)),(texel(ix+1,iy),tx*(1-ty)),
          (texel(ix,iy+1),(1-tx)*ty),(texel(ix+1,iy+1),tx*ty)]
    return tuple(sum(pixel[c]*weight for pixel,weight in taps) for c in range(len(default)))

def interpolate(values,weights):
    assert len(values)==len(weights)
    return tuple(sum(v[c]*w for v,w in zip(values,weights)) for c in range(len(values[0])))

def latlong_direction(u,v):
    """OpenEXR/USD: image UV with top-left origin; north+Y, center+Z, +longitude+X."""
    longitude=math.pi*(1-2*u);theta=math.pi*v;s=math.sin(theta)
    return (s*math.sin(longitude),math.cos(theta),s*math.cos(longitude))

def latlong_uv(direction):
    x,y,z=direction;length=math.sqrt(x*x+y*y+z*z);assert length>0
    return ((.5-math.atan2(x,z)/(2*math.pi))%1,math.acos(max(-1,min(1,y/length)))/math.pi)

def pole_axis(direction,axis):
    x,y,z=direction
    if axis=='Y':return (x,y,z)
    if axis=='Z':return (x,-z,y)
    raise ValueError(axis)

def cell_solid_angle(width,height,row):
    return 2*math.pi/width*(math.cos(math.pi*row/height)-math.cos(math.pi*(row+1)/height))

def gauss_legendre(count):
    """Generate quadrature by Legendre recurrence/Newton roots, independent of native tables."""
    result=[]
    for index in range(1,count+1):
        x=math.cos(math.pi*(index-.25)/(count+.5))
        for _ in range(30):
            a,b=1.,x
            for k in range(2,count+1):a,b=b,((2*k-1)*x*b-(k-1)*a)/k
            derivative=count*(x*b-a)/(x*x-1);step=b/derivative;x-=step
            if abs(step)<2e-15:break
        weight=2/((1-x*x)*derivative*derivative);result.append((x,weight))
    return result

def positive_cosine_z_integral(a,b,z0,z1):
    """Integral max(0,a*sqrt(1-z²)+b*z) dz; exact horizon splitting."""
    def primitive(z):
        return .5*(a*(z*math.sqrt(max(0,1-z*z))+math.asin(max(-1,min(1,z))))+b*z*z)
    cuts=[z0,z1]
    if b:
        root=-math.copysign(1,b)*a/math.sqrt(a*a+b*b)
        if z0<root<z1:cuts.insert(1,root)
    total=0.
    for lo,hi in zip(cuts,cuts[1:]):
        mid=(lo+hi)/2
        if a*math.sqrt(max(0,1-mid*mid))+b*mid>0:total+=primitive(hi)-primitive(lo)
    return total

def lambert_piecewise_latlong(rows,normal=(0.,1.,0.),order=64):
    """Exact z integration plus independent phi quadrature for nearest-texel maps.

    Returns integral L(w)*max(0,n.w) dw/pi (unit Lambert reflectance).
    No candidate distribution, generated CDF, or renderer RNG is consulted.
    """
    h=len(rows);w=len(rows[0]);nodes=gauss_legendre(order);answer=[0.]*len(rows[0][0])
    nx,ny,nz=normal;assert abs(nx*nx+ny*ny+nz*nz-1)<1e-10
    for y,row in enumerate(rows):
        z0=math.cos(math.pi*(y+1)/h);z1=math.cos(math.pi*y/h)
        for x,pixel in enumerate(row):
            p0=math.pi*(1-2*(x+1)/w);p1=math.pi*(1-2*x/w);integral=0.
            for t,weight in nodes:
                phi=(p0+p1+(p1-p0)*t)/2
                integral+=weight*positive_cosine_z_integral(nx*math.sin(phi)+nz*math.cos(phi),ny,z0,z1)
            integral*=(p1-p0)/(2*math.pi)
            for c,value in enumerate(pixel):answer[c]+=value*integral
    return answer

def lambert_bilinear_latlong(rows,normal=(0.,1.,0.),order=24):
    """Bilinear radiance integral in (longitude, theta); horizon split.

    Domain partitions include every interpolation-cell boundary. Independent
    order doubling records the remaining quadrature error before rendering.
    """
    h=len(rows);w=len(rows[0]);nodes=gauss_legendre(order);answer=[0.]*len(rows[0][0])
    us=[0.]+[(i+.5)/w for i in range(w)]+[1.]
    zs=sorted([-1.,1.]+[math.cos(math.pi*(i+.5)/h) for i in range(h)])
    nx,ny,nz=normal;assert abs(nx*nx+ny*ny+nz*nz-1)<1e-10
    if nx or nz:
        horizon=math.atan2(-nz,nx)
        us=sorted(set(us+[(.5-phi/(2*math.pi))%1 for phi in (horizon,horizon+math.pi)]))
    for u0,u1 in zip(us,us[1:]):
        for tu,wu in nodes:
            u=(u0+u1+(u1-u0)*tu)/2;phi=math.pi*(1-2*u)
            a=nx*math.sin(phi)+nz*math.cos(phi);root=None
            if ny:root=-math.copysign(1,ny)*a/math.sqrt(a*a+ny*ny)
            for z0,z1 in zip(zs,zs[1:]):
                cuts=[z0,z1]
                if root is not None and z0<root<z1:cuts.insert(1,root)
                for lo,hi in zip(cuts,cuts[1:]):
                    mid=(lo+hi)/2
                    if a*math.sqrt(max(0,1-mid*mid))+ny*mid<=0:continue
                    theta0=math.acos(max(-1,min(1,hi)));theta1=math.acos(max(-1,min(1,lo)))
                    for tz,wz in nodes:
                        theta=(theta0+theta1+(theta1-theta0)*tz)/2;z=math.cos(theta);cosine=max(0,a*math.sin(theta)+ny*z)
                        color=sample_texture(rows,u,1-math.acos(max(-1,min(1,z)))/math.pi,'linear','periodic','clamp')
                        weight=wu*wz*(u1-u0)*(theta1-theta0)*.5*cosine*math.sin(theta)
                        for c,value in enumerate(color):answer[c]+=value*weight
    return answer

def importance_distribution(rows,luma=(.2126,.7152,.0722),uniform_fraction=.05):
    """Exact per-cell probability; any black map falls back to uniform solid angle."""
    h=len(rows);w=len(rows[0]);areas=[cell_solid_angle(w,h,y) for y in range(h)]
    power=[[max(0,sum(c*l for c,l in zip(p,luma)))*areas[y] for p in row] for y,row in enumerate(rows)]
    total=sum(sum(row) for row in power)
    return [[((1-uniform_fraction)*power[y][x]/total+uniform_fraction*areas[y]/(4*math.pi))
             if total>0 else areas[y]/(4*math.pi) for x in range(w)] for y in range(h)]

def environment_pdf(rows,direction,luma=(.2126,.7152,.0722),uniform_fraction=.05):
    u,v=latlong_uv(direction);h=len(rows);w=len(rows[0]);x=min(w-1,int(u*w));y=min(h-1,int(v*h))
    return importance_distribution(rows,luma,uniform_fraction)[y][x]/cell_solid_angle(w,h,y)
