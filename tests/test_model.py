import pytest

from diting.model import Concept, EdgeFile, SchemaError, Vocabulary


def test_vocab_rejects_ambiguous_surface_forms():
    with pytest.raises(SchemaError, match="claimed by both"):
        Vocabulary.from_dict({
            "entities": [{"name": "会员", "synonyms": ["VIP"]}, {"name": "贵宾", "synonyms": ["vip"]}],
        })


def test_vocab_hypernym_must_exist_and_descendants():
    with pytest.raises(SchemaError, match="unknown hypernym"):
        Vocabulary.from_dict({"entities": [{"name": "金卡会员", "hypernym": "会员"}]})
    v = Vocabulary.from_dict({"entities": [
        {"name": "用户"}, {"name": "会员", "hypernym": "用户"}, {"name": "金卡会员", "hypernym": "会员"},
    ]})
    assert v.descendants("用户") == {"用户", "会员", "金卡会员"}
    assert v.descendants("金卡会员") == {"金卡会员"}


def test_predicate_class_validated():
    with pytest.raises(SchemaError, match="class must be one of"):
        Vocabulary.from_dict({"predicates": [{"name": "享受", "class": "magic"}]})


def test_concept_id_and_enums():
    with pytest.raises(SchemaError, match="dotted lower_snake"):
        Concept.from_dict({"id": "Rounding", "statement": "x"})
    with pytest.raises(SchemaError, match="lifecycle"):
        Concept.from_dict({"id": "a.b", "statement": "x", "lifecycle": "zombie"})


def test_concept_vocab_warnings():
    v = Vocabulary.from_dict({"contexts": ["交易"], "entities": [{"name": "会员"}], "predicates": [{"name": "享受"}]})
    c = Concept.from_dict({"id": "trade.x", "statement": "s", "context": "营销",
                           "subject": "会员", "predicate": "享受", "object": "折扣"})
    w = c.validate(v)
    assert any("context '营销'" in x for x in w)
    assert any("object '折扣'" in x for x in w)
    assert len(w) == 2


def test_concept_roundtrip():
    d = {"id": "trade.member_discount.rounding", "statement": "会员折扣四舍五入", "subject": "会员",
         "predicate": "享受", "object": "折扣", "modifiers": {"condition": "订单金额 >= 200"},
         "lifecycle": "active", "level": "critical", "verify": "property", "version": 2}
    assert Concept.from_dict(d).to_dict() == d


def test_edgefile_duplicate_occurrence():
    with pytest.raises(SchemaError, match="duplicate occurrence"):
        EdgeFile.from_dict({"concept": "a.b", "occurrences": [{"id": "x"}, {"id": "x"}]})
