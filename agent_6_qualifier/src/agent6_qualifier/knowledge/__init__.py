"""Agent6/Agent7 knowledge / playbook layer (advisory, fail-safe).

RULES decide WHAT IS TRUE (code).
STATE decides WHAT WE ALREADY KNOW (session).
KNOWLEDGE decides HOW TO ACT (this package).
LLM decides HOW TO SAY IT (brain.polish).
"""

from __future__ import annotations

from agent6_qualifier.knowledge.models import KnowledgeEntry, RetrievalContext
from agent6_qualifier.knowledge.retriever import KnowledgeRetriever, retrieve_for_turn

__all__ = [
    "KnowledgeEntry",
    "RetrievalContext",
    "KnowledgeRetriever",
    "retrieve_for_turn",
]
