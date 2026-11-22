"""Tests the diagnostic without touching real API credentials."""
import importlib.util
from pathlib import Path


MOD_PATH = Path(__file__).resolve().parents[1] / "audit_marathi_output.py"
spec = importlib.util.spec_from_file_location("audit_marathi_output", MOD_PATH)
auditmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditmod)


def test_devanagari_digits_canonicalize():
    report = auditmod.audit("Time 9:00; 12 + 8", "वेळ ९:००; १२ + ८")
    assert report["checks"]["numbers_preserved_in_order"]


def test_mismatch_localizes_digit_and_line():
    report = auditmod.audit("A\nTime 9:00", "अ\nवेळ ९:३०")
    assert not report["checks"]["numbers_preserved_in_order"]
    assert report["numeric_mismatches"]
    assert report["numeric_mismatches"][0]["source"][0]["line"] == 2


def test_table_shape_and_values():
    src = "| Item | Quantity | Price |\n| --- | --- | --- |\n| Book | 2 | 30 |"
    good = "| वस्तू | प्रमाण | किंमत |\n| --- | --- | --- |\n| पुस्तक | २ | ३० |"
    broken = "| वस्तू | किंमत |\n| --- | --- |\n| पुस्तक | ३० |"
    assert auditmod.audit(src, good)["checks"]["table_numeric_cells_preserved"]
    assert not auditmod.audit(src, broken)["checks"]["table_shape_preserved"]


def test_math_preservation_exact():
    source = "Calculate $12 + 8 = \\underline{\\hspace{1.5cm}}$"
    good = "गणना करा $12 + 8 = \\underline{\\hspace{1.5cm}}$"
    bad = "गणना करा $12 + 9$"
    assert auditmod.audit(source, good)["checks"]["math_preserved_exactly"]
    assert not auditmod.audit(source, bad)["checks"]["math_preserved_exactly"]
