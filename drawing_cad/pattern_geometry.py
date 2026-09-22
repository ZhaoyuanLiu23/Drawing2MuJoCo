"""Detect small circular arrays, straight slots and a matching thin side view.

These are pixel-space topology candidates only. No physical dimension is
estimated here; dimensions are resolved from annotations or a separately
recorded, annotation-calibrated geometry inference stage.
"""
import cv2
import numpy as np


def annotation_segments(gray):
    """Keep faint dimension lines lost by the silhouette's Otsu threshold."""
    raw=cv2.HoughLinesP(cv2.Canny(gray,30,90),1,np.pi/1800,15,minLineLength=8,maxLineGap=3)
    segments=[dict(a=list(map(float,s[:2])),b=list(map(float,s[2:])),source='gray_hough')
              for s in ([] if raw is None else raw[:,0])]
    for cutoff in (160,220):
        mask=(gray<cutoff).astype(np.uint8)*255
        for axis in (0,1):
            straight=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((1,11) if axis==0 else (11,1),np.uint8))
            _,_,stats,_=cv2.connectedComponentsWithStats(straight)
            for x,y,w,h,area in stats[1:]:
                if (h if axis==0 else w)>4: continue
                a,b=([x,y+(h-1)/2],[x+w-1,y+(h-1)/2]) if axis==0 else ([x+(w-1)/2,y],[x+(w-1)/2,y+h-1])
                segments.append(dict(a=list(map(float,a)),b=list(map(float,b)),source='gray_morphology'))
    return segments


def _inside(box,outer,margin=1):
    x,y,X,Y=box; a,b,A,B=outer
    return x>a+margin and y>b+margin and X<A-margin and Y<B-margin


def matching_thin_projections(plate,axis_lines,binary=None,closed_rectangles=()):
    """Match either orthographic span, on either side of the front view.

    Both projected endpoints and closing edges must agree. Retain competing
    projections for the recognizer to reject; screen proximity is not a tie-break.
    """
    box=plate['bbox']; short=min(box[2]-box[0],box[3]-box[1])
    tolerance=max(3,short*.08); result=[]
    for axis in (0,1):
        across=1-axis; lo,hi=box[axis],box[axis+2]
        edges=sorted((s for s in axis_lines if s['axis']==axis
                      and s['lo']<=lo+tolerance and s['hi']>=hi-tolerance),
                     key=lambda s:s['coordinate'])
        for i,first in enumerate(edges):
            for second in edges[i+1:]:
                a,b=first['coordinate'],second['coordinate']; gap=b-a
                if not (2<=gap<short): continue
                if not (b<box[across]-2 or a>box[across+2]+2): continue
                bbox=[0.,0.,0.,0.]
                bbox[axis],bbox[axis+2]=lo,hi
                bbox[across],bbox[across+2]=a,b
                boundary=next((s for s in closed_rectangles if s['source']=='raster_closed_contour'
                               and np.allclose(s['bbox'],bbox,atol=3,rtol=0)),None)
                # A thicker projection needs a complete contour as independent
                # evidence, instead of accepting more dimension-line cycles.
                if gap>short*.20 and boundary is None: continue
                closures=[]; raster_closures=[]
                for endpoint in (lo,hi):
                    crossings=[s for s in axis_lines if s['axis']==across
                             and abs(s['coordinate']-endpoint)<=tolerance
                             and s['lo']<=a+2 and s['hi']>=b-2]
                    # Dimension baselines and their long extensions also form
                    # rectangles. A closing contour ends near the two edges;
                    # an extension that continues far beyond them is not one.
                    overrun=max(3,gap*.5)
                    matches=[s for s in crossings if s['lo']>=a-overrun and s['hi']<=b+overrun]
                    if matches:
                        closures.append(min(matches,key=lambda s:abs(s['coordinate']-endpoint))['id'])
                        continue
                    # Very short closing edges may be absent from Hough lines.
                    # Check actual dark strokes, not a fabricated clipped edge.
                    if binary is None or crossings: break
                    image=binary if axis==0 else binary.T
                    crop=image[max(0,int(a)):int(b)+1,
                               max(0,int(endpoint-tolerance)):int(endpoint+tolerance)+1]
                    if crop.size==0 or np.max(np.mean(crop>0,axis=0))<.8: break
                    raster_closures.append(dict(endpoint_px=endpoint,span_px=[a,b],
                                                method='continuous_dark_closing_stroke'))
                if len(closures)+len(raster_closures)!=2: continue
                if any(np.allclose(bbox,v['bbox'],atol=2.2,rtol=0) for v in result): continue
                result.append(dict(kind='thin_rectangle',bbox=bbox,confidence=.88,
                    source='matched_projection_edge_pair',plate_bbox=box,
                    edge_line_ids=[first['id'],second['id']],
                    view_relation=dict(method='aligned_orthographic_endpoints_and_closing_edges',
                        shared_axis=axis,shared_span_px=[lo,hi],thickness_axis=across,
                        closing_line_ids=closures,raster_closing_evidence=raster_closures,
                        closed_boundary_bbox=boundary['bbox'] if boundary else None,
                        projected_span_coverage=[min(s['hi'],hi)-max(s['lo'],lo) for s in (first,second)])))
    return result


def plate_pattern_primitives(binary,texts,rectangles,axis_lines,projection_lines=None):
    clean=binary.copy()
    for token in texts:
        x,y,X,Y=map(int,token['bbox'])
        clean[max(0,y-2):Y+3,max(0,x-2):X+3]=0
    image_height,image_width=clean.shape
    result=[]
    plates=[r for r in rectangles if (r['bbox'][2]-r['bbox'][0])>2*(r['bbox'][3]-r['bbox'][1])]
    contours,_=cv2.findContours(clean,cv2.RETR_LIST,cv2.CHAIN_APPROX_NONE)
    for plate in plates:
        x0,y0,x1,y1=plate['bbox']; pw=x1-x0; ph=y1-y0
        capsules=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if not _inside([x,y,x+w-1,y+h-1],plate['bbox'],2):
                continue
            area=cv2.contourArea(contour); hull=cv2.contourArea(cv2.convexHull(contour))
            ratio=w/max(h,1)
            if not (1.5<ratio<6 and ph*.10<h<ph*.80 and pw*.07<w<pw*.90):
                continue
            if not (.62<area/max(1,w*h)<.94 and area/max(1,hull)>.94):
                continue
            candidate=dict(kind='capsule',bbox=[float(x),float(y),float(x+w-1),float(y+h-1)],
                           center=[x+(w-1)/2,y+(h-1)/2],axis=0,
                           confidence=.90,source='closed_convex_capsule_contour',plate_bbox=plate['bbox'])
            if not any(np.linalg.norm(np.asarray(candidate['center'])-v['center'])<ph*.08 for v in capsules):
                capsules.append(candidate)
        # Small circular features may contain centre marks, so use radial voting
        # rather than assuming a clean closed contour.
        min_radius=max(2,int(ph*.022)); max_radius=max(min_radius+2,int(ph*.14))
        detected=cv2.HoughCircles(cv2.GaussianBlur(255-clean,(3,3),.7),cv2.HOUGH_GRADIENT,
                                  dp=1,minDist=max(6,ph*.07),param1=100,param2=14,
                                  minRadius=min_radius,maxRadius=max_radius)
        circles=[]
        for cx,cy,radius in ([] if detected is None else detected[0]):
            box=[cx-radius,cy-radius,cx+radius,cy+radius]
            if not _inside(box,plate['bbox'],1):
                continue
            if any(c['bbox'][0]-3<cx<c['bbox'][2]+3 and c['bbox'][1]-3<cy<c['bbox'][3]+3 for c in capsules):
                continue
            candidate=dict(kind='small_circle',center=[float(cx),float(cy)],radius=float(radius),
                           bbox=list(map(float,box)),confidence=.86,source='small_radius_hough',plate_bbox=plate['bbox'])
            match=next((v for v in circles if np.linalg.norm(np.asarray(v['center'])-candidate['center'])<max(3,.9*(v['radius']+candidate['radius']))),None)
            if match is None:
                circles.append(candidate)
            else:
                # Overlapping votes from centre marks are one circular feature.
                # Keep their weighted centre, not a second hole in the grid.
                centre=(np.asarray(match['center'])*match['radius']+np.asarray(candidate['center'])*candidate['radius'])/(match['radius']+candidate['radius'])
                radius=max(match['radius'],candidate['radius'])
                match.update(center=centre.tolist(),radius=radius,
                             bbox=[centre[0]-radius,centre[1]-radius,centre[0]+radius,centre[1]+radius])
        projections=matching_thin_projections(plate,projection_lines or axis_lines,clean,rectangles)
        for projection in projections:
            projection['view_relation']['line_collection']='vector_axis_lines' if projection_lines else 'axis_lines'
        result.extend(projections)
        result.extend(capsules)
        result.extend(circles)
    return result
