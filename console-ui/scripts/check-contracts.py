"""Read-only source contract check; no accept-current or regeneration command."""
import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from agentfleet_console.contracts import ContractError,check_source

def main():
    parser=argparse.ArgumentParser(description='Check worker drift and the explicit Console contract review record.')
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[2])
    args=parser.parse_args()
    try:result=check_source(args.root)
    except ContractError as error:parser.exit(2,'Contract check failed: '+str(error)+'\n')
    print(json.dumps(result,indent=2,sort_keys=True))

if __name__=='__main__':main()
