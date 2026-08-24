"""Global tools for the services-delivery pod — the day-0 tool surface.

These are the tools agents call, in the turn, to PULL what they need:
  - research_agent pulls prior art (search_prior_art / list_resolvable_sources)
  - the parallel status_sweep_orchestrator copies claim engagements from the
    shared backlog (claim_next_active_engagement)

Each reads the dev store (seed corpus / seed backlog) so the pod runs with NO
connector wired, and **each announces itself**: when there is no real source
and no seed, it returns `available: false` and says why — never a synthetic
success that looks like a real read (see building-agents-thoroughly A4/A5).

To go live, wire the KB/RAG `mcp:` block in config.yaml (research) and swap the
store's `claim_next_active_engagement` body for your PSA query + atomic claim.
Registered by `import agency.tools` in main.py BEFORE `from_yaml()`/`start()`.
"""
import os

from leafmesh import global_tool, LeafMeshLogger

try:
    from ._shared import store
except ImportError:  # pragma: no cover — direct-run fallback
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _shared import store  # type: ignore

logger = LeafMeshLogger(__name__)


def _kb_wired() -> bool:
    """True if a real KB/RAG connector is configured (KB mcp URL set)."""
    return bool(os.getenv("KB_MCP_URL"))


@global_tool(category="research")
def search_prior_art(engagement_id: str = "", practice_area: str = "") -> dict:
    """Search prior art (past projects, industry reports, methodology) relevant
    to an engagement's practice area, for grounding research findings.

    Returns `available: true` with a `sources` list and the resolvable
    source-id set, or `available: false` with a reason when there is genuinely
    nothing to return (never an empty-but-confident hit).

    Args:
        engagement_id: The engagement whose practice area to search for.
        practice_area: Optional explicit practice area (overrides the brief's).
    """
    result = store.search_prior_art(str(engagement_id).strip(), str(practice_area).strip())
    if not result.get("available") and _kb_wired():
        return {"available": False,
                "reason": "KB connector wired but returned nothing for this query"}
    logger.info(f"search_prior_art eng={engagement_id} available={result.get('available')}")
    return result


@global_tool(category="research")
def list_resolvable_sources() -> dict:
    """List every retrievable source_id — the citation-or-nothing resolvable
    set a finding's cite must be drawn from."""
    return store.list_resolvable_sources()


@global_tool(category="delivery_ops")
def claim_next_active_engagement(worker_id: str = "") -> dict:
    """Atomically claim ONE active engagement from the shared weekly-sweep
    backlog. Parallel sweep copies each call this so they split the backlog
    instead of duplicating work.

    Returns `available: true` with the claimed engagement, or
    `available: false` when the backlog is exhausted (never a synthetic row).

    Args:
        worker_id: The claiming copy's id (for the claim record); optional.
    """
    result = store.claim_next_active_engagement(str(worker_id).strip())
    logger.info(f"claim_next_active_engagement worker={worker_id or '-'} "
                f"available={result.get('available')} "
                f"eng={result.get('engagement_id', '-')}")
    return result
