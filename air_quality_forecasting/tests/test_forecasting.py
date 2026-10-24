"""Regression tests for leakage, time alignment, model math and CLI workflow."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import numpy as np
import pandas as pd
import yaml

from main import ROOT, parse_args, run
from predict import forecast
from src.data.ingestion import (_apply_time_backbone, _parse_hourly_response,
                                load_config, validate_config, restrict_training_period,
                                write_cache_metadata, cache_metadata)
from src.data.preprocessing import prepare_splits
from src.features.engineering import METEO_FEATURES, add_targets, build_features, feature_names
from src.models.experiment import FAMILIES, ForecastModel, select_models
from src.evaluation.metrics import metric_row


def config():
    cfg = load_config(ROOT / 'config/config.yaml')
    cfg['cities'] = {k: cfg['cities'][k] for k in ['hanoi', 'haiphong']}
    cfg['features'] = {'lag_hours': [1, 3, 6], 'rolling_windows': [3, 6]}
    cfg['validation']['n_splits'] = 2
    cfg['model']['polynomial_degrees'] = [2]
    return cfg


def panel(n=300):
    rng = np.random.default_rng(9)
    frames = []
    for city, offset in [('hanoi', 0), ('haiphong', 100)]:
        df = pd.DataFrame({'timestamp': pd.date_range('2024-01-01', periods=n, freq='h', tz='Asia/Ho_Chi_Minh'),
                           'city': city, 'pm2_5': offset + np.arange(n) * .1 + 10})
        for col in METEO_FEATURES:
            df[col] = rng.uniform(1, 20, n)
        df['wind_direction_10m'] = rng.uniform(0, 360, n)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


class DataTests(unittest.TestCase):
    def setUp(self):
        self.cfg, self.raw = config(), panel()

    def test_targets_exact_time_and_city(self):
        f = add_targets(build_features(self.raw, self.cfg), [1, 3, 6])
        for city, group in f.groupby('city'):
            for h in [1, 3, 6]:
                np.testing.assert_allclose(group[f'target_{h}h'].iloc[:-h], group.pm2_5.iloc[h:])
                self.assertTrue(group[f'target_{h}h'].iloc[-h:].isna().all())
        self.assertFalse(any(c.startswith('target_') for c in feature_names(self.cfg)))

    def test_future_mutation_cannot_change_past_features(self):
        cutoff = self.raw.timestamp.min() + pd.Timedelta(hours=150)
        before = build_features(self.raw, self.cfg)
        changed = self.raw.copy()
        changed.loc[changed.timestamp > cutoff, ['pm2_5', *METEO_FEATURES]] *= 20
        after = build_features(changed, self.cfg)
        cols = ['timestamp', 'city', *feature_names(self.cfg)]
        pd.testing.assert_frame_equal(before.loc[before.timestamp <= cutoff, cols], after.loc[after.timestamp <= cutoff, cols])

    def test_rolling_never_crosses_city(self):
        f = build_features(self.raw, self.cfg)
        for city, group in f.groupby('city'):
            actual = group.pm25_roll_mean_3h
            expected = group.pm2_5.rolling(3, min_periods=3).mean()
            np.testing.assert_allclose(actual, expected, equal_nan=True)
            self.assertTrue(actual.iloc[:2].isna().all())

    def test_missing_hour_is_not_next_row_and_target_not_imputed(self):
        missing_time = self.raw.timestamp.min() + pd.Timedelta(hours=100)
        raw = self.raw.loc[~((self.raw.city == 'hanoi') & (self.raw.timestamp == missing_time))]
        f = add_targets(build_features(raw, self.cfg), [1, 3, 6])
        row = f.loc[(f.city == 'hanoi') & (f.timestamp == missing_time)]
        self.assertTrue(row.pm2_5.isna().all())
        previous = f.loc[(f.city == 'hanoi') & (f.timestamp == missing_time - pd.Timedelta(hours=1))]
        self.assertTrue(previous.target_1h.isna().all())
        later = f.loc[(f.city == 'hanoi') & (f.timestamp == missing_time + pd.Timedelta(hours=1))]
        self.assertTrue(later.pm25_lag_1.isna().all())

    def test_duplicates_and_naive_timestamps_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            build_features(pd.concat([self.raw, self.raw.iloc[:1]]), self.cfg)
        bad = self.raw.copy()
        bad['timestamp'] = bad.timestamp.dt.tz_localize(None)
        with self.assertRaisesRegex(ValueError, 'timezone'):
            build_features(bad, self.cfg)

    def test_zero_wind_stays_finite_without_inverse_feature(self):
        self.raw['wind_speed_10m'] = 0
        f = build_features(self.raw, self.cfg)
        self.assertNotIn('stagnation', feature_names(self.cfg))
        self.assertTrue(np.isfinite(f[feature_names(self.cfg)].dropna().to_numpy()).all())

    def test_split_purges_labels_and_preserves_city(self):
        f = add_targets(build_features(self.raw, self.cfg), [1, 3, 6])
        dev, test, folds, meta = prepare_splits(f, self.cfg)
        self.assertLess((dev.timestamp + pd.Timedelta(hours=6)).max(), test.timestamp.min())
        self.assertTrue(set(dev.timestamp).isdisjoint(test.timestamp))
        for train, val in folds:
            self.assertLess((train.timestamp + pd.Timedelta(hours=6)).max(), val.timestamp.min())
            self.assertLess((val.timestamp + pd.Timedelta(hours=6)).max(), test.timestamp.min())
        for rows in [dev, test]:
            expected = self.raw.set_index(['timestamp', 'city']).pm2_5
            np.testing.assert_allclose(rows.pm2_5, expected.loc[pd.MultiIndex.from_frame(rows[['timestamp', 'city']])])
        self.assertEqual(len(test), meta['test_rows'])

    def test_end_date_includes_whole_day_and_gaps_remain(self):
        d = self.raw.loc[self.raw.city == 'hanoi'].set_index('timestamp')[['pm2_5']]
        d = d.drop(d.index[1])
        result = _apply_time_backbone(d, '2024-01-01', '2024-01-02')
        self.assertEqual(len(result), 48)
        self.assertEqual(result.index[-1].hour, 23)
        self.assertTrue(pd.isna(result.iloc[1, 0]))

    def test_response_parser_does_not_mutate_json(self):
        raw = {'hourly': {'time': ['2024-01-01T00:00'], 'pm2_5': [None]}}
        before = copy.deepcopy(raw)
        self.assertTrue(_parse_hourly_response(raw, 'Asia/Ho_Chi_Minh').pm2_5.isna().all())
        self.assertEqual(raw, before)

    def test_invalid_config(self):
        self.cfg['forecast']['horizons'] = [0, 1]
        with self.assertRaises(ValueError):
            validate_config(self.cfg)

    def test_training_dates_filter_local_days_without_mutating_history(self):
        self.cfg['data'].update(start_date='2024-01-03', end_date='2024-01-04')
        selected = restrict_training_period(self.raw, self.cfg)
        self.assertEqual(len(selected), 48 * 2)
        self.assertEqual(selected.timestamp.min(), pd.Timestamp('2024-01-03', tz='Asia/Ho_Chi_Minh'))
        self.assertEqual(selected.timestamp.max(), pd.Timestamp('2024-01-04 23:00', tz='Asia/Ho_Chi_Minh'))
        self.assertEqual(len(self.raw), 600)
        self.cfg['data'].update(start_date='2030-01-01', end_date='2030-01-02')
        with self.assertRaisesRegex(ValueError, 'No training observations'):
            restrict_training_period(self.raw, self.cfg)

    def test_cached_snapshot_hash_detects_modified_data(self):
        with tempfile.TemporaryDirectory() as directory:
            for city, frame in self.raw.groupby('city'):
                frame.to_csv(Path(directory) / f'{city}_raw.csv', index=False)
            self.assertIsNone(cache_metadata(directory, self.cfg))
            write_cache_metadata(directory, self.cfg)
            self.assertIs(cache_metadata(directory, self.cfg)['local_imputation'], False)
            with open(Path(directory) / 'hanoi_raw.csv', 'a') as stream:
                stream.write('\n')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                cache_metadata(directory, self.cfg)


class ModelTests(unittest.TestCase):
    def test_scaler_fit_only_on_training_and_predict_needs_no_targets(self):
        cfg = config()
        f = add_targets(build_features(panel(), cfg), [1, 3, 6])
        dev, test, _, _ = prepare_splits(f, cfg)
        model = ForecastModel('multiple_linear', 6, cfg).fit(dev)
        np.testing.assert_allclose(model.scaler.mean_, dev[model.feature_names].mean())
        prediction = model.predict(test.drop(columns=['target_1h', 'target_3h', 'target_6h']))
        self.assertEqual(len(prediction), len(test))

    def test_selection_is_repeatable(self):
        cfg = config()
        f = add_targets(build_features(panel(), cfg), [1, 3, 6])
        _, _, folds, _ = prepare_splits(f, cfg)
        a, scores, winner, _ = select_models(folds, 1, cfg)
        b, scores2, winner2, _ = select_models(folds, 1, cfg)
        self.assertEqual(a, b)
        self.assertEqual(scores, scores2)
        self.assertEqual(winner, winner2)

    def test_regression_metrics_and_degenerate_targets(self):
        df = pd.DataFrame({'y_true': [1., 3.], 'y_pred': [2., 2.], 'y_baseline': [1., 3.]})
        row = metric_row(df)
        self.assertEqual(row['rmse'], 1.)
        self.assertEqual(row['mae'], 1.)
        self.assertEqual(row['r2'], 0.)
        self.assertTrue(np.isnan(row['improvement_pct']))
        df['y_true'] = 1.
        self.assertTrue(np.isnan(metric_row(df)['r2']))

    def test_only_assignment_models_are_available(self):
        self.assertEqual(FAMILIES, ['simple_linear', 'multiple_linear', 'polynomial'])
        with self.assertRaisesRegex(ValueError, 'Unknown model'):
            ForecastModel('unsupported', 1, config())


class EndToEndTests(unittest.TestCase):
    def test_run_save_reload_predict_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw_dir = root / 'raw'
            raw_dir.mkdir()
            raw = panel()
            for city, frame in raw.groupby('city'):
                frame.to_csv(raw_dir / f'{city}_raw.csv', index=False)
            cfg_path = root / 'config.yaml'
            cfg_path.write_text(yaml.safe_dump(config()))
            args = parse_args(['--config', str(cfg_path), '--raw-dir', str(raw_dir),
                               '--run-dir', str(root / 'run')])
            run_dir = run(args)
            metrics = pd.read_csv(run_dir / 'metrics.csv')
            self.assertEqual(len(metrics), 9)
            self.assertEqual(set(metrics.model), set(FAMILIES))
            self.assertEqual(metrics.groupby('horizon_hours')['n'].nunique().tolist(), [1, 1, 1])
            predictions = pd.read_csv(run_dir / 'predictions.csv')
            observed = raw.set_index(['timestamp', 'city']).pm2_5
            keys = pd.MultiIndex.from_arrays([pd.to_datetime(predictions.target_time), predictions.city])
            np.testing.assert_allclose(predictions.y_true, observed.loc[keys])
            latest = forecast(run_dir, raw_dir)
            self.assertEqual(len(latest), 6)
            self.assertEqual(set(latest.horizon_hours), {1, 3, 6})
            self.assertTrue((latest.target_time > raw.timestamp.max()).all())
            self.assertEqual(len(list((run_dir / 'models').glob('*.pkl'))), 9)
            with self.assertRaises(FileExistsError):
                run(args)
            with self.assertRaisesRegex(ValueError, 'training uses'):
                forecast(run_dir, raw_dir, raw.timestamp.iloc[100].isoformat())
            with self.assertRaisesRegex(ValueError, 'Missing origin'):
                forecast(run_dir, raw_dir, (raw.timestamp.max() + pd.Timedelta(hours=1)).isoformat())
            self.assertTrue((run_dir / 'figures/rmse_by_horizon.png').exists())


if __name__ == '__main__':
    unittest.main()
