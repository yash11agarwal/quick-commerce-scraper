import pytest

from qcom.core.errors import InputValidationError
from qcom.io.excel_in import read_input
from qcom.io.template import write_template


def test_template_then_fill_reads_back(tmp_path):
    import openpyxl

    path = write_template(tmp_path / "input.xlsx")
    wb = openpyxl.load_workbook(path)
    wb["products"].append(["amul butter", "Amul", "500 g", "Dairy", None])
    wb["products"].append(["parked", None, None, None, "FALSE"])
    wb["pincodes"].append(["700048", "Kolkata", None, None])
    wb["pincodes"].append([110001, None, None, None])  # numeric cell is accepted and read as text
    wb["settings"]["B2"] = "fake"
    wb["settings"]["B3"] = 5
    wb["settings"]["B4"] = "label"
    wb.save(path)
    spec = read_input(path)
    assert [p.product_name for p in spec.products] == ["amul butter"]
    assert spec.products[0].input_row_id == 2 and spec.products[0].brand == "Amul"
    assert [z.pincode for z in spec.pincodes] == ["700048", "110001"] and spec.pincodes[1].input_row_id == 3
    assert spec.settings.platforms == ["fake"] and spec.settings.max_results_per_query == 5 and spec.settings.run_label == "label"
    assert len(spec.sha256) == 64


def test_sheets_found_by_position_with_default_names(make_workbook):
    path = make_workbook([("maggi",)], [("700048",)], product_headers=("Product",), pincode_headers=("PIN",), sheet_names=("Sheet1", "Sheet2", "x"))
    spec = read_input(path)
    assert spec.products[0].product_name == "maggi" and spec.pincodes[0].pincode == "700048"
    assert spec.settings.platforms == [] and spec.settings.max_results_per_query == 20


def _problems(path):
    with pytest.raises(InputValidationError) as exc:
        read_input(path)
    return [str(p) for p in exc.value.problems]


def test_every_problem_is_reported_with_sheet_and_cell(make_workbook):
    path = make_workbook(
        [("amul butter", None), ("", "Amul"), ("Amul  Butter", None), (None, None)],
        [("70004", None), (700048.5, None), ("700048", None), ("700048", None), ("70004A", None), (None, "Kolkata"), (None, None)],
        product_headers=("product_name", "brand"),
        pincode_headers=("pincode", "city"),
        settings=[("platforms", "fake"), ("bogus", "1"), ("max_results_per_query", "zero")],
    )
    problems = _problems(path)
    assert any("products!A3" in p and "blank" in p for p in problems)
    assert any("products!A4" in p and "duplicate" in p for p in problems)
    assert any("pincodes!A2" in p and "six digits" in p for p in problems)
    assert any("pincodes!A3" in p for p in problems)
    assert any("pincodes!A5" in p and "duplicate" in p for p in problems)
    assert any("pincodes!A6" in p for p in problems)
    assert any("pincodes!A7" in p and "blank" in p for p in problems)
    assert not any("A8" in p for p in problems)  # a fully blank row is skipped, not reported
    assert any("unknown setting 'bogus'" in p for p in problems)
    assert any("max_results_per_query" in p for p in problems)


def test_missing_headers(make_workbook):
    path = make_workbook([("x",)], [("700048",)], product_headers=("title",), pincode_headers=("zip",))
    problems = _problems(path)
    assert any("product_name" in p for p in problems) and any("'pincode'" in p for p in problems)


def test_no_active_rows(make_workbook):
    path = make_workbook([("x", "FALSE")], [("700048", "TRUE")], product_headers=("product_name", "active"), pincode_headers=("pincode", "active"))
    assert any("no active products" in p for p in _problems(path))


def test_bad_active_value(make_workbook):
    path = make_workbook([("x", "maybe")], [("700048",)], product_headers=("product_name", "active"), pincode_headers=("pincode",))
    assert any("active must be TRUE or FALSE" in p for p in _problems(path))


def test_missing_file(tmp_path):
    assert any("does not exist" in p for p in _problems(tmp_path / "nope.xlsx"))
