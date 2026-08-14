from pathlib import Path


STATIC_ROOT = Path(__file__).resolve().parent / "static"


def test_public_javascript_avoids_string_replace_all() -> None:
    for name in ("app_helpers.js", "app.js", "diagnosis_workspace.js", "board_lite.html", "phone.html"):
        source = (STATIC_ROOT / name).read_text(encoding="utf-8")
        assert ".replaceAll(" not in source, f"{name} still requires String.prototype.replaceAll"


def test_midori_fallbacks_are_present() -> None:
    app_source = (STATIC_ROOT / "app.js").read_text(encoding="utf-8")
    diagnosis_source = (STATIC_ROOT / "diagnosis_workspace.js").read_text(encoding="utf-8")
    helper_source = (STATIC_ROOT / "app_helpers.js").read_text(encoding="utf-8")

    assert 'if ("PointerEvent" in window)' in app_source
    assert 'addEventListener("touchstart"' in app_source
    assert 'typeof AbortController === "function"' in diagnosis_source
    assert "helpers.consumeSseResponse" in diagnosis_source
    assert "typeof response.body.getReader" in helper_source
    assert "response.text" in helper_source


def test_midori_scripts_use_the_current_cache_version() -> None:
    html = (STATIC_ROOT / "index.html").read_text(encoding="utf-8")
    for name in ("app_helpers.js", "app.js", "diagnosis_workspace.js"):
        assert f'/static/{name}?v=20260807-midori7' in html
