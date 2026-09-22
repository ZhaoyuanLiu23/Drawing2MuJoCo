"""Acceptance check against a user-supplied PDF, without sample-specific dimensions.

Run in .venv-drawing2cad. Creates PDF/PNG/JPG fixtures under outputs only.
It changes dimension annotations while leaving geometric drawing strokes intact.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz
from PIL import Image
import numpy as np
from drawing_cad.dimensions import parse_number
from drawing_cad.pipeline import run_pipeline, write_json


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing",type=Path)
    parser.add_argument("--scale",type=float,default=1.5)
    parser.add_argument("--output",type=Path,default=Path("outputs/drawing2cad_acceptance"))
    args=parser.parse_args()
    source=args.drawing.resolve()
    root=args.output.resolve()
    root.mkdir(parents=True,exist_ok=True)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    reports=[]

    def check(name, drawing, expected=None, **kwargs):
        doc,out=run_pipeline(drawing,output=root/name,**kwargs)
        assert doc["status"]=="generated_with_assumptions", (name,doc)
        values={k:p["value_mm"] for k,p in doc["parameters"].items()}
        if expected:
            assert values.keys()==expected.keys()
            assert all(np.isclose(values[k],v) for k,v in expected.items()), (name,values,expected)
        assert doc["validation"]["step_reimport_valid"]
        assert doc["validation"]["stl"]["watertight"]
        reports.append(dict(case=name,status="passed",parameters_mm=values,
                            output=str(out),validation=doc["validation"]))
        print(name, values, flush=True)
        return values

    baseline=check("original_pdf",source)
    renamed=root/"anonymous_input.pdf"
    renamed.write_bytes(source.read_bytes())
    check("anonymous_filename",renamed,baseline)
    with fitz.open(source) as pdf:
        pix=pdf[0].get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False)
        image=Image.frombytes("RGB",(pix.width,pix.height),pix.samples)
    for extension in ("png","jpg"):
        raster=root/f"raster_input.{extension}"
        image.save(raster,quality=95)
        check(f"raster_{extension}",raster,baseline)
    changed=root/"changed_dimensions.pdf"
    edits=[]
    with fitz.open(source) as pdf:
        page=pdf[0]
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines",[]):
                for span in line["spans"]:
                    number=parse_number(span["text"])
                    if not number or number["unit"] is None:
                        continue
                    # This fixture mutator handles plain, explicit unit labels only.
                    if number["tolerance"] is not None or number["symbol"]:
                        raise ValueError("Fixture mutation needs plain numeric unit labels.")
                    text=f"{number['value']*args.scale:g}{number['unit']}"
                    edits.append((span,text))
                    page.add_redact_annot(span["bbox"],fill=(1,1,1))
        assert edits, "No native dimension labels available for this fixture mutation."
        page.apply_redactions(images=0,graphics=0)
        for span,text in edits:
            page.insert_text(span["origin"],text,fontsize=span["size"],fontname="helv")
        pdf.save(changed)
    expected={k:v*args.scale for k,v in baseline.items()}
    check("changed_annotations",changed,expected)
    strict,out=run_pipeline(source,output=root/"strict_limits",limit_policy="require-nominal")
    has_limits=any(p["limits_mm"] is not None for p in strict["parameters"].values())
    if has_limits:
        assert strict["status"]=="needs_review"
        assert not (out/"model.step").exists()
        reports.append(dict(case="strict_limits",status="passed",observed="needs_review"))
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    result=dict(source=str(source),source_unchanged=True,source_sha256=original_hash,
                annotation_scale=args.scale,changed_label_count=len(edits),cases=reports,
                note="Expected variant values derive from source evidence multiplied by the test factor; no sample dimensions are built into the parser.")
    write_json(root/"acceptance_report.json",result)
    print("All acceptance cases passed:",root/"acceptance_report.json")


if __name__=="__main__":
    main()
