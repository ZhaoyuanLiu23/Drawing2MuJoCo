import argparse
from pathlib import Path
import sys
import webbrowser

from .pipeline import run_pipeline


def main(argv=None):
    parser=argparse.ArgumentParser(description="Drawing2CAD V0.1: evidence-based PDF/PNG/JPG to parametric STEP/STL")
    parser.add_argument("drawing",type=Path)
    parser.add_argument("--output",type=Path,help="Empty or drawing2cad-owned output directory")
    parser.add_argument("--page",type=int,default=1)
    parser.add_argument("--dpi",type=int,default=180)
    parser.add_argument("--ocr",choices=("auto","always","off"),default="auto")
    parser.add_argument("--units",choices=("mm","cm","in"),help="Explicit user unit for bare dimensions only")
    parser.add_argument("--limit-policy",choices=("midpoint","require-nominal"),default="midpoint")
    parser.add_argument("--open",action="store_true",help="Open the offline interactive 3D preview")
    args=parser.parse_args(argv)
    if not 72<=args.dpi<=400 or args.page<1:
        parser.error("--dpi must be 72..400 and --page must be positive")
    try:
        report,output=run_pipeline(args.drawing,args.output,args.page,args.dpi,args.ocr,args.units,args.limit_policy)
    except (ValueError,OSError) as exc:
        print(f"ERROR: {exc}",file=sys.stderr)
        return 1
    print(f"Status: {report['status']}\nOutput: {output}\nEvidence: {output/'parsed.json'}")
    for name,p in report["parameters"].items():
        value,unit=(p.get('value_degrees'),'deg') if p.get('quantity')=='angle' else (p['value_mm'],'mm')
        print(f"  {name}: {value} {unit} [{p['status']}], confidence={p['confidence']:.3f}")
        if p["inferred"]: print(f"    Inference: {p['reason']}")
    if report["status"]=="generated_with_assumptions":
        print(f"STEP: {output/'model.step'}\nSTL: {output/'model.stl'}\nPreview: {output/'preview.html'}")
        if args.open: webbrowser.open((output/"preview.html").as_uri())
        return 0
    for reason in report.get("blocking_reasons",[]): print(f"NEEDS REVIEW: {reason}")
    if report.get("error"): print(f"ERROR: {report['error']['message']}",file=sys.stderr)
    return 2 if report["status"]=="needs_review" else 1


if __name__=="__main__":
    raise SystemExit(main())
