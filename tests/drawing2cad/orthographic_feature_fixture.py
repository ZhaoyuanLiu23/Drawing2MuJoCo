"""Independent, dimension-driven drawings for projection and callout tests."""
from matplotlib.font_manager import findfont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def draw_orthographic_features(path, direction, *, scale=1., legacy_slot=False):
    # All fixture values are test inputs; production never imports this module.
    sizes=dict(plate_width=180.*scale,plate_height=70.*scale,thickness=4.*scale,
               hole_diameter=6.*scale,slot_length=28.*scale,slot_width=9.*scale)
    holes=[(18.,16.),(90.,16.),(162.,54.)]; slots=[(100.,42.)]
    left,top,right,bottom=220.,260.,580.,400.
    sides={'left':(80,top,88,bottom),'right':(700,top,708,bottom),
           'above':(left,90,right,98),'below':(left,540,right,548)}
    c=canvas.Canvas(str(path),pagesize=(800,640),pageCompression=0)
    pdfmetrics.registerFont(TTFont('OrthographicFixture',findfont('DejaVu Sans')))

    def line(x,y,X,Y,shade=.35,width=.45):
        c.setStrokeGray(shade);c.setLineWidth(width);c.line(x,640-y,X,640-Y)

    def text(x,y,value,vertical=False,center=True):
        c.saveState();c.setFont('OrthographicFixture',9);c.setFillGray(0);c.translate(x,640-y)
        if vertical:c.rotate(90)
        (c.drawCentredString if center else c.drawString)(0,0,str(value));c.restoreState()

    def rectangle(box):
        x,y,X,Y=box;c.setFillGray(.8);c.setStrokeGray(0);c.setLineWidth(1)
        c.rect(x,640-Y,X-x,Y-y,fill=1,stroke=1)

    def cross(x,y):
        line(x-11,y,x+11,y);line(x,y-11,x,y+11)

    def dimx(points,values,y,anchor):
        for x in points:line(x,y-6,x,anchor)
        for a,b,value in zip(points,points[1:],values):
            label=f'{value:g}';mid=(a+b)/2
            half=pdfmetrics.stringWidth(label,'OrthographicFixture',9)/2+4
            line(a,y,mid-half,y);line(mid+half,y,b,y)
            text(mid,y+3,label)

    def dimy(points,values,x,anchor):
        line(x,points[0]-5,x,points[-1]+5)
        for y in points:line(x-6,y,anchor,y)
        for a,b,value in zip(points,points[1:],values):text(x-4,(a+b)/2,f'{value:g}',vertical=True)

    def chain(total,points):
        locations=[0.,*sorted(set(points)),total]
        return [(b-a)*scale for a,b in zip(locations,locations[1:])]

    text(410,35,'DIMENSIONS IN mm')
    rectangle((left,top,right,bottom));rectangle(sides[direction])
    for x,y in holes:
        px,py=left+2*x,top+2*y
        c.setStrokeGray(0);c.setLineWidth(1);c.circle(px,640-py,6,fill=0,stroke=1);cross(px,py)
    for x,y in slots:
        px,py=left+2*x,top+2*y
        c.setStrokeGray(0);c.setFillGray(1);c.setLineWidth(1)
        c.roundRect(px-28,640-(py+9),56,18,9,fill=1,stroke=1);cross(px,py)
    dimx([left,right],[sizes['plate_width']],185,top)
    dimy([top,bottom],[sizes['plate_height']],148,left)
    for axis,points,total,baseline in [(0,[p[0] for p in holes],180.,225),
                                     (1,[p[1] for p in holes],70.,183)]:
        origin=left if axis==0 else top
        pixels=[origin]+[origin+2*v for v in sorted(set(points))]+[right if axis==0 else bottom]
        if axis==0:dimx(pixels,chain(total,points),baseline,top)
        else:dimy(pixels,chain(total,points),baseline,left)
    dimx([left,left+2*slots[0][0],right],chain(180.,[slots[0][0]]),436,bottom)
    dimy([top,top+2*slots[0][1],bottom],chain(70.,[slots[0][1]]),626,right)
    a,b,A,B=sides[direction]
    if direction in ('left','right'):
        # Text outside a narrow dimension span is a normal drafting practice.
        line(a-12,238,A+28,238);line(a,230,a,top);line(A,230,A,top)
        text(A+16,234,f"{sizes['thickness']:g}")
    else:
        dimy([b,B],[sizes['thickness']],198,left)
    label=(f"{len(slots)}-SLOT({sizes['slot_width']:g}x{sizes['slot_length']:g})" if legacy_slot else
           f"{len(slots)}X SLOT {sizes['slot_length']:g} × {sizes['slot_width']:g} THRU")
    text(240,482,label,center=False)
    line(238,481,211,462);line(211,462,left+2*slots[0][0]-28,top+2*slots[0][1])
    text(568,492,f"{len(holes)}X Ø{sizes['hole_diameter']:g} THRU",center=False)
    line(567,491,554,448);line(554,448,left+2*holes[-1][0],top+2*holes[-1][1]+6)
    c.showPage();c.save()
    return sizes,[(x*scale,y*scale) for x,y in holes],[(x*scale,y*scale) for x,y in slots]
