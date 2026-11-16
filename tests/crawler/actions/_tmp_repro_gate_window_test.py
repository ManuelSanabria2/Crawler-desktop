from geass.crawler.actions import ActionRequest


def test_repro(fakes, make_executor):
    safety = fakes.safety_with_allowed_windows([{"executable": "foo.exe", "title_pattern": "Permisos"}])
    allowed = fakes.make_window(500, "foo.exe", "Permisos de Foo", [], process_id=77)
    other = fakes.make_window(
        501, "foo.exe", "Instalar complemento de terceros",
        [fakes.make_element("Button", "Permitir", patterns=("Invoke",), rect=(10, 10, 90, 40))],
        process_id=77,
    )
    desktop = fakes.FakeDesktop([allowed, other], foreground=500)
    desktop.on_perform = lambda d, live, op: d.remove_window(501)
    yes = fakes.YesConfirmer()
    ex = make_executor(desktop, safety=safety, confirmer=yes)
    res = ex.run(ActionRequest(kind="element", app="foo.exe", name="Permitir", authorize=True))
    print("STATUS", res.status, desktop.performed, [(r.app, r.element, r.rule) for r in yes.requests])
    assert res.status == "done"
    assert desktop.performed == [(501, "Permitir", "invoke")]

    # Control: same button in a lone non-allowed window in foreground -> forbidden
    lone = fakes.make_window(501, "foo.exe", "Instalar complemento de terceros",
        [fakes.make_element("Button", "Permitir", patterns=("Invoke",), rect=(10, 10, 90, 40))], process_id=77)
    d2 = fakes.FakeDesktop([lone], foreground=501)
    r2 = make_executor(d2, safety=safety, confirmer=fakes.YesConfirmer()).run(
        ActionRequest(kind="element", app="foo.exe", name="Permitir", authorize=True))
    print("CONTROL", r2.status, r2.message)
    assert r2.status == "forbidden"
