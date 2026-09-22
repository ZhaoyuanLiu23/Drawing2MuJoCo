import copy
import pytest
from drawing_cad.dimensions import parse_number, parse_dimensions
from drawing_cad.features import recognize_features


@pytest.mark.parametrize("text,value,unit,tolerance", [
    ("Ø12.5mm",12.5,"mm",None), (".75 in",.75,"in",None),
    ("20 ± 0.2 mm",20,"mm",.2), ("3,2 cm",3.2,"cm",None),
    ("100.00mm±0.20",100,"mm",.2), ("25.00 mm ± 0.20 mm",25,"mm",.2),
    ("6.00mm±0.50",6,"mm",.5), ("8 cm ± 0.1",8,"cm",.1)])
def test_number_parser(text,value,unit,tolerance):
    number=parse_number(text)
    assert number["value"] == value
    assert number["unit"] == unit
    assert number["tolerance"] == tolerance


@pytest.mark.parametrize("text", ["M4", "REV 2", "95610A014", "4x3mm", "1/4 inch", "Runknown", "nan mm", "10mm±0.2cm"])
def test_non_dimensions_are_not_guessed(text):
    assert parse_number(text) is None


def evidence(values, confidence=1):
    geometry={"axis_lines":[
        dict(id="left",axis=1,coordinate=10,lo=20,hi=180),
        dict(id="right",axis=1,coordinate=110,lo=20,hi=180),
        dict(id="baseline",axis=0,coordinate=150,lo=10,hi=110)]}
    feature={"measurements":[dict(parameter="outer_diameter",geometry_id="outer",axis=0,lo=10,hi=110)]}
    texts=[dict(id=f"text_{i}",text=text,bbox=box,confidence=confidence,source="ocr")
           for i,(text,box) in enumerate(values)]
    return texts,geometry,feature


def test_low_confidence_is_unknown():
    args=evidence([("20mm",[40,140,80,152])],.7)
    p=parse_dimensions(*args)["parameters"]["outer_diameter"]
    assert p["status"]=="unknown" and p["value_mm"] is None


@pytest.mark.parametrize('label',['20 ± 0.2 mm','20mm±0.2'])
def test_nominal_and_symmetric_tolerance_semantics(label):
    args=evidence([(label,[30,140,90,152])])
    p=parse_dimensions(*args)["parameters"]["outer_diameter"]
    assert p["nominal"] == 20
    assert p["min"] == pytest.approx(19.8)
    assert p["max"] == pytest.approx(20.2)
    assert p["value_source"] == "drawing_nominal"
    assert p["inferred"] is False
    assert p["evidence"][0]["raw_text"] == label


def test_conflicting_dimensions_are_unknown():
    args=evidence([("20mm",[40,139,80,151]), ("22mm",[100,139,140,151])])
    p=parse_dimensions(*args)["parameters"]["outer_diameter"]
    assert p["status"]=="unknown" and p["value_mm"] is None


def test_eccentric_or_multiple_holes_not_silently_flattened():
    outer=dict(id="outer",kind="circle",center=[200,200],radius=100,bbox=[100,100,300,300],confidence=.99)
    hole=dict(id="hole",kind="circle",center=[240,200],radius=30,bbox=[210,170,270,230],confidence=.99)
    side=dict(id="side",kind="rectangle",bbox=[450,100,470,300],confidence=.99)
    assert recognize_features({"primitives":[outer,hole,side]})["status"]=="unknown"
    hole.update(center=[200,200],bbox=[170,170,230,230])
    extra=dict(hole,id="extra",radius=20)
    assert recognize_features({"primitives":[outer,hole,extra,side]})["status"]=="unknown"
