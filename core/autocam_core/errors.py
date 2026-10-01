"""Coded problems. The service turns codes into Trello comments and card moves."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class Issue:
    code: str
    msg: str

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "msg": self.msg}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Issue":
        return cls(code=str(data["code"]), msg=str(data["msg"]))


# Part problems (send the part card to Needs fixing)
STEP_MISSING = "STEP_MISSING"
STEP_IMPORT = "STEP_IMPORT"
BODY_COUNT = "BODY_COUNT"
NOT_A_PLATE = "NOT_A_PLATE"
THICKNESS_NOT_STOCK = "THICKNESS_NOT_STOCK"
CHAMFER = "CHAMFER"
EDGE_FILLET = "EDGE_FILLET"
UNSUPPORTED_FACE = "UNSUPPORTED_FACE"
TWO_SIDED = "TWO_SIDED"
FEATURE_TOO_SMALL = "FEATURE_TOO_SMALL"
NEEDS_MANUAL_CAM = "NEEDS_MANUAL_CAM"
INSIDE_RADIUS_TOO_SMALL = "INSIDE_RADIUS_TOO_SMALL"
SHORT_QTY = "SHORT_QTY"

# Part warnings (cut, but a reviewer should look)
HOLE_CONTOURED = "HOLE_CONTOURED"
SHARP_INSIDE_CORNERS = "SHARP_INSIDE_CORNERS"
SMALL_INSIDE_RADIUS = "SMALL_INSIDE_RADIUS"

# Sheet problems (the sheet's program is not offered for cutting)
TAP_BELOW_FLOOR = "TAP_BELOW_FLOOR"
TAP_REJECTED = "TAP_REJECTED"
PAUSES_WRONG = "PAUSES_WRONG"
TOOL_GUID_MISMATCH = "TOOL_GUID_MISMATCH"
OP_ERROR = "OP_ERROR"
POST_FAILED = "POST_FAILED"

# Job problems
CORE_VERSION_MISMATCH = "CORE_VERSION_MISMATCH"
NOTHING_TO_NEST = "NOTHING_TO_NEST"

# Decision 13: the exact comment for a poly part that needs the (not yet configured) poly 1/8 in tool.
POLY_EIGHTH_MISSING_MSG = "needs manual CAM: no poly feeds for the 1/8 in endmill"
