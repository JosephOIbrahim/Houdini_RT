"""Choose scratch outputs without overwriting accepted evidence or test sources."""
import argparse
from pathlib import Path


def output_path(artifact, require_empty=False):
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[3] / 'build/m7/native-texture-fixture')
    path = parser.parse_args().output.resolve()
    if (path / artifact).exists():
        parser.error(f'{artifact} already exists; choose a new --output directory')
    if require_empty and path.exists() and any(path.iterdir()):
        parser.error('Fixture generation requires an empty/new output directory')
    path.mkdir(parents=True, exist_ok=True)
    return path
