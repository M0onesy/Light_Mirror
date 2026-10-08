from decimal import Decimal

from mingjing.compute import display_amount, qoq, ratio, tolerance, yoy
from mingjing.analysis import cash_content, gross_margin, non_recurring_adjustment
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, parse_number_claim
from mingjing.models import Fact


def test_decimal_and_units():
    value, unit = parse_number_claim("12.68亿元")
    assert value == Decimal("1268000000") and unit == "亿元"
    assert tolerance(2) == Decimal("0.005")


def test_yoy_and_zero_base():
    assert yoy(Decimal("120"), Decimal("100")) == Decimal("0.2")
    assert qoq(Decimal("10"), Decimal("0")) is None
    assert ratio(Decimal("2"), Decimal("4")) == Decimal("0.5")
    assert qoq(Decimal('10'), Decimal('-5')) is None


def test_amount_display_round_trip_including_million_unit():
    for unit in ('元', '万元', '亿元', '百万元', '万', '亿'):
        shown = display_amount(Decimal('-114165954'), unit, 2)
        parsed, parsed_unit = parse_number_claim(shown)
        from mingjing.mentions import unit_multiplier
        assert parsed_unit == unit
        assert abs(parsed-Decimal('-114165954')) <= unit_multiplier(unit)*Decimal('0.005')
    assert display_amount(Decimal('-114165954'), '百万元', 2) == '-114.17百万元'


def test_three_state_judgement():
    facts = FactStore([Fact("f1", "A", "NP_PARENT", "2024Y", value=Decimal("120000000"), decimals=2, evidence=("e1",), publish_date="2025-03-01"), Fact("f2", "A", "NP_PARENT", "2023Y", value=Decimal("100000000"), decimals=2, evidence=("e2",), publish_date="2025-03-01")])
    claims = extract_mentions("A公司2024年归属于母公司所有者的净利润1.2亿元，同比增长20.0%。", "A")
    results = [Judge(facts, "2025-03-31").verify(c) for c in claims]
    assert {r.status for r in results} == {"verified"}
    missing = extract_mentions("A公司2024年某分部收入50元。", "A")[0]
    assert Judge(facts).verify(missing).status == "abstained"
    assert extract_mentions("预计A公司2025年营业收入50元。", "A") == []


def test_an_amount_matching_an_old_version_is_not_proof_of_version_mismatch():
    facts = FactStore([
        Fact("latest", "A", "REVENUE", "2024Y", value=Decimal("110"), decimals=0, version="调整后", evidence=("new",), publish_date="2025-03-02"),
        Fact("old", "A", "REVENUE", "2024Y", value=Decimal("100"), decimals=0, version="原披露", evidence=("old",), publish_date="2025-03-01"),
    ])
    claim = extract_mentions("A公司2024年营业收入100元。", "A")[0]
    result = Judge(facts, "2025-03-31").verify(claim)
    assert result.status == "violated" and result.error_type == "E1"
    assert result.diagnostic_hypotheses[0]['error_type'] == 'E9'
    assert result.diagnostic_hypotheses[0]['proof'] == 'numeric_interval_compatibility_only'


def test_financial_analysis_uses_decimal():
    assert gross_margin(Decimal("100"), Decimal("60")) == Decimal("0.4")
    assert cash_content(Decimal("80"), Decimal("100")) == Decimal("0.8")
    assert non_recurring_adjustment(Decimal("130"), Decimal("20")) is None
    assert non_recurring_adjustment(Decimal("130"), Decimal("20"), Decimal("0"), Decimal("0")) == Decimal("110")


def test_mentions_filter_document_noise_and_keep_financial_context():
    text = "第1页\n证券代码：920493\n统一社会信用代码：91110108A12345678X\n2024年营业收入12.68亿元，同比增长20.0%。"
    claims = extract_mentions(text, "A")
    assert len(claims) == 2
    assert all(c.account == "REVENUE" for c in claims)
    assert {c.relation for c in claims} == {"value", "yoy"}
    assert not any("920493" in c.text or "社会信用" in c.text for c in claims)


def test_fact_store_deduplicates_and_selects_latest_version():
    facts = [
        Fact("old", "A", "REVENUE", "2024Y", value=Decimal("100"), publish_date="2025-03-01", version="原披露", evidence=("e",)),
        Fact("dup", "A", "REVENUE", "2024Y", value=Decimal("100"), publish_date="2025-03-01", version="原披露", evidence=("e",)),
        Fact("new", "A", "REVENUE", "2024Y", value=Decimal("110"), publish_date="2025-03-01", version="更正后", evidence=("n",)),
    ]
    store = FactStore(facts)
    assert len(store.facts) == 2
    assert store.query("A", "REVENUE", "2024Y").fact_id == "new"
