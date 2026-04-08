from __future__ import annotations
import re
from pathlib import Path

import yaml


class TranscriptAdapter:
    CHECKPOINT_RE = re.compile(r"<checkpoint>(.*?)</checkpoint>", re.S)

    def parse_checkpoint(self, text: str) -> dict:
        matches = self.CHECKPOINT_RE.findall(text)
        if not matches:
            return {}
        # Use the most recent checkpoint in the buffer
        block = matches[-1]
        try:
            result = yaml.safe_load(block)
            if isinstance(result, dict):
                return result
        except yaml.YAMLError:
            pass
        # Fallback: manual line-by-line parsing for non-YAML checkpoint format
        return self._parse_lines(block)

    def _parse_lines(self, block: str) -> dict:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        result = {"evidence": [], "candidate_next_actions": [], "needs": [], "question_for_supervisor": []}
        current_list = None
        for line in lines:
            if line.startswith("status:"):
                result["status"] = line.split(":", 1)[1].strip()
            elif line.startswith("current_node:"):
                result["current_node"] = line.split(":", 1)[1].strip()
            elif line.startswith("summary:"):
                result["summary"] = line.split(":", 1)[1].strip()
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

    def read_text(self, path: str) -> str:
        return Path(path).read_text(encoding="utf-8")
