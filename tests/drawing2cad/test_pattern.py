import copy
import json
import math
from pathlib import Path
import runpy

import numpy as np
import pytest

from drawing_cad.cad import make_recipe, export_cad
from drawing_cad.dimensions import parse_dimensions
from drawing_cad.features import recognize_features
from drawing_cad.pipeline import run_pipeline
from drawing_cad.pattern_constraints import resolve_pattern_constraints
from pattern_fixture import CHANGED, draw_pattern_pdf
from variable_pattern_fixture import draw_variable_pattern_pdf

SOURCE=Path(__file__).parent/'fixtures'/'benchmark3.png'


@pytest.fixture(scope='module')
def pattern_result(tmp_path_factory):
    # Explicit user-authorized TEST unit. The screenshot has no unit note.
    return run_pipeline(SOURCE,output=tmp_path_factory.mktemp('pattern')/'model',units='mm')


def assert_pattern(doc,out,expected):
    assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
    assert doc['features']['recognizer']=='rectangular_plate_feature_pattern'
    for name,value in expected.items():
        p=doc['parameters'][name]
        assert (p['value_degrees'] if name=='countersink_angle' else p['value_mm'])==pytest.approx(value)
        assert p['value_source']=='drawing_nominal'
    v=doc['validation'];r=doc['recipe']
    assert v['bounds_mm']==pytest.approx([expected['plate_width'],expected['plate_height'],expected['thickness']])
    # Independent analytic calculation, not the production volume helper.
    l,s,t=(expected[n] for n in ('slot_length','slot_width','thickness'))
    a,b=(expected[n]/2 for n in ('hole_diameter','countersink_diameter'))
    depth=(b-a)/math.tan(math.radians(expected['countersink_angle']/2))
    expected_volume=expected['plate_width']*expected['plate_height']*t
    expected_volume-=len(r['slot_centres_mm'])*(s*(l-s)+math.pi*s*s/4)*t
    expected_volume-=len(r['hole_centres_mm'])*(math.pi*a*a*t+math.pi*depth*(b*b+b*a-2*a*a)/3)
    assert v['volume_mm3']==pytest.approx(expected_volume)
    assert v['step_reimport_valid'] and v['stl']['watertight']
    assert v['stl']['volume_mm3']==pytest.approx(expected_volume,rel=.005)
    # Actual conical faces in the independently rebuilt exported model.
    solid=runpy.run_path(str(out/'model.py'))['result'].val()
    cones=[f for f in solid.Faces() if f.geomType()=='CONE']
    assert len(cones)==len(r['hole_centres_mm'])
    for x,y in r['hole_centres_mm']:
        assert any(np.allclose([face.Center().x,face.Center().y],[x,y],atol=1e-6) for face in cones)
    assert solid.Volume()==pytest.approx(expected_volume)
    for filename in ('parsed.json','parameters.json','model.step','model.stl','preview.html','preview.png','model.py'):
        assert (out/filename).stat().st_size>100


def test_original_pattern_and_conflict(pattern_result):
    doc,out=pattern_result
    expected=dict(plate_width=176.8,plate_height=60,thickness=3.2,slot_width=12.5,slot_length=30,
                  hole_diameter=4.8,countersink_diameter=8.5,countersink_angle=100)
    assert_pattern(doc,out,expected)
    assert len(doc['recipe']['hole_centres_mm'])==8 and len(doc['recipe']['slot_centres_mm'])==2
    conflict=doc['conflicts'][0]
    assert conflict['raw_values']==[15,36,15]
    assert conflict['direct_total']==60 and conflict['chain_sum']==66 and conflict['difference']==6
    assert doc['parameters']['hole_y_chain_1']['nominal']==36
    for name,selected,candidates in [('hole_y_0',15,[15,9]),('hole_y_1',45,[51,45])]:
        p=doc['parameters'][name]
        assert p['status']=='inferred' and p['resolution_status']=='resolved_by_geometry'
        assert p['nominal'] is None and p['confidence']==pytest.approx(.6)
        assert p['value_mm']==selected and [v['value_mm'] for v in p['candidates']]==candidates
        assert name in doc['recipe']['inferred_dimension_names']
        assert doc['recipe']['dimension_usage'][name]['inferred']
    assert doc['recipe']['conflicts']==doc['conflicts']
    assert doc['units']['default_source']=='cli_override' and doc['units']['detected']==[]
    assert doc['recipe']['unit_provenance']['default_source']=='cli_override'


def test_angle_has_degree_units(pattern_result):
    doc,_=pattern_result
    p=doc['parameters']['countersink_angle']
    assert p['value_mm'] is None and p['value_degrees']==100 and p['unit']=='deg'
    assert doc['recipe']['dimension_usage']['countersink_angle']['used_value_degrees']==100


def test_unknown_units_preserve_unscaled_conflict(pattern_result):
    doc,_=pattern_result
    f=recognize_features(doc['geometry'])
    parsed=parse_dimensions(doc['texts'],doc['geometry'],f)
    assert parsed['units']['default'] is None and parsed['units']['status']=='unknown'
    assert parsed['units']['default_source'] is None and parsed['units']['detected']==[]
    c=next(c for c in parsed['constraints'] if c['id']=='hole_y_chain')
    assert c['status']=='conflict' and c['unit']=='unknown' and c['raw_values']==[15,36,15]
    assert all(p['value_mm'] is None for p in parsed['parameters'].values())
    assert make_recipe(dict(features=f,parameters=parsed['parameters']))[0] is None


def test_constraint_paths_use_normalized_units(pattern_result):
    doc,_=pattern_result
    feature=recognize_features(doc['geometry'])
    parsed=parse_dimensions(doc['texts'],doc['geometry'],feature,'cm')
    recipe,reasons=make_recipe(dict(features=feature,parameters=parsed['parameters']))
    assert recipe is not None,reasons
    assert np.allclose(recipe['hole_centres_mm'],np.array(doc['recipe']['hole_centres_mm'])*10)
    assert np.allclose(recipe['slot_centres_mm'],np.array(doc['recipe']['slot_centres_mm'])*10)
    assert next(c for c in parsed['constraints'] if c['id']=='hole_y_chain')['raw_values']==[15,36,15]


def test_unresolved_conflict_is_not_replaced_by_centerline(pattern_result):
    doc,_=pattern_result
    feature=copy.deepcopy(doc['features']);parameters=copy.deepcopy(doc['parameters'])
    spec=feature['position_axes']['hole_y'];axis=spec['axes'][0]
    original=parameters['hole_y_0']['candidates']
    equidistant=sum(c['value_mm'] for c in original)/len(original)
    lo,hi=spec['plate_span_px']
    axis['coordinate_px']=lo+equidistant/parameters['plate_height']['value_mm']*(hi-lo)
    # Even an available geometric centre-line suggestion cannot replace two
    # contradictory explicit datum paths which geometry cannot disambiguate.
    axis['centerline_constraint']=True
    constraints=resolve_pattern_constraints(feature,doc['dimensions'],parameters,'mm')
    assert next(c for c in constraints if c['id']=='hole_y_chain')['status']=='conflict'
    assert parameters['hole_y_0']['resolution_status']=='ambiguous'
    assert parameters['hole_y_0']['value_mm'] is None
    assert make_recipe(dict(features=feature,parameters=parameters))[0] is None


@pytest.mark.parametrize('missing,can_resolve',[
    (['hole_x_chain_2'],True),
    (['hole_x_chain_1','hole_x_chain_2'],False),
])
def test_incomplete_chains_require_a_complete_datum_path(pattern_result,missing,can_resolve):
    doc,_=pattern_result
    remove={i for d in doc['dimensions'] if d['association'] and d['association']['parameter'] in missing for i in d['text_ids']}
    feature=recognize_features(doc['geometry'])
    parsed=parse_dimensions([t for t in doc['texts'] if t['id'] not in remove],doc['geometry'],feature,'mm')
    recipe,_=make_recipe(dict(features=feature,parameters=parsed['parameters']))
    assert (recipe is not None)==can_resolve
    if can_resolve:
        assert np.allclose(recipe['hole_centres_mm'],doc['recipe']['hole_centres_mm'])
    else:
        assert any(v['status']=='ambiguous' for v in feature['resolved_features'])


def test_same_geometry_different_dimensions(pattern_result,tmp_path):
    original,_=pattern_result
    path=draw_pattern_pdf(tmp_path/'unrelated_name.pdf')
    changed,out=run_pipeline(path,output=tmp_path/'changed',dpi=72,units='mm')
    assert_pattern(changed,out,CHANGED)
    assert changed['conflicts']==[]
    assert sorted(changed['recipe']['hole_centres_mm'])==sorted([[x,y] for y in (54,16) for x in (20,70,130,180)])
    assert sorted(changed['recipe']['slot_centres_mm'])==sorted([[45,35],[155,35]])
    assert changed['validation']['volume_mm3']!=pytest.approx(original['validation']['volume_mm3'])
    # Same synthetic drawing strokes, second independent set of dimensions.
    values={name:value*1.2 for name,value in CHANGED.items()}
    values['countersink_angle']=95
    second_path=draw_pattern_pdf(tmp_path/'another.pdf',values)
    second,second_out=run_pipeline(second_path,output=tmp_path/'second',dpi=72,units='mm')
    assert_pattern(second,second_out,values)
    assert second['validation']['volume_mm3']!=pytest.approx(changed['validation']['volume_mm3'])


@pytest.mark.parametrize('missing',['plate_height','thickness','hole_diameter','countersink_angle'])
def test_missing_evidence_blocks_model(pattern_result,missing):
    doc,_=pattern_result
    remove={i for d in doc['dimensions'] if d['association'] and d['association']['parameter']==missing for i in d['text_ids']}
    texts=[t for t in doc['texts'] if t['id'] not in remove]
    f=recognize_features(doc['geometry'])
    p=parse_dimensions(texts,doc['geometry'],f,'mm')
    assert p['parameters'][missing]['status']=='unknown'
    assert make_recipe(dict(features=f,parameters=p['parameters']))[0] is None


def test_missing_slot_callout_uses_explicit_geometry_inference(pattern_result):
    doc,_=pattern_result
    remove={i for d in doc['dimensions'] if d['association'] and d['association']['parameter']=='slot_width' for i in d['text_ids']}
    feature=recognize_features(doc['geometry'])
    parsed=parse_dimensions([t for t in doc['texts'] if t['id'] not in remove],doc['geometry'],feature,'mm')
    recipe,reasons=make_recipe(dict(features=feature,parameters=parsed['parameters']))
    assert recipe is not None,reasons
    assert recipe['mode']=='inferred_geometry'
    assert parsed['parameters']['slot_width']['value_mm'] is None
    for slot in (v for v in feature['resolved_features'] if v['kind']=='slot'):
        assert parsed['parameters'][slot['width_parameter']]['value_source']=='inferred_geometry'
    # Existing dimension-chain conflict is still retained, not repaired.
    assert any(c['status']=='conflict' and c['raw_values']==[15,36,15] for c in parsed['constraints'])


def test_disconnected_callouts_cannot_export(pattern_result):
    doc,_=pattern_result
    geometry=dict(doc['geometry'],segments=[],annotation_segments=[])
    f=recognize_features(geometry)
    p=parse_dimensions(doc['texts'],geometry,f,'mm')
    assert p['parameters']['slot_length']['status']=='unknown'
    assert p['parameters']['hole_diameter']['status']=='unknown'
    assert make_recipe(dict(features=f,parameters=p['parameters']))[0] is None


def test_wrong_hole_count_cannot_export(pattern_result):
    doc,_=pattern_result
    f=recognize_features(doc['geometry']);f['callout_requirements']['hole_count']+=1
    p=parse_dimensions(doc['texts'],doc['geometry'],f,'mm')
    assert p['parameters']['hole_diameter']['status']=='unknown'
    assert make_recipe(dict(features=f,parameters=p['parameters']))[0] is None


@pytest.mark.parametrize('name,value',[('thickness',.1),('countersink_diameter',3),('countersink_angle',180)])
def test_impossible_countersink_is_rejected(pattern_result,name,value):
    doc,_=pattern_result
    altered=copy.deepcopy(doc)
    altered['parameters'][name]['value_degrees' if name=='countersink_angle' else 'value_mm']=value
    recipe,reasons=make_recipe(altered)
    assert recipe is None and reasons


def test_overlapping_cuts_fail_volume_check(pattern_result,tmp_path):
    doc,_=pattern_result
    r=copy.deepcopy(doc['recipe'])
    r['hole_centres_mm'][0]=r['slot_centres_mm'][0]
    with pytest.raises(ValueError,match='volume'):
        export_cad(r,tmp_path)
    assert not (tmp_path/'model.step').exists()


VARIABLE_LAYOUTS = [
    pytest.param(
        [(20,15),(100,15),(180,15),(20,65),(100,65),(180,65)],
        [(100,40)],
        'six_holes_one_slot',
        id='6-holes-1-slot',
    ),
    pytest.param(
        [(15,y) for y in (12,40,68)] + [(70,y) for y in (12,40,68)]
        + [(130,y) for y in (12,40,68)] + [(185,y) for y in (12,40,68)],
        [(42,40),(100,40),(158,40)],
        'twelve_holes_three_slots',
        id='12-holes-3-slots-three-rows',
    ),
    pytest.param(
        [(18,15),(92,15),(178,15),(18,67),(92,67),(178,67)],
        [(122,54)],
        'non_centre_slot',
        id='non-centre-slot',
    ),
    pytest.param(
        [(25,15),(75,15),(125,40),(175,65),(145,65)],
        [(48,55),(155,25)],
        'changed_positions_and_counts',
        id='changed-count-and-positions',
    ),
]


@pytest.mark.parametrize('holes,slots,stem',VARIABLE_LAYOUTS)
def test_variable_feature_candidates_constraints_and_cad(tmp_path,holes,slots,stem):
    drawing,expected=draw_variable_pattern_pdf(tmp_path/f'{stem}.pdf',holes,slots)
    doc,out=run_pipeline(drawing,output=tmp_path/f'{stem}_out',dpi=72,units='mm')
    assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
    feature=doc['features']; recipe=doc['recipe']
    assert len(feature['feature_candidates'])==len(holes)+len(slots)
    assert len([v for v in feature['feature_candidates'] if v['kind']=='circle'])==len(holes)
    assert len([v for v in feature['feature_candidates'] if v['kind']=='slot'])==len(slots)
    assert len(feature['resolved_features'])==len(holes)+len(slots)
    assert all(v['status']=='resolved' for v in feature['resolved_features'])
    assert sorted(map(tuple,recipe['hole_centres_mm']))==pytest.approx(
        sorted((x,expected['plate_height']-y) for x,y in holes))
    assert sorted(map(tuple,recipe['slot_centres_mm']))==pytest.approx(
        sorted((x,expected['plate_height']-y) for x,y in slots))
    assert doc['validation']['step_reimport_valid'] and doc['validation']['stl']['watertight']


def test_non_centre_slot_without_y_constraint_is_ambiguous(tmp_path):
    holes=[(20,15),(100,15),(180,15),(20,65),(100,65),(180,65)]
    slots=[(122,54)]
    drawing,_=draw_variable_pattern_pdf(tmp_path/'ambiguous_slot.pdf',holes,slots,draw_slot_y=False)
    doc,_=run_pipeline(drawing,output=tmp_path/'ambiguous_slot_out',dpi=72,units='mm')
    assert doc['status']=='needs_review'
    slot=next(v for v in doc['features']['resolved_features'] if v['kind']=='slot')
    assert slot['status']=='ambiguous'
    y=doc['parameters'][slot['y_parameter']]
    assert y['status']=='unknown' and y['resolution_status']=='ambiguous'
    assert doc.get('recipe') is None
