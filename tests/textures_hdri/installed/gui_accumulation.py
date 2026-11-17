"""Regular installed workflow with active GPU pause and measured publication latency."""
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import hou
from hutil.Qt import QtCore,QtWidgets

HARNESS=Path(os.environ['RT_LIVE_HARNESS_ROOT']).resolve()
def helper(name):
    spec=importlib.util.spec_from_file_location(name,HARNESS/(name+'.py'))
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
workflow=helper('live_workflow_base'); events=helper('event_journal')
Pending=workflow.base.Pending

class AccumSmoke(workflow.LiveSmoke):
    def __init__(self):
        super().__init__(); self.journal=events.EventJournal(self.log)
        self.active_hold=None; self.pending_edit=None; self.latencies=[]; self.target_spp=65536
        self.expected_edit_generation=None; self.last_pause_floor=0
        self.result.update(scope='Regular installed active pause, live pixels and publication timing',
                           latency_samples=self.latencies)
        self.result['harness_sha256'].update({name:self.sha(HARNESS/name) for name in
            ('gui_accumulation.py','event_journal.py')})
        timing=[]
        for kind in ('transform','color','camera'):
            for n in range(20):
                timing.extend((self.action_step(kind,n),self.await_edit))
        self.steps=[self.setup,self.select_karma,self.pause_karma,self.check_karma,self.open_panel,
            self.target_viewer,self.enable,self.check_active,self.active_pause,self.await_active_ack,
            self.check_active_hold,self.active_resume,self.await_same_generation_resume]
        self.steps+=timing+[self.begin_image_phase,self.reset_fixture,self.check_active,
            self.capture_baseline,self.rapid_burst,self.capture_burst,self.reset_fixture,self.check_active,
            self.edit_transform,self.capture_transform,self.edit_hide,self.capture_hide,
            self.edit_show,self.capture_show,self.edit_color,self.capture_color,self.edit_insert,self.capture_insert,
            self.edit_delete,self.capture_delete,self.pause,self.check_paused,self.begin_pause_hold,
            self.edit_while_paused,self.check_pause_hold,self.recreate_panel,self.target_viewer,self.check_recreated,
            self.resume,self.check_active,self.capture_resumed_edits,self.create_other,self.check_other,
            self.disable,self.check_restored]

    def setup(self):
        hou.qt.mainWindow().showNormal()
        super().setup()
        self.viewer.curViewport().setCamera('/Camera')
        self.result['camera_timing_path']='Bound USD /Camera xformOp:translate through Python LOP parameter'
        assert self.viewer.curViewport().cameraPath()=='/Camera'

    def create_scene(self):
        super().create_scene()
        code=self.node.parm('python').evalAsString().replace('.Set(1024)','.Set(65536)')
        group=self.node.parmTemplateGroup()
        group.append(hou.FloatParmTemplate('camx','Test camera translation',1,default_value=(0.0,)))
        self.node.setParmTemplateGroup(group)
        code=code.replace('Gf.Vec3d(0, 0, 5)',"Gf.Vec3d(node.evalParm('camx'), 0, 5)")
        w,h=self.viewer.curViewport().size()[2:]
        code += '\nsettings.CreateResolutionAttr(Gf.Vec2i(%d,%d))\n'%(w,h)
        self.result['test_render_resolution']=[w,h]
        self.node.parm('python').set(code)

    def button(self,name):
        button=self.widget.findChild(QtWidgets.QPushButton,name)
        self.ready(button is not None and button.isEnabled(),'Waiting for Qt control '+name)
        return button

    def check_active(self):
        workflow.base.InstalledSmoke.check_active(self)
        self.native(); self.journal.bind(self.renderer)
        self.ready(self.journal.current(),'Waiting for actual current-generation pixels')
        present=self.journal.latest['present']
        if self.expected_edit_generation is not None:
            self.ready(present['generation']>self.expected_edit_generation,'Waiting for edited image')
        if not getattr(self,'image_phase',False):
            self.ready(0<present['samples']<self.target_spp,'Render must still be accumulating')

    def pause(self):
        self.journal.pump()
        self.last_pause_floor=self.journal.latest.get('pause_request',{}).get('epoch',0)
        super().pause()

    def check_paused(self):
        super().check_paused(); self.journal.pump()
        self.ready(self.journal.latest.get('pause_ack',{}).get('epoch',0)>self.last_pause_floor,
                   'Waiting for pause boundary ACK')

    def active_pause(self):
        self.journal.pump(); p=self.journal.latest['present']
        assert 0<p['samples']<self.target_spp
        self.active_hold={'generation':p['generation'],'samples_at_click':p['samples'],
            'epoch_floor':self.journal.latest.get('pause_request',{}).get('epoch',0),
            'clicked_ns':time.perf_counter_ns()}
        self.pause()

    def await_active_ack(self):
        self.journal.pump(); status=self.controller.status(self.viewer)
        self.ready(status.state=='paused' and not status.busy,status)
        ack=self.journal.latest.get('pause_ack',{})
        self.ready(ack.get('epoch',0)>self.active_hold['epoch_floor'],'Waiting for native pause boundary ACK')
        assert ack['generation']==self.active_hold['generation']
        assert 0<ack['samples']<self.target_spp,ack
        self.active_hold.update(ack=ack,hold_started_ns=time.perf_counter_ns())
        self.result['active_pause']=self.active_hold; self.write()

    def check_active_hold(self):
        self.journal.pump(); elapsed=(time.perf_counter_ns()-self.active_hold['hold_started_ns'])/1e9
        self.deadline=time.perf_counter()+5
        self.ready(elapsed>=30.0,'Holding paused active render for independent UI observation')
        assert self.viewer.isRendererPaused()
        ack=self.active_hold['ack']
        later=[e for e in self.journal.events if e['sequence']>ack['sequence']
               and e.get('renderer')==self.renderer and e['kind'] in ('batch','present')]
        assert not later,('GPU work or publication continued after pause ACK',later)
        self.active_hold['confirmed_hold_seconds']=elapsed; self.write()

    def active_resume(self):
        self.resume(); self.active_hold['resumed_ns']=time.perf_counter_ns()

    def await_same_generation_resume(self):
        self.ready(not self.viewer.isRendererPaused(),'Waiting for native Resume')
        self.ready(self.journal.current(),'Waiting for resumed publication')
        p=self.journal.latest['present']; ack=self.active_hold['ack']
        assert p['generation']==ack['generation'],(ack,p)
        self.ready(p['samples']>ack['samples'],'Waiting for new samples in same generation')
        self.active_hold['resumed_presentation']=p; self.write()

    def action_step(self,kind,index):
        def action():
            self.journal.pump(); before=self.journal.latest['request']['generation']
            self.pending_edit={'kind':kind,'index':index,'before_generation':before,
                               'driver_action_entry_ns':time.perf_counter_ns(),'polls_ns':[]}
            if kind=='transform': self.edit(tx=.3 if index%2==0 else -.3)
            elif kind=='color': self.edit(blue=index%2==0)
            else:
                viewport=self.viewer.curViewport(); assert viewport.cameraPath()=='/Camera'
                self.pending_edit['camera_path']=viewport.cameraPath()
                self.pending_edit['before_scene']=self.journal.latest['request']['scene']
                begun=time.perf_counter_ns()
                attr=self.node.stage().GetPrimAtPath('/Camera').GetAttribute('xformOp:translate')
                self.pending_edit['camera_before']=list(attr.Get())
                self.pending_edit['pre_camera_read_ms']=(time.perf_counter_ns()-begun)/1e6
                self.edit(camx=.1 if index%2==0 else -.1)
            self.pending_edit['driver_action_exit_ns']=time.perf_counter_ns()
        action.__name__='edit_'+kind+'_'+str(index)
        return action

    def await_edit(self):
        edit=self.pending_edit; edit['polls_ns'].append(time.perf_counter_ns())
        self.ready(self.journal.current(),'Waiting for current image after edit')
        present=self.journal.latest['present']
        self.ready(present['generation']>edit['before_generation'],'Waiting for changed generation')
        first=next(e for e in self.journal.events if e['kind']=='present' and e.get('renderer')==self.renderer
                   and e['generation']==present['generation'] and e.get('session')==present.get('session'))
        assert first['observed_ns']>=edit['started_ns']
        poll_times=[edit['started_ns']]+edit['polls_ns']
        sample={'kind':edit['kind'],'index':edit['index'],'generation':first['generation'],
            'scene':first['scene'],'samples':first['samples'],
            'driver_edit_ns':edit['started_ns'],'publication_observed_ns':first['observed_ns'],
            'native_publication_time_s':first['time_s'],'polls_ns':edit['polls_ns'],
            'edit_to_publication_observed_ms':(first['observed_ns']-edit['started_ns'])/1e6,
            'native_request_to_first_publish_ms':first['first_image_ms'],
            'max_poll_gap_ms':max((b-a)/1e6 for a,b in zip(poll_times,poll_times[1:])),
            'trace_submits':first['trace_submits'],'resolve_submits':first['resolve_submits'],
            'readback_bytes':first['readback_bytes']}
        if edit['kind']=='camera':
            begun=time.perf_counter_ns()
            attr=self.node.stage().GetPrimAtPath('/Camera').GetAttribute('xformOp:translate')
            edit['camera_after']=list(attr.Get())
            edit['post_camera_read_ms']=(time.perf_counter_ns()-begun)/1e6
            assert edit['camera_after']!=edit['camera_before']
            assert first['scene']==edit['before_scene'],'Camera edits must leave geometry content unchanged'
            sample.update({k:edit[k] for k in ('camera_path','before_scene','camera_before','camera_after','pre_camera_read_ms','post_camera_read_ms')})
        sample.update({k:edit[k] for k in ('driver_action_entry_ns','driver_action_exit_ns','call_costs_ms')})
        sample['post_action_to_first_poll_ms']=(edit['polls_ns'][0]-edit['driver_action_exit_ns'])/1e6
        self.latencies.append(sample); self.pending_edit=None; self.write()

    def reset_fixture(self):
        self.edit(tx=0.0,blue=False,hidden=False,insert=False,camx=0.0)
        viewport=self.viewer.curViewport(); viewport.setCamera('/Camera')
        assert viewport.cameraPath()=='/Camera'
        prim=self.node.stage().GetPrimAtPath('/Camera')
        assert list(prim.GetAttribute('xformOp:translate').Get())==[0,0,5]
        assert str(prim.GetAttribute('projection').Get())=='orthographic'
        assert float(prim.GetAttribute('horizontalAperture').Get())==60.0
        self.result['spatial_capture_camera']={'path':'/Camera','translation':[0,0,5],
            'projection':'orthographic','horizontal_aperture':60.0}
        viewport.draw(); hou.ui.postRedrawFence()

    def begin_image_phase(self):
        self.image_phase=True; self.target_spp=256
        code=self.node.parm('python').evalAsString().replace('.Set(65536)','.Set(256)')
        self.node.parm('python').set(code); self.node.cook(force=True)
        self.result['image_phase_budget']=256
        self.write()

    def edit(self,**values):
        self.journal.pump(); self.expected_edit_generation=self.journal.latest['request']['generation']
        costs={}
        def timed(name,callback):
            start=time.perf_counter_ns(); answer=callback()
            costs[name]=(time.perf_counter_ns()-start)/1e6; return answer
        before=timed('native_log_read',self.native)
        started=time.perf_counter_ns()
        timed('parameter_set',lambda:[self.node.parm(name).set(value) for name,value in values.items()])
        timed('cook',lambda:self.node.cook(force=True))
        assert not timed('node_errors',self.node.errors)
        timed('viewport_draw',self.viewer.curViewport().draw)
        timed('post_redraw_fence',hou.ui.postRedrawFence)
        self.result.setdefault('edits',[]).append({'values':values,'elapsed_seconds':time.monotonic()-self.started,
            'request_before':before['requests'].get(self.renderer),'actual_edit_ns':started,'call_costs_ms':costs})
        timed('write_result',self.write)
        if self.pending_edit is not None:
            self.pending_edit.update(started_ns=started,call_costs_ms=costs)

    def rapid_burst(self):
        self.result['rapid_burst']={'started_ns':time.perf_counter_ns(),
            'values':[.1,.2,-.4,.5,.7],'awaited_intermediate_images':False}
        for value in self.result['rapid_burst']['values']: self.edit(tx=value)
        self.result['rapid_burst']['completed_ns']=time.perf_counter_ns(); self.write()

    def capture_burst(self):
        image=self.capture('burst_final')
        workflow.metrics.assert_shift(self.images['baseline']['metrics'],image,'red',.7)
        workflow.metrics.assert_stationary(self.images['baseline']['metrics'],image,'green')

    def begin_pause_hold(self):
        super().begin_pause_hold(); self.journal.pump()
        self.pause_hold['native_ack']=self.journal.latest['pause_ack']; self.write()

    def check_pause_hold(self):
        super().check_pause_hold(); self.journal.pump()
        ack=self.pause_hold['native_ack']
        later=[e for e in self.journal.events if e['sequence']>ack['sequence']
               and e.get('renderer')==self.renderer and e['kind'] in ('batch','present')]
        assert not later,('Work continued during paused edits',later)

    def capture(self,name):
        self.ready(self.journal.current(),'Waiting for current pixels before capture')
        if self.expected_edit_generation is not None:
            self.ready(self.journal.latest['present']['generation']>self.expected_edit_generation,
                       'Waiting for captured edit to reach current pixels')
        self.ready(self.journal.latest['present']['samples']==256,'Waiting for explicit image-phase 256-sample completion')
        native=self.native(); assert not self.viewer.isRendererPaused()
        path=self.output.parent/'images'/(name+'.exr')
        if name not in self.capture_pending:
            path.parent.mkdir(parents=True,exist_ok=True)
            self.capture_pending[name]={'request_before':native['requests'][self.renderer],
                                        'present_before':native['presentations'][self.renderer]}
            options=self.viewer.flipbookSettings().stash()
            options.frameRange((hou.frame(),hou.frame())); options.outputToMPlay(False)
            options.output(path.as_posix()); options.useResolution(False)
            options.beautyPassOnly(True); options.renderAllViewports(False)
            options.setUseFrameTimeLimit(True); options.setFrameTimeLimit(.25)
            options.setUseFrameProgressLimit(True); options.setFrameProgessLimit(100.0)
            self.viewer.flipbook(self.viewer.curViewport(),options,open_dialog=False)
        self.ready(path.is_file() and path.stat().st_size>0,'Waiting for viewport EXR')
        after=self.native(); capture=dict(self.capture_pending[name])
        capture.update(path=str(path),sha256=self.sha(path),metrics=workflow.metrics.inspect_image(path),
            request_after=after['requests'][self.renderer],present_after=after['presentations'][self.renderer])
        self.images[name]=capture; self.write()
        assert not self.viewer.isRendererPaused()
        assert capture['request_after']['generation']==capture['request_before']['generation']
        return capture['metrics']

    def run_next(self):
        if self.index>=len(self.steps): self.finish(True); return
        step=self.steps[self.index]
        if self.deadline is None: self.deadline=time.perf_counter()+30.0
        try: step()
        except Pending as error:
            if time.perf_counter()<self.deadline:
                QtCore.QTimer.singleShot(10,self.run_next); return
            self.result.update(failed_step=step.__name__,error='Readback timeout: '+str(error))
            self.finish(False); return
        except Exception:
            self.result.update(failed_step=step.__name__,error=traceback.format_exc())
            self.finish(False); return
        print('RT_ACCUM_SMOKE %.3fs %s'%(time.monotonic()-self.started,step.__name__),file=sys.stderr,flush=True)
        self.index+=1; self.deadline=None
        delay=1000 if step in (self.setup,self.select_karma,self.open_panel,self.recreate_panel,self.create_other) else 20
        QtCore.QTimer.singleShot(delay,self.run_next)

    def finish(self,passed):
        self.journal.pump(); self.result['native_work_events']=self.journal.events
        summary={}
        for kind in ('transform','color','camera'):
            group=[x for x in self.latencies if x['kind']==kind]
            summary[kind]={'count':len(group)}
            for field in ('edit_to_publication_observed_ms','native_request_to_first_publish_ms','max_poll_gap_ms'):
                values=sorted(x[field] for x in group)
                if values: summary[kind][field]={'p50':values[math.ceil(len(values)*.50)-1],
                                                  'p95':values[math.ceil(len(values)*.95)-1]}
        self.result['latency_summary']=summary
        self.result['latency_scope']='Edit marker to native current AOV publication observed through log polling; includes polling delay, not compositor/screen latency.'
        super().finish(passed)

if os.environ.get('RT_ACCUM_AUTOSTART','1')=='1':
    hou.session._hrt_accum_smoke=AccumSmoke()
    hou.session._hrt_accum_smoke.write()
    QtCore.QTimer.singleShot(int(os.environ.get('RT_INSTALLED_START_DELAY_MS','45000')),
                            hou.session._hrt_accum_smoke.run_next)
