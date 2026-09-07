from fastapi.testclient import TestClient

from nba_pipeline.web_api.app import app

client = TestClient(app)

def test_catalog_and_player_paging():
    catalog = client.get('/api/v1/catalog').json()
    season = catalog['seasons'][0]
    response = client.get('/api/v1/players', params={'season': season, 'page_size': 25})
    assert response.status_code == 200
    body = response.json()
    assert len(body['items']) <= 25
    assert body['meta']['total'] >= len(body['items'])

def test_invalid_range_and_sort_are_rejected():
    assert client.get('/api/v1/players?season=2025-26&sort=sql').status_code == 422
    assert client.get('/api/v1/shots/summary?season=2025-26&min_distance=9&max_distance=2').status_code == 422

def test_shot_summary_math_and_safe_map():
    summary = client.get('/api/v1/shots/summary?season=2025-26').json()
    assert summary['fga'] == summary['total_matching']
    assert 0 <= summary['fg_pct'] <= 1
    mapped = client.get('/api/v1/shots/map?season=2025-26').json()
    assert mapped['mode'] == 'bins'
    assert mapped['returned'] == len(mapped['items'])
