from app.api.schemas.phase_d import PublicFactResponse
from app.domain.presentation import PresentationActorField, PresentationKnowledgeLevel
from app.domain.scenario_v2 import FactDefinitionV2
from app.services.presentation_resolver import resolve_presentation_profile


def test_public_fact_contract_exposes_only_authored_safe_presentation_metadata() -> None:
    public = PublicFactResponse.model_validate(
        {
            "node_key": "clinic",
            "fact_key": "clinical_readiness",
            "name": "Clinical readiness",
            "value": "BLOCKED",
            "value_label": "Blocked",
            "summary_value_label": "Needs treatment",
            "detail_value_label": "Treatment not started",
            "presentation_role": "HEADER_PRIMARY",
        }
    )

    assert public.summary_value_label == "Needs treatment"
    assert public.detail_value_label == "Treatment not started"
    assert public.presentation_role == "HEADER_PRIMARY"
    assert "hidden_value" not in public.model_dump(mode="json")


def test_authored_context_labels_precede_default_without_changing_scalar_identity() -> None:
    fact = FactDefinitionV2.model_validate(
        {
            "key": "clinical_readiness",
            "name": "Clinical readiness",
            "value_type": "ENUM",
            "initial_value": "BLOCKED",
            "initial_visibility": "KNOWN",
            "allowed_values": ["READY", "BLOCKED"],
            "presentation_role": "HEADER_PRIMARY",
            "value_labels": [
                {"value": "READY", "label": "Ready"},
                {
                    "value": "BLOCKED",
                    "label": "Blocked",
                    "summary_label": "Needs treatment",
                    "detail_label": "Treatment not started",
                },
            ],
        }
    )
    resolved = resolve_presentation_profile(None)

    assert fact.initial_value == "BLOCKED"
    assert resolved.fact_value_label(fact, "BLOCKED") == "Blocked"
    assert resolved.fact_summary_value_label(fact, "BLOCKED") == "Needs treatment"
    assert resolved.fact_detail_value_label(fact, "BLOCKED") == "Treatment not started"


def test_template_deltas_share_the_standard_semantic_authority() -> None:
    compact = resolve_presentation_profile({"schema_version": 1, "template": "compact"})
    standard = resolve_presentation_profile({"schema_version": 1, "template": "standard"})
    detailed = resolve_presentation_profile({"schema_version": 1, "template": "detailed"})

    assert compact.knowledge_level == PresentationKnowledgeLevel.A
    assert standard.knowledge_level == PresentationKnowledgeLevel.A_PLUS_B_PLUS_C
    assert detailed.knowledge_level == standard.knowledge_level
    assert set(compact.actor_fields) < set(standard.actor_fields)
    assert set(detailed.actor_fields) > set(standard.actor_fields)
    assert PresentationActorField.STATUS not in standard.actor_fields
    assert PresentationActorField.TASK not in standard.actor_fields
