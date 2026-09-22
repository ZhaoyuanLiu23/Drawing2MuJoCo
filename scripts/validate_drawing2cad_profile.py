"""Second-benchmark regression. --units is an explicit test setting, never guessed.

The changed drawing retains geometry pixels and changes seven annotations by
different factors, including an off-centre hole. It is marked NOT TO SCALE.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests'/'drawing2cad'))
import numpy as np
from drawing_cad.pipeline import run_pipeline,write_json
from drawing_cad.dimensions import UNIT_FACTORS
from profile_fixture import reannotate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('drawing',type=Path)
    parser.add_argument('--units',choices=('in','mm','cm'),required=True)
    parser.add_argument('--output',type=Path,default=ROOT/'outputs'/'drawing2cad_benchmark2')
    args=parser.parse_args()
    root=args.output.resolve(); root.mkdir(parents=True,exist_ok=True)
    source=args.drawing.resolve(); original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    cases=[]
    def check(name,path,expected=None,**options):
        doc,out=run_pipeline(path,output=root/name,**options)
        assert doc['status']=='generated_with_assumptions',(name,doc.get('blocking_reasons',doc.get('error')))
        values={k:p['value_mm'] for k,p in doc['parameters'].items()}
        if expected is not None:
            assert set(values)==set(expected)
            assert all(np.isclose(values[k],v) for k,v in expected.items()),(values,expected)
        assert doc['validation']['step_reimport_valid'] and doc['validation']['stl']['watertight']
        cases.append(dict(case=name,status='passed',parameters_mm=values,output=str(out),validation=doc['validation']))
        print(name,values,flush=True)
        return doc,out
    raw,out=run_pipeline(source,output=root/'original_unknown_units')
    assert raw['units']['default'] is None
    assert raw['status']=='needs_review' and not (out/'model.step').exists()
    cases.append(dict(case='original_unknown_units',status='passed',observed='needs_review',output=str(out)))
    original,out=check('original_explicit_test_unit',source,units=args.units)
    factor=UNIT_FACTORS[args.units]
    changes=dict(width=1.4,height=1.4,thickness=1.4,hole_x=1.1,hole_y_from_top=1.3,hole_diameter=.8,corner_radius=.8)
    labels={k:round(original['parameters'][k]['value_mm']/factor*multiplier,2) for k,multiplier in changes.items()}
    variant=root/'changed_annotations.png'
    edits=reannotate(source,original,variant,labels,unit_note=args.units)
    modified,_=check('changed_annotations',variant,{k:v*factor for k,v in labels.items()})
    assert not np.isclose(modified['validation']['volume_mm3'],original['validation']['volume_mm3'])
    anonymous=root/'anonymous_input.png'; anonymous.write_bytes(source.read_bytes())
    check('anonymous_filename',anonymous,{k:p['value_mm'] for k,p in original['parameters'].items()},units=args.units)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    write_json(root/'acceptance_report.json',dict(source=str(source),source_sha256=original_hash,source_unchanged=True,
              test_unit=args.units,unit_note='The source has no unit label. This is an explicit TEST configuration, not inferred drawing evidence or user confirmation.',
              variant_description='Only dimension text changed; unchanged geometry is marked NOT TO SCALE. Units are printed on the synthetic variant.',
              annotation_changes=edits,cases=cases))
    print('Passed:',root/'acceptance_report.json')


if __name__=='__main__':
    main()
