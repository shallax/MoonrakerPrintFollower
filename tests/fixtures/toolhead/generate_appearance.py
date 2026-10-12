"""Reproduce the owned synthetic CAD material/occurrence fixture with OCP."""
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])  # Verified optional runtime, never Cura's ABI.
from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TOC_sRGB
from OCP.STEPCAFControl import STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs
from OCP.TCollection import TCollection_ExtendedString, TCollection_HAsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS_Compound
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorSurf
from OCP.gp import gp_Ax1, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec

document = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
shapes = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
materials = XCAFDoc_DocumentTool.MaterialTool_s(document.Main())
colours = XCAFDoc_DocumentTool.ColorTool_s(document.Main())


def compound(name):
    shape = TopoDS_Compound()
    BRep_Builder().MakeCompound(shape)
    label = shapes.AddShape(shape, True)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    return label


def part(shape, name, material, colour):
    label = shapes.AddShape(shape, False)
    TDataStd_Name.Set_s(label, TCollection_ExtendedString(name))
    assigned = materials.AddMaterial(TCollection_HAsciiString(material), TCollection_HAsciiString("Fixture material"),
        1., TCollection_HAsciiString("g/cm3"), TCollection_HAsciiString("mass density"))
    materials.SetMaterial(label, assigned)
    colours.SetColor(label, Quantity_ColorRGBA(Quantity_Color(*colour[:3], Quantity_TOC_sRGB), colour[3]), XCAFDoc_ColorSurf)
    return label


def translation(x, y, z):
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(x, y, z))
    return TopLoc_Location(transform)


housing = part(BRepPrimAPI_MakeBox(6, 4, 3).Shape(), "Printed housing", "ABS", (.08, .08, .08, 1.))
rotor = part(BRepPrimAPI_MakeCylinder(1, 1).Shape(), "Rotor definition", "Aluminium", (.8, .8, .8, 1.))
glass = part(BRepPrimAPI_MakeBox(2, 2, .5).Shape(), "Window", "Glass", (.2, .5, .9, .35))
assembly = compound("Material assembly")
shapes.AddComponent(assembly, housing, TopLoc_Location())
shapes.AddComponent(assembly, glass, translation(3, 0, 4))
nested = compound("Nested fan")
inner = shapes.AddComponent(nested, rotor, translation(10, 0, 0))
TDataStd_Name.Set_s(inner, TCollection_ExtendedString("Fan rotor"))
rotation = gp_Trsf()
rotation.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), 1.5707963267948966)
shapes.AddComponent(assembly, nested, TopLoc_Location(rotation))
shapes.AddComponent(assembly, nested, translation(0, 20, 0))
# Identical placement must still retain a separately selectable occurrence.
shapes.AddComponent(assembly, nested, translation(0, 20, 0))
shapes.UpdateAssemblies()
writer = STEPCAFControl_Writer()
writer.SetColorMode(True)
writer.SetNameMode(True)
writer.SetMaterialMode(True)
assert writer.Transfer(document, STEPControl_AsIs)
print(writer.Write(str(Path(sys.argv[2]))))
