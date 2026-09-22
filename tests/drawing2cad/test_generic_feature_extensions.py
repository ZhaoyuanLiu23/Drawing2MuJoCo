import copy
import math
import runpy

import numpy as np
import pytest

from drawing_cad.cad import make_recipe
from drawing_cad.dimensions import parse_dimensions
from drawing_cad.feature_callouts import slot_callout, callout_tokens
from drawing_cad.features import recognize_features
from drawing_cad.pattern_geometry import matching_thin_projections
from drawing_cad.pipeline import run_pipeline
from orthographic_feature_fixture import draw_orthographic_features


def _token(word,box=None,confidence=1.,id='t'):
    return dict(id=id,text=word,bbox=box or [0,0,120,10],confidence=confidence,source='synthetic')


@pytest.mark.parametrize('callout',[
    '7X SLOT 27 × 11 THRU','7x slot 27 x 11 thru','7-SLOT(11x27)','7-长圆孔（11×27）',
])
def test_slot_annotations_normalize_to_same_feature(callout):
    parsed=slot_callout(_token(callout))
    assert parsed['count']['value']==7
    assert parsed['width']['value']==11 and parsed['length']['value']==27
    assert parsed['extent']=='through'
    assert parsed['inferred']==('THRU' not in callout.upper())


@pytest.mark.parametrize('callout',['7X Ø11(Ø27)','7X SLOT 27 × 11 DEPTH 3','27 × 11','SLOT unknown'])
def test_other_callouts_are_not_through_slots(callout):
    assert slot_callout(_token(callout)) is None


def test_split_words_keep_numeric_evidence_and_do_not_bridge_rows():
    words=['5X','SLOT','33','×','13','THRU'];tokens=[];x=0
    for index,word in enumerate(words):
        tokens.append(_token(word,[x,0,x+len(word)*5,10],id=f't{index}'));x+=len(word)*5+4
    matches=[t for t in callout_tokens(tokens) if slot_callout(t)]
    assert len(matches)==1
    assert matches[0]['source_text_ids']==[t['id'] for t in tokens]
    assert [n['source_text_id'] for n in matches[0]['numeric_parts']]==['t0','t2','t4']
    tokens[3]['bbox'][1:4:2]=[30,40]
    assert not any(slot_callout(t) for t in callout_tokens(tokens))


def _rectangle_lines(box,prefix):
    x,y,X,Y=box
    return [dict(id=prefix+str(i),axis=axis,coordinate=c,lo=lo,hi=hi)
            for i,(axis,c,lo,hi) in enumerate([(0,y,x,X),(0,Y,x,X),(1,x,y,Y),(1,X,y,Y)])]


def test_view_relation_rejects_wrong_projection_and_preserves_competition():
    plate=dict(bbox=[200,200,600,340])
    wrong=_rectangle_lines([700,220,710,330],'wrong')
    assert matching_thin_projections(plate,wrong)==[]
    left=_rectangle_lines([80,200,90,340],'left')
    right=_rectangle_lines([710,200,720,340],'right')
    projections=matching_thin_projections(plate,wrong+left+right)
    assert len(projections)==2  # No nearest-side / left-right priority.
    assert all(v['view_relation']['shared_axis']==1 for v in projections)
    assert matching_thin_projections(plate,right[:2])==[]  # Closing edges alone are insufficient.
    # Two dimension baselines with extensions continuing to the front view
    # must not be confused with a thin rectangular contour.
    extensions=_rectangle_lines([120,200,145,340],'dimensions')
    for line in extensions:
        if line['axis']==0:line.update(lo=110,hi=200)
    assert matching_thin_projections(plate,extensions)==[]


@pytest.fixture(scope='module',params=['left','right','above','below'])
def orthographic_result(request,tmp_path_factory):
    root=tmp_path_factory.mktemp('orthographic_'+request.param)
    expected,holes,slots=draw_orthographic_features(root/'input.pdf',request.param)
    doc,out=run_pipeline(root/'input.pdf',output=root/'out',dpi=72)
    return request.param,expected,holes,slots,doc,out


def assert_ordinary_plate(doc,out,expected,holes,slots):
    assert doc['status']=='generated_with_assumptions',doc.get('blocking_reasons',doc.get('error'))
    for name,value in expected.items():
        assert doc['parameters'][name]['nominal']==pytest.approx(value)
    assert doc['units']['default_source']=='drawing_unit_note'
    definitions=doc['features']['feature_definitions']
    assert definitions['hole']['kind']=='cylindrical_through_hole'
    assert definitions['hole']['additions']==[]
    assert definitions['slot']['kind']=='straight_slot'
    for name in ('countersink_diameter','countersink_angle'):
        assert doc['parameters'][name]['status']=='unknown'
        assert not doc['parameters'][name]['required_for_cad']
    assert not any(k.startswith('countersink_') for k in doc['recipe'])
    assert len(doc['features']['resolved_features'])==len(holes)+len(slots)
    assert all(v['definition'] in definitions for v in doc['features']['resolved_features'])
    for key,points in [('hole',holes),('slot',slots)]:
        assert np.allclose(sorted(doc['recipe'][key+'_centres_mm']),sorted((x,expected['plate_height']-y) for x,y in points))
    w,h,t=(expected[n] for n in ('plate_width','plate_height','thickness'))
    s,l,d=(expected[n] for n in ('slot_width','slot_length','hole_diameter'))
    volume=w*h*t-len(holes)*math.pi*d*d/4*t-len(slots)*(s*(l-s)+math.pi*s*s/4)*t
    assert doc['validation']['bounds_mm']==pytest.approx([w,h,t])
    assert doc['validation']['volume_mm3']==pytest.approx(volume)
    assert doc['validation']['step_reimport_valid'] and doc['validation']['stl']['watertight']
    solid=runpy.run_path(str(out/'model.py'))['result'].val()
    assert not any(f.geomType()=='CONE' for f in solid.Faces())
    assert solid.Volume()==pytest.approx(volume)
    for name in ('model.step','model.stl','preview.html','preview.png','parsed.json'):
        assert (out/name).stat().st_size>100
    preview=(out/'preview.html').read_text(encoding='utf-8')
    assert '圆柱通孔' in preview and '锥形沉孔' not in preview


def test_four_sides_with_plain_holes_and_spaced_slot_callout(orthographic_result):
    direction,expected,holes,slots,doc,out=orthographic_result
    assert_ordinary_plate(doc,out,expected,holes,slots)
    relation=next(v for v in doc['views'] if v['id']=='view_side')['relation']
    assert relation['thickness_axis']==(0 if direction in ('left','right') else 1)
    assert doc['features']['feature_definitions']['slot']['extent_source']=='explicit_thru'
    dimension=next(d for d in doc['dimensions'] if (d.get('association') or {}).get('parameter')=='slot_width')
    assert len(dimension['text_ids'])>1  # PDF words were associated, not replaced by supplied answers.


def test_plain_hole_requires_count_and_connected_leader(orthographic_result):
    _,_,_,_,doc,_=orthographic_result
    assert doc['features']['status']=='recognized'
    for change in ('wrong_count','no_leader','missing_hole_size','incomplete_countersink'):
        geometry=copy.deepcopy(doc['geometry']);texts=copy.deepcopy(doc['texts'])
        feature=recognize_features(geometry)
        if change=='wrong_count':feature['callout_requirements']['hole_count']+=1
        elif change=='no_leader':geometry.update(annotation_segments=[],segments=[])
        elif change=='missing_hole_size':texts=[t for t in texts if 'Ø' not in t['text']]
        else:texts.append(_token('CSK',id='unsupported_csk'))
        parsed=parse_dimensions(texts,geometry,feature)
        recipe,_=make_recipe(dict(features=feature,parameters=parsed['parameters']))
        assert recipe is None,change


def test_different_dimensions_and_legacy_slot_keep_same_pipeline(tmp_path):
    expected,holes,slots=draw_orthographic_features(tmp_path/'another.pdf','right',scale=1.3,legacy_slot=True)
    doc,out=run_pipeline(tmp_path/'another.pdf',output=tmp_path/'out',dpi=72)
    assert_ordinary_plate(doc,out,expected,holes,slots)
    assert doc['features']['feature_definitions']['slot']['extent_source']=='legacy_slot_callout_convention'
