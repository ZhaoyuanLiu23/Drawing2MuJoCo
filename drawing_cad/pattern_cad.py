"""Rectangular extrusion, straight slots and through holes with optional countersinks."""
import math


def pattern_dimensions(parameters,feature):
    def value(name): return parameters[name]['value_mm']
    w,h,t=(value(n) for n in ('plate_width','plate_height','thickness'))
    inferred=feature.get('mode')=='inferred_geometry'
    slot_features=[v for v in feature['resolved_features'] if v['kind']=='slot']
    if inferred:
        sizes=[[value(v['length_parameter']),value(v['width_parameter'])] for v in slot_features]
        slot_dimensions=dict(slot_sizes_mm=sizes)
    else:
        length,width=(value(n) for n in ('slot_length','slot_width'))
        sizes=[[length,width] for v in slot_features]
        slot_dimensions=dict(slot_length_mm=length,slot_width_mm=width)
    diameter=value('hole_diameter') if feature['holes'] else None
    hole=feature.get('feature_definitions',{}).get('hole')
    if feature['holes'] and (not hole or hole['kind']!='cylindrical_through_hole'):
        raise ValueError('A supported through-hole annotation is required.')
    lengths=[w,h,t]+[v for size in sizes for v in size]+([diameter] if feature['holes'] else [])
    if not all(v is not None and math.isfinite(v) and v>0 for v in lengths):
        raise ValueError('All pattern dimensions must be finite and positive.')
    if any(width>=length for length,width in sizes): raise ValueError('Slot length must exceed width.')
    additions={}; opening=diameter
    if hole and hole['additions']:
        large=value('countersink_diameter'); angle=parameters['countersink_angle']['value_degrees']
        if not all(v is not None and math.isfinite(v) for v in (large,angle)) or not (diameter<large and 0<angle<180):
            raise ValueError('Require increasing countersink diameters and 0 < included angle < 180.')
        depth=(large-diameter)/(2*math.tan(math.radians(angle/2)))
        if depth>=t: raise ValueError('Countersink depth reaches or exceeds the plate thickness.')
        opening=large
        additions=dict(countersink_diameter_mm=large,countersink_angle_deg=angle,countersink_depth_mm=depth,
                       countersink_depth_source='derived_from_diameters_and_included_angle')
    unresolved=[v['id'] for v in feature.get('resolved_features',[]) if v['status']!='resolved']
    if unresolved: raise ValueError('Unresolved feature positions: '+', '.join(unresolved))
    holes=[[v['x_mm'],h-v['y_from_top_mm']] for v in feature['resolved_features'] if v['kind']=='circle']
    centres=[[v['x_mm'],h-v['y_from_top_mm']] for v in feature['resolved_features'] if v['kind']=='slot']
    if any(not (opening/2<x<w-opening/2 and opening/2<y<h-opening/2) for x,y in holes):
        raise ValueError('A hole or its countersink crosses the outer boundary.')
    if any(not (length/2<x<w-length/2 and width/2<y<h-width/2) for (x,y),(length,width) in zip(centres,sizes)):
        raise ValueError('A slot crosses the outer boundary.')
    return dict(width_mm=w,height_mm=h,thickness_mm=t,**slot_dimensions,
                hole_diameter_mm=diameter,hole_centres_mm=holes,slot_centres_mm=centres,
                mode='inferred_geometry' if inferred else 'dimension_constraints',
                feature_definitions=feature['feature_definitions'],**additions)


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


def pattern_expected(recipe):
    w,h,t=(recipe[n+'_mm'] for n in ('width','height','thickness'))
    sizes=recipe.get('slot_sizes_mm')
    if sizes is None:sizes=[(recipe['slot_length_mm'],recipe['slot_width_mm']) for _ in recipe['slot_centres_mm']]
    slot_volume=sum((s*(l-s)+math.pi*s*s/4)*t for l,s in sizes)
    hole_volume=0.
    if recipe['hole_centres_mm']:
        r=recipe['hole_diameter_mm']/2
        hole_volume=math.pi*r*r*t
        if recipe['feature_definitions']['hole']['additions']:
            R=recipe['countersink_diameter_mm']/2; z=recipe['countersink_depth_mm']
            hole_volume+=math.pi*z/3*(R*R+R*r+r*r)-math.pi*r*r*z
    return [w,h,t],w*h*t-slot_volume-len(recipe['hole_centres_mm'])*hole_volume
