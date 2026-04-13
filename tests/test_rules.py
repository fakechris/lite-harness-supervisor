from supervisor.gates.rules import classify_checkpoint


def test_classify_checkpoint_uses_summary_for_missing_external_input():
    checkpoint = {
        "status": "working",
        "summary": "waiting for user credentials before proceeding",
        "needs": ["none"],
        "question_for_supervisor": ["none"],
        "evidence": [],
    }

    assert classify_checkpoint(checkpoint) == "MISSING_EXTERNAL_INPUT"


def test_classify_checkpoint_uses_evidence_for_dangerous_action():
    checkpoint = {
        "status": "working",
        "summary": "progressing carefully",
        "needs": ["none"],
        "question_for_supervisor": ["none"],
        "evidence": ["delete production database after export"],
    }

    assert classify_checkpoint(checkpoint) == "DANGEROUS_ACTION"


def test_classify_checkpoint_preserves_evidence_keys_for_missing_input_detection():
    checkpoint = {
        "status": "working",
        "summary": "progressing carefully",
        "needs": ["none"],
        "question_for_supervisor": ["none"],
        "evidence": [{"need": "credentials for dingtalk tenant"}],
    }

    assert classify_checkpoint(checkpoint) == "MISSING_EXTERNAL_INPUT"


def test_classify_checkpoint_does_not_treat_waiting_for_test_as_blocked():
    checkpoint = {
        "status": "working",
        "summary": "waiting for test to finish before collecting results",
        "needs": ["none"],
        "question_for_supervisor": ["none"],
        "evidence": [],
    }

    assert classify_checkpoint(checkpoint) is None
