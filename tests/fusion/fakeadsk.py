"""A tiny stand-in for Fusion's `adsk` package so Fusion scripts can be smoke-run offline.

This only catches Python mistakes (typos, wrong variable names, broken code paths). It says
nothing about whether the real Fusion API behaves this way; that's what docs/manual-tests.md is for.
"""

import json
import sys
import types


class Collection:
    def __init__(self, items):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class Value:
    objectType = "adsk::cam::FloatParameterValue"

    def __init__(self, value):
        self.value = value


class ChoiceValue(Value):
    objectType = "adsk::cam::ChoiceParameterValue"

    def getChoices(self):
        return True, ["a", "b"], ["'a'", "'b'"]


class Param:
    def __init__(self, name, value, choice=False):
        self.name = name
        self.title = name.title()
        self.expression = repr(value)
        self.isEditable = True
        self.isEnabled = True
        self.value = (ChoiceValue if choice else Value)(value)


class Tool:
    parameters = Collection([Param("tool_diameter", 0.4)])

    def toJson(self):
        return json.dumps({"guid": "7b77ef53-1ace-4e3b-ac2d-380b01a638bd", "description": "4mm 0 flute Aluminum",
                           "post-process": {"number": 1}})


class Op:
    def __init__(self, name):
        self.name = name
        self.objectType = "adsk::cam::Operation"
        self.strategy = "contour2d"
        self.hasToolpath = True
        self.isSuppressed = False
        self.tool = Tool()
        self.parameters = Collection([Param("contours", None), Param("bottomHeight_mode", "'from stock bottom'",
                                                                     choice=True)])


class Setup:
    name = "Setup1"
    stockMode = 3
    parameters = Collection([Param("wcs_origin_boxPoint", "'bottom 1'")])
    allOperations = Collection([Op("[outer] contour"), Op("[drill] holes")])


class Url:
    def __init__(self, s):
        self.s = s

    def toString(self):
        return self.s


class PostLibrary:
    def urlByLocation(self, loc):
        return Url(f"root{loc}")

    def childAssetURLs(self, url):
        return [Url(url.s + "/shopsabre.cps")]

    def childFolderURLs(self, url):
        return []

    def postConfigurationAtURL(self, url):
        return types.SimpleNamespace(description="ShopSabre with automatic mist")


class Program:
    name = "NCProgram1"
    postConfiguration = types.SimpleNamespace(description="ShopSabre with automatic mist")
    parameters = Collection([Param("nc_program_filename", "x")])
    postParameters = Collection([Param("useMist", True)])


class Cam:
    setups = Collection([Setup()])
    ncPrograms = Collection([Program()])


def install():
    """Put fake adsk modules in sys.modules; returns (adsk, messages shown with messageBox)."""
    messages = []
    adsk = types.ModuleType("adsk")
    core = types.ModuleType("adsk.core")
    cam = types.ModuleType("adsk.cam")
    fusion = types.ModuleType("adsk.fusion")

    doc = types.SimpleNamespace(
        name="Manual job 1",
        products=types.SimpleNamespace(itemByProductType=lambda name: Cam() if name == "CAMProductType" else None))
    app = types.SimpleNamespace(
        version="2.0.99999", activeDocument=doc,
        activeProduct=types.SimpleNamespace(unitsManager=types.SimpleNamespace(defaultLengthUnits="in")),
        userInterface=types.SimpleNamespace(messageBox=lambda text, title="": messages.append(text)))
    core.Application = types.SimpleNamespace(get=lambda: app)

    class Setup_:  # the class, for hasattr probes
        stockSolids = None
        allOperations = None

    cam.Setup = Setup_
    cam.CAM = types.SimpleNamespace(cast=lambda x: x)
    cam.CAMManager = types.SimpleNamespace(
        get=lambda: types.SimpleNamespace(libraryManager=types.SimpleNamespace(postLibrary=PostLibrary())))
    cam.LibraryLocations = types.SimpleNamespace(LocalLibraryLocation=0, CloudLibraryLocation=1)
    cam.LoopTypes = types.SimpleNamespace(OnlyOutsideLoops=0, OnlyInsideLoops=1, AllLoops=2)
    fusion.ArrangeSolverTypes = types.SimpleNamespace(Arrange2DTrueShapeSolverType=0)

    adsk.core, adsk.cam, adsk.fusion = core, cam, fusion
    sys.modules.update({"adsk": adsk, "adsk.core": core, "adsk.cam": cam, "adsk.fusion": fusion})
    return adsk, messages


def uninstall():
    for name in ("adsk", "adsk.core", "adsk.cam", "adsk.fusion"):
        sys.modules.pop(name, None)
