"""
utils/helpers.py — Req2Defect Shared Utilities
LLM routing (Claude, Gemini, Groq, OpenRouter, Ollama), logging, script generators.
Mock mode removed — all runs go live.
"""

import streamlit as st
import datetime
import random
import json
import pandas as pd
import os
import sys


# ─────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────

def log(message: str, level: str = "INFO"):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] [{level}] {message}"
    if "agent_logs" not in st.session_state:
        st.session_state["agent_logs"] = []
    st.session_state["agent_logs"].append(entry)
    return entry


def get_provider_label() -> str:
    """Return a short label for the currently active provider."""
    provider = st.session_state.get("config", {}).get("provider", "Not configured")
    short = {
        "Claude (Anthropic)": "Claude",
        "Gemini":             "Gemini",
        "Groq (free)":        "Groq",
        "OpenRouter (free)":  "OpenRouter",
        "Ollama (local)":     "Ollama",
    }
    return short.get(provider, provider)


# ─────────────────────────────────────────────────────────────
# LLM Routing
# ─────────────────────────────────────────────────────────────

def llm_call(system_prompt: str, user_content: str, fallback_message: str,
             max_tokens: int = 4096) -> str:
    """
    Route an LLM call to the configured provider.
    Supported: Claude | Gemini | Groq | OpenRouter | Ollama
    fallback_message is shown if no key is configured — never silent.
    """
    cfg = st.session_state.get("config", {})
    provider = cfg.get("provider", "")
    provider_clean = provider.replace(" (free)", "").replace(" (local)", "")
    log(f"LLM call — provider: {provider}, max_tokens: {max_tokens}", "INFO")

    if provider_clean == "Claude (Anthropic)" or provider_clean == "Claude":
        return _call_claude(system_prompt, user_content, cfg, max_tokens)
    elif provider_clean == "Gemini":
        return _call_gemini(system_prompt, user_content, cfg)
    elif provider_clean == "Groq":
        return _call_groq(system_prompt, user_content, cfg, max_tokens)
    elif provider_clean == "OpenRouter":
        return _call_openrouter(system_prompt, user_content, cfg, max_tokens)
    elif provider_clean == "Ollama":
        return _call_ollama(system_prompt, user_content, cfg)
    else:
        log(f"No provider configured — cannot call LLM", "ERROR")
        return fallback_message


def _call_claude(system_prompt, user_content, cfg, max_tokens=4096):
    api_key = cfg.get("anthropic_key", "") or os.environ.get("ANTHROPIC_API_KEY", "")
    model   = cfg.get("claude_model", "claude-sonnet-4-6")
    if not api_key:
        log("No Anthropic API key — set it in the sidebar or ANTHROPIC_API_KEY env var", "ERROR")
        return ""
    try:
        import anthropic
        client  = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=model, max_tokens=max_tokens, system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )
        text = message.content[0].text
        log(f"Claude ({model}) — {message.usage.input_tokens} in / {message.usage.output_tokens} out", "OK")
        _track_tokens(message.usage.input_tokens, message.usage.output_tokens)
        run_id = st.session_state.get("run_id")
        if run_id:
            try:
                sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
                from core.database import record_token_usage
                record_token_usage(run_id, "unknown", "Claude (Anthropic)", model,
                                   message.usage.input_tokens, message.usage.output_tokens)
            except Exception as db_err:
                log(f"Token DB write failed: {db_err}", "WARN")
        return text
    except ImportError:
        log("anthropic package not installed — pip install anthropic", "ERROR")
        return ""
    except Exception as e:
        log(f"Claude error: {e}", "ERROR")
        return ""


def _call_gemini(system_prompt, user_content, cfg):
    api_key = cfg.get("gemini_key", "") or os.environ.get("GEMINI_API_KEY", "")
    model   = cfg.get("gemini_model", "gemini-2.0-flash")
    if not api_key:
        log("No Gemini API key — set it in the sidebar or GEMINI_API_KEY env var", "ERROR")
        return ""
    try:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        full_prompt = f"{system_prompt}\n\n---\n\n{user_content}"
        m = genai.GenerativeModel(model_name=model)
        response = m.generate_content(full_prompt)
        if not response.text:
            log(f"Gemini ({model}) returned empty response", "WARN")
            return ""
        log(f"Gemini ({model}) responded — {len(response.text)} chars", "OK")
        return response.text
    except ImportError:
        log("google-generativeai not installed — pip install google-generativeai", "ERROR")
        return ""
    except Exception as e:
        log(f"Gemini error: {e}", "ERROR")
        return ""


def _call_ollama(system_prompt, user_content, cfg):
    base_url = cfg.get("ollama_url", "http://localhost:11434")
    model    = cfg.get("ollama_model", "llama3")
    try:
        import requests
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            "stream": False,
        }
        r = requests.post(f"{base_url}/api/chat", json=payload, timeout=180)
        r.raise_for_status()
        text = r.json()["message"]["content"]
        log(f"Ollama ({model}) responded", "OK")
        return text
    except Exception as e:
        log(f"Ollama error: {e}", "ERROR")
        return ""


def _call_groq(system_prompt, user_content, cfg, max_tokens=4096):
    api_key = cfg.get("groq_key", "") or os.environ.get("GROQ_API_KEY", "")
    model   = cfg.get("groq_model", "llama-3.3-70b-versatile")
    if not api_key:
        log("No Groq API key — get a free key at console.groq.com", "ERROR")
        return ""
    try:
        import requests
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
                          headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        data  = r.json()
        text  = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        log(f"Groq ({model}) — {usage.get('prompt_tokens','?')} in / {usage.get('completion_tokens','?')} out", "OK")
        _track_tokens(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return text
    except Exception as e:
        log(f"Groq error: {e}", "ERROR")
        return ""


def _call_openrouter(system_prompt, user_content, cfg, max_tokens=4096):
    api_key = cfg.get("openrouter_key", "") or os.environ.get("OPENROUTER_API_KEY", "")
    model   = cfg.get("openrouter_model", "meta-llama/llama-3.3-70b-instruct:free")
    if not api_key:
        log("No OpenRouter API key — get a free key at openrouter.ai", "ERROR")
        return ""
    try:
        import requests
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://github.com/req2defect",
            "X-Title":       "Req2Defect Pipeline",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }
        r = requests.post("https://openrouter.ai/api/v1/chat/completions",
                          headers=headers, json=payload, timeout=120)
        r.raise_for_status()
        data  = r.json()
        text  = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        log(f"OpenRouter ({model}) — {usage.get('prompt_tokens','?')} in / {usage.get('completion_tokens','?')} out", "OK")
        _track_tokens(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
        return text
    except Exception as e:
        log(f"OpenRouter error: {e}", "ERROR")
        return ""


def _track_tokens(input_tokens: int, output_tokens: int):
    if "token_usage" not in st.session_state:
        st.session_state["token_usage"] = {"input": 0, "output": 0, "calls": 0}
    st.session_state["token_usage"]["input"]  += input_tokens
    st.session_state["token_usage"]["output"] += output_tokens
    st.session_state["token_usage"]["calls"]  += 1


def parse_llm_json(raw: str, fallback):
    """Safely parse JSON from LLM output, stripping markdown fences."""
    import re
    if not raw or not raw.strip():
        log("LLM returned empty response", "WARN")
        return fallback
    try:
        clean = raw.strip()
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", clean, re.DOTALL)
        if fence_match:
            clean = fence_match.group(1).strip()
        elif not (clean.startswith("{") or clean.startswith("[")):
            for start_char in ["{", "["]:
                idx = clean.find(start_char)
                if idx != -1:
                    clean = clean[idx:]
                    break
        result = json.loads(clean)
        log("JSON parsed successfully", "OK")
        return result
    except Exception as e:
        log(f"JSON parse failed: {e}", "WARN")
        return fallback


# ─────────────────────────────────────────────────────────────
# Script Generators
# ─────────────────────────────────────────────────────────────

_PLAYWRIGHT_SYSTEM = """You are a senior Python QA automation engineer specialising in Playwright.

Given a list of test cases (JSON array), generate a complete, immediately runnable pytest-playwright test suite.

RULES:
1. Every test function must have REAL, executable Playwright code — no pass, no TODO, no stubs.
2. Use page.goto, page.fill, page.click, page.select_option, expect(...) etc. as appropriate.
3. Derive selectors from Steps and Test_Data. Use get_by_role or get_by_label preferably.
4. For API test cases use page.request or Python requests.
5. For Security tests include the actual payloads from Test_Data.
6. For Performance tests wrap with time.time() and assert elapsed time.
7. For Concurrency tests use threading.Thread.
8. Each test starts with Preconditions as setup code.
9. Group tests by module using pytest classes.
10. Include fixtures and helpers at the top.

OUTPUT: A single Python file, no markdown fences, no explanation — just the code."""

_APPIUM_SYSTEM = """You are a senior Python QA automation engineer specialising in Appium.

Given a list of test cases (JSON array) and a target platform, generate a complete, immediately runnable pytest-Appium test suite.

RULES:
1. Every test must have REAL, executable Appium code — no pass, no TODO.
2. Use AppiumBy locators appropriate for the platform.
3. Include proper WebDriverWait and expected_conditions.
4. Group tests by module using pytest classes.

OUTPUT: A single Python file, no markdown fences, no explanation — just the code."""


def _llm_generate_script(system: str, user_content: str, cfg: dict) -> str | None:
    """Call the configured LLM to generate a script. Returns None on failure."""
    api_key = (
        cfg.get("anthropic_key") or cfg.get("gemini_key") or
        cfg.get("groq_key")     or cfg.get("openrouter_key") or
        os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GROQ_API_KEY") or
        os.environ.get("OPENROUTER_API_KEY") or os.environ.get("GEMINI_API_KEY")
    )
    if not api_key and cfg.get("provider", "") not in ("Ollama (local)", "Ollama"):
        return None

    import re
    result = llm_call(system, user_content, "", max_tokens=8000)
    if not result:
        return None
    result = re.sub(r"^```python\s*", "", result.strip())
    result = re.sub(r"^```\s*", "", result)
    result = re.sub(r"\s*```$", "", result)
    return result


def generate_playwright_script(tc_df: pd.DataFrame, cfg: dict = None) -> str:
    base_url = (cfg or {}).get("test_base_url", "http://localhost:3000")
    ts = datetime.datetime.now().isoformat()

    result = _llm_generate_script(
        _PLAYWRIGHT_SYSTEM,
        f"BASE_URL = '{base_url}'\n\nTest cases:\n{tc_df.to_json(orient='records', indent=2)}",
        cfg or {},
    )
    if result:
        return result

    # Structured stub when no LLM available
    lines = [
        f'"""',
        f"Playwright test suite — {ts}",
        f"Run: pytest test_suite.py --headed",
        f'"""',
        "", "import pytest", "import time", "import requests",
        "from playwright.sync_api import sync_playwright, Page, expect",
        "", f"BASE_URL = '{base_url}'", "DEFAULT_TIMEOUT = 10_000",
        "", "@pytest.fixture(scope='session')",
        "def browser():",
        "    with sync_playwright() as p:",
        "        b = p.chromium.launch(headless=True, slow_mo=50)",
        "        yield b", "        b.close()",
        "", "import pathlib",
        "@pytest.fixture",
        "def page(browser, request):",
        "    ctx = browser.new_context(viewport={'width': 1280, 'height': 720})",
        "    pg = ctx.new_page()",
        "    pg.set_default_timeout(DEFAULT_TIMEOUT)",
        "    yield pg",
        "    if request.node.rep_call.failed:",
        "        pathlib.Path('screenshots').mkdir(exist_ok=True)",
        "        tc_name = request.node.name",
        "        pg.screenshot(path=f'screenshots/{tc_name}_fail.png', full_page=True)",
        "    pg.close()", "    ctx.close()",
        "",
        "@pytest.hookimpl(tryfirst=True, hookwrapper=True)",
        "def pytest_runtest_makereport(item, call):",
        "    outcome = yield",
        "    rep = outcome.get_result()",
        "    setattr(item, 'rep_' + rep.when, rep)",
        "",
    ]
    modules = tc_df["Module"].unique() if "Module" in tc_df.columns else ["Tests"]
    for mod in modules:
        mod_df = tc_df[tc_df["Module"] == mod] if "Module" in tc_df.columns else tc_df
        class_name = "Test" + mod.replace(" ", "").replace("&", "And").replace("/", "")
        lines += [f"class {class_name}:", ""]
        for _, row in mod_df.iterrows():
            tc_id    = row.get("TC_ID", "TC_000").replace("-", "_").lower()
            scenario = row.get("Scenario", "").replace('"', '\\"')
            severity = row.get("Severity", "Medium")
            steps    = row.get("Steps", "")
            step_lines = [f"        # {s.strip()}" for s in str(steps).split("\n") if s.strip()]
            lines += [
                f"    @pytest.mark.{severity.lower()}",
                f"    def test_{tc_id}(self, page: Page):",
                f'        """{scenario}"""',
                *step_lines,
                f"        raise NotImplementedError('Configure an LLM provider to generate implementation')",
                "",
            ]
        lines.append("")
    return "\n".join(lines)


def generate_appium_script(tc_df: pd.DataFrame, platform: str, cfg: dict) -> str:
    ts       = datetime.datetime.now().isoformat()
    is_android = "Android" in platform
    pkg      = cfg.get("android_pkg", "com.example.app") if is_android else cfg.get("ios_bundle", "com.example.app")
    appium_url = cfg.get("appium_url", "http://localhost:4723")

    result = _llm_generate_script(
        _APPIUM_SYSTEM,
        f"Platform: {platform}\nApp: {pkg}\nAppium: {appium_url}\n\nTest cases:\n{tc_df.to_json(orient='records', indent=2)}",
        cfg,
    )
    if result:
        return result

    lines = [
        f'"""Appium test suite — {platform} — {ts}"""',
        "", "import pytest",
        "from appium import webdriver",
        "from appium.webdriver.common.appiumby import AppiumBy",
        "from selenium.webdriver.support.ui import WebDriverWait",
        "from selenium.webdriver.support import expected_conditions as EC",
        "", "DESIRED_CAPS = {",
        f"    'platformName': '{'Android' if is_android else 'iOS'}',",
        f"    'appPackage': '{pkg}',",
        f"    'automationName': '{'UIAutomator2' if is_android else 'XCUITest'}',",
        "    'newCommandTimeout': 300,", "}", f"APPIUM_URL = '{appium_url}'", "",
        "@pytest.fixture(scope='session')",
        "def driver():",
        "    from appium.options.android import UiAutomator2Options",
        "    opts = UiAutomator2Options().load_capabilities(DESIRED_CAPS)",
        f"    d = webdriver.Remote('{appium_url}', options=opts)",
        "    yield d", "    d.quit()", "",
    ]
    for _, row in tc_df.iterrows():
        tc_id    = row.get("TC_ID", "TC_000").replace("-", "_").lower()
        scenario = row.get("Scenario", "").replace('"', '\\"')
        severity = row.get("Severity", "Medium")
        lines += [
            f"@pytest.mark.{severity.lower()}",
            f"def test_{tc_id}(driver):",
            f'    """{scenario}"""',
            f"    raise NotImplementedError('Configure an LLM provider to generate implementation')",
            "",
        ]
    return "\n".join(lines)


def generate_github_actions_yaml(tc_df: pd.DataFrame) -> str:
    modules = tc_df["Module"].unique().tolist() if "Module" in tc_df.columns else []
    return f"""name: Req2Defect — Automated QA Pipeline

on:
  push:
    branches: [main, develop]
  pull_request:
    branches: [main]
  schedule:
    - cron: '0 2 * * *'

jobs:
  req2defect-qa:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        module: {json.dumps(modules)}

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          playwright install chromium

      - name: Run test suite — ${{{{ matrix.module }}}}
        env:
          BASE_URL: ${{{{ secrets.APP_BASE_URL }}}}
          GROQ_API_KEY: ${{{{ secrets.GROQ_API_KEY }}}}
        run: |
          pytest tests/test_suite.py \\
            -m "${{{{ matrix.module }}}}" \\
            --screenshot on \\
            --junit-xml=results/${{{{ matrix.module }}}}_results.xml \\
            -v

      - name: Upload test results
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: test-results-${{{{ matrix.module }}}}
          path: results/
"""
