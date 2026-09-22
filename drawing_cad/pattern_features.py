"""Turn detected plate geometry into generic circle/slot feature candidates."""
import numpy as np


def _axes(items,axis,tolerance):
    """Cluster feature centres into annotation axes without assuming a grid."""
    groups=[]
    for item in sorted(items,key=lambda v:v['center'][axis]):
        group=next((g for g in groups if abs(np.mean([x['center'][axis] for x in g])-item['center'][axis])<tolerance),None)
        if group is None: groups.append([item])
        else: group.append(item)
    return [dict(index=i,coordinate_px=float(np.mean([v['center'][axis] for v in group])),
                 geometry_ids=[v['id'] for v in group]) for i,group in enumerate(groups)]


def _same_layout(first,second):
    matches=0;total=0
    for index in (1,3):
        a=[v['center'] for v in first[index]];b=[v['center'] for v in second[index]]
        if len(a)!=len(b): return False
        unused=set(range(len(b)))
        for point in a:
            match=next((i for i in unused if np.allclose(point,b[i],atol=4)),None)
            if match is not None:unused.remove(match);matches+=1
        total+=len(a)
    # A nested dimension rectangle can introduce one noisy Hough vote while
    # preserving the rest of the layout.  Treat it as the same layout only
    # when the large majority of typed feature centres still agree.
    return total>0 and matches/total>=.8


def _layout_subset(subset,superset):
    """Return true when every typed feature centre occurs in a larger layout."""
    strict=False
    for index in (1,3):
        small=[v['center'] for v in subset[index]];large=[v['center'] for v in superset[index]]
        if len(small)>len(large): return False
        if len(small)<len(large): strict=True
        if any(not any(np.allclose(a,b,atol=4) for b in large) for a in small): return False
    return strict


def _contains(outer,inner):
    a=outer[0]['bbox'];b=inner[0]['bbox']
    return all(a[k]<=b[k]+2 for k in (0,1)) and all(a[k]>=b[k]-2 for k in (2,3))


class PatternPlateRecognizer:
    name='rectangular_plate_feature_pattern'

    def recognize(self,geometry):
        shapes=geometry['primitives']; candidates=[]
        for plate in (p for p in shapes if p['kind']=='rectangle'):
            x0,y0,x1,y1=plate['bbox']; width=x1-x0; height=y1-y0
            if width<=2*height: continue
            slots=[p for p in shapes if p['kind']=='capsule' and p.get('plate_bbox')==plate['bbox']]
            circles=[p for p in shapes if p['kind']=='small_circle' and p.get('plate_bbox')==plate['bbox']]
            sides=[p for p in shapes if p['kind']=='thin_rectangle' and p.get('plate_bbox')==plate['bbox']]
            if slots and len(sides)==1:
                candidates.append((plate,sorted(slots,key=lambda v:(v['center'][1],v['center'][0])),
                                   sides[0],sorted(circles,key=lambda v:(v['center'][1],v['center'][0]))))
        candidates=[candidate for candidate in candidates if not any(other is not candidate and (
            (_contains(other,candidate) and _layout_subset(candidate,other))
            or (_contains(candidate,other) and _same_layout(candidate,other)
                and other[0]['bbox']!=candidate[0]['bbox'])) for other in candidates)]
        if len(candidates)!=1:
            return dict(status='unknown',recognizer=self.name,views=[],measurements=[],
                        reason='Need one unambiguous rectangular plate, one aligned thin side projection, and detected straight slots with optional circles.')
        plate,slots,side,circles=candidates[0]
        x0,y0,x1,y1=plate['bbox']; a,b,c,d=side['bbox']; width=x1-x0;height=y1-y0
        circle_x=_axes(circles,0,max(3,width*.025));circle_y=_axes(circles,1,max(3,height*.05))
        slot_x=_axes(slots,0,max(3,width*.025));slot_y=_axes(slots,1,max(3,height*.05))
        for axis in slot_y:
            collinear=[line for line in geometry['axis_lines'] if line['axis']==0
                       and abs(line['coordinate']-axis['coordinate_px'])<max(2,height*.02)]
            crosses_plate=collinear and min(line['lo'] for line in collinear)<=x0+width*.08 \
                and max(line['hi'] for line in collinear)>=x1-width*.08
            axis['centerline_constraint']=bool(abs(axis['coordinate_px']-(y0+y1)/2)<max(2,height*.025) and crosses_plate)
            if axis['centerline_constraint']:
                axis['centerline_evidence']=dict(kind='detected_plate_midline',
                    line_ids=[line['id'] for line in collinear],coordinate_px=axis['coordinate_px'],
                    plate_midline_px=(y0+y1)/2,inferred=True,
                    interpretation='Collinear strokes span the plate at its geometric midline and coincide with this feature axis.')

        def nearest(axes,value): return min(axes,key=lambda v:abs(v['coordinate_px']-value))['index']
        feature_candidates=[]
        for index,item in enumerate(circles):
            feature_candidates.append(dict(id=f'circle_feature_{index}',kind='circle',geometry_id=item['id'],
                x_axis_index=nearest(circle_x,item['center'][0]),y_axis_index=nearest(circle_y,item['center'][1]),
                detected_center_px=item['center'],confidence=item['confidence'],status='candidate'))
        for index,item in enumerate(slots):
            feature_candidates.append(dict(id=f'slot_feature_{index}',kind='slot',geometry_id=item['id'],
                x_axis_index=nearest(slot_x,item['center'][0]),y_axis_index=nearest(slot_y,item['center'][1]),
                detected_center_px=item['center'],detected_bbox_px=item['bbox'],source='detected',
                confidence=item['confidence'],status='candidate'))

        def span(name,shape,axis,lo,hi,anchor,**extra):
            return dict(parameter=name,geometry_id=shape['id'],axis=axis,lo=float(lo),hi=float(hi),
                        anchor_range=list(map(float,anchor)),baseline_outside_anchor=True,
                        annotation_lines=True,text_within_span=name!='thickness',dimension_chain=True,**extra)
        depth_axis=side['view_relation']['thickness_axis']; side_box=side['bbox']
        measurements=[span('plate_width',plate,0,x0,x1,[y0,y1]),span('plate_height',plate,1,y0,y1,[x0,x1]),
                      span('thickness',side,depth_axis,side_box[depth_axis],side_box[depth_axis+2],
                           [side_box[1-depth_axis],side_box[3-depth_axis]])]
        position_axes={}
        for key,axes,edges,axis in [('hole_x',circle_x,(x0,x1),0),('hole_y',circle_y,(y0,y1),1),
                                    ('slot_x',slot_x,(x0,x1),0),('slot_y',slot_y,(y0,y1),1)]:
            if not axes: continue
            chain=[edges[0]]+[v['coordinate_px'] for v in axes]+[edges[1]];names=[]
            for index,(lo,hi) in enumerate(zip(chain,chain[1:])):
                name=f'{key}_chain_{index}';names.append(name)
                measurements.append(span(name,plate,axis,lo,hi,[y0,y1] if axis==0 else [x0,x1],
                                         projection_view='view_front',optional_constraint=True))
            position_axes[key]=dict(axis=axis,coordinate_system='processed_image_pixels_top_left',
                                    plate_span_px=list(map(float,edges)),axes=axes,chain_parameters=names)
        callout_names=['slot_width','slot_length']+(['hole_diameter'] if circles else [])
        additions=['countersink_diameter','countersink_angle']
        optional=[name for spec in position_axes.values() for name in spec['chain_parameters']]
        views=[dict(id='view_front',type='orthographic_rectangular_plate',bbox=plate['bbox'],
                    contour_ids=[plate['id']]+[s['id'] for s in slots],hole_ids=[v['id'] for v in circles],confidence=.86),
               dict(id='view_side',type='orthographic_thin_side',bbox=side['bbox'],
                    contour_ids=[side['id']],hole_ids=[],confidence=side['confidence'],
                    relation=side['view_relation'])]
        return dict(status='recognized',recognizer=self.name,views=views,measurements=measurements,
                    required_parameters=['plate_width','plate_height','thickness']+callout_names,
                    optional_parameters=optional+additions,
                    parameter_specs={**{m['parameter']:'length' for m in measurements},
                                     **{name:'length' for name in callout_names+additions[:-1]},'countersink_angle':'angle'},
                    plate_contour=plate['id'],slots=[s['id'] for s in slots],holes=[v['id'] for v in circles],
                    feature_candidates=feature_candidates,position_axes=position_axes,
                    hole_grid=dict(columns=len(circle_x),rows=len(circle_y),count=len(circles)),
                    slot_pattern=dict(x_axes=len(slot_x),y_axes=len(slot_y),count=len(slots)),
                    callout_requirements=dict(slot_count=len(slots),hole_count=len(circles),
                                              kinds=['straight_slot_size','cylindrical_through_hole','optional_countersink']),
                    confidence=.82,
                    geometric_features=[dict(kind='rectangular_extrusion',depth_parameter='thickness'),
                                        dict(kind='circle_feature_candidates',count=len(circles)),
                                        dict(kind='straight_slot_feature_candidates',count=len(slots))],
                    assumptions=[],
                    unknown=['Material, edge treatment, dimensional tolerances and surface finish are unknown.'])
