from __future__ import annotations
import re
from pathlib import Path

import yaml

from supervisor.domain.models import Checkpoint


class TranscriptAdapter:
    CHECKPOINT_RE = re.compile(r"<checkpoint>(.*?)</checkpoint>", re.S)

    def parse_checkpoint(self, text: str, *, run_id: str = "", surface_id: str = "") -> Checkpoint | None:
        checkpoints = self.parse_checkpoints(text, run_id=run_id, surface_id=surface_id)
        return checkpoints[-1] if checkpoints else None

    def parse_checkpoints(self, text: str, *, run_id: str = "", surface_id: str = "") -> list[Checkpoint]:
        """Parse the most recent checkpoint from terminal output.

        Returns a list of Checkpoint dataclasses in appearance order.
        *run_id* and *surface_id* are filled in by the caller (supervisor loop)
        to ensure identity even if the agent omitted them.
        """
        matches = self.CHECKPOINT_RE.findall(text)
        if not matches:
            return []
        parsed: list[Checkpoint] = []
        for block in matches:
            checkpoint = self._build_checkpoint(block, run_id=run_id, surface_id=surface_id)
            if checkpoint is not None:
                parsed.append(checkpoint)
        return parsed

    def _build_checkpoint(self, block: str, *, run_id: str = "", surface_id: str = "") -> Checkpoint | None:
        raw: dict = {}
        try:
            parsed = yaml.safe_load(block)
            if isinstance(parsed, dict):
                raw = parsed
        except yaml.YAMLError:
            pass
        if not raw:
            raw = self._parse_lines(block)
        if "status" not in raw or "current_node" not in raw:
            return None
        return Checkpoint(
            status=raw.get("status", ""),
            current_node=raw.get("current_node", ""),
            summary=raw.get("summary", ""),
            run_id=str(raw.get("run_id", "")) or run_id,
            checkpoint_seq=self._safe_int(raw.get("checkpoint_seq", 0)),
            surface_id=str(raw.get("surface_id", "")) or surface_id,
            evidence=raw.get("evidence", []),
            candidate_next_actions=raw.get("candidate_next_actions", []),
            needs=raw.get("needs", []),
            question_for_supervisor=raw.get("question_for_supervisor", []),
        )

    def _parse_lines(self, block: str) -> dict:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        result: dict = {"evidence": [], "candidate_next_actions": [], "needs": [], "question_for_supervisor": []}
        current_list = None
        for line in lines:
            if line.startswith("status:"):
                result["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("current_node:"):
                result["current_node"] = line.split(":", 1)[1].strip()
            elif line.startswith("summary:"):
                result["summary"] = line.split(":", 1)[1].strip()
            elif line.startswith("run_id:"):
                result["run_id"] = line.split(":", 1)[1].strip()
            elif line.startswith("checkpoint_seq:"):
                result["checkpoint_seq"] = line.split(":", 1)[1].strip()
            elif line.startswith("evidence:"):
                current_list = "evidence"
            elif line.startswith("candidate_next_actions:"):
                current_list = "candidate_next_actions"
            elif line.startswith("needs:"):
                current_list = "needs"
            elif line.startswith("question_for_supervisor:"):
                current_list = "question_for_supervisor"
            elif line.startswith("- "):
                if current_list:
                    result[current_list].append(line[2:].strip())
        return result

    @staticmethod
    def _safe_int(val, default: int = 0) -> int:
        try:
            return int(val)
        except (ValueError, TypeError):
            return default

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")
