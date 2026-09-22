"""B3 acceptance: original raster, unknown units, anonymous input, changed labels."""
import argparse
import hashlib
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'/'drawing2cad'))
from drawing_cad.pipeline import run_pipeline,write_json
from pattern_fixture import CHANGED,draw_pattern_pdf
from test_pattern import assert_pattern


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drawing',type=Path)
    parser.add_argument('--units',choices=['mm'],required=True,help='Explicit user/test unit; absent on source drawing')
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'drawing2cad_benchmark3')
    args=parser.parse_args();root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    source=args.drawing.resolve();digest=hashlib.sha256(source.read_bytes()).hexdigest();cases=[]
    raw,out=run_pipeline(source,output=root/'unknown_units')
    assert raw['status']=='needs_review' and raw['units']['status']=='unknown'
    assert raw['units']['default_source'] is None and not (out/'model.step').exists()
    assert any(c['status']=='conflict' and c['unit']=='unknown' for c in raw['constraints'])
    cases.append(dict(case='unknown_units',status='passed',output=str(out),units=raw['units'],conflicts=raw['conflicts']))

    def check(name,path,expected=None,**options):
        doc,out=run_pipeline(path,output=root/name,units=args.units,**options)
        assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
        values={name:(p['value_degrees'] if p.get('quantity')=='angle' else p['value_mm'])
                for name,p in doc['parameters'].items() if p['status']=='known' and not p['inferred']}
        assert_pattern(doc,out,expected or values)
        cases.append(dict(case=name,status='passed',parameters=values,output=str(out),
                          validation=doc['validation'],conflicts=doc['conflicts'],units=doc['units']))
        print(name,doc['validation']['bounds_mm'],flush=True)
        return doc
    original=check('original_explicit_test_unit',source)
    anonymous=root/'anonymous_input.png';anonymous.write_bytes(source.read_bytes())
    check('anonymous_filename',anonymous)
    variant=draw_pattern_pdf(root/'changed_dimensions.pdf')
    changed=check('changed_dimensions',variant,CHANGED,dpi=72)
    second_values={n:v*1.2 for n,v in CHANGED.items()};second_values['countersink_angle']=95
    second=draw_pattern_pdf(root/'changed_again.pdf',second_values)
    check('same_strokes_second_dimensions',second,second_values,dpi=72)
    assert original['validation']['volume_mm3']!=changed['validation']['volume_mm3']
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    write_json(root/'acceptance_report.json',dict(status='passed',source=str(source),source_sha256=digest,
        source_unchanged=True,test_unit=args.units,unit_source='explicit_user_authorized_test_parameter',
        variant_description='Independent vector engineering drawing with the same rectangular/slot/countersink topology. Two versions retain identical geometry strokes and change annotations only; not a direct raster edit of the screenshot.',
        cases=cases))
    print('Passed:',root/'acceptance_report.json')


if __name__=='__main__':main()
