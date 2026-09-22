"""Independent, not-to-scale engineering drawing for geometry-pattern regression.

All constants here are TEST geometry/expected labels, never runtime defaults.
The projected outlines stay fixed while every supplied dimension can change.
"""
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from matplotlib.font_manager import findfont

CHANGED=dict(plate_width=200,plate_height=70,thickness=4,
             hole_x_chain_0=20,hole_x_chain_1=50,hole_x_chain_2=60,hole_x_chain_3=50,hole_x_chain_4=20,
             hole_y_chain_0=16,hole_y_chain_1=38,hole_y_chain_2=16,
             slot_x_chain_0=45,slot_x_chain_1=110,slot_x_chain_2=45,
             slot_width=14,slot_length=34,hole_diameter=5.6,countersink_diameter=10,countersink_angle=110)


def draw_pattern_pdf(path,values=CHANGED):
    pdfmetrics.registerFont(TTFont('TestDrawing',findfont('DejaVu Sans')))
    c=canvas.Canvas(str(path),pagesize=(500,500),pageCompression=0)
    def line(x,y,X,Y,shade=.35,width=.45):
        c.setStrokeGray(shade);c.setLineWidth(width);c.line(x,500-y,X,500-Y)
    def text(x,y,word,size=12,vertical=False):
        c.saveState();c.setFillGray(0);c.setFont('TestDrawing',size)
        c.translate(x,500-y)
        if vertical:c.rotate(90)
        c.drawCentredString(0,0,str(word));c.restoreState()
    def rect(x,y,X,Y,fill=.80):
        c.setStrokeGray(0);c.setLineWidth(1);c.setFillGray(fill);c.rect(x,500-Y,X-x,Y-y,fill=1,stroke=1)
    def cross(x,y,r):
        line(x-r,y,x+r,y,.35,.4);line(x,y-r,x,y+r,.35,.4)
    def label(name):return f'{values[name]:g}'
    def dimx(xs,y,anchor,names):
        line(xs[0],y,xs[-1],y)
        for x in xs:line(x,y-7,x,anchor)
        for lo,hi,name in zip(xs,xs[1:],names):text((lo+hi)/2,y-5,label(name))
    def dimy(ys,x,anchor,names):
        line(x,ys[0],x,ys[-1])
        for y in ys:line(x-6,y,anchor,y)
        for lo,hi,name in zip(ys,ys[1:],names):text(x-5,(lo+hi)/2,label(name),vertical=True)
    text(250,25,'TEST VARIANT / NOT TO SCALE',11)
    rect(80,124,416,238)
    for y in (152,210):
        for x in (108,204,292,388):
            c.setStrokeGray(0);c.setLineWidth(.7);c.circle(x,500-y,6,fill=0);c.circle(x,500-y,3,fill=0)
            cross(x,y,10)
    for x in (156,340):
        c.setFillGray(1);c.setStrokeGray(0);c.setLineWidth(1)
        c.roundRect(x-28,500-192,56,24,12,fill=1,stroke=1);cross(x,180,17)
    line(68,180,431,180,.45,.3)
    rect(80,322,416,328)
    for x in (108,204,292,388):line(x,318,x,332)
    dimx([80,416],69,124,['plate_width'])
    dimx([80,108,204,292,388,416],97,153,[f'hole_x_chain_{i}' for i in range(5)])
    dimy([124,238],22,80,['plate_height'])
    dimy([124,152,210,238],51,108,[f'hole_y_chain_{i}' for i in range(3)])
    dimx([80,156,340,416],272,238,[f'slot_x_chain_{i}' for i in range(3)])
    line(45,292,45,345);line(40,322,80,322);line(40,328,80,328)
    text(39,308,f"{values['thickness']:.1f}",vertical=True)
    text(235,194,f"2-SLOT({label('slot_width')}x{label('slot_length')})",8)
    line(204,197,186,197);line(186,197,173,182)
    text(422,294,f"8-Ø{label('hole_diameter')}(Ø{label('countersink_diameter')})",9)
    line(390,296,382,296);line(382,296,299,217)
    text(426,309,f"CSK {label('countersink_angle')}°",8)
    rect(200,398,245,404);rect(253,398,296,404)
    line(239,398,245,404,0,.6);line(259,398,253,404,0,.6)
    line(245,367,245,404);line(253,367,253,404)
    line(239,375,259,375);line(239,369,239,398);line(259,369,259,398)
    text(249,371,f"Ø{label('countersink_diameter')}",11)
    line(245,386,283,386);line(253,383,253,390)
    text(305,388,f"Ø{label('hole_diameter')}",11)
    text(249,356,f"{label('countersink_angle')}°",12)
    text(248,456,'COUNTERSINK SECTION',9)
    c.showPage();c.save()
    return path
