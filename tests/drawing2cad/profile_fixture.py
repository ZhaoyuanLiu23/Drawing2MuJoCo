"""Test-only annotation changes. Geometry pixels remain unchanged (not to scale).

Coordinates come from the original pipeline's associated text evidence. The
expected values belong to tests, never the production recognizer or CAD builder.
"""
from PIL import Image, ImageDraw, ImageFont
from matplotlib.font_manager import findfont


def reannotate(source,document,path,values,unit_note=None):
    image=Image.open(source).convert('RGB')
    draw=ImageDraw.Draw(image)
    font_path=findfont('DejaVu Sans')
    changes=[]
    for dimension in document['dimensions']:
        association=dimension['association']
        if not association or association['parameter'] not in values:
            continue
        name=association['parameter']; value=values[name]
        # A null test value deliberately removes the annotation.
        text='' if value is None else f'{value:.2f}'
        if text and name=='corner_radius': text='R'+text
        if text and dimension['through']: text='Ø'+text+' THRU'
        box=dimension['bbox']
        if dimension['through']:
            originals=[t['ocr_original']['bbox'] for t in document['texts']
                       if t['id'] in dimension['text_ids'] and t.get('ocr_original')]
            if originals:
                box=[min(b[0] for b in originals),min(b[1] for b in originals),
                     max(b[2] for b in originals),max(b[3] for b in originals)]
        x,y,X,Y=box
        draw.rectangle((x-1,y-1,X+1,Y+1),fill='white')
        if text:
            size=int((Y-y)*.95)
            while size>10:
                font=ImageFont.truetype(font_path,size)
                a,b,c,d=font.getbbox(text)
                if c-a<=X-x and d-b<=Y-y: break
                size-=1
            draw.text(((x+X-c+a)/2-a,(y+Y-d+b)/2-b),text,font=font,fill='black')
        changes.append(dict(parameter=name,old_text=dimension['raw_text'],new_text=text,bbox=box))
    if unit_note:
        draw.text((30,35),'TEST VARIANT / NOT TO SCALE',font=ImageFont.truetype(font_path,22),fill='black')
        draw.text((30,75),'UNITS: '+unit_note,font=ImageFont.truetype(font_path,22),fill='black')
    image.save(path)
    return changes
