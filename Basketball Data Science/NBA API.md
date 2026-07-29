Use **`nba_api`**, an open-source Python client that connects directly to the endpoints of `stats.nba.com`. You don't have to write web scrapers; you just call functions that return clean `pandas` DataFrames.

Below is a complete, ready-to-run script that extracts shot coordinate data for **any player** (we'll use Stephen Curry as an example). This serves as the raw data pipeline for your **Shot Charts** project.

Python

```
# First, run this in your terminal: pip install nba_api pandas
from nba_api.stats.static import players
from nba_api.stats.endpoints import shotchartdetail
import pandas as pd

# Step 1: Look up the player's unique NBA ID
# Front offices use IDs, not names, because names can have typos or duplicates.
player_dict = players.find_players_by_full_name('Stephen Curry')
curry_id = player_dict[0]['id']

# Step 2: Request shot log data directly from the NBA API
# We must pass a TeamID (0 means all teams) and a Season (e.g., '2023-24')
shot_json = shotchartdetail.ShotChartDetail(
    team_id=0,
    player_id=curry_id,
    context_measure_simple='FGA',  # Field Goal Attempts (includes makes and misses)
    season_nullable='2023-24',
    season_type_all_star='Regular Season'
)

# Step 3: Extract the structured data into a pandas DataFrame
# The API returns multiple data tables; index [0] contains the actual shot data
df = shot_json.get_data_frames()[0]

# Keep only the columns needed for modeling or visual charts
columns_to_keep = ['PLAYER_NAME', 'EVENT_TYPE', 'SHOT_MADE_FLAG', 'SHOT_TYPE', 'SHOT_DISTANCE', 'LOC_X', 'LOC_Y']
df_clean = df[columns_to_keep]

# Preview your clean basketball data
print(df_clean.head())
```

### Understanding the Coordinates

When you run this data pipeline, look closely at the `LOC_X` and `LOC_Y` columns. This is the spatial data you need for modeling:

- **`LOC_X`**: The horizontal position on the court (left-to-right), ranging from `-250` to `250` (measured in tenths of a foot, mapping perfectly to a 50-foot-wide NBA court).
    
- **`LOC_Y`**: The vertical position from the baseline, ranging from `-50` (just behind the hoop) up to `400+` (past half court).

## 📂 Alternate "Zero-Code" Public Datasets

If you want to skip APIs entirely and start modeling with downloadable CSV files right now, bookmarks these three goldmines:

1. **Kaggle (NBA Data Hubs):** Search for "NBA Player Stats" or "NBA Play-by-Play". Thousands of users upload pre-cleaned CSV files of every single season's box scores spanning back to 1950.
    
2. **The `hoopR` Dataset (College Hoops):** If your goal is to work with men's or women's college basketball tracking and play-by-play data, the `hoopR` open data repository hosts full CSV season dumps.
    
3. **Big Data Ball:** While they sell premium tracking files, they offer free, downloadable sample CSV datasets of historical play-by-play logs that are perfect for building your first win-probability model.