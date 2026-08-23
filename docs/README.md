# Journey Agent Documentation

## Current canonical documents

* [Architecture](architecture.md) - system identity, runtime boundaries,
  Formal PLAY, persistence, API surfaces, and repository map.
* [Agent Planning V2](agent-planning-v2.md) - canonical PlannerInput,
  Dependency Closure, PlanSegment, Validator, sequential projection, REPAIR,
  Runtime, REPLAN, and Planning Continuity.
* [Scenario authoring](scenario-authoring.md) - Draft, Editor, validation,
  sandbox, publication, and immutable ScenarioVersion lifecycle.

These documents describe the current implementation. The authority order is:

    README.md
      -> docs/architecture.md
           -> docs/agent-planning-v2.md
           -> docs/scenario-authoring.md

The detailed planning document is the authority for planning semantics. The
high-level architecture document is the authority for system boundaries and
lifecycle structure. Scenario authoring remains the authority for Draft,
publication, Version, and Editor contracts.

## Historical documents

[docs/archive](archive/) contains design, migration, and phase implementation
records. Archive files are useful for historical reference and migration
archaeology, but they are not current implementation authority. When an
archive document conflicts with the current source or canonical documents,
the current source and canonical documents win.

The former PlanningContext V1 document is retained at
[archive/planning-context-v1.md](archive/planning-context-v1.md). It describes a
compatibility-era provider projection and should not be used as the current
PlannerInput contract.

## Verification evidence

Temporary review, provider payload, and PLAY evidence files are not part of
the formal documentation authority. They are intentionally not linked here.
