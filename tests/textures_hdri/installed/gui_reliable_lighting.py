"""Opt-in installed lighting workflow; launch only with a verified manifest/GPU slot."""
import importlib.util, json, os, time
from pathlib import Path
import hou
from hutil.Qt import QtCore, QtWidgets
ROOT = Path(os.environ['RT_LIVE_HARNESS_ROOT'])
os.environ['RT_CAMERA_AUTOSTART'] = '0'
spec = importlib.util.spec_from_file_location('lighting_camera_base', ROOT/'gui_camera_repair.py')
camera = importlib.util.module_from_spec(spec); spec.loader.exec_module(camera)


class LightingSmoke(camera.CameraRepairSmoke):
    def __init__(self):
        super().__init__()
        self.lights = {}; self.lighting_edit = None; self.resume_edit = None
        self.report_before = None; self.capture_floor = 0; self.spp_budget = 65536
        self.result.update(scope='Regular installed native-light controls, support report, pause and current images',
                           native_light_edits=[], support_reports=[])
        self.result['harness_sha256']['gui_reliable_lighting.py'] = self.sha(ROOT/'gui_reliable_lighting.py')
        self.steps = [self.setup, self.select_karma, self.pause_karma, self.check_karma,
          self.prepare_mixed, self.capture_original, self.open_panel, self.target_viewer,
          self.enable, self.check_active, self.assert_guard, self.start_support, self.await_support,
          self.active_pause, self.await_active_ack, self.check_active_hold,
          self.edit_light_while_paused, self.await_paused_edit_request, self.check_edited_pause_hold,
          self.recreate_panel, self.target_viewer, self.check_recreated, self.resume,
          self.await_latest_light_resume, self.create_other, self.check_other, self.capture_other,
          self.start_support, self.await_support, self.change_native_exposure, self.await_light_image,
          self.await_support_stale, self.start_support, self.await_support]
        for i in range(5): self.steps.extend([self.fast_step(i), self.await_camera])
        self.steps += [self.reset_camera, self.await_camera, self.begin_lighting_images]
        for kind in ('rect', 'distant', 'disk', 'sphere'):
            self.steps += [self.solo_step(kind), self.await_light_image, self.await_lighting_budget,
                           self.capture_step(kind+'_base')]
        self.steps += [self.solo_step('rect'), self.await_light_image, self.await_lighting_budget,
          self.capture_step('rect_exposure0'), self.exposure_step(1), self.await_light_image,
          self.await_lighting_budget, self.capture_step('rect_exposure1'),
          self.disable, self.check_restored, self.assert_floor_restored,
          self.restore_startup_flags, self.await_startup_flags, self.final_lighting_checks]

    @staticmethod
    def set_menu(node, name, desired):
        parm = node.parm(name); assert parm is not None, (node.path(),name)
        items, labels = parm.menuItems(), parm.menuLabels()
        matches = [i for i, (item,label) in enumerate(zip(items,labels))
                   if desired in (item.lower(),label.lower()) or desired in label.lower()]
        assert len(matches)==1, (name,desired,items,labels)
        index=matches[0]
        parm.set(index if parm.parmTemplate().type()==hou.parmTemplateType.Menu else items[index])
        return {'parameter':name,'item':items[index],'label':labels[index]}

    @staticmethod
    def set_native(node, name, value):
        # Light::2.0 exposes USD inputs through documented encoded parm names.
        if name not in ('primpath','t','r'):
            name=hou.text.encodeParm('inputs:'+name)
        parm=node.parmTuple(name) if isinstance(value,tuple) else node.parm(name)
        assert parm is not None, ('Native Light LOP parameter missing',node.path(),name)
        parm.set(value)

    def create_scene(self):
        super().create_scene()
        code=self.node.parm('python').evalAsString().replace('CreateIntensityAttr(1.0)','CreateIntensityAttr(0.0)')
        code += '\nUsdLux.CylinderLight.Define(stage, "/Unsupported/cylinder").CreateIntensityAttr(0.0)\n'
        self.node.parm('python').set(code)
        prior=self.node
        choices={}
        for kind in ('rect','distant','disk','sphere'):
            node=hou.node('/stage').createNode('light','hrt_native_'+kind)
            node.setInput(0,prior); prior=node
            self.set_native(node,'primpath','/Lights/'+kind)
            choices[kind]=self.set_menu(node,'lighttype',kind)
            placement={'rect':(-1.5,1.5,2.0),'disk':(0.0,1.5,2.0),
                       'sphere':(1.5,1.5,2.0),'distant':(0.0,0.0,2.0)}[kind]
            for name,value in [('intensity',20.0 if kind=='rect' else 0.0),('exposure',0.0),
                               ('normalize',1),('color',(1.0,1.0,1.0)),('t',placement)]:
                self.set_native(node,name,value)
            if kind=='rect':
                self.set_native(node,'width',1.0); self.set_native(node,'height',1.0)
            elif kind=='distant': self.set_native(node,'angle',0.53)
            else: self.set_native(node,'radius',0.5)
            self.lights[kind]=node
        self.output_node=prior
        prior.setDisplayFlag(True); prior.cook(force=True)
        assert not prior.errors(), prior.errors()
        self.result['native_light_nodes']={kind:node.path() for kind,node in self.lights.items()}
        self.result['native_light_type_menus']=choices
        self.record_light_values('initial native Light LOPs')

    def setup(self):
        super().setup()
        self.viewer.setCurrentNode(self.output_node)
        self.viewer.curViewport().setCamera('/Camera')
        import rt_runtime.scene_support as support
        self.result['imported_scene_support']=self.verify_file(support.__file__)
        self.result['expected_capability_profile']=self.verify_file(self.payload/'renderer/capabilities.json')
        config_path=Path(self.verify_file(self.payload/'houdini/UsdRenderers.json')).resolve()
        import usdrenderers
        discovered=[Path(path).resolve() for path in hou.findFiles('UsdRenderers.json')]
        assert config_path in discovered,discovered
        info=usdrenderers.getRendererInfo('HdRTRendererPlugin','HoudiniRT')
        assert info.get('depthstyle')=='opengl' and info.get('aovsupport') is True,info
        self.result['loaded_renderer_configuration']={'path':str(config_path),
            'sha256':self.sha(config_path),'discovered_paths':[str(path) for path in discovered],
            'effective_info':info}
        self.result['viewer_color_management']={'using_ocio':self.viewer.usingOCIO(),
            'display':self.viewer.getOCIODisplay(),'view':self.viewer.getOCIOView()}

    def create_other(self):
        super().create_other()
        self.other.setCurrentNode(self.output_node)

    def record_light_values(self,label):
        stage=self.output_node.stage()
        values={}
        for kind in self.lights:
            prim=stage.GetPrimAtPath('/Lights/'+kind)
            expected={'rect':'RectLight','distant':'DistantLight','disk':'DiskLight','sphere':'SphereLight'}[kind]
            assert prim.GetTypeName()==expected,(kind,prim.GetTypeName())
            values[kind]={name:prim.GetAttribute('inputs:'+name).Get() for name in ('intensity','exposure','normalize')}
        self.result['native_light_edits'].append({'label':label,'values':values,'time_ns':time.perf_counter_ns()})
        self.write()

    def start_support(self):
        self.button('rt_scene_support_check').click()

    def await_support(self):
        status=self.widget.findChild(QtWidgets.QLabel,'rt_scene_support_status').text()
        text=self.widget.findChild(QtWidgets.QPlainTextEdit,'rt_scene_support_report').toPlainText()
        self.result['support_last_observation']={'status':status,'text':text,'time_ns':time.perf_counter_ns()}
        self.write()
        assert 'unavailable' not in status.lower(),(status,text)
        self.ready('Snapshot check complete' in status,'Waiting complete scene-support report: '+status)
        assert '/Unsupported/cylinder' in text and 'CylinderLight' in text,text
        assert 'Not assessed' not in text,text  # Fixture uses native mesh geometry, not analytic shapes.
        assert 'not a native translated-scene census' in text,text
        report=self.widget._support_scan.report
        finite=[finding for finding in report.findings if finding.feature=='Finite light geometry']
        assert {finding.path for finding in finite}=={'/Lights/rect','/Lights/disk','/Lights/sphere'},finite
        assert all(finding.kind=='Limitation' and 'lightgeocastsshadow' in finding.reason for finding in finite)
        profile=report.profile
        self.verify_file(profile.path)
        assert profile.version=='reliable-lighting-v1',profile
        assert report.viewer==self.viewer.name() and report.state=='complete'
        self.result['support_reports'].append({'status':status,'text':text,'profile_path':profile.path,
            'profile_sha256':self.sha(profile.path),'supported_light_paths':['/Lights/'+k for k in self.lights],
            'time_ns':time.perf_counter_ns()})
        self.report_before=text;self.write()

    def await_support_stale(self):
        status=self.widget.findChild(QtWidgets.QLabel,'rt_scene_support_status').text()
        self.ready('Outdated scene check' in status,'Waiting USD change invalidation: '+status)
        self.result['support_stale_after_native_light_edit']={'status':status,'time_ns':time.perf_counter_ns()}
        self.write()

    def light_change(self,label,changes):
        self.journal.pump();before=dict(self.journal.latest['request'])
        entry=time.perf_counter_ns()
        with hou.undos.group('HoudiniRT verification native light edit'):
            for kind,name,value in changes:self.set_native(self.lights[kind],name,value)
        self.output_node.cook(force=True)
        assert not self.output_node.errors(),self.output_node.errors()
        self.viewer.curViewport().draw();hou.ui.postRedrawFence()
        self.lighting_edit={'label':label,'changes':changes,'before_request':before,
                            'started_ns':entry,'driver_returned_ns':time.perf_counter_ns()}
        self.record_light_values(label)

    def await_light_image(self):
        self.ready(self.journal.current(),'Waiting latest native light image')
        present=self.journal.latest['present'];edit=self.lighting_edit
        self.ready(present['generation']>edit['before_request']['generation'],'Waiting edited light generation')
        self.ready(present['scene']!=edit['before_request']['scene'],
                   'Waiting light-edit content after any pending camera or sample-budget generation')
        first=next(e for e in self.journal.events if e['kind']=='present' and e.get('renderer')==self.renderer
                   and e.get('session')==present.get('session') and e['generation']==present['generation'])
        edit.update(presentation=present,first_presentation=first,
                    observed_ms=(first['observed_ns']-edit['started_ns'])/1e6)
        self.result.setdefault('light_publications',[]).append(edit)
        self.lighting_edit=None
        if self.other_original is not None:assert self.guides(self.other)==self.other_original
        self.write()

    def edit_light_while_paused(self):
        assert self.viewer.isRendererPaused()
        self.light_change('native rect exposure while paused',[('rect','exposure',0.5)])
        self.resume_edit=self.lighting_edit

    def await_paused_edit_request(self):
        self.ready(time.perf_counter_ns()-self.resume_edit['driver_returned_ns']>=500_000_000,'Waiting paused edit observation')
        self.journal.pump()
        assert self.viewer.isRendererPaused()
        self.result['paused_light_edit']={'edit':self.resume_edit,'request_while_paused':self.journal.latest.get('request'),
            'host_may_defer_execute_until_resume':True}
        self.write()

    def check_edited_pause_hold(self):
        self.ready(time.perf_counter_ns()-self.resume_edit['driver_returned_ns']>=2_000_000_000,'Holding edited paused scene')
        self.journal.pump();ack=self.active_hold['ack']
        assert self.viewer.isRendererPaused()
        later=[e for e in self.journal.events if e['sequence']>ack['sequence'] and
               e.get('renderer')==self.renderer and e['kind'] in ('batch','present')]
        assert not later,('GPU work continued during paused native light edit',later)
        self.result['paused_light_edit']['no_work_hold_confirmed_ns']=time.perf_counter_ns();self.write()

    def await_latest_light_resume(self):
        self.ready(not self.viewer.isRendererPaused(),'Waiting native resume')
        self.await_light_image()
        self.result['paused_light_edit']['resumed_latest']=self.journal.latest['present'];self.write()

    def change_native_exposure(self):
        self.light_change('native rect exposure invalidates support report',[('rect','exposure',1.0)])

    def reset_camera(self):
        self.fast_step(5)()

    def begin_lighting_images(self):
        super().begin_image_phase()
        self.output_node.cook(force=True)
        self.viewer.curViewport().draw();hou.ui.postRedrawFence()

    def solo_step(self,kind):
        def step():
            changes=[]
            for name in self.lights:
                power=(2.0 if kind=='distant' else 20.0) if name==kind else 0.0
                changes += [(name,'intensity',power),(name,'exposure',0.0)]
            self.light_change('native '+kind+' lighting',changes)
        step.__name__='native_solo_'+kind
        return step

    def exposure_step(self,value):
        def step():self.light_change('native rect exposure '+str(value),[('rect','exposure',float(value))])
        step.__name__='native_rect_exposure_'+str(value)
        return step

    def await_lighting_budget(self):
        self.ready(self.journal.current(),'Waiting current capture image')
        present=self.journal.latest['present']
        self.ready(present['samples']==256,'Waiting complete 256-sample capture budget')
        signature=(present.get('session'),present['generation'],present['scene'])
        if self.settle is None or self.settle[0]!=signature:self.settle=(signature,time.monotonic())
        self.ready(time.monotonic()-self.settle[1]>=0.5,'Waiting stable current image')

    def capture_step(self,name):
        def step():
            self.capture(name)
            path=self.output.parent/'images'/(name+'.usda')
            assert self.viewer.stage().Export(str(path))
            capture=self.images[name]
            capture.update(stage_path=str(path),stage_sha256=self.sha(path),camera=self.camera())
            self.record_light_values('captured '+name)
        step.__name__='capture_'+name
        return step

    def final_lighting_checks(self):
        self.observe('final original settings',True)
        assert self.viewer.currentHydraRenderer()==self.previous and self.viewer.isRendererPaused()==self.previous_paused
        assert self.other.currentHydraRenderer()=='Karma CPU' and not self.other.isRendererPaused()
        assert len([x for x in self.latencies if x['kind']=='camera'])==6
        assert set(self.images)=={'rect_base','distant_base','disk_base','sphere_base','rect_exposure0','rect_exposure1'}
        assert self.result.get('support_stale_after_native_light_edit')
        assert self.result['paused_light_edit'].get('resumed_latest')
        self.result['all_requested_workflow_checks']=True
        self.result['image_validation']='EXRs and exact displayed stages captured; independent image judge must assess them.'
        self.write()


if os.environ.get('RT_LIGHTING_AUTOSTART','1')=='1':
    hou.session._hrt_lighting_smoke=LightingSmoke()
    hou.session._hrt_lighting_smoke.write()
    QtCore.QTimer.singleShot(int(os.environ.get('RT_INSTALLED_START_DELAY_MS','1000')),
                            hou.session._hrt_lighting_smoke.run_next)
