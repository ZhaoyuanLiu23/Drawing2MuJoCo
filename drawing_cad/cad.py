"""Validated parametric CAD builders and neutral-file exports (all lengths in mm)."""
import json
import math
from pathlib import Path
import struct
from collections import Counter

import numpy as np
from .profile_cad import line_arc_profile_extrusion, profile_dimensions, profile_expected
from .pattern_cad import rectangular_plate_feature_pattern, pattern_dimensions, pattern_expected


def make_recipe(document):
    feature=document["features"]
    parameters=document["parameters"]
    if feature["status"]!="recognized":
        return None, [feature.get("reason","Geometry is unknown.")]
    missing=[k for k,p in parameters.items() if p.get('required_for_cad',True)
             and (p.get('value_degrees' if p.get('quantity')=='angle' else 'value_mm') is None or p["status"]=="unknown")]
    if missing:
        return None, [f"Unknown required parameter: {k}. {parameters[k]['reason']}" for k in missing]
    if feature['recognizer'] in ('line_arc_profile_extrusion','rectangular_plate_feature_pattern'):
        try:
            if feature['recognizer']=='rectangular_plate_feature_pattern' and feature.get('resolution_status')!='resolved':
                return None,feature.get('resolution_blocking_reasons') or ['Feature positions are ambiguous.']
            dimensions=(pattern_dimensions if feature['recognizer']=='rectangular_plate_feature_pattern' else profile_dimensions)(parameters,feature)
        except ValueError as exc:
            return None,[str(exc)]
    else:
        outer=parameters["outer_diameter"]["value_mm"]
        inner=parameters["inner_diameter"]["value_mm"] if feature["holes"] else None
        thickness=parameters["thickness"]["value_mm"]
        if not all(math.isfinite(v) and v>0 for v in (outer,thickness)) or (inner is not None and not 0<inner<outer):
            return None, ["Invalid dimensions: require positive thickness and outer diameter, and 0 < hole diameter < outer diameter."]
        dimensions=dict(outer_diameter_mm=outer,hole_diameters_mm=[] if inner is None else [inner],thickness_mm=thickness)
    dimension_usage={name:dict(used_value_mm=p["value_mm"],value_source=p["value_source"],
                               source=p.get('source'),
                               nominal=p["nominal"],min=p["min"],max=p["max"],
                               inferred=p["inferred"],confidence=p["confidence"],evidence=p["evidence"])
                     for name,p in parameters.items()}
    for name,p in parameters.items():
        if p.get('quantity')=='angle':
            dimension_usage[name].update(quantity='angle',unit='deg',used_value_degrees=p['value_degrees'])
    inferred_names=[name for name,p in dimension_usage.items() if p['inferred']]
    return dict(builder=feature["recognizer"],unit="mm",**dimensions,
                contains_inferred_dimensions=bool(inferred_names),inferred_dimension_names=inferred_names,
                dimension_usage=dimension_usage,parameter_evidence=parameters,
                constraints=document.get('constraints',[]),conflicts=document.get('conflicts',[]),
                unit_provenance=document.get('units',{}),assumptions=feature["assumptions"]), []


def circular_profile_extrusion(recipe):
    import cadquery as cq
    profile=cq.Workplane("XY").circle(recipe["outer_diameter_mm"]/2)
    for diameter in recipe["hole_diameters_mm"]:
        profile=profile.circle(diameter/2)
    return profile.extrude(recipe["thickness_mm"])


BUILDERS={"circular_profile_extrusion":circular_profile_extrusion,
          "line_arc_profile_extrusion":line_arc_profile_extrusion,
          "rectangular_plate_feature_pattern":rectangular_plate_feature_pattern}


def _bounds(solid):
    b=solid.BoundingBox()
    return [b.xlen,b.ylen,b.zlen]


def inspect_stl(path):
    """Validate the ACTUAL binary STL, including watertight edge incidence."""
    raw=Path(path).read_bytes()
    if len(raw)<84:
        raise ValueError("Truncated STL")
    count=struct.unpack_from("<I",raw,80)[0]
    if len(raw)!=84+50*count or count==0:
        raise ValueError("Invalid binary STL triangle data")
    dtype=np.dtype([("normal","<f4",(3,)),("vertices","<f4",(3,3)),("attribute","<u2")])
    triangles=np.frombuffer(raw,dtype=dtype,offset=84,count=count)["vertices"].astype(float)
    if not np.isfinite(triangles).all():
        raise ValueError("STL contains non-finite coordinates")
    edges=Counter()
    for triangle in np.round(triangles,6):
        for a,b in ((triangle[0],triangle[1]),(triangle[1],triangle[2]),(triangle[2],triangle[0])):
            edges[tuple(sorted((tuple(a),tuple(b))))]+=1
    if any(n!=2 for n in edges.values()):
        raise ValueError("Exported STL is not a closed manifold")
    volume=abs(float(np.einsum("ij,ij->i",triangles[:,0],np.cross(triangles[:,1],triangles[:,2])).sum()/6))
    return dict(triangles=count,watertight=True,volume_mm3=volume,
                bounds_mm=np.ptp(triangles.reshape(-1,3),axis=0).tolist())


def export_cad(recipe, output):
    import cadquery as cq
    result=BUILDERS[recipe["builder"]](recipe)
    solid=result.val()
    if not solid.isValid() or len(result.solids().vals())!=1:
        raise ValueError("CAD kernel did not produce one valid solid")
    if recipe['builder']=='line_arc_profile_extrusion':
        expected_bounds,expected_volume=profile_expected(recipe)
    elif recipe['builder']=='rectangular_plate_feature_pattern':
        expected_bounds,expected_volume=pattern_expected(recipe)
    else:
        outer=recipe["outer_diameter_mm"]
        thickness=recipe["thickness_mm"]
        expected_volume=math.pi/4*(outer*outer-sum(d*d for d in recipe["hole_diameters_mm"]))*thickness
        expected_bounds=[outer,outer,thickness]
    span=max(expected_bounds)
    # Save exact pre-mesh bounds: OpenCASCADE's cached triangulation can inflate
    # BoundingBox by its deflection after exportStl/tessellate.
    validated_bounds=_bounds(solid)
    if not np.allclose(validated_bounds,expected_bounds,rtol=1e-6,atol=1e-7):
        raise ValueError("CAD bounds disagree with parsed parameters")
    if not math.isclose(solid.Volume(),expected_volume,rel_tol=1e-8):
        raise ValueError("CAD volume disagrees with the feature recipe")
    # OpenCASCADE native exporters can fail on Unicode paths on Windows. Write to
    # a temporary ASCII filename under the system temp directory, then copy via Python.
    import tempfile
    import shutil
    with tempfile.TemporaryDirectory(prefix="drawing_cad_") as temporary:
        step_path=Path(temporary)/"model.step"
        stl_path=Path(temporary)/"model.stl"
        cq.exporters.export(result,str(step_path))
        solid.exportStl(str(stl_path),tolerance=max(span*1e-4,1e-5),angularTolerance=.05,relative=False)
        reimport=cq.importers.importStep(str(step_path)).val()
        if not reimport.isValid() or not np.allclose(_bounds(reimport),expected_bounds,atol=1e-6):
            raise ValueError("STEP reimport verification failed")
        if not math.isclose(reimport.Volume(),expected_volume,rel_tol=1e-7):
            raise ValueError("STEP volume verification failed")
        stl_check=inspect_stl(stl_path)
        if not math.isclose(stl_check["volume_mm3"],expected_volume,rel_tol=.005):
            raise ValueError("STL volume deviates by more than 0.5%")
        shutil.copy2(step_path,output/"model.step")
        shutil.copy2(stl_path,output/"model.stl")
    vertices,triangles=solid.tessellate(max(span*1e-4,1e-5),.05)
    mesh=dict(vertices=[[v.x,v.y,v.z] for v in vertices],triangles=[list(t) for t in triangles])
    (output/"parameters.json").write_text(json.dumps(recipe,indent=2,ensure_ascii=False),encoding="utf-8")
    (output/"mesh.json").write_text(json.dumps(mesh),encoding="utf-8")
    import inspect
    builders='\n'.join(inspect.getsource(builder) for builder in BUILDERS.values())
    (output/"model.py").write_text(REBUILD_SCRIPT.replace('# __BUILDERS__',builders),encoding="utf-8")
    return mesh,dict(valid_solid=True,solid_count=1,volume_mm3=solid.Volume(),bounds_mm=validated_bounds,
                     step_reimport_valid=True,step_reimport_volume_mm3=reimport.Volume(),stl=stl_check)


REBUILD_SCRIPT='''"""Editable CadQuery model. Edit parameters.json, then run in a CAD environment."""
import json
import math
from pathlib import Path
import cadquery as cq

# __BUILDERS__

root = Path(__file__).resolve().parent
p = json.loads((root / "parameters.json").read_text(encoding="utf-8"))
assert p["unit"] == "mm"
if p['builder']=='line_arc_profile_extrusion':
    result=line_arc_profile_extrusion(p)
elif p['builder']=='rectangular_plate_feature_pattern':
    result=rectangular_plate_feature_pattern(p)
elif p['builder']=='circular_profile_extrusion':
    d,h=p['outer_diameter_mm'],p['thickness_mm']
    assert math.isfinite(d) and math.isfinite(h) and d>0 and h>0
    assert len(p['hole_diameters_mm'])<=1
    assert all(math.isfinite(v) and 0<v<d for v in p['hole_diameters_mm'])
    result=circular_profile_extrusion(p)
else:
    raise ValueError('Unsupported profile builder')
assert result.val().isValid() and len(result.solids().vals())==1
if __name__ == "__main__":
    import tempfile, shutil
    with tempfile.TemporaryDirectory(prefix="cad_rebuild_") as temporary:
        for extension in ("step", "stl"):
            target = Path(temporary) / ("model." + extension)
            cq.exporters.export(result, str(target))
            shutil.copy2(target, root / target.name)
    print("Rebuilt STEP/STL from parameters.json; original parsing evidence and previews were not changed.")
'''
