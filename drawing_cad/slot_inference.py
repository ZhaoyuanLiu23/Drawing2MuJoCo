"""Explicitly recorded, scale-calibrated estimates for undimensioned slots.

This is an approximate reconstruction mode. It cannot supply manufacturing
dimensions or overwrite rejected annotations / conflicting position constraints.
"""
import math
import re

from .feature_callouts import callout_tokens


def infer_undimensioned_slots(feature,geometry,texts,dimensions,parameters,constraints):
    slots=[v for v in feature['feature_candidates'] if v['kind']=='slot']
    if not slots or feature.get('feature_definitions',{}).get('slot'): return
    if any(parameters[n]['evidence'] or parameters[n]['value_mm'] is not None for n in ('slot_length','slot_width')): return
    if any(d['kind']=='slot_callout' for d in feature.get('callout_diagnostics',[])): return
    # Malformed numeric slot callouts are unresolved evidence, not missing data.
    if any(re.search(r'\bSLOT\b',t['text'],re.I) and re.search(r'\d',t['text']) for t in callout_tokens(texts)):
        feature['geometry_inference_blocked_reason']='Unresolved numeric slot annotation; missing dimensions cannot be presumed.'
        return
    if re.search(r'NOT\s+TO\s+SCALE|\bN\.?T\.?S\.?\b',' '.join(t['text'] for t in texts),re.I):
        feature['geometry_inference_blocked_reason']='Drawing explicitly says not to scale.'
        return
    base={n:parameters[n]['value_mm'] for n in ('plate_width','plate_height','thickness')}
    if any(v is None or not math.isfinite(v) or v<=0 for v in base.values()): return
    front=next(v for v in feature['views'] if v['id']=='view_front')['bbox']
    x0,y0,x1,y1=front
    sx=base['plate_width']/(x1-x0);sy=base['plate_height']/(y1-y0)
    if abs(sx-sy)/max(sx,sy)>.05:
        feature['geometry_inference_blocked_reason']='Horizontal and vertical annotation scales disagree by more than 5%.'
        return
    calibration=dict(method='front_view_extents_calibrated_by_annotated_plate_dimensions',
        plate_bbox_px=front,mm_per_pixel_x=sx,mm_per_pixel_y=sy,
        parameters={n:dict(value_mm=parameters[n]['value_mm'],dimension_ids=parameters[n]['dimension_ids'])
                    for n in ('plate_width','plate_height')},
        assumption='Unspecified geometry is drawn to scale in this orthographic view.')
    feature.update(mode='inferred_geometry',geometry_calibration=calibration)
    feature['assumptions'].append('Undimensioned slot sizes and unannotated locations are estimated from detected geometry using annotated plate scale; these are approximate, not specified nominal dimensions. Slots are interpreted as through cuts.')
    feature['feature_definitions']['slot']=dict(kind='straight_slot',parameter_scope='per_feature',
        extent='through',extent_source='inferred_from_closed_internal_contour',inferred=True)
    for name in ('slot_length','slot_width'):
        parameters[name]['required_for_cad']=False
        feature['required_parameters'].remove(name)
        if name not in feature['optional_parameters']:feature['optional_parameters'].append(name)

    def inferred_parameter(name,value,evidence):
        parameters[name]=dict(status='inferred',source='inferred',value_source='inferred_geometry',
            value_mm=float(value),nominal=None,min=None,max=None,nominal_mm=None,limits_mm=None,
            tolerance_mm=None,inferred=True,confidence=.55,unit='mm',quantity='length',
            evidence=[dict(evidence,source='detected',calibration=calibration)],dimension_ids=[],required_for_cad=True,
            reason='Estimated from detected orthographic geometry, calibrated by explicit plate dimensions; no nominal or tolerance is specified.')
        if name not in feature['required_parameters']:feature['required_parameters'].append(name)

    # Only completely unannotated axes may use image coordinates. A partial
    # or conflicting chain remains subject to the existing constraint gate.
    for key in ('slot_x','slot_y'):
        spec=feature['position_axes'][key]
        chain=next(c for c in constraints if c['id']==key+'_chain')
        for axis,name in zip(spec['axes'],spec['position_parameters']):
            p=parameters[name]
            if p['value_mm'] is not None: continue
            if chain['status']=='conflict' or any(v is not None for v in chain['raw_values']): continue
            if any(parameters[n]['evidence'] for n in spec['chain_parameters']): continue
            value=(axis['coordinate_px']-(x0 if key=='slot_x' else y0))*(sx if key=='slot_x' else sy)
            inferred_parameter(name,value,dict(geometry_ids=axis['geometry_ids'],detected_coordinate_px=axis['coordinate_px']))
            parameters[name].update(resolution_status='inferred_geometry',constraint_id=chain['id'])

    for candidate in slots:
        a,b,A,B=candidate['detected_bbox_px'];prefix=candidate['id']
        names=dict(length=prefix+'_length',width=prefix+'_width')
        for kind,value in (('length',(A-a)*sx),('width',(B-b)*sy)):
            inferred_parameter(names[kind],value,dict(geometry_id=candidate['geometry_id'],
                                                     detected_bbox_px=candidate['detected_bbox_px']))
        resolved=next(v for v in feature['resolved_features'] if v['id']==prefix)
        x=parameters[resolved['x_parameter']];y=parameters[resolved['y_parameter']]
        ok=x['value_mm'] is not None and y['value_mm'] is not None
        resolved.update(length_parameter=names['length'],width_parameter=names['width'],source='inferred',
                        x_mm=x['value_mm'],y_from_top_mm=y['value_mm'],
                        status='resolved' if ok else 'ambiguous',confidence=min(.55,x['confidence'],y['confidence']))
    feature['resolution_status']='resolved' if all(v['status']=='resolved' for v in feature['resolved_features']) else 'ambiguous'
    feature['resolution_blocking_reasons']=[f"{v['id']}: unresolved X/Y position constraint" for v in feature['resolved_features'] if v['status']!='resolved']
