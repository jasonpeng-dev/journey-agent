from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.infrastructure.db.models import EvaluationResult, EvaluationRun
from app.infrastructure.db.session import get_db
from evals.runner import run_evaluations

router = APIRouter(prefix="/api/v1/evals", tags=["evaluations"])


@router.post("/runs", status_code=201)
def create_eval_run(db: Session = Depends(get_db)) -> dict[str, object]:
    report = run_evaluations()
    summary = report["summary"]
    results = report["results"]
    assert isinstance(summary, dict) and isinstance(results, list)
    run = EvaluationRun(status="COMPLETED", summary=summary)
    db.add(run)
    db.flush()
    for item in results:
        assert isinstance(item, dict)
        db.add(
            EvaluationResult(
                run_id=run.id,
                scenario_name=str(item["name"]),
                category=str(item["category"]),
                passed=bool(item["passed"]),
                expected_code=str(item["expected_code"]),
                actual_code=str(item["actual_code"]),
                latency_ms=round(float(item["latency_ms"])),
            )
        )
    db.commit()
    return {"id": run.id, "status": run.status, "summary": run.summary}


@router.get("/runs/{run_id}")
def get_eval_run(run_id: UUID, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(EvaluationRun, run_id)
    if not run:
        raise NotFoundError("evaluation_run", run_id)
    return {"id": run.id, "status": run.status, "summary": run.summary}


@router.get("/runs/{run_id}/results")
def get_eval_results(run_id: UUID, db: Session = Depends(get_db)) -> list[dict[str, object]]:
    values = db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all()
    return [
        {
            "scenario_name": item.scenario_name,
            "category": item.category,
            "passed": item.passed,
            "expected_code": item.expected_code,
            "actual_code": item.actual_code,
            "latency_ms": item.latency_ms,
        }
        for item in values
    ]
