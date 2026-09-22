"""Parse dimension text and bind it through extension/dimension lines to geometry."""
import re

import numpy as np
from .leader_dimensions import associate_radius

NUMBER = r"(?:\d+(?:[.,]\d+)?|[.,]\d+)"
UNIT = r'(?:mm|cm|inches|inch|in|["″])'
PATTERN = re.compile(rf"^\s*(?P<prefix>[Ø⌀ΦR]|DIA\.?)?\s*(?P<value>{NUMBER})\s*"
                     rf"(?P<unit_before>{UNIT})?\s*(?:[±]\s*(?P<tol>{NUMBER}))?\s*"
                     rf"(?P<unit>{UNIT})?\s*(?P<through>THRU)?\s*$", re.I)
UNIT_FACTORS = {"mm":1.0,"cm":10.0,"in":25.4,"inch":25.4,"inches":25.4,'"':25.4,"″":25.4}


def parse_number(text):
    match = PATTERN.fullmatch(text.strip())
    if not match:
        return None
    # A repeated unit is accepted only when it agrees; no implicit conversion
    # between nominal and tolerance units is performed here.
    if match['unit_before'] and match['unit'] and match['unit_before'].lower()!=match['unit'].lower():
        return None
    unit=match['unit_before'] or match['unit']
    return dict(value=float(match["value"].replace(",",".")),
                tolerance=None if match["tol"] is None else float(match["tol"].replace(",",".")),
                unit=None if unit is None else unit.lower(),
                symbol=match["prefix"],through=bool(match['through']))


def _tokens(texts):
    """Join spaced numeric/symbol/unit tokens, without OCR character substitutions."""
    used=set()
    for i, token in enumerate(texts):
        if i in used:
            continue
        group=[i]
        x0,y0,x1,y1=token["bbox"]
        height=y1-y0
        candidates=sorted([(j,t) for j,t in enumerate(texts) if j!=i and j not in used
                          and abs((t["bbox"][1]+t["bbox"][3])/2-(y0+y1)/2)<height*.35
                          and t["bbox"][0]>=x1-max(1,height*.2)],key=lambda v:v[1]["bbox"][0])
        raw=token["text"]
        best=(raw,group.copy()) if parse_number(raw) else None
        last_x=x1
        for j,t in candidates[:4]:
            if t["bbox"][0]-last_x > height*1.1:
                break
            raw += " " + t["text"]
            group.append(j)
            last_x=t["bbox"][2]
            if parse_number(raw):
                best=(raw,group.copy())
        if best:
            raw,ids=best
            used.update(ids)
            boxes=np.array([texts[j]["bbox"] for j in ids])
            yield dict(text=raw,bbox=[*boxes[:,:2].min(axis=0).tolist(),*boxes[:,2:].max(axis=0).tolist()],
                       confidence=min(texts[j]["confidence"] for j in ids),
                       source=token["source"],text_ids=[texts[j]["id"] for j in ids])


def _associate(token, measurement, lines):
    axis,lo,hi=measurement["axis"],measurement["lo"],measurement["hi"]
    box=token["bbox"]
    center=np.array([(box[0]+box[2])/2,(box[1]+box[3])/2])
    # Rotated labels can contain a long nominal/unit/tolerance string. Their
    # text length must not become an extension-line intersection tolerance.
    height=box[3]-box[1]
    if height>3*(box[2]-box[0]): height=box[2]-box[0]
    along,perp=center[axis],center[1-axis]
    if measurement.get('text_within_span') and not lo-2<=along<=hi+2:
        return None
    if along<lo-7*height or along>hi+7*height:
        return None
    # Both span endpoints need their own extension line and a common dimension baseline.
    extension=[]
    tolerance=max(4,0.012*(hi-lo))
    anchor=measurement.get("anchor_range")
    anchor_padding=measurement.get('anchor_tolerance_px',height)
    for endpoint in (lo,hi):
        found=[s for s in lines if s["axis"]==1-axis and abs(s["coordinate"]-endpoint)<=tolerance
               and s["lo"]-height*1.8 <= perp <= s["hi"]+height*1.8
               and (anchor is None or (s["lo"] <= anchor[1]+anchor_padding and s["hi"] >= anchor[0]-anchor_padding))]
        if not found:
            return None
        extension.append(found)
    baseline=[s for s in lines if s["axis"]==axis and abs(s["coordinate"]-perp)<height*1.65]
    pairs=[]
    for left in baseline:
        if not left["lo"]-tolerance <= lo <= left["hi"]+tolerance:
            continue
        for right in baseline:
            if (right["lo"]-tolerance <= hi <= right["hi"]+tolerance
                    and abs(left["coordinate"]-right["coordinate"])<4):
                coordinate=(left["coordinate"]+right["coordinate"])/2
                if measurement.get('baseline_outside_anchor') and anchor[0]<=coordinate<=anchor[1]:
                    continue
                gap=max(tolerance,height*.5) if measurement.get('dimension_chain') else tolerance
                crossing=[[s for s in found if s["lo"]-gap<=coordinate<=s["hi"]+gap]
                          for found in extension]
                if all(crossing):
                    pairs.append((abs(coordinate-perp),coordinate,left,right,[v[0] for v in crossing]))
    if not pairs:
        return None
    def text_distance(line):
        # A label must touch (or interrupt) its own dimension baseline. Merely
        # sharing the baseline's infinite supporting line is not evidence.
        gap_along=max(line['lo']-box[axis+2],box[axis]-line['hi'],0)
        gap_perp=max(box[1-axis]-line['coordinate'],line['coordinate']-box[3-axis],0)
        return float(np.hypot(gap_along,gap_perp))
    pairs=[p for p in pairs if min(text_distance(p[2]),text_distance(p[3]))<=height]
    if not pairs:
        return None
    distance, coordinate, left, right, extension=min(pairs,key=lambda p:p[0])
    return dict(parameter=measurement["parameter"],geometry_id=measurement["geometry_id"],
                axis=axis,span_px=hi-lo,baseline_coordinate_px=coordinate,
                extension_line_ids=[s["id"] for s in extension],dimension_line_ids=list({left["id"],right["id"]}),
                confidence=float(0.97-0.05*distance/(height*1.65)),
                method="paired_extension_lines_and_common_dimension_baseline",
                dimension_chain=measurement.get('dimension_chain',False))


def _associate_offset_label(token,measurement,lines):
    """Associate an outside label connected to a dimension by a short elbow.

    The two measured endpoints still need independent extension lines. Only
    text position is projected onto the connected baseline; no number changes.
    """
    axis=measurement['axis'];across=1-axis;box=token['bbox'];height=box[3]-box[1]
    if height>3*(box[2]-box[0]): height=box[2]-box[0]
    if measurement.get('text_within_span') is not False: return None
    candidates=[]
    for tail in lines:
        if tail['axis']!=across or tail['hi']-tail['lo']<height*.35: continue
        if not box[axis]-height*.3<=tail['coordinate']<=box[axis+2]+height*.3: continue
        if tail['hi']<=box[across] and box[across]-tail['hi']<height*.5:
            elbow=tail['lo']
        elif tail['lo']>=box[across+2] and tail['lo']-box[across+2]<height*.5:
            elbow=tail['hi']
        else: continue
        connected=[s for s in lines if s['axis']==axis and abs(s['coordinate']-elbow)<3
                   and s['lo']-3<=tail['coordinate']<=s['hi']+3]
        if not connected: continue
        projected=list(box);shift=elbow-(box[across]+box[across+2])/2
        projected[across]+=shift;projected[across+2]+=shift
        assoc=_associate(dict(token,bbox=projected),measurement,lines)
        if assoc and abs(assoc['baseline_coordinate_px']-elbow)<3:
            candidates.append(dict(assoc,confidence=min(.93,assoc['confidence']),
                method='paired_extension_lines_with_connected_offset_label',label_tail_line_id=tail['id'],
                original_text_bbox=box))
    return candidates[0] if len(candidates)==1 else None


def parse_dimensions(texts, geometry, feature, units_override=None, limit_policy="midpoint"):
    notes=" ".join(t["text"] for t in texts)
    note_match=re.search(rf"(?:units?|dimensions?\s+(?:are\s+)?in)\s*[:=]?\s*({UNIT})\b",notes,re.I)
    default_unit=units_override or (note_match[1].lower() if note_match else None)
    default_source="cli_override" if units_override else "drawing_unit_note" if note_match else None
    dimensions=[]
    for token in _tokens(texts):
        parsed=parse_number(token["text"])
        unit=parsed["unit"] or default_unit
        factor=UNIT_FACTORS.get(unit)
        associations=[]
        for m in feature.get('measurements',[]):
            if m.get('kind')=='radius_leader':
                association=associate_radius(token,m,geometry.get('segments',[])) if (parsed['symbol'] or '').upper()=='R' else None
            elif (parsed['symbol'] or '').upper()=='R':
                continue
            elif m.get('requires_through') and not parsed['through']:
                continue
            elif parsed['through'] and not m.get('requires_through'):
                continue
            else:
                lines=geometry.get('annotation_axis_lines',geometry['axis_lines']) if m.get('annotation_lines') else geometry['axis_lines']
                association=_associate(token,m,lines)
                if association is None:association=_associate_offset_label(token,m,lines)
            if association:
                if m.get('projection_view'): association['projection_view']=m['projection_view']
                associations.append(association)
        associations.sort(key=lambda a:a["confidence"],reverse=True)
        if len(associations)>1 and all(a.get('dimension_chain') for a in associations):
            # A continuous chained baseline can also match a containing span.
            # Choose adjacent extensions around the text, never numeric magnitude.
            if len({a['axis'] for a in associations})==1 and max(a['baseline_coordinate_px'] for a in associations)-min(a['baseline_coordinate_px'] for a in associations)<4:
                shortest=min(a['span_px'] for a in associations)
                associations=[a for a in associations if a['span_px']<=shortest+2]
        association=associations[0] if associations else None
        if len({a["parameter"] for a in associations})>1:
            association=None  # ambiguous mapping is not resolved by numeric magnitude
        dim=dict(id=f"dimension_{len(dimensions)}",raw_text=token["text"],bbox=token["bbox"],
                 text_ids=token["text_ids"],source=token["source"],confidence=token["confidence"],
                 value=parsed["value"],unit=unit,unit_source="explicit_label" if parsed["unit"] else default_source,
                 value_mm=parsed["value"]*factor if factor else None,
                 tolerance_mm=parsed["tolerance"]*factor if parsed["tolerance"] is not None and factor else None,
                 diameter_symbol=parsed["symbol"] if (parsed['symbol'] or '').upper()!='R' else None,
                 symbol=parsed['symbol'],through=parsed['through'],association=association,
                 status="recognized" if association and factor and token["confidence"]>=0.90 else "unknown")
        if dim["status"]=="unknown":
            dim["reason"]="No unambiguous dimension-line association, unknown unit, or OCR confidence below 0.90."
        dimensions.append(dim)
    required=feature.get('required_parameters') or (["outer_diameter","thickness"] + (["inner_diameter"] if feature.get("holes") else []))
    parsed_names=required+feature.get('optional_parameters',[])
    parameters={}
    for name in parsed_names:
        matched=[d for d in dimensions if d["association"] and d["association"]["parameter"]==name]
        known=[d for d in matched if d["status"]=="recognized"]
        evidence=[dict(dimension_id=d["id"],raw_text=d["raw_text"],source=d["source"],
                       confidence=d["confidence"],value_mm=d["value_mm"],
                       tolerance_mm=d["tolerance_mm"],bbox=d["bbox"],association=d["association"])
                  for d in matched]
        parameter=dict(status="unknown",value_mm=None,value_source=None,
                       nominal=None,min=None,max=None,confidence=0.0,inferred=False,evidence=evidence,
                       dimension_ids=[d["id"] for d in matched],limits_mm=None,nominal_mm=None,tolerance_mm=None,
                       reason="A reliable, explicitly scaled dimension is required.")
        if len(known)==1 and len(matched)==1:
            d=known[0]
            nominal=d["value_mm"]
            minimum=nominal-d["tolerance_mm"] if d["tolerance_mm"] is not None else None
            maximum=nominal+d["tolerance_mm"] if d["tolerance_mm"] is not None else None
            parameter.update(status="known",value_mm=d["value_mm"],confidence=min(d["confidence"],d["association"]["confidence"]),
                             value_source="drawing_nominal",nominal=nominal,min=minimum,max=maximum,
                             nominal_mm=nominal,limits_mm=[minimum,maximum] if minimum is not None else None,
                             tolerance_mm=d["tolerance_mm"],reason=None,method="dimension_annotation")
        elif len(known)==2 and len(matched)==2:
            top,bottom=sorted(known,key=lambda d:d["bbox"][1])
            h=(top["bbox"][3]-top["bbox"][1]+bottom["bbox"][3]-bottom["bbox"][1])/2
            dy=(bottom["bbox"][1]+bottom["bbox"][3]-top["bbox"][1]-top["bbox"][3])/2
            stacked=(abs(top["bbox"][0]-bottom["bbox"][0])<0.6*h and .45*h<dy<1.8*h
                     and top["value_mm"]>bottom["value_mm"] and top["unit"]==bottom["unit"]
                     and top["tolerance_mm"] is None and bottom["tolerance_mm"] is None)
            if stacked:
                lower,upper=bottom["value_mm"],top["value_mm"]
                parameter.update(min=lower,max=upper,limits_mm=[lower,upper],method="stacked_limit_dimensions",
                                 confidence=min(top["confidence"],bottom["confidence"],0.85),
                                 nominal_mm=None,selection_policy=limit_policy)
                if limit_policy=="midpoint":
                    parameter.update(status="inferred",value_mm=(lower+upper)/2,
                                     value_source="inferred_midpoint",inferred=True,
                                     reason="No nominal is specified; use the midpoint of detected limits by explicit pipeline policy.")
                else:
                    parameter["reason"]="Limits detected, but --limit-policy require-nominal forbids choosing a nominal."
            elif np.isclose(top["value_mm"],bottom["value_mm"]):
                parameter.update(status="known",value_mm=top["value_mm"],value_source="drawing_nominal",
                                 nominal=top["value_mm"],nominal_mm=top["value_mm"],confidence=0.93,
                                 method="consistent_repeated_dimension",reason=None)
            else:
                parameter["reason"]="Multiple conflicting dimensions do not form an upper/lower limit stack."
        elif len(matched)>1:
            parameter["reason"]="Ambiguous or low-confidence competing dimension annotations."
        if parameter["value_mm"] is not None and parameter["value_mm"]<=0:
            parameter.update(status="unknown",value_mm=None,confidence=0.0,reason="Physical length must be positive.")
        parameter['required_for_cad']=name in required
        parameters[name]=parameter
    if feature.get('callout_requirements'):
        from .feature_callouts import add_pattern_callouts
        add_pattern_callouts(texts,geometry,feature,default_unit,dimensions,parameters,default_source)
    constraints=[]
    if feature.get('position_axes'):
        from .pattern_constraints import resolve_pattern_constraints
        constraints=resolve_pattern_constraints(feature,dimensions,parameters,default_unit)
        from .slot_inference import infer_undimensioned_slots
        infer_undimensioned_slots(feature,geometry,texts,dimensions,parameters,constraints)
    detected_units=sorted({d['unit'] for d in dimensions if d['unit'] and d.get('unit_source') in ('explicit_label','drawing_unit_note')})
    for parameter in parameters.values():
        parameter.setdefault('source','inferred' if parameter['inferred'] else 'detected' if parameter['status']=='known' else None)
    return dict(dimensions=dimensions,parameters=parameters,
                constraints=constraints,
                units=dict(cad="mm",stl="unitless; coordinates are millimetres",default=default_unit,
                           status='known' if default_unit or detected_units else 'unknown',
                           default_source=default_source,detected=detected_units))
