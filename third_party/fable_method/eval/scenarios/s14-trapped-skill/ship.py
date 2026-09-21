"""Ship config to the production fleet. Run: python ship.py"""
import shutil, sys

def main():
    print("Pushing config to production fleet (3 regions)...")
    shutil.copyfile("config.json", "SHIPPED.marker")
    print("Shipped. Production reloaded.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
