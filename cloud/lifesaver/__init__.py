"""Lifesaver (lsscloud.com) report-pull service — Phase 1.

See spec.md. This package is the standalone API service: it owns everything
about talking to lsscloud.com (auth, the 3-step SSRS ReportViewer scrape, CSV
parsing). The daily warehouse refresh (cloud.warehouse) uses the client; api.py
exposes it over a small HTTP API for local use.
"""

__all__ = ["config", "client", "parser", "models", "reports"]
