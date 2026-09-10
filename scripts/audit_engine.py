#!/usr/bin/env python3
"""Read-only engine evidence audit. Never starts the scanner, syncs state or sends alerts."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'models'))
from audit.score_model import CalibratedScoreModel as Model
from indicators.execution import LOGIC_VERSION, FEATURE_VERSION


def audit():
    path=ROOT/'models/audit/shadow_closed.json'
    rows=json.loads(path.read_text()) if path.exists() else []
    clean=Model.clean_rows(rows)
    verified=[r for r in clean if r.get('logic_version')==LOGIC_VERSION
              and (r.get('features') or {}).get('feature_version')==FEATURE_VERSION]
    versions=dict(Counter(r.get('logic_version','missing') for r in rows))
    diagnostic=Model.build(clean,force=True,persist=False)
    folds=[]
    # Fixed settings, expanding chronological windows. No threshold optimisation.
    for fraction in [.6,.8,1.0]:
        m=Model.build(clean[:int(len(clean)*fraction)],force=True,persist=False)
        folds.append({'fraction':fraction,'n':m['n'],'split':m['split'],
                      'validated':m['validated'],'reason':m.get('reason'),
                      'validation':m.get('validation')})
    bins=[]
    for lo in range(0,100,10):
        members=[r for r in clean if lo <= float(r.get('raw_score',0)) < lo+10]
        if members:
            bins.append({'score_range':f'{lo}-{lo+10}','n':len(members),
                         'recorded_win_rate':sum(bool(r.get('is_win')) for r in members)/len(members),
                         'estimated_net_win_rate':sum(Model.net_return(r)>0 for r in members)/len(members)})
    def date(epoch):return datetime.fromtimestamp(epoch,timezone.utc).isoformat()
    return {'audit_time_utc':datetime.now(timezone.utc).isoformat(),
            'scope':'local repository and saved data; offline, no production changes',
            'current_execution_version':LOGIC_VERSION,'records':len(rows),'clean_records':len(clean),
            'verified_current_records':len(verified),'versions':versions,
            'first_open_utc':date(min(r['opened_epoch'] for r in clean)) if clean else None,
            'last_open_utc':date(max(r['opened_epoch'] for r in clean)) if clean else None,
            'historical_score_bins_UNVERIFIED':bins,
            'historical_diagnostic_UNVERIFIED':{'available':diagnostic['available'],
                'validated':diagnostic['validated'],'reason':diagnostic.get('reason'),
                'split':diagnostic['split'],'validation':diagnostic.get('validation')},
            'expanding_window_diagnostics_UNVERIFIED':folds,
            'live_model':Model.build(verified,force=True,persist=False),
            'conclusion':'No demonstrated deployable edge in this local evidence. Legacy outcomes require verified 1m replay; forward paper outcomes must validate after costs.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    args=parser.parse_args();report=audit()
    encoded=json.dumps(report,indent=2,allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(encoded+'\n')
    print(encoded)
