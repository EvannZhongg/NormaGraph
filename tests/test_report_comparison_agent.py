from __future__ import annotations

from pathlib import Path
import sys
import unittest
import json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from adapters.llm_client import ResponseAPIError
from core.config import get_config
from services.report_comparison_agent import ReportComparisonAgentService, build_report_clause_assessment_prompt


class StubReportComparisonClient:
    def __init__(self, payloads: list[object]) -> None:
        self.payloads = payloads
        self.calls = 0
        self.enabled = True

    def create_structured_output(self, **kwargs) -> object:
        del kwargs
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        return payload


class StubViolatedSummaryTextFallbackClient:
    def __init__(self, text_summary: str) -> None:
        self.text_summary = text_summary
        self.structured_calls = 0
        self.text_calls = 0
        self.enabled = True

    def create_structured_output(self, **kwargs) -> dict:
        del kwargs
        self.structured_calls += 1
        if self.structured_calls == 1:
            return {
                "summary": "matched covered=0, violated=1",
                "matched_items": [
                    {
                        "clause_id": "sl258:2017:main:6.2.1",
                        "status": "violated",
                        "report_evidence": "报告明确表示未落实相关要求。",
                        "reason": "与条款要求冲突。",
                    }
                ],
                "explored_clause_ids": ["sl258:2017:main:6.2.1"],
            }
        raise ResponseAPIError("Responses API did not return valid JSON text.")

    def create_text_output(self, **kwargs) -> str:
        del kwargs
        self.text_calls += 1
        return self.text_summary


class StubViolatedSummaryImmediateFallbackRetryClient:
    def __init__(self) -> None:
        self.structured_calls = 0
        self.text_calls = 0
        self.enabled = True

    def create_structured_output(self, **kwargs) -> dict:
        del kwargs
        self.structured_calls += 1
        if self.structured_calls == 1:
            return {
                "summary": "matched covered=0, violated=1",
                "matched_items": [
                    {
                        "clause_id": "sl258:2017:main:6.2.1",
                        "status": "violated",
                        "report_evidence": "报告明确表示未落实相关要求。",
                        "reason": "与条款要求冲突。",
                    }
                ],
                "explored_clause_ids": ["sl258:2017:main:6.2.1"],
            }
        raise ResponseAPIError("Responses API did not return valid JSON text.")

    def create_text_output(self, **kwargs) -> str:
        del kwargs
        self.text_calls += 1
        if self.text_calls == 1:
            return ""
        return "报告指出存在明显违规缺口，相关要求未落实并带来运行风险。"


class ReportComparisonAgentTest(unittest.TestCase):
    def test_routing_accepts_list_payloads_from_llm(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        client = StubReportComparisonClient(
            [
                ["sl258:2017:chapter:6"],
                [{"section_id": "sl258:2017:section:6.2"}],
            ]
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.route_report_scope(
            report_scope={
                "scope_uid": "report:doc:section:6",
                "title": "5 运行管理评价",
                "text": "报告提到了管理机构、划界确权和监测资料整编。",
            },
            standard_id="sl258:2017",
            chapter_candidates=[
                {"id": "sl258:2017:chapter:6", "title": "运行管理评价", "ref": "6"},
                {"id": "sl258:2017:chapter:7", "title": "防洪能力复核", "ref": "7"},
            ],
            section_candidates=[
                {"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6", "title": "运行管理", "ref": "6.2"},
                {"id": "sl258:2017:section:7.2", "chapter_id": "sl258:2017:chapter:7", "title": "防洪标准", "ref": "7.2"},
            ],
        )

        self.assertEqual(result["chapter_ids"], ["sl258:2017:chapter:6"])
        self.assertEqual(result["section_ids"], ["sl258:2017:section:6.2"])
        self.assertEqual(client.calls, 2)

    def test_table_prompt_uses_html_only(self) -> None:
        prompt = build_report_clause_assessment_prompt(
            report_unit={
                "unitUid": "table-1",
                "unitType": "table",
                "title": "表 1.1-1 主要参数表",
                "html": "<table><tr><td>项目</td><td>数值</td></tr></table>",
                "text": "旧的 markdown 表格文本",
                "textNormalized": "旧的 markdown 表格文本",
            },
            chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clauses=[],
        )

        payload = json.loads(prompt)
        self.assertEqual(payload["report_unit"]["text"], "<table><tr><td>项目</td><td>数值</td></tr></table>")

    def test_discovery_accepts_coverage_status_alias_for_matched_clause(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        client = StubReportComparisonClient(
            [
                {
                    "matched_items": [
                        {
                            "clause_id": "sl258:2017:main:6.2.1",
                            "coverage_status": "covered",
                            "report_evidence": "报告提到了管理机构和人员不足。",
                            "reason": "明确覆盖。",
                        }
                    ],
                    "explored_clause_ids": ["sl258:2017:main:6.2.1"],
                }
            ]
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告提到了管理机构和人员不足。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["status"], "covered")
        self.assertEqual(result["matched_clauses"], result["items"])
        self.assertEqual(result["explored_clause_ids"], ["sl258:2017:main:6.2.1"])
        self.assertEqual(client.calls, 1)

    def test_discovery_drops_incompatible_status_without_retrying_unit(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        config.llm.batch_retry_backoff_seconds = 0.0
        client = StubReportComparisonClient(
            [
                {
                    "matched_items": [
                        {"clause_id": "sl258:2017:main:6.2.1", "status": "partial", "reason": "弱相关", "report_evidence": None},
                        {
                            "clause_id": "sl258:2017:main:6.2.1",
                            "status": "covered",
                            "report_evidence": "报告已覆盖。",
                            "reason": "覆盖。",
                        },
                    ],
                    "explored_clause_ids": ["sl258:2017:main:6.2.1"],
                },
            ]
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告已覆盖。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(result["items"][0]["status"], "covered")
        self.assertEqual(client.calls, 1)

    def test_violated_items_trigger_extra_defect_summary_call(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        client = StubReportComparisonClient(
            [
                {
                    "summary": "matched covered=0, violated=1",
                    "matched_items": [
                        {
                            "clause_id": "sl258:2017:main:6.2.1",
                            "status": "violated",
                            "report_evidence": "报告明确表示未落实相关要求。",
                            "reason": "与条款要求冲突。",
                        }
                    ],
                    "explored_clause_ids": ["sl258:2017:main:6.2.1"],
                },
                {
                    "summary": "报告文本显示相关管理要求未落实，存在明显违规缺口和运行风险。",
                },
            ]
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告明确表示未落实相关要求。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(result["items"][0]["status"], "violated")
        self.assertEqual(result["summary"], "报告文本显示相关管理要求未落实，存在明显违规缺口和运行风险。")
        self.assertEqual(client.calls, 2)

    def test_violated_summary_failure_falls_back_to_base_summary(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        client = StubReportComparisonClient(
            [
                {
                    "summary": "matched covered=0, violated=1",
                    "matched_items": [
                        {
                            "clause_id": "sl258:2017:main:6.2.1",
                            "status": "violated",
                            "report_evidence": "报告明确表示未落实相关要求。",
                            "reason": "与条款要求冲突。",
                        }
                    ],
                    "explored_clause_ids": ["sl258:2017:main:6.2.1"],
                },
                {
                    "not_summary": "bad-payload",
                },
            ]
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告明确表示未落实相关要求。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(result["summary"], "matched covered=0, violated=1")
        self.assertEqual(client.calls, 2)

    def test_violated_summary_accepts_plain_text_fallback_when_structured_parse_fails(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 0
        client = StubViolatedSummaryTextFallbackClient(
            "报告指出相关管理要求未落实，存在明显违规缺口和运行风险。"
        )
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告明确表示未落实相关要求。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(result["summary"], "报告指出相关管理要求未落实，存在明显违规缺口和运行风险。")
        self.assertEqual(client.structured_calls, 1)
        self.assertEqual(client.text_calls, 1)

    def test_violated_summary_uses_single_structured_attempt_then_retries_plain_text(self) -> None:
        config = get_config().model_copy(deep=True)
        config.llm.batch_max_retries = 2
        config.llm.batch_retry_backoff_seconds = 0.0
        client = StubViolatedSummaryImmediateFallbackRetryClient()
        service = ReportComparisonAgentService(config=config, client=client)

        result = service.assess_report_unit(
            report_unit={"unitUid": "u1", "text": "报告明确表示未落实相关要求。"},
            standard_id="sl258:2017",
            selected_chapters=[{"id": "sl258:2017:chapter:6", "title": "运行管理"}],
            selected_sections=[{"id": "sl258:2017:section:6.2", "chapter_id": "sl258:2017:chapter:6"}],
            clause_candidates=[
                {
                    "id": "sl258:2017:main:6.2.1",
                    "section_id": "sl258:2017:section:6.2",
                    "chapter_id": "sl258:2017:chapter:6",
                    "clause_ref": "6.2.1",
                    "label": "6.2.1",
                }
            ],
        )

        self.assertEqual(result["summary"], "报告指出存在明显违规缺口，相关要求未落实并带来运行风险。")
        self.assertEqual(client.structured_calls, 1)
        self.assertEqual(client.text_calls, 2)


if __name__ == "__main__":
    unittest.main()
