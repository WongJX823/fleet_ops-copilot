"""Agent orchestration: intent -> role-scoped tool selection -> retrieval ->
validation -> prompt packing -> LLM -> audited response.

This is the runtime half of the 'RAG + Tool Pipeline' diagram page.
"""
import json

from .. import escalations
from ..audit import record
from ..config import CONFIDENCE_ESCALATION_THRESHOLD, DEFAULT_ROLE, ROLE_TOOLS
from ..models import ChatResponse, Evidence
from ..rag.index import SopIndex
from ..tools import actions, registry
from . import confidence as confidence_scoring
from . import diagnosis
from . import precedence
from . import sanitize
from .intent import IntentClassifier
from .llm import LLMClient
from .prompts import ANSWER_TEMPLATE


class Orchestrator:
    def __init__(self, sop_index: SopIndex):
        self.llm = LLMClient()
        self.classifier = IntentClassifier()
        self.tools = {
            "schedule_lookup": registry.schedule_lookup,
            "fleet_status": registry.fleet_status,
            "sop_search": registry.make_sop_search(sop_index),
        }

    def handle(
        self,
        question: str,
        role: str | None,
        images: list[tuple[str, bytes]],
        notes: list[str],
        history: list[dict] | None = None,
        user: dict | None = None,
    ) -> ChatResponse:
        role = role if role in ROLE_TOOLS else DEFAULT_ROLE
        allowed = ROLE_TOOLS[role]
        intents, intent_mode = self.classifier.classify(question, history)

        evidence: list[Evidence] = []
        for tool_name in intents:
            if tool_name not in allowed:
                notes.append(f"Tool '{tool_name}' is not permitted for role '{role}'; skipped.")
                continue
            evidence.append(self.tools[tool_name](question, role))

        # Guided incident diagnosis (Phase 3): needs roster data, so it runs
        # only for roles that may use fleet_status; drivers still get their
        # driver-facing SOP steps through sop_search.
        scenario = diagnosis.detect_scenario(question)
        proposals: list[dict] = []
        if scenario and "fleet_status" in allowed:
            diag = diagnosis.run(scenario, question)
            evidence.append(diag)
            intents.append(f"diagnosis:{scenario}")
            # Phase 4: derive action proposals from the computed checklist.
            # Server-side records; the client can only reference them by id.
            proposals = actions.propose_from_diagnosis(diag.payload, user, role)

        # Prompt-injection defense (Section 10): retrieved content is data,
        # not instructions. Scrub it before it reaches the prompt; anything
        # neutralized is surfaced, escalated, and audited.
        evidence, injection_findings = sanitize.scrub_evidence(evidence)
        if injection_findings:
            notes.append(
                "Injection defense: suspicious content was neutralized in retrieved data ("
                + "; ".join(injection_findings) + ")"
            )

        # Confidence is scored on retrieved tool evidence only: the diagnosis
        # block is derived from that same data (and always 'fresh'), so letting
        # it count would inflate coverage and dilute staleness signals.
        tool_evidence = [e for e in evidence if e.kind != "diagnosis"]
        stale = [e.source for e in tool_evidence if not e.fresh]
        conflicts = precedence.detect_conflicts(tool_evidence)
        score = confidence_scoring.score(tool_evidence, intents, allowed, intent_mode, conflicts=bool(conflicts))
        escalated = score < CONFIDENCE_ESCALATION_THRESHOLD or bool(conflicts) or bool(injection_findings)
        if stale:
            notes.append(f"Evidence from {', '.join(stale)} exceeded the freshness limit.")
        if conflicts:
            notes.extend(conflicts)
        if not evidence:
            notes.append("No permitted tool matched this question; escalate to a human operator.")
        elif escalated:
            notes.append(
                f"Confidence {score:.2f} is below the escalation threshold "
                f"({CONFIDENCE_ESCALATION_THRESHOLD}); recommend human review."
            )

        packed = ANSWER_TEMPLATE.format(
            role=role,
            question=question,
            evidence=json.dumps(
                [e.model_dump(mode="json") for e in evidence], indent=2, ensure_ascii=False
            ),
        )
        if notes:
            packed += "\n\nOperational notes (mention these to the user):\n- " + "\n- ".join(notes)

        answer = self.llm.answer(packed, images, history)

        escalation_id = None
        if escalated:
            escalation_id = escalations.create(
                {
                    "user": user,
                    "role": role,
                    "question": question,
                    "history": history or [],
                    "intents": intents,
                    "intent_mode": intent_mode,
                    "confidence": score,
                    "notes": list(notes),
                    "evidence": [e.model_dump(mode="json") for e in evidence],
                    "draft_answer": answer,
                    "model": self.llm.model,
                }
            )
            notes.append(f"Escalation {escalation_id} logged for operator review.")

        response = ChatResponse(
            answer=answer,
            intent=intents,
            role=role,
            evidence=evidence,
            model=self.llm.model,
            confidence=score,
            escalated=escalated,
            escalation_id=escalation_id,
            proposed_actions=[actions.summary(pr, role) for pr in proposals],
            notes=notes,
        )
        record(
            {
                "event": "chat_turn",
                "role": role,
                "question": question,
                "intents": intents,
                "intent_mode": intent_mode,
                "tools_used": [e.source for e in evidence],
                "stale_sources": stale,
                "image_count": len(images),
                "model": self.llm.model,
                "confidence": score,
                "escalated": escalated,
                "injection_findings": injection_findings,
                "escalation_id": escalation_id,
                "proposed_actions": [pr["id"] for pr in proposals],
                "answer_preview": answer[:200],
            }
        )
        return response
