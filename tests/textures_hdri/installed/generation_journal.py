"""Read native renderer publication logs; never substitute for image validation."""
from pathlib import Path
import re

SESSION = re.compile(r'render session=(\S+) (created|destroyed)')
REQUEST = re.compile(r'request renderer=(\S+) generation=(\d+) scene=(\d+)')
PRESENT = re.compile(r'present renderer=(\S+) generation=(\d+) scene=(\d+) samples=(\d+)')


def read_journal(path):
    result = {'sessions': [], 'active_sessions': [], 'requests': {},
              'presentations': {}, 'events': [], 'stale_presentations': []}
    if not Path(path).is_file():
        return result
    active = set()
    for number, line in enumerate(Path(path).read_text(encoding='utf-8-sig', errors='replace').splitlines(), 1):
        if not line.startswith('HoudiniRT:'):
            continue
        match = SESSION.search(line)
        if match:
            pointer, action = match.groups()
            pointer = pointer.lower()
            result['sessions'].append({'line': number, 'renderer': pointer, 'action': action})
            if action == 'created':
                active.add(pointer)
            else:
                active.discard(pointer)
            continue
        match = PRESENT.search(line)
        if match:
            pointer, generation, scene, samples = match.groups()
            item = {'line': number, 'renderer': pointer.lower(), 'generation': int(generation),
                    'scene': int(scene), 'samples': int(samples), 'kind': 'present'}
            request = result['requests'].get(item['renderer'])
            if request and item['generation'] != request['generation']:
                result['stale_presentations'].append(item)
            result['presentations'][item['renderer']] = item
            result['events'].append(item)
            continue
        match = REQUEST.search(line)
        if match:
            pointer, generation, scene = match.groups()
            item = {'line': number, 'renderer': pointer.lower(), 'generation': int(generation),
                    'scene': int(scene), 'kind': 'request'}
            result['requests'][item['renderer']] = item
            result['events'].append(item)
    result['active_sessions'] = sorted(active)
    return result


def current_image(journal, renderer, minimum_samples=1):
    request = journal['requests'].get(renderer)
    present = journal['presentations'].get(renderer)
    return bool(request and present
                and request['generation'] == present['generation']
                and request['scene'] == present['scene']
                and present['samples'] >= minimum_samples)
