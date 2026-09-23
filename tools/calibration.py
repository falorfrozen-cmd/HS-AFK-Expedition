"""Descriptive pace stability. Engineering thresholds, not confidence intervals.

Only complete 60-second region-clock windows are compared. Zero-kill windows
remain in the sample. This does not prove drop parity or future-hour accuracy.
"""
import math
import statistics

WINDOW_SECONDS = 60
MIN_PROFILE_SECONDS = 60
MIN_PROFILE_KILLS = 30
RECOMMENDED_SECONDS = 600
RECOMMENDED_KILLS = 200


def quality(records, room, usable_packets=None):
    elapsed = 0.0
    kills = 0
    bins = {}
    invalid = False
    for row in records:
        if row.get('kind') == 'context_invalid': invalid = True
        if row.get('room') != room: continue
        if row.get('kind') == 'farm_clock':
            seconds = row.get('seconds')
            if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 5:
                invalid = True
                continue
            elapsed += seconds
        elif row.get('kind') == 'kill' and row.get('packet') and (usable_packets is None or row['packet'] in usable_packets):
            bucket = int(elapsed // WINDOW_SECONDS)
            bins[bucket] = bins.get(bucket, 0) + 1
            kills += 1
    windows = int(elapsed // WINDOW_SECONDS)
    rates = [bins.get(i, 0) for i in range(windows)]
    mean = statistics.mean(rates) if rates else 0
    variation = statistics.pstdev(rates)/mean if len(rates)>1 and mean else None
    half = len(rates)//2
    first = statistics.mean(rates[:half]) if half else 0
    second = statistics.mean(rates[-half:]) if half else 0
    drift = abs(second-first)/mean if half and mean else None
    if invalid: status, message = 'invalid', 'Recording changed or the region timer is invalid. Record a new session.'
    elif elapsed < MIN_PROFILE_SECONDS or kills < MIN_PROFILE_KILLS: status, message = 'insufficient', 'Keep recording: at least 1 minute and 30 usable kills are required.'
    elif elapsed < RECOMMENDED_SECONDS or kills < RECOMMENDED_KILLS: status, message = 'short', 'Short sample. Additional normal routes are optional and can improve your pace estimate; reward records are checked when saving.'
    elif variation is None or drift is None or variation > .25 or drift > .20:
        status, message = 'variable', 'Pace varies across the recording. Repeat your normal route and check for long breaks or temporary bursts.'
    else: status, message = 'steady', 'Pace is steady within this recording. A separate validation run is still needed.'
    return dict(schema=1,status=status,message=message,seconds=elapsed,kills=kills,
                window_seconds=WINDOW_SECONDS,window_rates=rates,complete_windows=windows,
                rate=60*kills/elapsed if elapsed else 0,variation=variation,half_drift=drift,
                observed_min=min(rates) if rates else None,observed_max=max(rates) if rates else None,
                independently_validated=False,
                thresholds=dict(recommended_seconds=600,recommended_kills=200,max_variation=.25,max_half_drift=.20),
                note='Observed variation, not a confidence interval or an accuracy guarantee.')
