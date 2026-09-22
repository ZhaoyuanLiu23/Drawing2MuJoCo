"""Local OCR only; the bundled ONNX models do not send drawing contents elsewhere."""
from functools import lru_cache
import re

import cv2
import numpy as np


@lru_cache(maxsize=1)
def _engine():
    from rapidocr_onnxruntime import RapidOCR
    return RapidOCR(intra_op_num_threads=2, inter_op_num_threads=2)


def read_text(page, mode="auto"):
    if mode == "off" or (mode == "auto" and page.texts):
        return
    results, _ = _engine()(page.image)
    texts = []
    for points, text, confidence in results or []:
        xs, ys = [p[0] for p in points], [p[1] for p in points]
        texts.append(dict(id=f"text_{len(texts)}", text=text, bbox=[min(xs),min(ys),max(xs),max(ys)],
                          confidence=float(confidence), source="ocr"))
    # Diameter glyphs touching a THRU callout can lower whole-line confidence.
    # Re-read the local pixels as separate words; never replace a character with
    # a guessed digit or dimension. Keep the original OCR result as evidence.
    refined=[]
    for token in texts:
        if token['confidence']<.90 and 'THRU' in token['text'].upper():
            x,y,X,Y=map(int,token['bbox']); x=max(0,x-5); y=max(0,y-5)
            words,_=_engine()(page.image[y:Y+5,x:X+5])
            if words and any(w[1].upper()=='THRU' for w in words) and all(w[2]>=.90 for w in words):
                for box,word,score in words:
                    refined.append(dict(text=word,bbox=[min(p[0] for p in box)+x,min(p[1] for p in box)+y,
                                                       max(p[0] for p in box)+x,max(p[1] for p in box)+y],
                                        confidence=float(score),source='ocr',ocr_original=token))
                page.preprocessing.append(dict(operation='local_callout_ocr',original=token,
                                                interpretation='Re-recognized numeric/THRU words; no character substitution.'))
                continue
        refined.append(token)
    # Dimension text is often printed vertically. A second, rotated pass is
    # admitted only for high-confidence numeric tokens whose mapped box is
    # vertical in the source image. This does not substitute any character.
    if not page.paths:
        height,width=page.image.shape[:2]
        scale=3
        rotated=cv2.rotate(page.image,cv2.ROTATE_90_CLOCKWISE)
        rotated=cv2.resize(rotated,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC)
        vertical,_=_engine()(rotated)
        added=[]
        for box,word,score in vertical or []:
            if score<.90 or not re.fullmatch(r'\d+(?:[.,]\d+)?',word.strip()):
                continue
            points=np.asarray(box,dtype=float)/scale
            mapped=np.c_[points[:,1],height-1-points[:,0]]
            candidate=dict(text=word,bbox=[float(mapped[:,0].min()),float(mapped[:,1].min()),
                                           float(mapped[:,0].max()),float(mapped[:,1].max())],
                           confidence=float(score),source='ocr_rotated')
            x,y,X,Y=candidate['bbox']
            if Y-y <= (X-x)*1.15:
                continue
            def overlap(token):
                a,b,A,B=token['bbox']
                area=max(0,min(X,A)-max(x,a))*max(0,min(Y,B)-max(y,b))
                return area/max(1,min((X-x)*(Y-y),(A-a)*(B-b)))
            conflicts=[t for t in refined if overlap(t)>.30]
            if any(t['bbox'][2]-t['bbox'][0]>t['bbox'][3]-t['bbox'][1]
                   and (X-x)*(Y-y)<1.5*(t['bbox'][2]-t['bbox'][0])*(t['bbox'][3]-t['bbox'][1])
                   for t in conflicts):
                continue  # Do not replace a complete horizontal number by a rotated digit fragment.
            if conflicts and any(t['text']==word and t['confidence']>=score for t in conflicts):
                continue
            candidate['ocr_originals']=conflicts
            refined=[t for t in refined if overlap(t)<=.30]
            added.append(candidate)
        refined.extend(added)
        if added:
            page.preprocessing.append(dict(operation='rotated_numeric_ocr',degrees=90,scale=scale,
                                            text_count=len(added),character_substitution=False))
    texts=[dict(t,id=f'text_{i}') for i,t in enumerate(refined)]
    if any(t['confidence']<.90 and re.search(r'\d',t['text']) for t in texts):
        scale=min(3.,max(1.,1800/max(page.image.shape[:2])))
        detailed,_=_engine()(cv2.resize(page.image,None,fx=scale,fy=scale,interpolation=cv2.INTER_CUBIC),
                              return_word_box=True)
        for token in texts:
            x,y,X,Y=token['bbox']
            for box,word,score,*extra in detailed or []:
                points=np.asarray(box)/scale
                a,b=points.min(axis=0); A,B=points.max(axis=0)
                intersection=max(0,min(X,A)-max(x,a))*max(0,min(Y,B)-max(y,b))
                if intersection/max(1,(X-x)*(Y-y))<.55 or len(extra)!=3:
                    continue
                boxes,characters,scores=extra
                token['ocr_detail']=dict(text=word,confidence=float(score),scale=scale,
                    characters=[dict(text=char,confidence=float(conf),bbox=[float(v) for v in
                                  [np.asarray(cb)[:,0].min()/scale,np.asarray(cb)[:,1].min()/scale,
                                   np.asarray(cb)[:,0].max()/scale,np.asarray(cb)[:,1].max()/scale]])
                                for cb,char,conf in zip(boxes,characters,scores)])
                break
        page.preprocessing.append(dict(operation='character_confidence_ocr',scale=scale,
                                        interpretation='Preserve original text; expose separate numeric and uncertain-symbol evidence.'))
    page.texts = texts
    page.preprocessing.append(dict(operation="rapidocr", mode=mode, text_count=len(texts)))
    if not texts:
        page.warnings.append("No text was recognized; dimensions will remain unknown.")
