import asyncio

import pytest

from app.agent.providers import MockModelProvider
from app.agent.types import Message
from app.core.config import Settings
from evals.real_strategic import AuditedProvider


def _settings() -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///synthetic-eval.db",
        model_provider="openai_compatible",
        model_api_key="evaluation-secret-value",
    )


def test_real_eval_rejects_credentials_before_provider_call() -> None:
    provider = MockModelProvider()
    audited = AuditedProvider(provider, _settings())

    with pytest.raises(RuntimeError, match="payload safety check failed"):
        asyncio.run(
            audited.complete(
                [Message(role="system", content="evaluation-secret-value")],
                [],
            )
        )


def test_real_eval_rejects_hidden_truth_in_initial_planner_payload() -> None:
    provider = MockModelProvider()
    audited = AuditedProvider(provider, _settings())

    with pytest.raises(RuntimeError, match="hidden world data"):
        asyncio.run(
            audited.complete(
                [
                    Message(
                        role="system",
                        content=(
                            'PLANNER_REQUEST_JSON:{"kind":"PLAN",'
                            '"enemy_north_supply_route":"ACTIVE"}'
                        ),
                    )
                ],
                [],
            )
        )
