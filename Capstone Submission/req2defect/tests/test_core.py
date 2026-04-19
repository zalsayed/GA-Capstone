"""
tests/test_core.py — Unit tests for core modules.
Run: pytest tests/ -v
"""

import pytest
import json
import os
import pandas as pd
import sys

os.environ.setdefault("APP_ENV", "testing")
os.environ.setdefault("MOCK_MODE", "true")
os.environ.setdefault("DB_PATH", ":memory:")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_config_loads():
    from core.config import AppConfig

    cfg = AppConfig()
    assert cfg.app_env is not None
    assert isinstance(cfg.mock_mode_default, bool)


def test_config_validate_warns_without_key():
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.anthropic_api_key = ""
    cfg.gemini_api_key = ""
    warnings = cfg.validate()
    assert any("API key" in w for w in warnings)


def test_config_is_production():
    from core.config import AppConfig

    cfg = AppConfig()
    cfg.app_env = "production"
    assert cfg.is_production is True
    cfg.app_env = "development"
    assert cfg.is_production is False


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB to a temp file for each test."""
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import core.config as cc

    cc._config = None
    from core.database import init_db

    init_db()
    yield db_file
    cc._config = None


def test_create_and_get_run(tmp_db):
    from core.database import create_run, get_run

    create_run("RUN-TEST-001", "Claude", "claude-sonnet", "Web", False)
    run = get_run("RUN-TEST-001")
    assert run is not None
    assert run["run_id"] == "RUN-TEST-001"
    assert run["provider"] == "Claude"


def test_save_and_load_requirements(tmp_db):
    from core.database import create_run, save_requirements, load_requirements

    create_run("RUN-REQ-001", "Claude", "claude-sonnet", "Web", True)
    mrd = {
        "product": "TestApp",
        "modules": [],
        "api_endpoints": [],
        "non_functional": [],
    }
    save_requirements("RUN-REQ-001", mrd)
    loaded = load_requirements("RUN-REQ-001")
    assert loaded["product"] == "TestApp"


def test_save_and_load_test_cases(tmp_db):
    from core.database import create_run, save_test_cases, load_test_cases

    create_run("RUN-TC-001", "Claude", "claude-sonnet", "Web", True)
    df = pd.DataFrame(
        [
            {
                "TC_ID": "TC-001",
                "Module": "Auth",
                "Type": "Functional",
                "Scenario": "Login test",
                "Severity": "Critical",
                "Status": "Pending",
            }
        ]
    )
    save_test_cases("RUN-TC-001", df)
    loaded = load_test_cases("RUN-TC-001")
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded.iloc[0]["TC_ID"] == "TC-001"


def test_save_and_load_execution_results(tmp_db):
    from core.database import create_run, save_execution_results, load_execution_results

    create_run("RUN-EXEC-001", "Claude", "claude-sonnet", "Web", True)
    df = pd.DataFrame([{"TC_ID": "TC-001", "Status": "PASS", "Duration_ms": 350}])
    save_execution_results("RUN-EXEC-001", df, duration_sec=1.5)
    loaded = load_execution_results("RUN-EXEC-001")
    assert loaded is not None
    assert loaded.iloc[0]["Status"] == "PASS"


def test_save_and_load_defects(tmp_db):
    from core.database import create_run, save_defects, load_defects

    create_run("RUN-DEF-001", "Claude", "claude-sonnet", "Web", True)
    df = pd.DataFrame(
        [
            {
                "Defect_No": "DEF-001",
                "TC_ID": "TC-001",
                "Severity": "Critical",
                "Description": "Login broken",
                "Status": "Open",
            }
        ]
    )
    save_defects("RUN-DEF-001", df)
    loaded = load_defects("RUN-DEF-001")
    assert loaded is not None
    assert loaded.iloc[0]["Defect_No"] == "DEF-001"


def test_list_and_delete_runs(tmp_db):
    from core.database import create_run, list_runs, delete_run, get_run

    create_run("RUN-A", "Claude", "model", "Web", True)
    create_run("RUN-B", "Gemini", "model", "Android", False)
    runs = list_runs()
    assert len(runs) >= 2
    delete_run("RUN-A")
    assert get_run("RUN-A") is None


def test_token_usage_tracking(tmp_db):
    from core.database import create_run, record_token_usage, get_token_summary

    create_run("RUN-TOK", "Claude", "claude-sonnet", "Web", False)
    record_token_usage("RUN-TOK", "agent_a", "Claude", "claude-sonnet", 1000, 500)
    record_token_usage("RUN-TOK", "agent_b", "Claude", "claude-sonnet", 2000, 800)
    summary = get_token_summary("RUN-TOK")
    assert summary["input"] == 3000
    assert summary["output"] == 1300
    assert summary["calls"] == 2


def test_db_log_and_get_logs(tmp_db):
    from core.database import create_run, db_log, get_logs

    create_run("RUN-LOG", "Claude", "model", "Web", True)
    db_log("RUN-LOG", "INFO", "Pipeline started")
    db_log("RUN-LOG", "OK", "Agent A complete")
    logs = get_logs("RUN-LOG")
    assert len(logs) == 2
    assert logs[0]["message"] == "Pipeline started"


def test_parse_llm_json_valid():
    from utils.helpers import parse_llm_json

    raw = '{"product": "TestApp", "modules": []}'
    result = parse_llm_json(raw, {})
    assert result["product"] == "TestApp"


def test_parse_llm_json_with_fences():
    from utils.helpers import parse_llm_json

    raw = '```json\n{"product": "TestApp"}\n```'
    result = parse_llm_json(raw, {"product": "fallback"})
    assert result["product"] == "TestApp"


def test_parse_llm_json_invalid_uses_fallback():
    from utils.helpers import parse_llm_json

    raw = "This is not JSON at all!!!"
    fallback = {"product": "fallback"}
    result = parse_llm_json(raw, fallback)
    assert result == fallback


def test_mock_master_req_structure():
    from utils.helpers import mock_master_req

    mrd = mock_master_req("text")
    assert "modules" in mrd
    assert "api_endpoints" in mrd
    assert "non_functional" in mrd
    assert len(mrd["modules"]) > 0
    for mod in mrd["modules"]:
        assert "requirements" in mod
        for req in mod["requirements"]:
            assert "id" in req
            assert "priority" in req
            assert req["priority"] in ("Critical", "High", "Medium", "Low")


def test_mock_test_cases_df_structure():
    from utils.helpers import mock_test_cases_df

    df = mock_test_cases_df()
    assert len(df) > 0
    required_cols = [
        "TC_ID",
        "Module",
        "Type",
        "Scenario",
        "Test_Kind",
        "Severity",
        "Status",
    ]
    for col in required_cols:
        assert col in df.columns, f"Missing column: {col}"
    assert set(df["Test_Kind"].unique()).issubset({"Positive", "Negative", "Boundary"})


def test_mock_defects_df_from_failures():
    from utils.helpers import mock_defects_df

    failures = pd.DataFrame(
        [
            {
                "TC_ID": "TC-001",
                "Scenario": "Login test",
                "Module": "Auth",
                "Severity": "Critical",
                "Expected_Result": "200 OK",
                "Actual_Result": "HTTP 500 — Internal Server Error",
            }
        ]
    )
    df = mock_defects_df(failures)
    assert len(df) == 1
    assert df.iloc[0]["TC_ID"] == "TC-001"
    assert df.iloc[0]["Status"] == "Open"


def test_generate_playwright_script():
    from utils.helpers import generate_playwright_script, mock_test_cases_df

    df = mock_test_cases_df().head(3)
    script = generate_playwright_script(df)
    assert "import pytest" in script
    assert "playwright" in script
    assert "BASE_URL" in script
    for tc_id in df["TC_ID"].tolist():
        fn = tc_id.lower().replace("-", "_")
        assert f"def test_{fn}" in script


def test_generate_github_actions_yaml():
    from utils.helpers import generate_github_actions_yaml, mock_test_cases_df

    df = mock_test_cases_df()
    yaml = generate_github_actions_yaml(df)
    assert "name: Req2Defect" in yaml
    assert "pytest" in yaml
    assert "ANTHROPIC_API_KEY" in yaml


# ── Executor tests ───────────────────────────────────────────────


def test_simulation_produces_correct_shape():
    from core.executor import _simulate_execution
    from utils.helpers import mock_test_cases_df

    tc_df = mock_test_cases_df()
    results = _simulate_execution(tc_df, None, None)
    assert len(results) == len(tc_df)
    assert "Status" in results.columns
    assert set(results["Status"].unique()).issubset({"PASS", "FAIL"})


def test_simulation_severity_distribution():
    """Critical tests should fail less often than Low tests on average."""
    from core.executor import _simulate_execution
    import pandas as pd

    # Create 100 Critical and 100 Low tests
    rows = [
        {
            "TC_ID": f"TC-C{i:03d}",
            "Severity": "Critical",
            "Type": "Functional",
            "Scenario": "crit",
            "Module": "A",
            "Expected_Result": "ok",
        }
        for i in range(100)
    ] + [
        {
            "TC_ID": f"TC-L{i:03d}",
            "Severity": "Low",
            "Type": "Functional",
            "Scenario": "low",
            "Module": "A",
            "Expected_Result": "ok",
        }
        for i in range(100)
    ]
    df = pd.DataFrame(rows)
    results = _simulate_execution(df, None, None)
    crit_fail = (
        results[results["TC_ID"].str.startswith("TC-C")]["Status"] == "FAIL"
    ).mean()
    low_fail = (
        results[results["TC_ID"].str.startswith("TC-L")]["Status"] == "FAIL"
    ).mean()
    # Statistical: Critical should fail less. Allow tolerance for randomness.
    assert (
        crit_fail <= low_fail + 0.2
    ), f"Critical fail rate {crit_fail:.2f} unexpectedly higher than Low {low_fail:.2f}"
