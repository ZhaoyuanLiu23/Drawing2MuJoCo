import copy
import json
import math
import runpy

import numpy as np
import pytest

from drawing_cad.cad import make_recipe
from drawing_cad.dimensions import parse_dimensions
from drawing_cad.features import recognize_features
from drawing_cad.pipeline import run_pipeline
from slot_only_fixture import draw_slot_only


@pytest.fixture(scope='module')
def slot_only_result(tmp_path_factory):
    root=tmp_path_factory.mktemp('slot_only')
    return run_pipeline(draw_slot_only(root/'anonymous.pdf'),output=root/'out',dpi=144)


def check_exports(doc,out,width,height,thickness,slots):
    assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
    assert doc['features']['mode']=='inferred_geometry'
    r=doc['recipe'];v=doc['validation']
    assert r['mode']=='inferred_geometry'
    assert r['hole_centres_mm']==[]
    assert len(r['slot_centres_mm'])==len(slots)
    actual=sorted(zip(r['slot_centres_mm'],r['slot_sizes_mm']),key=lambda v:v[0][0])
    expected_slots=sorted(slots)
    assert np.allclose([v[0] for v in actual],[(s[0],height-s[1]) for s in expected_slots],atol=.65)
    assert np.allclose([v[1] for v in actual],[(s[2],s[3]) for s in expected_slots],atol=.65)
    expected=width*height*thickness-sum((d*(l-d)+math.pi*d*d/4)*thickness for l,d in r['slot_sizes_mm'])
    assert v['bounds_mm']==pytest.approx([width,height,thickness])
    assert v['volume_mm3']==pytest.approx(expected)
    assert v['step_reimport_valid'] and v['stl']['watertight']
    assert v['stl']['volume_mm3']==pytest.approx(expected,rel=.005)
    rebuilt=runpy.run_path(str(out/'model.py'))['result'].val()
    assert rebuilt.Volume()==pytest.approx(expected)
    assert not any(f.geomType()=='CONE' for f in rebuilt.Faces())
    for filename in ('parsed.json','parameters.json','model.step','model.stl','preview.png','preview.html'):
        assert (out/filename).stat().st_size>100
    saved=json.loads((out/'parsed.json').read_text(encoding='utf-8'))
    candidates=saved['features']['feature_candidates']
    assert all(c['kind']=='slot' and c['source']=='detected' for c in candidates)
    for slot in saved['features']['resolved_features']:
        for key in ('length_parameter','width_parameter','x_parameter','y_parameter'):
            name=slot[key];p=saved['parameters'][name]
            assert p['source']=='inferred' and p['inferred']
            assert p['nominal'] is None and p['min'] is None and p['max'] is None
            assert p['confidence']<.9
            assert name in saved['recipe']['inferred_dimension_names']
            usage=saved['recipe']['dimension_usage'][name]
            assert usage['source']=='inferred' and usage['inferred']
        for key in ('length_parameter','width_parameter'):
            p=saved['parameters'][slot[key]]
            assert p['value_source']=='inferred_geometry'
            assert p['evidence'][0]['source']=='detected'
            assert p['evidence'][0]['calibration']['parameters']['plate_width']['dimension_ids']


def test_100_by_25_by_6_without_holes(slot_only_result):
    doc,out=slot_only_result
    check_exports(doc,out,100,25,6,[(25,12.5,24,10),(75,12.5,24,10)])
    assert doc['units']['status']=='known' and doc['units']['default_source'] is None
    for name,value,tol in [('plate_width',100,.2),('plate_height',25,.2),('thickness',6,.5)]:
        p=doc['parameters'][name]
        assert p['source']=='detected' and not p['inferred']
        assert p['nominal']==value and p['min']==pytest.approx(value-tol) and p['max']==pytest.approx(value+tol)
    assert any(d['association'] and d['association'].get('method')=='paired_extension_lines_with_connected_offset_label'
               for d in doc['dimensions'])


def test_changed_sizes_count_and_off_centre_locations(tmp_path):
    slots=[(25,12,26,8),(75,20,30,12),(125,15,24,10)]
    path=draw_slot_only(tmp_path/'arbitrary.pdf',width=150,height=35,thickness=8,slots=slots)
    doc,out=run_pipeline(path,output=tmp_path/'out',dpi=144)
    check_exports(doc,out,150,35,8,slots)


@pytest.mark.parametrize('change',['missing_thickness','unknown_units','not_to_scale','inconsistent_scale','malformed_slot','split_malformed_slot','disconnected_label'])
def test_missing_or_rejected_evidence_cannot_be_inferred(slot_only_result,change):
    doc,_=slot_only_result
    texts=copy.deepcopy(doc['texts']);geometry=copy.deepcopy(doc['geometry'])
    if change=='missing_thickness':
        ids={i for d in doc['dimensions'] if d['association'] and d['association']['parameter']=='thickness' for i in d['text_ids']}
        texts=[t for t in texts if t['id'] not in ids]
    elif change=='unknown_units':
        for t in texts:t['text']=t['text'].replace('mm','')
    elif change=='inconsistent_scale':
        for t in texts:t['text']=t['text'].replace('100.00mm','160.00mm')
    elif change=='disconnected_label':
        # Move the thickness label away from every line; no pixel fallback can supply thickness.
        ids={i for d in doc['dimensions'] if d['association'] and d['association']['parameter']=='thickness' for i in d['text_ids']}
        for t in texts:
            if t['id'] in ids:t['bbox']=[20,20,160,40]
    elif change=='split_malformed_slot':
        x=10
        for index,word in enumerate(('2X','SLOT','30','x','?','THRU')):
            width=len(word)*10
            texts.append(dict(id=f'extra_{index}',text=word,bbox=[x,10,x+width,30],confidence=1,source='pdf_text'))
            x+=width+5
    else:
        texts.append(dict(id='extra',text='NOT TO SCALE' if change=='not_to_scale' else '2X SLOT 30 x ? THRU',
                          bbox=[10,10,200,30],confidence=1,source='pdf_text'))
    feature=recognize_features(geometry)
    parsed=parse_dimensions(texts,geometry,feature)
    assert make_recipe(dict(features=feature,parameters=parsed['parameters']))[0] is None


def test_partial_slot_position_constraint_is_not_overwritten(slot_only_result):
    from drawing_cad.slot_inference import infer_undimensioned_slots
    doc,_=slot_only_result
    feature=recognize_features(doc['geometry'])
    # Resolve original dimensions while withholding thickness to disable inference initially.
    texts=[t for t in doc['texts'] if not t['text'].startswith('6.00')]
    parsed=parse_dimensions(texts,doc['geometry'],feature)
    parsed['parameters']['thickness']=doc['parameters']['thickness']
    chain=next(c for c in parsed['constraints'] if c['id']=='slot_x_chain')
    chain['raw_values'][0]=17.
    infer_undimensioned_slots(feature,doc['geometry'],texts,parsed['dimensions'],parsed['parameters'],parsed['constraints'])
    assert feature['resolution_status']=='ambiguous'
    assert make_recipe(dict(features=feature,parameters=parsed['parameters']))[0] is None
