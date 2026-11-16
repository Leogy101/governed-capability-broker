"""Original fixed fixtures. Code is inspected as text and is never executed."""
from dataclasses import dataclass
from importlib.resources import files
import json

from .validation import canonical, digest

_SUITE = json.loads(files("gcb").joinpath("fixtures/profile-cases-v2.json").read_text(encoding="utf-8"))
SUITE_VERSION = _SUITE["suite_version"]
SCORER_VERSION = _SUITE["scorer_version"]
SUITE_HASH = digest(_SUITE)


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    category: str
    input_json: str
    expected_json: str
    requires: tuple[str, ...]
    contrast: bool

    @property
    def hash(self):
        return digest({"id":self.id,"category":self.category,"input":self.input_json,"expected":self.expected_json,
                       "requires":self.requires,"scorer":SCORER_VERSION})


CASES = tuple(BenchmarkCase(row["id"],row["category"],canonical(row["input"]),canonical(row["expected"]),
                            tuple(row["requires"]),row["contrast"]) for row in _SUITE["cases"])
CASE_BY_ID = {case.id:case for case in CASES}


def score_text(case: BenchmarkCase, output: str) -> bool:
    if len(output.encode("utf-8")) > 32768:
        return False
    try:
        return canonical(json.loads(output)) == case.expected_json
    except (ValueError, TypeError, RecursionError):
        return False


def fixture_tool(name: str, arguments_json: str) -> dict:
    """Only the original fixture lookup, never a host tool/shell or arbitrary code."""
    if name != "fixture_lookup":
        return {"error":"unknown_fixture_tool"}
    if arguments_json != canonical({"key":"alpha"}):
        return {"error":"invalid_fixture_key","required_key":"alpha"}
    return {"value":42}
