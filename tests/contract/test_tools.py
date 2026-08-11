from app.tools.catalog import build_registry


def test_tool_schemas_forbid_unknown_fields() -> None:
    definition = next(
        item for item in build_registry().definitions() if item.name == "update_relationship"
    )
    assert definition.parameters["additionalProperties"] is False


def test_expected_tool_catalog_is_complete() -> None:
    names = {item.name for item in build_registry().definitions()}
    assert names == {
        "get_player_state",
        "get_inventory",
        "get_world_state",
        "get_available_nodes",
        "get_active_quests",
        "get_encounter_state",
        "update_relationship",
        "create_quest",
        "propose_game_action",
        "inspect_task_requirements",
        "inspect_command_state",
        "create_task_plan",
        "replan_task",
        "prepare_starfire_route",
        "request_npc_assistance",
        "restore_outpost",
        "grant_access",
        "start_recon_operation",
        "start_military_operation",
        "negotiate_village_support",
        "start_outpost_repair",
        "start_trade_route_test",
    }
