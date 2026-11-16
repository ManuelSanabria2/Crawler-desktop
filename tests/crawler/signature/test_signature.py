"""Tests de las firmas de estado con volcados guardados. No necesitan Windows."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from geass.core.config import load_crawler_config
from geass.core.errors import ConfigValidationError
from geass.core.paths import config_dir
from geass.crawler.signature import (
    SignatureRules,
    StateSignature,
    compare_dumps,
    compute_signature,
    default_rules,
    explain,
)
from geass.crawler.uia import WindowDump

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "uia"
RECENT_COLORS_LIST = "SystemSettings_Personalize_Color_RecentColors_EntityItemColorGrid"
ALL_FIXTURES = [
    "calculator_a",
    "calculator_b",
    "notepad_short",
    "notepad_long",
    "settings_mouse",
    "settings_colors",
]


@pytest.fixture(scope="module")
def rules() -> SignatureRules:
    return default_rules()


def edited(dump: WindowDump, edit: Callable[[dict], None]) -> WindowDump:
    """Copia de un volcado con una modificación aplicada sobre su forma JSON."""
    data = json.loads(dump.model_dump_json())
    edit(data)
    return WindowDump.model_validate(data)


def find_node(node: dict, **criteria: str) -> dict:
    stack = [node]
    while stack:
        current = stack.pop()
        if all(current[key] == value for key, value in criteria.items()):
            return current
        stack.extend(current["children"])
    raise AssertionError(f"no hay nodo con {criteria}")


def rules_with(**changes: Any) -> SignatureRules:
    settings = load_crawler_config().signature
    return SignatureRules.from_config(settings.model_copy(update=changes))


# Los tres casos del paso


def test_same_view_dumped_twice_has_the_same_signature(uia_fixture, rules) -> None:  # noqa: ANN001
    first = compute_signature(uia_fixture("calculator_a"), rules)
    second = compute_signature(uia_fixture("calculator_b"), rules)
    assert first.structural == second.structural
    assert first.detailed == second.detailed


def test_notepad_with_different_text_has_the_same_signature(uia_fixture, rules) -> None:  # noqa: ANN001
    short, long = uia_fixture("notepad_short"), uia_fixture("notepad_long")
    # Los volcados sí son distintos: otro archivo, otro tamaño, barras de desplazamiento.
    assert short.title != long.title
    assert short.node_count != long.node_count
    assert long.root.find_all(control_type="ScrollBar")
    assert not short.root.find_all(control_type="ScrollBar")

    first, second = compute_signature(short, rules), compute_signature(long, rules)
    assert first.structural == second.structural
    assert first.detailed == second.detailed
    assert first.stable_title == second.stable_title == "bloc de notas"


def test_two_settings_pages_have_different_signatures(uia_fixture, rules) -> None:  # noqa: ANN001
    mouse, colors = uia_fixture("settings_mouse"), uia_fixture("settings_colors")
    # Misma app, misma clase y mismo título: solo el contenido distingue las páginas.
    assert mouse.process_name == colors.process_name
    assert mouse.class_name == colors.class_name
    assert mouse.title == colors.title

    first, second = compute_signature(mouse, rules), compute_signature(colors, rules)
    assert first.structural != second.structural
    assert first.detailed != second.detailed


def test_every_fixture_view_is_distinct(uia_fixture, rules) -> None:  # noqa: ANN001
    views = ["calculator_a", "notepad_short", "settings_mouse", "settings_colors"]
    structural = {compute_signature(uia_fixture(name), rules).structural for name in views}
    assert len(structural) == len(views)


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_signature_is_deterministic(uia_fixture, rules, name: str) -> None:  # noqa: ANN001
    dump = uia_fixture(name)
    signature = compute_signature(dump, rules)
    assert isinstance(signature, StateSignature)
    assert signature == compute_signature(copy.deepcopy(dump), rules)
    assert len(signature.structural) == len(signature.detailed) == 64
    assert signature.structural != signature.detailed
    assert signature.process_name == dump.process_name
    assert signature.window_class == dump.class_name
    assert 0 < signature.token_count <= dump.node_count
    assert signature.partial is False


def test_default_rules_are_used_when_none_are_given(uia_fixture, rules) -> None:  # noqa: ANN001
    dump = uia_fixture("calculator_a")
    assert compute_signature(dump) == compute_signature(dump, rules)


# Qué cambia cada firma


def test_volatile_metadata_does_not_matter(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")

    def edit(data: dict) -> None:
        data["handle"] = 999
        data["process_id"] = 1
        data["elapsed_s"] = 9.9
        data["captured_at"] = "2030-01-01T00:00:00Z"
        for child in data["root"]["children"]:
            child["rect"] = {"left": 1, "top": 2, "right": 30, "bottom": 40}
            child["offscreen"] = not child["offscreen"]
            child["path"] = "otra/ruta"

    assert compute_signature(edited(base, edit), rules) == compute_signature(base, rules)


def test_sibling_order_does_not_matter(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("settings_mouse")

    def edit(data: dict) -> None:
        stack = [data["root"]]
        while stack:
            node = stack.pop()
            node["children"].reverse()
            stack.extend(node["children"])

    changed = compute_signature(edited(base, edit), rules)
    assert changed.structural == compute_signature(base, rules).structural
    assert changed.detailed == compute_signature(base, rules).detailed


def test_disabled_button_changes_only_the_detailed_signature(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")

    def edit(data: dict) -> None:
        find_node(data["root"], automation_id="equalButton")["enabled"] = False

    before, after = compute_signature(base, rules), compute_signature(edited(base, edit), rules)
    assert after.structural == before.structural
    assert after.detailed != before.detailed


def test_renaming_an_element_with_automation_id_changes_only_the_detailed(
    uia_fixture,  # noqa: ANN001
    rules: SignatureRules,
) -> None:
    base = uia_fixture("calculator_a")

    def edit(data: dict) -> None:
        find_node(data["root"], automation_id="plusButton")["name"] = "Sumar"

    before, after = compute_signature(base, rules), compute_signature(edited(base, edit), rules)
    assert after.structural == before.structural
    assert after.detailed != before.detailed


def test_renaming_an_element_without_automation_id_changes_both(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("notepad_short")

    def edit(data: dict) -> None:
        find_node(data["root"], name="Tabla", control_type="Button")["name"] = "Insertar imagen"

    before, after = compute_signature(base, rules), compute_signature(edited(base, edit), rules)
    assert after.structural != before.structural
    assert after.detailed != before.detailed


def test_numbers_in_names_are_ignored(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("notepad_short")

    def edit(data: dict) -> None:
        find_node(data["root"], name="5 caracteres.")["name"] = "98.765 caracteres."
        find_node(data["root"], name="Tabla", control_type="Button")["name"] = "Tabla"

    assert compute_signature(edited(base, edit), rules) == compute_signature(base, rules)


def test_new_element_changes_the_structural_signature(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")

    def edit(data: dict) -> None:
        button = copy.deepcopy(find_node(data["root"], automation_id="equalButton"))
        button["automation_id"] = "newFeatureButton"
        data["root"]["children"].append(button)

    assert (
        compute_signature(edited(base, edit), rules).structural
        != compute_signature(base, rules).structural
    )


def test_process_and_window_class_are_part_of_the_signature(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")
    reference = compute_signature(base, rules).structural
    other_process = edited(base, lambda data: data.update(process_name="Otra.exe"))
    other_class = edited(base, lambda data: data.update(class_name="OtraClase"))
    same_process = edited(base, lambda data: data.update(process_name="CALCULATORAPP.EXE"))
    assert compute_signature(other_process, rules).structural != reference
    assert compute_signature(other_class, rules).structural != reference
    assert compute_signature(same_process, rules).structural == reference


def test_file_name_in_title_is_ignored_but_app_name_is_not(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("notepad_short")
    reference = compute_signature(base, rules)

    def set_title(title: str) -> Callable[[dict], None]:
        def edit(data: dict) -> None:
            data["title"] = title
            data["root"]["name"] = title

        return edit

    other_file = compute_signature(edited(base, set_title("otro archivo.md: Bloc de notas")), rules)
    other_app = compute_signature(edited(base, set_title("nota.txt: Otro editor")), rules)
    assert other_file == reference
    assert other_app.structural != reference.structural
    assert other_app.stable_title == "otro editor"


def test_list_contents_matter_only_through_the_count_range(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("settings_colors")
    list_id = RECENT_COLORS_LIST
    assert len(base.root.find_all(automation_id=list_id)[0].children) == 5

    def resize(count: int) -> Callable[[dict], None]:
        def edit(data: dict) -> None:
            node = find_node(data["root"], automation_id=list_id)
            item = node["children"][0]
            node["children"] = [dict(copy.deepcopy(item), name=f"Color {i}") for i in range(count)]

        return edit

    reference = compute_signature(base, rules)
    assert compute_signature(edited(base, resize(19)), rules) == reference  # sigue en 5 a 19
    assert compute_signature(edited(base, resize(7)), rules) == reference
    assert compute_signature(edited(base, resize(4)), rules).structural != reference.structural
    assert compute_signature(edited(base, resize(20)), rules).structural != reference.structural
    assert compute_signature(edited(base, resize(0)), rules).structural != reference.structural


def test_partial_dump_is_flagged(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")
    partial = edited(base, lambda d: d.update(partial=True, partial_reasons=["timeout"]))
    assert compute_signature(partial, rules).partial is True


# Las reglas vienen de crawler.yaml


def test_rules_are_loaded_from_crawler_yaml() -> None:
    settings = load_crawler_config().signature
    rules = default_rules()
    assert rules.ignored_control_types == frozenset(settings.ignored_control_types)
    assert rules.volatile_container_types == frozenset(settings.volatile_container_types)
    assert rules.prune_empty_control_types == frozenset(settings.prune_empty_control_types)
    assert rules.count_buckets == tuple(settings.count_buckets)
    assert len(rules.variable_text_patterns) == len(settings.variable_text_patterns)


def test_signature_code_has_no_hardcoded_rules() -> None:
    package = Path(__file__).resolve().parents[3] / "src/geass/crawler/signature"
    code = "".join(path.read_text(encoding="utf-8") for path in package.glob("*.py"))
    for literal in ('"ScrollBar"', '"List"', '"Document"', '"Pane"', "<n>", r"\d"):
        assert literal not in code, literal


def test_without_ignored_types_notepad_signatures_differ(uia_fixture) -> None:  # noqa: ANN001
    rules = rules_with(ignored_control_types=[])
    short = compute_signature(uia_fixture("notepad_short"), rules)
    long = compute_signature(uia_fixture("notepad_long"), rules)
    assert short.structural != long.structural


def test_without_pruning_notepad_signatures_differ(uia_fixture) -> None:  # noqa: ANN001
    rules = rules_with(prune_empty_control_types=[])
    short = compute_signature(uia_fixture("notepad_short"), rules)
    long = compute_signature(uia_fixture("notepad_long"), rules)
    assert short.structural != long.structural


def test_without_text_patterns_notepad_differs_only_in_detail(uia_fixture) -> None:  # noqa: ANN001
    rules = rules_with(variable_text_patterns=[])
    short = compute_signature(uia_fixture("notepad_short"), rules)
    long = compute_signature(uia_fixture("notepad_long"), rules)
    assert short.structural == long.structural
    assert short.detailed != long.detailed


def test_without_volatile_containers_list_contents_change_the_signature(uia_fixture) -> None:  # noqa: ANN001
    base = uia_fixture("settings_colors")

    def edit(data: dict) -> None:
        node = find_node(data["root"], automation_id=RECENT_COLORS_LIST)
        node["children"][0]["name"] = "Otro color"

    assert compute_signature(edited(base, edit), default_rules()) == compute_signature(base)
    strict = rules_with(volatile_container_types=[])
    assert (
        compute_signature(edited(base, edit), strict).detailed
        != compute_signature(base, strict).detailed
    )


def test_title_pattern_per_executable() -> None:
    rules = rules_with(title_patterns={"notepad.exe": r"^(?P<stable>[^:]+):"})
    assert rules.stable_title("nota.txt: Bloc de notas", "Notepad.exe") == "nota.txt"
    assert rules.stable_title("nota.txt: Bloc de notas", "otra.exe") == "bloc de notas"


@pytest.mark.parametrize(
    ("title", "stable"),
    [
        ("nota.txt: Bloc de notas", "bloc de notas"),
        ("Informe final - Word", "word"),
        ("a - b - Visual Studio Code", "visual studio code"),
        ("Configuración", "configuración"),
        ("Calculadora", "calculadora"),
        ("", ""),
    ],
)
def test_default_stable_title(rules: SignatureRules, title: str, stable: str) -> None:
    assert rules.stable_title(title, "cualquiera.exe") == stable


@pytest.mark.parametrize(
    ("name", "normalized"),
    [
        ("  Línea 12,\nColumna 3 ", "línea <n>, columna <n>"),
        ("4.543 caracteres.", "<n> caracteres."),
        ("Guardado el 2026-11-11 a las 10:35", "guardado el <fecha> a las <hora>"),
        ("11/11/2026 22:05:09", "<fecha> <hora>"),
        ("Zoom 100%", "zoom <n>%"),
        ("Negrita (Ctrl+N)", "negrita (ctrl+n)"),
        ("", ""),
    ],
)
def test_normalize_name(rules: SignatureRules, name: str, normalized: str) -> None:
    assert rules.normalize_name(name) == normalized


def test_count_buckets(rules: SignatureRules) -> None:
    counts = (0, 1, 4, 5, 19, 20, 99, 100, 5000)
    assert [rules.count_bucket(n) for n in counts] == [
        "0",
        "1-4",
        "1-4",
        "5-19",
        "5-19",
        "20-99",
        "20-99",
        "100+",
        "100+",
    ]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("count_buckets", [1, 5], "empezar en 0"),
        ("count_buckets", [0, 5, 5], "creciente"),
        ("default_title_pattern", "^(.+)$", "stable"),
        ("default_title_pattern", "(?P<stable>[a-", "expresión regular inválida"),
        ("title_patterns", {"x.exe": "sin grupo"}, "stable"),
        ("variable_text_patterns", [{"name": "x", "pattern": "(", "replacement": ""}], "inválida"),
    ],
)
def test_invalid_rules_are_rejected(tmp_path: Path, key: str, value: Any, message: str) -> None:
    import yaml

    data = yaml.safe_load((config_dir() / "crawler.yaml").read_text(encoding="utf-8"))
    data["signature"][key] = value
    (tmp_path / "crawler.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ConfigValidationError, match=message):
        load_crawler_config(config_dir=tmp_path)


# Comparación y CLI


def test_compare_identical_views(uia_fixture, rules) -> None:  # noqa: ANN001
    comparison = compare_dumps(uia_fixture("calculator_a"), uia_fixture("calculator_b"), rules)
    assert comparison.structural_equal and comparison.detailed_equal
    assert comparison.structural_only_a == comparison.structural_only_b == []
    assert comparison.ignored_differences == {}
    text = "\n".join(explain(comparison))
    assert "Firma estructural: IGUAL" in text
    assert "Firma detallada: IGUAL" in text
    assert "No hay diferencias ignoradas" in text


def test_compare_explains_what_was_ignored(uia_fixture, rules) -> None:  # noqa: ANN001
    comparison = compare_dumps(uia_fixture("notepad_short"), uia_fixture("notepad_long"), rules)
    assert comparison.structural_equal and comparison.detailed_equal
    assert set(comparison.ignored_differences) == {
        "ignored_type",
        "pruned_empty",
        "variable_text",
        "volatile_content",
    }
    text = "\n".join(explain(comparison))
    assert "Diferencias que ninguna firma tiene en cuenta" in text
    for key in (
        "ignored_control_types",
        "prune_empty_control_types",
        "variable_text_patterns",
        "volatile_container_types",
    ):
        assert key in text


def test_compare_lists_structural_differences(uia_fixture, rules) -> None:  # noqa: ANN001
    comparison = compare_dumps(uia_fixture("settings_mouse"), uia_fixture("settings_colors"), rules)
    assert not comparison.structural_equal and not comparison.detailed_equal
    only_a = " ".join(description for description, _ in comparison.structural_only_a)
    only_b = " ".join(description for description, _ in comparison.structural_only_b)
    assert "SystemSettings_Input_Mouse_SetCursorSpeed_Slider" in only_a
    assert "SystemSettings_Personalize_Color_EnableTransparency_ToggleSwitch" in only_b
    assert "Mouse" not in only_b

    text = "\n".join(explain(comparison, max_lines=3))
    assert "Firma estructural: DISTINTA" in text
    assert "solo en A" in text and "solo en B" in text
    assert "más" in text


def test_compare_detailed_only_difference(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")

    def edit(data: dict) -> None:
        find_node(data["root"], automation_id="equalButton")["enabled"] = False

    comparison = compare_dumps(base, edited(base, edit), rules)
    assert comparison.structural_equal and not comparison.detailed_equal
    assert len(comparison.detailed_only_a) == len(comparison.detailed_only_b) == 1
    assert "equalButton" in comparison.detailed_only_a[0][0]
    assert "habilitado" in comparison.detailed_only_a[0][0]
    assert "deshabilitado" in comparison.detailed_only_b[0][0]
    text = "\n".join(explain(comparison))
    assert "Firma estructural: IGUAL" in text
    assert "Misma vista, distinta variante" in text


def test_compare_reports_process_difference(uia_fixture, rules) -> None:  # noqa: ANN001
    base = uia_fixture("calculator_a")
    other = edited(base, lambda data: data.update(process_name="Otra.exe"))
    comparison = compare_dumps(base, other, rules)
    assert not comparison.structural_equal
    assert comparison.structural_only_a == []
    assert "proceso: CalculatorApp.exe frente a Otra.exe" in "\n".join(explain(comparison))


def test_cli_compare() -> None:
    from geass.crawler.__main__ import app

    runner = CliRunner()
    same = runner.invoke(
        app,
        [
            "compare",
            str(FIXTURES_DIR / "notepad_short.json"),
            str(FIXTURES_DIR / "notepad_long.json"),
        ],
    )
    assert same.exit_code == 0, same.output
    assert "Firma estructural: IGUAL" in same.output
    assert "ninguna firma tiene en cuenta" in same.output

    different = runner.invoke(
        app,
        [
            "compare",
            str(FIXTURES_DIR / "settings_mouse.json"),
            str(FIXTURES_DIR / "settings_colors.json"),
            "--max-lines",
            "2",
        ],
    )
    assert different.exit_code == 0
    assert "Firma estructural: DISTINTA" in different.output


def test_cli_compare_rejects_bad_files(tmp_path: Path) -> None:
    from geass.crawler.__main__ import app

    bad = tmp_path / "malo.json"
    bad.write_text('{"no": "es un volcado"}', encoding="utf-8")
    good = str(FIXTURES_DIR / "calculator_a.json")
    assert CliRunner().invoke(app, ["compare", good, str(bad)]).exit_code == 1
    assert CliRunner().invoke(app, ["compare", good, str(tmp_path / "falta.json")]).exit_code == 1


# Los fixtures no llevan datos personales


@pytest.mark.parametrize("name", ALL_FIXTURES)
def test_fixtures_are_complete_and_scrubbed(uia_fixture, name: str) -> None:  # noqa: ANN001
    import os
    import re

    dump = uia_fixture(name)
    assert dump.partial is False
    text = (FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    user = os.environ.get("USERNAME", "")
    if len(user) >= 3:
        assert user.lower() not in text.lower()
