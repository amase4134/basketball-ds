## 1. The Scout: Player Evaluation & Clustering

**Objective:** Move beyond generic box-score stats by engineering a model that contextualizes _how_ a player impacts the game, rather than just how many points they score.

- **The Project:** Use unsupervised machine learning—like k-means clustering or t-SNE (t-Distributed Stochastic Neighbor Embedding)—on advanced box score data (e.g., usage rate, assist percentage, rim protection metrics) to build **custom role archetypes** instead of traditional positions (PG, SG, SF, PF, C).
    
- **Next-Level Feature Engineering:** Build a metric like _Context-Adjusted Points Over Expected_. Calculate a shot's expected value based on its distance and the shooter's history, then measure how a player performs against that baseline.
    
    Kalidrafts Basketball Analytics
    
- **Why Front Offices Care:** Modern NBA rosters are built on versatility and role alignment (e.g., "3-and-D wing", "Stretch 5", "Connector"). Showing you can programmatically identify these archetypes proves you think like a modern front-office scout.
    

## 2. The Strategist: Spatial Tracking & Shot Quality

**Objective:** Show that you can work with spatiotemporal (space and time) data, which is the gold standard in modern basketball operations.

- **The Project:** Using publicly available tracking datasets (like historical NBA tracking scraps on GitHub or NCAA tracking data), map out a **Shot Quality & Court Geometry Dashboard**.
    
- **The Technical Build:** Calculate a team's **Offensive Spacing Metric**. You can use a convex hull algorithm to determine the literal surface area (in ft2) occupied by the 5 offensive players on the floor at any millisecond, analyzing how space correlates with true shooting percentage (TS%).
    
- **Visualizing the Data:** Don't just make a basic scatter plot. Build interactive shot charts using `matplotlib` or `Plotly` that overlay kernel density estimations (KDE) to display shooting efficiency hot spots relative to league average.
    

## 3. The Engineer: Automated Game-State & Win-Probability

**Objective:** Prove you can build robust data pipelines (ETL) and production-grade applications, not just static Jupyter Notebooks.

Assessment.com

- **The Project:** Build an **In-Game Live Win Probability App**.
    
- **The Core Logic:** Train a supervised learning model (like XGBoost or a logistic regression baseline) using historical play-by-play data. Features should include score differential, time remaining, possession arrow, and pre-game Vegas point spreads.
    
- **The Deployment:** Build a lightweight interactive web application using **Streamlit** or **Shiny** where a user can move sliders for time and score to see the live win probability curve shift dynamically.
    
    Reddit
    

## 💡 Pro-Tips for Landing the Job

- **Host Everything Open Source:** Push your code to GitHub with clean, modular structures (separate your `/scrapers`, `/models`, and `/app` directories). Write a thorough `README.md` that acts as an executive summary.
    
- **The "Coach Test":** For every project, write a **one-page executive summary** written completely without data jargon. If a head coach can't read your project summary and immediately understand how to use it to win a game, the project isn't finished yet.
    
- **Use Public API Scrapers Responsibly:** Use Python libraries like `nba_api` to fetch your initial historical datasets, but make sure to cache your data locally into SQLite or parquet files so you aren't slamming the APIs during model iterations.