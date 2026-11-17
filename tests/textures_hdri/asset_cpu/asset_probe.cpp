#include "imageAssets.h"
#include <OpenColorIO/OpenColorIO.h>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <stdexcept>
namespace OCIO=OCIO_NAMESPACE;
namespace fs=std::filesystem;
#define CHECK(x) do { if (!(x)) throw std::runtime_error("FAILED: " #x); } while(false)
std::vector<std::string> Fields(const std::string& line)
{
    std::stringstream in(line); std::vector<std::string> fields; std::string value;
    while (std::getline(in,value,'\t')) fields.push_back(value);
    return fields;
}
void Pixels(const fs::path& file,const HdRTImageAssets::Image& image)
{
    std::ofstream output(file,std::ios::binary);
    output.write(reinterpret_cast<const char*>(image->rgba.data()),image->rgba.size()*sizeof(float));
    CHECK(output.good());
}
void Cases(const fs::path& directory)
{
    HdRTImageAssets assets;
    std::ifstream input(directory/"requests.tsv"); CHECK(input.good());
    std::ofstream result(directory/"actual.tsv"); CHECK(result.good());
    result << "case\tsuccess\twidth\theight\tdecodes\thits\terror\n";
    std::vector<HdRTImageAssets::Image> retained;
    std::string line;
    while (std::getline(input,line)) {
        const auto fields=Fields(line); CHECK(fields.size()==7);
        HdRTImageRequest request{fields[1],fields[2]=="-"?"":fields[2],std::stoi(fields[3]),
            std::stof(fields[4]),std::stof(fields[5]),fields[6]=="1"};
        std::string error; const auto image=assets.Load(request,[]{return true;},&error);
        if (image) {
            Pixels(directory/(fields[0]+".rgba"),image);
            const auto decodes=assets.Decodes(),hits=assets.CacheHits();
            const auto repeated=assets.Load(request,[]{return true;},&error);
            CHECK(repeated==image && assets.Decodes()==decodes && assets.CacheHits()==hits+1);
            retained.push_back(image);
        }
        for (char& c:error) if (c=='\t' || c=='\r' || c=='\n') c=' ';
        result << fields[0] << '\t' << bool(image) << '\t' << (image?image->width:0) << '\t'
            << (image?image->height:0) << '\t' << assets.Decodes() << '\t' << assets.CacheHits() << '\t' << error << '\n';
    }
}
void Lifecycle(const fs::path& directory)
{
    HdRTImageAssets assets; std::string error;
    const auto path=directory/"changing.png";
    fs::copy_file(directory/"first.png",path,fs::copy_options::overwrite_existing);
    HdRTImageRequest request{path.string(),"Raw",3,0,1,false};
    const auto first=assets.Load(request,[]{return true;},&error); CHECK(first);
    fs::copy_file(directory/"second.png",path,fs::copy_options::overwrite_existing);
    fs::last_write_time(path,fs::last_write_time(path)+std::chrono::seconds(2));
    const auto second=assets.Load(request,[]{return true;},&error);
    CHECK(second && first!=second && first->id!=second->id && first->rgba!=second->rgba);
    auto domain=request; domain.maximum=2;
    const auto other=assets.Load(domain,[]{return true;},&error); CHECK(other && other!=second && other->id!=second->id);
    fs::remove(path);
    CHECK(!assets.Load(request,[]{return true;},&error) && !error.empty());
    fs::copy_file(directory/"first.png",path);
    CHECK(assets.Load(request,[]{return true;},&error));
    HdRTImageRequest large{(directory/"cancel.png").string(),"Raw",3,0,1,false};
    size_t callbacks=0; const auto start=std::chrono::steady_clock::now();
    const auto cancelled=assets.Load(large,[&]{++callbacks;return false;},&error);
    const double seconds=std::chrono::duration<double>(std::chrono::steady_clock::now()-start).count();
    CHECK(!cancelled && callbacks>0 && seconds<.25);
    const auto resumed=assets.Load(large,[]{return true;},&error); CHECK(resumed && resumed->width==2048);
    const auto decodes=assets.Decodes();
    CHECK(assets.Load(large,[]{return true;},&error)==resumed && assets.Decodes()==decodes);
    const auto originalConfig=OCIO::GetCurrentConfig();
    OCIO::SetCurrentConfig(OCIO::Config::CreateRaw());
    const auto otherConfig=assets.Load(large,[]{return true;},&error);
    CHECK(otherConfig && otherConfig!=resumed && otherConfig->id!=resumed->id);
    OCIO::SetCurrentConfig(originalConfig);
    auto converted=large; converted.colorSpace="srgb_texture";
    size_t configCallbacks=0;
    const auto capturedConfig=assets.Load(converted,[&]{
        if (!configCallbacks++) OCIO::SetCurrentConfig(OCIO::Config::CreateRaw());
        return true;
    },&error);
    OCIO::SetCurrentConfig(originalConfig);
    CHECK(configCallbacks>0 && capturedConfig && capturedConfig->rgba[0]==1.f);
    std::ofstream receipt(directory/"lifecycle.tsv");
    receipt << "file_change\tdomain_separation\tmissing_recovery\tcancel_callbacks\tcancel_seconds\tresume\tconfig_domain\tconfig_snapshot\n"
        << "1\t1\t1\t" << callbacks << '\t' << std::setprecision(9) << seconds << "\t1\t1\t1\n";
}
int main(int argc,char** argv)
{
    try { CHECK(argc==2); const fs::path directory=fs::absolute(argv[1]); Cases(directory); Lifecycle(directory); }
    catch(const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
    std::cout << "Asset decode outputs, shared cache, changed assets, recovery and cancellation captured\n";
}
