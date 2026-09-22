"""Detect an axis-aligned closed profile with one tangent quarter-circle corner.

Pixel radii describe evidence only. CAD radii must come from associated R labels.
"""
import cv2
import numpy as np


def rounded_profiles(binary):
    closed=cv2.morphologyEx(binary,cv2.MORPH_CLOSE,np.ones((5,5),np.uint8))
    contours,_=cv2.findContours(closed,cv2.RETR_LIST,cv2.CHAIN_APPROX_NONE)
    result=[]
    for contour in contours:
        x,y,w,h=cv2.boundingRect(contour)
        area=cv2.contourArea(contour)
        if min(w,h)<40 or area<1000 or area/(w*h)<.78:
            continue
        if area/max(1,cv2.contourArea(cv2.convexHull(contour)))<.97:
            continue
        p=contour[:,0].astype(float)
        corners=np.array([[x,y],[x+w-1,y],[x+w-1,y+h-1],[x,y+h-1]],dtype=float)
        gaps=np.array([np.linalg.norm(p-q,axis=1).min() for q in corners])
        curved=np.flatnonzero(gaps>max(4,.025*min(w,h)))
        if len(curved)!=1:
            continue
        index=int(curved[0]); corner=corners[index]
        estimate=gaps[index]/(np.sqrt(2)-1)
        if not 7<estimate<min(w,h)*.5:
            continue
        inward=np.array([1 if index in (0,3) else -1,1 if index in (0,1) else -1])
        local=(p-corner)*inward
        arc=p[(local[:,0]>2)&(local[:,1]>2)&(local[:,0]<estimate*1.25)&(local[:,1]<estimate*1.25)]
        if len(arc)<12:
            continue
        origin=arc.mean(axis=0); a=arc-origin
        cx,cy,k=np.linalg.lstsq(np.c_[2*a,np.ones(len(a))],(a*a).sum(axis=1),rcond=None)[0]
        radius=float(np.sqrt(max(0,k+cx*cx+cy*cy))); center=origin+[cx,cy]
        residual=float(np.sqrt(np.mean((np.linalg.norm(arc-center,axis=1)-radius)**2))/max(radius,1))
        if residual>.045 or np.max(np.abs((center-corner)*inward-radius))>radius*.20:
            continue
        # Validate the entire closed outline against four straight edges and this arc.
        distances=np.c_[abs(p[:,0]-x),abs(p[:,0]-(x+w-1)),abs(p[:,1]-y),abs(p[:,1]-(y+h-1))]
        distance=np.minimum(distances.min(axis=1),abs(np.linalg.norm(p-center,axis=1)-radius))
        if np.quantile(distance,.99)>max(3,min(w,h)*.012):
            continue
        bbox=[float(x),float(y),float(x+w-1),float(y+h-1)]
        if any(np.max(np.abs(np.array(v['bbox'])-bbox))<7 for v in result):
            continue
        result.append(dict(kind='rounded_rectangle',bbox=bbox,confidence=.88,
                           source='closed_contour_line_arc_fit',corner_index=index,
                           arc=dict(center=center.tolist(),radius_px=radius,fit_residual=residual,
                                    points=arc[::max(1,len(arc)//32)].tolist()),
                           inference='Four axis-aligned edges with one tangent quarter-circle corner.'))
    return result
