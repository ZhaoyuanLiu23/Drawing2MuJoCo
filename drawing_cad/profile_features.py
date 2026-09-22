"""Compose line/arc extrusion and located circular-hole features from projections."""
import numpy as np


class ProfileExtrusionRecognizer:
    name='line_arc_profile_extrusion'

    def recognize(self,geometry):
        shapes=geometry['primitives']
        candidates=[]
        for profile in (p for p in shapes if p['kind']=='rounded_rectangle'):
            x0,y0,x1,y1=profile['bbox']; w=x1-x0; h=y1-y0
            holes=[p for p in shapes if p['kind']=='circle' and
                   p['bbox'][0]>x0 and p['bbox'][2]<x1 and p['bbox'][1]>y0 and p['bbox'][3]<y1]
            if len(holes)!=1:
                continue
            tops=[]; sides=[]
            for rect in (p for p in shapes if p['kind']=='rectangle'):
                a,b,c,d=rect['bbox']; rw=c-a; rh=d-b
                if abs(rw/w-1)<.05 and abs((a+c-x0-x1)/2)<w*.03 and d<y0 and rh<h*.6:
                    tops.append(rect)
                if abs(rh/h-1)<.05 and abs((b+d-y0-y1)/2)<h*.03 and a>x1 and rw<w*.5:
                    sides.append(rect)
            if len(tops)==1 and len(sides)==1:
                # Orthographic thickness corroboration; never a physical-size estimate.
                t=tops[0]['bbox']; s=sides[0]['bbox']
                if abs((t[3]-t[1])/(s[2]-s[0])-1)<.15:
                    candidates.append((profile,holes[0],tops[0],sides[0]))
        if len(candidates)!=1:
            return dict(status='unknown',recognizer=self.name,views=[],measurements=[],
                        reason='Need an unambiguous line/arc profile, one circular hole, and aligned top/right projections.')
        profile,hole,top,side=candidates[0]
        x0,y0,x1,y1=profile['bbox']; a,b,c,d=side['bbox']; t=top['bbox']
        def span(name,shape,axis,lo,hi,anchor,**extra):
            return dict(parameter=name,geometry_id=shape['id'],axis=axis,lo=lo,hi=hi,anchor_range=anchor,**extra)
        measurements=[span('width',profile,0,x0,x1,[y0,y1],baseline_outside_anchor=True),
                      span('height',profile,1,y0,y1,[x0,x1],baseline_outside_anchor=True),
                      span('thickness',side,0,a,c,[b,d],baseline_outside_anchor=True),
                      span('hole_x',hole,0,x0,hole['center'][0],[t[1],t[3]],projection_view='view_top'),
                      span('hole_y_from_top',hole,1,y0,hole['center'][1],[a,c],projection_view='view_side'),
                      span('hole_diameter',hole,1,hole['bbox'][1],hole['bbox'][3],[a,c],
                           projection_view='view_side',requires_through=True),
                      dict(parameter='corner_radius',geometry_id=profile['id'],kind='radius_leader',arc=profile['arc'])]
        views=[dict(id=name,type=kind,bbox=p['bbox'],contour_ids=[p['id']],
                    hole_ids=[hole['id']] if name=='view_front' else [],confidence=p['confidence'])
               for name,kind,p in [('view_front','orthographic_line_arc_profile',profile),
                                   ('view_top','orthographic_top',top),('view_side','orthographic_side',side)]]
        return dict(status='recognized',recognizer=self.name,views=views,measurements=measurements,
                    required_parameters=[m['parameter'] for m in measurements],outer_contour=profile['id'],
                    holes=[hole['id']],corner_index=profile['corner_index'],confidence=.86,
                    geometric_features=[dict(kind='line_arc_profile',contour_id=profile['id'],corner_index=profile['corner_index']),
                                        dict(kind='linear_extrusion',depth_parameter='thickness'),
                                        dict(kind='located_circular_hole',contour_id=hole['id'],
                                             parameters=['hole_x','hole_y_from_top','hole_diameter'],
                                             depth_evidence='THRU annotation required before CAD generation')],
                    assumptions=['Matched orthographic views describe one constant-thickness extrusion.',
                                 'The detected corner arc is tangent to its two adjacent straight edges.'],
                    unknown=['Unspecified bevels, threads, hidden steps and material are unknown.'])
