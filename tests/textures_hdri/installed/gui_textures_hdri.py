"""Owned regular-H22 native texture/HDRI workflow; requires installed manifest/GPU handoff."""
import importlib.util,json,os,time
from pathlib import Path
import hou
from hutil.Qt import QtCore,QtWidgets
ROOT=Path(os.environ['RT_LIVE_HARNESS_ROOT'])
os.environ['RT_MATERIAL_AUTOSTART']='0'
spec=importlib.util.spec_from_file_location('texture_material_base',ROOT/'gui_reliable_materials.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
CASES=('baseline','uv_transform','roughness_map','image_swap','hdri_rotation','hdri_exposure','hdri_swap','missing_image','restored')


class TextureSmoke(base.MaterialSmoke):
    def __init__(self):
        super().__init__()
        self.assets=Path(os.environ['RT_TEXTURE_ASSETS']).resolve()
        self.asset_manifest=json.loads((self.assets/'assets.json').read_text())
        for item in self.asset_manifest['files'].values():assert self.sha(item['path'])==item['sha256']
        self.result.update(scope='Native texture/UV/HDRI edits, pause, current images and integrated restoration',
            asset_manifest=str(self.assets/'assets.json'),asset_manifest_sha256=self.sha(self.assets/'assets.json'),
            texture_controls=[],capture_cases=CASES,reference_kind='fresh_normal_houdini_gui_flipbook',raw_husk_equivalence=False)
        self.result['harness_sha256']['gui_textures_hdri.py']=self.sha(ROOT/'gui_textures_hdri.py')
        self.steps=[self.setup,self.select_karma,self.pause_karma,self.check_karma,self.prepare_mixed,self.capture_original,
            self.open_panel,self.target_viewer,self.enable,self.check_active,self.assert_guard,self.start_support,self.await_support,
            self.active_pause,self.await_active_ack,self.open_image_controls,self.check_active_hold,self.await_image_observation,
            self.show_dome_controls,self.await_native_controls,self.close_native_controls,self.edit_material_while_paused,
            self.await_paused_material,self.check_material_pause_hold,self.recreate_panel,self.target_viewer,
            self.check_recreated,self.resume,self.await_material_image,self.create_other,self.check_other,self.capture_other,
            self.start_support,self.await_support,self.stale_edit,self.await_material_image,self.await_support_stale,
            self.start_support,self.await_support]
        for i in range(4):self.steps += [self.fast_step(i),self.await_camera]
        self.steps += [self.begin_lighting_images,self.await_capture_budget]
        for name in CASES:
            self.steps += [self.texture_step(name),self.await_material_image,self.await_lighting_budget,
                           self.capture_texture_step(name)]
            if name in ('missing_image','restored'):self.steps += [self.start_support,self.await_support]
        self.steps += [self.restore_capture_guides,self.disable,self.check_restored,self.assert_floor_restored,
                       self.restore_startup_flags,self.await_startup_flags,self.final_texture_checks]

    def create_scene(self):
        super().create_scene()
        code=self.node.parm('python').evalAsString()+"""
for name in ('red','green'):
    mesh=UsdGeom.Mesh(stage.GetPrimAtPath('/World/'+name))
    UsdGeom.PrimvarsAPI(mesh).CreatePrimvar('st',Sdf.ValueTypeNames.TexCoord2fArray,'faceVarying').Set([(0,0),(1,0),(1,1),(0,1)])
"""
        self.node.parm('python').set(code)
        self.dome=hou.node('/stage').createNode('domelight','hrt_native_hdri')
        assert self.dome.type().name()=='domelight::3.0'
        self.dome.setInput(0,self.node);self.library.setInput(0,self.dome)
        self.dome.parm('primpath').set('/Lights/environment')
        self.dome.parm('poleAxis').set('Y')
        self.dome.parm('xn__inputstextureformat_06ah').set('latlong')
        self.dome.parm('xn__inputstexturefile_r3ah').set((self.assets/'sky_a.exr').as_posix())
        builder=self.shader.parent()
        self.uv=builder.createNode('mtlxtexcoord','uv0')
        self.place=builder.createNode('mtlxplace2d','uv_placement')
        self.place.setInput(self.place.inputIndex('texcoord'),self.uv)
        self.image=builder.createNode('mtlximage','base_image')
        self.image.parm('filecolorspace').set('srgb_texture')
        self.rough=builder.createNode('mtlximage','roughness_image');self.rough.parm('signature').set('default')
        self.rough.parm('filecolorspace').set('Raw')
        for node in (self.image,self.rough):node.setInput(node.inputIndex('texcoord'),self.place)
        self.shader.setInput(self.shader.inputIndex('base_color'),self.image)
        self.shader.setInput(self.shader.inputIndex('specular_roughness'),self.rough)
        self.apply_case('baseline');self.output_node.cook(force=True)
        assert not self.output_node.errors(),self.output_node.errors()
        self.result['native_texture_nodes']={n.path():n.type().name() for n in (self.dome,self.uv,self.place,self.image,self.rough)}

    def setup(self):
        super().setup()
        import rt_runtime.texture_support as support
        import PyOpenColorIO as ocio
        self.result['imported_texture_support']=self.verify_file(support.__file__)
        config=Path(hou.Color.ocio_configPath()).resolve()
        self.result['native_color_context']=dict(path=str(config),sha256=self.sha(config),
            scene_linear=ocio.GetCurrentConfig().getColorSpace('scene_linear').getName(),
            cache_id=ocio.GetCurrentConfig().getCacheID())
        self.record_texture_controls('initial')

    def apply_case(self,name):
        values=dict(base='base_a.png',rough='rough_a.exr',sky='sky_a.exr',scale=(1,1),offset=(0,0),rotate=0.,rotation=(0,0,0),exposure=0.,pole='Y')
        changes={'uv_transform':dict(scale=(1.7,.8),offset=(.2,-.1),rotate=30.),
            'roughness_map':dict(rough='rough_b.exr'),'image_swap':dict(base='base_b.png'),
            'hdri_rotation':dict(rotation=(0,60,0)),'hdri_exposure':dict(exposure=1.),
            'hdri_swap':dict(sky='sky_b.exr'),'missing_image':dict(base='deliberately_missing.png')}
        values.update(changes.get(name,{}))
        self.image.parm('file').set((self.assets/values['base']).as_posix())
        self.rough.parm('file').set((self.assets/values['rough']).as_posix())
        self.dome.parm('xn__inputstexturefile_r3ah').set((self.assets/values['sky']).as_posix())
        for key in ('scale','offset'):self.place.parmTuple(key).set(values[key])
        self.place.parm('rotate').set(values['rotate'])
        self.dome.parmTuple('r').set(values['rotation'])
        self.dome.parm('poleAxis').set(values['pole'])
        self.dome.parm(hou.text.encodeParm('inputs:exposure')).set(values['exposure'])
        self.case_values=values

    def texture_step(self,name):
        def step():
            self.material_change(name,lambda:self.apply_case(name));self.record_texture_controls(name)
        step.__name__='texture_'+name
        return step

    def record_texture_controls(self,label):
        record=dict(label=label,time_ns=time.perf_counter_ns(),
            image_file=self.image.parm('file').evalAsString(),image_colorspace=self.image.parm('filecolorspace').evalAsString(),
            roughness_file=self.rough.parm('file').evalAsString(),roughness_colorspace=self.rough.parm('filecolorspace').evalAsString(),
            uv_scale=self.place.parmTuple('scale').eval(),uv_offset=self.place.parmTuple('offset').eval(),
            uv_rotate=self.place.parm('rotate').eval(),uv_order=self.place.parm('operationorder').eval(),
            dome_file=self.dome.parm('xn__inputstexturefile_r3ah').evalAsString(),dome_pole=self.dome.parm('poleAxis').evalAsString(),
            dome_format=self.dome.parm('xn__inputstextureformat_06ah').evalAsString(),
            dome_rotation=self.dome.parmTuple('r').eval(),dome_exposure=self.dome.parm(hou.text.encodeParm('inputs:exposure')).eval())
        self.result['texture_controls'].append(record);self.write()
        return record

    def await_support(self):
        status=self.widget.findChild(QtWidgets.QLabel,'rt_scene_support_status').text()
        text=self.widget.findChild(QtWidgets.QPlainTextEdit,'rt_scene_support_report').toPlainText()
        self.result['support_last_observation']=dict(status=status,text=text);self.write()
        assert 'unavailable' not in status.lower(),(status,text)
        self.ready('Snapshot check complete' in status,'Waiting texture support check')
        report=self.widget._support_scan.report;self.verify_file(report.profile.path)
        assert report.profile.version=='textures-hdri-v1',report.profile
        assert report.material_bindings==2 and report.supported_material_bindings==0,text
        assert not [f for f in report.findings if f.feature=='Material fallback'],text
        assert '/materials/openpbr/base_image.inputs:file' in text and '/Lights/environment' in text,text
        assert 'Not assessed' in text and 'not checked' in text and '/Unsupported/cylinder' in text,text
        self.result['support_reports'].append(dict(status=status,text=text,profile_path=report.profile.path,
            profile_sha256=self.sha(report.profile.path),asset_checks='Not assessed; no UI file resolution or decoding'))
        self.write()

    def edit_material_while_paused(self):
        assert self.viewer.isRendererPaused()
        def change():
            self.place.parmTuple('offset').set((.35,.2));self.dome.parm('poleAxis').set('Z')
        self.material_change('paused native UV offset and dome pole axis',change)
        self.resume_edit=self.material_edit;self.record_texture_controls('paused UV offset and pole Z')

    def open_image_controls(self):
        self.native_parms=hou.ui.curDesktop().createFloatingPaneTab(hou.paneTabType.Parm,size=(650,760))
        self.native_parms.setCurrentNode(self.image,pick_node=False);self.native_parms.setPin(True)
        screen=QtWidgets.QApplication.primaryScreen().availableGeometry()
        self.native_parms.floatingPanel().setPosition((screen.x()+20,screen.y()+30))
        self.panel.floatingPanel().setPosition((screen.x()+min(700,screen.width()-450),screen.y()+30))
        self.native_image_shown=time.monotonic()
        self.result['native_parameter_panes']=[dict(node=self.image.path(),kind='MaterialX Image',opened_ns=time.perf_counter_ns())]
        self.write()

    def await_image_observation(self):
        self.await_observation('image',self.image,self.native_image_shown)

    def show_dome_controls(self):
        self.native_parms.setCurrentNode(self.dome,pick_node=False)
        self.native_dome_shown=time.monotonic()
        self.result['native_parameter_panes'].append(dict(node=self.dome.path(),kind='Dome Light',opened_ns=time.perf_counter_ns()))
        self.write()

    def await_native_controls(self):
        self.await_observation('dome',self.dome,self.native_dome_shown)

    def await_observation(self,phase,node,started):
        marker=self.output.parent/('native_'+phase+'_observed.json')
        if not marker.is_file():
            assert time.monotonic()-started<90,'Native '+phase+' CUA observation timed out'
            self.deadline=time.perf_counter()+5
            self.ready(False,'Waiting actual native '+phase+' control observation marker')
        payload=json.loads(marker.read_text())
        assert payload.get('observed') is True and payload.get('phase')==phase and payload.get('node')==node.path(),payload
        self.result.setdefault('native_control_observations',{})[phase]=dict(path=str(marker),sha256=self.sha(marker),observation=payload)
        self.write()

    def close_native_controls(self):
        if getattr(self,'native_parms',None) is not None:
            self.native_parms.close();self.native_parms=None

    def finish(self,passed):
        self.close_native_controls()
        super().finish(passed)

    def stale_edit(self):
        self.material_change('native UV change invalidates support',lambda:self.place.parm('rotate').set(17))
        self.record_texture_controls('report stale UV edit')

    def capture_texture_step(self,name):
        inherited=self.capture_material_step(name)
        def step():
            inherited()
            self.images[name]['native_texture_controls']=self.record_texture_controls('captured '+name)
            self.images[name]['asset_files']={key:dict(path=value['path'],sha256=value['sha256'])
                for key,value in self.asset_manifest['files'].items()}
            self.images[name]['native_color_context']=dict(self.result['native_color_context'])
            self.images[name]['missing_asset_expected']=name=='missing_image'
            self.write()
        step.__name__='capture_texture_'+name
        return step

    def final_texture_checks(self):
        self.observe('final original settings',True)
        assert self.viewer.currentHydraRenderer()==self.previous and self.viewer.isRendererPaused()==self.previous_paused
        assert self.other.currentHydraRenderer()=='Karma CPU' and not self.other.isRendererPaused()
        assert len([x for x in self.latencies if x['kind']=='camera'])==4
        assert set(self.images)==set(CASES)
        assert self.result.get('support_stale_after_native_material_edit')
        assert self.result['paused_material_edit'].get('resumed_latest')
        for item in self.asset_manifest['files'].values():assert self.sha(item['path'])==item['sha256']
        assert not (self.assets/'deliberately_missing.png').exists()
        self.result.update(all_requested_workflow_checks=True,assets_unchanged=True,
            texture_counter_scope='Native GPU/CPU tests own UV/texture upload, AS work and decode/cache counters; HOM does not expose them.',
            native_diagnostic_scope='Missing asset is deliberately unassessed by panel; native log and fresh-image judge verify fallback/recovery.',
            same_path_reload_scope='Independent loader tests cover same-path reload; GUI tests cover Off/On restoration, not an end-to-end overwrite image claim.',
            image_validation='Nine current viewport EXRs and exact immutable stages; compare full-frame fresh normal-Houdini captures, not raw-husk edge parity.')
        self.write()


if os.environ.get('RT_TEXTURE_AUTOSTART','1')=='1':
    hou.session._hrt_texture_smoke=TextureSmoke();hou.session._hrt_texture_smoke.write()
    QtCore.QTimer.singleShot(int(os.environ.get('RT_INSTALLED_START_DELAY_MS','1000')),hou.session._hrt_texture_smoke.run_next)
