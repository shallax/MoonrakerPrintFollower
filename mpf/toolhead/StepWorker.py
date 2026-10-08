"""Standalone, stdlib-only STEP helper; executed with an isolated pinned Python.

No Qt, Cura, NumPy or parent-process module imports. Native faults and opaque
CAD calls stay in this disposable child; the parent can terminate it on cancel.
"""
from __future__ import annotations

from array import array
import importlib.util
import math
import os
from pathlib import Path
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
        from OCP.XCAFDoc import XCAFDoc, XCAFDoc_DocumentTool, XCAFDoc_Material
        from OCP.TDataStd import TDataStd_Name, TDataStd_TreeNode
        from OCP.TopTools import TopTools_IndexedMapOfShape
        from OCP.XCAFPrs import XCAFPrs, XCAFPrs_IndexedDataMapOfShapeStyle
        from OCP.Quantity import Quantity_TOC_sRGB
        from OCP.BRepMesh import BRepMesh_IncrementalMesh
        from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_SHELL
        from OCP.TopoDS import TopoDS, TopoDS_Iterator
        from OCP.TopLoc import TopLoc_Location
        from OCP.BRep import BRep_Tool
        from OCP.BRepAdaptor import BRepAdaptor_Surface
        from OCP.GeomAbs import GeomAbs_Cylinder
    finally:
        sys.path.remove(runtime)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetMatMode(True)
    reader.SetNameMode(True)
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
    # Load only the stdlib format contract, not the plugin package. Python -I
    # intentionally excludes sibling modules from its import search path.
    specification = importlib.util.spec_from_file_location("mpf_mesh_format",
        Path(__file__).parents[1] / "geometry" / "ToolheadMeshFormat.py")
    format_ = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(format_)
    specification = importlib.util.spec_from_file_location("mpf_rotor_axis",
        Path(__file__).parents[1] / "geometry" / "ToolheadRotorAxis.py")
    axis_module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(axis_module)
    body_bounds, body_axes = {}, {}
    triangles, palette, surfaces = array("f"), array("f"), array("I")
    material_ids, body_ids = array("I"), array("I")
    metadata = {"materials": [dict(format_.UNKNOWN_METADATA["materials"][0])], "bodies": []}
    materials = {( "Unknown material", "", "unknown"): 0}
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

    def annotations(label):
        name, material = "", None
        # Some OCP output-handle overloads crash natively on absent attributes;
        # IsAttribute is required, not a Python exception handler.
        if label.IsAttribute(TDataStd_Name.GetID_s()):
            attribute = TDataStd_Name()
            if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
                name = format_.display_name(attribute.Get().ToExtString(), "")
                if name.startswith("=>["): name = ""
        guid = XCAFDoc.MaterialRefGUID_s()
        if label.IsAttribute(guid):
            reference = TDataStd_TreeNode()
            if label.FindAttribute(guid, reference) and reference.HasFather():
                father = reference.Father().Label()
                if father.IsAttribute(XCAFDoc_Material.GetID_s()):
                    value = XCAFDoc_Material()
                    if father.FindAttribute(XCAFDoc_Material.GetID_s(), value):
                        title, description = value.GetName(), value.GetDescription()
                        material = (format_.display_name(title.ToCString() if title is not None else "", "Unknown material"),
                                    format_.display_name(description.ToCString() if description is not None else "", ""), "step-material")
        return name, material

    def material_index(value):
        value = value or ("Unknown material", "", "unknown")
        if value not in materials:
            if len(materials) >= format_.MAX_PARTS: raise ValueError("STEP material limit exceeded")
            materials[value] = len(metadata["materials"])
            metadata["materials"].append(dict(zip(("name", "description", "source"), value, strict=True)))
        return materials[value]

    def styled(shape, styles, inherited):
        if styles.Contains(shape):
            style = styles.FindFromKey(shape)
            if not style.IsVisible(): return None
            if style.IsSetColorSurf():
                value = style.GetColorSurfRGBA()
                return (*value.GetRGB().Values(Quantity_TOC_sRGB), value.Alpha())
        return inherited

    def walk(shape, styles, inherited, material, subshapes, submaterials, name, body=None, depth=0):
        nonlocal topology_nodes, faces, triangle_count
        topology_nodes += 1
        if topology_nodes > 2_000_000 or depth > 64: raise ValueError("STEP topology exceeds the nesting or shape limit")
        inherited = styled(shape, styles, inherited)
        if inherited is None: return
        if subshapes.Contains(shape): material = submaterials[subshapes.FindIndex(shape)]
        if body is None and shape.ShapeType() in (TopAbs_SOLID, TopAbs_SHELL, TopAbs_FACE):
            if len(metadata["bodies"]) >= format_.MAX_PARTS: raise ValueError("STEP body limit exceeded")
            # Every traversal is one occurrence, even for coincident instances.
            body = len(metadata["bodies"])
            metadata["bodies"].append(dict(name=name, source="step-name", centre=None, axis=None))
        if shape.ShapeType() != TopAbs_FACE:
            # Iterator accumulates both location and orientation. Styles can
            # live on compounds, solids, shells or individual faces.
            children = TopoDS_Iterator(shape)
            while children.More():
                walk(children.Value(), styles, inherited, material, subshapes, submaterials, name, body, depth+1)
                children.Next()
            return
        faces += 1
        if faces > 50_000: raise ValueError("STEP model exceeds 50,000 faces")
        face = TopoDS.Face_s(shape)
        surface = BRepAdaptor_Surface(face)
        if surface.GetType() == GeomAbs_Cylinder:
            cylinder = surface.Cylinder()
            point, direction = cylinder.Location(), cylinder.Axis().Direction()
            candidates = body_axes.setdefault(body, [])
            if len(candidates) <= 64:
                # The adaptor already applies the located face's placement.
                candidates.append(((point.X(), point.Y(), point.Z()),
                                   (direction.X(), direction.Y(), direction.Z())))
        local = TopLoc_Location()
        mesh = BRep_Tool.Triangulation_s(face, local)
        if mesh is None: return
        if triangle_count+mesh.NbTriangles() > MAX_TRIANGLES: raise ValueError("Model exceeds 1,000,000 triangles")
        transform = local.Transformation()
        for index in range(1, mesh.NbTriangles()+1):
            ids = list(mesh.Triangle(index).Get())
            if face.Orientation() == TopAbs_REVERSED: ids[1], ids[2] = ids[2], ids[1]
            points = [mesh.Node(i).Transformed(transform) for i in ids]
            coordinates = [(p.X(), p.Y(), p.Z()) for p in points]
            low, high = body_bounds.setdefault(body, ([math.inf]*3, [-math.inf]*3))
            for coordinate in coordinates:
                for axis in range(3):
                    low[axis] = min(low[axis], coordinate[axis])
                    high[axis] = max(high[axis], coordinate[axis])
            triangles.extend(value for point in coordinates for value in point)
            palette.extend(inherited)
            surfaces.append(faces)
            material_ids.append(material_index(material))
            body_ids.append(body)
            triangle_count += 1
            if triangle_count % 4096 == 0:
                report(f"Building display mesh · {triangle_count:,} triangles", force=False)

    def occurrence(label, parent, styles, colour, inherited_material=None, preferred_name=None, override_material=None):
        name, authored = annotations(label)
        located = shapes.GetShape_s(label).Moved(parent)
        colour = styled(located, styles, colour)
        if colour is None: return
        if shapes.IsReference_s(label):
            referred = TDF_Label()
            shapes.GetReferredShape_s(label, referred)
            # GetShape(component) above already has its own placement. For
            # traversal use the definition plus this actual location chain.
            occurrence(referred, parent.Multiplied(shapes.GetLocation_s(label)), styles, colour,
                       inherited_material, preferred_name or name, authored or override_material)
        elif shapes.IsAssembly_s(label):
            children = TDF_LabelSequence()
            shapes.GetComponents_s(label, children)
            material = override_material or authored or inherited_material
            for index in range(1, children.Length()+1):
                occurrence(children.Value(index), parent, styles, colour, material)
        else:
            name = preferred_name or name or "Part"
            material = override_material or authored or inherited_material or (name, "", "step-name")
            labels = TDF_LabelSequence()
            shapes.GetSubShapes_s(label, labels)
            subshapes, submaterials = TopTools_IndexedMapOfShape(), {}
            if labels.Length() > 50_000: raise ValueError("STEP subshape metadata limit exceeded")
            for index in range(1, labels.Length()+1):
                sublabel = labels.Value(index)
                _subname, submaterial = annotations(sublabel)
                if submaterial is not None:
                    submaterials[subshapes.Add(shapes.GetShape_s(sublabel).Moved(parent))] = submaterial
            walk(located, styles, colour, material, subshapes, submaterials, name)

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
        occurrence(label, TopLoc_Location(), styles, (0.72, 0.74, 0.78, 1))
    if not triangle_count: raise ValueError("STEP model has no surfaces")
    if sys.byteorder != "little":
        triangles.byteswap()
        palette.byteswap()
        surfaces.byteswap()
        material_ids.byteswap()
        body_ids.byteswap()
    for index, body in enumerate(metadata["bodies"]):
        body["centre"], body["axis"] = axis_module.rotation_axis(body_axes.get(index, []),
            *body_bounds.get(index, (None, None)))
    encoded = format_.encode_metadata(metadata)
    report(f"Writing display mesh · {triangle_count:,} triangles")
    with open(output, "wb") as handle:
        handle.write(format_.HEADER.pack(format_.MAGIC, triangle_count))
        triangles.tofile(handle)
        palette.tofile(handle)
        surfaces.tofile(handle)
        material_ids.tofile(handle)
        body_ids.tofile(handle)
        handle.write(struct.pack("<I", len(encoded)))
        handle.write(encoded)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 4: raise ValueError("Invalid STEP helper request")
        convert(*sys.argv[1:])
    except Exception as error:
        print(str(error)[:1024], file=sys.stderr)
        sys.exit(1)
