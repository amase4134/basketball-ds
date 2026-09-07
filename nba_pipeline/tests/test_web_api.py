from fastapi.testclient import TestClient

from nba_pipeline.web_api.app import app

client = TestClient(app)

def newest_season():
    return client.get('/api/v1/catalog').json()['seasons'][0]

def test_catalog_and_player_paging():
    catalog = client.get('/api/v1/catalog').json()
    season = catalog['seasons'][0]
    response = client.get('/api/v1/players', params={'season': season, 'page_size': 25})
    assert response.status_code == 200
    body = response.json()
    assert len(body['items']) <= 25
    assert body['meta']['total'] >= len(body['items'])

def test_catalog_publishes_selection_limits():
    limits = client.get('/api/v1/catalog').json()['limits']
    assert limits['max_selected_players'] >= 2
    assert limits['page_sizes'] == [25, 50, 100]

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

def test_column_ranges_filter_and_validate():
    season = newest_season()
    filtered = client.get('/api/v1/players', params={'season': season, 'min_stat': ['MIN:500'], 'max_stat': ['MIN:1500']})
    assert filtered.status_code == 200
    assert filtered.json()['items'], 'expected at least one row inside the requested minute band'
    assert all(500 <= row['MIN'] <= 1500 for row in filtered.json()['items'])
    unfiltered_total = client.get('/api/v1/players', params={'season': season}).json()['meta']['total']
    assert filtered.json()['meta']['total'] < unfiltered_total

def test_column_range_rejects_unknown_column_and_bad_values():
    season = newest_season()
    assert client.get('/api/v1/players', params={'season': season, 'min_stat': ['PLAYER_ID:1']}).status_code == 422
    assert client.get('/api/v1/players', params={'season': season, 'min_stat': ['PTS:abc']}).status_code == 422
    assert client.get('/api/v1/players', params={'season': season, 'min_stat': ['PTS:30'], 'max_stat': ['PTS:10']}).status_code == 422

def test_contains_filter_is_allow_listed():
    season = newest_season()
    matched = client.get('/api/v1/players', params={'season': season, 'contains': ['PLAYER_NAME:cur']})
    assert matched.status_code == 200
    assert all('cur' in row['PLAYER_NAME'].lower() for row in matched.json()['items'])
    assert client.get('/api/v1/players', params={'season': season, 'contains': ['PLAYER_ID:1']}).status_code == 422

def test_multi_player_selection_filters_rows():
    season = newest_season()
    everyone = client.get('/api/v1/players', params={'season': season, 'page_size': 25}).json()['items']
    ids = sorted({row['PLAYER_ID'] for row in everyone})[:3]
    scoped = client.get('/api/v1/players', params={'season': season, 'player_ids': ','.join(map(str, ids))}).json()
    assert scoped['meta']['filters']['player_ids'] == list(ids)
    assert {row['PLAYER_ID'] for row in scoped['items']} <= set(ids)

def test_selection_rejects_non_integer_and_oversized_lists():
    season = newest_season()
    assert client.get('/api/v1/players', params={'season': season, 'player_ids': '1;DROP TABLE'}).status_code == 422
    too_many = ','.join(str(n) for n in range(1, 100))
    assert client.get('/api/v1/players', params={'season': season, 'player_ids': too_many}).status_code == 422

def test_sort_direction_changes_ordering():
    season = newest_season()
    params = {'season': season, 'sort': 'PTS', 'page_size': 25}
    descending = client.get('/api/v1/players', params={**params, 'direction': 'desc'}).json()['items']
    ascending = client.get('/api/v1/players', params={**params, 'direction': 'asc'}).json()['items']
    assert [row['PTS'] for row in descending] == sorted((row['PTS'] for row in descending), reverse=True)
    assert [row['PTS'] for row in ascending] == sorted(row['PTS'] for row in ascending)
    assert descending[0]['PTS'] >= ascending[0]['PTS']

def test_compare_players_reports_leaders():
    season = newest_season()
    ranked = client.get('/api/v1/players', params={'season': season, 'sort': 'PTS', 'page_size': 25}).json()['items']
    ids = [row['PLAYER_ID'] for row in ranked[:3]]
    comparison = client.get('/api/v1/players/compare', params={'season': season, 'player_ids': ','.join(map(str, ids))})
    assert comparison.status_code == 200
    body = comparison.json()
    assert {row['PLAYER_ID'] for row in body['items']} <= set(ids)
    best_pts = max(row['PTS'] for row in body['items'] if row['PTS'] is not None)
    assert body['leaders']['PTS']['value'] == best_pts
    # Turnovers are the one metric where the smallest value wins.
    assert body['leaders']['TOV']['lower_is_better'] is True
    assert body['leaders']['TOV']['value'] == min(row['TOV'] for row in body['items'] if row['TOV'] is not None)

def test_compare_players_needs_at_least_two():
    season = newest_season()
    assert client.get('/api/v1/players/compare', params={'season': season, 'player_ids': '201939'}).status_code == 422

def test_player_lookup_resolves_ids_to_names():
    resolved = client.get('/api/v1/lookups/players', params={'ids': '201939,203954'})
    assert resolved.status_code == 200
    assert {item['PLAYER_ID'] for item in resolved.json()['items']} == {201939, 203954}
    assert client.get('/api/v1/lookups/players').status_code == 422

def test_multi_player_shot_map_returns_points_within_limit():
    season = newest_season()
    shooters = client.get('/api/v1/shots', params={'season': season, 'page_size': 25}).json()['items']
    ids = sorted({row['PLAYER_ID'] for row in shooters})[:2]
    selection = ','.join(map(str, ids))
    mapped = client.get('/api/v1/shots/map', params={'season': season, 'player_ids': selection}).json()
    assert mapped['player_ids'] == list(ids)
    assert mapped['returned'] == len(mapped['items'])
    if mapped['mode'] == 'points':
        assert mapped['total_matching'] <= mapped['grid']['point_limit']
        assert {row['PLAYER_ID'] for row in mapped['items']} <= set(ids)
    summary = client.get('/api/v1/shots/summary', params={'season': season, 'player_ids': selection}).json()
    assert summary['selected_player_count'] <= len(ids)
    assert summary['fga'] == mapped['total_matching']
