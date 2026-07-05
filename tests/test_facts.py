"""Tests for the productised memory-intelligence layer (facts + remember/recall)."""

from memdio.core.facts import classify_query, extract_facts


def test_classify_query():
    assert classify_query("How many model kits have I bought?") == "aggregation"
    assert classify_query("What is the total weight of feed?") == "aggregation"
    assert classify_query("When did I start my new job?") == "temporal"
    assert classify_query("What is the order of airlines earliest to latest?") == "temporal"
    assert classify_query("What is my favorite color?") == "detail"
    # "how long" is a duration, not counting
    assert classify_query("How long have I lived here?") == "detail"


def test_extract_facts_parses_and_cleans():
    def fake_llm(_prompt):
        return "1. The user bought a Tamiya Spitfire kit.\n- The user owns 3 tanks.\nNONE\n\n"
    facts = extract_facts(fake_llm, "some text", "2023-05-20")
    assert facts == ["The user bought a Tamiya Spitfire kit.", "The user owns 3 tanks."]


def test_extract_facts_swallows_llm_errors():
    def broken_llm(_prompt):
        raise RuntimeError("api down")
    assert extract_facts(broken_llm, "text") == []


def test_remember_stores_raw_and_facts(storage):
    def fake_llm(_prompt):
        return "The user owns a red bike.\nThe user commutes 5 miles daily."
    mem_id = storage.remember("I ride my red bike 5 miles to work.", llm=fake_llm)
    # raw memory retrievable
    assert "red bike" in storage.retrieve(mem_id)
    # facts stored and tagged
    tagged = storage.search_by_tag("fact")
    contents = [t["content"] for t in tagged]
    assert any("red bike" in c for c in contents)
    assert any("5 miles" in c for c in contents)


def test_remember_without_llm_is_plain_store(storage):
    mem_id = storage.remember("just a note")
    assert storage.retrieve(mem_id) == "just a note"
    assert storage.search_by_tag("fact") == []


def test_recall_returns_relevant_memories(storage):
    storage.store("Alice lives in Boston and loves hiking.", tags="session")
    storage.store("Bob is a chef in Paris.", tags="session")
    results = storage.recall("Where does Alice live?", top_k=5)
    assert any("Boston" in r["content"] for r in results)


def test_recall_aggregation_prefers_facts(storage):
    # A fact-tagged memory and a raw session; aggregation routing should surface facts.
    storage.store("The user bought a 20-pound bag of feed.", tags="fact")
    storage.store("Long raw conversation about chickens and feed and life.", tags="session")
    results = storage.recall("How many bags of feed did I buy?", top_k=5, route=True)
    assert results  # returns something
    # every returned memory in aggregation mode is a fact
    assert all((r.get("tags") or "") == "fact" for r in results)
