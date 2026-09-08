"""Verify an installed wheel, isolated from the checkout and user settings.

Run with the project's Python: python tools/check-wheel.py /path/to/wheel.whl
Requires uv on PATH; uses no network and installs no dependencies.
"""

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="turbollm-wheel-") as directory:
        root = Path(directory)
        target = root / "site"
        subprocess.run([
            "uv", "--cache-dir", str(root / "cache"), "pip", "install",
            "--offline", "--no-deps", "--target", str(target), str(wheel),
        ], check=True)
        subprocess.run([
            sys.executable, "-I", "-c", """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import turbollm
from turbollm import registry
from turbollm.harnesses.omp_lean import LEAN_PROMPT_PATH
root = Path.cwd()
assert Path(turbollm.__file__).resolve().is_relative_to(root / 'site')
registry.CONFIG_DIR = root / 'user'
registry.USER_TOML = registry.CONFIG_DIR / 'models.toml'
data = registry.load_registry()
assert 'qwen38-27b-oq6e-mtp' in data['models']
assert 'omp-lean' in data['harnesses']
assert 'goose' not in data['harnesses']
assert registry.USER_TOML.is_file()
assert registry.BUNDLED_TOML.resolve().is_relative_to(root / 'site')
assert LEAN_PROMPT_PATH.resolve().is_relative_to(root / 'site')
assert 'local coding assistant' in LEAN_PROMPT_PATH.read_text()
registry.USER_TOML.write_text('[models.custom]\\nhf_repo = "org/custom"\\n')
assert list(registry.load_registry()['models']) == ['custom']
print('Installed wheel: registry bootstrap, custom-config preservation, and lean prompt passed.')
""", str(target),
        ], cwd=root, check=True)


if __name__ == "__main__":
    main()
