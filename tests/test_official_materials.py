from __future__ import annotations
import json
from datetime import datetime, timezone

import pytest
from mingjing.official_materials import OfficialCollector, material_type, official_url
from mingjing.issuer_identity import same_issuer


@pytest.mark.parametrize('title,kind', [
    ('2022年年度报告', '年报'), ('2023年半年度报告', '半年报'),
    ('关于第一轮问询的回复', '问询回复'), ('关于第二轮问询的回复', '问询回复'),
    ('2022年年报更正公告', '更正公告'), ('招股说明书（注册稿）', '招股书'),
    ('2023年度审计报告及财务报表', '审计报告'), ('2023年年度报告摘要', None),
])
def test_collection_keeps_full_financial_disclosures(title, kind):
    assert material_type(title) == kind


@pytest.mark.parametrize('url', ['http://static.cninfo.com.cn/a.pdf',
    'https://static.cninfo.com.cn.evil.test/a.pdf', 'https://evil.test/a.pdf',
    'https://www.bse.cn/../a.pdf', 'https://user:password@www.bse.cn/a.pdf'])
def test_collection_rejects_unapproved_or_escaped_urls(url):
    with pytest.raises(ValueError):
        official_url(url)


def test_disclosure_date_is_source_record_date_and_respects_cutoff():
    record = {'announcementTime': int(datetime(2024,4,14,16,tzinfo=timezone.utc).timestamp()*1000),
              'announcementId': '1219624560', 'adjunctUrl': 'finalpage/2024-04-15/1219624560.PDF',
              'announcementTitle':'2023年年度报告', 'secCode':'839493'}
    assert OfficialCollector.cninfo_record(record, cutoff='2024-04-22')['date'] == '2024-04-15'
    with pytest.raises(ValueError, match='cutoff'):
        OfficialCollector.cninfo_record(record, cutoff='2024-04-14')
    with pytest.raises(ValueError, match='missing'):
        OfficialCollector.cninfo_record(dict(record, announcementTime=None), cutoff='2024-04-22')


def test_official_frozen_records_cannot_be_overwritten(tmp_path):
    collector = OfficialCollector(tmp_path)
    first = collector.freeze_bytes('records/official.json', b'{"disclosure":"original"}')
    assert collector.freeze_bytes('records/official.json', b'{"disclosure":"original"}') == first
    with pytest.raises(ValueError, match='overwrite'):
        collector.freeze_bytes('records/official.json', b'{"disclosure":"replacement"}')
    with pytest.raises(ValueError, match='escapes'):
        collector.freeze_bytes('../escape.json', b'{}')


def test_documented_code_change_does_not_make_similar_issuers_equal():
    assert same_issuer('839493', '920493')
    assert same_issuer('并行科技', '北京并行科技股份有限公司')
    assert not same_issuer('并行科技', '并行科技产业发展有限公司')
    assert not same_issuer('920493', '920494')
