import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
from datetime import datetime, timezone

import numpy as np
from PIL import Image

from . import __version__
from .ingest import read_drawing
from .preprocess import preprocess
from .ocr import read_text
from .geometry import detect_geometry
from .features import recognize_features
from .dimensions import parse_dimensions
from .cad import make_recipe, export_cad
from .preview import save_overlay, save_preview

GENERATED=("model.step","model.stl","parameters.json","model.py","mesh.json","preview.png","preview.html",
           "parsed.json","evidence.png","preprocessed.png","source.png")


def write_json(path, value):
    def convert(v):
        if isinstance(v,np.generic): return v.item()
        if isinstance(v,np.ndarray): return v.tolist()
        raise TypeError(type(v).__name__)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=convert,allow_nan=False),encoding="utf-8")


def run_pipeline(path, output=None, page=1, dpi=180, ocr="auto", units=None, limit_policy="midpoint"):
    path=Path(path).resolve()
    digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    stem=re.sub(r"[^\w.-]","_",path.stem)[:70]
    root=Path(__file__).resolve().parent.parent
    output=Path(output).resolve() if output else root/"outputs"/"drawing2cad"/f"{stem}_{(digest or 'missing')[:8]}"
    marker=output/".drawing2cad-output"
    if output.exists() and any(output.iterdir()) and not marker.exists():
        raise ValueError(f"Output directory is not owned by drawing2cad; choose an empty directory: {output}")
    output.mkdir(parents=True,exist_ok=True)
    marker.write_text("drawing2cad V0.1 generated artifacts\n",encoding="utf-8")
    for name in GENERATED:
        target=output/name
        if target.is_file():
            target.unlink()  # fixed names inside this explicitly managed output directory only
    document=dict(schema_version="0.1",status="analyzing",source=dict(path=str(path),sha256=digest,page=page),
                  provenance=dict(pipeline_version=__version__,created_utc=datetime.now(timezone.utc).isoformat(),
                                  dependencies={name:importlib.metadata.version(name) for name in
                                                ("cadquery","cadquery-ocp","opencv-python","PyMuPDF","rapidocr-onnxruntime")},
                                  confidence_note="Heuristic evidence scores, not calibrated probabilities."),
                  configuration=dict(dpi=dpi,ocr=ocr,units_override=units,limit_policy=limit_policy),
                  preprocessing=[],texts=[],geometry={},views=[],dimensions=[],parameters={},features={},
                  units={},inferences=[],unknowns=[],warnings=[],artifacts={},validation={})
    try:
        drawing=read_drawing(path,page,dpi)
        Image.fromarray(drawing.image).save(output/"source.png")
        gray,binary=preprocess(drawing)
        Image.fromarray(255-binary).save(output/"preprocessed.png")
        read_text(drawing,ocr)
        geometry=detect_geometry(drawing,binary)
        features=recognize_features(geometry)
        parsed=parse_dimensions(drawing.texts,geometry,features,units,limit_policy)
        document.update(preprocessing=drawing.preprocessing,texts=drawing.texts,geometry=geometry,
                        views=features.get("views",[]),features=features,dimensions=parsed["dimensions"],
                        parameters=parsed["parameters"],units=parsed["units"],warnings=drawing.warnings)
        document['constraints']=parsed['constraints']
        document['conflicts']=[c for c in parsed['constraints'] if c['status']=='conflict']
        document["source"].update(page_count=drawing.page_count,processed_image_size=list(drawing.image.shape[1::-1]))
        document["unknowns"]=features.get("unknown",[])+[f"{k}: {p['reason']}" for k,p in parsed["parameters"].items() if p["status"]=="unknown"]
        document["inferences"]=features.get("assumptions",[])+[f"{k}: {p['reason']}" for k,p in parsed["parameters"].items() if p["inferred"]]
        save_overlay(drawing,geometry,parsed["dimensions"],output)
        recipe,reasons=make_recipe(document)
        if recipe is None:
            document.update(status="needs_review",blocking_reasons=reasons)
        else:
            mesh,validation=export_cad(recipe,output)
            save_preview(mesh,recipe,output)
            document.update(status="generated_with_assumptions",recipe=recipe,validation=validation)
        document["artifacts"]={name:str(output/name) for name in GENERATED if (output/name).exists()}
        document["artifacts"]["parsed.json"]=str(output/"parsed.json")
    except Exception as exc:
        document.update(status="error",error=dict(type=type(exc).__name__,message=str(exc)))
        # Failed exports must not leave plausible-looking stale or partial CAD deliverables.
        for name in ("model.step","model.stl","preview.png","preview.html"):
            if (output/name).is_file(): (output/name).unlink()
    write_json(output/"parsed.json",document)
    return document,output
