"""Resolve generic feature candidates from explicit dimension/geometric constraints."""
import math


def resolve_pattern_constraints(feature,dimensions,parameters,unit):
    constraints=[];by_id={d['id']:d for d in dimensions};required=set(feature['required_parameters'])
    for name,p in parameters.items(): p['required_for_cad']=name in required

    def raw(name):
        records=[by_id[i] for i in parameters[name]['dimension_ids']]
        if not records or any(d['confidence']<.9 for d in records): return None
        values=[d['value'] for d in records]
        return values[0] if all(math.isclose(v,values[0],rel_tol=1e-8) for v in values) else None

    def derived(name,value,source,names,confidence,reason,**extra):
        evidence=[e for n in names for e in parameters[n]['evidence']]
        parameters[name]=dict(status='inferred' if value is not None else 'unknown',value_mm=value,
            value_source=source if value is not None else None,nominal=None,min=None,max=None,nominal_mm=None,
            limits_mm=None,tolerance_mm=None,inferred=value is not None,confidence=confidence if value is not None else 0.,
            evidence=evidence,dimension_ids=list(dict.fromkeys(e['dimension_id'] for e in evidence)),
            required_for_cad=True,reason=reason,**extra)

    def resolve_axis(key,total_name):
        spec=feature['position_axes'][key];names=spec['chain_parameters'];values=[raw(n) for n in names]
        total_raw=raw(total_name);complete=total_raw is not None and all(v is not None for v in values)
        summed=sum(values) if complete else None
        status=('consistent' if math.isclose(summed,total_raw,rel_tol=1e-6,abs_tol=1e-8) else 'conflict') if complete else 'unknown'
        item=dict(id=f'{key}_chain',kind='dimension_chain',status=status,unit=unit or 'unknown',raw_values=values,
                  chain_parameters=names,total_parameter=total_name,direct_total=total_raw,chain_sum=summed,
                  difference=summed-total_raw if complete else None,
                  evidence=[dict(parameter=n,dimension_ids=parameters[n]['dimension_ids'],
                                 raw_annotations=[by_id[i]['raw_text'] for i in parameters[n]['dimension_ids']])
                            for n in [total_name]+names],
                  policy='Preserve all annotations. Resolve only from an agreeing chain, one complete datum path, a geometry-disambiguated conflict, or an explicit centre-line constraint.')
        constraints.append(item)
        physical_total=parameters[total_name]['value_mm'];pixel_lo,pixel_hi=spec['plate_span_px'];position_names=[]
        # Raw annotations stay in the conflict record; CAD paths use normalized
        # lengths, never raw cm/in values mixed with a millimetre total.
        scaled=[parameters[n]['value_mm'] if raw(n) is not None else None for n in names]
        for axis in spec['axes']:
            index=axis['index'];name=f'{key}_{index}';position_names.append(name)
            prefix=scaled[:index+1];suffix=scaled[index+1:]
            forward=sum(prefix) if physical_total is not None and all(v is not None for v in prefix) else None
            reverse=physical_total-sum(suffix) if physical_total is not None and all(v is not None for v in suffix) else None
            candidates=[]
            if forward is not None:candidates.append(dict(value_mm=forward,datum='start_edge'))
            if reverse is not None:candidates.append(dict(value_mm=reverse,datum='end_edge'))
            chosen=None;source=None;confidence=0.;resolution='ambiguous'
            reason='Position lacks a complete, unambiguous constraint path.'
            if forward is not None and reverse is not None and math.isclose(forward,reverse,rel_tol=1e-6,abs_tol=1e-8):
                chosen=(forward+reverse)/2;source='derived_dimension_chain';confidence=.85;resolution='resolved'
                reason='Start/end datum paths agree.'
            elif status=='conflict' and forward is not None and reverse is not None and physical_total is not None:
                geometric=(axis['coordinate_px']-pixel_lo)/(pixel_hi-pixel_lo)*physical_total
                distances=[abs(forward-geometric),abs(reverse-geometric)];tolerance=max(.5,physical_total*.02)
                best=int(distances[1]<distances[0])
                if abs(distances[0]-distances[1])>tolerance and distances[best]<=max(2,physical_total*.08):
                    chosen=(forward,reverse)[best];source='constraint_conflict_resolved_by_detected_geometry';confidence=.60
                    resolution='resolved_by_geometry'
                    reason='Conflicting datum paths are preserved; detected feature geometry uniquely selects one candidate.'
            elif (forward is None)!=(reverse is None):
                chosen=forward if forward is not None else reverse;source='derived_single_datum_path';confidence=.78
                resolution='resolved';reason='One complete annotated path from an edge datum determines the position.'
            if chosen is None and not candidates and axis.get('centerline_constraint') and physical_total is not None:
                chosen=physical_total/2;source='drawing_centerline_constraint';confidence=.80;resolution='resolved'
                reason='Detected feature centre coincides with the explicitly drawn plate centre line.'
            derived(name,chosen,source,[total_name]+names,confidence,reason,resolution_status=resolution,
                    constraint_id=item['id'],candidates=candidates,detected_coordinate_px=axis['coordinate_px'],
                    geometry_ids=axis['geometry_ids'],geometric_evidence=axis.get('centerline_evidence'))
        item['position_parameters']=position_names
        if status=='conflict':item['affected_position_parameters']=position_names
        return position_names

    positions={key:resolve_axis(key,'plate_width' if spec['axis']==0 else 'plate_height')
               for key,spec in feature['position_axes'].items()}
    for key,names in positions.items():feature['position_axes'][key]['position_parameters']=names
    resolved=[]
    for candidate in feature['feature_candidates']:
        prefix='hole' if candidate['kind']=='circle' else 'slot'
        x_name=positions[f'{prefix}_x'][candidate['x_axis_index']];y_name=positions[f'{prefix}_y'][candidate['y_axis_index']]
        x=parameters[x_name]['value_mm'];y_top=parameters[y_name]['value_mm'];ok=x is not None and y_top is not None
        resolved.append(dict(id=candidate['id'],kind=candidate['kind'],geometry_id=candidate['geometry_id'],
            definition=candidate.get('definition'),
            status='resolved' if ok else 'ambiguous',x_parameter=x_name,y_parameter=y_name,x_mm=x,y_from_top_mm=y_top,
            confidence=min(candidate['confidence'],parameters[x_name]['confidence'],parameters[y_name]['confidence']),
            evidence=dict(detected_center_px=candidate['detected_center_px'],x_constraint=x_name,y_constraint=y_name)))
    feature['resolved_features']=resolved
    feature['resolution_status']='resolved' if all(v['status']=='resolved' for v in resolved) else 'ambiguous'
    feature['resolution_blocking_reasons']=[f"{v['id']}: unresolved X/Y position constraint" for v in resolved if v['status']!='resolved']
    return constraints
