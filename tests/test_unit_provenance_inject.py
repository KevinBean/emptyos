"""Pure tests for the opt-in auto-provenance page injection seam."""

from emptyos.web.server import _auto_provenance_enabled, _inject_theme_bootstrap


class _Settings:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Services:
    def __init__(self, settings=None):
        self.settings = settings

    def get_optional(self, name):
        return self.settings if name == "settings" else None


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _Kernel:
    def __init__(self, *, settings=None, config=None):
        self.services = _Services(settings)
        self.config = _Config(config)


def test_provenance_tag_is_not_deferred():
    """A deferred script runs only after parsing, so a page that fetches during
    parse (the normal load-my-data-on-open path) would issue its request against
    the unwrapped window.fetch and never receive a chip. Must block, like the
    theme bootstrap — unlike eos-i18n.js, which only touches the DOM."""
    html = _inject_theme_bootstrap("<html><head></head><body></body></html>",
                                   auto_provenance=True)
    # Cut at the opening tag's own '>' so a later <script defer> can't leak in.
    seg = [t for t in html.split("<script") if "eos-provenance.js" in t][0]
    tag = seg[: seg.index(">") + 1]
    assert "defer" not in tag, f"provenance runtime must not be deferred: <script{tag}"
    assert "async" not in tag


def test_reset_setting_null_does_not_shadow_config_or_env():
    """`POST /settings/api/reset` clears a key by writing an explicit null, not by
    deleting it (SettingsService.set(key, None)). Treating that null as `False`
    would let one reset permanently kill the [ui] TOML value and the
    EOS_UI_AUTO_PROVENANCE env override. Null means *unset*."""
    kernel = _Kernel(
        settings=_Settings({"ui.auto_provenance": None}),
        config={"ui.auto_provenance": True},
    )
    assert _auto_provenance_enabled(kernel) is True


def test_explicit_false_setting_still_overrides_config():
    """The inverse of the above: an intentional `false` must beat a TOML `true`."""
    kernel = _Kernel(
        settings=_Settings({"ui.auto_provenance": False}),
        config={"ui.auto_provenance": True},
    )
    assert _auto_provenance_enabled(kernel) is False


def test_dark_default_does_not_inject_provenance():
    out = _inject_theme_bootstrap("<html><head></head><body></body></html>")
    assert "eos-provenance.js" not in out


def test_flag_injects_once_immediately_after_head():
    html = "<html><head data-x='1'><title>x</title></head></html>"
    once = _inject_theme_bootstrap(html, auto_provenance=True)
    twice = _inject_theme_bootstrap(once, auto_provenance=True)
    assert once == twice
    assert once.count("eos-provenance.js") == 1
    assert once.index("eos-provenance.js") < once.index("<title>")


def test_provenance_injection_is_independent_of_theme_and_i18n_presence():
    html = (
        '<html><head><script>localStorage.getItem("eos-theme")</script>'
        '<script src="/static/eos-i18n.js"></script></head></html>'
    )
    out = _inject_theme_bootstrap(html, auto_provenance=True)
    assert out.count("eos-provenance.js") == 1
    assert out.count("eos-i18n.js") == 1


def test_setting_overrides_config_without_restart():
    kernel = _Kernel(
        settings=_Settings({"ui.auto_provenance": False}),
        config={"ui.auto_provenance": True},
    )
    assert _auto_provenance_enabled(kernel) is False
    kernel.services.settings.values["ui.auto_provenance"] = True
    assert _auto_provenance_enabled(kernel) is True


def test_config_is_fallback_when_setting_missing():
    kernel = _Kernel(settings=_Settings(), config={"ui.auto_provenance": True})
    assert _auto_provenance_enabled(kernel) is True


def test_string_truthy_coercion_for_setting_and_env_style_config():
    for raw in ("1", "true", "YES", " on "):
        assert _auto_provenance_enabled(
            _Kernel(settings=_Settings({"ui.auto_provenance": raw}))
        )
        assert _auto_provenance_enabled(
            _Kernel(config={"ui.auto_provenance": raw})
        )
    for raw in ("", "0", "false", "off"):
        assert not _auto_provenance_enabled(
            _Kernel(settings=_Settings({"ui.auto_provenance": raw}))
        )
