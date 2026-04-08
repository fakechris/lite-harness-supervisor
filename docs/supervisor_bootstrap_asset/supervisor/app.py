from __future__ import annotations
import argparse
import json
from pathlib import Path

from supervisor.plan.loader import load_spec
from supervisor.storage.state_store import StateStore
from supervisor.loop import SupervisorLoop
from supervisor.adapters.transcript_adapter import TranscriptAdapter

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("spec_path")
    parser.add_argument("--event-file", default=None, help="JSON file with a single event for local testing")
    args = parser.parse_args()

    spec = load_spec(args.spec_path)
    store = StateStore("runtime")
    state = store.load_or_init(spec)
    loop = SupervisorLoop(store)

    if args.event_file:
        event = json.loads(Path(args.event_file).read_text())
        if event.get("type") == "agent_output" and "text" in event.get("payload", {}):
            adapter = TranscriptAdapter()
            event["payload"]["checkpoint"] = adapter.parse_checkpoint(event["payload"]["text"])
        store.append_event(event)
        loop.handle_event(state, event)

        if str(state.top_state) == "TopState.GATING" or getattr(state.top_state, "value", "") == "GATING":
            decision = loop.gate(spec, state)
            store.append_decision(decision)
            loop.apply_decision(spec, state, decision)

        if getattr(state.top_state, "value", "") == "VERIFYING":
            verification = loop.verify_current_node(spec, state)
            store.append_event({"type": "verification_finished", "payload": verification})
            loop.apply_verification(spec, state, verification)

        store.save(state)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return

    print("Spec loaded and state initialized.")
    print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
