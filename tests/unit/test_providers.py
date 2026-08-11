import json

from app.agent.providers import _json_arguments
from app.tools.handlers import PlanStepArgs


def test_planner_argument_adapter_decodes_nested_json_objects() -> None:
    arguments = {
        "steps": [
            {
                "constraints": "{}",
                "tool_arguments": '{"troop_count": 60}',
                "expected_outcome": '{"status": "PENDING"}',
                "resume_condition": "null",
            }
        ]
    }

    parsed = _json_arguments(json.dumps(arguments))

    assert parsed["steps"][0]["constraints"] == {}
    assert parsed["steps"][0]["tool_arguments"] == {"troop_count": 60}
    assert parsed["steps"][0]["expected_outcome"] == {"status": "PENDING"}
    assert parsed["steps"][0]["resume_condition"] is None


def test_planner_argument_adapter_does_not_guess_non_json_objects() -> None:
    arguments = {"steps": [{"tool_arguments": "troop_count=60"}]}

    parsed = _json_arguments(arguments)

    assert parsed["steps"][0]["tool_arguments"] == "troop_count=60"


def test_plan_step_schema_exposes_exact_execution_type_enum() -> None:
    schema = PlanStepArgs.model_json_schema()

    assert schema["properties"]["execution_type"]["enum"] == [
        "TOOL",
        "WAIT_FOR_USER",
        "WAIT_FOR_PLAYER_ACTION",
        "WAIT_FOR_WORLD_EVENT",
    ]
