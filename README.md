# GA-Capstone
This repository houses two QA frameworks designed to accelerate digital transformation, ensure regulatory compliance, and automate the software development lifecycle. By leveraging multi-agent AI architectures, these tools reduce manual oversight, enhance service quality, and streamline reporting for enterprise stakeholders.

# 1. ContentAuditor: Intelligent Service Governance

**Objective:** Ensure high-fidelity content delivery for public-facing digital services (Bahrain.bh).

Manual auditing of government service pages is traditionally a bottleneck, requiring **30–40 minutes** of manual review per service. **ContentAuditor** compresses this cycle to **under 60 seconds** by combining rigid rule-based checks with context-aware AI analysis.

---

## Demonstration & Knowledge Transfer

- **Watch Demo Video** — See the tool in action, auditing services in under 60 seconds.  

---

## Business Value

- **Operational Efficiency:** Achieves a ~95% reduction in audit time.  
- **Quality Assurance:** Eliminates human error in bilingual content, ensuring Arabic and English consistency.  
- **Regulatory Compliance:** Automatically flags missing links, incorrect fee structures, and vague requirements.  
- **Seamless Reporting:** Generates audit trails with actionable fixes and integrated screenshot evidence.  

---

## Core Capabilities

| Feature               | Business Benefit                                                                 |
|----------------------|----------------------------------------------------------------------------------|
| Hybrid Detection     | Combines rule-based logic (accuracy) with GenAI (context/nuance).               |
| Localization Audit   | Validates contradictory information between Arabic and English versions.        |
| Evidence Collection  | Automated screenshot capture and storage (e.g., Google Drive) for audit trails. |
| Interactive Review   | Allows human-in-the-loop (HITL) sign-off before final export.                   |

---

# 2. Req2Defect: Enterprise-Grade QA Pipeline

**Objective:** End-to-end automated testing, from requirements analysis to defect management.

**Req2Defect** moves beyond basic testing by integrating directly into the development ecosystem. It acts as an autonomous QA engineer, interpreting requirements, executing test plans, and managing the defect lifecycle within existing project management tools (Jira).

---

## Demonstration & Knowledge Transfer

- **Watch Demo Video** — Walkthrough of requirement analysis to automated Jira ticket creation.  
---

## Business Value

- **Faster Release Cycles:** Accelerates the path from requirements to QA sign-off.  
- **Integrated Workflow:** Automates the creation of Jira tickets, reducing administrative overhead for developers.  
- **Scalability & Reliability:** Built for production, utilizing Docker and CI/CD pipelines to ensure consistent test execution.  
- **Governance:** Provides full traceability and logging of token usage, test history, and system logs.  

---

## Key Operational Features

- **Jira Integration:** Automatically pushes high-fidelity defect reports into your existing Jira project for immediate triaging.  
- **Production Readiness:** Includes authentication (bcrypt), SQLite persistence for audit history, and environment configuration management.  
- **CI/CD Ready:** Fully containerized with Docker/Docker Compose and GitHub Actions integration.  
- **Fallback Logic:** Real-world testing via Playwright with automated fallback to simulation if the target application is unreachable.  

---

# Technical Overview & Deployment

Both systems are built on scalable, modular architectures designed to fit into modern enterprise infrastructures.

---

## Architecture Summary

- **Extensibility:** Multi-agent design allows for easy expansion of capabilities (e.g., adding new rule sets or test agents).  

- **Deployment Options:**
  - **Containerized:** Fully Dockerized for rapid, portable deployment in cloud or on-prem environments.  
  - **Cloud Agnostic:** Supports multiple LLM backends (Gemini, Claude, Groq, OpenRouter) to balance cost, performance, and vendor diversification.  

- **Maintainability:** Both systems support automated testing suites to ensure system stability during updates.  

