"""Compaction summarizers plugged into ADK's built-in EventsCompactionConfig.

ADK decides WHEN to compact (after a turn whose prompt crossed a token
threshold) and WHICH events (all but the most recent N). We decide HOW.

  SummarizingCompactor  keeps conclusions, drops chatter (LLM summary when
                        live, a template when offline).
  DroppingCompactor     the naive sliding window: old turns simply vanish.

Neither one is trusted with constraints. Those are pinned in session state by
the pin_constraint tool and re-sent every turn (see agents.py), so they never
enter the summarisation path at all.
"""

from __future__ import annotations

from google.adk.apps.base_events_summarizer import BaseEventsSummarizer
from google.adk.apps.llm_event_summarizer import LlmEventSummarizer
from google.adk.events.event import Event
from google.adk.events.event_actions import EventActions, EventCompaction
from google.genai import types


def _raw(events: list[Event]) -> list[Event]:
  """Events that are real conversation, not an earlier compaction summary."""
  out = []
  for e in events:
    if e.actions and e.actions.compaction:
      continue
    text = "".join(p.text or "" for p in (e.content.parts if e.content else None) or [])
    if text.startswith(("[Compacted summary", "[Earlier messages removed")):
      continue
    out.append(e)
  return out


def _compaction_event(events: list[Event], text: str) -> Event:
  return Event(
      author="user",
      invocation_id=Event.new_id(),
      actions=EventActions(compaction=EventCompaction(
          start_timestamp=events[0].timestamp,
          end_timestamp=events[-1].timestamp,
          compacted_content=types.Content(role="model", parts=[types.Part(text=text)]),
      )),
  )


class DroppingCompactor(BaseEventsSummarizer):
  """Naive: keep the last N events, drop the rest without reading them."""

  async def maybe_summarize_events(self, *, events: list[Event]) -> Event | None:
    if not events:
      return None
    n = len(_raw(events))
    return _compaction_event(
        events, f"[Earlier messages removed to save space: {n} message{'' if n == 1 else 's'} dropped, unread]")


class SummarizingCompactor(BaseEventsSummarizer):
  """Summarise old turns. Uses Gemini when live, a template when offline."""

  def __init__(self, llm=None):
    self._inner = LlmEventSummarizer(llm=llm, prompt_template=(
        "Summarise this support conversation for the agent that will continue it. "
        "Keep decisions, amounts, transaction IDs, references and open questions. "
        "Drop greetings and small talk. At most 120 words.\n\n{conversation_history}"
    )) if llm is not None else None

  async def maybe_summarize_events(self, *, events: list[Event]) -> Event | None:
    if not events:
      return None
    if self._inner is not None:
      ev = await self._inner.maybe_summarize_events(events=events)
      if ev is not None and ev.actions and ev.actions.compaction:
        content = ev.actions.compaction.compacted_content
        body = "\n".join(p.text or "" for p in (content.parts or []))
        ev.actions.compaction.compacted_content = types.Content(role="model", parts=[types.Part(
            text=f"[Compacted summary of {len(_raw(events))} earlier messages]\n{body}")])
      return ev
    return _compaction_event(events, self._template(events))

  @staticmethod
  def _template(events: list[Event]) -> str:
    asks, tools = [], []
    for ev in _raw(events):
      for p in (ev.content.parts if ev.content else None) or []:
        if ev.author == "user" and p.text:
          asks.append(p.text.strip().rstrip("?.!"))
        if p.function_call is not None and p.function_call.name not in tools:
          tools.append(p.function_call.name)
    topics = "; ".join(asks[-6:])
    return (
        f"[Compacted summary of {len(_raw(events))} earlier messages]\n"
        f"Customer asked {len(asks)} routine questions (most recent: {topics}). "
        f"All were answered. Tools used: {', '.join(tools) or 'none'}. No open tasks."
    )
