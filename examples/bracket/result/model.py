"""Editable CadQuery model. Edit parameters.json, then run in a CAD environment."""
import json
import math
from pathlib import Path
import cadquery as cq

def circular_profile_extrusion(recipe):
    import cadquery as cq
    profile=cq.Workplane("XY").circle(recipe["outer_diameter_mm"]/2)
    for diameter in recipe["hole_diameters_mm"]:
        profile=profile.circle(diameter/2)
    return profile.extrude(recipe["thickness_mm"])

def line_arc_profile_extrusion(recipe):
    import cadquery as cq
    w,h,t=(recipe[k+'_mm'] for k in ('width','height','thickness'))
    r=recipe['corner_radius_mm']
    x,y,d=(recipe[k+'_mm'] for k in ('hole_x','hole_y_from_top','hole_diameter'))
    if not all(math.isfinite(v) and v>0 for v in (w,h,t,r,x,y,d)):
        raise ValueError('Profile dimensions must be finite and positive')
    if not r<min(w,h) or not d/2<x<w-d/2 or not d/2<y<h-d/2:
        raise ValueError('Radius or hole placement is outside the profile envelope')
    # Corner indices follow image coordinates: top-left, top-right, bottom-right,
    # bottom-left. CAD datum is bottom-left, with Z the extrusion direction.
    corners=[(0,h),(w,h),(w,0),(0,0)]
    index=recipe['corner_index']
    if index not in range(4):
        raise ValueError('Unknown rounded corner')
    vertex=corners[index]
    def unit_to(point):
        delta=[point[k]-vertex[k] for k in range(2)]
        length=math.hypot(*delta)
        return [v/length for v in delta]
    u,v=unit_to(corners[(index-1)%4]),unit_to(corners[(index+1)%4])
    entry=tuple(vertex[k]+r*u[k] for k in range(2))
    end=tuple(vertex[k]+r*v[k] for k in range(2))
    middle=tuple(vertex[k]+r*(u[k]+v[k])*(1-1/math.sqrt(2)) for k in range(2))
    profile=cq.Workplane('XY').moveTo(*end)
    for offset in (1,2,3):
        profile=profile.lineTo(*corners[(index+offset)%4])
    body=profile.lineTo(*entry).threePointArc(middle,end).close().extrude(t)
    hole=cq.Workplane('XY').center(x,h-y).circle(d/2).extrude(t)
    return body.cut(hole)

def rectangular_plate_feature_pattern(recipe):
    import cadquery as cq
    w,h,t=(recipe[n+'_mm'] for n in ('width','height','thickness'))
    body=cq.Workplane('XY').moveTo(0,0).lineTo(w,0).lineTo(w,h).lineTo(0,h).close().extrude(t)
    for index,(x,y) in enumerate(recipe['slot_centres_mm']):
        length,width=recipe['slot_sizes_mm'][index] if 'slot_sizes_mm' in recipe else (recipe['slot_length_mm'],recipe['slot_width_mm'])
        cut=cq.Workplane('XY').center(x,y).slot2D(length,width).extrude(t)
        body=body.cut(cut)
    for x,y in recipe['hole_centres_mm']:
        small=recipe['hole_diameter_mm']/2
        body=body.cut(cq.Workplane('XY').center(x,y).circle(small).extrude(t))
        if recipe['feature_definitions']['hole']['additions']:
            large=recipe['countersink_diameter_mm']/2
            depth=(large-small)/math.tan(math.radians(recipe['countersink_angle_deg']/2))
            cone=cq.Solid.makeCone(small,large,depth,cq.Vector(x,y,t-depth),cq.Vector(0,0,1))
            body=body.cut(cone)
    return body


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
