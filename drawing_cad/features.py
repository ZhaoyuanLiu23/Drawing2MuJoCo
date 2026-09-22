"""Feature registry for evidence-based circular and line/arc profile extrusions."""
import numpy as np
from .profile_features import ProfileExtrusionRecognizer
from .pattern_features import PatternPlateRecognizer


class CircularExtrusionRecognizer:
    name = "circular_profile_extrusion"

    def recognize(self, geometry):
        circles = [p for p in geometry["primitives"] if p["kind"] == "circle"]
        rectangles = [p for p in geometry["primitives"] if p["kind"] == "rectangle"]
        candidates = []
        for outer in circles:
            inside = [c for c in circles if c["id"] != outer["id"] and
                      np.linalg.norm(np.array(c["center"])-outer["center"])+c["radius"] < outer["radius"]*1.02]
            if len(inside)>1 or any(np.linalg.norm(np.array(c["center"])-outer["center"]) > outer["radius"]*.025+2 for c in inside):
                continue
            diameter = 2*outer["radius"]
            sides = []
            for rect in rectangles:
                x0,y0,x1,y1 = rect["bbox"]
                sizes = np.array([x1-x0,y1-y0])
                long_axis = int(np.argmax(sizes))
                if sizes.min()/sizes.max() > 0.75:
                    continue
                center = np.array([(x0+x1)/2,(y0+y1)/2])
                same_projection = abs(center[long_axis]-outer["center"][long_axis]) < 0.06*diameter+3
                separate = np.linalg.norm(center-outer["center"]) > outer["radius"] + sizes.min()/2
                if abs(sizes.max()/diameter-1) < 0.045 and same_projection and separate:
                    sides.append(rect)
            if len(sides)==1:
                candidates.append((outer,inside,sides[0]))
        if len(candidates) != 1:
            return dict(status="unknown", recognizer=self.name, views=[], measurements=[],
                        reason="Need one unambiguous circular profile and one aligned rectangular side view; unsupported or ambiguous topology.")
        outer, holes, side = candidates[0]
        measurements = []
        for name, contour in [("outer_diameter",outer)] + ([("inner_diameter",holes[0])] if holes else []):
            for axis in (0,1):
                measurements.append(dict(parameter=name, geometry_id=contour["id"], axis=axis,
                                         lo=contour["bbox"][axis], hi=contour["bbox"][axis+2],
                                         anchor_range=[contour["center"][1-axis]]*2))
        x0,y0,x1,y1 = side["bbox"]
        short_axis = int((y1-y0) < (x1-x0))
        measurements.append(dict(parameter="thickness", geometry_id=side["id"], axis=short_axis,
                                 lo=side["bbox"][short_axis],hi=side["bbox"][short_axis+2],
                                 anchor_range=[side["bbox"][1-short_axis],side["bbox"][3-short_axis]]))
        views = [dict(id="view_front", type="orthographic_circular_profile", bbox=outer["bbox"],
                      contour_ids=[outer["id"]], hole_ids=[h["id"] for h in holes], confidence=outer["confidence"]),
                 dict(id="view_side",type="orthographic_side",bbox=side["bbox"],
                      contour_ids=[side["id"]],hole_ids=[],confidence=side["confidence"])]
        ellipses = [p for p in geometry["primitives"] if p["kind"]=="ellipse"]
        if ellipses:
            boxes=np.array([e["bbox"] for e in ellipses])
            views.append(dict(id="view_oblique",type="oblique_candidate",confidence=0.75,
                              bbox=[*boxes[:,:2].min(axis=0).tolist(),*boxes[:,2:].max(axis=0).tolist()],
                              contour_ids=[e["id"] for e in ellipses],hole_ids=[],used_for_dimensions=False,
                              inference="Elliptical projected curves; projection convention is not established."))
        assumptions=["The circular profile has constant cross-section through the rectangular side-view thickness."]
        geometric_features=[dict(kind="constant_extrusion",status="inferred",confidence=.80,
                                 contour_id=outer["id"],side_view_id="view_side")]
        if holes:
            assumptions.append("Nested concentric loops are interpreted as through-holes; no depth section is supplied.")
            geometric_features.append(dict(kind="concentric_through_hole",status="inferred",confidence=.75,
                                           contour_id=holes[0]["id"],depth_evidence="unknown; through-depth is an explicit inference"))
        return dict(status="recognized", recognizer=self.name, views=views, measurements=measurements,
                    outer_contour=outer["id"], holes=[h["id"] for h in holes], side_contour=side["id"],
                    confidence=min(outer["confidence"],side["confidence"],0.9),
                    geometric_features=geometric_features,assumptions=assumptions,
                    unknown=["Unspecified edge fillets/chamfers and hidden internal features are not reconstructed.",
                             "Material properties and manufacturing process are unknown."])


RECOGNIZERS = [CircularExtrusionRecognizer(),ProfileExtrusionRecognizer(),PatternPlateRecognizer()]


def recognize_features(geometry):
    results = [r.recognize(geometry) for r in RECOGNIZERS]
    recognized = [r for r in results if r["status"] == "recognized"]
    if len(recognized) == 1:
        return recognized[0]
    return results[0] if not recognized else dict(status="unknown", views=[],measurements=[],reason="Conflicting feature recognizers.")
