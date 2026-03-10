"""Tests for company department definitions and classification."""

import pytest

from src.company.departments import (
    Department,
    TOOL_ALLOCATION,
    SHARED_TOOLS,
    classify_department,
    get_department_tools,
    get_department_display_name,
)


class TestClassifyDepartment:
    def test_finance_xauusd(self):
        assert classify_department("gia XAUUSD hom nay") == Department.FINANCE

    def test_finance_trading(self):
        assert classify_department("mo lenh buy gold") == Department.FINANCE

    def test_finance_mt5(self):
        assert classify_department("/mt5 account") == Department.FINANCE

    def test_finance_trade_command(self):
        assert classify_department("/trade start") == Department.FINANCE

    def test_security_scan(self):
        assert classify_department("scan vuln target.com") == Department.SECURITY

    def test_security_pentest(self):
        assert classify_department("pentest website nay") == Department.SECURITY

    def test_security_cve(self):
        assert classify_department("tim CVE cho Apache") == Department.SECURITY

    def test_security_bounty(self):
        assert classify_department("/bounty list") == Department.SECURITY

    def test_engineering_code(self):
        assert classify_department("debug function nay giup") == Department.ENGINEERING

    def test_engineering_git(self):
        assert classify_department("git commit va push") == Department.ENGINEERING

    def test_engineering_docker(self):
        assert classify_department("docker container dang chay") == Department.ENGINEERING

    def test_research_search(self):
        assert classify_department("search information about AI") == Department.RESEARCH

    def test_research_news(self):
        assert classify_department("tin tức mới nhất") == Department.RESEARCH

    def test_research_document(self):
        assert classify_department("summarize document nay") == Department.RESEARCH

    def test_operations_remind(self):
        assert classify_department("remind me at 5pm") == Department.OPERATIONS

    def test_operations_tts(self):
        assert classify_department("doc text nay thanh voice") == Department.OPERATIONS

    def test_general_greeting(self):
        assert classify_department("xin chao") == Department.GENERAL

    def test_general_chitchat(self):
        assert classify_department("ban khoe khong") == Department.GENERAL

    def test_general_ambiguous(self):
        assert classify_department("ok") == Department.GENERAL

    def test_general_empty(self):
        assert classify_department("") == Department.GENERAL

    def test_highest_score_wins(self):
        # Multiple finance keywords should still classify as FINANCE
        result = classify_department("mt5 XAUUSD buy order lot 0.1 sl tp")
        assert result == Department.FINANCE


class TestGetDepartmentTools:
    def test_finance_includes_mt5(self):
        tools = get_department_tools(Department.FINANCE)
        assert "mt5_price" in tools
        assert "trade_plan" in tools

    def test_finance_includes_shared(self):
        tools = get_department_tools(Department.FINANCE)
        assert "web_search" in tools

    def test_security_includes_recon(self):
        tools = get_department_tools(Department.SECURITY)
        assert "nuclei_scan" in tools
        assert "subdomain_enum" in tools

    def test_engineering_includes_git(self):
        tools = get_department_tools(Department.ENGINEERING)
        assert "git_status" in tools
        assert "ast_analyze" in tools

    def test_general_only_shared(self):
        tools = get_department_tools(Department.GENERAL)
        assert tools == SHARED_TOOLS

    def test_research_includes_web(self):
        tools = get_department_tools(Department.RESEARCH)
        assert "deep_search" in tools
        assert "browse_web" in tools


class TestGetDisplayName:
    def test_finance(self):
        assert "Tai chinh" in get_department_display_name(Department.FINANCE)

    def test_general(self):
        assert "CEO" in get_department_display_name(Department.GENERAL)

    def test_security(self):
        assert "An ninh" in get_department_display_name(Department.SECURITY)

    def test_all_departments_have_names(self):
        for dept in Department:
            name = get_department_display_name(dept)
            assert len(name) > 0


class TestToolAllocation:
    def test_no_overlap_between_departments(self):
        """No tool should be in 2+ departments (except shared)."""
        all_dept_tools: list[str] = []
        for dept, tools in TOOL_ALLOCATION.items():
            for tool in tools:
                if tool not in SHARED_TOOLS:
                    all_dept_tools.append(tool)
        # Check for duplicates
        assert len(all_dept_tools) == len(set(all_dept_tools))

    def test_finance_tool_count(self):
        assert len(TOOL_ALLOCATION[Department.FINANCE]) >= 20

    def test_security_tool_count(self):
        assert len(TOOL_ALLOCATION[Department.SECURITY]) >= 30
