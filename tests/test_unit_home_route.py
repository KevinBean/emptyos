"""`emptyos.web.server.home_target` — where ``GET /`` lands.

Portal became the landing surface on 2026-10-03 (its home board renders the
hub's lanes + panels), with hub as the fallback for a build that does not ship
portal, and two overrides above both: the Settings store ``os.home`` (the Home
page field on /settings) then TOML ``[os] home``. Pure function of a kernel
shape, so it runs with no daemon.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from emptyos.web.server import HOME_FALLBACK, HOME_PORTAL, home_target


def _boom(*_a, **_k):
    raise RuntimeError("store unavailable")


def _kernel(*, toml_home=None, setting_home=None, settings=True, portal=True,
            operator=True, settings_raises=False, apps_raise=False):
    cfg = {"os.home": toml_home} if toml_home is not None else {}
    store = {"os.home": setting_home} if setting_home is not None else {}
    get = _boom if settings_raises else (lambda key, default=None: store.get(key, default))
    svc = SimpleNamespace(get=get) if settings else None
    instances = {"portal": object()} if portal else {}
    apps = SimpleNamespace(instances=SimpleNamespace(get=_boom)) if apps_raise \
        else SimpleNamespace(instances=instances)
    return SimpleNamespace(
        config=SimpleNamespace(get=lambda key, default=None: cfg.get(key, default),
                               web_is_operator=operator),
        services=SimpleNamespace(get_optional=lambda name: svc if name == "settings" else None),
        apps=apps,
    )


def test_portal_is_the_default_landing_when_loaded():
    assert home_target(_kernel()) == HOME_PORTAL == "/portal/"


def test_hub_is_the_fallback_when_portal_is_not_loaded():
    assert home_target(_kernel(portal=False)) == HOME_FALLBACK == "/hub/"


def test_toml_home_overrides_the_default():
    assert home_target(_kernel(toml_home="/welcome/")) == "/welcome/"


def test_settings_store_wins_over_toml():
    assert home_target(_kernel(toml_home="/welcome/", setting_home="/workspaces/#eng")) == "/workspaces/#eng"


def test_a_missing_leading_slash_is_added():
    assert home_target(_kernel(setting_home="hub/")) == "/hub/"


@pytest.mark.parametrize("blank", ["", "   ", "/", None])
def test_blank_or_root_values_fall_through_instead_of_looping(blank):
    # A cleared Settings field writes "" and /settings/api/reset writes null;
    # "/" would redirect to itself forever. All fall through to the default.
    assert home_target(_kernel(setting_home=blank)) == HOME_PORTAL
    assert home_target(_kernel(setting_home=blank, toml_home="/welcome/")) == "/welcome/"


def test_no_settings_service_still_resolves():
    assert home_target(_kernel(settings=False, toml_home="/x/")) == "/x/"
    assert home_target(_kernel(settings=False)) == HOME_PORTAL


@pytest.mark.parametrize("hostile", [
    "//evil.example/x",          # protocol-relative: starts with "/"
    "/\\evil.example",           # backslash form several browsers read as "//"
    "/portal/\r\nSet-Cookie: x=1",  # raw CR/LF would split the Location header
])
def test_an_off_site_or_header_splitting_home_falls_through(hostile):
    # os.home is writable from /settings, so GET / must never become an open
    # redirect or a header injection — a refused value behaves like unset.
    assert home_target(_kernel(setting_home=hostile)) == HOME_PORTAL
    assert home_target(_kernel(setting_home=hostile, toml_home="/welcome/")) == "/welcome/"


def test_a_scheme_becomes_a_same_site_path_never_a_redirect_off_site():
    # "https://x" does not start with "/", so it is prefixed — a path here.
    assert home_target(_kernel(setting_home="https://evil.example")) == "/https://evil.example"


@pytest.mark.parametrize("loops", ["/?x", "?x", "/#x", "#x", "/./", "/.", "/%2e/", "/a/../"])
def test_any_value_that_resolves_to_root_falls_through(loops):
    # Each of these passes safe_next yet makes the browser request "/" again —
    # GET / → 302 → GET / … A literal `== "/"` guard misses all of them.
    assert home_target(_kernel(setting_home=loops)) == HOME_PORTAL


def test_a_real_path_with_a_query_is_kept():
    assert home_target(_kernel(setting_home="/workspaces/?space=eng")) == "/workspaces/?space=eng"


def test_user_posture_ignores_the_stored_setting_but_keeps_the_operators_toml():
    # Shared demo / hosted learner: a visitor-written os.home must not decide
    # where "/" lands for everyone; the operator's TOML still does.
    k = _kernel(setting_home="/task/", toml_home="/welcome/", operator=False)
    assert home_target(k) == "/welcome/"
    assert home_target(_kernel(setting_home="/task/", operator=False)) == HOME_PORTAL


def test_a_failing_settings_store_falls_back_to_toml():
    assert home_target(_kernel(settings_raises=True, toml_home="/welcome/")) == "/welcome/"


def test_an_unreadable_app_registry_falls_back_to_hub():
    assert home_target(_kernel(apps_raise=True)) == HOME_FALLBACK
