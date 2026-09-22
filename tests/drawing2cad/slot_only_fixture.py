"""Independent drawings with specified plate dimensions and unannotated slots."""
from matplotlib.font_manager import findfont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


def draw_slot_only(path, *, width=100., height=25., thickness=6.,
                   slots=((25.,12.5,24.,10.),(75.,12.5,24.,10.)), note=''):
    # Physical fixture data only; the application does not import this module.
    scale=3.; left,top=350.,320.; right,bottom=left+width*scale,top+height*scale
    side_y=150.; side_bottom=side_y+thickness*scale
    page_height=700.
    c=canvas.Canvas(str(path),pagesize=(1200,page_height),pageCompression=0)
    pdfmetrics.registerFont(TTFont('SlotOnlyFixture',findfont('DejaVu Sans')))

    def line(x,y,X,Y):
        c.setLineWidth(.45);c.setStrokeGray(.2);c.line(x,page_height-y,X,page_height-Y)

    def text(x,y,label,vertical=False):
        c.saveState();c.setFillGray(0);c.setFont('SlotOnlyFixture',10);c.translate(x,page_height-y)
        if vertical:c.rotate(90)
        c.drawCentredString(0,0,label);c.restoreState()

    def rect(x,y,w,h):
        c.setLineWidth(1);c.setStrokeGray(0);c.setFillGray(.8)
        c.rect(x,page_height-y-h,w,h,fill=1,stroke=1)

    rect(left,top,width*scale,height*scale)
    rect(left,side_y,width*scale,thickness*scale)
    for x,y,length,diameter in slots:
        c.setLineWidth(1);c.setStrokeGray(0);c.setFillGray(1)
        c.roundRect(left+(x-length/2)*scale,page_height-top-(y+diameter/2)*scale,
                    length*scale,diameter*scale,diameter*scale/2,fill=1,stroke=1)
    label=f'{width:.2f}mm±0.20';mid=(left+right)/2;dim_y=270.
    half=pdfmetrics.stringWidth(label,'SlotOnlyFixture',10)/2+5
    line(left,dim_y-8,left,top-3);line(right,dim_y-8,right,top-3)
    line(left,dim_y,mid-half,dim_y);line(mid+half,dim_y,right,dim_y)
    text(mid,dim_y+3,label)
    dim_x=300.
    line(dim_x,top-5,dim_x,bottom+5)
    line(dim_x-5,top,left-3,top);line(dim_x-5,bottom,left-3,bottom)
    text(dim_x-4,(top+bottom)/2,f'{height:.2f}mm±0.20',vertical=True)
    # Narrow side: outside vertical dimension with a connected horizontal label tail.
    dx=right+30
    line(right+3,side_y,dx+5,side_y);line(right+3,side_bottom,dx+5,side_bottom)
    line(dx,side_y-5,dx,side_bottom+20);line(dx,side_bottom+20,dx+10,side_bottom+20)
    text(dx+50,side_bottom+23,f'{thickness:.2f}mm±0.50')
    if note:text(600,80,note)
    c.showPage();c.save()
    return path
