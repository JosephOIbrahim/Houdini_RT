// Included in vkFurnace.cpp: immutable decoded assets, resident on the render thread.
bool HdRTVkFurnace::SetTextures(const std::vector<std::shared_ptr<const HdRTVkTexture>>& input,
    const std::vector<HdRTVkTextureBinding>& bindings, const std::vector<HdRTVkEnvironment>& envs,
    HdRTVkEditStats* measured, std::string* error)
{
    Impl& d = *_impl;
    if (!d.ready) return _Fail(error, "SetTextures called before Init");
    if (input.size()>128 || bindings.size()>65536 || envs.size()>8)
        return _Fail(error, "texture scene exceeds 128 assets, 65536 bindings or 8 HDRI domes");
    HdRTVkEditStats stats;
    const double pi=3.14159265358979323846;
    const VkBufferUsageFlags usage=VK_BUFFER_USAGE_STORAGE_BUFFER_BIT | VK_BUFFER_USAGE_TRANSFER_DST_BIT
        | VK_BUFFER_USAGE_SHADER_DEVICE_ADDRESS_BIT;
    std::vector<TextureRow> rows;
    std::set<std::string> retained;
    uint64_t totalTexels=0;
    for (const auto& source : input) {
        if (!source || source->id.empty() || !source->width || !source->height)
            return _Fail(error, "invalid texture identity or dimensions");
        const uint64_t count=uint64_t(source->width)*source->height;
        totalTexels+=count;
        if (count>16777216 || totalTexels>33554432 || source->rgba.size()!=count*4)
            return _Fail(error, "texture exceeds 16M texels per image or 32M scene texels");
        retained.insert(source->id);
        TextureGpu& tex=d.textureCache[source->id];
        if (!tex.source) {
            std::fill_n(tex.integral,3,0.0);
            std::vector<float> cdf(size_t(count),0);
            double sum=0;
            for (uint32_t y=0; y<source->height; ++y) {
                double omega=2*pi/source->width*(std::cos(pi*y/source->height)-std::cos(pi*(y+1)/source->height));
                for (uint32_t x=0; x<source->width; ++x) {
                    size_t i=size_t(y)*source->width+x;
                    const float* p=&source->rgba[i*4];
                    for (int c=0;c<4;++c) if (!std::isfinite(p[c])) return _Fail(error,"nonfinite texture pixel");
                    double lum=std::max(0.0,.2126*p[0]+.7152*p[1]+.0722*p[2]);
                    sum+=lum*omega;
                    cdf[i]=float(sum);
                    for (int c=0;c<3;++c) tex.integral[c]+=p[c]*omega;
                }
            }
            if (!std::isfinite(sum) || sum>1e35) return _Fail(error,"texture importance integral exceeds finite range");
            if (sum>0) for (float& v:cdf) v=std::clamp(float(double(v)/sum),0.f,1.f);
            else for (size_t i=0;i<count;++i) cdf[i]=float(double(i+1)/count);
            cdf.back()=1;
            const VkDeviceSize pixelBytes=count*4*sizeof(float), cdfBytes=count*sizeof(float);
            if (!d.EnsureBuffer(pixelBytes,usage,false,&tex.pixels,error)
                || !d.Upload(tex.pixels,source->rgba.data(),pixelBytes,0,error)
                || !d.EnsureBuffer(cdfBytes,usage,false,&tex.cdf,error)
                || !d.Upload(tex.cdf,cdf.data(),cdfBytes,0,error)) return false;
            stats.textureUploadBytes+=pixelBytes+cdfBytes;
            tex.source=source;
        } else ++stats.textureCacheHits;
        TextureRow row;
        row.pixels=d.BufferAddress(tex.pixels.buffer); row.cdf=d.BufferAddress(tex.cdf.buffer);
        row.size[0]=source->width; row.size[1]=source->height;
        rows.push_back(row);
    }
    for (const auto& b:bindings) {
        if (b.config[0]<0 || size_t(b.config[0])>=input.size() || b.config[1]<0 || b.config[1]>3
            || b.config[2]<0 || b.config[2]>3 || b.config[3]<0 || b.config[3]>1)
            return _Fail(error,"invalid texture binding");
        for (float f:b.uvU) if (!std::isfinite(f)) return _Fail(error,"nonfinite UV transform");
        for (float f:b.uvV) if (!std::isfinite(f)) return _Fail(error,"nonfinite UV transform");
        for (float f:b.fallback) if (!std::isfinite(f)) return _Fail(error,"nonfinite image border");
    }
    std::vector<EnvironmentRow> environments;
    for (const auto& e:envs) {
        if (e.texture<0 || size_t(e.texture)>=input.size()) return _Fail(error,"invalid HDRI texture");
        EnvironmentRow row={};
        std::memcpy(row.transform,e.worldToDome,sizeof(row.transform));
        for (float f:row.transform) if (!std::isfinite(f)) return _Fail(error,"nonfinite HDRI transform");
        for (int a=0;a<3;++a) for (int b=a;b<3;++b) {
            double dot=0; for (int c=0;c<3;++c) dot+=double(row.transform[4*a+c])*row.transform[4*b+c];
            if (std::abs(dot-(a==b?1.0:0.0))>1e-4) return _Fail(error,"HDRI direction matrix must be orthonormal");
        }
        const auto& tex=d.textureCache[input[e.texture]->id];
        double weight=0;
        const double luminance[3]={.2126,.7152,.0722};
        for (int c=0;c<3;++c) {
            if (!std::isfinite(e.radiance[c]) || e.radiance[c]<0) return _Fail(error,"invalid HDRI radiance");
            row.radiance[c]=e.radiance[c]; weight+=tex.integral[c]*e.radiance[c]*luminance[c];
        }
        if (!std::isfinite(weight) || weight>1e35) return _Fail(error,"HDRI power exceeds supported finite range");
        row.info[0]=float(e.texture); row.info[1]=float(std::max(0.0,weight));
        environments.push_back(row);
    }
    auto upload=[&](Buffer& buffer,std::vector<unsigned char>& cached,const void* data,size_t bytes,uint32_t binding)->bool {
        if (buffer.buffer && cached.size()==bytes && !std::memcmp(cached.data(),data,bytes)) return true;
        if (!d.EnsureBuffer(bytes,usage,false,&buffer,error) || !d.Upload(buffer,data,bytes,0,error)) return false;
        VkDescriptorBufferInfo info={buffer.buffer,0,bytes};
        VkWriteDescriptorSet write={VK_STRUCTURE_TYPE_WRITE_DESCRIPTOR_SET};
        write.dstSet=d.descriptorSet; write.dstBinding=binding; write.descriptorCount=1;
        write.descriptorType=VK_DESCRIPTOR_TYPE_STORAGE_BUFFER; write.pBufferInfo=&info;
        vkUpdateDescriptorSets(d.device,1,&write,0,nullptr); stats.tableUploadBytes+=bytes;
        const auto* first=static_cast<const unsigned char*>(data); cached.assign(first,first+bytes); return true;
    };
    const TextureRow emptyTexture={}; const HdRTVkTextureBinding emptyBinding;
    const EnvironmentRow emptyEnvironment={};
    if (!upload(d.textureTable,d.textureRows,rows.empty()?&emptyTexture:rows.data(),std::max(size_t(1),rows.size())*sizeof(TextureRow),8)
        || !upload(d.bindingTable,d.bindingRows,bindings.empty()?&emptyBinding:bindings.data(),std::max(size_t(1),bindings.size())*sizeof(HdRTVkTextureBinding),9)
        || !upload(d.environmentTable,d.environmentRows,environments.empty()?&emptyEnvironment:environments.data(),std::max(size_t(1),environments.size())*sizeof(EnvironmentRow),10)) return false;
    d.environmentCount=uint32_t(envs.size());
    d.textureBindingCount=uint32_t(bindings.size());
    for (auto it=d.textureCache.begin();it!=d.textureCache.end();) {
        if (retained.count(it->first)) { ++it; continue; }
        d.DestroyBuffer(it->second.pixels); d.DestroyBuffer(it->second.cdf); it=d.textureCache.erase(it);
    }
    if (measured) *measured=stats;
    return true;
}
