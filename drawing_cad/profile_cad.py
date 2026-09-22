"""A straight-edge/quarter-arc profile, extruded and cut by a located through hole."""
import math


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


def profile_dimensions(parameters,feature):
    values={name:parameters[name]['value_mm'] for name in feature['required_parameters']}
    w,h,r=(values[name] for name in ('width','height','corner_radius'))
    x,y,d=(values[name] for name in ('hole_x','hole_y_from_top','hole_diameter'))
    if not all(math.isfinite(v) and v>0 for v in values.values()):
        raise ValueError('Profile dimensions must be finite and positive')
    if not r<min(w,h) or not d/2<x<w-d/2 or not d/2<y<h-d/2:
        raise ValueError('Radius or hole placement is outside the profile envelope')
    return dict({name+'_mm':value for name,value in values.items()},
                corner_index=feature['corner_index'],
                datum='CAD origin: front-view bottom-left; X right, Y up; Z through thickness.',
                hole_depth='through',profile_edges='four straight edges and one tangent quarter-circle')


def profile_expected(recipe):
    w,h,t,r,d=(recipe[k+'_mm'] for k in ('width','height','thickness','corner_radius','hole_diameter'))
    volume=(w*h-(1-math.pi/4)*r*r-math.pi*d*d/4)*t
    return [w,h,t],volume
