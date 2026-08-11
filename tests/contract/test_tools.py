from app.tools.catalog import build_registry


def test_tool_schemas_forbid_unknown_fields() -> None:
    definition = next(
        item for item in build_registry().definitions() if item.name == "inspect_command_state"
    )
    assert definition.parameters["additionalProperties"] is False


def test_expected_tool_catalog_is_complete() -> None:
    names = {item.name for item in build_registry().definitions()}
    assert names == {
        "inspect_command_state",
        "create_task_plan",
        "replan_task",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
