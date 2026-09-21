"""Install one built wheel in a clean environment and exercise its public API."""

import argparse
import os
from pathlib import Path
import subprocess
import tempfile
import venv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dist", type=Path, nargs="?", default=Path("dist"))
    args = parser.parse_args()
    wheels = list(args.dist.glob("*.whl"))
    if len(wheels) != 1:
        parser.error(f"Expected exactly one wheel in {args.dist}, found {len(wheels)}")
    wheel = wheels[0].resolve()
    suite = Path(__file__).resolve().parents[1] / "tests" / "test_starlette_compatibility.py"

    with tempfile.TemporaryDirectory(prefix="rbl-wheel-") as directory:
        environment = Path(directory) / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
        subprocess.run([str(python), "-m", "pip", "check"], check=True)
        # -I excludes the checkout and PYTHONPATH, so only the installed wheel is tested.
        subprocess.run([str(python), "-I", str(suite), "-v"], cwd=directory, check=True)


if __name__ == "__main__":
    main()
