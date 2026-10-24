"""Open-Meteo acquisition. Preserve missing observations, including targets."""
import logging
import hashlib
import json
from datetime import datetime, timezone
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import yaml

logger = logging.getLogger(__name__)
METEO_API = 'https://archive-api.open-meteo.com/v1/archive'
AQ_API = 'https://air-quality-api.open-meteo.com/v1/air-quality'
METEO_VARS = ['temperature_2m', 'relative_humidity_2m', 'dew_point_2m',
              'surface_pressure', 'wind_speed_10m', 'wind_direction_10m',
              'precipitation', 'cloud_cover', 'boundary_layer_height', 'shortwave_radiation']
AQ_VARS = ['pm2_5', 'pm10', 'nitrogen_dioxide', 'ozone']


def _get_json(url, params, retries=5, backoff=2.0):
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=60)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            if isinstance(exc, requests.HTTPError) and 400 <= response.status_code < 500 and response.status_code != 429:
                raise
            if attempt == retries - 1:
                raise
            wait = backoff * 2 ** attempt
            logger.warning('Request failed; retrying in %.1fs: %s', wait, exc)
            time.sleep(wait)


def _apply_time_backbone(df, start, end, tz='Asia/Ho_Chi_Minh'):
    """Date endpoints are inclusive whole local days. Never impute labels."""
    first = pd.Timestamp(start, tz=tz).normalize()
    stop = pd.Timestamp(end, tz=tz).normalize() + pd.Timedelta(days=1)
    if stop <= first:
        raise ValueError('end_date must be on or after start_date')
    if df.index.has_duplicates:
        raise ValueError('API returned duplicate hours')
    index = pd.date_range(first, stop, freq='h', inclusive='left', name='timestamp')
    return df.reindex(index)


def _parse_hourly_response(raw, tz):
    hourly = dict(raw['hourly'])
    times = pd.to_datetime(hourly.pop('time'))
    df = pd.DataFrame(hourly, index=times)
    df.index = df.index.tz_localize(tz)
    df.index.name = 'timestamp'
    return df


def fetch_city(city_key, cfg, save_dir=None):
    city = cfg['cities'][city_key]
    params = {'latitude': city['lat'], 'longitude': city['lon'],
              'start_date': cfg['data']['start_date'], 'end_date': cfg['data']['end_date'],
              'timezone': cfg['timezone']}
    meteo = _parse_hourly_response(_get_json(METEO_API, {
        **params, 'hourly': ','.join(METEO_VARS), 'wind_speed_unit': 'ms'}), cfg['timezone'])
    aq = _parse_hourly_response(_get_json(AQ_API, {
        **params, 'hourly': ','.join(AQ_VARS)}), cfg['timezone'])
    df = _apply_time_backbone(meteo.join(aq, how='outer'), params['start_date'],
                              params['end_date'], cfg['timezone'])
    df['city'] = city_key
    if save_dir is not None:
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        df.to_csv(Path(save_dir) / f'{city_key}_raw.csv')
    return df


def ingest_all_cities(cfg, save_dir=None):
    frames = [fetch_city(city, cfg, save_dir) for city in cfg['cities']]
    if save_dir is not None:
        write_cache_metadata(save_dir, cfg)
    return pd.concat(frames).reset_index().sort_values(['timestamp', 'city']).reset_index(drop=True)


def write_cache_metadata(raw_dir, cfg):
    raw_dir = Path(raw_dir)
    metadata = {
        'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
        'source': 'Open-Meteo historical weather and CAMS air quality',
        'meteo_api': METEO_API, 'air_quality_api': AQ_API,
        'local_imputation': False, 'timezone': cfg['timezone'],
        'start_date': cfg['data']['start_date'], 'end_date': cfg['data']['end_date'],
        'cities': cfg['cities'],
        'sha256': {f'{city}_raw.csv': hashlib.sha256((raw_dir / f'{city}_raw.csv').read_bytes()).hexdigest()
                   for city in cfg['cities']},
    }
    (raw_dir / 'source.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')


def cache_metadata(raw_dir, cfg):
    """Identify this project's unchanged API snapshots; unknown caches stay unknown."""
    path = Path(raw_dir) / 'source.json'
    if not path.exists():
        return None
    metadata = json.loads(path.read_text(encoding='utf-8'))
    for city in cfg['cities']:
        name = f'{city}_raw.csv'
        actual = hashlib.sha256((Path(raw_dir) / name).read_bytes()).hexdigest()
        if metadata.get('sha256', {}).get(name) != actual:
            raise ValueError(f'Cache hash mismatch: {name}; source metadata no longer matches data')
    return metadata


def load_cached(raw_dir, cfg):
    frames = []
    for city in cfg['cities']:
        path = Path(raw_dir) / f'{city}_raw.csv'
        df = pd.read_csv(path)
        if set(df['city']) != {city}:
            raise ValueError(f'Wrong city labels in {path}')
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def restrict_training_period(raw, cfg):
    """Honor inclusive local date bounds without restricting inference history."""
    times = pd.to_datetime(raw['timestamp'], utc=True).dt.tz_convert(cfg['timezone'])
    start = pd.Timestamp(cfg['data']['start_date'], tz=cfg['timezone']).normalize()
    stop = pd.Timestamp(cfg['data']['end_date'], tz=cfg['timezone']).normalize() + pd.Timedelta(days=1)
    selected = raw.loc[(times >= start) & (times < stop)].copy()
    missing = set(cfg['cities']) - set(selected.city)
    if missing:
        raise ValueError(f'No training observations in requested dates for cities: {sorted(missing)}')
    return selected


def validate_config(cfg):
    ratios = [cfg['data'][f'{s}_ratio'] for s in ('train', 'val', 'test')]
    if not all(0 < r < 1 for r in ratios) or not np.isclose(sum(ratios), 1):
        raise ValueError('Split ratios must be positive and sum to 1')
    for key, values, minimum in [
        ('horizons', cfg['forecast']['horizons'], 1),
        ('lag_hours', cfg['features']['lag_hours'], 1),
        ('rolling_windows', cfg['features']['rolling_windows'], 2),
        ('degrees', cfg['model']['polynomial_degrees'], 2),
    ]:
        if not values or len(set(values)) != len(values) or any(type(v) is not int or v < minimum for v in values):
            raise ValueError(f'Invalid {key}')
    poly = cfg['model']['polynomial_features']
    if not poly or len(set(poly)) != len(poly):
        raise ValueError('Polynomial features must be a nonempty list without duplicates')
    if type(cfg['validation']['n_splits']) is not int or cfg['validation']['n_splits'] < 1:
        raise ValueError('n_splits must be a positive integer')
    if not cfg['cities'] or 'hanoi' not in cfg['cities']:
        raise ValueError('Configured cities must include reference city hanoi')
    if pd.Timestamp(cfg['data']['end_date']) < pd.Timestamp(cfg['data']['start_date']):
        raise ValueError('end_date precedes start_date')
    return cfg


def load_config(path='config/config.yaml'):
    with open(path, encoding='utf-8') as stream:
        return validate_config(yaml.safe_load(stream))
