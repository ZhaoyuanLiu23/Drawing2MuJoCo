"""Server-owned upload limits and storage location; never taken from a request."""

from pathlib import Path
import os

PROJECT_ROOT = Path(__file__).resolve().parents[3]
JOBS_ROOT = PROJECT_ROOT / "outputs" / "web" / "jobs"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024  # multipart headers/boundary
MAX_IMAGE_PIXELS = 20_000_000
PREVIEW_MAX_EDGE = 1600
CAD_PYTHON = PROJECT_ROOT / ".venv-drawing2cad" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
CAD_TIMEOUT_SECONDS = 180
# Server-owned local robot runtime/profile; never accepted from uploaded metadata.
SIMULATION_PYTHON = Path(os.environ.get("WEB_SIMULATION_PYTHON", str(Path.home() / "Desktop/python/python.exe")))
PANDA_SCENE = Path(os.environ.get("CAD_MUJOCO_PANDA_SCENE", str(Path.home() / "Desktop/mujoco_menagerie/franka_emika_panda/scene.xml")))
SIMULATION_TIMEOUT_SECONDS = 600
SIMULATION_DENSITY_KG_M3 = 7800.0  # Explicit test material, not drawing recognition.
SIMULATION_ZONE_SIZE_M = (0.16, 0.16)

# Extension -> (public file_type, declared MIME, decoded format).
FILE_TYPES = {
    ".pdf": ("pdf", "application/pdf", "PDF"),
    ".png": ("png", "image/png", "PNG"),
    ".jpg": ("jpeg", "image/jpeg", "JPEG"),
    ".jpeg": ("jpeg", "image/jpeg", "JPEG"),
}
