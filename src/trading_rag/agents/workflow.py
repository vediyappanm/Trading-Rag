from typing import TypedDict
from langgraph.graph import StateGraph, END
from datetime import datetime, timedelta

from trading_rag.models import (
    RouterOutput,
    RetrievedEvidence,
    AnalysisOutput,
    BaselineStats,
    FinalResponse,
    QueryPath,
    TimeWindow,
    QueryType,
)
from trading_rag.router import route_query, route_query_llm
from trading_rag.retrieval import retrieve_evidence, correlate_execution_and_feed, semantic_search_incidents
from trading_rag.baselines import get_or_compute_baseline, get_default_baseline
from trading_rag.analysis import generate_analysis
import asyncio
from trading_rag.cache import (
    get_cached_answer,
    set_cached_answer,
    build_cache_context,
    get_cached_evidence,
    set_cached_evidence,
    get_semantic_cached_answer,
    set_semantic_cached_answer,
)
from trading_rag.config import settings
from trading_rag.observability import METRICS, Timer
from trading_rag.esql_guard import ESQLGuard, ESQLValidationError
from trading_rag.reranker import rerank_evidence
from trading_rag.evaluation import evaluate_response
from trading_rag.freshness import FreshnessContract
from trading_rag.cost import CostBudget
from trading_rag.clients import embed_text, es_client


class AgentState(TypedDict):
    question: str
    router_output: RouterOutput | None
    query_type: QueryType | None
    esql_query: str | None
    evidence: RetrievedEvidence | None
    baseline: BaselineStats | None
    analysis: AnalysisOutput | None
    reflections_count: int
    final_response: FinalResponse | None
    error: str | None
    cache_hit: bool
    retrieval_stats: dict
    data_freshness: str | None
    cached_at: str | None
    total_llm_tokens: int
    llm_calls: int
    cost_usd: float
    force_output: bool
    cost_limit_hit: bool
    groundedness_score: float | None
    correctness_score: float | None
    citation_score: float | None
    should_abstain: bool
    abstain_reason: str | None


MAX_REFLECTIONS = settings.llm.max_reflections


def should_continue(state: AgentState) -> str:
    # If no evidence at all, skip reflection cycles
    evidence: RetrievedEvidence | None = state.get("evidence")
    if evidence and not evidence.logs and not evidence.aggregations:
        return "end"
    # Fix 5: Skip reflection for high-confidence structured queries
    router_output: RouterOutput | None = state.get("router_output")
    if router_output and router_output.confidence >= 0.9 and router_output.query_path == QueryPath.STRUCTURED_ESQL:
        return "end"

    if state.get("force_output"):
        return "end"

    if not settings.llm.enable_reflection:
        return "end"

    if state.get("should_abstain"):
        return "end"

    grounded = state.get("groundedness_score") or 0.0
    correct = state.get("correctness_score") or 0.0
    if grounded >= 0.90 and correct >= 0.90:
        return "end"

    if state["reflections_count"] < MAX_REFLECTIONS:
        return "refine"
    return "end"


def router_node(state: AgentState) -> AgentState:
    question = state["question"]
    with Timer("workflow.router"):
        # Switch to LLM-based router for better intent detection
        router_output = route_query_llm(question)
        state["router_output"] = router_output
        state["query_type"] = router_output.query_type
        state["esql_query"] = router_output.esql_query
        CostBudget().update(state, question, router_output.reasoning)
        if CostBudget().exceeded(state):
            state["force_output"] = True
            state["cost_limit_hit"] = True
    return state


async def retrieval_node(state: AgentState) -> AgentState:
    router_output = state["router_output"]
    if not router_output:
        state["error"] = "No router output"
        return state

    cache_context = build_cache_context(
        router_output.query_path.value,
        router_output.symbol,
        router_output.time_window.end if router_output.time_window else None,
    )
    cached = get_cached_answer(state["question"], cache_context)
    if cached:
        state["final_response"] = FinalResponse(**cached)
        state["cache_hit"] = True
        METRICS.inc("workflow.cache_hit")
        return state

    # Semantic cache (optional)
    embedding = embed_text(state["question"])
    if embedding:
        semantic_cached = get_semantic_cached_answer(embedding)
        if semantic_cached:
            state["final_response"] = FinalResponse(**semantic_cached)
            state["cache_hit"] = True
            METRICS.inc("workflow.semantic_cache_hit")
            return state
    
    # Ensure we have a valid time window
    time_window = router_output.time_window
    if time_window is None:
        current_time = datetime.utcnow()
        time_window = TimeWindow(
            start=current_time - timedelta(hours=24),
            end=current_time,
        )
    
    hour = time_window.start.hour
    
    # Apply ES|QL guard if present
    esql_query = router_output.esql_query
    if esql_query:
        try:
            esql_query, warnings = ESQLGuard(es_client).validate_and_patch(esql_query)
            state["retrieval_stats"] = {"esql_guard_warnings": warnings}
        except ESQLValidationError as e:
            state["error"] = str(e)
            return state

    evidence_cache_key = f"{router_output.query_type.value}:{router_output.query_path.value}:{router_output.symbol}:{time_window.start.isoformat()}:{time_window.end.isoformat()}"
    cached_evidence = get_cached_evidence(evidence_cache_key)
    if cached_evidence:
        evidence = RetrievedEvidence(**cached_evidence)
        state["evidence"] = evidence
        state["baseline"] = get_default_baseline()
        return state

    async def run_structured():
        return await asyncio.to_thread(
            retrieve_evidence,
            query_path=router_output.query_path,
            query_type=router_output.query_type,
            query=state["question"],
            time_window=time_window,
            symbol=router_output.symbol,
            esql_query=esql_query,
        )

    async def run_correlation():
        return await asyncio.to_thread(
            correlate_execution_and_feed,
            time_window,
            router_output.symbol,
        )

    async def run_semantic():
        return await asyncio.to_thread(
            semantic_search_incidents,
            state["question"],
            time_window,
        )
    
    baseline_task = asyncio.to_thread(
        get_or_compute_baseline,
        router_output.symbol,
        hour
    )

    async def safe_evidence():
        try:
            return await evidence_task
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Evidence retrieval failed: {e}")
            return None

    async def safe_baseline():
        try:
            return await baseline_task
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"Baseline fetch failed: {e}")
            return None

    try:
        with Timer("workflow.retrieval"):
            if router_output.confidence < 0.6:
                evidence_tasks = [run_structured(), run_correlation(), run_semantic()]
                evidence_results = await asyncio.gather(*evidence_tasks, return_exceptions=True)
                evidence_candidates = [e for e in evidence_results if isinstance(e, RetrievedEvidence)]
                evidence = RetrievedEvidence(
                    logs=[log for ev in evidence_candidates for log in ev.logs],
                    aggregations={},
                    query_used="multi-path",
                    path=router_output.query_path,
                )
            else:
                evidence = await run_structured()
            baseline = await safe_baseline()
            await asyncio.sleep(0)
        if not evidence:
            evidence = None
    except asyncio.TimeoutError:
        state["error"] = "Retrieval timed out"
        METRICS.inc("workflow.retrieval_timeout")
        return state
    
    if evidence is None:
        evidence = RetrievedEvidence(
            logs=[],
            aggregations={},
            query_used="unavailable: retrieval failure",
            path=router_output.query_path,
        )
        
    evidence = rerank_evidence(evidence, state["question"])
    state["evidence"] = evidence
    state["retrieval_stats"].update({
        "path": router_output.query_path.value,
        "query_type": router_output.query_type.value,
        "logs_retrieved": len(evidence.logs),
        "reranked_to": len(evidence.logs),
    })
    if baseline is None:
        baseline = get_default_baseline()
    state["baseline"] = baseline

    try:
        set_cached_evidence(evidence_cache_key, evidence.model_dump())
    except Exception:
        pass

    freshness_label = FreshnessContract().label(None, router_output.query_type)
    state["data_freshness"] = freshness_label
    
    return state


def after_retrieval(state: AgentState) -> str:
    if state.get("cache_hit"):
        return "cached"
    return "analysis"


def analysis_node(state: AgentState) -> AgentState:
    question = state["question"]
    evidence = state["evidence"]
    baseline = state["baseline"]
    
    if not evidence or not baseline:
        state["error"] = "Missing evidence or baseline"
        return state
    
    with Timer("workflow.analysis"):
        analysis = generate_analysis(question, evidence, baseline)
    state["analysis"] = analysis
    CostBudget().update(state, question, analysis.answer)
    if CostBudget().exceeded(state):
        state["force_output"] = True
        state["cost_limit_hit"] = True
    return state


def reflection_node(state: AgentState) -> AgentState:
    question = state["question"]
    evidence = state["evidence"]
    baseline = state["baseline"]
    analysis = state["analysis"]
    
    if not analysis:
        state["error"] = "No analysis to reflect on"
        return state
    
    with Timer("workflow.evaluator"):
        evaluation = evaluate_response(analysis.answer, analysis.citations, evidence, baseline)
    state["groundedness_score"] = evaluation.groundedness_score
    state["correctness_score"] = evaluation.correctness_score
    state["citation_score"] = evaluation.citation_score
    state["should_abstain"] = evaluation.should_abstain
    state["abstain_reason"] = evaluation.abstain_reason
    METRICS.observe_ms("workflow.groundedness", evaluation.groundedness_score * 1000)
    METRICS.observe_ms("workflow.correctness", evaluation.correctness_score * 1000)
    if evaluation.should_abstain:
        METRICS.inc("workflow.abstain")
    if evaluation.should_abstain:
        state["force_output"] = True
    state["reflections_count"] = state.get("reflections_count", 0) + 1
    return state


def refine_analysis_node(state: AgentState) -> AgentState:
    question = state["question"]
    evidence = state["evidence"]
    baseline = state["baseline"]
    
    with Timer("workflow.refine"):
        grounded = state.get("groundedness_score") or 0.0
        correct = state.get("correctness_score") or 0.0
        if grounded < 0.90 or correct < 0.90:
            modified_prompt = (
                f"{question}\n\nPrevious issues: groundedness={grounded:.2f}, correctness={correct:.2f}. "
                "Improve evidence alignment and numeric accuracy."
            )
            analysis = generate_analysis(modified_prompt, evidence, baseline)
        else:
            analysis = generate_analysis(question, evidence, baseline)
    
    state["analysis"] = analysis
    CostBudget().update(state, question, analysis.answer)
    if CostBudget().exceeded(state):
        state["force_output"] = True
        state["cost_limit_hit"] = True
    return state


def final_node(state: AgentState) -> AgentState:
    analysis = state["analysis"]
    router_output = state["router_output"]
    
    if analysis and router_output:
        final_answer = analysis.answer
        if state.get("should_abstain"):
            final_answer = state.get("abstain_reason") or "Insufficient evidence to answer reliably."
        if not state.get("cached_at"):
            state["cached_at"] = datetime.utcnow().isoformat()
        state["final_response"] = FinalResponse(
            answer=final_answer,
            baseline_comparison=analysis.baseline_comparison,
            citations=analysis.citations,
            query_path=router_output.query_path,
            query_type=router_output.query_type,
            reflections=state.get("reflections_count", 0),
            processing_time_ms=0,
            from_cache=False,
            groundedness_score=state.get("groundedness_score"),
            correctness_score=state.get("correctness_score"),
            citation_score=state.get("citation_score"),
            should_abstain=state.get("should_abstain", False),
            abstain_reason=state.get("abstain_reason"),
            data_freshness=state.get("data_freshness"),
            cached_at=state.get("cached_at"),
            cost_usd=state.get("cost_usd"),
            cost_limit_hit=state.get("cost_limit_hit", False),
            retrieval_stats=state.get("retrieval_stats", {}),
        )
    else:
        state["error"] = "Cannot generate final response"
    
    return state


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)
    
    graph.add_node("router", router_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("reflection", reflection_node)
    graph.add_node("refine", refine_analysis_node)
    graph.add_node("final", final_node)
    
    graph.set_entry_point("router")
    graph.add_edge("router", "retrieval")
    graph.add_conditional_edges(
        "retrieval",
        after_retrieval,
        {
            "analysis": "analysis",
            "cached": END,
        },
    )
    graph.add_edge("analysis", "reflection")
    
    graph.add_conditional_edges(
        "reflection",
        should_continue,
        {
            "refine": "refine",
            "end": "final",
        },
    )
    graph.add_edge("refine", "analysis")
    graph.add_edge("final", END)
    
    return graph


def _build_checkpointer():
    if not settings.postgres.dsn:
        return None
    try:
        from langgraph.checkpoint.postgres import PostgresSaver
        return PostgresSaver.from_conn_string(settings.postgres.dsn, settings.postgres.pg_schema)
    except Exception:
        return None


_checkpointer = _build_checkpointer()
workflow = build_graph().compile(checkpointer=_checkpointer) if _checkpointer else build_graph().compile()


async def run_workflow(question: str) -> FinalResponse:
    # Fast cache check using regex router (avoids LLM call when possible)
    try:
        quick_router = route_query(question)
        quick_context = build_cache_context(
            quick_router.query_path.value,
            quick_router.symbol,
            quick_router.time_window.end if quick_router.time_window else None,
        )
    except Exception:
        quick_context = None
    cached = get_cached_answer(question, quick_context)
    if cached:
        return FinalResponse(**cached)

    initial_state: AgentState = {
        "question": question,
        "router_output": None,
        "query_type": None,
        "esql_query": None,
        "evidence": None,
        "baseline": None,
        "analysis": None,
        "reflections_count": 0,
        "final_response": None,
        "error": None,
        "cache_hit": False,
        "retrieval_stats": {},
        "data_freshness": None,
        "cached_at": None,
        "total_llm_tokens": 0,
        "llm_calls": 0,
        "cost_usd": 0.0,
        "force_output": False,
        "cost_limit_hit": False,
        "groundedness_score": None,
        "correctness_score": None,
        "citation_score": None,
        "should_abstain": False,
        "abstain_reason": None,
    }
    
    result = await workflow.ainvoke(initial_state)
    
    if result.get("error"):
        raise ValueError(result["error"])
    
    response = result["final_response"]
    
    # Save to cache using router context and bucketed time window
    if response:
        embedding = embed_text(question)
        router_output = result.get("router_output")
        cache_context = build_cache_context(
            router_output.query_path.value if router_output else None,
            router_output.symbol if router_output else None,
            router_output.time_window.end if router_output and router_output.time_window else None,
        )
        set_cached_answer(question, response.model_dump(), cache_context)
        if embedding:
            set_semantic_cached_answer(embedding, response.model_dump())
        
    return response


async def astream_workflow(question: str):
    """
    Async generator for streaming node updates to the API.
    Used for perceived latency improvement.
    """
    # Tier 1 Cache check (fast regex router to build context)
    try:
        quick_router = route_query(question)
        cache_context = build_cache_context(
            quick_router.query_path.value,
            quick_router.symbol,
            quick_router.time_window.end if quick_router.time_window else None,
        )
    except Exception:
        cache_context = None
    cached = get_cached_answer(question, cache_context)
    if cached:
        yield {"final_response": FinalResponse(**cached), "type": "cache_hit"}
        return

    initial_state: AgentState = {
        "question": question,
        "router_output": None,
        "query_type": None,
        "esql_query": None,
        "evidence": None,
        "baseline": None,
        "analysis": None,
        "reflections_count": 0,
        "final_response": None,
        "error": None,
        "cache_hit": False,
        "retrieval_stats": {},
        "data_freshness": None,
        "cached_at": None,
        "total_llm_tokens": 0,
        "llm_calls": 0,
        "cost_usd": 0.0,
        "force_output": False,
        "cost_limit_hit": False,
        "groundedness_score": None,
        "correctness_score": None,
        "citation_score": None,
        "should_abstain": False,
        "abstain_reason": None,
    }
    
    async for event in workflow.astream(initial_state):
        # Result keys are node names. We flatten them for the UI.
        for node_name, state_update in event.items():
            if not state_update:
                continue
                
            # Cleanly serialize Pydantic objects to dicts for JSON safety
            serializable_update = {}
            if isinstance(state_update, dict):
                for k, v in state_update.items():
                    if hasattr(v, "model_dump"):
                        serializable_update[k] = v.model_dump()
                    elif isinstance(v, list):
                        serializable_update[k] = [item.model_dump() if hasattr(item, "model_dump") else item for item in v]
                    else:
                        serializable_update[k] = v
            
            # If it's an error, yield it explicitly
            if serializable_update.get("error"):
                yield {"error": serializable_update["error"]}
            
            # Yield the node's flattened state update
            yield serializable_update
