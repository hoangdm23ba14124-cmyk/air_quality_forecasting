"""Causal features at origin t, after origin-hour observations are available.
Missing hours stay missing; targets are never filled or interpolated.
"""
import numpy as np
import pandas as pd

METEO_FEATURES = [
    'temperature_2m', 'relative_humidity_2m', 'dew_point_2m',
    'surface_pressure', 'wind_speed_10m', 'precipitation', 'cloud_cover',
    'boundary_layer_height', 'shortwave_radiation',
]
DERIVED_FEATURES = [
    'pm25_current', 'sin_hour', 'cos_hour', 'sin_month', 'cos_month',
    'sin_dow', 'cos_dow', 'is_weekend', 'heat_humidity', 'wind_u',
    'wind_v', 'dewpoint_depression', 'pressure_delta',
]


def feature_names(cfg):
    """Explicit allowlist: new target columns can never enter X."""
    names = METEO_FEATURES + DERIVED_FEATURES
    names += [f'pm25_lag_{h}' for h in cfg['features']['lag_hours']]
    for w in cfg['features']['rolling_windows']:
        names += [f'pm25_roll_mean_{w}h', f'pm25_roll_std_{w}h']
    names += [f'city_{c}' for c in sorted(cfg['cities']) if c != 'hanoi']
    return names


def hourly_panel(raw, cfg):
    required = {'timestamp', 'city', 'pm2_5', 'wind_direction_10m', *METEO_FEATURES}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f'Missing input columns: {sorted(missing)}')
    df = raw.copy()
    if df['city'].isna().any():
        raise ValueError('City must not be missing')
    unknown = set(df['city']) - set(cfg['cities'])
    if unknown:
        raise ValueError(f'Unknown cities: {sorted(unknown)}')
    if any(pd.Timestamp(v).tzinfo is None for v in df['timestamp']):
        raise ValueError('Timestamps must include a timezone offset')
    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert(cfg['timezone'])
    if df.timestamp.isna().any() or (df.timestamp != df.timestamp.dt.floor('h')).any():
        raise ValueError('Timestamps must be valid hourly observations')
    if df.duplicated(['city', 'timestamp']).any():
        raise ValueError('Duplicate (city, timestamp) observations')
    numeric = ['pm2_5', 'wind_direction_10m', *METEO_FEATURES]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors='raise')
    if np.isinf(df[numeric].to_numpy()).any():
        raise ValueError('Infinite input values')
    if (df.pm2_5 < 0).any() or (df.wind_speed_10m < 0).any():
        raise ValueError('PM2.5 and wind speed must be nonnegative')
    frames = []
    for city, grp in df.groupby('city', sort=True):
        grp = grp.set_index('timestamp').sort_index()
        index = pd.date_range(grp.index.min(), grp.index.max(), freq='h', name='timestamp')
        grp = grp.reindex(index)
        grp['city'] = city
        frames.append(grp.reset_index())
    if not frames:
        raise ValueError('No observations supplied')
    return pd.concat(frames, ignore_index=True)


def build_features(raw, cfg):
    df = hourly_panel(raw, cfg)
    ts = df.timestamp
    for name, values, period in [('hour', ts.dt.hour, 24), ('month', ts.dt.month, 12),
                                 ('dow', ts.dt.dayofweek, 7)]:
        df[f'sin_{name}'] = np.sin(2 * np.pi * values / period)
        df[f'cos_{name}'] = np.cos(2 * np.pi * values / period)
    df['is_weekend'] = (ts.dt.dayofweek >= 5).astype(float)
    df['pm25_current'] = df.pm2_5
    df['heat_humidity'] = df.temperature_2m * df.relative_humidity_2m
    direction = np.deg2rad(df.wind_direction_10m)
    df['wind_u'] = -df.wind_speed_10m * np.sin(direction)
    df['wind_v'] = -df.wind_speed_10m * np.cos(direction)
    df['dewpoint_depression'] = df.temperature_2m - df.dew_point_2m
    df['pressure_delta'] = df.groupby('city').surface_pressure.diff()
    pm = df.groupby('city')['pm2_5']
    for h in cfg['features']['lag_hours']:
        df[f'pm25_lag_{h}'] = pm.shift(h)
    # y(t) is known at origin. Complete windows avoid silently skipping gaps.
    for w in cfg['features']['rolling_windows']:
        df[f'pm25_roll_mean_{w}h'] = pm.transform(lambda s: s.rolling(w, min_periods=w).mean())
        df[f'pm25_roll_std_{w}h'] = pm.transform(lambda s: s.rolling(w, min_periods=w).std(ddof=0))
    for city in sorted(cfg['cities']):
        if city != 'hanoi':
            df[f'city_{city}'] = (df.city == city).astype(float)
    return df.sort_values(['timestamp', 'city']).reset_index(drop=True)


def add_targets(features, horizons):
    df = features.sort_values(['city', 'timestamp']).copy()
    for h in horizons:
        df[f'target_{h}h'] = df.groupby('city').pm2_5.shift(-h)
    return df.sort_values(['timestamp', 'city']).reset_index(drop=True)
