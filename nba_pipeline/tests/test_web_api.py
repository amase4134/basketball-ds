import pytest
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
    # Derive the band from the data: the newest season may be only days old, so
    # any fixed minute range risks excluding every player and testing nothing.
    everyone = client.get('/api/v1/players', params={'season': season, 'page_size': 100, 'sort': 'MIN'}).json()
    minutes = sorted(row['MIN'] for row in everyone['items'] if row['MIN'] is not None)
    assert minutes, f'no players with minutes recorded for {season}'
    low, high = minutes[len(minutes) // 4], minutes[3 * len(minutes) // 4]

    filtered = client.get(
        '/api/v1/players',
        params={'season': season, 'min_stat': [f'MIN:{low}'], 'max_stat': [f'MIN:{high}']},
    )
    assert filtered.status_code == 200
    assert filtered.json()['items'], 'expected at least one row inside the requested minute band'
    assert all(low <= row['MIN'] <= high for row in filtered.json()['items'])
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

def test_catalog_publishes_the_prediction_season_window():
    catalog = client.get('/api/v1/catalog').json()
    assert catalog['target_season'] not in catalog['training_seasons']
    assert len(catalog['training_seasons']) == 5
    assert catalog['training_seasons'] == sorted(catalog['training_seasons'])
    assert 'team_season_stats' in catalog['datasets']
    assert 'win_predictions' in catalog['datasets']

def test_win_predictions_cover_every_team_and_reconcile():
    body = client.get('/api/v1/predictions/wins').json()
    assert len(body['items']) == 30
    assert body['meta']['games_per_season'] == 82
    # Every win is also a loss, so the projections must total half the games.
    expected_total = body['meta']['games_per_season'] * body['summary']['teams'] / 2
    assert sum(row['preseason_pred_wins'] for row in body['items']) == pytest.approx(expected_total)
    assert body['meta']['season'] == body['summary']['season']
    assert body['meta']['training_seasons'] == body['summary']['training']['training_seasons']

def test_win_prediction_rows_are_internally_consistent():
    body = client.get('/api/v1/predictions/wins').json()
    games_per_season = body['meta']['games_per_season']
    for row in body['items']:
        assert 0 <= row['preseason_pred_wins'] <= games_per_season
        assert 0 <= row['updated_pred_wins'] <= games_per_season
        assert row['actual_wins'] + row['actual_losses'] == pytest.approx(row['games_played'])
        if row['games_played'] > 0:
            # Wins already banked can never be projected away.
            assert row['updated_pred_wins'] >= row['actual_wins'] - 1e-6
            assert row['preseason_expected_wins_at_gp'] == pytest.approx(
                row['preseason_pred_wins'] / games_per_season * row['games_played']
            )
            assert row['preseason_win_error_at_gp'] == pytest.approx(
                row['actual_wins'] - row['preseason_expected_wins_at_gp']
            )

def test_win_prediction_summary_matches_the_season_state():
    body = client.get('/api/v1/predictions/wins').json()
    summary = body['summary']
    scored = ['mae_preseason_at_gp', 'mae_inseason_at_gp', 'spearman_rho', 'playoff_tier_accuracy']
    if summary['season_started']:
        assert all(summary[key] is not None for key in scored)
        assert summary['rmse_preseason_at_gp'] >= summary['mae_preseason_at_gp']
        assert -1 <= summary['spearman_rho'] <= 1
        assert len(summary['overperformers']) <= 5
    else:
        # Before opening night there are no results to score against.
        assert all(summary[key] is None for key in scored)
        assert summary['overperformers'] == [] and summary['underperformers'] == []
        assert summary['games_played_avg'] == 0

def test_win_prediction_training_accuracy_is_published():
    training = client.get('/api/v1/predictions/wins').json()['summary']['training']
    assert len(training['training_seasons']) == 5
    for name in ('preseason_model', 'profile_model'):
        block = training[name]
        assert block['rows'] > 0
        assert block['mae'] > 0
        assert block['rmse'] >= block['mae']
        assert block['alpha'] > 0
    # Reading a season as it happens beats forecasting it a year early.
    assert training['profile_model']['mae'] < training['preseason_model']['mae']

def test_win_prediction_sorting_is_allow_listed_and_ordered():
    descending = client.get('/api/v1/predictions/wins?sort=preseason_pred_wins&direction=desc').json()['items']
    ascending = client.get('/api/v1/predictions/wins?sort=preseason_pred_wins&direction=asc').json()['items']
    wins = [row['preseason_pred_wins'] for row in descending]
    assert wins == sorted(wins, reverse=True)
    assert [row['preseason_pred_wins'] for row in ascending] == sorted(wins)
    assert descending[0]['TEAM_ID'] == ascending[-1]['TEAM_ID']

def test_win_prediction_rejects_bad_sorts_seasons_and_directions():
    assert client.get('/api/v1/predictions/wins?sort=TEAM_ID;DROP+TABLE').status_code == 422
    assert client.get('/api/v1/predictions/wins?sort=season').status_code == 422
    assert client.get('/api/v1/predictions/wins?direction=sideways').status_code == 422
    assert client.get('/api/v1/predictions/wins?season=1999-00').status_code == 404

def test_win_prediction_history_returns_an_accuracy_timeline():
    body = client.get('/api/v1/predictions/wins/history').json()
    assert body['meta']['snapshots'] == len(body['items'])
    assert body['items'], 'expected at least one recorded snapshot'
    dates = [row['as_of_date'] for row in body['items']]
    assert dates == sorted(dates)
    assert len(dates) == len(set(dates)), 'one row per snapshot date'
    assert 'team' not in body

def test_win_prediction_history_can_follow_one_team():
    teams = client.get('/api/v1/predictions/wins').json()['items']
    team_id = teams[0]['TEAM_ID']
    body = client.get(f'/api/v1/predictions/wins/history?team_id={team_id}').json()
    assert body['team']['TEAM_ID'] == team_id
    assert body['team']['items']
    dates = [row['as_of_date'] for row in body['team']['items']]
    assert dates == sorted(dates)
    assert client.get('/api/v1/predictions/wins/history?team_id=999999').status_code == 404
    assert client.get('/api/v1/predictions/wins/history?season=1999-00').status_code == 404

@pytest.fixture(scope='module')
def in_season_client(tmp_path_factory):
    """An API over a synthetic warehouse 30 games into the tracked season.

    The real warehouse sits in the pre-season, so the in-season branches of the
    prediction endpoints would otherwise never be exercised.
    """
    from fastapi.testclient import TestClient as Client
    from nba_pipeline.modeling import team_wins, tracking
    from nba_pipeline.web_api.app import create_app
    from test_win_predictions import TARGET, build_warehouse

    cfg = build_warehouse(tmp_path_factory.mktemp('in_season'), target_games=30, target_seed=99)
    model, training, target = team_wins.train(TARGET, cfg=cfg)
    predictions = team_wins.predict(model, training, target)
    tracking.write_predictions(predictions, TARGET, cfg=cfg)
    tracking.write_metrics(model, cfg=cfg)
    for index, day in enumerate(('2026-12-04', '2026-12-11')):
        summary = tracking.summarise(predictions, model)
        summary['as_of_date'] = day
        summary['mae_preseason_at_gp'] = 4.0 - index
        tracking.append_snapshot(predictions, summary, TARGET, cfg=cfg)
    return Client(create_app(cfg)), TARGET

def test_in_season_predictions_report_live_accuracy(in_season_client):
    api, season = in_season_client
    body = api.get(f'/api/v1/predictions/wins?season={season}').json()
    summary = body['summary']
    assert summary['season_started'] is True
    assert summary['games_played_avg'] == 30
    assert summary['mae_preseason_at_gp'] is not None
    assert summary['rmse_preseason_at_gp'] >= summary['mae_preseason_at_gp']
    assert -1 <= summary['spearman_rho'] <= 1
    assert len(summary['overperformers']) == 5
    assert len(summary['underperformers']) == 5
    # Overperformers beat their expectation; underperformers trail it.
    assert summary['overperformers'][0]['preseason_win_error_at_gp'] >= 0
    assert summary['underperformers'][0]['preseason_win_error_at_gp'] <= 0
    # Reading the season as it happens beats the pre-season forecast.
    assert summary['mae_inseason_at_gp'] < summary['mae_preseason_at_gp']

def test_in_season_rows_blend_banked_wins_with_the_remainder(in_season_client):
    api, season = in_season_client
    body = api.get(f'/api/v1/predictions/wins?season={season}').json()
    for row in body['items']:
        assert row['games_played'] == 30
        assert row['profile_trust'] == pytest.approx(30 / 40)
        assert row['updated_pred_wins'] >= row['actual_wins'] - 1e-6
        assert row['current_82_pace'] == pytest.approx(row['actual_wins'] / 30 * 82)
        assert row['actual_rank'] is not None

def test_in_season_history_charts_improving_accuracy(in_season_client):
    api, season = in_season_client
    body = api.get(f'/api/v1/predictions/wins/history?season={season}').json()
    assert body['meta']['snapshots'] == 2
    errors = [row['mae_preseason_at_gp'] for row in body['items']]
    assert errors == [4.0, 3.0]

    team_id = api.get(f'/api/v1/predictions/wins?season={season}').json()['items'][0]['TEAM_ID']
    team = api.get(f'/api/v1/predictions/wins/history?season={season}&team_id={team_id}').json()['team']
    assert len(team['items']) == 2
    assert all(point['games_played'] == 30 for point in team['items'])

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
