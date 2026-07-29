## 1. Player Archetype Clustering (The Scout)

- **Small-Effort Version (MVP):** Instead of using years of historical tracking or deep advanced stats, pull a single season's standard per-100-possession box scores and filter only for players with greater than 500 minutes played.
    
- **The Execution:** Apply a basic k-means clustering algorithm (setting k=6 or 7 to represent common roles like Rim Protector, Floor General, 3-and-D). Use standard principal component analysis (PCA) to reduce the data into two dimensions just so you can easily visualize the distinct player clusters on a basic scatter plot.
    
- **Estimated Effort:** 3–5 hours.
    

## 2. Interactive Court Shot Charts (The Strategist)

- **Small-Effort Version (MVP):** Skip the nightmare of parsing raw 25-frames-per-second player tracking coordinates. Instead, focus entirely on **static shot location coordinates** (x,y), which are readily available and much cleaner.
    
- **The Execution:** Pick one high-profile player (e.g., Stephen Curry) or one specific team from a single season. Scrape their shot data—which includes whether the shot was made or missed and its exact coordinates on the floor. Use Python's `matplotlib` to draw a basic court outline and plot the shots as simple red (miss) and green (make) dots.
    
- **Estimated Effort:** 4–6 hours.
    

## 3. Historic Win Probability Explorer (The Engineer)

- **Small-Effort Version (MVP):** Real-time live streaming data pipelines are incredibly difficult to build. Instead, build a **post-game historical explorer** using a single iconic game (like Game 7 of the 2016 NBA Finals).
    
- **The Execution:** Download the play-by-play CSV file for that single game. Calculate a primitive win probability metric using a basic mathematical formula based purely on score differential and time remaining (e.g., Bill James' safe lead formula adapted for hoops). Plot this single line on a timeline graph from minute 0 to minute 48 to show the flow of the game.
    
- **Estimated Effort:** 2–4 hours.
    

## 🛠️ The "Small-Effort" Tech Stack

To keep your friction as low as possible for these small-scale builds, stick to these specific python libraries:

|Project|Key Library|What It Handles|
|---|---|---|
|**Data Pulling**|`nba_api`|Directly fetches structured stats and shot charts from NBA.com without manual web scraping.|
|**Data Cleaning**|`pandas`|Cleans up missing data and filters out bench players who mess up your models.|
|**Clustering**|`scikit-learn`|Runs your k-means and PCA in literally 3 lines of code.|
|**Visuals**|`matplotlib` / `seaborn`|Handles basic plotting, shot charts, and simple game flow timelines.|
