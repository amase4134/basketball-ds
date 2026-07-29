## Usage

cd "/Volumes/Data-700GB/Basketball DS/nba_pipeline"

source .venv/bin/activate

python -m nba_pipeline refresh --all          # full refresh

python -m nba_pipeline refresh                # current season only

python -m nba_pipeline refresh --season 2023-24 --dataset shot_charts --force 