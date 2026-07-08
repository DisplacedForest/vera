"""Stack-updates producer unit tests. Standalone — run: python3 tests/test_updates.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from routers import updates as u  # noqa: E402


def test_friendly_image():
    assert u._friendly_image("lscr.io/linuxserver/sonarr:latest") == "sonarr"
    assert u._friendly_image("ghcr.io/binhex/arch-qbittorrentvpn:latest") == "arch-qbittorrentvpn"
    assert u._friendly_image("openwebui") == "openwebui"
    assert u._friendly_image("plexinc/pms-docker:latest@sha256:abc") == "pms-docker"


def test_docker_pending_only_false():
    status = {
        "lscr.io/linuxserver/sonarr:latest": {"local": "a", "remote": "a", "status": "true"},
        "lscr.io/linuxserver/radarr:latest": {"local": "a", "remote": "b", "status": "false"},
        "some/weird:latest": {"status": "undef"},          # not checkable -> not pending
    }
    pending = u._docker_pending(status)
    assert [c["name"] for c in pending] == ["radarr"]
    assert pending[0]["group"] == "Containers"
    assert pending[0]["id"] == "docker:lscr.io/linuxserver/radarr:latest"
    assert u._docker_pending({}) == []
    assert u._docker_pending(None) == []


def test_classify_allowlist_and_unraid():
    # Unraid integration entities ride in under hacs but get their own group.
    assert u._classify("update.unraid_update", "hacs") == "Unraid OS"
    assert u._classify("update.unraid_management_agent_update", "hacs") == "Unraid OS"
    # Infra platforms map to groups.
    assert u._classify("update.udmpro", "unifi") == "Network"
    assert u._classify("update.home_assistant_core_update", "hassio") == "Home Assistant"
    assert u._classify("update.bubble_card_update", "hacs") == "HACS"
    assert u._classify("update.media_server_update", "plex") == "Apps"
    # Per-device IoT firmware is dropped.
    assert u._classify("update.bedroom_lamp_firmware_2", "shelly") is None
    assert u._classify("update.hearth_shades_left_firmware", "matter") is None
    assert u._classify("update.downstairs_plug_firmware", "zha") is None
    assert u._classify("update.office_firmware", "litterrobot") is None
    assert u._classify("update.whatever", None) is None


def test_ha_label():
    assert u._ha_label("update.home_assistant_core_update", {"title": "Home Assistant Core"}) == "Home Assistant Core"
    assert u._ha_label("update.udmpro", {"title": None}) == "udmpro"
    assert u._ha_label("update.bubble_card_update", {}) == "bubble card"
    assert u._ha_label("update.hearth_shades_left_firmware", {}) == "hearth shades left"


def test_ha_pending_filters_state_and_platform():
    states = [
        {"entity_id": "update.bubble_card_update", "state": "on",
         "attributes": {"installed_version": "v3.2.2", "latest_version": "v3.2.3"}},
        {"entity_id": "update.unraid_management_agent_update", "state": "on",
         "attributes": {"installed_version": "v2026.6.1", "latest_version": "v2026.6.3"}},
        {"entity_id": "update.udmpro", "state": "off",                       # current -> skip
         "attributes": {"installed_version": "x", "latest_version": "x"}},
        {"entity_id": "update.bedroom_lamp_firmware_2", "state": "on",       # IoT -> dropped
         "attributes": {"installed_version": "v1.7.5", "latest_version": "v1.7.6"}},
        {"entity_id": "sensor.not_an_update", "state": "on", "attributes": {}},
    ]
    platforms = {
        "update.bubble_card_update": "hacs",
        "update.unraid_management_agent_update": "hacs",
        "update.udmpro": "unifi",
        "update.bedroom_lamp_firmware_2": "shelly",
    }
    pending = u._ha_pending(states, platforms)
    by_id = {c["id"]: c for c in pending}
    assert set(by_id) == {"update.bubble_card_update", "update.unraid_management_agent_update"}
    assert by_id["update.bubble_card_update"]["group"] == "HACS"
    assert by_id["update.unraid_management_agent_update"]["group"] == "Unraid OS"
    assert by_id["update.bubble_card_update"]["latest"] == "v3.2.3"


def test_detail():
    assert u._detail({"group": "HACS", "cur": "v1", "latest": "v2"}) == "v1 → v2"
    assert u._detail({"group": "Containers", "cur": None, "latest": None}) == "new image available"
    assert u._detail({"group": "Network", "cur": None, "latest": None}) == ""


def test_component_action_routing():
    # Containers -> Unraid API docker.update, carrying name + raw image.
    va = u._component_action({"group": "Containers", "id": "docker:x/radarr:latest", "name": "radarr", "image": "x/radarr:latest"})
    assert va == ("docker.update", {"name": "radarr", "image": "x/radarr:latest"})
    # HA-domain entities install natively via HA update.install.
    va = u._component_action({"group": "HACS", "id": "update.bubble_card_update"})
    assert va == ("ha.service", {"domain": "update", "service": "install", "data": {"entity_id": "update.bubble_card_update"}})
    # The unraid management agent IS an HA entity -> installable.
    assert u._component_action({"group": "Unraid OS", "id": "update.unraid_management_agent_update"})[0] == "ha.service"
    # Unraid OS proper -> flag-only (apply = reboot).
    assert u._component_action({"group": "Unraid OS", "id": "update.unraid_update"}) is None


def test_summary_body_grouped_in_order():
    components = [
        {"group": "HACS", "name": "bubble card"},
        {"group": "Containers", "name": "radarr"},
        {"group": "Unraid OS", "name": "unraid management agent"},
    ]
    body = u._summary_body(components)
    # Sections appear in GROUP_ORDER, not input order.
    assert body.index("**Containers**") < body.index("**Unraid OS**") < body.index("**HACS**")
    assert "radarr" in body and "bubble card" in body


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
