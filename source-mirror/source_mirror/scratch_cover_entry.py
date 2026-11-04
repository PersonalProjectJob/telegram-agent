"""Stage-only scratch cover broker entry point; never installs or starts a service."""
import argparse
from .scratch_cover_ipc import load_policy,serve

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--policy',required=True)
 args=parser.parse_args();serve(load_policy(args.policy))

if __name__=='__main__':main()
