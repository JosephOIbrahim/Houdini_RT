"""Opt-in regular-install camera repair acceptance in one fresh scratch HIP."""
import importlib.util,os,re,time
from pathlib import Path
import hou
from hutil.Qt import QtCore
ROOT=Path(os.environ['RT_LIVE_HARNESS_ROOT'])
os.environ['RT_ACCUM_AUTOSTART']='0'
spec=importlib.util.spec_from_file_location('camera_accept_base',ROOT/'gui_accumulation.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
class CameraRepairSmoke(base.AccumSmoke):
    def __init__(self):
        super().__init__();self.original=None;self.other_original=None;self.phase='fast';self.settle=None;self.initial_flags=None;self.expected_flags=None
        self.result.update(scope='Installed camera repair: actual current images, scoped guide lease, controls and restoration',guide_observations=[])
        self.result['harness_sha256']['gui_camera_repair.py']=self.sha(ROOT/'gui_camera_repair.py')
        self.steps=[self.setup,self.select_karma,self.pause_karma,self.check_karma,self.prepare_mixed,self.capture_original,
          self.open_panel,self.target_viewer,self.enable,self.check_active,self.assert_guard,
          self.active_pause,self.await_active_ack,self.check_active_hold,self.recreate_panel,self.target_viewer,
          self.check_recreated,self.active_resume,self.await_same_generation_resume,self.create_other,self.check_other,
          self.capture_other,self.disable,self.check_restored,self.assert_floor_restored,
          self.enable,self.check_active,self.assert_guard,self.manual_override,self.await_override,self.slow_before,self.await_camera,
          self.disable,self.check_restored,self.assert_floor_restored,self.prepare_mixed,self.enable,self.check_active,self.assert_guard]
        for n in range(20):self.steps.extend([self.fast_step(n),self.await_camera])
        self.steps.extend([self.begin_image_phase,self.await_capture_budget,lambda:self.capture('floor_hidden_camera_right'),
          self.move_image_camera,self.await_camera,self.await_capture_budget,lambda:self.capture('floor_hidden_camera_left'),
          self.manual_override,self.await_override,self.await_capture_budget,lambda:self.capture('floor_visible_camera_left'),
          self.slow_after,self.await_camera,self.disable,self.check_restored,self.assert_floor_restored,
          self.prepare_mixed,self.enable,self.check_active,self.assert_guard,self.close_panel_for_external,self.external_renderer,
          self.await_external_restore,self.open_panel,self.target_viewer,self.check_external_panel,
          self.restore_karma_pause,self.check_karma,self.restore_startup_flags,self.await_startup_flags,self.final_checks])
    def check_active(self):
        base.workflow.base.InstalledSmoke.check_active(self)
        self.native();self.journal.bind(self.renderer)
        self.ready(self.journal.current(),'Waiting actual current-generation image')
        present=self.journal.latest['present']
        if self.expected_edit_generation is not None:
            self.ready(present['generation']>self.expected_edit_generation,'Waiting edited image')
        if self.active_hold is None:
            self.ready(0<present['samples']<self.target_spp,'Initial pause proof requires unfinished accumulation')
    def native(self):
        data=base.workflow.journal.read_journal(self.log)
        assert not data['stale_presentations'],data['stale_presentations']
        live=[]
        for event in data['sessions']:
            if event['action']=='created':live.append((event['renderer'],event['line']))
            else:live=[entry for entry in live if entry[0]!=event['renderer']]
        if self.viewer.currentHydraRenderer()=='HoudiniRT':
            self.ready(len(live)==1,'Waiting exactly one live intended renderer')
            if getattr(self,'activation_identity',None)!=live[-1]:
                self.activation_identity=live[-1];self.renderer=live[-1][0]
                self.journal.pump();self.journal.bind(self.renderer);self.expected_edit_generation=None
        return data
    def guides(self,viewer):
        s=viewer.curViewport().settings();p=viewer.referencePlane()
        target=hou.ui.curDesktop().name()+'.'+viewer.name()+'.world'
        assert re.fullmatch(r'[\w.-]+',target)
        text,error=hou.hscript('viewrefplane '+target);assert not error,error
        ruler=re.search(r'(?:^|\s)-R\s+([012])(?:\s|$)',text);assert ruler,text
        return dict(reference_visible=p.isVisible(),reference_ruler=int(ruler[1]),
          reference_transform=list(p.transform().asTuple()),reference_cell_size=list(p.cellSize()),
          reference_line_spacing=list(p.numberOfCellsPerRulerLine()),ortho_ruler=str(s.orthoRuler()),
          ortho_grid_visible=s.displayOrthoGrid(),ortho_spacing=list(s.orthoGridSpacing()),
          ortho_offset=list(s.orthoGridOffset()),ortho_number_spacing=list(s.orthoGridRuler()))
    def camera(self):
        prim=self.node.stage().GetPrimAtPath('/Camera')
        result={'camera_path':self.viewer.curViewport().cameraPath()}
        for key in ('clippingRange','projection','horizontalAperture','verticalAperture','focalLength'):
            attr=prim.GetAttribute(key);value=attr.Get()
            result[key]={'value':list(value) if key=='clippingRange' else value,'authored':attr.HasAuthoredValueOpinion()}
        result['translation']=list(prim.GetAttribute('xformOp:translate').Get())
        return result
    def capture_original(self):
        self.original=self.guides(self.viewer);self.camera_original=self.camera()
        assert self.original['reference_visible'],'Fixture expects original visible reference floor'
        self.result.update(original_guides=self.original,original_camera=self.camera_original)
        self.observe('original',True)
    def viewport_states(self):
        return {v.name():{'type':str(v.type()),'ortho_visible':v.settings().displayOrthoGrid()} for v in self.viewer.viewports()}
    def prepare_mixed(self):
        if self.initial_flags is None:
            self.initial_flags=self.viewport_states();self.result['startup_viewports']=self.initial_flags
        right=next(v for v in self.viewer.viewports() if v.name()=='right1')
        right.settings().setDisplayOrthoGrid(False)
        self.expected_flags=self.viewport_states();assert not self.expected_flags['right1']['ortho_visible']
        assert self.expected_flags['persp1']['ortho_visible']
        self.result['mixed_fixture_viewports']=self.expected_flags;self.write()
    def restore_startup_flags(self):
        for v in self.viewer.viewports():v.settings().setDisplayOrthoGrid(self.initial_flags[v.name()]['ortho_visible'])
        self.expected_flags=dict(self.initial_flags);self.flags_restored_at=time.monotonic()
    def await_startup_flags(self):
        self.ready(time.monotonic()-self.flags_restored_at>=1,'Waiting original startup vector')
        assert self.viewport_states()==self.initial_flags
    def observe(self,label,visible):
        actual=self.guides(self.viewer);camera=self.camera()
        for key,value in self.original.items():
            if key!='reference_visible':assert actual[key]==value,(label,key,value,actual[key])
        assert actual['reference_visible']==visible,(label,actual)
        views=self.viewport_states();assert views==self.expected_flags,(label,views,self.expected_flags)
        for key,value in self.camera_original.items():
            if key!='translation':assert camera[key]==value,(label,key,value,camera[key])
        assert camera['translation']==[self.node.parm('camx').eval(),0,5]
        if self.other_original is not None:assert self.guides(self.other)==self.other_original
        self.result['guide_observations'].append(dict(label=label,guides=actual,viewports=views,camera=camera,time_ns=time.perf_counter_ns()))
        self.write()
    def assert_guard(self):
        self.ready(self.viewport_states()==self.expected_flags,'Waiting preserved per-viewport grid vector')
        self.observe('owned activation hides only reference floor',False)
        status=self.controller.status(self.viewer)
        self.result.setdefault('guard_statuses',[]).append(str(status));self.write()
    def capture_other(self):
        self.other_original=self.guides(self.other);self.result['other_original_guides']=self.other_original
    def manual_override(self):
        self.viewer.referencePlane().setIsVisible(True);self.override_started=time.monotonic()
        self.expected_flags={name:{**value,'ortho_visible':True} for name,value in self.expected_flags.items()}
    def await_override(self):
        self.ready(time.monotonic()-self.override_started>=1,'Waiting for manual override to survive controller polling')
        self.ready(self.viewport_states()==self.expected_flags,'Waiting native global-show broadcast')
        self.observe('manual floor visibility respected',True)
    def slow_before(self):
        self.phase='baseline_manual_override';self.action_step('camera',0)()
    def fast_step(self,n):
        def step():
            self.phase='repaired';self.action_step('camera',n+1)()
        return step
    def slow_after(self):
        self.phase='restored_manual_override';self.action_step('camera',0)()
    def await_camera(self):
        super().await_edit();self.latencies[-1]['phase']=self.phase
        self.observe('camera edit '+self.phase,self.phase!='repaired' and self.phase!='image_move')
    def move_image_camera(self):
        self.phase='image_move';self.action_step('camera',1)()
    def begin_image_phase(self):
        self.journal.pump();self.budget_before_generation=self.journal.latest['request']['generation']
        super().begin_image_phase();self.settle=None
    def await_capture_budget(self):
        self.ready(self.journal.current(),'Waiting current capture image')
        p=self.journal.latest['present']
        self.ready(p['samples']==256 and p['generation']>self.budget_before_generation,'Waiting completed 256-sample image')
        signature=(p['generation'],p['scene'],self.node.cookCount())
        if self.settle is None or self.settle[0]!=signature:self.settle=(signature,time.monotonic())
        self.ready(time.monotonic()-self.settle[1]>=1,'Waiting stable capture generation')
    def capture(self,name):
        self.journal.pump();self.expected_edit_generation=None
        return super().capture(name)
    def check_active_hold(self):
        self.journal.pump();elapsed=(time.perf_counter_ns()-self.active_hold['hold_started_ns'])/1e9
        self.ready(elapsed>=8,'Holding active-render pause for UI observation')
        assert self.viewer.isRendererPaused()
        ack=self.active_hold['ack']
        assert not [e for e in self.journal.events if e['sequence']>ack['sequence'] and
                    e.get('renderer')==self.renderer and e['kind'] in ('batch','present')]
        self.active_hold['confirmed_hold_seconds']=elapsed;self.observe('paused guide lease',False)
    def assert_floor_restored(self):self.observe('Off restores original floor',True)
    def close_panel_for_external(self):
        self.panel.close();self.panel=None;self.widget=None
        self.result['external_test']={'panel_closed_ns':time.perf_counter_ns()};self.write()
    def external_renderer(self):
        self.viewer.setHydraRenderer('Karma CPU');self.viewer.curViewport().draw();hou.ui.triggerUpdate()
    def await_external_restore(self):
        # Deliberately do not call controller.status: the independent session callback must restore it.
        self.ready(self.viewer.currentHydraRenderer()=='Karma CPU','Waiting external renderer')
        self.ready(self.viewer.referencePlane().isVisible(),'Waiting automatic restoration while panel closed')
        self.ready(self.viewport_states()==self.expected_flags,'Waiting mixed vector restoration with panel closed')
        self.observe('external renderer restores floor with panel closed',True)
        self.result['external_test']['restored_without_status_call_ns']=time.perf_counter_ns();self.write()
    def check_external_panel(self):
        status=self.record('recreated after external takeover')
        assert not status.owned and not status.busy and status.state=='off',status
        self.observe('recreated panel retains external restoration',True)
    def restore_karma_pause(self):self.viewer.setRendererPaused(self.previous_paused)
    def final_checks(self):
        self.observe('final original settings',True)
        assert self.viewer.currentHydraRenderer()==self.previous and self.viewer.isRendererPaused()==self.previous_paused
        assert len([x for x in self.latencies if x['phase']=='repaired'])==20
        assert len(self.images)==3
        self.result['all_requested_workflow_checks']=True
    def finish(self,passed):
        try:super().finish(passed)
        except Exception:
            import traceback
            self.result.update(status='failed',cleanup_error=traceback.format_exc());self.write()
            if self.safe_to_exit:hou.ui.postEventCallback(lambda:hou.hscript('quit -f 1'))
if os.environ.get('RT_CAMERA_AUTOSTART','1')=='1':
    hou.session._hrt_camera_repair=CameraRepairSmoke();hou.session._hrt_camera_repair.write()
    QtCore.QTimer.singleShot(int(os.environ.get('RT_INSTALLED_START_DELAY_MS','1000')),hou.session._hrt_camera_repair.run_next)
