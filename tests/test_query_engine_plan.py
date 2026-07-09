from src.ai.query_engine import QueryEngine, QueryFilters, QueryPlan


class FakeQueryEngine(QueryEngine):
    def __init__(self):
        self.llm = None
        self.model = "test-model"
        self.rf = None
        self._account_id = "test"
        self._cache = None
        self._query_graph = None

    def _call_llm(self, prompt: str, max_tokens: int = 500, system: str | None = None) -> str:
        return "测试回答"

    def _build_query_plan(self, question: str, context: dict | None = None) -> QueryPlan:
        return QueryPlan(
            route="stat",
            aggregation="count",
            filters=QueryFilters(statuses=["failed"]),
            confidence=0.9,
            reason="test plan",
        )


class FakeCache:
    def query_stats(self, **kwargs):
        self.last_kwargs = kwargs
        return {
            "total": 2,
            "by_status": {"done": 1, "failed": 1, "skipped": 0, "processing": 0, "pending": 0},
            "by_sender": [],
            "items": [
                {
                    "message_id": "m1",
                    "subject": "合同预算",
                    "from_addr": "alice@example.com",
                    "from_name": "张三",
                    "date": "2026-07-08T10:00:00",
                    "status": "done",
                    "has_attachment": True,
                },
                {
                    "message_id": "m2",
                    "subject": "项目进展",
                    "from_addr": "bob@example.com",
                    "from_name": "李四",
                    "date": "2026-07-07T10:00:00",
                    "status": "failed",
                    "has_attachment": False,
                },
            ],
            "matched_ids": ["m1", "m2"],
        }


def test_low_confidence_plan_is_repaired_by_precheck():
    engine = FakeQueryEngine()
    plan = QueryPlan(
        route="content",
        filters=QueryFilters(topic="近三天失败邮件多少封"),
        confidence=0.2,
        reason="uncertain",
    )

    repaired = engine._repair_plan_with_precheck("近三天失败邮件多少封", plan)

    assert repaired.route == "stat"
    assert repaired.aggregation == "count"


def test_topic_on_stat_upgrades_to_hybrid():
    engine = FakeQueryEngine()
    plan = QueryPlan(
        route="stat",
        aggregation="list",
        filters=QueryFilters(topic="合同"),
        confidence=0.9,
        reason="stat list",
    )

    repaired = engine._repair_plan_with_precheck("上周张三发的关于合同的邮件有哪些", plan)

    assert repaired.route == "hybrid"
    assert repaired.aggregation == "list"


def test_execute_stat_query_passes_multi_statuses_and_filters_sender_name():
    engine = FakeQueryEngine()
    cache = FakeCache()
    engine._cache = cache
    plan = QueryPlan(
        route="stat",
        aggregation="list",
        filters=QueryFilters(statuses=["done", "failed"], sender="张三"),
        confidence=0.9,
        reason="list by sender",
    )

    result = engine._execute_stat_query("张三成功和失败的邮件有哪些", plan, 0)

    assert cache.last_kwargs["statuses"] == ["done", "failed"]
    assert result["total_rows"] == 1
    assert result["rows"][0]["发件人"] == "张三"


def test_clarify_response_keeps_frontend_contract():
    engine = FakeQueryEngine()
    plan = QueryPlan(
        route="clarify",
        clarifying_question="你想查数量还是内容？",
        confidence=0.4,
        reason="ambiguous",
    )

    result = engine._clarify_response("查一下邮件", plan, 0)

    assert result["answer"] == "你想查数量还是内容？"
    assert result["trace"][0]["status"] == "warning"
    assert result["rows"] == []
    assert result["query_plan"]["route"] == "clarify"


def test_query_uses_langgraph_when_available_and_keeps_contract():
    engine = FakeQueryEngine()
    engine._cache = FakeCache()

    result = engine.query("失败邮件有多少")

    assert result["query_plan"]["route"] == "stat"
    assert result["rows"]
    assert result["error"] == ""


class FakeRAGFlow:
    def retrieve_chunks(self, query: str, top_k: int = 10):
        return [
            {"doc_name": "mail_m1.md", "content": "合同预算正文", "score": 0.9},
            {"doc_name": "mail_other.md", "content": "无关正文", "score": 0.8},
        ]


def test_hybrid_retrieval_filters_chunks_by_matched_message_id():
    engine = FakeQueryEngine()
    engine.rf = FakeRAGFlow()

    chunks = engine._retrieve_hybrid_chunks("合同", ["m1"])

    assert len(chunks) == 1
    assert chunks[0]["doc_name"] == "mail_m1.md"
