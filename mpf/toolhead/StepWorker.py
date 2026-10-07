"""Standalone, stdlib-only STEP helper; executed with an isolated pinned Python.

No Qt, Cura, NumPy or parent-process module imports. Native faults and opaque
CAD calls stay in this disposable child; the parent can terminate it on cancel.
"""
from __future__ import annotations

from array import array
import os
import struct
import sys
import time

MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_TRIANGLES = 1_000_000

def convert(path, runtime, output):
    next_report = 0.
    def report(text, *, force=True):
        nonlocal next_report
        now = time.monotonic()
        if not force and now < next_report: return
        next_report = now + .25
        # Atomic replacement keeps the parent's bounded read independent of
        # native diagnostics and avoids a pipe that could block CAD conversion.
        with open(output + ".progress.tmp", "w", encoding="utf-8") as status:
            status.write(text)
        os.replace(output + ".progress.tmp", output + ".progress")

    report("Reading STEP file…")
    if os.path.getsize(path) > MAX_FILE_BYTES: raise ValueError("Model file exceeds 128 MiB")
    with open(path, "rb") as source:
        if b"ISO-10303-21" not in source.read(4096): raise ValueError("Expected a STEP/STP Part 21 model")
    sys.path.insert(0, runtime)
    try:
        from OCP.STEPCAFControl import STEPCAFControl_Reader
        from OCP.STEPConstruct import STEPConstruct_ExternRefs
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.TDocStd import TDocStd_Document
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.TDF import TDF_LabelSequence, TDF_Label
        from OCP.XCAFDoc import XCAFDoc_DocumentTool
        from OCP.XCAFPrs import XCAFPrs, XCAFPrs_IndexedDataMapOfShapeStyle
        from OCP.Quantity import Quantity_TOC_sRGB
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
        from OCP.TopoDS import TopoDS, TopoDS_Iterator
        from OCP.TopLoc import TopLoc_Location
        from OCP.BRep import BRep_Tool
    finally:
        sys.path.remove(runtime)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone: raise ValueError("Could not read STEP model")
    reader.Reader().SetSystemLengthUnit(1.0)  # OCCT target units: millimetres
    external = STEPConstruct_ExternRefs(reader.Reader().WS())
    external.LoadExternRefs()
    if external.NbExternRefs(): raise ValueError("STEP model references external files; export a self-contained assembly")
    document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    report("Building CAD geometry…")
    if not reader.Transfer(document): raise ValueError("Could not convert STEP model")
    shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    shapes.GetFreeShapes(roots)
    triangles, palette, surfaces = array("f"), array("f"), array("I")
    triangle_count = 0
    nodes = faces = 0

    def validate(label, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 4096 or depth > 32: raise ValueError("STEP assembly exceeds the part or nesting limit")
        if shapes.IsReference_s(label):
            referred = TDF_Label()
            if not shapes.GetReferredShape_s(label, referred): raise ValueError("Broken STEP assembly reference")
            validate(referred, depth+1)
        elif shapes.IsAssembly_s(label):
            children = TDF_LabelSequence()
            shapes.GetComponents_s(label, children)
            for index in range(1, children.Length()+1): validate(children.Value(index), depth+1)

    topology_nodes = 0
    def walk(shape, styles, inherited, depth=0):
        nonlocal topology_nodes, faces, triangle_count
        topology_nodes += 1
        if topology_nodes > 2_000_000 or depth > 64: raise ValueError("STEP topology exceeds the nesting or shape limit")
        if styles.Contains(shape):
            style = styles.FindFromKey(shape)
            if not style.IsVisible(): return
            if style.IsSetColorSurf():
                value = style.GetColorSurfRGBA()
                inherited = (*value.GetRGB().Values(Quantity_TOC_sRGB), value.Alpha())
        if shape.ShapeType() != TopAbs_FACE:
            # Iterator accumulates both location and orientation. Styles can
            # live on compounds, solids, shells or individual faces.
            children = TopoDS_Iterator(shape)
            while children.More():
                walk(children.Value(), styles, inherited, depth+1)
                children.Next()
            return
        faces += 1
        if faces > 50_000: raise ValueError("STEP model exceeds 50,000 faces")
        face = TopoDS.Face_s(shape)
        local = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, local)
        if mesh is None: return
        if triangle_count+mesh.NbTriangles() > MAX_TRIANGLES: raise ValueError("Model exceeds 1,000,000 triangles")
        transform = local.Transformation()
        for index in range(1, mesh.NbTriangles()+1):
            ids = list(mesh.Triangle(index).Get())
            if face.Orientation() == TopAbs_REVERSED: ids[1], ids[2] = ids[2], ids[1]
            points = [mesh.Node(i).Transformed(transform) for i in ids]
            triangles.extend(value for p in points for value in (p.X(), p.Y(), p.Z()))
            palette.extend(inherited)
            surfaces.append(faces)
            triangle_count += 1
            if triangle_count % 4096 == 0:
                report(f"Building display mesh · {triangle_count:,} triangles", force=False)

    report("Checking assembly…")
    for index in range(1, roots.Length()+1): validate(roots.Value(index))
    for index in range(1, roots.Length()+1):
        label = roots.Value(index)
        styles = XCAFPrs_IndexedDataMapOfShapeStyle()
        report("Preparing model colours…")
        XCAFPrs.CollectStyleSettings_s(label, TopLoc_Location(), styles)
        shape = shapes.GetShape_s(label)
        report(f"Meshing assembly · {index} of {roots.Length()}")
        BRepMesh_IncrementalMesh(shape, 0.25, False, 0.5, True)
        report(f"Building display mesh · {triangle_count:,} triangles")
        walk(shape, styles, (0.72, 0.74, 0.78, 1))
    if not triangle_count: raise ValueError("STEP model has no surfaces")
    if sys.byteorder != "little":
        triangles.byteswap()
        palette.byteswap()
        surfaces.byteswap()
    report(f"Writing display mesh · {triangle_count:,} triangles")
    with open(output, "wb") as handle:
        handle.write(struct.pack("<8sI", b"MPFHEAD2", triangle_count))
        triangles.tofile(handle)
        palette.tofile(handle)
        surfaces.tofile(handle)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4: raise ValueError("Invalid STEP helper request")
        convert(*sys.argv[1:])
    except Exception as error:
        print(str(error)[:1024], file=sys.stderr)
        sys.exit(1)
