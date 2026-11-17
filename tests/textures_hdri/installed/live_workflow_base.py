"""Normal installed Phase2A GUI validation; launch only after candidate gates pass."""
import importlib.util
import os
from pathlib import Path
import time
import hou
from hutil.Qt import QtCore, QtWidgets

HARNESS = Path(os.environ['RT_LIVE_HARNESS_ROOT']).resolve()


def load_helper(name):
    spec = importlib.util.spec_from_file_location(name, HARNESS/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_helper('installed_workflow_base')
journal = load_helper('generation_journal')
metrics = load_helper('image_metrics')


class LiveSmoke(base.InstalledSmoke):
    def __init__(self):
        super().__init__()
        self.log = Path(os.environ['RT_LIVE_DEBUG_LOG'])
        self.renderer = None
        self.images = {}
        self.capture_pending = {}
        self.pause_hold = None
        self.result.update(scope='Regular installed GUI live edits: native publications and independent EXR spatial/color checks',
                           image_validation='Pending actual viewport EXR captures', images=self.images)
        self.result['harness_sha256'] = {p.name: self.sha(p) for p in
            (HARNESS/'live_workflow_base.py', HARNESS/'installed_workflow_base.py',
             HARNESS/'generation_journal.py', HARNESS/'image_metrics.py', HARNESS/'live_fixture_lop.py')}
        self.steps = [self.setup, self.select_karma, self.pause_karma, self.check_karma,
            self.open_panel, self.target_viewer, self.enable, self.check_active,
            self.capture_baseline, self.edit_transform, self.capture_transform,
            self.edit_hide, self.capture_hide, self.edit_show, self.capture_show,
            self.edit_color, self.capture_color, self.edit_insert, self.capture_insert,
            self.edit_delete, self.capture_delete, self.pause, self.check_paused,
            self.begin_pause_hold, self.edit_while_paused, self.check_pause_hold,
            self.recreate_panel, self.target_viewer, self.check_recreated,
            self.resume, self.check_active, self.capture_resumed_edits,
            self.create_other, self.check_other, self.disable, self.check_restored]

    def setup(self):
        assert os.environ.get('RT_LIVE_VERIFY') == '1'
        super().setup()
        viewport = self.viewer.curViewport()
        camera = viewport.defaultCamera().stash()
        camera.setPerspective(False)
        camera.setOrthoWidth(6.0)
        camera.setRotation(hou.Matrix3(1))
        camera.setTranslation((0, 0, 5))
        camera.setPivot((0, 0, 0))
        viewport.setDefaultCamera(camera)
        self.result['camera'] = {'orthographic_width': 6.0, 'translation': [0, 0, 5]}

    def create_scene(self):
        self.node = hou.node('/stage').createNode('pythonscript', 'hrt_live_fixture')
        group = self.node.parmTemplateGroup()
        for template in (hou.FloatParmTemplate('tx', 'Test translation', 1, default_value=(0.0,)),
            hou.ToggleParmTemplate('hidden', 'Test hidden'), hou.ToggleParmTemplate('blue', 'Test blue'),
            hou.ToggleParmTemplate('insert', 'Test insertion'),
            hou.FloatParmTemplate('aspect', 'Test aspect', 1, default_value=(1.0,))):
            group.append(template)
        self.node.setParmTemplateGroup(group)
        geometry = self.viewer.curViewport().size()
        self.node.parm('aspect').set(geometry[2] / geometry[3])
        code = self.node.parm('python')
        assert code is not None, 'Inspect Python LOP code parameter before proceeding'
        code.set((HARNESS/'live_fixture_lop.py').read_text(encoding='utf-8'))
        self.node.setDisplayFlag(True)

    def native(self):
        data = journal.read_journal(self.log)
        created = [s for s in data['sessions'] if s['action'] == 'created']
        if self.renderer is None:
            self.ready(len(created) == 1, 'Waiting for exactly one renderer session')
            self.renderer = created[0]['renderer']
        assert len(created) == 1 and created[0]['renderer'] == self.renderer, data['sessions']
        assert not data['stale_presentations'], data['stale_presentations']
        return data

    def check_active(self):
        super().check_active()
        native = self.native()
        self.ready(journal.current_image(native, self.renderer, 1024), native['presentations'])

    def record(self, name):
        status = super().record(name)
        if self.renderer is not None:
            native = self.native()
            self.records[-1]['native_request'] = native['requests'].get(self.renderer)
            self.records[-1]['native_presentation'] = native['presentations'].get(self.renderer)
            self.write()
        return status

    def capture(self, name):
        native = self.native()
        self.ready(journal.current_image(native, self.renderer, 1024), native['presentations'])
        assert not self.viewer.isRendererPaused(), 'Image exports are only made while unpaused'
        path = self.output.parent/'images'/(name+'.exr')
        if name not in self.capture_pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.capture_pending[name] = {'request_before': native['requests'][self.renderer],
                                          'present_before': native['presentations'][self.renderer]}
            options = self.viewer.flipbookSettings().stash()
            options.frameRange((hou.frame(), hou.frame()))
            options.outputToMPlay(False)
            options.output(path.as_posix())
            options.useResolution(False)
            options.beautyPassOnly(True)
            options.renderAllViewports(False)
            options.setUseFrameTimeLimit(True)
            options.setFrameTimeLimit(10.0)
            options.setUseFrameProgressLimit(True)
            options.setFrameProgessLimit(100.0)
            self.viewer.flipbook(self.viewer.curViewport(), options, open_dialog=False)
        self.ready(path.is_file() and path.stat().st_size > 0, 'Waiting for EXR capture')
        after = self.native()
        capture = dict(self.capture_pending[name])
        capture.update(path=str(path), sha256=self.sha(path), metrics=metrics.inspect_image(path),
                       request_after=after['requests'][self.renderer],
                       present_after=after['presentations'][self.renderer])
        self.images[name] = capture
        self.write()
        assert not self.viewer.isRendererPaused(), 'Flipbook changed the native pause flag'
        assert capture['request_after']['generation'] == capture['request_before']['generation'], (
            'Capture changed renderer generation; investigate capture semantics before trusting pixel proof', capture)
        return capture['metrics']

    def edit(self, **values):
        before = self.native()
        for name, value in values.items():
            self.node.parm(name).set(value)
        self.node.cook(force=True)
        assert not self.node.errors(), self.node.errors()
        self.viewer.curViewport().draw()
        hou.ui.postRedrawFence()
        self.result.setdefault('edits', []).append({'values': values,
            'elapsed_seconds': time.monotonic()-self.started,
            'request_before': before['requests'].get(self.renderer)})
        self.write()

    def capture_baseline(self):
        image = self.capture('baseline')
        metrics.assert_region_present(image, 'red')
        metrics.assert_region_present(image, 'green')
        metrics.assert_region_absent(image, 'blue')
        metrics.assert_region_absent(image, 'magenta')

    def edit_transform(self):
        self.edit(tx=0.7)

    def capture_transform(self):
        image = self.capture('transform')
        baseline = self.images['baseline']['metrics']
        metrics.assert_shift(baseline, image, 'red', 0.7)
        metrics.assert_stationary(baseline, image, 'green')

    def edit_hide(self):
        self.edit(hidden=True)

    def capture_hide(self):
        image = self.capture('hidden')
        metrics.assert_region_absent(image, 'red')
        metrics.assert_region_present(image, 'green')

    def edit_show(self):
        self.edit(hidden=False)

    def capture_show(self):
        image = self.capture('shown')
        metrics.assert_stationary(self.images['transform']['metrics'], image, 'red')

    def edit_color(self):
        self.edit(blue=True)

    def capture_color(self):
        image = self.capture('color')
        metrics.assert_region_present(image, 'blue')
        metrics.assert_region_absent(image, 'green')

    def edit_insert(self):
        self.edit(insert=True)

    def capture_insert(self):
        metrics.assert_region_present(self.capture('inserted'), 'magenta')

    def edit_delete(self):
        self.edit(insert=False)

    def capture_delete(self):
        metrics.assert_region_absent(self.capture('deleted'), 'magenta')

    def begin_pause_hold(self):
        native = self.native()
        assert self.viewer.isRendererPaused()
        self.pause_hold = {'time': time.monotonic(),
            'presentation': native['presentations'].get(self.renderer),
            'present_event_count': sum(e['kind'] == 'present' for e in native['events'])}
        self.result['pause_hold'] = self.pause_hold
        self.write()

    def edit_while_paused(self):
        assert self.viewer.isRendererPaused()
        self.edit(tx=-0.45, blue=False, insert=True)

    def check_pause_hold(self):
        self.ready(time.monotonic()-self.pause_hold['time'] >= 2.0, 'Waiting for paused edit dwell')
        native = self.native()
        assert self.viewer.isRendererPaused()
        count = sum(e['kind'] == 'present' for e in native['events'])
        assert count == self.pause_hold['present_event_count'], (self.pause_hold, native)
        assert native['presentations'].get(self.renderer) == self.pause_hold['presentation']
        self.result['pause_hold']['confirmed_seconds'] = time.monotonic()-self.pause_hold['time']
        self.result['pause_hold']['request_after_edits'] = native['requests'].get(self.renderer)
        self.write()

    def capture_resumed_edits(self):
        image = self.capture('resumed_edits')
        metrics.assert_shift(self.images['baseline']['metrics'], image, 'red', -0.45)
        metrics.assert_region_present(image, 'green')
        metrics.assert_region_absent(image, 'blue')
        metrics.assert_region_present(image, 'magenta')

    def finish(self, passed):
        self.result['image_validation'] = ('passed' if passed else 'incomplete or failed')
        self.result['native_journal'] = journal.read_journal(self.log)
        self.result['image_files_count'] = len(self.images)
        super().finish(passed)


