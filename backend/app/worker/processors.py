"""Job processors (DESIGN.md §10, §12).

A processor advances a job through stages via ``ctx.heartbeat`` (which persists
progress, renews the lease, and raises on cancellation) and publishes its result.

This slice ships only a stub that exercises the execution engine end to end. The
real extraction processor (grant col:line, native + OCR) and the restricted-egress
fetch processor arrive in later slices behind this same signature.
"""

from __future__ import annotations

from ..db.models import ExtractionJob, UserDocument
from .engine import JobContext


async def stub_processor(ctx: JobContext, job: ExtractionJob) -> None:
    """Placeholder processor: walk a few steps, then mark the document ready.

    Real work (PDF parsing, OCR, artifact publication) replaces the loop body and
    the document transition in Phase 2/3.
    """
    total = 4
    await ctx.heartbeat(
        stage="validating_source",
        stage_label="Preparing",
        total_units=total,
        unit="steps",
        completed_units=0,
        indeterminate=False,
    )
    for step in range(1, total + 1):
        await ctx.heartbeat(stage="processing", completed_units=step)

    if job.document_id is not None:
        async with ctx.sessionmaker() as session:
            doc = await session.get(UserDocument, job.document_id)
            if doc is not None and doc.state != "deleted":
                doc.state = "ready"
                await session.commit()
