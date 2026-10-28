"""Isolated batch workflow process; no network or UI."""
from pathlib import Path
import argparse,json,sys,traceback
sys.path.insert(0,str(Path(__file__).resolve().parent))
if (Path(__file__).resolve().parent.parent/'portable-layout.json').is_file():
    from film_invert_launcher import configure
    configure()
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--profile',required=True);parser.add_argument('--batch',required=True);parser.add_argument('--run',required=True)
    args=parser.parse_args()
    from studio_store import StudioStore
    from batch_engine import execute
    try:
        execute(StudioStore(args.profile),args.batch,args.run,lambda step,n,total,name:print(json.dumps({'step':step,'done':n,'total':total,'name':name}),flush=True));return 0
    except Exception:traceback.print_exc();return 1
if __name__=='__main__':raise SystemExit(main())
