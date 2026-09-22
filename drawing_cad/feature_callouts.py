"""Counted slot callouts and paired-diameter/angle section evidence.

The numeric characters are evaluated separately from uncertain OCR glyphs.
Counts must match detected geometry; section labels must match dimension lines.
"""
import re
import numpy as np


def numeric_evidence(token):
    if 'numeric_parts' in token: return token['numeric_parts']
    detail=token.get('ocr_detail')
    text=detail['text'] if detail else token['text']
    chars=detail.get('characters',[]) if detail else []
    result=[]
    for match in re.finditer(r'\d+(?:[.,]\d+)?',text):
        if chars and ''.join(c['text'] for c in chars)==text:
            selected=chars[match.start():match.end()]
            confidence=min(c['confidence'] for c in selected)
            bbox=[min(c['bbox'][0] for c in selected),min(c['bbox'][1] for c in selected),
                  max(c['bbox'][2] for c in selected),max(c['bbox'][3] for c in selected)]
        else:
            confidence=token['confidence']; bbox=token['bbox']
        result.append(dict(value=float(match.group().replace(',','.')),confidence=confidence,bbox=bbox,
                           raw_text=match.group(),source_text=text,character_range=[match.start(),match.end()]))
    return result


def callout_tokens(texts):
    """Join adjacent horizontal words, retaining original numeric provenance.

    PDF words / locally re-read OCR words can split an annotation. Only a
    counted annotation starts a join; rows and large gaps are never bridged.
    """
    result=list(texts)
    for first in texts:
        if not re.match(r'^\s*\d+\s*[-×xX]',first['text']): continue
        x,y,X,Y=first['bbox']; height=Y-y
        row=sorted((t for t in texts if t['id']!=first['id'] and t['bbox'][0]>=X-1
                    and abs((t['bbox'][1]+t['bbox'][3]-y-Y)/2)<height*.3
                    and .65*height<t['bbox'][3]-t['bbox'][1]<1.5*height),key=lambda t:t['bbox'][0])
        joined=[first]
        for token in row:
            if re.search(r'\bTHRU\s*$',joined[-1]['text'],re.I): break
            if token['bbox'][0]-joined[-1]['bbox'][2]>height*1.5: break
            joined.append(token)
        if len(joined)<2: continue
        content=' '.join(t['text'] for t in joined)
        if not re.search(r'\bTHRU\s*$|[)）]\s*$',content,re.I): continue
        result.append(dict(id='callout_'+'_'.join(t['id'] for t in joined),text=content,
            bbox=[min(t['bbox'][0] for t in joined),min(t['bbox'][1] for t in joined),
                  max(t['bbox'][2] for t in joined),max(t['bbox'][3] for t in joined)],
            confidence=min(t['confidence'] for t in joined),source='joined_annotation_words',
            source_text_ids=[t['id'] for t in joined],
            numeric_parts=[dict(n,source_text_id=t['id'],source=t['source'])
                           for t in joined for n in numeric_evidence(t)]))
    return result


def slot_callout(token):
    """Normalize counted legacy width/length and explicit SLOT ... THRU text."""
    content=token['text'].translate(str.maketrans({'（':'(', '）':')'}))
    numbers=numeric_evidence(token)
    if len(numbers)!=3 or not re.match(r'^\s*\d+\s*[-×xX]',content): return None
    legacy=bool(re.search(r'\([^)]*[×xX][^)]*\)',content))
    explicit=bool(re.fullmatch(r'\s*\d+\s*[-×xX]\s*SLOT\s+\d+(?:[.,]\d+)?\s*[×xX]\s*'
                               r'\d+(?:[.,]\d+)?\s+THRU\s*',content,re.I))
    if not (legacy or explicit): return None
    count,first,second=numbers
    minor,major=sorted((first,second),key=lambda n:n['value'])
    return dict(count=count,width=minor,length=major,extent='through',
                extent_source='explicit_thru' if explicit else 'legacy_slot_callout_convention',
                inferred=not explicit)


def trace_leader(token,targets,segments):
    """Trace at most three connected segments from a callout to a feature edge."""
    x,y,X,Y=token['bbox']; h=Y-y; join=max(4,h*.65)
    lines=[s for s in segments if np.linalg.norm(np.asarray(s['b'])-s['a'])>max(6,h*.6)]
    def edge_distance(point,target):
        if target['kind']=='small_circle':
            return abs(np.linalg.norm(point-target['center'])-target['radius'])
        a,b,A,B=target['bbox']; radius=(B-b)/2; cy=(b+B)/2
        nearest=np.array([np.clip(point[0],a+radius,A-radius),cy])
        return abs(np.linalg.norm(point-nearest)-radius)
    queue=[]
    for s in lines:
        for near,far in ((s['a'],s['b']),(s['b'],s['a'])):
            if np.linalg.norm(np.asarray(near)-np.clip(near,[x,y],[X,Y]))<=join:
                if x<=far[0]<=X and y<=far[1]<=Y: continue
                queue.append((np.asarray(far),np.asarray(far)-near,[s['id']]))
    visited=set()
    while queue:
        point,direction,ids=queue.pop(0)
        key=(ids[-1],tuple(point))
        if key in visited: continue
        visited.add(key)
        direction=direction/np.linalg.norm(direction)
        for target in targets:
            gap=min(edge_distance(point+direction*t,target) for t in np.linspace(0,h*.75,6))
            if gap<=max(3,h*.25):
                return dict(method='connected_callout_leader',leader_segment_ids=ids,
                            target_geometry_id=target['id'],confidence=.90)
        if len(ids)>=3: continue
        for s in lines:
            if s['id'] in ids: continue
            for near,far in ((s['a'],s['b']),(s['b'],s['a'])):
                if np.linalg.norm(np.asarray(near)-point)<=join:
                    queue.append((np.asarray(far),np.asarray(far)-near,ids+[s['id']]))
    return None


def _section(feature,geometry):
    """Two coplanar material strips separated by a centred through opening."""
    front=next(v for v in feature['views'] if v['id']=='view_front')['bbox']
    side=next(v for v in feature['views'] if v['id']=='view_side')['bbox']
    def separate(box,other):
        return box[2]<other[0] or box[0]>other[2] or box[3]<other[1] or box[1]>other[3]
    rectangles=[s for s in geometry['primitives'] if s['kind']=='rectangle'
                and separate(s['bbox'],front) and separate(s['bbox'],side)
                and s['bbox'][3]-s['bbox'][1]<(front[3]-front[1])*.20]
    candidates=[]
    for left in rectangles:
        a,b,A,B=left['bbox']
        for right in rectangles:
            x,y,X,Y=right['bbox']
            if not (0<x-A<(A-a+X-x)*.35 and abs(b-y)<4 and abs(B-Y)<4): continue
            if abs((A-a)-(X-x))>max(A-a,X-x)*.25: continue
            candidates.append(dict(bbox=[a,min(b,y),X,max(B,Y)],gap=[A,x],
                                   geometry_ids=[left['id'],right['id']]))
    return candidates[0] if len(candidates)==1 else None


def add_pattern_callouts(texts,geometry,feature,units,dimensions,parameters,unit_source=None):
    from .dimensions import UNIT_FACTORS, _associate
    factor=UNIT_FACTORS.get(units)
    section=_section(feature,geometry)
    requirements=feature['callout_requirements']
    by_id={s['id']:s for s in geometry['primitives']}
    segments=geometry.get('annotation_segments',geometry.get('segments',[]))
    diagnostics=[]
    observations={}
    def observe(name,number,token,association,quantity='length',**extra):
        observations.setdefault(name,[]).append((number,token,association,quantity,extra))
    tokens=callout_tokens(texts)
    definitions=feature['feature_definitions']={}
    for token in tokens:
        slot=slot_callout(token)
        if slot:
            count=slot['count']
            association=trace_leader(token,[by_id[i] for i in feature['slots']],segments)
            if count['value']!=requirements['slot_count'] or count['confidence']<.9 or not association:
                diagnostics.append(dict(kind='slot_callout',status='unknown',text_id=token['id'],
                                        reason='Count, leader or numeric evidence does not match detected slots.'))
                continue
            for name,number in [('slot_width',slot['width']),('slot_length',slot['length'])]:
                observe(name,number,token,association,count=int(count['value']),
                        interpretation='Minor size is slot width; major size is overall slot length.')
            definitions['slot']=dict(kind='straight_slot',width_parameter='slot_width',
                length_parameter='slot_length',extent=slot['extent'],extent_source=slot['extent_source'],
                inferred=slot['inferred'])
            if slot['inferred']:
                assumption='Legacy slot size callouts without a depth are interpreted as through slots.'
                if assumption not in feature['assumptions']: feature['assumptions'].append(assumption)
        # A cylindrical through hole requires its own diameter, count and
        # connected leader. A missing countersink is never used as a fallback.
        if re.fullmatch(r'\s*\d+\s*[-×xX]\s*[Ø⌀Φφ]\s*\d+(?:[.,]\d+)?\s+THRU\s*',token['text'],re.I):
            numbers=numeric_evidence(token)
            assoc=trace_leader(token,[by_id[i] for i in feature['holes']],segments)
            if (len(numbers)!=2 or numbers[0]['value']!=requirements['hole_count']
                    or numbers[0]['confidence']<.9 or not assoc):
                diagnostics.append(dict(kind='through_hole_callout',status='unknown',text_id=token['id'],
                                        reason='Count or leader does not match detected circles.'))
                continue
            observe('hole_diameter',numbers[1],token,assoc,count=int(numbers[0]['value']))
            definitions['hole']=dict(kind='cylindrical_through_hole',diameter_parameter='hole_diameter',
                                      extent='through',extent_source='explicit_thru',additions=[])
    # Incomplete countersink evidence remains mandatory instead of silently
    # degrading the feature to an ordinary cylindrical hole.
    csk_requested=any(
        re.search(r'\b(?:CSK|COUNTERSINK)\b',t['text'],re.I)
        or (len(numeric_evidence(t))==3 and re.match(r'^\s*\d+\s*[-×xX]',t['text'])
            and not slot_callout(t)) for t in tokens)
    if csk_requested:
        for name in ('countersink_diameter','countersink_angle'):
            if name not in feature['required_parameters']: feature['required_parameters'].append(name)
        feature['optional_parameters']=[n for n in feature['optional_parameters'] if n not in feature['required_parameters']]
        definitions.setdefault('hole',dict(kind='cylindrical_through_hole',diameter_parameter='hole_diameter',
            extent='through',extent_source='paired_section_and_counted_callout',additions=[]))['additions']=[
                dict(kind='countersink',diameter_parameter='countersink_diameter',angle_parameter='countersink_angle')]
        feature['assumptions'].append('Paired section diameters and an included angle define a countersink on each hole; the opening is on the positive extrusion face.')
    if section and csk_requested:
        a,b,A,B=section['bbox']; lo,hi=section['gap']; centre=(lo+hi)/2
        axis_lines=geometry.get('vector_axis_lines') or geometry.get('annotation_axis_lines',geometry['axis_lines'])
        extension=[s for s in axis_lines if s['axis']==1 and a<s['coordinate']<A
                   and s['lo']<b and s['hi']>=b-4]
        spans=[(lo,hi,'hole_diameter')]
        for left in extension:
            for right in extension:
                x,X=left['coordinate'],right['coordinate']
                if x<lo-2 and X>hi+2 and abs((x+X)/2-centre)<3 and X-x<(A-a)*.45:
                    spans.append((x,X,'countersink_diameter'))
        diameter_candidates=[]
        for token in texts:
            numbers=numeric_evidence(token)
            if len(numbers)!=1: continue
            x,y,X,Y=token['bbox']; cx=(x+X)/2
            if not (a-10<cx<A+(A-a)*.4 and b-(A-a)<y<B): continue
            if re.fullmatch(r'\s*\d+(?:\.\d+)?\s*[°º]\s*',token['text']):
                if abs(cx-centre)<(A-a)*.3:
                    observe('countersink_angle',numbers[0],token,
                            dict(method='included_angle_above_paired_diameter_section',confidence=.90,
                                 section_geometry_ids=section['geometry_ids']),quantity='angle')
                continue
            if not re.match(r'\s*[Ø⌀Φφ①]',token['text']): continue
            matches=[]
            for x0,x1,name in spans:
                measurement=dict(parameter=name,geometry_id=section['geometry_ids'][0],axis=0,
                                 lo=x0,hi=x1,anchor_range=[b,B],anchor_tolerance_px=2)
                association=_associate(token,measurement,axis_lines)
                if association: matches.append((name,association))
            for name in ('hole_diameter','countersink_diameter'):
                eligible=[assoc for n,assoc in matches if n==name]
                if eligible:
                    diameter_candidates.append((name,numbers[0],token,max(eligible,key=lambda a:a['confidence'])))
        assignments=[]
        for first in diameter_candidates:
            for second in diameter_candidates:
                if first[0]!='hole_diameter' or second[0]!='countersink_diameter' or first[2]['id']==second[2]['id']: continue
                assignments.append((first[3]['confidence']+second[3]['confidence'],first,second))
        assignments.sort(key=lambda a:a[0],reverse=True)
        if assignments and (len(assignments)==1 or assignments[0][0]-assignments[1][0]>.002):
            for name,number,token,association in assignments[0][1:]:
                observe(name,number,token,dict(association,joint_assignment='unique paired section labels'),
                        symbol_status='uncertain OCR glyph; diameter role corroborated by section extension lines')
        # The counted main-view callout must point to a detected circular feature.
        # Its two numeric sizes must agree with independently associated detail labels.
        groups=[]
        for token in tokens:
            numbers=numeric_evidence(token)
            if len(numbers)!=3 or not re.match(r'^\s*\d+\s*[-×xX]',token['text']):continue
            if slot_callout(token):continue
            if numbers[0]['value']!=requirements['hole_count'] or min(n['confidence'] for n in numbers)<.90:continue
            assoc=trace_leader(token,[by_id[i] for i in feature['holes']],segments)
            if assoc:groups.append((token,numbers,assoc))
        matches=[]
        for token,numbers,assoc in groups:
            if all(observations.get(name) and all(np.isclose(o[0]['value'],number['value']) for o in observations[name])
                   for name,number in zip(('hole_diameter','countersink_diameter'),numbers[1:])):
                matches.append((token,numbers,assoc))
        if len(matches)==1 and observations.get('countersink_angle'):
            token,numbers,assoc=matches[0]
            for name,number in zip(('hole_diameter','countersink_diameter'),numbers[1:]):
                observe(name,number,token,assoc,count=int(numbers[0]['value']))
            feature['section_evidence']=dict(status='inferred',confidence=.80,section=section,
                main_callout_text_id=token['id'],hole_count=int(numbers[0]['value']),
                interpretation='Paired section diameters and included angle are interpreted as a conical countersink through the plate.')
        else:
            for name in ('hole_diameter','countersink_diameter','countersink_angle'):
                observations.pop(name,None)
            diagnostics.append(dict(kind='countersink_callout',status='unknown',
                                    reason='Require a matching counted leader, two dimensioned section diameters and an included angle.'))
    for name,items in observations.items():
        records=[]
        for number,token,association,quantity,extra in items:
            angle=quantity=='angle'
            value=number['value'] if angle else number['value']*factor if factor else None
            confidence=min(number['confidence'],association['confidence'])
            record=dict(id=f'dimension_{len(dimensions)}',raw_text=token['text'],bbox=token['bbox'],
                        text_ids=token.get('source_text_ids',[token['id']]),source=token['source'],confidence=number['confidence'],
                        value=number['value'],unit='deg' if angle else units,
                        unit_source='explicit_angle_symbol' if angle else unit_source,
                        quantity=quantity,value_mm=None if angle else value,value_degrees=value if angle else None,
                        tolerance_mm=None,association=dict(association,parameter=name),
                        numeric_evidence=number,status='recognized' if value is not None and confidence>=.9 else 'unknown',
                        **extra)
            dimensions.append(record); records.append(record)
        p=parameters[name]
        p['evidence']=[dict(dimension_id=r['id'],raw_text=r['raw_text'],source=r['source'],confidence=r['confidence'],
                            value_mm=r['value_mm'],value_degrees=r['value_degrees'],bbox=r['bbox'],
                            association=r['association'],numeric_evidence=r['numeric_evidence']) for r in records]
        p['dimension_ids']=[r['id'] for r in records]
        good=all(r['status']=='recognized' for r in records)
        consistent=all(np.isclose(r['value'],records[0]['value']) for r in records)
        angle=records[0]['quantity']=='angle'; value=records[0]['value_degrees'] if angle else records[0]['value_mm']
        p.update(quantity='angle' if angle else 'length',unit='deg' if angle else 'mm' if factor else 'unknown')
        if good and consistent:
            p.update(status='known',value_mm=None if angle else value,value_degrees=value if angle else None,
                     value_source='drawing_nominal',nominal=value,nominal_mm=None if angle else value,
                     confidence=min(min(r['confidence'],r['association']['confidence']) for r in records),
                     reason=None,method='feature_callout_with_geometry_and_numeric_character_evidence')
        elif not consistent:
            p['reason']='Conflicting independent feature annotations.'
    feature['callout_diagnostics']=diagnostics
    for candidate in feature['feature_candidates']:
        candidate['definition']='hole' if candidate['kind']=='circle' else 'slot'
