"""Opt-in installed native OpenPBR workflow; requires a manifest and exclusive GPU slot."""
import importlib.util, json, os, time
from pathlib import Path
import hou, voptoolutils
from hutil.Qt import QtCore, QtWidgets
from pxr import UsdShade
ROOT = Path(os.environ['RT_LIVE_HARNESS_ROOT'])
os.environ['RT_LIGHTING_AUTOSTART'] = '0'
spec = importlib.util.spec_from_file_location('material_lighting_base', ROOT/'gui_reliable_lighting.py')
lighting = importlib.util.module_from_spec(spec); spec.loader.exec_module(lighting)


class MaterialSmoke(lighting.LightingSmoke):
    def __init__(self):
        super().__init__()
        self.material_edit = None; self.resume_edit = None
        self.expected_supported = 2; self.expected_fallback = False

        self.result.update(scope='Installed native OpenPBR edits, assignments, support, pause and current images',
                           native_material_edits=[], material_publications=[])
        self.result['harness_sha256']['gui_reliable_materials.py'] = self.sha(ROOT/'gui_reliable_materials.py')
        self.steps = [self.setup, self.select_karma, self.pause_karma, self.check_karma,
          self.prepare_mixed, self.capture_original, self.open_panel, self.target_viewer,
          self.enable, self.check_active, self.assert_guard, self.start_support, self.await_support,
          self.active_pause, self.await_active_ack, self.check_active_hold,
          self.edit_material_while_paused, self.await_paused_material, self.check_material_pause_hold,
          self.recreate_panel, self.target_viewer, self.check_recreated, self.resume,
          self.await_material_image, self.create_other, self.check_other, self.capture_other,
          self.start_support, self.await_support, self.material_step('stale color', base_color=(.5,.15,.05)),
          self.await_material_image, self.await_support_stale, self.start_support, self.await_support]
        for i in range(3): self.steps += [self.fast_step(i), self.await_camera]
        self.steps += [self.fast_step(3), self.await_camera, self.begin_lighting_images, self.await_capture_budget]
        cases = [('baseline', dict(base_color=(.2,.4,.7), base_metalness=0., specular_roughness=.3,
                                  specular_ior=1.5, specular_weight=1.)),
                 ('base_color', dict(base_color=(.65,.12,.04))),
                 ('metalness', dict(base_color=(.2,.4,.7), base_metalness=.8)),
                 ('roughness', dict(base_metalness=0., specular_roughness=.65)),
                 ('ior', dict(specular_roughness=.3, specular_ior=2.2)),
                 ('specular_weight', dict(specular_ior=1.5, specular_weight=.2))]
        for name, values in cases:
            self.steps += [self.material_step(name, **values), self.await_material_image,
                           self.await_lighting_budget, self.capture_material_step(name)]
        self.steps += [self.reassign_material, self.await_material_image, self.await_lighting_budget,
          self.capture_material_step('reassigned'), self.start_support, self.await_support,
          self.connect_unsupported, self.await_material_image, self.await_lighting_budget,
          self.capture_material_step('unsupported_connection'), self.start_support, self.await_support,
          self.restore_supported, self.await_material_image, self.await_lighting_budget,
          self.capture_material_step('restored'), self.start_support, self.await_support,
          self.restore_capture_guides, self.disable, self.check_restored, self.assert_floor_restored,
          self.restore_startup_flags, self.await_startup_flags, self.final_material_checks]

    def create_scene(self):
        super().create_scene()
        prior = self.output_node
        self.library = hou.node('/stage').createNode('materiallibrary', 'hrt_native_materials')
        self.library.setInput(0, prior); self.library.parm('genpreviewshaders').set(0)
        self.shaders = {}
        for name, color in [('openpbr', (.2,.4,.7)), ('alternate', (.08,.65,.2))]:
            builder = voptoolutils._setupMtlXBuilderSubnet(destination_node=self.library, name=name)
            shader = builder.createNode('mtlxopen_pbr_surface', 'surface')
            shader.parmTuple('base_color').set(color)
            builder.node('surface_output').setInput(0, shader)
            for child in builder.children():
                if child.type().name() == 'mtlxstandard_surface': child.destroy()
            self.shaders[name] = shader
        self.shader = self.shaders['openpbr']
        self.assignment = hou.node('/stage').createNode('assignmaterial', 'hrt_native_assignment')
        self.assignment.setInput(0, self.library)
        self.assignment.parm('nummaterials').set(2)
        for index, mesh in enumerate(('red', 'green'), 1):
            self.assignment.parm('primpattern'+str(index)).set('/World/'+mesh)
            self.assignment.parm('matspecpath'+str(index)).set('/materials/openpbr')
        self.output_node = self.assignment
        self.output_node.setDisplayFlag(True); self.output_node.cook(force=True)
        assert not self.output_node.errors(), self.output_node.errors()
        self.result['native_material_nodes'] = {name:shader.path() for name,shader in self.shaders.items()}
        self.result['native_material_library'] = self.library.path()
        self.result['native_material_assignment'] = self.assignment.path()

    def setup(self):
        super().setup()
        import rt_runtime.material_support as support
        self.result['imported_material_support'] = self.verify_file(support.__file__)
        self.record_material_values('initial')

    def record_material_values(self, label):
        stage = self.output_node.stage(); values = {}
        for mesh in ('red', 'green'):
            material, binding = UsdShade.MaterialBindingAPI(stage.GetPrimAtPath('/World/'+mesh)).ComputeBoundMaterial('full')
            shader, port, kind = material.ComputeSurfaceSource('mtlx')
            assert shader and port == 'out' and kind == UsdShade.AttributeType.Output
            values[mesh] = dict(material=str(material.GetPath()), terminal=str(shader.GetPath()),
                shader_id=shader.GetIdAttr().Get(),
                inputs={str(p.GetBaseName()):str(p.Get()) for p in shader.GetInputs()},
                connections={str(p.GetBaseName()):[str(x) for x in p.GetAttr().GetConnections()]
                             for p in shader.GetInputs() if p.GetAttr().GetConnections()})
        self.result['native_material_edits'].append(dict(label=label, values=values, time_ns=time.perf_counter_ns()))
        self.write()

    def await_support(self):
        status = self.widget.findChild(QtWidgets.QLabel, 'rt_scene_support_status').text()
        text = self.widget.findChild(QtWidgets.QPlainTextEdit, 'rt_scene_support_report').toPlainText()
        self.result['support_last_observation'] = dict(status=status, text=text)
        self.write()
        assert 'unavailable' not in status.lower(), (status, text)
        self.ready('Snapshot check complete' in status, 'Waiting complete material support report')
        report = self.widget._support_scan.report
        self.verify_file(report.profile.path)
        assert report.profile.version == 'reliable-materials-v1', report.profile
        assert report.material_bindings == 2 and report.supported_material_bindings == self.expected_supported, text
        fallback = [f for f in report.findings if f.feature == 'Material fallback']
        assert bool(fallback) == self.expected_fallback, text
        if fallback:
            assert all(f.path == '/World/red' and 'inputs:base_weight' in f.reason for f in fallback), text
        assert '/Unsupported/cylinder' in text and 'Material Library / OpenPBR Surface' in text, text
        self.result['support_reports'].append(dict(status=status, text=text,
            profile_path=report.profile.path, profile_sha256=self.sha(report.profile.path),
            expected_supported=self.expected_supported, expected_fallback=self.expected_fallback))
        self.write()

    def material_change(self, label, action):
        self.journal.pump(); before = dict(self.journal.latest['request'])
        started = time.perf_counter_ns()
        with hou.undos.group('HoudiniRT verification native material edit'): action()
        self.output_node.cook(force=True)
        assert not self.output_node.errors(), self.output_node.errors()
        self.viewer.curViewport().draw(); hou.ui.postRedrawFence()
        self.material_edit = dict(label=label, before_request=before, started_ns=started,
                                  driver_returned_ns=time.perf_counter_ns())
        self.record_material_values(label)

    def material_step(self, label, **values):
        def change():
            for name, value in values.items():
                parm = self.shader.parmTuple(name) if isinstance(value, tuple) else self.shader.parm(name)
                assert parm is not None, name
                parm.set(value)
        def step(): self.material_change(label, change)
        step.__name__ = 'material_' + label.replace(' ', '_')
        return step

    def await_material_image(self):
        self.ready(self.journal.current(), 'Waiting current material image')
        present = self.journal.latest['present']; edit = self.material_edit
        self.ready(present['generation'] > edit['before_request']['generation'], 'Waiting material generation')
        self.ready(present['scene'] != edit['before_request']['scene'], 'Waiting actual material scene update')
        edit.update(presentation=present, observed_ms=(time.perf_counter_ns()-edit['started_ns'])/1e6)
        self.result['material_publications'].append(edit)
        if self.resume_edit is edit:
            self.result['paused_material_edit']['resumed_latest'] = present
        self.material_edit = None
        if self.other_original is not None: assert self.guides(self.other) == self.other_original
        self.write()

    def edit_material_while_paused(self):
        assert self.viewer.isRendererPaused()
        self.material_step('paused latest base color', base_color=(.25,.55,.1))()
        self.resume_edit = self.material_edit

    def await_paused_material(self):
        self.ready(time.perf_counter_ns()-self.resume_edit['driver_returned_ns'] >= 500_000_000,
                   'Waiting paused material observation')
        self.journal.pump(); assert self.viewer.isRendererPaused()
        self.result['paused_material_edit'] = dict(edit=self.resume_edit,
            request_while_paused=self.journal.latest.get('request'), host_may_defer_execute_until_resume=True)
        self.write()

    def check_material_pause_hold(self):
        self.ready(time.perf_counter_ns()-self.resume_edit['driver_returned_ns'] >= 2_000_000_000,
                   'Holding edited paused material')
        self.journal.pump(); ack = self.active_hold['ack']
        assert self.viewer.isRendererPaused()
        assert not [e for e in self.journal.events if e['sequence'] > ack['sequence']
                    and e.get('renderer') == self.renderer and e['kind'] in ('batch', 'present')]
        self.result['paused_material_edit']['no_work_hold_confirmed_ns'] = time.perf_counter_ns()
        self.write()

    def reassign_material(self):
        self.material_change('native binding reassignment',
            lambda: self.assignment.parm('matspecpath2').set('/materials/alternate'))

    def connect_unsupported(self):
        def change():
            self.constant = self.shader.parent().createNode('mtlxconstant', 'unsupported_constant')
            self.constant.parm('value').set(.5)
            self.shader.setInput(self.shader.inputNames().index('base_weight'), self.constant)
        self.material_change('native connected constant fallback', change)
        self.expected_supported = 1; self.expected_fallback = True

    def restore_supported(self):
        def change():
            self.shader.setInput(self.shader.inputNames().index('base_weight'), None)
            self.constant.destroy()
        self.material_change('restore supported native shader', change)
        self.expected_supported = 2; self.expected_fallback = False

    def await_support_stale(self):
        status = self.widget.findChild(QtWidgets.QLabel, 'rt_scene_support_status').text()
        self.ready('Outdated scene check' in status, 'Waiting native material notice invalidation: '+status)
        self.result['support_stale_after_native_material_edit'] = dict(status=status, time_ns=time.perf_counter_ns())
        self.write()

    def capture_material_step(self, name):
        def step():
            self.capture(name)
            path = self.output.parent/'images'/(name+'.usda')
            stage = self.viewer.stage()
            assert stage.Export(str(path))
            settings = stage.GetPrimAtPath('/Render/settings')
            effective = {key:settings.GetAttribute('houdinirt:'+key).Get() for key in ('spp','bounces','seed')}
            assert effective == dict(spp=256,bounces=4,seed=101), effective
            resolution = list(settings.GetAttribute('resolution').Get())
            capture = self.images[name]
            capture.update(stage_path=str(path), stage_sha256=self.sha(path), camera=self.camera(),
                effective_render_settings=effective, authored_resolution=resolution,
                viewport_size=list(self.viewer.curViewport().size()),
                child_config_path=os.environ['HOUDINIRT_S3_CONFIG'],
                child_config=json.loads(Path(os.environ['HOUDINIRT_S3_CONFIG']).read_text()),
                active_camera=self.viewer.curViewport().cameraPath())
            assert capture['active_camera'] == '/Camera'
            assert capture['present_after']['samples'] == 256
            self.record_material_values('captured '+name)
        step.__name__ = 'capture_material_'+name
        return step

    def begin_lighting_images(self):
        # Solaris beautyPassOnly retains light/selection/origin display decorations.
        self.capture_viewport_settings = self.viewer.curViewport().settings()
        self.capture_display_original = dict(lights=self.viewer.showLights(),
            selection=self.viewer.showSelection(), object_origins=self.capture_viewport_settings.objectOrigins())
        self.viewer.setShowLights(False)
        self.viewer.setShowSelection(False)
        self.capture_viewport_settings.setObjectOrigins(False)
        current = dict(lights=self.viewer.showLights(), selection=self.viewer.showSelection(),
                       object_origins=self.capture_viewport_settings.objectOrigins())
        assert not any(current.values()), current
        self.result['capture_display_override'] = dict(
            apis=['SceneViewer.setShowLights','SceneViewer.setShowSelection','GeometryViewportSettings.setObjectOrigins'],
            before=dict(self.capture_display_original), during=current,
            scope='Task-owned viewer display decorations only; no USD opinions, framing changes or raster masking')
        self.viewer.curViewport().draw(); hou.ui.postRedrawFence()
        super().begin_lighting_images()

    def restore_capture_guides(self):
        if hasattr(self, 'capture_display_original'):
            before = self.capture_display_original
            self.viewer.setShowLights(before['lights'])
            self.viewer.setShowSelection(before['selection'])
            self.capture_viewport_settings.setObjectOrigins(before['object_origins'])
            restored = dict(lights=self.viewer.showLights(), selection=self.viewer.showSelection(),
                            object_origins=self.capture_viewport_settings.objectOrigins())
            assert restored == before, (before, restored)
            self.result['capture_display_override']['restored'] = restored
            del self.capture_display_original, self.capture_viewport_settings
            self.write()

    def finish(self, passed):
        if hasattr(self, 'capture_display_original'):
            try:self.restore_capture_guides()
            except Exception as error:self.result['capture_guide_restore_error'] = str(error)
        super().finish(passed)

    def final_material_checks(self):
        self.observe('final original settings', True)
        assert self.viewer.currentHydraRenderer() == self.previous
        assert self.viewer.isRendererPaused() == self.previous_paused
        assert self.other.currentHydraRenderer() == 'Karma CPU' and not self.other.isRendererPaused()
        assert len([x for x in self.latencies if x['kind'] == 'camera']) == 4
        assert set(self.images) == {'baseline','base_color','metalness','roughness','ior','specular_weight',
                                    'reassigned','unsupported_connection','restored'}
        assert self.result.get('support_stale_after_native_material_edit')
        assert self.result['paused_material_edit'].get('resumed_latest')
        self.result.update(all_requested_workflow_checks=True,
            material_counter_scope='HOM does not expose delegate AS/upload counters; native GPU/CPU tests own that invariant.',
            image_validation='Exact displayed USD stages and viewport EXRs captured; independent replay/image judge required.')
        self.write()


if os.environ.get('RT_MATERIAL_AUTOSTART','1') == '1':
    hou.session._hrt_material_smoke = MaterialSmoke()
    hou.session._hrt_material_smoke.write()
    QtCore.QTimer.singleShot(int(os.environ.get('RT_INSTALLED_START_DELAY_MS','1000')),
                            hou.session._hrt_material_smoke.run_next)
