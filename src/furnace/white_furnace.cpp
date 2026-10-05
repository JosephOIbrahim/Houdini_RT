// White furnace + sampling-consistency exam for Adobe's OpenPBR BSDF (C++ backend).
//
// What it proves
//   1. Energy: under a uniform environment of radiance 1, a lossless white material returns ~1.
//      The estimator is the mean importance-sampling weight (BSDF * cos / pdf).
//   2. Lambert reduction: with specular_weight = 0 the BSDF returns exactly base_color.
//   3. MIS safety: for every sample, weight == eval(dir) / pdf(dir), and the pdf returned by
//      openpbr_sample equals openpbr_pdf(dir).
//
// Why it is useful as an oracle
//   The same source compiles as GLSL. Run these configs on the GPU, then diff against the CSV this
//   program writes. That tests your integration, not Adobe's BSDF.
//
// Build (needs https://github.com/adobe/openpbr-bsdf and https://github.com/g-truc/glm):
//   g++ -std=c++17 -O2 -I<openpbr-bsdf> -I<glm> white_furnace.cpp -o white_furnace
//   cl /std:c++17 /O2 /EHsc /I<openpbr-bsdf> /I<glm> white_furnace.cpp          (MSVC)
//
// Run:
//   white_furnace [seed] [csv_path]
//   Exit code 0 = every row inside tolerance, 1 = at least one row failed.
//
// Tolerances (per row): |albedo - expected| <= model_tolerance + 3 * standard_error
//   white rows                : 0.5%
//   coat rows with cosV < 0.2 : 3%   (documented coat reciprocity-vs-energy trade in the BSDF)
//   lambert rows              : 0.1%
//   control rows              : not judged (they exist to show the test can tell grey from white)

#include <glm/glm.hpp>
#include "openpbr.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <random>

struct Result
{
    double albedo;
    double std_error;
    double max_rgb;
    double invalid_frac;
    double weight_vs_eval_mean_rel;
    double pdf_mismatch_mean_rel;
};

static Result run(const OpenPBR_ResolvedInputs& inputs, const float cos_view, const int n, const std::uint32_t seed)
{
    const float sin_view = std::sqrt(std::max(0.0f, 1.0f - cos_view * cos_view));
    const vec3 view_direction = normalize(vec3(sin_view, 0.0f, cos_view));

    const OpenPBR_PreparedBsdf prepared =
        openpbr_prepare(inputs, vec3(1.0f), OpenPBR_BaseRgbWavelengths_nm, OpenPBR_VacuumIor, view_direction);

    std::mt19937 rng(seed);
    std::uniform_real_distribution<float> uni(0.0f, std::nextafter(1.0f, 0.0f));

    double sum = 0.0, sum_sq = 0.0;
    double sum_rgb[3] = { 0.0, 0.0, 0.0 };
    double rel_weight = 0.0, rel_pdf = 0.0;
    long invalid = 0, valid = 0;

    for (int i = 0; i < n; ++i)
    {
        const float xi0 = uni(rng);
        const float xi1 = uni(rng);
        const float xi2 = uni(rng);

        vec3 light_direction;
        OpenPBR_DiffuseSpecular weight;
        float pdf = 0.0f;
        OpenPBR_BsdfLobeType lobe_type;
        openpbr_sample(prepared, vec3(xi0, xi1, xi2), light_direction, weight, pdf, lobe_type);

        double value = 0.0;
        if (pdf > 0.0f)
        {
            const vec3 w = openpbr_get_sum_of_diffuse_specular(weight);
            value = (double(w.x) + double(w.y) + double(w.z)) / 3.0;
            sum_rgb[0] += w.x;
            sum_rgb[1] += w.y;
            sum_rgb[2] += w.z;

            const vec3 dir = normalize(light_direction);
            const vec3 f_cos = openpbr_get_sum_of_diffuse_specular(openpbr_eval(prepared, dir));
            const float pdf_eval = openpbr_pdf(prepared, dir);
            if (pdf_eval > 0.0f)
            {
                const double from_eval = (double(f_cos.x) + double(f_cos.y) + double(f_cos.z)) / 3.0 / double(pdf_eval);
                rel_weight += std::abs(value - from_eval) / std::max(value, 1e-6);
                rel_pdf += std::abs(double(pdf) - double(pdf_eval)) / double(pdf);
                ++valid;
            }
        }
        else
        {
            ++invalid;
        }
        sum += value;
        sum_sq += value * value;
    }

    Result r;
    r.albedo = sum / n;
    const double variance = std::max(0.0, sum_sq / n - r.albedo * r.albedo);
    r.std_error = std::sqrt(variance / n);
    r.max_rgb = std::max(sum_rgb[0], std::max(sum_rgb[1], sum_rgb[2])) / n;
    r.invalid_frac = double(invalid) / n;
    r.weight_vs_eval_mean_rel = valid ? rel_weight / valid : 0.0;
    r.pdf_mismatch_mean_rel = valid ? rel_pdf / valid : 0.0;
    return r;
}

enum Kind { White, Lambert, Control };

struct Config
{
    const char* name;
    Kind kind;
    float metalness;
    float base_gray;
    float specular_weight;
    float coat;
    float fuzz;
};

int main(int argc, char** argv)
{
    const std::uint32_t seed_base = argc > 1 ? std::uint32_t(std::strtoul(argv[1], nullptr, 10)) : 1u;
    const char* csv_path = argc > 2 ? argv[2] : nullptr;

    const int n = 400000;
    const float cos_views[] = { 1.0f, 0.7f, 0.3f, 0.1f };
    const float roughnesses[] = { 0.1f, 0.5f, 1.0f };

    const Config configs[] = {
        { "white_dielectric", White, 0.0f, 1.0f, 1.0f, 0.0f, 0.0f },
        { "white_metal", White, 1.0f, 1.0f, 1.0f, 0.0f, 0.0f },
        { "white_dielectric_coat", White, 0.0f, 1.0f, 1.0f, 1.0f, 0.0f },
        { "white_dielectric_fuzz", White, 0.0f, 1.0f, 1.0f, 0.0f, 1.0f },
        { "lambert_grey_no_specular", Lambert, 0.0f, 0.5f, 0.0f, 0.0f, 0.0f },
        { "grey_dielectric_control", Control, 0.0f, 0.5f, 1.0f, 0.0f, 0.0f },
    };

    std::FILE* csv = csv_path ? std::fopen(csv_path, "w") : nullptr;
    if (csv)
        std::fprintf(csv, "config,kind,metalness,base_gray,specular_weight,coat,fuzz,roughness,cos_view,albedo,std_error,"
                          "max_rgb,expected,tolerance,verdict\n");

    std::printf("%-26s %5s %5s  %8s %8s %8s %8s %9s  %s\n", "config", "rough", "cosV", "albedo", "3*SE", "expect", "tol", "w!=f/pdf",
                "verdict");

    int failures = 0, judged = 0;
    double worst_consistency = 0.0;
    std::uint32_t seed = seed_base;

    for (const Config& c : configs)
    {
        for (const float roughness : roughnesses)
        {
            for (const float cos_view : cos_views)
            {
                OpenPBR_ResolvedInputs inputs = openpbr_make_default_resolved_inputs();
                inputs.base_color = vec3(c.base_gray);
                inputs.base_metalness = c.metalness;
                inputs.specular_weight = c.specular_weight;
                inputs.specular_roughness = roughness;
                inputs.coat_weight = c.coat;
                inputs.coat_color = vec3(1.0f);
                inputs.coat_roughness = roughness;
                inputs.fuzz_weight = c.fuzz;
                inputs.fuzz_color = vec3(1.0f);

                const Result r = run(inputs, cos_view, n, seed++);

                double expected = -1.0, model_tolerance = 0.0;
                if (c.kind == White)
                {
                    expected = 1.0;
                    model_tolerance = (c.coat > 0.0f && cos_view < 0.2f) ? 0.03 : 0.005;
                }
                else if (c.kind == Lambert)
                {
                    expected = c.base_gray;
                    model_tolerance = 0.001;
                }

                const double tolerance = model_tolerance + 3.0 * r.std_error;
                const char* verdict = "n/a";
                if (expected >= 0.0)
                {
                    ++judged;
                    const bool energy_ok = std::abs(r.albedo - expected) <= tolerance;
                    const bool consistency_ok = r.weight_vs_eval_mean_rel < 1e-4 && r.pdf_mismatch_mean_rel < 1e-4;
                    verdict = (energy_ok && consistency_ok) ? "PASS" : "FAIL";
                    if (!(energy_ok && consistency_ok))
                        ++failures;
                }
                worst_consistency = std::max(worst_consistency, r.weight_vs_eval_mean_rel);

                std::printf("%-26s %5.2f %5.2f  %8.4f %8.4f %8.4f %8.4f %9.2e  %s\n", c.name, roughness, cos_view, r.albedo,
                            3.0 * r.std_error, expected, tolerance, r.weight_vs_eval_mean_rel, verdict);
                if (csv)
                    std::fprintf(csv, "%s,%s,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f,%.6f,%.6f,%.6f,%.4f,%.6f,%s\n", c.name,
                                 c.kind == White ? "white" : (c.kind == Lambert ? "lambert" : "control"), c.metalness, c.base_gray,
                                 c.specular_weight, c.coat, c.fuzz, roughness, cos_view, r.albedo, r.std_error, r.max_rgb, expected,
                                 tolerance, verdict);
            }
        }
    }
    if (csv)
        std::fclose(csv);

    std::printf("\nseed %u: %d rows judged, %d failed. worst mean relative |weight - eval/pdf| = %.3e\n", seed_base, judged, failures,
                worst_consistency);
    return failures == 0 ? 0 : 1;
}
