// Generated only from frozen semantic fixtures; does not contain oracle output.
std::vector<Case> cases() {
std::vector<Case> out;
{ Case c;
c.name="dark";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
out.push_back(c); }
{ Case c;
c.name="dome_control";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.7f,1.1f,0.4f};
c.seeds={479123,1299721,753649};
out.push_back(c); }
{ Case c;
c.name="distant_delta";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_oblique";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{-0.6f,0.0f,-0.8f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_translation";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{12.0f,-53.0f,72.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_below_receiver";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_double";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{1.8f,1.2f,0.6f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_exposure";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{1.8f,1.2f,0.6f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_tilted_normal";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.6f,0.0f,0.8f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_cone_12";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,0.994521895f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_normalized_12";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,0.994521895f},{0.0f,0.0f,-1.0f,0.0f},{26.2194457f,17.4796304f,8.73981522f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_cone_60";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,0.866025404f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_normalized_60";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,0.866025404f},{0.0f,0.0f,-1.0f,0.0f},{1.14591559f,0.763943727f,0.381971863f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.714285714f,0.476190476f,0.238095238f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_moved";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.6f,-0.35f,2.2f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_backface";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_scaled_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{1.05f,0.0f,0.0f,1.26f},{0.0f,0.3f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.714285714f,0.476190476f,0.238095238f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.584650811f,0.389767208f,0.194883604f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_moved";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.6f,-0.35f,2.2f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_backface";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_scaled_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,1.0f},{1.05f,0.0f,0.0f,0.989601686f},{0.0f,0.3f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.909456818f,0.606304545f,0.303152273f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="sphere";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="sphere_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.236759419f,0.157839613f,0.0789198065f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="sphere_moved";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.6f,-0.35f,2.2f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="sphere_scaled_normalized";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,2.0f},{0.0f,0.0f,0.0f,8.04247719f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.8f},{0.111905819f,0.0746038796f,0.0373019398f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_tilted";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.56f,0.0f,-0.42f,1.26f},{0.0f,0.45f,0.0f,1.0f},{-0.6f,0.0f,-0.8f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_tilted";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,1.0f},{0.56f,0.0f,-0.42f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{-0.6f,0.0f,-0.8f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_small";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.025f,0.0f,0.0f,0.0025f},{0.0f,0.025f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{90.0f,60.0f,30.0f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_double";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{1.8f,1.2f,0.6f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_exposure";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{1.8f,1.2f,0.6f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="mixed";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{-0.7f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{3.0f,0.3f,0.15f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.8f,0.2f,2.7f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.07f,0.7f,0.14f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.0f,-0.4f,2.4f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.12f,0.24f,1.2f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.04f,0.06f,0.02f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="mixed_reordered";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.04f,0.06f,0.02f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.0f,-0.4f,2.4f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.12f,0.24f,1.2f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.8f,0.2f,2.7f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.07f,0.7f,0.14f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{-0.7f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{3.0f,0.3f,0.15f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="mixed_dome";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.17f,0.11f,0.23f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{-0.7f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{3.0f,0.3f,0.15f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.8f,0.2f,2.7f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.07f,0.7f,0.14f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.0f,-0.4f,2.4f,2.0f},{0.0f,0.0f,0.0f,3.80132711f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.55f},{0.12f,0.24f,1.2f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.04f,0.06f,0.02f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="light_removed";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.lights.push_back(HdRTVkLight{{-0.7f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{3.0f,0.3f,0.15f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="distant_shadow";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.blocker=true; c.block={0.0f,10.0f,0.3f,1.5f};
c.lights.push_back(HdRTVkLight{{0.0f,0.0f,3.0f,3.0f},{0.0f,0.0f,0.0f,0.0f},{0.0f,0.0f,0.0f,1.0f},{0.6f,0.0f,-0.8f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="rect_shadow";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.blocker=true; c.block={0.0f,10.0f,0.3f,1.5f};
c.lights.push_back(HdRTVkLight{{-1.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="disk_shadow";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.blocker=true; c.block={0.0f,10.0f,0.3f,1.5f};
c.lights.push_back(HdRTVkLight{{-1.0f,0.0f,3.0f,1.0f},{0.7f,0.0f,0.0f,1.5393804f},{0.0f,0.7f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
{ Case c;
c.name="blocker_behind_light";
c.width=24; c.height=24; c.spp=2048;
c.extent=1.2f;
c.albedo={0.45f,0.3f,0.18f};
c.normal={0.0f,0.0f,1.0f};
c.dome={0.0f,0.0f,0.0f};
c.seeds={479123,1299721,753649};
c.blocker=true; c.block={-2.0f,10.0f,4.0f,10.0f};
c.lights.push_back(HdRTVkLight{{-1.0f,0.0f,3.0f,0.0f},{0.7f,0.0f,0.0f,1.26f},{0.0f,0.45f,0.0f,1.0f},{0.0f,0.0f,-1.0f,0.0f},{0.9f,0.6f,0.3f,0.0f},{0.0f,0.0f,0.0f,0.0f}});
out.push_back(c); }
return out;
}
