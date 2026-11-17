"""One immutable stage, one fresh normal Houdini process, one host flipbook."""
import importlib.util
import json
import os
from pathlib import Path
import time
import traceback
import hou
from hutil.Qt import QtCore
from pxr import Usd

HELPERS=Path(os.environ['RT_LIVE_HARNESS_ROOT'])
def load(name):
    spec=importlib.util.spec_from_file_location('fresh_'+name,HELPERS/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
base=load('installed_workflow_base');journal_module=load('event_journal')
generation=load('generation_journal')


class FreshStage(base.InstalledSmoke):
    def __init__(self):
        super().__init__()
        self.job_path=Path(os.environ['RT_FRESH_GUI_JOB']);self.job=json.loads(self.job_path.read_text())
        self.capture_data=self.job['capture'];self.display_before=None;self.capture_before=None
        self.journal=journal_module.EventJournal(os.environ['RT_LIVE_DEBUG_LOG'])
        self.result.update(scope='Fresh normal-H22 delegate and same-host flipbook of immutable exported USD',
            reference_kind='fresh_normal_houdini_gui_flipbook',raw_husk_equivalence=False,
            job=str(self.job_path),job_sha256=self.sha(self.job_path),case=self.job['name'],
            original_result=self.job['original_result'],original_result_sha256=self.job['original_result_sha256'])
        self.steps=[self.setup,self.select_karma,self.pause_karma,self.check_karma,
            self.enable,self.await_current,self.capture,self.restore_display,
            self.disable,self.await_restored,self.verify_final]

    def create_scene(self):
        stage=Path(self.capture_data['stage_path'])
        assert self.sha(stage)==self.capture_data['stage_sha256']
        self.node=hou.node('/stage').createNode('sublayer','hrt_fresh_exported_stage')
        self.node.parm('filepath1').set(str(stage));self.node.setDisplayFlag(True);self.node.cook(force=True)
        assert not self.node.errors(),self.node.errors()
        self.result.update(stage_path=str(stage),stage_sha256=self.sha(stage))

    @staticmethod
    def stage_values(stage):
        values={}
        for prim in stage.Traverse():
            # Houdini inserts its own non-rendering layer bookkeeping prim.
            if str(prim.GetPath()).startswith('/HoudiniLayerInfo'):continue
            values[str(prim.GetPath())]=dict(type=prim.GetTypeName(),
                attributes={p.GetName():dict(value=str(p.Get()),type=str(p.GetTypeName()),
                    connections=list(map(str,p.GetConnections())),color_space=str(p.GetMetadata('colorSpace')))
                    for p in prim.GetAttributes()},
                relationships={p.GetName():list(map(str,p.GetTargets())) for p in prim.GetRelationships()})
        return values

    def display(self):
        return dict(using_ocio=self.viewer.usingOCIO(),display=self.viewer.getOCIODisplay(),
                    view=self.viewer.getOCIOView(),lights=self.viewer.showLights(),
                    selection=self.viewer.showSelection(),object_origins=self.viewer.curViewport().settings().objectOrigins())

    def setup(self):
        hou.qt.mainWindow().showNormal();super().setup()
        self.viewer.curViewport().setCamera(self.capture_data['active_camera'])
        self.display_before=self.display();wanted=self.job['color_management']
        self.viewer.setOCIODisplayView(wanted['display'],wanted['view'])
        self.viewer.setUsingOCIO(wanted['using_ocio']);controls=self.job['display_controls']
        self.viewer.setShowLights(controls['lights']);self.viewer.setShowSelection(controls['selection'])
        self.viewer.curViewport().settings().setObjectOrigins(controls['object_origins'])
        current=self.display()
        assert all(current[k]==v for k,v in wanted.items()),(current,wanted)
        assert all(current[k]==v for k,v in controls.items()),(current,controls)
        self.result['display_override']=dict(before=self.display_before,during=current,
            scope='Exact original capture OCIO/light/selection/origin display controls; no image adjustment')
        size=list(self.viewer.curViewport().size());assert size[2:]==[626,647],size
        assert size==self.capture_data['viewport_size'],(size,self.capture_data['viewport_size'])
        self.result['actual_viewport_size']=size
        import PyOpenColorIO as ocio
        native_config=Path(hou.Color.ocio_configPath()).resolve()
        color_context=dict(path=str(native_config),sha256=self.sha(native_config),
            scene_linear=ocio.GetCurrentConfig().getColorSpace('scene_linear').getName(),
            cache_id=ocio.GetCurrentConfig().getCacheID())
        assert color_context==self.capture_data['native_color_context'],color_context
        self.result['native_color_context']=color_context
        stage=self.viewer.stage();source=Usd.Stage.Open(self.capture_data['stage_path'])
        actual=self.stage_values(stage);expected=self.stage_values(source)
        assert actual==expected,'Displayed composition differs from immutable exported stage'
        self.result['composed_stage_values']=actual
        settings=stage.GetPrimAtPath('/Render/settings')
        effective={k:settings.GetAttribute('houdinirt:'+k).Get() for k in ('spp','bounces','seed')}
        assert effective==self.capture_data['effective_render_settings']==dict(spp=256,bounces=4,seed=101)
        assert list(settings.GetAttribute('resolution').Get())==[626,647]
        assert list(map(str,settings.GetRelationship('camera').GetTargets()))==['/Camera']
        assert self.viewer.curViewport().cameraPath()=='/Camera'
        self.result['effective_render_settings']=effective
        self.result['child_config']=json.loads(Path(os.environ['HOUDINIRT_S3_CONFIG']).read_text())
        assert self.result['child_config']==self.capture_data['child_config']
        self.write()

    def enable(self):
        status=self.controller.activate(self.viewer);assert not status.error,status
        self.viewer.curViewport().draw();hou.ui.triggerUpdate();hou.ui.postRedrawFence()
        self.settle=None

    def await_current(self):
        status=self.controller.status(self.viewer)
        self.ready(status.state=='active' and not status.busy,status)
        assert status.owned and not status.error,status
        self.verify_loaded_delegate()
        native=generation.read_journal(os.environ['RT_LIVE_DEBUG_LOG'])
        created=[e for e in native['sessions'] if e['action']=='created']
        self.ready(len(created)==1 and len(native['active_sessions'])==1,'Waiting one fresh delegate')
        assert not native['stale_presentations']
        self.renderer=native['active_sessions'][0];self.journal.pump();self.journal.bind(self.renderer)
        self.ready(self.journal.current(),'Waiting current fresh image')
        current=self.journal.latest['present'];self.ready(current['samples']==256,'Waiting256 samples')
        signature=(current['session'],current['generation'],current['scene'])
        if self.settle is None or self.settle[0]!=signature:self.settle=(signature,time.monotonic())
        self.ready(time.monotonic()-self.settle[1]>=.5,'Waiting stable complete image')
        self.result['fresh_native_session']=created[0];self.write()

    def capture(self):
        self.await_current();self.journal.pump()
        path=Path(self.job['image'])
        if self.capture_before is None:
            self.capture_before=dict(request=dict(self.journal.latest['request']),present=dict(self.journal.latest['present']))
            options=self.viewer.flipbookSettings().stash()
            self.result['flipbook_inherited']=dict(antialias=str(options.antialias()),
                override_gamma=options.overrideGamma(),gamma=options.gamma(),override_lut=options.overrideLUT(),
                background=options.backgroundImage(),crop_mask=options.cropOutMaskOverlay())
            options.frameRange((hou.frame(),hou.frame()));options.outputToMPlay(False)
            options.output(path.as_posix());options.useResolution(False)
            options.beautyPassOnly(True);options.renderAllViewports(False)
            options.setUseFrameTimeLimit(True);options.setFrameTimeLimit(.25)
            options.setUseFrameProgressLimit(True);options.setFrameProgessLimit(100.0)
            self.result['flipbook_applied']=dict(beauty_only=options.beautyPassOnly(),use_resolution=options.useResolution(),
                frame_time_limit=.25,frame_progress_limit=100.,all_viewports=options.renderAllViewports())
            self.write();self.viewer.flipbook(self.viewer.curViewport(),options,open_dialog=False)
        self.ready(path.is_file() and path.stat().st_size>0,'Waiting fresh viewport EXR')
        self.journal.pump();assert self.journal.current()
        records=[self.capture_before['request'],self.capture_before['present'],
                 self.journal.latest['request'],self.journal.latest['present']]
        assert len({(r['renderer'],r['session'],r['generation'],r['scene']) for r in records})==1
        assert records[-1]['samples']==256
        self.result['capture']=dict(path=str(path),sha256=self.sha(path),before=self.capture_before,
            request_after=records[-2],present_after=records[-1],display=self.display(),
            camera=self.viewer.curViewport().cameraPath(),viewport_size=list(self.viewer.curViewport().size()))
        assert self.sha(Path(self.capture_data['stage_path']))==self.capture_data['stage_sha256']
        self.write()

    def restore_display(self):
        if self.display_before is None:return
        prior=self.display_before
        self.viewer.setOCIODisplayView(prior['display'],prior['view']);self.viewer.setUsingOCIO(prior['using_ocio'])
        self.viewer.setShowLights(prior['lights']);self.viewer.setShowSelection(prior['selection'])
        self.viewer.curViewport().settings().setObjectOrigins(prior['object_origins'])
        assert self.display()==prior
        self.result['display_override']['restored']=self.display();self.display_before=None;self.write()

    def disable(self):
        status=self.controller.deactivate(self.viewer);assert not status.error,status

    def await_restored(self):
        status=self.controller.status(self.viewer)
        self.ready(status.state=='off' and not status.busy and not status.can_restore,status)
        assert self.viewer.currentHydraRenderer()==self.previous
        assert self.viewer.isRendererPaused()==self.previous_paused
        assert not status.error,status

    def verify_final(self):
        self.result['restored_renderer']=self.viewer.currentHydraRenderer()
        self.result['restored_paused']=self.viewer.isRendererPaused()
        self.result['native_work_events']=self.journal.events
        self.result['reference_completed']=True;self.write()

    def run_next(self):
        if self.index>=len(self.steps):self.finish(True);return
        step=self.steps[self.index]
        if self.deadline is None:self.deadline=time.monotonic()+30
        try:step()
        except base.Pending as error:
            if time.monotonic()<self.deadline:QtCore.QTimer.singleShot(50,self.run_next);return
            self.result.update(failed_step=step.__name__,error='Readback timeout: '+str(error));self.finish(False);return
        except Exception:
            self.result.update(failed_step=step.__name__,error=traceback.format_exc());self.finish(False);return
        self.index+=1;self.deadline=None
        QtCore.QTimer.singleShot(1000 if step in (self.setup,self.select_karma) else 50,self.run_next)

    def finish(self,passed):
        try:self.restore_display()
        except Exception:self.result['display_restore_error']=traceback.format_exc();passed=False
        self.result['harness_files']={str(p):self.sha(p) for p in HELPERS.iterdir() if p.is_file()}
        super().finish(passed)


hou.session._hrt_fresh_stage=FreshStage();hou.session._hrt_fresh_stage.write()
QtCore.QTimer.singleShot(1000,hou.session._hrt_fresh_stage.run_next)
