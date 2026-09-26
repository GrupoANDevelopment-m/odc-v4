"""Training pipeline tests — dataset curation + constitutional gating."""
import json
from pathlib import Path

import pytest

from odc.training import (
    DatasetCurator, TrainingExample, TrainingStore, PatternStats,
    scrub, MIN_SAMPLES_PER_PATTERN, MIN_BIPOLAR_RATIO,
)


# ── scrub ────────────────────────────────────────────────────────
class TestScrub:
    def test_scrubs_openai_keys(self):
        assert "[REDACTED_KEY]" in scrub("my key is sk-abcdefghijklmnopqrstuvwxyz1234567890")

    def test_scrubs_nvidia_keys(self):
        assert "[REDACTED_KEY]" in scrub("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")

    def test_scrubs_github_tokens(self):
        assert "[REDACTED_TOKEN]" in scrub("ghp_D8GTjgpSzjbglWi5NRzBvtrzyLVEzq47EXAMPLE")

    def test_scrubs_aws_access_keys(self):
        assert "[REDACTED_AWS]" in scrub("AKIAIOSFODNN7EXAMPLE")

    def test_scrubs_pem_blocks(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        assert "[REDACTED_PEM]" in scrub(text)

    def test_scrubs_ssn(self):
        assert "[REDACTED_SSN]" in scrub("SSN 123-45-6789")

    def test_scrubs_credit_card(self):
        assert "[REDACTED_CC]" in scrub("card 4111-1111-1111-1111")

    def test_scrubs_email(self):
        assert "[REDACTED_EMAIL]" in scrub("contact me at user@example.com")

    def test_scrubs_absolute_paths(self):
        assert "[REDACTED_PATH]" in scrub("file at /home/user/secret.txt")

    def test_clean_text_passes_through(self):
        assert scrub("just normal text") == "just normal text"

    def test_handles_empty(self):
        assert scrub("") == ""


# ── bipolar pattern gate (I9, I10) ──────────────────────────────
class TestBipolarGate:
    def test_pattern_bipolar_true_when_mixed(self):
        p = PatternStats(tool="x", error_class="timeout")
        p.success = 8
        p.failure = 2  # 20% minority — passes 15% threshold
        assert p.bipolar is True

    def test_pattern_bipolar_false_when_too_small(self):
        p = PatternStats(tool="x", error_class="timeout")
        p.success = 2
        p.failure = 1  # only 3 total, below MIN_SAMPLES_PER_PATTERN
        assert p.bipolar is False

    def test_pattern_bipolar_false_when_all_success(self):
        p = PatternStats(tool="x", error_class="")
        p.success = 100
        p.failure = 0
        assert p.bipolar is False  # no minority = confirmation bias

    def test_pattern_bipolar_false_when_skewed(self):
        # 2% minority = below 15%
        p = PatternStats(tool="x", error_class="auth")
        p.success = 99
        p.failure = 1
        assert p.bipolar is False


# ── curator end-to-end ──────────────────────────────────────────
class TestCurator:
    def _write_journal(self, data_dir: Path, rows: list[dict]):
        d = data_dir / "refinement"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "journal.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

    def test_curate_with_no_data_returns_zero(self, tmp_path):
        c = DatasetCurator(tmp_path)
        result = c.curate()
        assert result["total_raw"] == 0
        assert result["after_bipolar"] == 0

    def test_curate_filters_unipolar_patterns(self, tmp_path):
        # 10 successes, 0 failures for one tool → pattern not bipolar
        rows = [
            {"tool": "osint.weather", "error_class": "", "success": True,
             "input": "weather in SP", "output": "16°C", "ts": 1.0}
            for _ in range(10)
        ]
        self._write_journal(tmp_path, rows)
        c = DatasetCurator(tmp_path)
        result = c.curate()
        # No bipolar patterns, so 0 examples survive
        assert result["after_bipolar"] == 0
        assert result["patterns_dropped"][0]["key"] == "osint.weather"

    def test_curate_keeps_bipolar_patterns(self, tmp_path):
        # 8 successes + 2 failures for same pattern → bipolar
        rows = []
        for i in range(8):
            rows.append({
                "tool": "osint.bitcoin", "error_class": "", "success": True,
                "input": f"btc price {i}", "output": "$75000",
                "ts": float(i),
            })
        for i in range(2):
            rows.append({
                "tool": "osint.bitcoin", "error_class": "timeout",
                "success": False,
                "input": f"btc price {i}", "output": "",
                "ts": float(10 + i),
            })
        self._write_journal(tmp_path, rows)
        c = DatasetCurator(tmp_path)
        result = c.curate()
        # All 10 survive bipolar gate
        assert result["after_bipolar"] == 10
        assert result["patterns_kept"] == 1

    def test_curate_scrubs_secrets(self, tmp_path):
        rows = []
        for i in range(6):
            rows.append({
                "tool": "fs.read", "error_class": "", "success": True,
                "input": f"read /home/user/secret-{i}.txt",
                "output": f"contains sk-abcdefghijklmnopqrstuvwxyz{i:04d}",
                "ts": float(i),
            })
        for i in range(2):
            rows.append({
                "tool": "fs.read", "error_class": "permission_denied",
                "success": False,
                "input": f"read /etc/passwd",
                "output": "",
                "ts": float(10 + i),
            })
        self._write_journal(tmp_path, rows)
        c = DatasetCurator(tmp_path)
        c.curate()

        # Read back dataset, verify scrubbed
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        assert dataset_path.exists()
        content = dataset_path.read_text(encoding="utf-8")
        assert "sk-abcdef" not in content
        assert "[REDACTED_KEY]" in content
        assert "[REDACTED_PATH]" in content

    def test_curate_writes_stats(self, tmp_path):
        rows = [
            {"tool": "t", "error_class": "", "success": True,
             "input": "u", "output": "a", "ts": 0.0}
            for _ in range(6)
        ] + [
            {"tool": "t", "error_class": "e", "success": False,
             "input": "u", "output": "", "ts": 1.0}
            for _ in range(2)
        ]
        self._write_journal(tmp_path, rows)
        c = DatasetCurator(tmp_path)
        c.curate()
        stats_path = tmp_path / "training" / "stats.json"
        assert stats_path.exists()
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        assert stats["after_bipolar"] == 8

    def test_curate_evidence_tier_set(self, tmp_path):
        rows = []
        for i in range(6):
            rows.append({
                "tool": "t", "error_class": "", "success": True,
                "input": "u", "output": "a", "ts": 0.0,
            })
        for i in range(2):
            rows.append({
                "tool": "t", "error_class": "e", "success": False,
                "input": "u", "output": "", "ts": 1.0,
            })
        self._write_journal(tmp_path, rows)
        c = DatasetCurator(tmp_path)
        c.curate()
        dataset_path = tmp_path / "training" / "dataset.jsonl"
        examples = [json.loads(l) for l in dataset_path.read_text().splitlines()]
        for ex in examples:
            assert ex["evidence_tier"] == "DIRECT_OBSERVATION"


# ── TrainingStore facade ────────────────────────────────────────
class TestTrainingStore:
    def test_stats_with_no_data(self, tmp_path):
        s = TrainingStore(data_dir=tmp_path)
        stats = s.stats()
        assert stats["total"] == 0
        assert stats["last_export"] == "never"

    def test_export_creates_dataset(self, tmp_path):
        # Write field data
        d = tmp_path / "refinement"
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            {"tool": "t", "error_class": "", "success": True,
             "input": "u", "output": "a", "ts": 0.0}
            for _ in range(6)
        ] + [
            {"tool": "t", "error_class": "e", "success": False,
             "input": "u", "output": "", "ts": 1.0}
            for _ in range(2)
        ]
        with open(d / "journal.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

        s = TrainingStore(data_dir=tmp_path)
        result = s.export_dataset()
        assert result["ok"] is True
        assert result["after_bipolar"] == 8

    def test_start_finetune_requires_export(self, tmp_path):
        s = TrainingStore(data_dir=tmp_path)
        r = s.start_finetune()
        assert r["ok"] is False
        assert "export first" in r["error"]

    def test_start_finetune_after_export(self, tmp_path):
        # Set up + export
        d = tmp_path / "refinement"
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            {"tool": "t", "error_class": "", "success": True,
             "input": "u", "output": "a", "ts": 0.0}
            for _ in range(6)
        ] + [
            {"tool": "t", "error_class": "e", "success": False,
             "input": "u", "output": "", "ts": 1.0}
            for _ in range(2)
        ]
        with open(d / "journal.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")

        s = TrainingStore(data_dir=tmp_path)
        s.export_dataset()
        r = s.start_finetune()
        assert r["ok"] is True
        assert r["examples"] == 8
        # Modelfile created
        modelfile = tmp_path / "training" / "Modelfile"
        assert modelfile.exists()
        assert "FROM" in modelfile.read_text()
        assert "SYSTEM" in modelfile.read_text()

    def test_last_run_records(self, tmp_path):
        d = tmp_path / "refinement"
        d.mkdir(parents=True, exist_ok=True)
        rows = [
            {"tool": "t", "error_class": "", "success": True,
             "input": "u", "output": "a", "ts": 0.0}
            for _ in range(6)
        ] + [
            {"tool": "t", "error_class": "e", "success": False,
             "input": "u", "output": "", "ts": 1.0}
            for _ in range(2)
        ]
        with open(d / "journal.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        s = TrainingStore(data_dir=tmp_path)
        s.export_dataset()
        last = s.last_run()
        assert last is not None
        assert last["type"] == "export"


# ── TrainingExample shape ───────────────────────────────────────
class TestTrainingExample:
    def test_to_dict_roundtrip(self):
        ex = TrainingExample(
            messages=[{"role": "user", "content": "x"}],
            outcome_success=True,
            evidence_tier="DIRECT_OBSERVATION",
        )
        d = ex.to_dict()
        assert d["messages"][0]["content"] == "x"
        assert d["outcome_success"] is True
        assert d["evidence_tier"] == "DIRECT_OBSERVATION"
