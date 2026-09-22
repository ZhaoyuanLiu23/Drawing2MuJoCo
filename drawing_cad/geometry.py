"""Geometric measurements only. No part names, catalogue IDs or physical sizes."""
import math

import cv2
import numpy as np
from .profile_geometry import rounded_profiles
from .pattern_geometry import plate_pattern_primitives, annotation_segments


def _circle(points, min_radius):
    p = np.asarray(points, dtype=float)
    if len(p) < 16:
        return None
    center0 = p.mean(axis=0)
    x, y = (p-center0).T
    a = np.column_stack([2*x, 2*y, np.ones(len(x))])
    cx, cy, constant = np.linalg.lstsq(a, x*x+y*y, rcond=None)[0]
    radius = math.sqrt(max(0, constant + cx*cx + cy*cy))
    center = center0 + [cx, cy]
    if radius < min_radius:
        return None
    residual = np.sqrt(np.mean((np.linalg.norm(p-center, axis=1)-radius)**2))/radius
    angles = np.sort(np.mod(np.arctan2(p[:,1]-center[1], p[:,0]-center[0]), 2*np.pi))
    coverage = 1 - np.diff(np.r_[angles, angles[0]+2*np.pi]).max()/(2*np.pi)
    if residual > 0.012 or coverage < 0.9:
        return None
    return dict(kind="circle", center=center.tolist(), radius=float(radius),
                bbox=[center[0]-radius,center[1]-radius,center[0]+radius,center[1]+radius],
                fit_residual=float(residual), angular_coverage=float(coverage))


def merge_axis_lines(segments):
    candidates = []
    for line in segments:
        a, b = np.array(line["a"]), np.array(line["b"])
        delta = np.abs(b-a)
        axis = int(delta[1] > delta[0])  # 0 = horizontal
        # Vector exporters may split a straight edge into hundreds of tiny segments.
        minimum = 0.02 if line["source"] == "pdf_vector" else 3
        tolerance = 0.015*delta[axis] + (0.001 if line["source"] == "pdf_vector" else 0.6)
        if delta[axis] < minimum or delta[1-axis] > tolerance:
            continue
        candidates.append(dict(axis=axis, coordinate=float((a[1-axis]+b[1-axis])/2),
                               lo=float(min(a[axis],b[axis])), hi=float(max(a[axis],b[axis])),
                               source=line["source"]))
    merged = []
    for line in sorted(candidates, key=lambda v:(v["axis"], v["coordinate"], v["lo"])):
        match = next((v for v in merged if v["axis"] == line["axis"]
                      and abs(v["coordinate"]-line["coordinate"]) < 2.2
                      and line["lo"] <= v["hi"]+3 and line["hi"] >= v["lo"]-3), None)
        if match is None:
            merged.append(line.copy())
        else:
            match["lo"], match["hi"] = min(match["lo"],line["lo"]), max(match["hi"],line["hi"])
    for i, line in enumerate(merged):
        line["id"] = f"axis_{i}"
    return merged


def _rectangles(lines, shape):
    h, w = shape
    vertical = [s for s in lines if s["axis"] == 1 and s["hi"]-s["lo"] > 5]
    horizontal = [s for s in lines if s["axis"] == 0]
    result = []
    for i, left in enumerate(vertical):
        for right in vertical[i+1:]:
            x0, x1 = sorted((left["coordinate"],right["coordinate"]))
            if x1-x0 < 5:
                continue
            # Dimension extensions can touch/continue an edge. Find intersections,
            # not just segment endpoints, so those extensions do not enlarge a view.
            bridges = sorted({s["coordinate"] for s in horizontal
                              if s["lo"] <= x0+4 and s["hi"] >= x1-4
                              and max(left["lo"],right["lo"])-4 <= s["coordinate"]
                              <= min(left["hi"],right["hi"])+4})
            for j,y0 in enumerate(bridges):
                for y1 in bridges[j+1:]:
                    if y1-y0 < 5 or max(x1-x0,y1-y0) < min(h,w)*.08 or (x1-x0)*(y1-y0) > .75*h*w:
                        continue
                    result.append(dict(kind="rectangle",bbox=[x0,y0,x1,y1],confidence=.96,
                                       source="axis_line_cycle"))
    return result


def _raster_geometry(binary, texts):
    clean = binary.copy()
    for text in texts:
        x0,y0,x1,y1 = map(int, text["bbox"])
        clean[max(0,y0-1):y1+2,max(0,x0-1):x1+2] = 0
    factor = min(1.0, 1600/max(clean.shape))
    small = cv2.resize(clean, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    h, w = small.shape
    min_radius, max_radius = max(12,int(min(h,w)*0.018)), int(min(h,w)*0.46)
    detected = cv2.HoughCircles(cv2.GaussianBlur(255-small,(5,5),1), cv2.HOUGH_GRADIENT,
                               dp=1.0, minDist=max(20,min(h,w)*0.035), param1=100, param2=34,
                               minRadius=min_radius, maxRadius=max_radius)
    seeds = [] if detected is None else [c[:2] for c in detected[0]]
    contours, _ = cv2.findContours(small, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    for contour in contours:
        fit = _circle(contour[:,0], min_radius)
        if fit and fit["radius"] <= max_radius:
            seeds.append(fit["center"])
    unique = []
    for center in seeds:
        if not any(np.linalg.norm(np.array(center)-old) < 4 for old in unique):
            unique.append(np.array(center))
    # Hough yields one radius per centre. Radial voting recovers ALL concentric loops.
    edges = cv2.Canny(small, 60, 150)
    near_edge = cv2.dilate(edges, np.ones((5,5),np.uint8)) > 0
    theta = np.linspace(0, 2*np.pi, 256, endpoint=False)
    radii = np.arange(min_radius, max_radius+1)
    circles = []
    for center in unique[:40]:
        xs = np.rint(center[0] + radii[:,None]*np.cos(theta)).astype(int)
        ys = np.rint(center[1] + radii[:,None]*np.sin(theta)).astype(int)
        valid = (xs>=0)&(xs<w)&(ys>=0)&(ys<h)
        score = (near_edge[np.clip(ys,0,h-1),np.clip(xs,0,w-1)] & valid).mean(axis=1)
        mask = score >= 0.85
        indices = np.flatnonzero(mask)
        clusters = np.split(indices, np.flatnonzero(np.diff(indices)>1)+1) if len(indices) else []
        for group in clusters:
            if not len(group):
                continue
            radius = float(np.average(radii[group],weights=score[group]))/factor
            cx,cy = center/factor
            candidate = dict(kind="circle", center=[float(cx),float(cy)], radius=radius,
                             bbox=[float(cx-radius),float(cy-radius),float(cx+radius),float(cy+radius)],
                             confidence=float(min(0.94,score[group].max())), source="raster_radial_vote")
            if not any(np.linalg.norm(np.array(candidate["center"])-v["center"]) < radius*0.04+3
                       and abs(radius-v["radius"]) < radius*0.03+2 for v in circles):
                circles.append(candidate)
    edges_full = cv2.Canny(clean, 60, 160)
    raw = cv2.HoughLinesP(edges_full, 1, np.pi/1800, 30, minLineLength=10, maxLineGap=5)
    segments = [dict(a=[float(x0),float(y0)], b=[float(x1),float(y1)],source="raster_hough")
                for x0,y0,x1,y1 in ([] if raw is None else raw[:,0])]
    for axis in (0,1):
        kernel=np.ones((1,13) if axis==0 else (13,1),np.uint8)
        straight=cv2.morphologyEx(clean,cv2.MORPH_OPEN,kernel)
        count,_,stats,_=cv2.connectedComponentsWithStats(straight)
        for x,y,width,height,area in stats[1:]:
            if (height if axis==0 else width)>8:
                continue
            a,b=([x,y+height/2],[x+width,y+height/2]) if axis==0 else ([x+width/2,y],[x+width/2,y+height])
            segments.append(dict(a=list(map(float,a)),b=list(map(float,b)),source="raster_morphology"))
    rectangles=[]
    for contour in contours:
        perimeter=cv2.arcLength(contour,True)
        quad=cv2.approxPolyDP(contour,0.008*perimeter,True)
        if len(quad)!=4 or not cv2.isContourConvex(quad):
            continue
        x,y,width,height=cv2.boundingRect(quad)
        if min(width,height)<5 or max(width,height)<min(h,w)*.08 or width*height>.75*h*w:
            continue
        if cv2.contourArea(quad)/(width*height)<.90:
            continue
        rectangles.append(dict(kind="rectangle",bbox=[x/factor,y/factor,(x+width)/factor,(y+height)/factor],
                               confidence=.92,source="raster_closed_contour"))
    return circles+rectangles, segments


def detect_geometry(page, binary):
    primitives = []
    minimum = min(binary.shape)*0.018
    for path in page.paths:
        points = path["points"]
        circle = _circle(points, minimum)
        if circle:
            circle.update(source="pdf_vector", confidence=0.99, path_id=path["id"])
            primitives.append(circle)
        elif len(points) >= 20 and path["closed"]:
            ellipse = cv2.fitEllipse(points.astype(np.float32))
            (cx,cy),(a,b),angle = ellipse
            if min(a,b)>minimum*2 and min(a,b)/max(a,b)<0.9:
                primitives.append(dict(kind="ellipse", center=[cx,cy], axes=[a,b], angle_degrees=angle,
                                       bbox=[float(points[:,0].min()),float(points[:,1].min()),
                                             float(points[:,0].max()),float(points[:,1].max())],
                                       source="pdf_vector", confidence=0.85, path_id=path["id"]))
    segments = page.segments
    if not any(p["kind"] == "circle" for p in primitives):
        circles, raster_segments = _raster_geometry(binary, page.texts)
        primitives.extend(circles)
        segments = segments + raster_segments
    lines = merge_axis_lines(segments)
    primitives.extend(_rectangles(lines, binary.shape))
    primitives.extend(rounded_profiles(binary))
    # Shaded material regions can dominate Otsu's threshold; retain only dark
    # strokes for the small-feature detector so grey fill does not erase holes.
    gray=cv2.cvtColor(page.image,cv2.COLOR_RGB2GRAY)
    pattern_binary=cv2.bitwise_and(binary,(gray<160).astype(np.uint8)*255)
    primitives.extend(plate_pattern_primitives(pattern_binary,page.texts,
                                                [p for p in primitives if p['kind']=='rectangle'],lines,
                                                projection_lines=merge_axis_lines(page.segments)))
    # Consolidate the two sides of a raster line into a single rectangle.
    unique = []
    for p in primitives:
        if p["kind"] == "rectangle" and any(v["kind"] == "rectangle" and
                np.max(np.abs(np.array(v["bbox"])-p["bbox"])) < 6 for v in unique):
            continue
        p["id"] = f"geometry_{len(unique)}"
        unique.append(p)
    raw_segments=[dict(s,id=f"segment_{i}") for i,s in enumerate(segments)]
    gray_segments=annotation_segments(cv2.cvtColor(page.image,cv2.COLOR_RGB2GRAY))
    annotations=merge_axis_lines(segments+gray_segments)
    # Coalesce line groups bridged by a later, longer segment. Keep the original
    # silhouette/legacy association lines unchanged.
    changed=True
    while changed:
        changed=False
        for i,a in enumerate(annotations):
            match=next((j for j,b in enumerate(annotations[i+1:],i+1)
                        if a['axis']==b['axis'] and abs(a['coordinate']-b['coordinate'])<2.2
                        and a['lo']<=b['hi']+3 and b['lo']<=a['hi']+3),None)
            if match is not None:
                b=annotations.pop(match)
                a['lo'],a['hi']=min(a['lo'],b['lo']),max(a['hi'],b['hi'])
                changed=True
                break
    for i,line in enumerate(annotations): line['id']=f'annotation_axis_{i}'
    return dict(primitives=unique, axis_lines=lines,segments=raw_segments,annotation_axis_lines=annotations,
                vector_axis_lines=merge_axis_lines(page.segments) if page.segments else [],
                annotation_segments=[dict(s,id=f'annotation_segment_{i}') for i,s in enumerate(segments+gray_segments)],
                coordinate_system="processed_image_pixels_top_left")
