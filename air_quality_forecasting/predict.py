"""Issue three forecasts from saved models without needing future labels."""
import argparse
import json
from pathlib import Path
import pickle
import pandas as pd

from src.data.ingestion import load_cached, load_config
from src.features.engineering import build_features, feature_names


def forecast(run_dir, history_dir, as_of=None):
    run_dir = Path(run_dir)
    cfg = load_config(run_dir / 'config_used.yaml')
    raw = load_cached(history_dir, cfg)
    times = pd.to_datetime(raw.timestamp, utc=True).dt.tz_convert(cfg['timezone'])
    if as_of is None:
        origin = times.groupby(raw.city).max().min()
    else:
        origin = pd.Timestamp(as_of)
        if origin.tzinfo is None:
            raise ValueError('--as-of must include a timezone offset')
        origin = origin.tz_convert(cfg['timezone'])
    if origin != origin.floor('h'):
        raise ValueError('Forecast origin must be aligned to a full hour')
    # Cut history BEFORE building features, so future records cannot affect inference.
    features = build_features(raw.loc[times <= origin], cfg)
    rows = features.loc[features.timestamp == origin].copy()
    if set(rows.city) != set(cfg['cities']) or rows[feature_names(cfg)].isna().any().any():
        raise ValueError('Missing origin observations or incomplete history; no silent fallback to an older hour')
    selection = json.loads((run_dir / 'selection.json').read_text())
    outputs = []
    for h in cfg['forecast']['horizons']:
        family = selection[str(h)]['winner']
        # Only load locally created/trusted artifacts: pickle can execute code.
        with open(run_dir / 'models' / f'{family}_{h}h.pkl', 'rb') as stream:
            model = pickle.load(stream)
        if model.training_end > origin:
            raise ValueError('Model training uses observations after the requested forecast origin')
        if model.horizon != h or model.family != family:
            raise ValueError('Saved model metadata does not match selection')
        predicted = model.predict(rows)
        outputs.append(pd.DataFrame({
            'city': rows.city.to_numpy(), 'forecast_origin': origin,
            'horizon_hours': h, 'target_time': origin + pd.Timedelta(hours=h),
            'model': family, 'predicted_pm25': predicted,
        }))
    return pd.concat(outputs, ignore_index=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--history-dir', type=Path, required=True)
    parser.add_argument('--as-of', help='Timezone-aware origin, e.g. 2024-12-30T08:00:00+07:00')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = forecast(args.run_dir, args.history_dir, args.as_of)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        result.to_csv(args.output, index=False, mode='x')
    print(result.to_string(index=False))
