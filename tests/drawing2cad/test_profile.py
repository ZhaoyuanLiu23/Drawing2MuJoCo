import copy
import json
import math
from pathlib import Path
import runpy

import numpy as np
import pytest
import cv2

from drawing_cad.cad import make_recipe, export_cad
from drawing_cad.dimensions import parse_dimensions, parse_number
from drawing_cad.pipeline import run_pipeline
from drawing_cad.profile_geometry import rounded_profiles
from profile_fixture import reannotate

SOURCE=Path(__file__).parent/'fixtures'/'benchmark2.png'
LABELS=dict(width=2.,height=1.,thickness=.25,hole_x=1.,hole_y_from_top=.5,hole_diameter=.5,corner_radius=.25)
CHANGED=dict(width=2.8,height=1.4,thickness=.35,hole_x=1.1,hole_y_from_top=.65,hole_diameter=.4,corner_radius=.2)


@pytest.fixture(scope='module')
def profile_result(tmp_path_factory):
    # Units are an explicit TEST INPUT, not a claim about the unlabelled screenshot.
    return run_pipeline(SOURCE,output=tmp_path_factory.mktemp('profile')/'model',units='in')


def assert_profile(doc,out,labels,factor=25.4):
    assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
    assert doc['features']['recognizer']=='line_arc_profile_extrusion'
    assert set(doc['parameters'])==set(labels)
    for name,value in labels.items():
        p=doc['parameters'][name]
        assert p['nominal']==pytest.approx(value*factor)
        assert p['value_mm']==pytest.approx(value*factor)
        assert p['value_source']=='drawing_nominal' and not p['inferred']
        assert p['evidence'] and p['confidence']>=.9
    w,h,t,r,d=(labels[k]*factor for k in ('width','height','thickness','corner_radius','hole_diameter'))
    expected=(w*h-(1-math.pi/4)*r*r-math.pi*d*d/4)*t
    assert doc['validation']['bounds_mm']==pytest.approx([w,h,t])
    assert doc['validation']['volume_mm3']==pytest.approx(expected)
    assert doc['validation']['step_reimport_valid']
    assert doc['validation']['stl']['watertight']
    assert doc['validation']['stl']['volume_mm3']==pytest.approx(expected,rel=.005)
    for name in ('parsed.json','parameters.json','model.step','model.stl','preview.html','preview.png','model.py'):
        assert (out/name).stat().st_size>100
    # Check the actual kernel surface location of the through hole, not only JSON.
    rebuilt=runpy.run_path(str(out/'model.py'))['result'].val()
    cylinders=[f for f in rebuilt.Faces() if f.geomType()=='CYLINDER']
    assert any(np.allclose([f.Center().x,f.Center().y],
                          [labels['hole_x']*factor,(labels['height']-labels['hole_y_from_top'])*factor])
               and math.isclose(f.Area(),math.pi*d*t,rel_tol=1e-7) for f in cylinders)


def test_new_benchmark(profile_result):
    doc,out=profile_result
    assert_profile(doc,out,LABELS)
    assert doc['units']['default_source']=='cli_override'
    radius=doc['parameters']['corner_radius']['evidence'][0]['association']
    assert radius['leader_segment_ids']
    assert len(doc['views'])==3


def test_changed_annotations_drive_all_dimensions(profile_result,tmp_path):
    doc,_=profile_result
    source=tmp_path/'arbitrary_input.png'
    reannotate(SOURCE,doc,source,CHANGED,unit_note='in')
    changed,out=run_pipeline(source,output=tmp_path/'changed')
    assert_profile(changed,out,CHANGED)
    assert changed['units']['default_source']=='drawing_unit_note'
    assert changed['validation']['volume_mm3']!=pytest.approx(doc['validation']['volume_mm3'])
    assert changed['parameters']['hole_x']['value_mm']!=pytest.approx(changed['parameters']['width']['value_mm']/2)


def test_bare_unit_is_unknown(profile_result):
    doc,_=profile_result
    p=parse_dimensions(doc['texts'],doc['geometry'],doc['features'])
    assert p['units']['default'] is None
    assert all(v['status']=='unknown' and v['value_mm'] is None for v in p['parameters'].values())
    recipe,reasons=make_recipe(dict(features=doc['features'],parameters=p['parameters']))
    assert recipe is None and reasons


@pytest.mark.parametrize('missing',['corner_radius','hole_x','hole_y_from_top','hole_diameter','thickness'])
def test_missing_dimensions_have_no_pixel_fallback(profile_result,missing):
    doc,_=profile_result
    remove={i for d in doc['dimensions'] if d['association'] and d['association']['parameter']==missing for i in d['text_ids']}
    texts=[t for t in doc['texts'] if t['id'] not in remove]
    p=parse_dimensions(texts,doc['geometry'],doc['features'],'in')
    assert p['parameters'][missing]['value_mm'] is None
    assert make_recipe(dict(features=doc['features'],parameters=p['parameters']))[0] is None


def test_through_evidence_required(profile_result):
    doc,_=profile_result
    texts=[t for t in doc['texts'] if t['text'].upper()!='THRU']
    p=parse_dimensions(texts,doc['geometry'],doc['features'],'in')
    assert p['parameters']['hole_diameter']['status']=='unknown'


def test_radius_without_connected_leader_is_unknown(profile_result):
    doc,_=profile_result
    geometry=dict(doc['geometry'],segments=[])
    p=parse_dimensions(doc['texts'],geometry,doc['features'],'in')
    assert p['parameters']['corner_radius']['status']=='unknown'


def test_chamfer_is_not_a_radius():
    binary=np.zeros((300,500),np.uint8)
    contour=np.array([[50,50],[450,50],[450,180],[380,250],[50,250]],np.int32)
    cv2.polylines(binary,[contour],True,255,2)
    assert not rounded_profiles(binary)


@pytest.mark.parametrize('index',[0,1,2,3])
def test_quarter_arc_can_be_on_any_corner(index):
    binary=np.zeros((300,500),np.uint8)
    cv2.line(binary,(50,50),(450,50),255,2)
    cv2.line(binary,(450,50),(450,190),255,2)
    cv2.ellipse(binary,(390,190),(60,60),0,0,90,255,2)
    cv2.line(binary,(390,250),(50,250),255,2)
    cv2.line(binary,(50,250),(50,50),255,2)
    if index in (0,1): binary=cv2.flip(binary,0)
    if index in (0,3): binary=cv2.flip(binary,1)
    profiles=rounded_profiles(binary)
    assert len(profiles)==1 and profiles[0]['corner_index']==index


@pytest.mark.parametrize('text,symbol,through',[('R5mm','R',False),('r.3 in','r',False),('Ø0.40 THRU','Ø',True)])
def test_radius_and_through_grammar(text,symbol,through):
    p=parse_number(text)
    assert p['symbol']==symbol and p['through']==through


def test_incompatible_hole_and_arc_cannot_export(profile_result,tmp_path):
    doc,_=profile_result
    recipe=copy.deepcopy(doc['recipe'])
    recipe.update(width_mm=20,height_mm=10,thickness_mm=2,corner_radius_mm=4,
                  hole_x_mm=18,hole_y_from_top_mm=8,hole_diameter_mm=3)
    # Hole lies inside the rectangular envelope but crosses the rounded boundary.
    with pytest.raises(ValueError,match='volume|one valid solid'):
        export_cad(recipe,tmp_path)
    assert not (tmp_path/'model.step').exists()
