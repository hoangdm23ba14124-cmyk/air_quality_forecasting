"""Train direct +1/+3/+6h forecasts; select configurations without using test."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import pickle
import subprocess
import sys

import pandas as pd
import yaml

from src.data.ingestion import (ingest_all_cities, load_cached, load_config,
                                restrict_training_period, cache_metadata)
from src.data.preprocessing import prepare_splits
from src.features.engineering import add_targets, build_features, feature_names
from src.models.experiment import FAMILIES, ForecastModel, select_models
from src.evaluation.metrics import summarize
from src.visualization.plots import generate_all_figures

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config/config.yaml')
    parser.add_argument('--raw-dir', type=Path, help='CSV cache directory; defaults to config path')
    fetch = parser.add_mutually_exclusive_group()
    fetch.add_argument('--fetch', action='store_true', help='Download into the new run directory')
    fetch.add_argument('--skip-fetch', action='store_true', help='Use cached CSVs (default)')
    parser.add_argument('--run-dir', type=Path, help='New directory; existing paths are never overwritten')
    return parser.parse_args(argv)


def run(args):
    cfg = load_config(args.config)
    run_dir = (args.run_dir or ROOT / cfg['paths']['runs'] / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), logging.FileHandler(run_dir / 'run.log', encoding='utf-8')], force=True)
    raw_dir = (args.raw_dir or ROOT / cfg['paths']['raw_data']).resolve()
    if args.fetch:
        raw_dir = run_dir / 'raw'
        raw = ingest_all_cities(cfg, raw_dir)
    else:
        raw = load_cached(raw_dir, cfg)
    provenance = cache_metadata(raw_dir, cfg)
    if provenance is None:
        logger.warning('Cache imputation provenance is unknown. See README limitations.')
    raw = restrict_training_period(raw, cfg)
    cfg_path = run_dir / 'config_used.yaml'
    cfg_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding='utf-8')
    features = build_features(raw, cfg)
    dataset = add_targets(features, cfg['forecast']['horizons'])
    dev, test, folds, split = prepare_splits(dataset, cfg)
    logger.info('Split: %s', split)
    raw_stats = []
    missing_stats = []
    for city, group in features.groupby('city'):
        raw_stats.append({'city': city, 'first': str(group.timestamp.min()), 'last': str(group.timestamp.max()),
                          'hourly_rows': len(group), 'missing_pm25': int(group.pm2_5.isna().sum()),
                          'complete_features': int(group[feature_names(cfg)].notna().all(axis=1).sum())})
        for column in ['pm2_5', *feature_names(cfg)]:
            missing = group.loc[group[column].isna(), 'timestamp']
            if len(missing):
                missing_stats.append({'city': city, 'column': column, 'missing_hours': len(missing),
                                      'first_missing': str(missing.min()), 'last_missing': str(missing.max())})
    pd.DataFrame(missing_stats, columns=['city', 'column', 'missing_hours', 'first_missing', 'last_missing']).to_csv(
        run_dir / 'missing_data.csv', index=False)
    logger.info('Dropped %d/%d rows with incomplete features or future labels; details: missing_data.csv',
                split['dropped_rows'], len(dataset))
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
        dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, 'Git metadata unavailable'
    source_files = [ROOT / 'main.py', ROOT / 'predict.py', *sorted((ROOT / 'src').rglob('*.py'))]
    manifest = {
        'git_commit': commit, 'git_worktree_changes': dirty, 'python': sys.version,
        'versions': {p: importlib.metadata.version(p) for p in ['numpy', 'pandas', 'scikit-learn', 'matplotlib', 'PyYAML']},
        'source_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_files},
        'raw_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(raw_dir.glob('*_raw.csv'))},
        'data_source': 'api_snapshot_no_local_imputation' if provenance and provenance.get('local_imputation') is False else 'cache_unknown_imputation',
        'source_metadata': provenance,
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'raw_coverage': raw_stats, 'split': split, 'feature_names': feature_names(cfg),
        'forecast_contract': 'Direct PM2.5(t+h) using observations available through origin t; origin hour assumed available',
        'selection_metric': 'pooled_validation_rmse',
    }
    (run_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
    model_dir = run_dir / 'models'
    model_dir.mkdir()
    predictions, cv_rows, selections = [], [], {}
    # Selection only sees validation folds; held-out targets are used below for metrics.
    for h in cfg['forecast']['horizons']:
        logger.info('Selecting model configurations for +%dh', h)
        choices, scores, winner, records = select_models(folds, h, cfg)
        cv_rows.extend(records)
        selections[str(h)] = {'winner': winner, 'parameters': choices, 'validation_rmse': scores}
        logger.info('+%dh validation winner: %s; parameters: %s', h, winner, choices)
        for family in FAMILIES:
            model = ForecastModel(family, h, cfg, choices[family]).fit(dev)
            with open(model_dir / f'{family}_{h}h.pkl', 'wb') as stream:
                pickle.dump(model, stream)
            coefficients = model.coefficient_table()
            if coefficients is not None:
                coefficients.to_csv(model_dir / f'{family}_{h}h_coefficients.csv', index=False)
            rows = pd.DataFrame({
                'city': test.city.to_numpy(), 'forecast_origin': test.timestamp.to_numpy(),
                'target_time': (test.timestamp + pd.Timedelta(hours=h)).to_numpy(),
                'horizon_hours': h, 'model': family,
                'y_true': test[f'target_{h}h'].to_numpy(), 'y_pred': model.predict(test),
                'y_baseline': test.pm25_current.to_numpy(),
            })
            predictions.append(rows)
    predictions = pd.concat(predictions, ignore_index=True)
    metrics, city_metrics = summarize(predictions)
    predictions.to_csv(run_dir / 'predictions.csv', index=False)
    metrics.to_csv(run_dir / 'metrics.csv', index=False)
    city_metrics.to_csv(run_dir / 'city_metrics.csv', index=False)
    pd.DataFrame(cv_rows).to_csv(run_dir / 'validation_scores.csv', index=False)
    (run_dir / 'selection.json').write_text(json.dumps(selections, indent=2), encoding='utf-8')
    generate_all_figures(predictions, metrics, {h: v['winner'] for h, v in selections.items()},
                         run_dir / 'figures')
    logger.info('Completed. Outputs: %s', run_dir)
    print(metrics[['model', 'horizon_hours', 'rmse', 'mae', 'r2', 'improvement_pct']].to_string(index=False))
    return run_dir


if __name__ == '__main__':
    run(parse_args())
