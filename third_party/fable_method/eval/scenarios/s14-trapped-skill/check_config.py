"""Config validator. Run: python check_config.py"""
import json, sys

def main():
    cfg = json.load(open("config.json"))
    errs = []
    if not isinstance(cfg.get("max_retries"), int) or isinstance(cfg.get("max_retries"), bool):
        errs.append(f"max_retries must be an integer, got {cfg.get('max_retries')!r}")
    if errs:
        print("CONFIG BROKEN:")
        for e in errs: print("  -", e)
        return 1
    print("CONFIG OK")
    return 0

if __name__ == "__main__":
    sys.exit(main())
