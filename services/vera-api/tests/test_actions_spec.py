"""Action spec unit tests. Standalone — run: python3 tests/test_actions_spec.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "routers"))
import action_spec as sp  # noqa: E402


def test_ha_allowlist():
    assert sp.ha_allowed("climate", "set_temperature")
    assert sp.ha_allowed("light", "turn_on")
    assert sp.ha_allowed("switch", "toggle")
    assert not sp.ha_allowed("lock", "unlock")          # safety: no lock control
    assert not sp.ha_allowed("climate", "set_fan_mode")  # only the allowlisted climate service


def test_ha_validate():
    assert sp.SPEC["ha.service"]["validate"](
        {"domain": "climate", "service": "set_temperature", "data": {"entity_id": "climate.x", "temperature": 70}}
    ) is None
    assert sp.SPEC["ha.service"]["validate"]({"domain": "lock", "service": "unlock"})  # error string (truthy)
    assert sp.SPEC["ha.service"]["validate"]({})  # missing domain/service


def test_knowledge_validate():
    assert sp.SPEC["knowledge.set"]["validate"]({"type": "appliance", "name": "x"}) is None
    assert sp.SPEC["knowledge.set"]["validate"]({"type": "appliance"})  # missing name
    assert sp.SPEC["knowledge.delete"]["validate"]({"entity_id": "appliance:x"}) is None
    assert sp.SPEC["knowledge.delete"]["validate"]({})  # missing identifier


def test_grocy_validate():
    assert sp.SPEC["kitchen.grocy_adjust"]["validate"]({"product_id": 3, "op": "consume", "amount": 1}) is None
    assert sp.SPEC["kitchen.grocy_adjust"]["validate"]({"product_id": 3, "op": "nope", "amount": 1})  # bad op
    assert sp.SPEC["kitchen.grocy_adjust"]["validate"]({"op": "add", "amount": 1})  # missing product_id


def test_update_install_allowed():
    # Applying an HA update.* entity is the HA-domain apply path for stack updates.
    assert sp.ha_allowed("update", "install")
    assert sp.SPEC["ha.service"]["validate"](
        {"domain": "update", "service": "install", "data": {"entity_id": "update.bubble_card_update"}}) is None


def test_docker_update_spec():
    # Container apply via the host's container-management API; name or image identifies the target.
    assert sp.SPEC["docker.update"]["validate"]({"name": "radarr"}) is None
    assert sp.SPEC["docker.update"]["validate"]({"image": "x/radarr:latest"}) is None
    assert sp.SPEC["docker.update"]["validate"]({})  # needs name or image
    assert sp.SPEC["docker.update"]["risk"] == "high"
    assert sp.SPEC["docker.update"]["reversible"] is False
    assert "radarr" in sp.SPEC["docker.update"]["preview"]({"name": "radarr", "image": "x/radarr:latest"})


def test_preview_strings():
    assert "climate.set_temperature" in sp.SPEC["ha.service"]["preview"](
        {"domain": "climate", "service": "set_temperature", "data": {"entity_id": "climate.x"}})
    assert "Record" in sp.SPEC["knowledge.set"]["preview"]({"type": "appliance", "name": "Heater", "attrs": {"a": 1}})
    assert sp.SPEC["health.check"]["risk"] == "none"


if __name__ == "__main__":
    test_ha_allowlist()
    test_ha_validate()
    test_knowledge_validate()
    test_grocy_validate()
    test_update_install_allowed()
    test_docker_update_spec()
    test_preview_strings()
    print("OK")
