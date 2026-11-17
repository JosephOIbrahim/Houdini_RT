"""Independent spatial/color observations for the live GUI fixture EXRs."""
import OpenImageIO as oiio
import numpy as np


def inspect_image(path):
    image = oiio.ImageInput.open(str(path))
    assert image is not None, 'Could not read image: ' + str(path)
    spec = image.spec()
    pixels = np.asarray(image.read_image('float'), dtype=np.float64).reshape(
        spec.height, spec.width, spec.nchannels)
    image.close()
    assert pixels is not None and pixels.size > 0, str(path)
    channels = list(spec.channelnames)
    rgb_indices = [channels.index(c) for c in ('R', 'G', 'B')]
    rgb = pixels[..., rgb_indices]
    assert np.isfinite(rgb).all(), 'Non-finite image pixels'
    alpha = pixels[..., channels.index('A')] if 'A' in channels else np.ones(rgb.shape[:2])
    r, g, b = (rgb[..., n] for n in range(3))
    covered = alpha > 0.5
    masks = {
        'red': covered & (r > 0.02) & (r > 2*g) & (r > 2*b),
        'green': covered & (g > 0.02) & (g > 2*r) & (g > 2*b),
        'blue': covered & (b > 0.02) & (b > 2*r) & (b > 2*g),
        'magenta': covered & (r > 0.02) & (b > 0.02) & (np.minimum(r,b) > 2*g)
    }
    result = {'width': int(spec.width), 'height': int(spec.height),
              'channels': channels, 'covered_pixels': int(covered.sum()), 'regions': {}}
    for name, mask in masks.items():
        y, x = np.nonzero(mask)
        result['regions'][name] = {'pixels': int(mask.sum()),
            'center_x': float(x.mean()) if len(x) else None,
            'center_y': float(y.mean()) if len(y) else None,
            'mean_rgb': rgb[mask].mean(axis=0).tolist() if len(x) else None}
    return result


def assert_region_present(metrics, region, minimum=150):
    assert metrics['regions'][region]['pixels'] >= minimum, (region, metrics)


def assert_region_absent(metrics, region, maximum=8):
    assert metrics['regions'][region]['pixels'] <= maximum, (region, metrics)


def assert_shift(before, after, region, shift_world, ortho_width=6.0):
    assert_region_present(before, region)
    assert_region_present(after, region)
    expected = before['width'] * shift_world / ortho_width
    actual = after['regions'][region]['center_x'] - before['regions'][region]['center_x']
    assert abs(actual-expected) < max(3.0, before['width']*0.02), (actual, expected)


def assert_stationary(before, after, region):
    assert_region_present(before, region)
    assert_region_present(after, region)
    for axis in ('center_x', 'center_y'):
        assert abs(before['regions'][region][axis] - after['regions'][region][axis]) < 3.0
