"""CLI launcher; isolates CAD dependencies from the existing Panda environment."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parent
    environment = root / ".venv-drawing2cad"
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.is_file() and Path(sys.prefix).resolve() != environment.resolve():
        return subprocess.call([str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        from drawing_cad.cli import main as run
    except ImportError as exc:
        print(f"CAD environment is missing: {exc}\nSee DRAWING2CAD.md for isolated installation.", file=sys.stderr)
        return 1
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
