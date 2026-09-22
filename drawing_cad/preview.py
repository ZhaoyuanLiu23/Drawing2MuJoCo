"""Inspectable source overlay, kernel-mesh PNG and self-contained interactive HTML."""
import html
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


def save_overlay(page, geometry, dimensions, output):
    image=Image.fromarray(page.image)
    draw=ImageDraw.Draw(image)
    for p in geometry["primitives"]:
        if p["kind"]=="circle":
            draw.ellipse(p["bbox"],outline="#147bbd",width=3)
        else:
            draw.rectangle(p["bbox"],outline="#147bbd",width=2)
        draw.text((p["bbox"][0],p["bbox"][1]-13),p["id"],fill="#145b96")
    labels={}
    for d in dimensions:
        color="#138a55" if d["status"]=="recognized" else "#cc6a14"
        draw.rectangle(d["bbox"],outline=color,width=2)
        if d["association"]:
            label=d["association"]["parameter"]
            # A stacked limit pair shares a label below both boxes.
            old=labels.get(label)
            labels[label]=(d["bbox"][0],max(d["bbox"][3],old[1] if old else 0),color)
    for label,(x,y,color) in labels.items():
        draw.text((x,y+3),label,fill=color)
    image.save(output/"evidence.png")


def save_preview(mesh, recipe, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    vertices=np.array(mesh["vertices"])
    triangles=np.array(mesh["triangles"])
    fig=plt.figure(figsize=(9,6),layout="constrained")
    ax=fig.add_subplot(111,projection="3d")
    points=vertices[triangles]
    normals=np.cross(points[:,1]-points[:,0],points[:,2]-points[:,0])
    normals/=np.maximum(1e-12,np.linalg.norm(normals,axis=1)[:,None])
    light=np.array([.3,-.4,1.]);light/=np.linalg.norm(light)
    intensity=.50+.40*np.maximum(0,normals@light)
    colors=np.column_stack([intensity*.25,intensity*.67,intensity*.84,np.ones(len(intensity))])
    if recipe['builder'] in ('line_arc_profile_extrusion','rectangular_plate_feature_pattern'):
        # Remove hidden back faces before the painter sort: long triangles on the
        # bottom face otherwise draw over the top of a perforated flat profile.
        elevation,azimuth=np.radians([30,-55])
        toward_camera=np.array([np.cos(elevation)*np.cos(azimuth),np.cos(elevation)*np.sin(azimuth),np.sin(elevation)])
        visible=normals@toward_camera>0
        points,colors=points[visible],colors[visible]
    ax.add_collection3d(Poly3DCollection(points,facecolors=colors,linewidths=0,rasterized=True,
                                       antialiaseds=False))
    if recipe['builder']=='line_arc_profile_extrusion':
        w,h,t,r,d=(recipe[k+'_mm'] for k in ('width','height','thickness','corner_radius','hole_diameter'))
        margin=max(w,h)*.12
        ax.set(xlim=(-margin,w+margin),ylim=(-margin,h+margin),zlim=(-margin,t+margin))
        ax.set_box_aspect((w+2*margin,h+2*margin,t+2*margin))
        title=f'Line/arc extrusion | {w:g} x {h:g} x {t:g} mm | hole {d:g} | R {r:g}'
        details=html.escape(f'宽 {w:g} · 高 {h:g} · 厚 {t:g} · 孔径 {d:g} · 圆角 R {r:g}（mm）')
    elif recipe['builder']=='rectangular_plate_feature_pattern':
        w,h,t=(recipe[k+'_mm'] for k in ('width','height','thickness'))
        margin=max(w,h)*.10
        ax.set(xlim=(-margin,w+margin),ylim=(-margin,h+margin),zlim=(-margin,t+margin))
        ax.set_box_aspect((w+2*margin,h+2*margin,t+2*margin))
        countersunk=bool(recipe['feature_definitions'].get('hole',{}).get('additions'))
        hole_label='锥形沉孔' if countersunk else '圆柱通孔'
        title=f'Rectangular extrusion | {w:g} x {h:g} x {t:g} mm | slots'
        if recipe['hole_centres_mm']: title+=' + '+('countersinks' if countersunk else 'through holes')
        label=f'宽 {w:g} · 高 {h:g} · 厚 {t:g} mm · {len(recipe["slot_centres_mm"])} 个长圆槽'
        if recipe['hole_centres_mm']:label+=f' · {len(recipe["hole_centres_mm"])} 个{hole_label}'
        if recipe.get('mode')=='inferred_geometry':
            title+=' | inferred geometry';label+=' · 槽尺寸/位置含图形推断值'
        details=html.escape(label)
    else:
        d,h=recipe["outer_diameter_mm"],recipe["thickness_mm"]
        ax.set(xlim=(-d*.6,d*.6),ylim=(-d*.6,d*.6),zlim=(-d*.15,max(h+d*.1,d*.22)))
        ax.set_box_aspect((1.2*d,1.2*d,max(h+d*.25,d*.37)))
        holes=", ".join(f"{v:g}" for v in recipe["hole_diameters_mm"]) or "none"
        title=f"Parametric CAD | OD {d:g} mm | holes {holes} mm | thickness {h:g} mm"
        details=html.escape(f"外径 {d:g} mm · 孔径 {holes} mm · 厚度 {h:g} mm")
    ax.set(xlabel='X (mm)',ylabel='Y (mm)',zlabel='Z (mm)')
    ax.view_init(elev=30,azim=-55)
    fig.suptitle(title)
    fig.text(.5,.015,"Geometry is inferred from drawing evidence. See parsed.json for limits and assumptions.",ha="center",fontsize=8,color="#536272")
    fig.savefig(output/"preview.png",dpi=160)
    plt.close(fig)
    payload=json.dumps(mesh,separators=(",",":")).replace("</","<\\/")
    inferred=[f"{name}: {p['reason']}" for name,p in recipe["parameter_evidence"].items() if p["inferred"]]
    notes=html.escape(" ".join(inferred + recipe["assumptions"]))
    template=(Path(__file__).with_name("preview_template.html")).read_text(encoding="utf-8")
    (output/"preview.html").write_text(template.replace("__MESH__",payload).replace("__DETAILS__",details).replace("__NOTES__",notes),encoding="utf-8")
