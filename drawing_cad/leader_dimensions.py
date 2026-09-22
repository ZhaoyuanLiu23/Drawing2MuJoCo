"""Trace a radius callout leader to a detected arc; never select by numeric value."""
import numpy as np


def associate_radius(token,measurement,segments):
    x,y,X,Y=token['bbox']; height=Y-y
    arc=np.array(measurement['arc']['points'])
    join=max(6,height*.3); hit=max(6,measurement['arc']['radius_px']*.18)
    lines=[s for s in segments if np.linalg.norm(np.array(s['b'])-s['a'])>10]
    queue=[]
    for s in lines:
        for near,far in ((s['a'],s['b']),(s['b'],s['a'])):
            distance=np.linalg.norm(np.array(near)-np.clip(near,[x,y],[X,Y]))
            if distance<join:
                queue.append((np.array(far),np.array(far)-near,[s['id']]))
    while queue:
        point,direction,ids=queue.pop(0)
        direction=direction/np.linalg.norm(direction)
        offsets=arc-point
        forward=offsets@direction
        perpendicular=np.linalg.norm(offsets-forward[:,None]*direction,axis=1)
        # Filled arrowheads can interrupt a Hough line. Bridge only a short gap
        # along the leader's direction, not an arbitrary nearest arc.
        hits=(forward>=-hit)&(forward<=height*.75)&(perpendicular<hit)
        if hits.any():
            return dict(parameter=measurement['parameter'],geometry_id=measurement['geometry_id'],
                        confidence=.92,method='radius_prefix_and_connected_leader_to_arc',leader_segment_ids=ids,
                        arrowhead_gap_px=float(max(0,forward[hits].min())))
        if len(ids)>=3:
            continue
        for s in lines:
            if s['id'] in ids: continue
            for near,far in ((s['a'],s['b']),(s['b'],s['a'])):
                if np.linalg.norm(np.array(near)-point)<join:
                    queue.append((np.array(far),np.array(far)-near,ids+[s['id']]))
    return None
